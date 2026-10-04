"""Trakt headers, token refresh, and history mapping."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import logging

import httpx

from config import TRAKT_API, TRAKT_CLIENT_ID, TRAKT_CLIENT_SECRET
from database import db


def trakt_headers(client_id: str, token: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "trakt-api-key": client_id,
        "trakt-api-version": "2",
        "User-Agent": "CineMindAI/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def resolve_trakt_client_id(conn: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Prefer the server OAuth app id so manual Settings overrides cannot desync tokens."""
    return TRAKT_CLIENT_ID or ((conn or {}).get("trakt_client_id") or None)


async def trakt_token(user_id: str, conn: Dict[str, Any]) -> Optional[str]:
    """Return a valid Trakt access token, refreshing when near expiry."""
    token = conn.get("trakt_access_token")
    refresh = conn.get("trakt_refresh_token")
    expires_at = conn.get("trakt_expires_at")
    client_id = resolve_trakt_client_id(conn)
    if not refresh or not expires_at or not TRAKT_CLIENT_SECRET or not client_id:
        return token
    if expires_at > int(datetime.now(timezone.utc).timestamp()) + 60:
        return token
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{TRAKT_API}/oauth/token",
                json={
                    "refresh_token": refresh,
                    "client_id": client_id,
                    "client_secret": TRAKT_CLIENT_SECRET,
                    "grant_type": "refresh_token",
                },
                headers=trakt_headers(client_id),
            )
    except httpx.HTTPError as exc:
        # Trakt could not be reached: that says nothing about the sign-in.
        logging.warning("Trakt refresh could not reach Trakt (%s); keeping the saved sign-in", exc.__class__.__name__)
        return token
    if response.status_code in (400, 401):
        # Trakt refused the refresh token itself (invalid_grant): the session is
        # gone. It is recorded, not wiped - wiping on *any* non-200 meant one
        # Trakt outage during a renewal disconnected a working account.
        from providers.auth_state import note_auth_failure

        logging.warning("Trakt refused the refresh (%s); user must reconnect", response.status_code)
        await note_auth_failure(
            user_id, "trakt", f"Trakt refused to renew the saved sign-in ({response.status_code}): connect Trakt again",
        )
        return None
    if response.status_code != 200:
        logging.warning("Trakt refresh failed %s; keeping the saved sign-in", response.status_code)
        return token
    payload = response.json()
    await db.connections.update_one(
        {"user_id": user_id},
        {"$set": {
            "trakt_access_token": payload["access_token"],
            "trakt_refresh_token": payload["refresh_token"],
            "trakt_expires_at": int(datetime.now(timezone.utc).timestamp()) + int(payload.get("expires_in", 7 * 86400)),
        }},
    )
    return payload["access_token"]


def parse_history_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    media = entry.get("movie") or entry.get("show") or {}
    ids = media.get("ids") or {}
    episode = entry.get("episode") or {}
    metadata = {"action": entry.get("action")}
    if entry.get("id") is not None:
        metadata["history_id"] = entry["id"]
    if episode:
        # Which episode a play was: without it a re-sync cannot tell a rewatch
        # from the next episode, and every show looks "rewatched".
        metadata.update({"season": episode.get("season"), "episode": episode.get("number"),
                         "episode_trakt_id": (episode.get("ids") or {}).get("trakt")})
    row = {
        "title": media.get("title", "Unknown"),
        "year": media.get("year"),
        "type": "movie" if entry.get("movie") else "show",
        "genres": [genre.title() for genre in (media.get("genres") or [])],
        # Trakt history items carry catalogue ratings, not the user's rating.
        "rating": None,
        "poster": None,
        "tmdb_id": ids.get("tmdb"),
        "imdb_id": ids.get("imdb"),
        "tvdb_id": ids.get("tvdb"),
        "trakt_id": ids.get("trakt"),
        "watched_at": entry.get("watched_at"),
        "last_watched_at": entry.get("watched_at"),
        "source": "trakt",
        "provider": "trakt",
        "provider_specific_metadata": metadata,
    }
    if entry.get("id") is not None:
        # Trakt's own id for this one play: how a re-sync recognises the row.
        row["provider_play_id"] = "trakt:%s" % entry["id"]
    return row


