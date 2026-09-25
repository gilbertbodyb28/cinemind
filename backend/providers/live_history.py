"""What Trakt and AniList hold right now, read for one job run and never written back.

The synced copy in `history` / `media_history` is only as good as the last sync,
and the last one on Gilbert's account (2026-09-08) was badly short. Measured
against his live Trakt account on 2026-09-24:

- 13,490 plays on Trakt, 10,000 in CineMind - the importer stopped at 100
  pages of 100, so everything watched before February 2022 was lost;
- 176 personal ratings on Trakt, 97 in CineMind - ratings were only attached to
  titles inside that window, so The Witcher, Titans, Legion, Merlin, the Lord of
  the Rings films, Harry Potter and most of the MCU (all 10/10) never reached
  the taste profile;
- 117 watched films and 57 watched series missing, so the watched filter let
  Charmed (1998), the Harry Potter films and Star Wars back in as "new".

AniList is read for a different reason: Gilbert scores on POINT_3 (smileys), and
the stored rows took a 3 (the best smiley, 8.5/10 on AniList's own scale) for 3/10.
Those two anime were the only low-rated titles in the whole profile.

This overlay reads the provider's own ratings, watched sets and list, cached in
`provider_cache` for LIVE_TTL, and hands them to the pipeline in memory. It
never deletes or edits a history row: a proper re-sync is Gilbert's call (see
HANDOFF.md). When a provider is unreachable the job simply runs on the synced copy.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

LIVE_TTL = timedelta(hours=6)
TRAKT_PAGE = 100
TRAKT_MAX_PAGES = 40


async def _cached(key: str, loader) -> Optional[Any]:
    from database import db

    now = datetime.now(timezone.utc)
    cached = await db.provider_cache.find_one({"key": key, "expires_at": {"$gt": now.isoformat()}})
    if cached and "payload" in cached:
        return cached["payload"]
    payload = await loader()
    if payload is None:
        return None
    await db.provider_cache.update_one(
        {"key": key},
        {"$set": {"key": key, "payload": payload, "expires_at": (now + LIVE_TTL).isoformat(),
                  "updated_at": now.isoformat()}},
        upsert=True,
    )
    return payload


async def _trakt_pages(client: httpx.AsyncClient, path: str, headers: Dict[str, str]) -> Optional[List[Dict[str, Any]]]:
    from config import TRAKT_API

    rows: List[Dict[str, Any]] = []
    page = 1
    while page <= TRAKT_MAX_PAGES:
        response = await client.get(
            f"{TRAKT_API}{path}", params={"page": page, "limit": TRAKT_PAGE, "extended": "full"}, headers=headers,
        )
        if response.status_code != 200:
            logging.warning("live Trakt %s -> %s", path, response.status_code)
            return None if not rows else rows
        batch = response.json() or []
        rows.extend(batch)
        pages = int(response.headers.get("X-Pagination-Page-Count") or 1)
        if page >= pages or not batch:
            break
        page += 1
    return rows


def _trakt_media(entry: Dict[str, Any], kind: str) -> Dict[str, Any]:
    media = entry.get(kind) or {}
    ids = media.get("ids") or {}
    row = {
        "title": media.get("title") or "Unknown",
        "year": media.get("year"),
        "type": "movie" if kind == "movie" else "show",
        "genres": [str(genre).title() for genre in (media.get("genres") or [])],
        "tmdb_id": ids.get("tmdb"),
        "imdb_id": ids.get("imdb"),
        "tvdb_id": ids.get("tvdb"),
        "trakt_id": ids.get("trakt"),
        "source": "trakt",
        "provider": "trakt",
        "live": True,
    }
    if media.get("language"):
        row["original_language"] = str(media["language"]).casefold()
    if media.get("country"):
        row["country"] = str(media["country"]).upper()
    if media.get("overview"):
        row["overview"] = media["overview"]
    return row


async def trakt_live(user_id: str, conn: Dict[str, Any]) -> Optional[Dict[str, List[Dict[str, Any]]]]:
    """Trakt's own ratings and watched sets, as history-shaped rows."""
    from providers.trakt import resolve_trakt_client_id, trakt_headers, trakt_token

    if not conn.get("trakt_access_token"):
        return None
    client_id = resolve_trakt_client_id(conn)
    token = await trakt_token(user_id, conn)
    if not (client_id and token):
        return None
    headers = trakt_headers(client_id, token)

    async def load() -> Optional[Dict[str, Any]]:
        out: Dict[str, Any] = {}
        async with httpx.AsyncClient(timeout=20) as client:
            for name, path in (
                ("ratings_movies", "/sync/ratings/movies"), ("ratings_shows", "/sync/ratings/shows"),
                ("watched_movies", "/sync/watched/movies"), ("watched_shows", "/sync/watched/shows"),
            ):
                rows = await _trakt_pages(client, path, headers)
                if rows is None:
                    return None
                out[name] = rows
        return out

    try:
        payload = await _cached("live:trakt:%s" % (conn.get("trakt_username") or user_id), load)
    except Exception as exc:
        from jobs.engine import safe_provider_error

        logging.warning("live Trakt overlay failed: %s", safe_provider_error(exc))
        return None
    if not payload:
        return None
    ratings: List[Dict[str, Any]] = []
    for kind, key in (("movie", "ratings_movies"), ("show", "ratings_shows")):
        for entry in payload.get(key) or []:
            if entry.get("rating") is None:
                continue
            ratings.append({**_trakt_media(entry, kind), "rating": float(entry["rating"]), "rating_scale": 10,
                            "rated_at": entry.get("rated_at")})
    watched: List[Dict[str, Any]] = []
    for kind, key in (("movie", "watched_movies"), ("show", "watched_shows")):
        for entry in payload.get(key) or []:
            row = _trakt_media(entry, kind)
            stamp = entry.get("last_watched_at")
            row.update({"watched_at": stamp, "last_watched_at": stamp,
                        # Plays are the depth signal Trakt's per-episode history carries.
                        "progress": int(entry.get("plays") or 1)})
            watched.append(row)
    return {"ratings": ratings, "watched": watched}


