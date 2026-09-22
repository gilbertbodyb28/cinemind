"""Compact taste context for Ollama. Never dump raw history or secrets.

The model is a re-ranker, not the title database, so it gets a structured
summary of the profile rather than thousands of watched rows. v1 sent eight
generic genre names and no titles at all - about 180 characters - which left
the model nothing to rank with.
"""

from typing import Any, Dict, List, Optional

MAX_PROMPT_CHARS = 4000


def _title_list(rows: Any, limit: int = 8) -> str:
    labels = []
    for row in rows or []:
        if isinstance(row, dict):
            title = row.get("title")
            if not title:
                continue
            year = row.get("year")
            labels.append("%s (%s)" % (title, year) if year else str(title))
        else:
            labels.append(str(row))
        if len(labels) >= limit:
            break
    return ", ".join(labels)


def _evidence_list(rows: Any, limit: int = 10) -> List[str]:
    """Loved titles with the reason they count, so the model can generalize."""
    lines = []
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("title"):
            continue
        marks = []
        if row.get("rating") is not None:
            marks.append("rated %.0f/10" % float(row["rating"]))
        if (row.get("plays") or 0) > 3:
            marks.append("%d episodes watched" % int(row["plays"]))
        genres = ", ".join((row.get("genres") or [])[:3])
        detail = "; ".join(filter(None, [genres, ", ".join(marks)]))
        year = row.get("year")
        lines.append("- %s%s%s" % (
            row["title"],
            " (%s)" % year if year else "",
            " - %s" % detail if detail else "",
        ))
        if len(lines) >= limit:
            break
    return lines


def _ranked_names(profile: Optional[Dict[str, Any]], limit: int, positive: bool = True) -> List[str]:
    rows = [
        (name, row) for name, row in (profile or {}).items()
        if (row.get("affinity", 0) > 0) == positive and row.get("affinity", 0) != 0
    ]
    rows.sort(key=lambda pair: -abs(pair[1]["affinity"] * (0.4 + 0.6 * pair[1].get("confidence", 0))))
    return [str(name).replace("|", " + ") for name, _ in rows[:limit]]


def compact_taste_prompt(taste: Optional[Dict[str, Any]]) -> str:
    taste = taste or {}
    media = _ranked_names(taste.get("media_types"), 3)
    lines = [
        "Viewer profile (built from watch history, personal ratings and explicit feedback).",
        "Preferred formats: " + ", ".join(name.replace("_", " ") for name in media),
        "Favourite genres: " + ", ".join(
            _ranked_names(taste.get("genres"), 8) or (taste.get("favorite_genres") or [])[:8]
        ),
        "Favourite genre combinations: " + ", ".join(_ranked_names(taste.get("genre_pairs"), 6)),
        "Avoids: " + ", ".join(
            _ranked_names(taste.get("genres"), 5, positive=False)
            or (taste.get("disliked_genres") or taste.get("least_preferred_genres") or [])[:5]
        ),
        "Favourite eras: " + ", ".join((taste.get("favorite_eras") or [])[:4]),
        "Languages: " + ", ".join((taste.get("preferred_languages") or [])[:4]),
    ]
    evidence = _evidence_list(taste.get("high_confidence_positive_titles"), 12)
    if evidence:
        lines.append("Titles this viewer demonstrably loves:")
        lines.extend(evidence)
    negatives = _title_list(taste.get("negative_titles"), 8)
    if negatives:
        lines.append("Dropped, disliked or rated badly: " + negatives)
    rewatched = ", ".join((taste.get("frequently_rewatched_titles") or [])[:6])
    if rewatched:
        lines.append("Rewatches: " + rewatched)
    text = "\n".join(line for line in lines if not line.rstrip().endswith(":"))
    return text[:MAX_PROMPT_CHARS]
