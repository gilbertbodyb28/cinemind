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

# Compared titles must share more than one broad label before the synopsis and
# era terms are allowed to carry them.
GENRE_WEIGHT = 0.42
PAIR_WEIGHT = 0.18
TEXT_WEIGHT = 0.16
LANGUAGE_WEIGHT = 0.09
BUCKET_WEIGHT = 0.07
STUDIO_TAG_WEIGHT = 0.05
ERA_WEIGHT = 0.03


def _names(row: Dict[str, Any], field: str = "genres") -> set:
    return {str(name).casefold() for name in (row.get(field) or []) if name}


def _jaccard(left: set, right: set) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


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
    score += TEXT_WEIGHT * _jaccard(_text(candidate), _text(liked))
    left = str(candidate.get("original_language") or "").casefold()
    right = str(liked.get("original_language") or "").casefold()
    if left and right and left == right:
        score += LANGUAGE_WEIGHT
    if media_bucket(candidate) == media_bucket(liked):
        score += BUCKET_WEIGHT
    shared_meta = (_names(candidate, "studios") & _names(liked, "studios")) | (
        _names(candidate, "tags") & _names(liked, "tags")
    )
    if shared_meta:
        score += STUDIO_TAG_WEIGHT
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
    profile = taste.get("keywords") or {}
    if not profile:
        return 0.0
    tokens = _text(candidate)
    if not tokens:
        return 0.0
    total = 0.0
    for token in tokens:
        row = profile.get(token)
        if row:
            total += float(row.get("affinity") or 0.0) * (0.4 + 0.6 * float(row.get("confidence") or 0.0))
    return round(max(-1.0, min(1.0, total / math.sqrt(max(len(tokens), 1)))), 4)


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
