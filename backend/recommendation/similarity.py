"""Similarity between a candidate and the titles the user demonstrably likes.

Genre-name overlap alone is far too broad - "Action" matches half the catalogue.
This layer compares genre *combinations*, language, era, studio, tags and the
words of the synopsis, so a title can rank because it resembles something the
user actually rated rather than because it shares one generic label.

Deliberately dependency-free: a lexical overlap on the synopsis is a weak but
honest semantic proxy, and it costs nothing at run time. Anything heavier has
to earn its place in the evaluation harness first.
"""

from typing import Any, Dict, List, Optional, Sequence
import math

from .media_identity import coerce_int
from .taste_engine import genre_pair, media_bucket, overview_tokens

# Genre labels used to carry 0.42 of the comparison and everything else was
# structurally dead - measured on real data, two unrelated "Action, Drama"
# shows came out bit-identical to four decimals. TMDb keywords and people are
# what actually separate titles inside a genre, so they carry most of it now.
GENRE_WEIGHT = 0.26
PAIR_WEIGHT = 0.12
KEYWORD_WEIGHT = 0.22
PEOPLE_WEIGHT = 0.14
TEXT_WEIGHT = 0.09
LANGUAGE_WEIGHT = 0.06
COMPANY_WEIGHT = 0.05
BUCKET_WEIGHT = 0.03
COLLECTION_WEIGHT = 0.02
ERA_WEIGHT = 0.01
# Sharing five specific keywords ("space opera", "chosen one") is as strong a
# statement as two titles ever make. Jaccard over 24-term lists would price
# that at 0.1 and let the genre label win again.
KEYWORD_SATURATION = 5.0


def _names(row: Dict[str, Any], field: str = "genres") -> set:
    return {str(name).casefold() for name in (row.get(field) or []) if name}


def _jaccard(left: set, right: set) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _shared(left: set, right: set, saturation: float) -> float:
    if not left or not right:
        return 0.0
    return min(1.0, len(left & right) / saturation)


def _pairs(names: Sequence[str]) -> set:
    ordered = sorted(names)
    return {
        genre_pair(ordered[first], ordered[second])
        for first in range(len(ordered))
        for second in range(first + 1, len(ordered))
    }


def _era_distance(left: Dict[str, Any], right: Dict[str, Any]) -> float:
    first, second = coerce_int(left.get("year")), coerce_int(right.get("year"))
    if first is None or second is None:
        return 0.0
    return max(0.0, 1.0 - abs(first - second) / 30.0)


def _text(row: Dict[str, Any]) -> set:
    cached = row.get("_tokens")
    if cached is not None:
        return cached
    tokens = set(overview_tokens(row.get("overview") or row.get("synopsis")))
    row["_tokens"] = tokens
    return tokens


def pair_similarity(candidate: Dict[str, Any], liked: Dict[str, Any]) -> float:
    """0..1 similarity between one candidate and one liked title."""
    candidate_genres, liked_genres = _names(candidate), _names(liked)
    score = GENRE_WEIGHT * _jaccard(candidate_genres, liked_genres)
    score += PAIR_WEIGHT * _jaccard(_pairs(candidate_genres), _pairs(liked_genres))
    themes = _names(candidate, "tmdb_keywords") | _names(candidate, "tags")
    liked_themes = _names(liked, "tmdb_keywords") | _names(liked, "tags")
    score += KEYWORD_WEIGHT * _shared(themes, liked_themes, KEYWORD_SATURATION)
    creators = _names(candidate, "creators") & _names(liked, "creators")
    cast = _names(candidate, "cast") & _names(liked, "cast")
    if creators or cast:
        score += PEOPLE_WEIGHT * min(1.0, 0.75 * len(creators) + 0.25 * len(cast))
    score += TEXT_WEIGHT * _jaccard(_text(candidate), _text(liked))
    left = str(candidate.get("original_language") or "").casefold()
    right = str(liked.get("original_language") or "").casefold()
    if left and right and left == right:
        score += LANGUAGE_WEIGHT
    shared_companies = (_names(candidate, "companies") & _names(liked, "companies")) | (
        _names(candidate, "studios") & _names(liked, "studios")
    )
    if shared_companies:
        score += COMPANY_WEIGHT
    if media_bucket(candidate) == media_bucket(liked):
        score += BUCKET_WEIGHT
    collection = str(candidate.get("collection") or "").casefold()
    if collection and collection == str(liked.get("collection") or "").casefold():
        score += COLLECTION_WEIGHT
    score += ERA_WEIGHT * _era_distance(candidate, liked)
    return round(min(1.0, score), 4)


