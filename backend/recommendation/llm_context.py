"""Compact taste context for Ollama. Never dump raw history or secrets."""

from typing import Any, Dict, Optional


def _title_list(rows: Any, limit: int = 8) -> str:
    labels = []
    for row in rows or []:
        if isinstance(row, dict):
            title = row.get("title")
            if not title:
                continue
            year = row.get("year")
            labels.append(f"{title} ({year})" if year else str(title))
        else:
            labels.append(str(row))
        if len(labels) >= limit:
            break
    return ", ".join(labels)


def compact_taste_prompt(taste: Optional[Dict[str, Any]]) -> str:
    taste = taste or {}
    lines = [
        "Favorite genres: " + ", ".join((taste.get("favorite_genres") or [])[:8]),
        "Liked genres: " + ", ".join((taste.get("liked_genres") or [])[:8]),
        "Disliked genres: " + ", ".join(
            (taste.get("disliked_genres") or taste.get("least_preferred_genres") or [])[:6]
        ),
        "Favorite eras: " + ", ".join((taste.get("favorite_eras") or [])[:4]),
        "Languages: " + ", ".join((taste.get("preferred_languages") or [])[:4]),
        "Highly rated / liked: " + _title_list(taste.get("high_confidence_positive_titles")),
        "Rewatches: " + ", ".join((taste.get("frequently_rewatched_titles") or [])[:8]),
        "Negative / dropped: " + _title_list(taste.get("negative_titles")),
    ]
    return "\n".join(line for line in lines if not line.rstrip().endswith(":"))
