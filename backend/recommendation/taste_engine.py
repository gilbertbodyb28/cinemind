"""Unified taste features from normalized history and feedback. Not an LLM dump."""

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from .media_identity import normalize_media_type, unique_taste_docs

DEFAULT_PROVIDER_WEIGHTS = {
    "plex": 1.0,
    "trakt": 1.0,
    "simkl": 1.0,
    "anilist": 1.5,
    "demo": 0.25,
}


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp
    except ValueError:
        return None


def evidence_score(item: Dict[str, Any], feedback_by_id: Dict[str, str]) -> float:
    status = (item.get("status") or "").upper()
    if status == "PLANNING":
        return 0.0

    canonical = item.get("canonical_media_id")
    fb = feedback_by_id.get(canonical or "", "")
    if fb == "blacklist":
        return -4.0
    if fb == "dislike":
        return -2.5
    if fb == "like":
        return 2.2

    score = 1.0
    rating = item.get("rating")
    scale = float(item.get("rating_scale") or 10) or 10
    if rating is not None:
        normalized = float(rating) / scale * 10
        if normalized >= 8:
            score = 3.0
        elif normalized <= 4:
            score = -2.0
        else:
            score = 1.2
    if item.get("favorite"):
        score = max(score, 2.6)
    if item.get("rewatched") or int(item.get("watch_count") or 1) > 1:
        score = max(score, 2.0) if score > 0 else score
    if status == "COMPLETED" or item.get("completed"):
        score = max(score, 1.4) if score > 0 else score
    if status == "DROPPED" and (rating is None or float(rating) <= 5):
        score = min(score, -3.0)
    if status == "REPEATING":
        score = max(score, 2.8) if score > 0 else score

    watched_at = _parse_dt(item.get("last_watched_at") or item.get("watched_at"))
    if watched_at and score > 0:
        days = (datetime.now(timezone.utc) - watched_at).days
        if days <= 30:
            score += 0.3
        elif days > 365:
            score *= 0.85
    return score


