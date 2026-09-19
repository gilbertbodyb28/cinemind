"""Simkl PIN OAuth helpers, history mapping, and recommendation feeds."""

from typing import Any, Dict, List, Optional, Sequence
import logging

import httpx

from config import SIMKL_API


def simkl_params(client_id: str) -> Dict[str, str]:
    return {"client_id": client_id, "app-name": "CineMindAI", "app-version": "1.0"}


def simkl_headers(client_id: str, token: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "User-Agent": "CineMindAI/1.0",
        "simkl-api-key": client_id,
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _poster_url(poster: Optional[str]) -> Optional[str]:
    if not poster:
        return None
    if str(poster).startswith("http"):
        return str(poster)
    return f"https://simkl.in/posters/{poster}_m.jpg"


def _bucket_for_type(media_type: Optional[str]) -> str:
    kind = (media_type or "movie").lower()
    if kind in {"anime"}:
        return "anime"
    if kind in {"show", "tv", "series"}:
        return "tv"
    return "movies"


def _normalize_simkl_id(ids: Optional[Dict[str, Any]], fallback: Any = None) -> Optional[int]:
    if fallback is not None and str(fallback).isdigit():
        return int(fallback)
    if not isinstance(ids, dict):
        return None
    for key in ("simkl", "simkl_id"):
        raw = ids.get(key)
        if raw is not None and str(raw).isdigit():
            return int(raw)
    return None


def parse_recommendation_entry(kind: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    """Map Simkl recommend / trending / users_recommendations rows into candidates."""
    media = entry.get(kind) or entry.get("show") or entry.get("movie") or entry.get("anime") or entry
    if not isinstance(media, dict):
        media = entry if isinstance(entry, dict) else {}
    ids = media.get("ids") or entry.get("ids") or {}
    tmdb_raw = ids.get("tmdb")
    entry_type = (media.get("type") or entry.get("type") or kind or "movie").lower()
    if entry_type in {"show", "tv", "series"}:
        out_kind = "show"
        media_type = "tv"
    elif entry_type == "anime":
        out_kind = "show"
        media_type = "anime"
    else:
        out_kind = "movie"
        media_type = "movie"
    rating = None
    ratings = media.get("ratings") or entry.get("ratings") or {}
    if isinstance(ratings, dict):
        simkl_rating = ratings.get("simkl") or {}
        if isinstance(simkl_rating, dict) and simkl_rating.get("rating") is not None:
            rating = float(simkl_rating["rating"])
    if rating is None and media.get("rating") is not None:
        try:
            rating = float(media.get("rating"))
        except (TypeError, ValueError):
            rating = None
    simkl_id = _normalize_simkl_id(ids, media.get("simkl_id") or entry.get("simkl_id"))
    return {
        "title": media.get("title") or entry.get("title") or "Unknown",
        "year": media.get("year") or entry.get("year"),
        "type": out_kind,
        "media_type": media_type,
        "genres": [genre.title() for genre in (media.get("genres") or entry.get("genres") or [])],
        "tmdb_id": int(tmdb_raw) if str(tmdb_raw or "").isdigit() else None,
        "simkl_id": simkl_id,
        "poster": _poster_url(media.get("poster") or entry.get("poster")),
        "source": "simkl",
        "candidate_score": float(rating or entry.get("score") or media.get("rank") or 0),
        "why": "Suggested from your Simkl recommendation feed.",
    }


def parse_history_entry(kind: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    media = entry.get(kind) or entry.get("show") or {}
    ids = media.get("ids") or {}
    tmdb_raw = ids.get("tmdb")
    poster = media.get("poster")
    return {
        "title": media.get("title", "Unknown"),
        "year": media.get("year"),
        "type": kind,
        "genres": [genre.title() for genre in (media.get("genres") or [])],
        "rating": None,
        "poster": _poster_url(poster),
        "tmdb_id": int(tmdb_raw) if str(tmdb_raw or "").isdigit() else None,
        "imdb_id": ids.get("imdb"),
        "simkl_id": ids.get("simkl"),
        "watched_at": entry.get("last_watched_at"),
        "last_watched_at": entry.get("last_watched_at"),
        "status": entry.get("status"),
        "source": "simkl",
        "provider": "simkl",
        "provider_specific_metadata": {"status": entry.get("status")},
    }


async def fetch_recommendations(
    client_id: str,
    access_token: str,
    history: Optional[Sequence[Dict[str, Any]]] = None,
    *,
    limit: int = 40,
    seed_limit: int = 6,
) -> List[Dict[str, Any]]:
    """Build Simkl candidates from related titles + trending feeds.

    Simkl has no `/recommend/movies` feed (that path 404s). Personalized
    suggestions live under each title's `users_recommendations`, and public
    discovery uses `/movies|tv|anime/trending`.
    """
    if not client_id or not access_token:
        return []
    collected: List[Dict[str, Any]] = []
    seen: set[str] = set()
    failures = 0
    attempts = 0

    def _add(row: Optional[Dict[str, Any]]) -> None:
        if not row or not row.get("title") or row["title"] == "Unknown":
            return
        key = str(row.get("simkl_id") or f"{row.get('title')}|{row.get('year')}|{row.get('type')}")
        if key in seen:
            return
        seen.add(key)
        collected.append(row)

    seeds: List[Dict[str, Any]] = []
    for item in history or []:
        if item.get("source") and item.get("source") != "simkl":
            continue
        sid = item.get("simkl_id")
        if sid is None or not str(sid).isdigit():
            continue
        seeds.append(item)
        if len(seeds) >= seed_limit:
            break

    params = simkl_params(client_id)
    headers = simkl_headers(client_id, access_token)
    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
        for item in seeds:
            bucket = _bucket_for_type(item.get("type") or item.get("media_type"))
            attempts += 1
            try:
                response = await client.get(
                    f"{SIMKL_API}/{bucket}/{int(item['simkl_id'])}",
                    params=params,
                    headers=headers,
                )
            except Exception as exc:
                failures += 1
                logging.warning("Simkl detail failed: %s", exc.__class__.__name__)
                continue
            if response.status_code != 200:
                failures += 1
                logging.warning("Simkl detail %s/%s -> %s", bucket, item.get("simkl_id"), response.status_code)
                continue
            payload = response.json() or {}
            related = payload.get("users_recommendations") or []
            if not isinstance(related, list):
                continue
            for entry in related:
                if not isinstance(entry, dict):
                    continue
                _add(parse_recommendation_entry(entry.get("type") or bucket.rstrip("s"), entry))
                if len(collected) >= limit:
                    return collected

        # Public discovery fallback / supplement — always available with a client id.
        for bucket, kind in (("movies", "movie"), ("tv", "show"), ("anime", "anime")):
            if len(collected) >= limit:
                break
            attempts += 1
            try:
                response = await client.get(
                    f"{SIMKL_API}/{bucket}/trending",
                    params=params,
                    headers=headers,
                )
            except Exception as exc:
                failures += 1
                logging.warning("Simkl trending failed: %s", exc.__class__.__name__)
                continue
            if response.status_code != 200:
                failures += 1
                logging.warning("Simkl trending %s -> %s", bucket, response.status_code)
                continue
            for entry in (response.json() or [])[:20]:
                if not isinstance(entry, dict):
                    continue
                row = parse_recommendation_entry(kind, entry)
                row["why"] = "Suggested from Simkl trending."
                _add(row)
                if len(collected) >= limit:
                    break

    if not collected and attempts and failures >= attempts:
        raise RuntimeError(f"Simkl recommendation feeds failed ({failures}/{attempts})")
    return collected[:limit]
