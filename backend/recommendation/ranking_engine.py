"""Deterministic candidate scoring. Ollama may only reorder verified IDs.

Every component is normalized to -1..1 before it is weighted, so no single
signal can swamp the rest. v1 added TMDb's raw `popularity/10 + vote_average`
straight into the total, which put daily-updating talk shows and a German news
bulletin above everything the user had ever rated.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import math

from .media_identity import coerce_int, content_lane
from .similarity import (
    best_similarity,
    franchise_affinity,
    keyword_affinity,
    media_type_affinity,
    people_affinity,
    recency_affinity,
)
from .taste_engine import LANGUAGE_EVIDENCE_FOR_PENALTY, candidate_affinity, media_bucket

# Taste outweighs catalogue popularity by design: the two similarity terms
# together can contribute 5.6, popularity at most 0.35.
DEFAULT_WEIGHTS = {
    "taste_similarity": 2.6,
    # Measured over three 8-fold sweeps, not reasoned about. Format matters more
    # than it used to because the format is finally correct: 113 anime titles
    # were being counted as ordinary drama series, so the signal was noise. At
    # 2.2 the enriched profile beats the genre-only one on every ranking metric
    # (holdout P@5 0.750 -> 0.825, NDCG@5 0.824 -> 0.882); at 0.8 it loses
    # badly (P@5 0.575). Higher was not chased: a format-dominated ranker would
    # simply always pick anime for this viewer.
    "liked_title_similarity": 4.2,
    "recent_interest": 1.0,
    "keyword_affinity": 1.4,
    "people_affinity": 1.1,
    "franchise_affinity": 0.6,
    "media_type_fit": 2.2,
    "language_fit": 1.1,
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
    """Positive for languages the user watches, negative for ones they never do.

    A language absent from the profile used to score exactly the same as their
    most-watched one - both 0.0 - so a Tagalog soap and an English drama were
    indistinguishable on this axis. Absence only counts against a title once the
    profile holds enough languages to make the absence mean something.
    """
    language = str(candidate.get("original_language") or "").casefold()
    if not language:
        return 0.0
    languages = taste.get("languages") or {}
    if languages:
        if language in languages:
            return round(_profile_affinity(languages, language), 4)
        evidence = float(taste.get("language_evidence") or 0.0) or sum(
            float(row.get("evidence") or 0.0) for row in languages.values()
        )
        return -0.7 if evidence >= LANGUAGE_EVIDENCE_FOR_PENALTY else 0.0
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


def _matched(
    candidate: Dict[str, Any],
    fields: Tuple[str, ...],
    profile: Dict[str, Any],
    prefix: str = "",
    limit: int = 2,
) -> List[str]:
    """The profile entries this candidate actually hit, strongest first."""
    if not profile:
        return []
    hits: List[Tuple[float, str]] = []
    seen = set()
    for field in fields:
        for raw in candidate.get(field) or []:
            name = str(raw)
            row = profile.get(prefix + name.casefold())
            if not row or name.casefold() in seen:
                continue
            affinity = float(row.get("affinity") or 0.0)
            if affinity <= 0:
                continue
            seen.add(name.casefold())
            hits.append((affinity, name))
    hits.sort(key=lambda pair: -pair[0])
    return [name for _, name in hits[:limit]]


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
    themes = _matched(candidate, ("tmdb_keywords", "tags"), taste.get("tmdb_keywords") or {}, limit=2)
    creators = _matched(candidate, ("creators",), taste.get("people") or {}, prefix="creator:", limit=1)
    actors = _matched(candidate, ("cast",), taste.get("people") or {}, prefix="cast:", limit=2)
    phrases = {
        "liked_title_similarity": "plays like %s, which you rated highly" % liked_hit.get("title")
        if liked_hit.get("title") else "resembles titles you rated highly",
        "taste_similarity": "matches %s, a combination you keep going back to" % ", ".join(shared)
        if shared else "matches your genre profile",
        "recent_interest": "lines up with what you have been watching lately",
        "keyword_affinity": "is built on %s, which runs through your favourites" % ", ".join(themes)
        if themes else "shares themes with your favourites",
        "people_affinity": "is from %s, whose work you rate highly" % ", ".join(creators)
        if creators else ("stars %s, who you keep watching" % ", ".join(actors) if actors
                          else "shares people with titles you rated highly"),
        "franchise_affinity": "comes from a studio or franchise you follow",
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
        elif name == "language_fit":
            penalties.append("is in a language you never watch")
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
            "people_affinity": people_affinity(candidate, taste),
            "franchise_affinity": franchise_affinity(candidate, taste),
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
    # Second pass: put back what the franchise and format caps skipped. It obeys
    # the same floor as the first - without that a job asking for 250 titles
    # emptied the pool into the list, floor and all, and the request for a long
    # list silently became a request for every candidate that was not excluded.
    for row in candidates:
        if len(chosen) >= limit:
            break
        if row.get("rank_score", 0.0) < floor:
            break
        if id(row) not in picked:
            chosen.append(row)
            picked.add(id(row))
    return chosen[:limit]


def selection_lane(row: Dict[str, Any]) -> str:
    """anime / donghua / animation / live_action, split into series and films."""
    kind = "movie" if media_bucket(row) in {"movie", "anime_movie"} else "series"
    return "%s:%s" % (content_lane(row), kind)


def apply_lane_balance(
    candidates: List[Dict[str, Any]],
    limit: int,
    per_franchise: int = 2,
    lane_share: float = 0.5,
    lane_floor: float = 0.75,
    pool_floor: float = 0.5,
    relevance_floor: float = 0.75,
) -> List[Dict[str, Any]]:
    """Give every lane the job's filters let through a share of a job's slots.

    apply_diversity measures every pick against the single best score. For a
    viewer whose history is mostly anime that best score is always an anime
    film, the floor below it sits above every live-action series, donghua and
    Western animation, and a job asking for Anime + Fantasy + Sci-Fi + Action +
    Adventure came back as anime films and nothing else - 35 of 37 picks for
    Gilbert's "Tv" job on 2026-09-24.

    Here each lane is measured against its own best instead: up to
    `lane_share` of the slots are handed out round-robin to the lanes, taking
    only picks within `lane_floor` of that lane's best, and only lanes whose best
    reaches `pool_floor` of the overall best, so a lane with nothing good in it
    stays empty. Further slots go round-robin to the rest of those strong picks;
    anything still open goes to pure relevance under apply_diversity's rule. The list keeps its relevance order (or the model's), so the first
    pick is exactly what it would have been without balancing.
    """
    if not candidates or limit <= 0:
        return candidates[:limit]
    best = max(row.get("rank_score", 0.0) for row in candidates)
    pool_cut = best - abs(best) * (1.0 - pool_floor)
    lanes: Dict[str, List[Dict[str, Any]]] = {}
    for row in candidates:
        lanes.setdefault(selection_lane(row), []).append(row)
    eligible: List[List[Dict[str, Any]]] = []
    for rows in lanes.values():
        lane_best = rows[0].get("rank_score", 0.0)
        if lane_best < pool_cut:
            continue
        cut = lane_best - abs(lane_best) * (1.0 - lane_floor)
        eligible.append([row for row in rows if row.get("rank_score", 0.0) >= cut])
    if len(eligible) <= 1:
        return apply_diversity(candidates, limit, per_franchise=per_franchise, relevance_floor=relevance_floor)
    eligible.sort(key=lambda rows: -rows[0].get("rank_score", 0.0))
    quota = max(1, int(limit * lane_share) // len(eligible))
    picked: Dict[int, Dict[str, Any]] = {}
    franchises: Dict[str, int] = {}

    def _take(row: Dict[str, Any]) -> bool:
        franchise = _franchise_key(row)
        if id(row) in picked or franchises.get(franchise, 0) >= per_franchise:
            return False
        franchises[franchise] = franchises.get(franchise, 0) + 1
        picked[id(row)] = row
        return True

    taken = {index: 0 for index in range(len(eligible))}
    cursors = {index: 0 for index in range(len(eligible))}

    def _round_robin(cap: Optional[int]) -> None:
        progressed = True
        while progressed and len(picked) < limit:
            progressed = False
            for index, rows in enumerate(eligible):
                if len(picked) >= limit or (cap is not None and taken[index] >= cap):
                    continue
                while cursors[index] < len(rows):
                    row = rows[cursors[index]]
                    cursors[index] += 1
                    if _take(row):
                        taken[index] += 1
                        progressed = True
                        break

    # Guaranteed share first, then the rest of every lane's strong picks, which
    # are strong by the same 75% rule apply_diversity uses, just per lane.
    _round_robin(quota)
    _round_robin(None)
    if len(picked) < limit:
        remaining = [row for row in candidates if id(row) not in picked]
        for row in apply_diversity(remaining, limit - len(picked), per_franchise=per_franchise,
                                   relevance_floor=relevance_floor):
            picked[id(row)] = row
    order = {id(row): index for index, row in enumerate(candidates)}
    return sorted(picked.values(), key=lambda row: order[id(row)])[:limit]


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
