"""Verified premiere dates for titles that have not come out yet.

Gilbert's decision of 2026-09-25: "Up Coming" on Home shows premieres that are
actually coming, with verified dates, and "Upcoming Tv Shows" gives only series,
as its settings say - an older series counts when a coming season has a verified
premiere date. Until then the Home panel showed Content to Watch picks 2-5, and
the job's 2026-2029 window let through everything first released since January.

A date counts as verified only when the provider gives the full day, after today:

- film: TMDb `release_date`;
- series: TMDb `first_air_date` (series premiere), or the first episode of a
  coming season - `next_episode_to_air` with episode 1, or a season's `air_date`;
- anime without a TMDb id: AniList `nextAiringEpisode` for episode 1, or the
  full `startDate` of a title AniList lists as NOT_YET_RELEASED.

A year, a month, or the next weekly episode of a season already running is not
a premiere. Answers are cached for PREMIERE_CACHE_HOURS in provider_cache.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

import httpx

PREMIERE_FIELDS = ("premiere_date", "premiere_kind", "premiere_season", "premiere_source", "premiere_checked_at")
PREMIERE_CACHE_HOURS = 12
ANILIST_GRAPHQL = "https://graphql.anilist.co"
#: Parallel TMDb detail calls; TMDb allows ~50 per second.
CONCURRENCY = 8

ANILIST_PREMIERE_QUERY = """
query ($ids: [Int]) {
  Page(perPage: 50) {
    media(id_in: $ids, type: ANIME) {
      id
      status
      format
      startDate { year month day }
      nextAiringEpisode { airingAt episode }
    }
  }
}
"""


def today_iso(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).date().isoformat()


def _full_date(value: Any) -> Optional[str]:
    text = str(value or "")[:10]
    if len(text) == 10 and text[4] == "-" and text[7] == "-" and text[:4].isdigit():
        return text
    return None


def _is_series(row: Dict[str, Any]) -> bool:
    fmt = str(row.get("format") or row.get("anime_format") or "").upper()
    if fmt in {"MOVIE", "FILM"}:
        return False
    kind = str(row.get("media_type") or row.get("type") or "").casefold()
    return kind in {"tv", "show", "series", "anime"}


def premiere_from_tmdb(details: Dict[str, Any], series: bool, today: str) -> Optional[Dict[str, Any]]:
    """The next premiere in a TMDb details answer, or None."""
    if not series:
        release = _full_date(details.get("release_date"))
        if release and release > today:
            return {"premiere_date": release, "premiere_kind": "film_release", "premiere_season": None,
                    "premiere_source": "tmdb:release_date"}
        return None
    first = _full_date(details.get("first_air_date"))
    if first and first > today:
        return {"premiere_date": first, "premiere_kind": "series_premiere", "premiere_season": 1,
                "premiere_source": "tmdb:first_air_date"}
    options = []
    nxt = details.get("next_episode_to_air") or {}
    nxt_date = _full_date(nxt.get("air_date"))
    if nxt_date and nxt_date > today and int(nxt.get("episode_number") or 0) == 1 and int(nxt.get("season_number") or 0) >= 1:
        options.append((nxt_date, int(nxt["season_number"]), "tmdb:next_episode_to_air"))
    for season in details.get("seasons") or []:
        number = int(season.get("season_number") or 0)
        aired = _full_date(season.get("air_date"))
        if number >= 1 and aired and aired > today:
            options.append((aired, number, "tmdb:season_air_date"))
    if not options:
        return None
    date, number, source = min(options)
    return {"premiere_date": date, "premiere_kind": "season_premiere", "premiere_season": number,
            "premiere_source": source}


def premiere_from_anilist(media: Dict[str, Any], today: str) -> Optional[Dict[str, Any]]:
    """The next premiere in an AniList Media answer, or None."""
    film = str(media.get("format") or "").upper() == "MOVIE"
    airing = media.get("nextAiringEpisode") or {}
    if airing.get("airingAt") and int(airing.get("episode") or 0) == 1:
        date = datetime.fromtimestamp(int(airing["airingAt"]), tz=timezone.utc).date().isoformat()
        if date > today:
            return {"premiere_date": date, "premiere_kind": "film_release" if film else "series_premiere",
                    "premiere_season": None, "premiere_source": "anilist:nextAiringEpisode"}
    start = media.get("startDate") or {}
    if media.get("status") == "NOT_YET_RELEASED" and start.get("year") and start.get("month") and start.get("day"):
        date = "%04d-%02d-%02d" % (int(start["year"]), int(start["month"]), int(start["day"]))
        if date > today:
            return {"premiere_date": date, "premiere_kind": "film_release" if film else "series_premiere",
                    "premiere_season": None, "premiere_source": "anilist:startDate"}
    return None


async def _cached(key: str) -> Optional[Dict[str, Any]]:
    from database import db

    now = datetime.now(timezone.utc).isoformat()
    doc = await db.provider_cache.find_one({"key": key, "expires_at": {"$gt": now}})
    return doc.get("payload") if doc and isinstance(doc.get("payload"), dict) else None


async def _store(key: str, payload: Dict[str, Any]) -> None:
    from database import db

    now = datetime.now(timezone.utc)
    await db.provider_cache.update_one(
        {"key": key},
        {"$set": {"key": key, "payload": payload, "updated_at": now.isoformat(),
                  "expires_at": (now + timedelta(hours=PREMIERE_CACHE_HOURS)).isoformat()}},
        upsert=True,
    )


async def _tmdb_details(client: httpx.AsyncClient, tmdb_id: Any, series: bool, key: str,
                        store: bool = True) -> Optional[Dict[str, Any]]:
    cache_key = "premiere-details:%s:%s" % ("tv" if series else "movie", tmdb_id)
    cached = await _cached(cache_key)
    if cached is not None:
        return cached
    try:
        response = await client.get(
            "https://api.themoviedb.org/3/%s/%s" % ("tv" if series else "movie", tmdb_id),
            params={"api_key": key},
        )
    except httpx.HTTPError as exc:
        logging.warning("premiere check failed for tmdb %s: %s", tmdb_id, exc.__class__.__name__)
        return None
    if response.status_code == 404:
        payload: Dict[str, Any] = {"missing": True}
    elif response.status_code != 200:
        return None
    else:
        body = response.json() or {}
        # Only what the premiere needs; the whole answer is several kB per title.
        payload = {name: body.get(name) for name in ("release_date", "first_air_date", "status")}
        payload["next_episode_to_air"] = {name: (body.get("next_episode_to_air") or {}).get(name)
                                          for name in ("air_date", "episode_number", "season_number")}
        payload["seasons"] = [{"season_number": season.get("season_number"), "air_date": season.get("air_date")}
                              for season in body.get("seasons") or []]
    if store:
        await _store(cache_key, payload)
    return payload


async def _anilist_media(ids: List[int], store: bool = True) -> Dict[int, Dict[str, Any]]:
    found: Dict[int, Dict[str, Any]] = {}
    missing = []
    for media_id in ids:
        cached = await _cached("premiere-anilist:%s" % media_id)
        if cached is not None:
            found[media_id] = cached
        else:
            missing.append(media_id)
    async with httpx.AsyncClient(timeout=20) as client:
        for start in range(0, len(missing), 50):
            batch = missing[start:start + 50]
            try:
                response = await client.post(ANILIST_GRAPHQL, json={"query": ANILIST_PREMIERE_QUERY,
                                                                     "variables": {"ids": batch}})
            except httpx.HTTPError as exc:
                logging.warning("AniList premiere check failed: %s", exc.__class__.__name__)
                continue
            if response.status_code != 200:
                continue
            for media in (((response.json() or {}).get("data") or {}).get("Page") or {}).get("media") or []:
                found[int(media["id"])] = media
                if store:
                    await _store("premiere-anilist:%s" % media["id"], media)
    return found


async def verify_premieres(rows: Iterable[Dict[str, Any]], api_key: Optional[str] = None,
                           now: Optional[datetime] = None, *, store: bool = True,
                           unanswered: Optional[List[Dict[str, Any]]] = None) -> int:
    """Set the premiere fields on each row in place; returns how many have one.

    A row without a verified premiere gets premiere_date None, so a later
    reader knows it was checked (premiere_checked_at) rather than never asked.
    `store=False` leaves provider_cache alone (a read-only caller such as
    evaluation.queue_cleanup plan). Rows the provider did not answer for (an
    outage, a timeout) are appended to `unanswered`: "no premiere" is then not
    known, only not found.
    """
    from config import TMDB_KEY

    key = api_key or TMDB_KEY
    today = today_iso(now)
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    rows = list(rows)
    for row in rows:
        row.update({"premiere_date": None, "premiere_kind": None, "premiere_season": None,
                    "premiere_source": None, "premiere_checked_at": stamp})
    tmdb_rows = [row for row in rows if row.get("tmdb_id") not in (None, "") and key]
    on_tmdb = {id(row) for row in tmdb_rows}
    anilist_rows = [row for row in rows if id(row) not in on_tmdb and row.get("anilist_id") not in (None, "")]
    if unanswered is not None:
        # No id to ask with (or no TMDb key): nobody was asked, so nobody answered.
        on_anilist = {id(row) for row in anilist_rows}
        unanswered.extend(row for row in rows if id(row) not in on_tmdb and id(row) not in on_anilist)
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient(timeout=10) as client:
        async def one(row: Dict[str, Any]) -> None:
            async with semaphore:
                series = _is_series(row)
                details = await _tmdb_details(client, row["tmdb_id"], series, key, store=store)
            if details is None and unanswered is not None:
                unanswered.append(row)
            found = premiere_from_tmdb(details, series, today) if details and not details.get("missing") else None
            if found:
                row.update(found)

        await asyncio.gather(*(one(row) for row in tmdb_rows))
    if anilist_rows:
        media = await _anilist_media(sorted({int(row["anilist_id"]) for row in anilist_rows}), store=store)
        for row in anilist_rows:
            if int(row["anilist_id"]) not in media and unanswered is not None:
                unanswered.append(row)
            found = premiere_from_anilist(media.get(int(row["anilist_id"])) or {}, today)
            if found:
                row.update(found)
    return sum(1 for row in rows if row.get("premiere_date"))


def is_upcoming_job(job: Optional[Dict[str, Any]]) -> bool:
    """A job that asks only for titles still to premiere (Jobs: "Only upcoming premieres")."""
    return bool(((job or {}).get("filters") or {}).get("upcoming_only"))


def premiere_window(filters: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, str]:
    """The air-date span an upcoming job's discover lanes ask for: tomorrow to the window's end."""
    tomorrow = ((now or datetime.now(timezone.utc)) + timedelta(days=1)).date().isoformat()
    start = str(filters.get("min_release_date") or "")[:10] or (
        "%04d-01-01" % int(filters["min_year"]) if filters.get("min_year") else "")
    end = str(filters.get("max_release_date") or "")[:10] or (
        "%04d-12-31" % int(filters["max_year"]) if filters.get("max_year") else "")
    window = {"from": max(start, tomorrow) if start else tomorrow}
    if end:
        window["to"] = end
    return window
