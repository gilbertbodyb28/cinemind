"""TMDb detail enrichment for history titles and candidates.

The profile used to be built from genre names alone. Measured on real data:
0 of 633 history titles carried a synopsis and only 44 carried a language, so
`keyword_affinity` was structurally always 0.0 and the language profile held a
single entry. Two unrelated shows that shared "Action, Drama" scored
bit-identically, which is why a Tagalog soap was explained as "plays like
Daredevil: Born Again".

Keywords, cast, creators, companies and collections are what actually separate
two titles inside one genre, so they are fetched once per title and cached
permanently - they do not change.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx

from config import TMDB_KEY

CAST_DEPTH = 8
KEYWORD_DEPTH = 24
COMPANY_DEPTH = 4
CONCURRENCY = 8
# Fields written onto a row. A row that already carries a truthful value keeps
# it; enrichment only fills gaps.
ENRICHED_FIELDS = (
    "overview", "original_language", "tmdb_keywords", "cast", "creators",
    "companies", "collection", "origin_countries", "runtime",
)


def enrichment_kind(row: Dict[str, Any]) -> str:
    """Which TMDb endpoint holds this row - anime films live under /movie."""
    from recommendation.taste_engine import media_bucket

    return "movie" if media_bucket(row) in {"movie", "anime_movie"} else "tv"


def enrichment_key(row: Dict[str, Any]) -> Optional[str]:
    tmdb_id = row.get("tmdb_id")
    if tmdb_id in (None, "", 0):
        return None
    try:
        return "%s:%d" % (enrichment_kind(row), int(tmdb_id))
    except (TypeError, ValueError):
        return None


def _names(rows: Optional[Iterable[Dict[str, Any]]], limit: int) -> List[str]:
    out: List[str] = []
    for row in rows or []:
        name = (row or {}).get("name")
        if name and name not in out:
            out.append(str(name))
        if len(out) >= limit:
            break
    return out


def _parse_detail(body: Dict[str, Any], kind: str) -> Dict[str, Any]:
    credits = body.get("credits") or body.get("aggregate_credits") or {}
    creators = _names(body.get("created_by"), CAST_DEPTH)
    for member in credits.get("crew") or []:
        job = str(member.get("job") or "")
        if job in {"Director", "Series Director"} and member.get("name") not in creators:
            creators.append(str(member["name"]))
        if len(creators) >= CAST_DEPTH:
            break
    companies = _names(body.get("production_companies"), COMPANY_DEPTH)
    companies += [name for name in _names(body.get("networks"), COMPANY_DEPTH) if name not in companies]
    collection = (body.get("belongs_to_collection") or {}).get("name")
    date = body.get("release_date") or body.get("first_air_date") or ""
    runtime = body.get("runtime")
    if runtime is None:
        episode_runtimes = body.get("episode_run_time") or []
        runtime = episode_runtimes[0] if episode_runtimes else None
    return {
        "overview": body.get("overview") or "",
        "original_language": body.get("original_language"),
        "tmdb_keywords": [
            str(row.get("name"))
            for row in ((body.get("keywords") or {}).get("keywords")
                        or (body.get("keywords") or {}).get("results") or [])[:KEYWORD_DEPTH]
            if (row or {}).get("name")
        ],
        "cast": _names((credits.get("cast") or []), CAST_DEPTH),
        "creators": creators[:CAST_DEPTH],
        "companies": companies[:COMPANY_DEPTH * 2],
        "collection": collection,
        "origin_countries": [str(item) for item in (body.get("origin_country") or []) if item],
        "runtime": runtime,
        "genres": [str(row.get("name")) for row in (body.get("genres") or []) if (row or {}).get("name")],
        "vote_count": body.get("vote_count"),
        "tmdb_rating": body.get("vote_average"),
        "year": int(date[:4]) if len(str(date)) >= 4 and str(date)[:4].isdigit() else None,
        "kind": kind,
    }


async def _fetch_one(client: httpx.AsyncClient, kind: str, tmdb_id: int, api_key: str) -> Optional[Dict[str, Any]]:
    append = "keywords,credits" if kind == "movie" else "keywords,aggregate_credits"
    try:
        response = await client.get(
            f"https://api.themoviedb.org/3/{kind}/{tmdb_id}",
            params={"api_key": api_key, "append_to_response": append},
        )
    except Exception as exc:
        logging.warning("TMDb enrich %s/%s failed: %s", kind, tmdb_id, exc.__class__.__name__)
        return None
    if response.status_code != 200:
        return None
    try:
        return _parse_detail(response.json() or {}, kind)
    except Exception:
        return None


async def load_enrichment(
    rows: Iterable[Dict[str, Any]],
    api_key: Optional[str] = None,
    *,
    limit: Optional[int] = None,
) -> Dict[str, Dict[str, Any]]:
    """Cached TMDb details for every row that carries a TMDb id.

    The cache never expires: a film's cast and keywords are settled facts, and
    re-fetching 600 titles on every scheduled run would be the slowest part of
    the pipeline for no gain.
    """
    from database import db

    wanted: Dict[str, Tuple[str, int]] = {}
    for row in rows:
        key = enrichment_key(row)
        if key and key not in wanted:
            kind, raw = key.split(":", 1)
            wanted[key] = (kind, int(raw))
    if not wanted:
        return {}

    found: Dict[str, Dict[str, Any]] = {}
    cursor = db.media_enrichment.find({"key": {"$in": sorted(wanted)}}, {"_id": 0})
    for doc in await cursor.to_list(len(wanted) + 10):
        payload = doc.get("payload")
        if isinstance(payload, dict):
            found[doc["key"]] = payload

    missing = [key for key in wanted if key not in found]
    key = api_key or TMDB_KEY
    if not missing or not key:
        return found
    if limit is not None:
        missing = missing[:limit]

    gate = asyncio.Semaphore(CONCURRENCY)
    now = datetime.now(timezone.utc).isoformat()

    async def one(client: httpx.AsyncClient, cache_key: str) -> None:
        kind, tmdb_id = wanted[cache_key]
        async with gate:
            payload = await _fetch_one(client, kind, tmdb_id, key)
        if payload is None:
            return
        found[cache_key] = payload
        await db.media_enrichment.update_one(
            {"key": cache_key},
            {"$set": {"key": cache_key, "payload": payload, "fetched_at": now}},
            upsert=True,
        )

    async with httpx.AsyncClient(timeout=12) as client:
        await asyncio.gather(*(one(client, cache_key) for cache_key in missing))
    return found


def apply_enrichment(row: Dict[str, Any], payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Fill the row's gaps in place. A value the provider already gave wins."""
    if not payload:
        return row
    for field in ENRICHED_FIELDS:
        value = payload.get(field)
        if value in (None, "", [], {}):
            continue
        if row.get(field) in (None, "", [], {}):
            row[field] = value
    # Synopsis is the field the rest of the code reads for text overlap.
    if payload.get("overview") and not row.get("synopsis"):
        row["synopsis"] = payload["overview"]
    if payload.get("genres") and not row.get("genres"):
        row["genres"] = payload["genres"]
    if row.get("year") in (None, "") and payload.get("year"):
        row["year"] = payload["year"]
    return row


async def enrich_rows(rows: List[Dict[str, Any]], api_key: Optional[str] = None) -> int:
    """Enrich a list of history or candidate rows in place. Returns rows touched."""
    if not rows:
        return 0
    try:
        cache = await load_enrichment(rows, api_key)
    except Exception as exc:
        logging.warning("TMDb enrichment unavailable: %s", exc.__class__.__name__)
        return 0
    touched = 0
    for row in rows:
        payload = cache.get(enrichment_key(row) or "")
        if payload:
            apply_enrichment(row, payload)
            touched += 1
    return touched
