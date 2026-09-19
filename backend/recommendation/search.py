"""Deterministic AI Search parsing. Ollama may refine the same structured intent."""

from typing import Any, Dict, List, Optional
import re

GENRE_ALIASES = {
    "sci-fi": "Sci-Fi",
    "science fiction": "Sci-Fi",
    "thriller": "Thriller",
    "drama": "Drama",
    "comedy": "Comedy",
    "horror": "Horror",
    "crime": "Crime",
    "history": "History",
    "action": "Action",
    "romance": "Romance",
    "anime": "Anime",
}


def parse_search_query(query: str) -> Dict[str, Any]:
    text = (query or "").strip()
    lowered = text.casefold()
    media_types: List[str] = []
    if re.search(r"\banime\b", lowered):
        media_types = ["anime"]
    elif re.search(r"\b(tv|series|show)s?\b", lowered):
        media_types = ["tv"]
    elif re.search(r"\b(movie|film)s?\b", lowered):
        media_types = ["movie"]
    genres = [label for alias, label in GENRE_ALIASES.items() if alias in lowered]
    year = None
    match = re.search(r"(?:after|since|from)\s+(19|20)\d{2}", lowered)
    if match:
        year = int(re.search(r"(19|20)\d{2}", match.group(0)).group(0))
    exact = re.search(r"\b((?:19|20)\d{2})\b", lowered)
    decade = re.search(r"\b((?:19|20)\d)0s\b", lowered)
    min_year = year
    max_year = None
    if decade:
        start = int(decade.group(1) + "0")
        min_year = min_year or start
        max_year = start + 9
    search_text = lowered
    for alias in sorted(GENRE_ALIASES, key=len, reverse=True):
        search_text = re.sub(rf"\b{re.escape(alias)}s?\b", " ", search_text)
    search_text = re.sub(
        r"\b(tv|series|shows?|movies?|films?|anime|after|since|from|the|with|major|plot|twists?)\b",
        " ",
        search_text,
    )
    search_text = re.sub(r"\b(?:19|20)\d{2}s?\b", " ", search_text)
    search_text = re.sub(r"\s+", " ", search_text).strip()
    return {
        "query": text,
        "search_text": search_text,
        "media_types": media_types or ["movie", "tv"],
        "include_genres": list(dict.fromkeys(genres)),
        "min_year": min_year,
        "max_year": max_year,
        "year": int(exact.group(1)) if exact and not year and not decade else None,
    }


ALLOWED_MEDIA = {"movie", "tv", "anime"}


def _clamp_year(value: Any) -> Optional[int]:
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    if 1900 <= year <= 2035:
        return year
    return None


def merge_search_intent(base: Dict[str, Any], extra: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Overlay LLM intent onto the deterministic parse. Unknown genres/years are ignored."""
    if not isinstance(extra, dict):
        return base
    out = dict(base)
    media = [item for item in (extra.get("media_types") or []) if item in ALLOWED_MEDIA]
    if media:
        out["media_types"] = media
    genres = list(out.get("include_genres") or [])
    for raw in extra.get("include_genres") or []:
        label = GENRE_ALIASES.get(str(raw).casefold())
        if not label and str(raw) in GENRE_ALIASES.values():
            label = str(raw)
        if label and label not in genres:
            genres.append(label)
    if genres:
        out["include_genres"] = genres
    min_year = _clamp_year(extra.get("min_year"))
    max_year = _clamp_year(extra.get("max_year"))
    if min_year is not None:
        out["min_year"] = min_year
    if max_year is not None:
        out["max_year"] = max_year
    search_text = extra.get("search_text")
    if isinstance(search_text, str) and search_text.strip():
        out["search_text"] = search_text.strip()[:80]
    return out