def parse_rating_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """User ratings from /sync/ratings — never catalogue metadata scores."""
    media = entry.get("movie") or entry.get("show") or {}
    ids = media.get("ids") or {}
    return {
        "title": media.get("title", "Unknown"),
        "year": media.get("year"),
        "type": "movie" if entry.get("movie") else "show",
        "rating": float(entry["rating"]) if entry.get("rating") is not None else None,
        "rating_scale": 10,
        "rated_at": entry.get("rated_at"),
        "tmdb_id": ids.get("tmdb"),
        "imdb_id": ids.get("imdb"),
        "trakt_id": ids.get("trakt"),
        "source": "trakt",
        "provider": "trakt",
    }


def parse_recommendation_entry(entry: Dict[str, Any], kind: Optional[str] = None) -> Dict[str, Any]:
    """One Trakt recommendation as a candidate.

    `/recommendations/{movies,shows}` answers with the bare movie or show
    object; only the wrapped {"movie": ...} / {"show": ...} shape used
    elsewhere on Trakt was read. Every recommendation therefore parsed as
    "Unknown" and was dropped, and Trakt contributed nothing to any job.
    `kind` ("movies" / "shows") says which one a bare object is.
    """
    wrapped = entry.get("movie") or entry.get("show")
    media = wrapped or entry or {}
    is_movie = bool(entry.get("movie")) if wrapped else str(kind or "").casefold() in {"movies", "movie"}
    ids = media.get("ids") or {}
    row = {
        "title": media.get("title", "Unknown"),
        "year": media.get("year"),
        "type": "movie" if is_movie else "show",
        "genres": [genre.title() for genre in (media.get("genres") or [])],
        "synopsis": media.get("overview") or "",
        "tmdb_rating": media.get("rating"),
        "tmdb_id": ids.get("tmdb"),
        "imdb_id": ids.get("imdb"),
        "trakt_id": ids.get("trakt"),
        "source": "trakt",
        "candidate_score": float(entry.get("score") or media.get("rating") or 0),
        "why": "Suggested from your Trakt recommendation feed.",
    }
    # Language and origin decide a title's lane (anime / donghua / live action)
    # and a job's language tier; without them every Trakt row was "unknown".
    if media.get("language"):
        row["original_language"] = str(media["language"]).casefold()
    if media.get("country"):
        country = str(media["country"]).upper()
        row["country"] = country
        row["origin_countries"] = [country]
    if media.get("votes") is not None:
        row["vote_count"] = media.get("votes")
    return row


def trakt_history_params(page: int = 1, limit: int = 50) -> Dict[str, Any]:
    return {"page": page, "limit": limit, "extended": "full"}


def apply_user_ratings(items: List[Dict[str, Any]], ratings: List[Dict[str, Any]]) -> None:
    # Keyed by type as well: TMDb and Trakt number films and series separately,
    # so a rated film used to hand its score to the series with the same id.
    by_trakt = {(row.get("type"), row["trakt_id"]): row for row in ratings if row.get("trakt_id") is not None}
    by_tmdb = {(row.get("type"), row["tmdb_id"]): row for row in ratings if row.get("tmdb_id") is not None}
    by_title = {(row.get("type"), row.get("title"), row.get("year")): row for row in ratings}
    for item in items:
        kind = item.get("type")
        match = (by_trakt.get((kind, item.get("trakt_id"))) or by_tmdb.get((kind, item.get("tmdb_id")))
                 or by_title.get((kind, item.get("title"), item.get("year"))))
        if match and match.get("rating") is not None:
            item["rating"] = match["rating"]
            item["rating_scale"] = 10


