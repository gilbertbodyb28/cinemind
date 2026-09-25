"""Server-side paging for the Requests queue and the Approved tab.

Both tabs used to download every request and filter, sort and count in the
browser. At 9,881 pending titles that was 4.7 MB per load and a hard cap that
hid half the queue. The same rules now run here and the tab fetches one page
at a time; the functions mirror frontend/src/lib/mediaFilters.js and the old
in-browser sort exactly, so the list reads the same as before.
"""

from typing import Any, Dict, Iterable, List, Optional, Tuple

PENDING_STATUSES = ("pending_approval", "pending", "requested")
APPROVED_STATUSES = ("approved", "available", "completed")
#: What the queue never shows: decided rows, and rows an archive batch set
#: aside (evaluation/queue_cleanup.py, reversible). Approved ones live on their own tab.
QUEUE_HIDDEN = ("rejected", "archived", *APPROVED_STATUSES)

VIEWS = {"queue", "approved"}
SORTS = {"added_desc", "added_asc", "release_desc", "release_asc", "match_desc", "title_asc"}
MAX_PAGE = 200

#: Fields filtering, sorting, facets and match scoring read. Everything else
#: (posters, synopsis, provider payloads) is fetched for the visible page only.
LIGHT_FIELDS = (
    "id", "status", "title", "year", "type", "release_date", "genres", "rating",
    "match_score", "recommendation_id", "updated_at", "created_at", "delivery_status",
)


def type_bucket(row: Dict[str, Any]) -> str:
    """Movies, TV series or Anime — anime films and donghua count as anime,
    the same buckets Home's Up Coming filter uses."""
    from recommendation.media_identity import content_lane
    kind = str(row.get("type") or "").lower()
    if kind == "anime" or content_lane(row) in {"anime", "donghua"}:
        return "anime"
    if kind in {"movie", "film"}:
        return "movie"
    return "tv"


def release_bucket(row: Dict[str, Any], today: str) -> str:
    raw = str(row.get("release_date") or "")[:10]
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-" and raw.replace("-", "").isdigit():
        return "upcoming" if raw > today else "released"
    head = raw[:4] if raw[:4].isdigit() else row.get("year")
    try:
        year = int(head)
    except (TypeError, ValueError):
        return "unknown"
    if not year:
        return "unknown"
    return "upcoming" if year > int(today[:4]) else "released"


def _added_key(row: Dict[str, Any]) -> str:
    return str(row.get("updated_at") or row.get("created_at") or "")


def _release_key(row: Dict[str, Any]) -> str:
    if row.get("release_date"):
        return str(row["release_date"])[:10]
    if row.get("year") is not None:
        return f"{row['year']}-00-00"
    return ""


def _tenths(value: Any) -> Optional[int]:
    """Rating in tenths, rounded half up like Math.round, so 7.1 never loses to 7.0999."""
    try:
        return int(float(value) * 10 + 0.5)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _checks(value: Optional[str]) -> set:
    return {item.strip() for item in str(value or "").split(",") if item.strip()}


def matches(row: Dict[str, Any], params: Dict[str, Any], today: str) -> bool:
    needle = str(params.get("q") or "").strip().lower()
    if needle and needle not in str(row.get("title") or "").lower():
        return False
    types, release = _checks(params.get("types")), _checks(params.get("release"))
    if types and type_bucket(row) not in types:
        return False
    if release and release_bucket(row, today) not in release:
        return False
    genre = params.get("genre")
    if genre and genre != "all" and genre not in (row.get("genres") or []):
        return False
    year = _as_int(row.get("year"))
    low, high = _as_int(params.get("from_year")), _as_int(params.get("to_year"))
    if low is not None and (year is None or year < low):
        return False
    if high is not None and (year is None or year > high):
        return False
    rating = _tenths(row.get("rating")) if row.get("rating") is not None else None
    r_low = _tenths(params.get("from_rating")) if params.get("from_rating") not in (None, "", "any") else None
    r_high = _tenths(params.get("to_rating")) if params.get("to_rating") not in (None, "", "any") else None
    if r_low is not None and (rating is None or rating < r_low):
        return False
    if r_high is not None and (rating is None or rating > r_high):
        return False
    return True


def sort_rows(rows: List[Dict[str, Any]], sort: str) -> List[Dict[str, Any]]:
    """Python's sort is stable like Array.prototype.sort, so ties keep the base order."""
    if sort == "added_desc":
        return sorted(rows, key=_added_key, reverse=True)
    if sort == "added_asc":
        # Undated rows go last, never first.
        return sorted(rows, key=lambda row: (not _added_key(row), _added_key(row)))
    if sort == "release_desc":
        return sorted(rows, key=_release_key, reverse=True)
    if sort == "release_asc":
        return sorted(rows, key=_release_key)
    if sort == "match_desc":
        return sorted(rows, key=lambda row: -(row.get("match_score") or 0))
    return sorted(rows, key=lambda row: str(row.get("title") or "").casefold())


def facets(rows: Iterable[Dict[str, Any]], today: str) -> Dict[str, Any]:
    """Counted on the whole tab, so each box shows what ticking it would leave."""
    genres, years = set(), set()
    type_counts: Dict[str, int] = {}
    release_counts: Dict[str, int] = {}
    kind_counts = {"movie": 0, "show": 0, "anime": 0}
    undelivered = 0
    for row in rows:
        genres.update(name for name in (row.get("genres") or []) if name)
        year = _as_int(row.get("year"))
        if year is not None:
            years.add(year)
        bucket = type_bucket(row)
        type_counts[bucket] = type_counts.get(bucket, 0) + 1
        release = release_bucket(row, today)
        release_counts[release] = release_counts.get(release, 0) + 1
        kind = str(row.get("type") or "movie").lower()
        kind_counts[kind if kind in kind_counts else "show"] += 1
        if row.get("delivery_status") == "not_delivered":
            undelivered += 1
    return {
        "genres": sorted(genres, key=str.casefold),
        "years": sorted(years, reverse=True),
        "type_counts": type_counts,
        "release_counts": release_counts,
        "kind_counts": kind_counts,
        "undelivered": undelivered,
    }


def page_rows(
    rows: List[Dict[str, Any]],
    params: Dict[str, Any],
    today: str,
    offset: int,
    limit: int,
) -> Tuple[List[Dict[str, Any]], int, List[str]]:
    """The requested slice, the filtered total, and every pending id in the filter.

    Without a known sort the rows keep the order they came in (the Approved
    tab's newest-first).
    """
    filtered = [row for row in rows if matches(row, params, today)]
    if params.get("sort") in SORTS:
        filtered = sort_rows(filtered, params["sort"])
    pending_ids = [row["id"] for row in filtered if row.get("status") in PENDING_STATUSES]
    return filtered[offset:offset + limit], len(filtered), pending_ids
