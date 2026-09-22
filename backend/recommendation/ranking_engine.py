"""Deterministic candidate scoring. Ollama may only reorder verified IDs.

Every component is normalized to -1..1 before it is weighted, so no single
signal can swamp the rest. v1 added TMDb's raw `popularity/10 + vote_average`
straight into the total, which put daily-updating talk shows and a German news
bulletin above everything the user had ever rated.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import math

from .media_identity import coerce_int
from .similarity import (
    best_similarity,
    keyword_affinity,
    media_type_affinity,
    recency_affinity,
)
from .taste_engine import candidate_affinity, media_bucket

# Taste outweighs catalogue popularity by design: the two similarity terms
# together can contribute 5.6, popularity at most 0.35.
DEFAULT_WEIGHTS = {
    "taste_similarity": 2.6,
    "liked_title_similarity": 3.0,
    "recent_interest": 1.0,
    "keyword_affinity": 0.8,
    "media_type_fit": 0.8,
    "language_fit": 0.6,
    "era_fit": 0.4,
    "quality": 1.3,
    "metadata_confidence": 0.5,
    "popularity": 0.35,
    "source_confidence": 0.4,
    "negative_affinity": 2.6,
    "thin_evidence": 1.2,
}

# Bayesian prior for TMDb ratings: a 9.0 from four voters is not a 9.0.
RATING_PRIOR_VOTES = 200.0
RATING_PRIOR_MEAN = 6.4

SOURCE_CONFIDENCE = {
    "tmdb_recommendations": 1.0,
    "tmdb_similar": 0.95,
    "anilist": 0.9,
    "anilist_upcoming": 0.7,
    "trakt": 0.85,
    "simkl": 0.8,
    "taste_seeded_discover": 0.8,
    "tmdb_discover": 0.55,
    "seed_expand": 0.5,
    # The offline harness injects held-out positives under this source. Pinned to
    # the floor so a measured gain can never be an artefact of the label's origin.
    "heldout_challenge": 0.5,
}

METADATA_FIELDS = ("genres", "synopsis", "original_language", "year", "poster")


def _released(candidate: Dict[str, Any], today: Optional[datetime] = None) -> bool:
    today = today or datetime.now(timezone.utc)
    for key in ("release_date", "first_air_date", "aired_at"):
        value = candidate.get(key)
        if value and len(str(value)) >= 10:
            return str(value)[:10] <= today.date().isoformat()
    year = coerce_int(candidate.get("year"))
    return year is None or year <= today.year


def quality_score(candidate: Dict[str, Any]) -> float:
    """Vote-count-smoothed community rating in 0..1; unrated lands on the prior."""
    try:
        rating = float(candidate.get("tmdb_rating") or candidate.get("rating") or 0.0)
    except (TypeError, ValueError):
        rating = 0.0
    votes = float(coerce_int(candidate.get("vote_count")) or 0)
    smoothed = (votes * rating + RATING_PRIOR_VOTES * RATING_PRIOR_MEAN) / (votes + RATING_PRIOR_VOTES)
    return round(max(0.0, min(1.0, smoothed / 10.0)), 4)


def metadata_confidence(candidate: Dict[str, Any]) -> float:
    present = sum(1 for field in METADATA_FIELDS if candidate.get(field) not in (None, "", [], {}))
    return round(present / len(METADATA_FIELDS), 4)


def thin_evidence_penalty(candidate: Dict[str, Any]) -> float:
    """A released title nobody has voted on is obscure, not undiscovered.

    Unreleased titles are exempt: they are judged on metadata fit alone, which
    is the only honest way to rank something that has not aired yet. A source
    that does not publish vote counts at all (AniList, Simkl) is exempt too -
    absent is not the same as zero, and punishing it would quietly demote every
    anime candidate.
    """
    if not _released(candidate):
        return 0.0
    if candidate.get("vote_count") is None:
        return 0.0
    votes = coerce_int(candidate.get("vote_count"))
    if votes is None or votes < 10:
        return -1.0
    if votes < 50:
        return -0.4
    return 0.0


def popularity_score(candidate: Dict[str, Any]) -> float:
    try:
        value = float(candidate.get("popularity") or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    if value <= 0:
        return 0.0
    return round(min(1.0, math.log10(1.0 + value) / 3.0), 4)


def _profile_affinity(store: Dict[str, Any], key: Optional[str]) -> float:
    if not store or not key:
        return 0.0
    row = store.get(str(key)) or store.get(str(key).casefold()) or {}
    return float(row.get("affinity") or 0.0) * (0.4 + 0.6 * float(row.get("confidence") or 0.0))


def era_fit(candidate: Dict[str, Any], taste: Dict[str, Any]) -> float:
    year = coerce_int(candidate.get("year"))
    if year is None:
        return 0.0
    decades = taste.get("decades") or {}
    if decades:
        return round(_profile_affinity(decades, "%ds" % ((year // 10) * 10)), 4)
    return 1.0 if "%ds" % ((year // 10) * 10) in (taste.get("favorite_eras") or []) else 0.0


def language_fit(candidate: Dict[str, Any], taste: Dict[str, Any]) -> float:
    language = str(candidate.get("original_language") or "").casefold()
    if not language:
        return 0.0
    languages = taste.get("languages") or {}
    if languages:
        return round(_profile_affinity(languages, language), 4)
    preferred = {str(item).casefold() for item in (taste.get("preferred_languages") or [])}
    return 1.0 if language in preferred else 0.0


def negative_affinity(
    candidate: Dict[str, Any],
    taste: Dict[str, Any],
    negative_hit: Dict[str, Any],
    liked_hit: Dict[str, Any],
) -> float:
    """How much the candidate looks like something the user rejected, as 0..-1.

    Only the *margin* over the positive match counts. Comparing raw similarity
    flagged every single candidate, because anything in the user's genres also
    resembles the handful of titles they dropped in those same genres.
    """
    names = {str(name).casefold() for name in (candidate.get("genres") or []) if name}
    disliked = {str(name).casefold() for name in (taste.get("disliked_genres") or [])}
    score = 0.0
    if names and disliked:
        score -= min(1.0, len(names & disliked) / max(len(names), 1))
    margin = float(negative_hit.get("score") or 0.0) - float(liked_hit.get("score") or 0.0)
    if margin > 0:
        score -= min(1.0, margin * 2.0)
    return round(max(-1.0, score), 4)


def source_confidence(candidate: Dict[str, Any]) -> float:
    return SOURCE_CONFIDENCE.get(str(candidate.get("source") or ""), 0.6)


def _explain(components: Dict[str, float], weights: Dict[str, float], candidate: Dict[str, Any],
             liked_hit: Dict[str, Any], taste: Dict[str, Any]) -> Tuple[str, List[str], List[str]]:
    """Reasons taken from the contributions that actually decided the rank.

    v1 fell back to the profile's top two genres whenever nothing matched, so a
    documentary was explained as "matches Action, Adventure".
    """
    contributions = sorted(
        ((name, components[name] * weights.get(name, 0.0)) for name in components),
        key=lambda pair: -pair[1],
    )
    names = [str(name) for name in (candidate.get("genres") or [])]
    shared = [name for name in names if (taste.get("genres") or {}).get(name, {}).get("affinity", 0) > 0][:3]
    phrases = {
        "liked_title_similarity": "plays like %s, which you rated highly" % liked_hit.get("title")
        if liked_hit.get("title") else "resembles titles you rated highly",
        "taste_similarity": "matches %s, a combination you keep going back to" % ", ".join(shared)
        if shared else "matches your genre profile",
        "recent_interest": "lines up with what you have been watching lately",
        "keyword_affinity": "shares themes with your favourites",
        "media_type_fit": "is the %s format you watch most" % media_bucket(candidate).replace("_", " "),
        "language_fit": "is in a language you watch a lot",
        "era_fit": "comes from an era you favour",
        "quality": "is well rated by a large audience",
        "popularity": "is widely watched right now",
        "metadata_confidence": "has complete, reliable metadata",
        "source_confidence": "came from a source seeded by your own history",
    }
    positive = [phrases[name] for name, value in contributions if value > 0.15 and name in phrases][:3]
    penalties = []
    for name, value in contributions:
        if value >= -0.15:
            continue
        if name == "negative_affinity":
            penalties.append("overlaps genres you rejected")
        elif name == "thin_evidence":
            penalties.append("almost nobody has rated it yet")
        elif name == "metadata_confidence":
            penalties.append("incomplete metadata")
    if positive:
        why = "%s %s." % (candidate.get("title"), "; ".join(positive))
    else:
        why = "%s is the closest remaining match to your profile." % candidate.get("title")
    return why, positive, penalties[:3]


def score_candidates(
    candidates: List[Dict[str, Any]],
    taste: Dict[str, Any],
    weights: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    positives = list(taste.get("high_confidence_positive_titles") or [])
    negatives = list(taste.get("negative_titles") or [])
    scored: List[Dict[str, Any]] = []
    for candidate in candidates:
        liked_hit = best_similarity(candidate, positives)
        negative_hit = best_similarity(candidate, negatives) if negatives else {"score": 0.0}
        components = {
            "taste_similarity": candidate_affinity(candidate, taste),
            "liked_title_similarity": liked_hit["score"],
            "recent_interest": recency_affinity(candidate, taste),
            "keyword_affinity": keyword_affinity(candidate, taste),
            "media_type_fit": media_type_affinity(candidate, taste),
            "language_fit": language_fit(candidate, taste),
            "era_fit": era_fit(candidate, taste),
            "quality": quality_score(candidate),
            "metadata_confidence": metadata_confidence(candidate),
            "popularity": popularity_score(candidate),
            "source_confidence": source_confidence(candidate),
            "negative_affinity": negative_affinity(candidate, taste, negative_hit, liked_hit),
            "thin_evidence": thin_evidence_penalty(candidate),
        }
        total = sum(value * weights.get(name, 0.0) for name, value in components.items())
        row = dict(candidate)
        row.pop("_tokens", None)
        row["score_components"] = {name: round(value, 4) for name, value in components.items()}
        row["score_contributions"] = {
            name: round(value * weights.get(name, 0.0), 4) for name, value in components.items()
        }
        row["deterministic_score"] = round(total, 4)
        row["rank_score"] = total
        row["similar_to"] = liked_hit.get("matches") or []
        why, evidence, penalties = _explain(components, weights, candidate, liked_hit, taste)
        # The evidence-based reason always wins. A provider's generic blurb
        # ("Suggested from AniList titles on your list") told the user nothing
        # about why this title in particular reached their list.
        row["why"] = why
        if candidate.get("why"):
            row["source_note"] = candidate["why"]
        row["why_evidence"] = evidence
        row["why_penalties"] = penalties
        # Fixed calibration, not min-max over the batch: v1 rescaled each run's
        # own range, so eight equally poor picks all showed 77%.
        row["match_score"] = max(1, min(99, int(round(100 / (1 + math.exp(-(total - 3.2) / 1.5))))))
        scored.append(row)
    scored.sort(key=lambda row: (-row["rank_score"], str(row.get("title") or "")))
    return scored


def _franchise_key(row: Dict[str, Any]) -> str:
    """Group sequels and spin-offs. "Pokemon Journeys" and "Pokemon Horizons"
    are the same franchise; splitting only on ":" left them looking distinct."""
    from .media_identity import title_key

    words = title_key(row.get("title")).split()
    if not words:
        return str(row.get("title") or "").casefold()
    head = words[0]
    if len(head) < 4 and len(words) > 1:
        head = "%s %s" % (head, words[1])
    return head


def _diversity_key(row: Dict[str, Any]) -> str:
    return "%s|%s" % (media_bucket(row), _franchise_key(row))


def apply_diversity(
    candidates: List[Dict[str, Any]],
    limit: int,
    per_franchise: int = 2,
    relevance_floor: float = 0.75,
    bucket_share: float = 0.6,
) -> List[Dict[str, Any]]:
    """Spread the final slots across franchises and formats, inside the strong pool.

    Diversity runs after relevance. It may skip a third entry from one
    franchise, or a sixth anime in an eight-slot list, only while something
    above `relevance_floor` of the best score is still available to take
    instead; once the strong pool is exhausted it stops varying and falls back
    to pure relevance, so it can never pull a weak pick up just to look varied.
    """
    if not candidates or limit <= 0:
        return candidates[:limit]
    best = max(row.get("rank_score", 0.0) for row in candidates)
    floor = best - abs(best) * (1.0 - relevance_floor)
    per_bucket = max(2, int(math.ceil(limit * bucket_share)))
    chosen: List[Dict[str, Any]] = []
    picked = set()
    franchises: Dict[str, int] = {}
    buckets: Dict[str, int] = {}
    for row in candidates:
        if len(chosen) >= limit:
            break
        if row.get("rank_score", 0.0) < floor:
            break
        franchise, bucket = _franchise_key(row), media_bucket(row)
        if franchises.get(franchise, 0) >= per_franchise or buckets.get(bucket, 0) >= per_bucket:
            continue
        franchises[franchise] = franchises.get(franchise, 0) + 1
        buckets[bucket] = buckets.get(bucket, 0) + 1
        chosen.append(row)
        picked.add(id(row))
    for row in candidates:
        if len(chosen) >= limit:
            break
        if id(row) not in picked:
            chosen.append(row)
            picked.add(id(row))
    return chosen[:limit]


def apply_rerank(candidates: List[Dict[str, Any]], ordered_ids: Optional[List[str]]) -> List[Dict[str, Any]]:
    if not ordered_ids:
        return candidates
    by_id = {str(row.get("candidate_id") or row.get("tmdb_id") or row["title"]): row for row in candidates}
    ordered = []
    seen = set()
    for index, key in enumerate(ordered_ids):
        row = by_id.get(str(key))
        if row and str(key) not in seen:
            ranked = dict(row)
            ranked["ai_rank"] = index + 1
            ranked["ai_score"] = max(1, 100 - index)
            ordered.append(ranked)
            seen.add(str(key))
    for row in candidates:
        marker = str(row.get("candidate_id") or row.get("tmdb_id") or row["title"])
        if marker not in seen:
            leftover = dict(row)
            leftover.setdefault("ai_rank", None)
            ordered.append(leftover)
    return ordered