#: Trakt's genre slugs for the canonical names jobs use (recommendation.filter_engine).
TRAKT_GENRE_SLUGS = {
    "sci-fi": "science-fiction", "science fiction": "science-fiction", "science-fiction": "science-fiction",
    "action": "action", "adventure": "adventure", "animation": "animation", "anime": "anime",
    "comedy": "comedy", "crime": "crime", "documentary": "documentary", "drama": "drama",
    "family": "family", "fantasy": "fantasy", "history": "history", "horror": "horror",
    "music": "music", "musical": "musical", "mystery": "mystery", "romance": "romance",
    "superhero": "superhero", "thriller": "thriller", "war": "war", "western": "western",
}
#: Trakt answers a calendar for at most 33 days at a time.
TRAKT_CALENDAR_DAYS = 33
TRAKT_UPCOMING_CACHE_HOURS = 6


#: Kids is two slugs on Trakt: "family" for films and series, "children" for series only.
TRAKT_KIDS_SLUGS = {"movies": ("family",), "shows": ("children", "family")}


def trakt_genre_filter(names: Optional[List[str]], kind: Optional[str] = None) -> Optional[str]:
    """The job's genres as Trakt's `genres` filter (comma = any of them), or None.

    `kind` is "movies" or "shows"; without it Kids asks only for what both have.
    """
    from recommendation.filter_engine import canonical_genres

    slugs = []
    for name in sorted(canonical_genres(names or [])):
        if name == "kids":
            found = TRAKT_KIDS_SLUGS.get(kind or "movies", TRAKT_KIDS_SLUGS["movies"])
        else:
            found = (TRAKT_GENRE_SLUGS.get(str(name).casefold()),)
        for slug in found:
            if slug and slug not in slugs:
                slugs.append(slug)
    return ",".join(slugs) or None


async def _cached_list(client: httpx.AsyncClient, path: str, params: Dict[str, Any], client_id: str) -> List[Dict[str, Any]]:
    from datetime import timedelta

    key = "trakt-upcoming:" + path + ":" + "&".join(f"{k}={params[k]}" for k in sorted(params))
    now = datetime.now(timezone.utc)
    cached = await db.provider_cache.find_one({"key": key, "expires_at": {"$gt": now.isoformat()}})
    if cached and isinstance(cached.get("payload"), list):
        return cached["payload"]
    try:
        response = await client.get(f"{TRAKT_API}{path}", params=params, headers=trakt_headers(client_id))
    except httpx.HTTPError as exc:
        logging.warning("Trakt %s failed: %s", path, exc.__class__.__name__)
        return []
    if response.status_code != 200:
        logging.warning("Trakt %s answered %s", path, response.status_code)
        return []
    payload = response.json() or []
    await db.provider_cache.update_one(
        {"key": key},
        {"$set": {"key": key, "payload": payload, "updated_at": now.isoformat(),
                  "expires_at": (now + timedelta(hours=TRAKT_UPCOMING_CACHE_HOURS)).isoformat()}},
        upsert=True,
    )
    return payload


#: Trakt's public lists a saved job widens into when it comes up short
#: (jobs.upcoming "other_sources"): what people watch now, over time, and wait for.
TRAKT_LIST_PATHS = ("trending", "popular", "anticipated")


