"""Titles by the people behind what the viewer liked: a lane for large jobs.

The taste floor wants a concrete link to a liked title (similarity.specific_link,
ranking_engine.specific_evidence): shared distinctive themes, a creator, cast, a
franchise, a studio. A title that is not out yet rarely has keywords - 344 of the
494 coming titles that met every setting of "Upcoming US + Anime/Donghua" on
2026-09-27 had none, and not one of them cleared the floor - but its director,
writer and cast are usually announced long before. So a large job (from
providers.tmdb.LARGE_BUDGET candidates) also asks TMDb what the viewer's own
people - the creators and cast the profile holds from rated titles
(taste_engine "people") - have made or have coming, inside the job's window.

Nothing here decides a pick: every row goes through the same enrichment,
filters, exclusions, taste floor and queue rules as any other candidate.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx

#: People asked about per run at most, strongest first (and their titles per person).
MAX_PEOPLE = 150
WORKS_PER_PERSON = 25
#: Crew jobs that make a person a title's creator (taste_engine keys them "creator:").
CREATOR_JOBS = {"Director", "Series Director", "Creator", "Screenplay", "Writer", "Story", "Novel", "Original Story"}
#: A cast credit counts from this billing on films (CAST_DEPTH in tmdb_enrich is 8) ...
CAST_ORDER_LIMIT = 7
#: ... and from this many episodes on series; a guest spot is not the actor's show.
CAST_EPISODES_MIN = 3
CREDITS_CACHE = timedelta(days=30)
WORKS_CACHE = timedelta(days=3)
CONCURRENCY = 6


def people_limit(budget: int) -> int:
    from providers.tmdb import LARGE_BUDGET

    if budget < LARGE_BUDGET:
        return 0
    return max(40, min(MAX_PEOPLE, budget // 200))


def ranked_people(taste: Dict[str, Any], limit: int) -> List[Tuple[str, str, float]]:
    """(role, name, strength) of the profile's people, strongest first.

    Strength is what similarity.people_affinity gives a title with that person:
    affinity scaled by how many rated titles back it.
    """
    rows: List[Tuple[str, str, float]] = []
    for key, row in (taste.get("people") or {}).items():
        role, _, name = str(key).partition(":")
        if role not in {"creator", "cast"} or not name:
            continue
        strength = float(row.get("affinity") or 0.0) * (0.4 + 0.6 * float(row.get("confidence") or 0.0))
        if strength > 0:
            rows.append((role, name, strength))
    rows.sort(key=lambda item: (-item[2], item[1]))
    return rows[:limit]


async def _cached(key: str) -> Optional[Any]:
    from database import db

    now = datetime.now(timezone.utc).isoformat()
    doc = await db.provider_cache.find_one({"key": key, "expires_at": {"$gt": now}})
    return doc.get("payload") if doc and "payload" in doc else None


async def _store(key: str, payload: Any, ttl: timedelta) -> None:
    from database import db

    now = datetime.now(timezone.utc)
    await db.provider_cache.update_one(
        {"key": key},
        {"$set": {"key": key, "payload": payload, "updated_at": now.isoformat(),
                  "expires_at": (now + ttl).isoformat()}},
        upsert=True,
    )


async def _get(client: httpx.AsyncClient, path: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    from providers.tmdb import TMDB_RATE_LIMIT_RETRIES, TMDB_RATE_LIMIT_WAIT

    for attempt in range(TMDB_RATE_LIMIT_RETRIES + 1):
        try:
            response = await client.get(f"https://api.themoviedb.org/3/{path}", params=params)
        except httpx.HTTPError as exc:
            logging.warning("TMDb %s failed: %s", path, exc.__class__.__name__)
            return None
        if response.status_code == 429 and attempt < TMDB_RATE_LIMIT_RETRIES:
            try:
                wait = float(response.headers.get("Retry-After") or 1.0)
            except ValueError:
                wait = 1.0
            await asyncio.sleep(min(TMDB_RATE_LIMIT_WAIT, max(0.5, wait)))
            continue
        if response.status_code != 200:
            return None
        return response.json() or {}
    return None


async def _title_people(client: httpx.AsyncClient, kind: str, tmdb_id: Any, api_key: str) -> Dict[str, int]:
    """name (casefolded, with role) -> TMDb person id, for one liked title's credits."""
    key = "people-credits:%s:%s" % (kind, tmdb_id)
    cached = await _cached(key)
    if isinstance(cached, dict):
        return cached
    append = "credits" if kind == "movie" else "aggregate_credits"
    body = await _get(client, f"{kind}/{tmdb_id}", {"api_key": api_key, "append_to_response": append})
    if body is None:
        return {}
    credits = body.get("credits") or body.get("aggregate_credits") or {}
    found: Dict[str, int] = {}
    for member in body.get("created_by") or []:
        if member.get("name") and member.get("id"):
            found["creator:" + str(member["name"]).casefold()] = int(member["id"])
    for member in credits.get("crew") or []:
        jobs = {str(member.get("job") or "")} | {str(job.get("job") or "") for job in member.get("jobs") or []}
        if member.get("name") and member.get("id") and jobs & CREATOR_JOBS:
            found.setdefault("creator:" + str(member["name"]).casefold(), int(member["id"]))
    for member in credits.get("cast") or []:
        if member.get("name") and member.get("id"):
            found.setdefault("cast:" + str(member["name"]).casefold(), int(member["id"]))
    await _store(key, found, CREDITS_CACHE)
    return found


