"""Structured taste features from normalized history and feedback. Not an LLM dump.

The v1 engine divided every item's weight by the number of rows its provider
contributed, which meant a user with 400 Trakt rows could never push a single
title past the "high confidence positive" threshold: the richer the history,
the emptier the profile. v2 scores each title on its own evidence, keeps the
counts and confidences that evidence carries, and only normalizes at the end,
where scale no longer changes the ordering.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
import math
import re

from .media_identity import coerce_int, normalize_media_type, title_key, unique_taste_docs

DEFAULT_PROVIDER_WEIGHTS = {
    "plex": 1.0,
    "trakt": 1.0,
    "simkl": 1.0,
    "anilist": 1.2,
    "feedback": 1.0,
    "demo": 0.25,
}

# A title needs this much evidence before it is quoted back as something the
# user demonstrably likes. Reachable: one 9/10 rating clears it on its own.
POSITIVE_EVIDENCE = 1.8
NEGATIVE_EVIDENCE = -1.0
RECENT_DAYS = 240
# List-valued metadata merged across every provider row behind one title.
# `tmdb_keywords`, `cast`, `creators` and `companies` arrive from TMDb detail
# enrichment; without them the profile carried nothing but genre names.
LIST_FIELDS = ("genres", "tags", "studios", "tmdb_keywords", "cast", "creators", "companies")
# A language profile built from 44 of 633 titles cannot tell English from
# Tagalog. Below this much evidence an unseen language stays neutral instead of
# being penalised on a profile that never had the chance to mention it.
LANGUAGE_EVIDENCE_FOR_PENALTY = 30
STOPWORDS = frozenset("""
a an and are as at be but by for from has have he her his in into is it its of on or she
that the their them they this to was were will with who what when which after before
his hers our your my their one two new life world story man woman young old first last
""".split())


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp
    except (ValueError, TypeError):
        return None


def doc_key(doc: Dict[str, Any]) -> str:
    """Stable per-title key so every provider row for a title lands together."""
    canonical = doc.get("canonical_media_id")
    if canonical:
        return str(canonical)
    media = normalize_media_type(doc.get("media_type") or doc.get("type"))
    return "%s|%s|%s" % (title_key(doc.get("title")), coerce_int(doc.get("year")), media)


def normalized_rating(item: Dict[str, Any]) -> Optional[float]:
    """The user's own score on a 0-10 scale, or None when they never rated it."""
    rating = item.get("rating")
    if rating is None:
        return None
    try:
        value = float(rating)
        scale = float(item.get("rating_scale") or 10) or 10.0
    except (TypeError, ValueError):
        return None
    if scale <= 0 or value < 0 or value > scale:
        return None
    return value / scale * 10.0


