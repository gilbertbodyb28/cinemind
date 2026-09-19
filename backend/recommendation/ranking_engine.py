"""Deterministic candidate scoring. Ollama may only reorder verified IDs."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .taste_engine import candidate_affinity


def score_candidates(candidates: List[Dict[str, Any]], taste: Dict[str, Any]) -> List[Dict[str, Any]]:
    scored = []
    positives = {row.get("title") for row in (taste.get("high_confidence_positive_titles") or [])}
    negatives = {row.get("title") for row in (taste.get("negative_titles") or [])}
    current_year = datetime.now(timezone.utc).year
    for candidate in candidates:
        affinity = candidate_affinity(candidate, taste)
        seed = float(candidate.get("candidate_score") or 0)
        rating = float(candidate.get("tmdb_rating") or candidate.get("rating") or 0) / 10.0
        raw_votes = float(candidate.get("vote_count") or 0)
        votes = min(raw_votes / 1000.0, 1.5)
        year = candidate.get("year")
        # Penalize high-rated / zero-evidence junk that sneaks through upcoming windows.
        quality_penalty = 0.0
        if raw_votes <= 0:
            quality_penalty = -8.0 if (year is None or int(year) <= current_year) else -3.0
        elif raw_votes < 25:
            quality_penalty = -4.0
        elif raw_votes < 100:
            quality_penalty = -1.5
        popularity = min(float(candidate.get("popularity") or 0) / 40.0, 2.5)
        title = candidate.get("title")
        positive_sim = 1.2 if title in positives else 0.0
        negative_sim = -2.0 if title in negatives else 0.0
        candidate_genres = {genre.casefold() for genre in (candidate.get("genres") or [])}
        liked = {genre.casefold() for genre in (taste.get("liked_genres") or [])}
        disliked = {genre.casefold() for genre in (taste.get("disliked_genres") or [])}
        feedback_overlap = 2.0 * len(candidate_genres & liked) - 2.0 * len(candidate_genres & disliked)
        lang = (candidate.get("original_language") or "").casefold()
        preferred_langs = {item.casefold() for item in (taste.get("preferred_languages") or [])}
        language_preference = 0.8 if lang and lang in preferred_langs else 0.0
        # Era boost already lives inside candidate_affinity — keep release_preference
        # visible in components but out of the total so eras are not double-counted.
        release_preference = 0.0
        if year and taste.get("favorite_eras"):
            decade = f"{(int(year) // 10) * 10}s"
            if decade in taste["favorite_eras"]:
                release_preference = 1.2
        seed_similarity = seed * 0.35
        components = {
            "genre_affinity": round(affinity, 3),
            "seed_similarity": round(seed_similarity, 3),
            "rating_quality": round(rating, 3),
            "vote_confidence": round(votes, 3),
            "popularity": round(popularity, 3),
            "quality_penalty": round(quality_penalty, 3),
            "positive_title_similarity": positive_sim,
            "negative_title_similarity": negative_sim,
            "feedback_overlap": round(feedback_overlap, 3),
            "language_preference": round(language_preference, 3),
            "release_preference": round(release_preference, 3),
        }
        total = sum(value for key, value in components.items() if key != "release_preference")
        row = dict(candidate)
        row["score_components"] = components
        row["deterministic_score"] = round(total, 3)
        row["rank_score"] = total
        if not row.get("why"):
            liked_hit = [genre for genre in (candidate.get("genres") or []) if genre.casefold() in liked][:2]
            fav_hit = [
                genre
                for genre in (candidate.get("genres") or [])
                if genre.casefold() in {g.casefold() for g in (taste.get("favorite_genres") or [])}
            ][:2]
            genres = ", ".join(liked_hit or fav_hit or (taste.get("favorite_genres") or [])[:2]) or "your watch history"
            if liked_hit or fav_hit:
                row["why"] = f"{row['title']} matches {genres} titles in your taste profile."
            else:
                row["why"] = f"{row['title']} matches {genres} in your normalized taste profile."
        scored.append(row)
    scored.sort(key=lambda row: (-row["rank_score"], row["title"]))
    if scored:
        best = scored[0]["rank_score"]
        worst = scored[-1]["rank_score"]
        span = best - worst
        for index, row in enumerate(scored):
            if span <= 0:
                row["match_score"] = max(1, min(99, 92 - index * 3))
            else:
                relative = (row["rank_score"] - worst) / span
                row["match_score"] = max(60, min(99, int(round(60 + relative * 39))))
    return scored


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