async def anilist_live(conn: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """The AniList list with scores on POINT_10, whatever format the user rates in."""
    from providers.anilist import fetch_media_list

    token = conn.get("anilist_access_token")
    if not token:
        return None

    async def load() -> Optional[List[Dict[str, Any]]]:
        return await fetch_media_list(token, conn.get("anilist_username"))

    try:
        rows = await _cached("live:anilist:%s" % (conn.get("anilist_username") or "viewer"), load)
    except Exception as exc:
        logging.warning("live AniList overlay failed: %s", exc.__class__.__name__)
        return None
    return [{**row, "live": True} for row in rows or []]


def _keys(row: Dict[str, Any]) -> set:
    from recommendation.exclusion_engine import identity_keys

    return identity_keys(row)


def overlay_rows(
    history: List[Dict[str, Any]],
    personal: List[Dict[str, Any]],
    trakt: Optional[Dict[str, List[Dict[str, Any]]]],
    anilist: Optional[List[Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """The live rows the synced copy is missing, keyed to its canonical ids.

    Returns (extra history rows, extra personal rows, report). A live row joins
    a title the database already has - by TMDb, IMDb, Trakt or AniList id - and
    borrows its canonical id, so the taste profile merges the two instead of
    counting one title twice.
    """
    canonical_by_key: Dict[tuple, str] = {}
    rated_keys: set = set()
    history_keys: set = set()
    for is_personal, rows in ((False, history), (True, personal)):
        for row in rows:
            keys = _keys(row)
            if row.get("canonical_media_id"):
                for key in keys:
                    if key[0] != "canonical":
                        canonical_by_key.setdefault(key, row["canonical_media_id"])
            if is_personal and row.get("rating") is not None and row.get("provider") == "trakt":
                rated_keys |= keys
            if str(row.get("status") or "").upper() not in {"PLANNING", "PLANTOWATCH"}:
                history_keys |= keys

    def _join(row: Dict[str, Any]) -> Dict[str, Any]:
        for key in _keys(row):
            canonical = canonical_by_key.get(key)
            if canonical:
                return {**row, "canonical_media_id": canonical}
        return row

    extra_history: List[Dict[str, Any]] = []
    extra_personal: List[Dict[str, Any]] = []
    report: Dict[str, Any] = {}
    if trakt:
        added_ratings = [_join(row) for row in trakt["ratings"] if not (_keys(row) & rated_keys)]
        added_watched = [_join(row) for row in trakt["watched"] if not (_keys(row) & history_keys)]
        extra_personal.extend(added_ratings)
        extra_history.extend(added_watched)
        report["trakt"] = {"live_ratings": len(trakt["ratings"]), "ratings_added": len(added_ratings),
                           "live_watched": len(trakt["watched"]), "watched_added": len(added_watched)}
    if anilist is not None:
        stored = {}
        for row in personal:
            if row.get("provider") == "anilist" and row.get("anilist_id") is not None:
                stored[int(row["anilist_id"])] = row
        corrected = []
        for row in anilist:
            if row.get("rating") is None or row.get("anilist_id") is None:
                continue
            old = stored.get(int(row["anilist_id"]))
            if old and old.get("rating") is not None and abs(float(old["rating"]) - float(row["rating"])) < 0.01:
                continue
            # A personal row outranks the stored one in merge_taste_docs, and
            # this one carries the score on the scale it claims.
            corrected.append(_join({**row, "rating_scale": 10}))
        extra_personal.extend(corrected)
        report["anilist"] = {"live_entries": len(anilist), "ratings_corrected_or_added": len(corrected)}
    return extra_history, extra_personal, report