async def _person_works(client: httpx.AsyncClient, person_id: int, api_key: str) -> Dict[str, Any]:
    key = "people-works:%d" % person_id
    cached = await _cached(key)
    if isinstance(cached, dict):
        return cached
    body = await _get(client, f"person/{person_id}/combined_credits", {"api_key": api_key})
    if body is None:
        return {}
    keep = ("id", "media_type", "title", "name", "original_title", "original_name", "release_date",
            "first_air_date", "genre_ids", "original_language", "origin_country", "overview", "poster_path",
            "popularity", "vote_average", "vote_count", "order", "episode_count", "job", "adult")
    payload = {
        part: [{field: row.get(field) for field in keep if field in row} for row in body.get(part) or []]
        for part in ("cast", "crew")
    }
    await _store(key, payload, WORKS_CACHE)
    return payload


def _in_time(row: Dict[str, Any], window: Optional[Dict[str, str]], filters: Dict[str, Any]) -> bool:
    date = str(row.get("release_date") or row.get("first_air_date") or "")[:10]
    if window:
        # Coming titles: a date inside the window, or none announced yet (the
        # premiere check decides those, providers.premieres).
        if not date:
            return True
        return date >= window["from"] and (not window.get("to") or date <= window["to"])
    if not date:
        return False
    year = int(date[:4]) if date[:4].isdigit() else None
    if year is None:
        return False
    if filters.get("min_year") not in (None, "") and year < int(filters["min_year"]):
        return False
    if filters.get("max_year") not in (None, "") and year > int(filters["max_year"]):
        return False
    return True


def _credit_counts(role: str, row: Dict[str, Any]) -> bool:
    if role == "creator":
        return str(row.get("job") or "") in CREATOR_JOBS
    if row.get("media_type") == "movie":
        order = row.get("order")
        return order is None or int(order) <= CAST_ORDER_LIMIT
    episodes = row.get("episode_count")
    return episodes is None or int(episodes) >= CAST_EPISODES_MIN


async def people_candidates(
    job: Dict[str, Any],
    taste: Dict[str, Any],
    api_key: Optional[str],
    window: Optional[Dict[str, str]] = None,
    report: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Titles by the viewer's own creators and cast, inside the job's window."""
    from providers.tmdb import _normalize_tmdb_result, candidate_budget

    limit = people_limit(candidate_budget(job))
    if not (api_key and limit):
        return []
    people = ranked_people(taste, limit)
    if not people:
        return []
    media = {str(item or "").casefold() for item in job.get("media_types") or ["movie", "tv"]}
    want_movies = bool(media & {"movie", "movies", "film", "anime"})
    want_series = bool(media & {"tv", "show", "series", "anime"})
    wanted = {"%s:%s" % (role, name) for role, name, _ in people}
    # Which liked title credits each person: its credits give the person's TMDb id.
    liked = [row for row in taste.get("liked_titles") or taste.get("high_confidence_positive_titles") or []
             if row.get("tmdb_id")]
    sources: Dict[str, Tuple[str, Any]] = {}
    shown: Dict[str, str] = {}
    for row in liked:
        kind = "movie" if str(row.get("media_type") or "") in {"movie", "anime_movie"} else "tv"
        for field, role in (("creators", "creator"), ("cast", "cast")):
            for name in row.get(field) or []:
                person = "%s:%s" % (role, str(name).casefold())
                if person in wanted and person not in sources:
                    sources[person] = (kind, row["tmdb_id"])
                    shown[person] = str(name)
    titles = sorted(set(sources.values()), key=lambda item: (item[0], str(item[1])))
    ids: Dict[str, int] = {}
    async with httpx.AsyncClient(timeout=10) as client:
        for start in range(0, len(titles), CONCURRENCY):
            batch = titles[start:start + CONCURRENCY]
            for found in await asyncio.gather(*(_title_people(client, kind, tid, api_key) for kind, tid in batch)):
                for person, pid in found.items():
                    if person in wanted:
                        ids.setdefault(person, pid)
        order = [(role, name, strength, ids["%s:%s" % (role, name)]) for role, name, strength in people
                 if "%s:%s" % (role, name) in ids]
        works: List[Dict[str, Any]] = []
        for start in range(0, len(order), CONCURRENCY):
            batch = order[start:start + CONCURRENCY]
            answers = await asyncio.gather(*(_person_works(client, pid, api_key) for *_, pid in batch))
            works.extend({"person": item, "credits": answer} for item, answer in zip(batch, answers))
    filters = job.get("filters") or {}
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for entry in works:
        role, name, _strength, _pid = entry["person"]
        part = "crew" if role == "creator" else "cast"
        credits = [row for row in (entry["credits"] or {}).get(part) or []
                   if not row.get("adult") and _credit_counts(role, row) and _in_time(row, window, filters)
                   and ((row.get("media_type") == "movie" and want_movies)
                        or (row.get("media_type") == "tv" and want_series))]
        credits.sort(key=lambda row: -float(row.get("popularity") or 0.0))
        for row in credits[:WORKS_PER_PERSON]:
            key = (row.get("media_type"), row.get("id"))
            if key in seen:
                continue
            seen.add(key)
            normalized = _normalize_tmdb_result(row, "movie" if row.get("media_type") == "movie" else "tv",
                                                "tmdb_people")
            normalized["source_seed"] = "%s %s" % ("made by" if role == "creator" else "with",
                                                   shown.get("%s:%s" % (role, name), name))
            normalized["why"] = ""
            out.append(normalized)
    if report is not None:
        report["people"] = {"asked": len(people), "found": len(order), "titles": len(out)}
    logging.info("job %s: %s titles from %s of the viewer's people", job.get("id"), len(out), len(order))
    return out
