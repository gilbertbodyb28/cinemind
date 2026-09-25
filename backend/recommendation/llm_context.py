"""Compact taste context for Ollama. Never dump raw history or secrets.

The model is a re-ranker, not the title database, so it gets a structured
summary of the profile rather than thousands of watched rows. v1 sent eight
generic genre names and no titles at all - about 180 characters - which left
the model nothing to rank with.

The loved titles it sees are the job's own lane first. Gilbert's strongest
titles are anime, so an English TV job used to show the model twelve loved
titles of which three were live action, and the model had to guess what this
viewer likes in a series from his favourite isekai. The Requests queue is
shown too: what he approved and turned down is the most direct statement of
taste CineMind has.

Nothing here is a label the offline harness scores against: held-out titles
never reach the profile the text is built from (evaluation.offline).
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
    from .taste_engine import genre_label

    lines = []
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("title"):
            continue
        marks = []
        if row.get("rating") is not None:
            marks.append("rated %.0f/10" % float(row["rating"]))
        elif row.get("decision") == "approved":
            marks.append("approved in Requests")
        if (row.get("plays") or 0) > 3:
            marks.append("%d episodes watched" % int(row["plays"]))
        genres = ", ".join(genre_label(name) for name in (row.get("genres") or [])[:3])
        themes = ", ".join((row.get("tmdb_keywords") or [])[:3])
        detail = "; ".join(filter(None, [genres, themes, ", ".join(marks)]))
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


def _lane_first(rows: List[Dict[str, Any]], intent: Optional[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """The job's own lane first (PRIMARY tier), then at most a few from elsewhere."""
    if not intent:
        return rows[:limit]
    from .job_intent import PRIMARY, intent_tier

    inside = [row for row in rows if intent_tier(row, intent) == PRIMARY]
    outside = [row for row in rows if intent_tier(row, intent) != PRIMARY]
    keep = max(0, limit - min(len(inside), limit))
    return inside[:limit] + outside[:min(keep, 3)]


def compact_taste_prompt(taste: Optional[Dict[str, Any]], intent: Optional[Dict[str, Any]] = None) -> str:
    from .taste_engine import genre_label

    taste = taste or {}
    media = _ranked_names(taste.get("media_types"), 3)
    genres = [genre_label(name) for name in _ranked_names(taste.get("genres"), 8)] or list(taste.get("favorite_genres") or [])[:8]
    pairs = [" + ".join(genre_label(part) for part in name.split(" + ")) for name in _ranked_names(taste.get("genre_pairs"), 6)]
    avoid = [genre_label(name) for name in _ranked_names(taste.get("genres"), 5, positive=False)] or list(
        taste.get("disliked_genres") or taste.get("least_preferred_genres") or [])[:5]
    lines = [
        "Viewer profile (built from watch history, personal ratings and explicit feedback).",
        "Preferred formats: " + ", ".join(name.replace("_", " ") for name in media),
        "Favourite genres: " + ", ".join(genres),
        "Favourite genre combinations: " + ", ".join(pairs),
        "Avoids: " + ", ".join(avoid),
        "Recurring themes: " + ", ".join(_ranked_names(taste.get("tmdb_keywords"), 12)),
        "Creators and cast they follow: " + ", ".join(
            name.split(":", 1)[-1] for name in _ranked_names(taste.get("people"), 8)
        ),
        "Studios and networks they watch: " + ", ".join(_ranked_names(taste.get("companies"), 6)),
        "Favourite eras: " + ", ".join((taste.get("favorite_eras") or [])[:4]),
        "Languages: " + ", ".join((taste.get("preferred_languages") or [])[:4]),
    ]
    liked = list(taste.get("liked_titles") or taste.get("high_confidence_positive_titles") or [])
    evidence = _evidence_list(_lane_first(liked, intent, 12), 12)
    if evidence:
        lines.append("Titles this viewer demonstrably loves%s:" % (" (this job's kind first)" if intent else ""))
        lines.extend(evidence)
    negatives = list(taste.get("negative_titles") or [])
    turned_down = [row for row in negatives if row.get("decision") == "rejected"]
    disliked = [row for row in negatives if row.get("decision") != "rejected"]
    if turned_down:
        lines.append("Turned down when CineMind suggested them: " + _title_list(_lane_first(turned_down, intent, 10), 10))
    if disliked:
        lines.append("Dropped, disliked or rated badly: " + _title_list(disliked, 8))
    rewatched = ", ".join((taste.get("frequently_rewatched_titles") or [])[:6])
    if rewatched:
        lines.append("Rewatches: " + rewatched)
    text = "\n".join(line for line in lines if not line.rstrip().endswith(":"))
    return text[:MAX_PROMPT_CHARS]


def candidate_evidence(row: Dict[str, Any], taste: Optional[Dict[str, Any]] = None) -> str:
    """The deterministic evidence behind one candidate, in a few words.

    "like Arrow (creator Greg Berlanti; themes superhero)" gives the model the
    same reason the ranking used, so it re-ranks on taste rather than on which
    titles it happens to have heard of.
    """
    from .similarity import explain_match

    references = {str(item.get("title") or "").casefold(): item
                  for item in ((taste or {}).get("liked_titles") or (taste or {}).get("high_confidence_positive_titles") or [])}
    parts = []
    for match in (row.get("similar_to") or [])[:2]:
        liked = references.get(str(match.get("title") or "").casefold())
        if not liked:
            continue
        shared = [part for part in explain_match(row, liked) if not part.startswith("genres ")][:2]
        parts.append("like %s%s" % (liked.get("title"), " (%s)" % "; ".join(shared) if shared else ""))
    if row.get("resembles_rejected"):
        parts.append("resembles %s, which was turned down" % row["resembles_rejected"])
    return "; ".join(parts)