def merge_taste_docs(
    history: Iterable[Dict[str, Any]],
    personal: Optional[Iterable[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """One row per canonical title, merging every provider row behind it.

    Raw Trakt history is one row per episode, so the number of rows behind a
    title is how much of it the user actually watched - the strongest signal
    the catalogue hands us for free. Keeping only the first row threw that away
    together with ratings that just one provider reported.
    """
    merged: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []

    def _absorb(row: Dict[str, Any], is_personal: bool) -> None:
        key = doc_key(row)
        current = merged.get(key)
        if current is None:
            current = {
                "plays": 0,
                "providers": set(),
                **{field: [] for field in LIST_FIELDS},
            }
            merged[key] = current
            order.append(key)
        current["plays"] += 0 if is_personal else 1
        provider = row.get("provider") or row.get("source")
        if provider:
            current["providers"].add(provider)
        for field in LIST_FIELDS:
            for value in row.get(field) or []:
                if value and value not in current[field]:
                    current[field].append(value)
        for field in (
            "title", "year", "type", "media_type", "canonical_media_id", "original_language",
            "country", "tmdb_id", "imdb_id", "anilist_id", "trakt_id", "simkl_id", "tvdb_id",
            "overview", "synopsis", "format", "collection", "runtime",
        ):
            if current.get(field) in (None, "", []) and row.get(field) not in (None, "", []):
                current[field] = row[field]
        # A personal rating outranks whatever a catalogue row carried.
        rating = normalized_rating(row)
        if rating is not None and (is_personal or current.get("rating_source") != "personal"):
            current["rating"] = rating
            current["rating_scale"] = 10
            current["rating_source"] = "personal" if is_personal else "history"
        for field, combine in (
            ("watch_count", max), ("favorite", max), ("completed", max), ("rewatched", max),
        ):
            value = row.get(field)
            if value is not None:
                current[field] = combine(current.get(field) or 0, int(bool(value)) if field != "watch_count" else int(value or 1))
        if row.get("status") and not current.get("status"):
            current["status"] = row["status"]
        stamp = _parse_dt(row.get("last_watched_at") or row.get("watched_at") or row.get("rated_at"))
        if stamp and (current.get("_stamp") is None or stamp > current["_stamp"]):
            current["_stamp"] = stamp
            current["last_watched_at"] = row.get("last_watched_at") or row.get("watched_at")

    for row in history or []:
        _absorb(row, False)
    for row in personal or []:
        _absorb(row, True)

    output = []
    for key in order:
        row = merged[key]
        row["provider"] = sorted(row.pop("providers"))[0] if row.get("providers") else "unknown"
        row.pop("_stamp", None)
        row["plays"] = max(1, row.get("plays") or 1)
        output.append(row)
    return output


def evidence_score(item: Dict[str, Any], feedback_by_id: Optional[Dict[str, str]] = None) -> float:
    """How strongly this title says "more like this", on a roughly -5..+5 scale.

    Watched is not liked. A rating, a rewatch or a hundred finished episodes are
    evidence; one sampled episode is barely any.
    """
    feedback_by_id = feedback_by_id or {}
    status = (item.get("status") or "").upper()
    if status in {"PLANNING", "PLANTOWATCH"}:
        return 0.0

    canonical = item.get("canonical_media_id")
    action = feedback_by_id.get(canonical or "", "")
    if action == "blacklist":
        return -5.0
    if action == "dislike":
        return -3.0

    score = 0.0
    rating = normalized_rating(item)
    if rating is not None:
        if rating >= 9:
            score = 3.4
        elif rating >= 8:
            score = 2.6
        elif rating >= 7:
            score = 1.2
        elif rating >= 5:
            score = 0.2
        else:
            score = -2.4
    else:
        # No rating: engagement depth is the only honest evidence we have.
        score = 0.35

    plays = int(item.get("plays") or 1)
    if plays > 1:
        # log so a 700-episode sitcom outweighs a 12-episode season without
        # burying every film the user has ever rated.
        score += min(1.9, math.log2(plays) * 0.42)
    if int(item.get("watch_count") or 1) > 1 or item.get("rewatched"):
        score += 0.8
    if item.get("favorite"):
        score = max(score, 2.8)
    if status == "REPEATING":
        score += 0.7
    if item.get("completed") or status == "COMPLETED":
        score += 0.3
    if status == "DROPPED" and (rating is None or rating <= 5):
        score = min(score, -2.0)
    if action == "like":
        score = max(score, 2.8)

    stamp = _parse_dt(item.get("last_watched_at") or item.get("watched_at"))
    if stamp and score > 0:
        days = (datetime.now(timezone.utc) - stamp).days
        if days <= 90:
            score *= 1.15
        elif days > 365 * 3:
            score *= 0.8
    return round(score, 4)


def stated_opinion(item: Dict[str, Any], feedback_by_id: Optional[Dict[str, str]] = None) -> bool:
    """Did the user actually pass judgement on this title, rather than watch it?

    Watched is not liked. Episode depth is real evidence of engagement, but it
    is not an opinion, and the precise signals - keywords, people, studios -
    are only trustworthy when an opinion stands behind them.
    """
    if normalized_rating(item) is not None:
        return True
    if item.get("favorite"):
        return True
    action = (feedback_by_id or {}).get(item.get("canonical_media_id") or "", "")
    return action in {"like", "dislike", "blacklist"}


def is_recent(item: Dict[str, Any], days: int = RECENT_DAYS) -> bool:
    stamp = _parse_dt(item.get("last_watched_at") or item.get("watched_at"))
    if stamp is None:
        return False
    return (datetime.now(timezone.utc) - stamp).days <= days


# TMDb tags animation-specific formats explicitly. Trakt and AniList report
# anime genres as "Action, Adventure, Comedy" with no "Animation" among them, so
# the genre test alone left 113 of Gilbert's 204 Japanese titles - Boruto, My
# Hero Academia, Slime - counted as ordinary drama series, and his anime_movie
# lane completely empty. Only formats that cannot exist in live action are
# listed, so a live-action manga adaptation is not swept up with them.
ANIMATED_FORMAT_KEYWORDS = frozenset({
    "anime", "donghua", "original net animation (ona)",
    "original video animation (ova)", "aeni",
})


def media_bucket(item: Dict[str, Any]) -> str:
    """movie / tv / anime / anime_movie - anime films rank nothing like anime series."""
    media = normalize_media_type(item.get("media_type") or item.get("type"))
    fmt = str(item.get("format") or item.get("anime_format") or "").upper()
    genres = {str(g).casefold() for g in (item.get("genres") or [])}
    language = str(item.get("original_language") or "").casefold()
    if media != "anime" and "animation" in genres and language in {"ja", "zh", "ko"}:
        media = "anime"
    if media != "anime" and ANIMATED_FORMAT_KEYWORDS & {
        str(name).casefold() for name in (item.get("tmdb_keywords") or [])
    }:
        media = "anime"
    # `normalize_media_type` defaults an absent value to "movie", so testing it
    # against a missing `type` filed every anime row that carried no separate
    # type - most of AniList's - as an anime film. The format has to be stated.
    stated_type = item.get("type")
    if media == "anime" and (
        fmt in {"MOVIE", "FILM"}
        or (stated_type and normalize_media_type(stated_type) == "movie")
    ):
        return "anime_movie"
    return media


def _accumulate(store: Dict[str, Dict[str, float]], name: str, weight: float) -> None:
    row = store.setdefault(name, {"score": 0.0, "evidence": 0.0, "positive": 0.0, "negative": 0.0})
    row["score"] += weight
    row["evidence"] += 1
    if weight >= 0:
        row["positive"] += 1
    else:
        row["negative"] += 1


def _finalize(store: Dict[str, Dict[str, float]], floor: float = 1.0, per_item: bool = False) -> Dict[str, Dict[str, float]]:
    """Map raw sums onto -1..1 affinity with an evidence-based confidence.

    Normalizing here rather than per item is what keeps one large provider from
    dominating without making individual evidence unreachably small.

    `per_item` divides by the number of titles first, for the few dimensions
    where volume is not preference. There are only a handful of formats, so the
    format with the most rows would otherwise always come out on top: once anime
    series were classified correctly they outnumbered everything else and took
    affinity 1.0 purely on count, which measured worse than not classifying them
    at all (NDCG@5 0.770 -> 0.573).
    """
    if not store:
        return {}
    value = (lambda row: row["score"] / max(row["evidence"], 1.0)) if per_item else (lambda row: row["score"])
    peak = max(abs(value(row)) for row in store.values()) or 1.0
    output: Dict[str, Dict[str, float]] = {}
    for name, row in store.items():
        evidence = row["evidence"]
        output[name] = {
            "affinity": round(max(-1.0, min(1.0, value(row) / max(peak, floor))), 4),
            "evidence": int(evidence),
            # Wilson-ish: 5 observations is where we start trusting a preference.
            "confidence": round(evidence / (evidence + 5.0), 4),
            "positive": int(row["positive"]),
            "negative": int(row["negative"]),
        }
    return output


def _top(store: Dict[str, Dict[str, float]], limit: int, sign: int = 1) -> List[str]:
    rows = [(name, row) for name, row in store.items() if row["affinity"] * sign > 0]
    rows.sort(key=lambda pair: -pair[1]["affinity"] * sign)
    return [name for name, _ in rows[:limit]]


def overview_tokens(text: Optional[str], limit: int = 24) -> List[str]:
    if not text:
        return []
    words = re.findall(r"[a-zA-Z][a-zA-Z'-]{3,}", str(text).casefold())
    seen: List[str] = []
    for word in words:
        if word in STOPWORDS or word in seen:
            continue
        seen.append(word)
        if len(seen) >= limit:
            break
    return seen


def build_taste_snapshot(
    history: List[Dict[str, Any]],
    feedback: Optional[List[Dict[str, Any]]] = None,
    provider_weights: Optional[Dict[str, float]] = None,
    taste_sources: Optional[List[str]] = None,
    personal_history: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    weights = {**DEFAULT_PROVIDER_WEIGHTS, **(provider_weights or {})}
    feedback_by_id = {
        row["canonical_media_id"]: row.get("action")
        for row in (feedback or [])
        if row.get("canonical_media_id")
    }

    docs = merge_taste_docs(history or [], personal_history)
    # taste_sources=None -> all providers; [] -> none; ["simkl"] -> only those.
    if taste_sources is not None:
        allowed = set(taste_sources)
        docs = [] if not allowed else [doc for doc in docs if (doc.get("provider") or "unknown") in allowed]

    genres: Dict[str, Dict[str, float]] = {}
    pairs: Dict[str, Dict[str, float]] = {}
    languages: Dict[str, Dict[str, float]] = {}
    decades: Dict[str, Dict[str, float]] = {}
    studios: Dict[str, Dict[str, float]] = {}
    tags: Dict[str, Dict[str, float]] = {}
    buckets: Dict[str, Dict[str, float]] = {}
    recent_genres: Dict[str, Dict[str, float]] = {}
    longterm_genres: Dict[str, Dict[str, float]] = {}
    keywords: Dict[str, Dict[str, float]] = {}
    tmdb_keywords: Dict[str, Dict[str, float]] = {}
    people: Dict[str, Dict[str, float]] = {}
    companies: Dict[str, Dict[str, float]] = {}
    collections: Dict[str, Dict[str, float]] = {}

    positives: List[Dict[str, Any]] = []
    negatives: List[Dict[str, Any]] = []
    rewatched: List[str] = []
    rated_count = 0

    for item in docs:
        weight = evidence_score(item, feedback_by_id) * weights.get(item.get("provider") or "unknown", 1.0)
        if normalized_rating(item) is not None:
            rated_count += 1
        names = [str(name) for name in (item.get("genres") or []) if name]
        recent = is_recent(item)
        for name in names:
            _accumulate(genres, name, weight)
            _accumulate(recent_genres if recent else longterm_genres, name, weight)
        for first in range(len(names)):
            for second in range(first + 1, len(names)):
                _accumulate(pairs, genre_pair(names[first], names[second]), weight)
        if item.get("original_language"):
            _accumulate(languages, str(item["original_language"]).casefold(), weight)
        year = coerce_int(item.get("year"))
        if year:
            _accumulate(decades, "%ds" % ((year // 10) * 10), weight)
        for studio in item.get("studios") or []:
            _accumulate(studios, str(studio), weight)
        for tag in item.get("tags") or []:
            _accumulate(tags, str(tag), weight)
        # Keywords, people and studios are precise enough that finishing a show
        # without ever rating it pollutes them. Episode depth alone can carry an
        # unrated title past POSITIVE_EVIDENCE, and those titles then matched
        # their own terms back. These stores therefore take only titles the user
        # actually judged - a rating, a like, a dislike or a favourite. Genres
        # still take every row; they need the volume to mean anything.
        if stated_opinion(item, feedback_by_id):
            for keyword in item.get("tmdb_keywords") or []:
                _accumulate(tmdb_keywords, str(keyword).casefold(), weight)
            # A director says more about taste than a fourth-billed actor, so
            # they are accumulated separately rather than as one "people" bag.
            for person in item.get("creators") or []:
                _accumulate(people, "creator:%s" % str(person).casefold(), weight)
            for person in item.get("cast") or []:
                _accumulate(people, "cast:%s" % str(person).casefold(), weight * 0.5)
            for company in item.get("companies") or []:
                _accumulate(companies, str(company).casefold(), weight)
            if item.get("collection"):
                _accumulate(collections, str(item["collection"]).casefold(), weight)
        _accumulate(buckets, media_bucket(item), weight)

        title = item.get("title")
        if not title:
            continue
        row = {
            "title": title,
            "year": item.get("year"),
            "score": round(weight, 3),
            "genres": names[:6],
            "media_type": media_bucket(item),
            "original_language": item.get("original_language"),
            "plays": item.get("plays"),
            "rating": normalized_rating(item),
            # Carried so a candidate can be compared on what a title is actually
            # about, not only on which genre labels it happens to share.
            "overview": item.get("overview") or item.get("synopsis"),
            "tmdb_keywords": (item.get("tmdb_keywords") or [])[:24],
            "cast": (item.get("cast") or [])[:8],
            "creators": (item.get("creators") or [])[:8],
            "companies": (item.get("companies") or [])[:8],
            "collection": item.get("collection"),
            "studios": (item.get("studios") or [])[:6],
            "tags": (item.get("tags") or [])[:12],
        }
        if weight >= POSITIVE_EVIDENCE:
            positives.append(row)
            for token in overview_tokens(item.get("overview") or item.get("synopsis")):
                _accumulate(keywords, token, weight)
        elif weight <= NEGATIVE_EVIDENCE:
            negatives.append(row)
        if item.get("rewatched") or int(item.get("watch_count") or 1) > 1:
            rewatched.append(title)

    # Explicit feedback is a direct instruction, so it lands on top of whatever
    # the watch history implied rather than being averaged into it.
    preference_reasons: List[Dict[str, Any]] = []
    seen_feedback: set = set()
    for row in feedback or []:
        action = row.get("action")
        title = row.get("title")
        if action not in {"like", "dislike", "blacklist"} or not title:
            continue
        if title in seen_feedback and action != "blacklist":
            continue
        seen_feedback.add(title)
        delta = 3.5 if action == "like" else (-4.5 if action == "blacklist" else -3.5)
        names = [name for name in (row.get("genres") or []) if name]
        for name in names:
            _accumulate(genres, name, delta)
        entry = {"title": title, "year": row.get("year"), "score": delta, "genres": names[:6]}
        if action == "like":
            positives.append(entry)
        else:
            negatives.append(entry)
        preference_reasons.append({
            "kind": "liked" if action == "like" else ("disliked" if action == "dislike" else "blacklisted"),
            "title": title,
            "effect": ", ".join(names[:3]) or ("positive signal" if action == "like" else "negative signal"),
        })

    genre_profile = _finalize(genres)
    positives.sort(key=lambda row: -row["score"])
    negatives.sort(key=lambda row: row["score"])
    # Provider IDs for the titles with the most evidence behind them, so
    # "more like this" queries are seeded by what the user actually loved
    # rather than by whichever rows the database happened to return first.
    seeds = sorted(
        (doc for doc in docs if doc.get("tmdb_id") or doc.get("anilist_id")),
        key=lambda doc: -evidence_score(doc, feedback_by_id),
    )
    seed_docs = [
        {
            "title": doc.get("title"),
            "year": doc.get("year"),
            "type": doc.get("type") or doc.get("media_type"),
            "media_type": media_bucket(doc),
            "tmdb_id": doc.get("tmdb_id"),
            "anilist_id": doc.get("anilist_id"),
            "genres": (doc.get("genres") or [])[:6],
            "evidence": round(evidence_score(doc, feedback_by_id), 3),
        }
        for doc in seeds[:24]
        if evidence_score(doc, feedback_by_id) > 0
    ]
    snapshot: Dict[str, Any] = {
        # --- v1 keys, still read by the UI, the LLM prompt and older jobs ---
        "favorite_genres": _top(genre_profile, 8),
        "least_preferred_genres": _top(genre_profile, 5, sign=-1),
        "liked_genres": _top(genre_profile, 8),
        "disliked_genres": _top(genre_profile, 8, sign=-1),
        "favorite_eras": _top(_finalize(decades), 4),
        "preferred_languages": _top(_finalize(languages), 4),
        "high_confidence_positive_titles": positives[:24],
        "negative_titles": negatives[:16],
        "frequently_rewatched_titles": list(dict.fromkeys(rewatched))[:12],
        "preference_reasons": preference_reasons[:12],
        "provider_weights": weights,
        "item_count": len(docs),
        # --- v2 structured profile ---
        "genres": genre_profile,
        "genre_pairs": _finalize(pairs),
        "languages": _finalize(languages),
        "decades": _finalize(decades),
        "studios": _finalize(studios),
        "tags": _finalize(tags),
        "media_types": _finalize(buckets, per_item=True),
        "recent_genres": _finalize(recent_genres),
        "longterm_genres": _finalize(longterm_genres),
        "keywords": _finalize(keywords),
        "tmdb_keywords": _finalize(tmdb_keywords),
        "people": _finalize(people),
        "companies": _finalize(companies),
        "collections": _finalize(collections),
        "language_evidence": sum(row["evidence"] for row in languages.values()),
        "rated_count": rated_count,
        "positive_count": len(positives),
        "negative_count": len(negatives),
        "seed_docs": seed_docs,
        "engine": "structured_taste_v2",
    }
    return snapshot


def genre_pair(left: str, right: str) -> str:
    return "|".join(sorted((str(left).casefold(), str(right).casefold())))


def _affinity(store: Dict[str, Dict[str, float]], name: str) -> Tuple[float, float]:
    row = store.get(name) or store.get(str(name).casefold()) or {}
    return float(row.get("affinity") or 0.0), float(row.get("confidence") or 0.0)


def _lookup_casefold(store: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
    return {str(name).casefold(): row for name, row in (store or {}).items()}


def candidate_affinity(candidate: Dict[str, Any], taste: Dict[str, Any]) -> float:
    """Confidence-weighted taste match in roughly -1..1.

    v1 returned an unbounded sum of fixed per-genre bonuses, which let any
    Action/Adventure title score as highly as something the user actually rated.
    """
    genre_profile = _lookup_casefold(taste.get("genres") or {})
    if not genre_profile:
        # Legacy snapshot: fall back to the ordered v1 lists.
        favorites = [str(name).casefold() for name in (taste.get("favorite_genres") or [])]
        least = {str(name).casefold() for name in (taste.get("least_preferred_genres") or [])}
        names = {str(name).casefold() for name in (candidate.get("genres") or [])}
        if not names:
            return 0.0
        hit = sum(1.0 - index * 0.1 for index, name in enumerate(favorites) if name in names)
        return max(-1.0, min(1.0, (hit - 1.5 * len(names & least)) / max(len(names), 1)))

    names = [str(name).casefold() for name in (candidate.get("genres") or []) if name]
    if not names:
        return 0.0
    total = 0.0
    weight_sum = 0.0
    for name in names:
        affinity, confidence = _affinity(genre_profile, name)
        total += affinity * (0.35 + 0.65 * confidence)
        weight_sum += 1.0
    score = total / max(weight_sum, 1.0)

    pair_profile = _lookup_casefold(taste.get("genre_pairs") or {})
    if pair_profile and len(names) > 1:
        best = 0.0
        for first in range(len(names)):
            for second in range(first + 1, len(names)):
                affinity, confidence = _affinity(pair_profile, genre_pair(names[first], names[second]))
                best = max(best, affinity * (0.3 + 0.7 * confidence)) if affinity > 0 else best
        score = score * 0.75 + best * 0.25
    return round(max(-1.0, min(1.0, score)), 4)