def _count_by_provider(items: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        provider = item.get("provider") or item.get("source") or "unknown"
        counts[provider] = counts.get(provider, 0) + 1
    return counts


def build_taste_snapshot(
    history: List[Dict[str, Any]],
    feedback: Optional[List[Dict[str, Any]]] = None,
    provider_weights: Optional[Dict[str, float]] = None,
    taste_sources: Optional[List[str]] = None,
) -> Dict[str, Any]:
    weights = {**DEFAULT_PROVIDER_WEIGHTS, **(provider_weights or {})}
    feedback_by_id = {
        row["canonical_media_id"]: row.get("action")
        for row in (feedback or [])
        if row.get("canonical_media_id")
    }
    # taste_sources=None → all providers; [] → none; ["simkl"] → only those.
    if taste_sources is None:
        docs = list(unique_taste_docs(history))
    elif not taste_sources:
        docs = []
    else:
        allowed = set(taste_sources)
        docs = [
            doc for doc in unique_taste_docs(history)
            if (doc.get("provider") or doc.get("source") or "unknown") in allowed
        ]
    provider_counts = _count_by_provider(docs) or {"unknown": 1}

    genre_scores: Dict[str, float] = {}
    year_scores: Dict[int, float] = {}
    language_scores: Dict[str, float] = {}
    positives: List[Dict[str, Any]] = []
    negatives: List[Dict[str, Any]] = []
    rewatched: List[str] = []

    for item in docs:
        provider = item.get("provider") or item.get("source") or "unknown"
        raw = evidence_score(item, feedback_by_id)
        share = 1.0 / max(provider_counts.get(provider, 1), 1)
        weighted = raw * weights.get(provider, 1.0) * share * len(provider_counts)
        for genre in item.get("genres") or []:
            genre_scores[genre] = genre_scores.get(genre, 0.0) + weighted
        if item.get("year"):
            year = int(item["year"])
            year_scores[year] = year_scores.get(year, 0.0) + weighted
        language = item.get("original_language")
        if language:
            language_scores[language] = language_scores.get(language, 0.0) + weighted
        title = item.get("title")
        if weighted >= 1.5 and title:
            positives.append({"title": title, "year": item.get("year"), "score": round(weighted, 3)})
        if weighted <= -1.0 and title:
            negatives.append({"title": title, "year": item.get("year"), "score": round(weighted, 3)})
        if item.get("rewatched") and title:
            rewatched.append(title)

    liked_genres: Dict[str, float] = {}
    disliked_genres: Dict[str, float] = {}
    preference_reasons: List[Dict[str, Any]] = []
    seen_feedback_titles: set[str] = set()
    for row in feedback or []:
        action = row.get("action")
        title = row.get("title")
        genres = [genre for genre in (row.get("genres") or []) if genre]
        if action not in {"like", "dislike", "blacklist"} or not title:
            continue
        if title in seen_feedback_titles and action != "blacklist":
            continue
        seen_feedback_titles.add(title)
        delta = 3.5 if action == "like" else -3.5
        if action == "blacklist":
            delta = -4.5
        for genre in genres:
            genre_scores[genre] = genre_scores.get(genre, 0.0) + delta
            if delta > 0:
                liked_genres[genre] = liked_genres.get(genre, 0.0) + delta
            else:
                disliked_genres[genre] = disliked_genres.get(genre, 0.0) + abs(delta)
        if action == "like":
            positives.append({"title": title, "year": row.get("year"), "score": 3.5})
            preference_reasons.append({
                "kind": "liked",
                "title": title,
                "effect": ", ".join(genres[:3]) or "positive signal",
            })
        else:
            negatives.append({"title": title, "year": row.get("year"), "score": delta})
            preference_reasons.append({
                "kind": "disliked" if action == "dislike" else "blacklisted",
                "title": title,
                "effect": ", ".join(genres[:3]) or "negative signal",
            })

    favorite_genres = [name for name, _ in sorted(genre_scores.items(), key=lambda kv: -kv[1]) if genre_scores[name] > 0][:8]
    least_genres = [name for name, _ in sorted(genre_scores.items(), key=lambda kv: kv[1]) if genre_scores[name] < 0][:5]
    eras = []
    if year_scores:
        decade_scores: Dict[str, float] = {}
        for year, score in year_scores.items():
            decade = f"{(year // 10) * 10}s"
            decade_scores[decade] = decade_scores.get(decade, 0.0) + score
        eras = [name for name, _ in sorted(decade_scores.items(), key=lambda kv: -kv[1])[:4]]

    return {
        "favorite_genres": favorite_genres,
        "least_preferred_genres": least_genres,
        "liked_genres": [name for name, _ in sorted(liked_genres.items(), key=lambda kv: -kv[1])[:8]],
        "disliked_genres": [name for name, _ in sorted(disliked_genres.items(), key=lambda kv: -kv[1])[:8]],
        "favorite_eras": eras,
        "preferred_languages": [name for name, _ in sorted(language_scores.items(), key=lambda kv: -kv[1])[:4]],
        "high_confidence_positive_titles": sorted(positives, key=lambda row: -row["score"])[:12],
        "negative_titles": sorted(negatives, key=lambda row: row["score"])[:12],
        "frequently_rewatched_titles": list(dict.fromkeys(rewatched))[:12],
        "preference_reasons": preference_reasons[:12],
        "provider_weights": weights,
        "item_count": len(docs),
        "engine": "unified_taste_v1",
    }


def candidate_affinity(candidate: Dict[str, Any], taste: Dict[str, Any]) -> float:
    score = 0.0
    genres = {genre.casefold() for genre in (candidate.get("genres") or [])}
    for index, genre in enumerate(taste.get("favorite_genres") or []):
        if genre.casefold() in genres:
            score += 3.0 - index * 0.25
    for genre in taste.get("least_preferred_genres") or []:
        if genre.casefold() in genres:
            score -= 2.5
    year = candidate.get("year")
    if year and taste.get("favorite_eras"):
        decade = f"{(int(year) // 10) * 10}s"
        if decade in taste["favorite_eras"]:
            score += 1.2
    return score