async def fetch_list_titles(
    client_id: Optional[str],
    media_types: Optional[List[str]] = None,
    genres: Optional[List[str]] = None,
    min_year: Optional[int] = None,
    max_year: Optional[int] = None,
    pages: int = 3,
) -> List[Dict[str, Any]]:
    """Trakt's trending, popular and anticipated films and series inside a job's genres and years.

    Public endpoints (the app's client id, no sign-in), cached like the upcoming
    lists. Every row still goes through the job's filters, exclusions and taste
    floor; this only widens where a job that has too few results looks.
    """
    if not client_id:
        return []
    wanted = {str(item or "").casefold() for item in (media_types or ["movie", "tv"])}
    kinds = [kind for kind, ok in (("movies", bool(wanted & {"movie", "movies", "film", "anime"})),
                                   ("shows", bool(wanted & {"tv", "show", "series", "anime"}))) if ok]
    base: Dict[str, Any] = {"extended": "full"}
    if min_year or max_year:
        low = int(min_year or max_year)
        high = int(max_year or datetime.now(timezone.utc).year + 5)
        base["years"] = str(low) if low == high else "%d-%d" % (min(low, high), max(low, high))
    rows: List[Dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=15) as client:
        for kind in kinds:
            genre_filter = trakt_genre_filter(genres, kind)
            params = {**base, "genres": genre_filter} if genre_filter else dict(base)
            for name in TRAKT_LIST_PATHS:
                for page in range(1, max(1, pages) + 1):
                    entries = await _cached_list(client, f"/{kind}/{name}", {**params, "page": page, "limit": 100},
                                                 client_id)
                    for entry in entries:
                        row = parse_recommendation_entry(entry, kind)
                        if row.get("title") and row["title"] != "Unknown":
                            row["source"] = "trakt_%s" % name
                            row["why"] = ""
                            rows.append(row)
                    if len(entries) < 100:
                        break
    return rows


async def fetch_upcoming_titles(
    client_id: Optional[str],
    media_types: Optional[List[str]] = None,
    start: Optional[str] = None,
    days: int = 99,
    genres: Optional[List[str]] = None,
    pages: int = 2,
) -> List[Dict[str, Any]]:
    """What Trakt sees coming: the most anticipated films and series, and the next
    weeks' season and series premieres, however old the series is.

    Public endpoints (the app's client id, no sign-in). Every row is still checked
    against TMDb for a verified premiere (providers.premieres) before a job can
    take it; Trakt only widens where an upcoming job looks.
    """
    if not client_id:
        return []
    from datetime import date, timedelta

    wanted = {str(item or "").casefold() for item in (media_types or ["movie", "tv", "anime"])}
    movies = bool(wanted & {"movie", "movies", "film", "anime"})
    shows = bool(wanted & {"tv", "show", "series", "anime"})
    bases: Dict[str, Dict[str, Any]] = {}
    for kind in ("movies", "shows"):
        genre_filter = trakt_genre_filter(genres, kind)
        bases[kind] = {"genres": genre_filter} if genre_filter else {}
    first = date.fromisoformat((start or (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat())[:10])
    rows: List[Dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=15) as client:
        for kind, path, wanted_kind in (("movies", "/movies/anticipated", movies), ("shows", "/shows/anticipated", shows)):
            if not wanted_kind:
                continue
            for page in range(1, max(1, pages) + 1):
                for entry in await _cached_list(client, path, {**bases[kind], "page": page, "limit": 100}, client_id):
                    row = parse_recommendation_entry(entry, kind)
                    if row.get("title") and row["title"] != "Unknown":
                        row["source"] = "trakt_upcoming"
                        row["why"] = "On %s Trakt lists of titles people are waiting for." % (entry.get("list_count") or "many")
                        rows.append(row)
        if shows:
            offset = 0
            while offset < days:
                span = min(TRAKT_CALENDAR_DAYS, days - offset)
                day = (first + timedelta(days=offset)).isoformat()
                for entry in await _cached_list(client, f"/calendars/all/shows/premieres/{day}/{span}", dict(bases["shows"]),
                                                client_id):
                    row = parse_recommendation_entry(entry)
                    episode = entry.get("episode") or {}
                    if not row.get("title") or row["title"] == "Unknown":
                        continue
                    row["source"] = "trakt_upcoming"
                    row["why"] = "Season %s premieres %s (Trakt calendar)." % (
                        episode.get("season") or "?", str(entry.get("first_aired") or "")[:10])
                    rows.append(row)
                offset += span
    return rows
