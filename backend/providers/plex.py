"""Plex helpers. Library membership is not the same as watch history."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def plex_headers(token: str) -> Dict[str, str]:
    return {"X-Plex-Token": token, "Accept": "application/json"}


def plex_page_headers(token: str, start: int = 0, size: int = 50) -> Dict[str, str]:
    return {
        **plex_headers(token),
        "X-Plex-Container-Start": str(start),
        "X-Plex-Container-Size": str(size),
    }


def container_total(payload: Dict[str, Any]) -> int:
    container = payload.get("MediaContainer") or {}
    return int(container.get("totalSize") or container.get("size") or 0)


def next_page_start(start: int, size: int, total: int) -> Optional[int]:
    nxt = start + size
    return nxt if nxt < total else None


def _base_item(metadata: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "title": metadata.get("title", "Unknown"),
        "year": metadata.get("year"),
        "type": "movie" if metadata.get("type") == "movie" else "show",
        "genres": [genre.get("tag") for genre in metadata.get("Genre", []) if genre.get("tag")],
        "poster": None,
        "plex_rating_key": str(metadata["ratingKey"]) if metadata.get("ratingKey") is not None else None,
        "source": "plex",
        "provider": "plex",
    }


def classify_library_metadata(metadata: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Split a Plex /library item into library membership vs actual watch history.

    Presence in the library is never treated as a watch. History is emitted only
    when Plex reports lastViewedAt.
    """
    library = {
        **_base_item(metadata),
        "in_library": True,
        "provider_specific_metadata": {
            "audience_rating": metadata.get("audienceRating"),
            "view_count": metadata.get("viewCount"),
            "last_viewed_at": metadata.get("lastViewedAt"),
        },
    }
    if not metadata.get("lastViewedAt"):
        return library, None
    history = {
        **_base_item(metadata),
        "watched_at": datetime.fromtimestamp(metadata["lastViewedAt"], tz=timezone.utc).isoformat(),
        "last_watched_at": datetime.fromtimestamp(metadata["lastViewedAt"], tz=timezone.utc).isoformat(),
        "watch_count": int(metadata.get("viewCount") or 1),
        "rewatched": int(metadata.get("viewCount") or 1) > 1,
        "rating": None,
        "provider_specific_metadata": library["provider_specific_metadata"],
    }
    return library, history


def parse_watch_history_metadata(metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map a Plex history-endpoint row. viewedAt/lastViewedAt is required."""
    viewed = metadata.get("viewedAt") or metadata.get("lastViewedAt")
    if not viewed:
        return None
    row = _base_item(metadata)
    stamp = datetime.fromtimestamp(int(viewed), tz=timezone.utc).isoformat()
    row.update({
        "watched_at": stamp,
        "last_watched_at": stamp,
        "watch_count": int(metadata.get("viewCount") or 1),
        "rewatched": int(metadata.get("viewCount") or 1) > 1,
        "rating": None,
        "provider_specific_metadata": {"viewed_at": viewed, "account_id": metadata.get("accountID")},
    })
    return row


def classify_history_payload(payload: Dict[str, Any], limit: int = 50) -> List[Dict[str, Any]]:
    rows = (payload.get("MediaContainer") or {}).get("Metadata") or []
    history: List[Dict[str, Any]] = []
    for metadata in rows[:limit]:
        parsed = parse_watch_history_metadata(metadata)
        if parsed:
            history.append(parsed)
    return history


def classify_library_payload(payload: Dict[str, Any], limit: int = 50) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    rows = (payload.get("MediaContainer") or {}).get("Metadata") or []
    library: List[Dict[str, Any]] = []
    history: List[Dict[str, Any]] = []
    for metadata in rows[:limit]:
        lib_row, hist_row = classify_library_metadata(metadata)
        library.append(lib_row)
        if hist_row:
            history.append(hist_row)
    return library, history