def best_similarity(
    candidate: Dict[str, Any],
    titles: Sequence[Dict[str, Any]],
    limit: int = 24,
) -> Dict[str, Any]:
    """Strongest match against the reference titles, with the title that matched.

    Weighted by how much evidence stands behind each reference title, so a 10/10
    rating pulls harder than something merely finished once.
    """
    best_score = 0.0
    best_title: Optional[Dict[str, Any]] = None
    runners: List[Dict[str, Any]] = []
    peak_evidence = max((abs(float(row.get("score") or 0)) for row in titles[:limit]), default=1.0) or 1.0
    for liked in titles[:limit]:
        raw = pair_similarity(candidate, liked)
        if raw <= 0:
            continue
        evidence = min(1.0, abs(float(liked.get("score") or 0)) / peak_evidence)
        weighted = raw * (0.55 + 0.45 * evidence)
        runners.append({"title": liked.get("title"), "similarity": round(weighted, 4)})
        if weighted > best_score:
            best_score, best_title = weighted, liked
    runners.sort(key=lambda row: -row["similarity"])
    return {
        "score": round(min(1.0, best_score), 4),
        "title": (best_title or {}).get("title"),
        "year": (best_title or {}).get("year"),
        "matches": runners[:3],
    }


def keyword_affinity(candidate: Dict[str, Any], taste: Dict[str, Any]) -> float:
    """How much of the candidate's synopsis vocabulary the user's favourites share.

    Deliberately one-sided: an absent overlap counts for nothing rather than
    against the title. Centring it on an expected coverage was tried and
    measured worse (holdout P@5 1.000 -> 0.800, NDCG@10 0.929 -> 0.693),
    because a title with a short or missing synopsis is not a bad match - it is
    an unknown one, and `metadata_confidence` already prices that in.
    """
    themes = _names(candidate, "tmdb_keywords") | _names(candidate, "tags")
    keyword_profile = taste.get("tmdb_keywords") or {}
    total = 0.0
    counted = 0
    for name in themes:
        row = keyword_profile.get(name)
        if row:
            total += float(row.get("affinity") or 0.0) * (0.4 + 0.6 * float(row.get("confidence") or 0.0))
        counted += 1
    profile = taste.get("keywords") or {}
    if profile:
        tokens = _text(candidate)
        for token in tokens:
            row = profile.get(token)
            if row:
                total += 0.5 * float(row.get("affinity") or 0.0) * (0.4 + 0.6 * float(row.get("confidence") or 0.0))
        counted += len(tokens)
    if not counted:
        return 0.0
    return round(max(-1.0, min(1.0, total / math.sqrt(max(counted, 1)))), 4)


def people_affinity(candidate: Dict[str, Any], taste: Dict[str, Any]) -> float:
    """Directors, creators and recurring cast the user keeps coming back to.

    The strongest single match wins rather than the sum: one favourite director
    should not need a full cast behind them, and a large ensemble should not
    out-score them by volume alone.
    """
    profile = taste.get("people") or {}
    if not profile:
        return 0.0
    best = 0.0
    for field, prefix in (("creators", "creator"), ("cast", "cast")):
        for name in _names(candidate, field):
            row = profile.get("%s:%s" % (prefix, name))
            if not row:
                continue
            value = float(row.get("affinity") or 0.0) * (0.4 + 0.6 * float(row.get("confidence") or 0.0))
            best = value if abs(value) > abs(best) else best
    return round(max(-1.0, min(1.0, best)), 4)


def franchise_affinity(candidate: Dict[str, Any], taste: Dict[str, Any]) -> float:
    """Same collection or same studio as something the user rated highly."""
    collections = taste.get("collections") or {}
    collection = str(candidate.get("collection") or "").casefold()
    if collection and collections.get(collection):
        row = collections[collection]
        return round(max(-1.0, min(1.0, float(row.get("affinity") or 0.0))), 4)
    profile = taste.get("companies") or {}
    studios = taste.get("studios") or {}
    best = 0.0
    for field, store in (("companies", profile), ("studios", studios)):
        if not store:
            continue
        for name in _names(candidate, field):
            row = store.get(name) or store.get(str(name))
            if not row:
                continue
            value = float(row.get("affinity") or 0.0) * (0.4 + 0.6 * float(row.get("confidence") or 0.0))
            best = value if abs(value) > abs(best) else best
    return round(max(-1.0, min(1.0, best)), 4)


def media_type_affinity(candidate: Dict[str, Any], taste: Dict[str, Any]) -> float:
    """Anime, anime films, series and films are separate lanes with separate taste."""
    profile = taste.get("media_types") or {}
    row = profile.get(media_bucket(candidate)) or {}
    return round(float(row.get("affinity") or 0.0) * (0.4 + 0.6 * float(row.get("confidence") or 0.0)), 4)


def recency_affinity(candidate: Dict[str, Any], taste: Dict[str, Any]) -> float:
    """What the user has been watching lately, kept separate from lifetime taste."""
    profile = {str(name).casefold(): row for name, row in (taste.get("recent_genres") or {}).items()}
    if not profile:
        return 0.0
    names = _names(candidate)
    if not names:
        return 0.0
    total = sum(
        float((profile.get(name) or {}).get("affinity") or 0.0)
        * (0.4 + 0.6 * float((profile.get(name) or {}).get("confidence") or 0.0))
        for name in names
    )
    return round(max(-1.0, min(1.0, total / len(names))), 4)
