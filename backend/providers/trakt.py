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
    if response.status_code != 200:
        logging.warning("Trakt refresh failed %s; user must reconnect", response.status_code)
        await db.connections.update_one(
            {"user_id": user_id},
            {"$set": {"trakt_access_token": None, "trakt_refresh_token": None, "trakt_expires_at": None}},
        )
        return None
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
    return {
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
        "provider_specific_metadata": {"action": entry.get("action")},
    }


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


def parse_recommendation_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    media = entry.get("movie") or entry.get("show") or {}
    ids = media.get("ids") or {}
    return {
        "title": media.get("title", "Unknown"),
        "year": media.get("year"),
        "type": "movie" if entry.get("movie") else "show",
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


def trakt_history_params(page: int = 1, limit: int = 50) -> Dict[str, Any]:
    return {"page": page, "limit": limit, "extended": "full"}


def apply_user_ratings(items: List[Dict[str, Any]], ratings: List[Dict[str, Any]]) -> None:
    by_tmdb = {row["tmdb_id"]: row for row in ratings if row.get("tmdb_id") is not None}
    by_title = {(row.get("title"), row.get("year")): row for row in ratings}
    for item in items:
        match = by_tmdb.get(item.get("tmdb_id")) or by_title.get((item.get("title"), item.get("year")))
        if match and match.get("rating") is not None:
            item["rating"] = match["rating"]
            item["rating_scale"] = 10
