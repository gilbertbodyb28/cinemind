"""AniList OAuth and list parsing. Access tokens never belong on public models."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx

ANILIST_GRAPHQL = "https://graphql.anilist.co"
ANILIST_OAUTH = "https://anilist.co/api/v2/oauth"

VIEWER_QUERY = """
query {
  Viewer { id name }
}
"""


def client_id() -> str:
    return (os.environ.get("ANILIST_CLIENT_ID") or "").strip()


def client_secret() -> str:
    return (os.environ.get("ANILIST_CLIENT_SECRET") or "").strip()


def redirect_uri() -> str:
    return (
        os.environ.get("ANILIST_REDIRECT_URI")
        or "http://localhost:8001/api/anilist/auth/callback"
    ).strip()


def credentials_configured() -> bool:
    return bool(client_id() and client_secret())


def auth_url(anilist_client_id: str, callback: str, response_type: str = "code") -> str:
    return (
        f"{ANILIST_OAUTH}/authorize?client_id={quote(anilist_client_id, safe='')}"
        f"&redirect_uri={quote(callback, safe='')}"
        f"&response_type={quote(response_type, safe='')}"
    )


async def exchange_code(code: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not credentials_configured():
        return None, "AniList app credentials are not configured on the server"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{ANILIST_OAUTH}/token",
                json={
                    "grant_type": "authorization_code",
                    "client_id": client_id(),
                    "client_secret": client_secret(),
                    "redirect_uri": redirect_uri(),
                    "code": code,
                },
            )
        if response.status_code != 200:
            # AniList's own wording names the cause; the bare status code never did.
            try:
                payload = response.json() or {}
            except Exception:
                payload = {}
            hint = payload.get("message") or payload.get("hint") or payload.get("error")
            if payload.get("error") == "invalid_client":
                hint = (
                    "AniList rejected the app credentials. Check ANILIST_CLIENT_ID and "
                    "ANILIST_CLIENT_SECRET against your app at anilist.co/settings/developer."
                )
            logging.warning("AniList token exchange failed: %s %s", response.status_code, hint or "")
            return None, f"AniList token exchange failed ({response.status_code})" + (f": {hint}" if hint else "")
        payload = response.json()
        if not payload.get("access_token"):
            return None, "AniList did not return an access token"
        return payload, None
    except Exception as exc:
        logging.warning("AniList token exchange failed: %s", exc)
        return None, "Could not reach AniList to finish login"


async def fetch_viewer(access_token: str) -> Optional[Dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                ANILIST_GRAPHQL,
                json={"query": VIEWER_QUERY},
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if response.status_code != 200:
            return None
        return ((response.json().get("data") or {}).get("Viewer")) or None
    except Exception as exc:
        logging.warning("AniList viewer lookup failed: %s", exc)
        return None


def _entry_watched_at(entry: Dict[str, Any]) -> Optional[str]:
    """AniList dates arrive as parts or as a unix stamp; history sorts on ISO strings."""
    parts = entry.get("completedAt") or {}
    year, month, day = parts.get("year"), parts.get("month"), parts.get("day")
    if year:
        return datetime(int(year), int(month or 1), int(day or 1), tzinfo=timezone.utc).isoformat()
    updated = entry.get("updatedAt")
    if updated:
        try:
            return datetime.fromtimestamp(int(updated), tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return None


def parse_media_list_entry(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    media = entry.get("media") or {}
    status = (entry.get("status") or "").upper()
    title = (media.get("title") or {}).get("english") or (media.get("title") or {}).get("romaji") or "Unknown"
    studios = [node.get("name") for node in ((media.get("studios") or {}).get("nodes") or []) if node.get("name")]
    tags = [tag.get("name") for tag in (media.get("tags") or []) if tag.get("name")]
    score = entry.get("score")
    return {
        "watched_at": _entry_watched_at(entry),
        "title": title,
        "year": media.get("seasonYear") or ((media.get("startDate") or {}).get("year")),
        "type": "anime",
        "media_type": "anime",
        "genres": list(media.get("genres") or []),
        "tags": tags,
        "studios": studios,
        "anilist_id": media.get("id"),
        "status": status,
        "rating": score if score not in (None, 0) else None,
        "rating_scale": 10,
        "source": "anilist",
        "provider": "anilist",
    }


def parse_media_list_collection(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    lists = ((payload.get("data") or {}).get("MediaListCollection") or {}).get("lists") or []
    items: List[Dict[str, Any]] = []
    for bucket in lists:
        for entry in bucket.get("entries") or []:
            parsed = parse_media_list_entry(entry)
            if parsed:
                items.append(parsed)
    return items


def parse_recommendation_media(media: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not media:
        return None
    title = (media.get("title") or {}).get("english") or (media.get("title") or {}).get("romaji")
    if not title:
        return None
    return {
        "title": title,
        "year": media.get("seasonYear") or ((media.get("startDate") or {}).get("year")),
        "type": "anime",
        "media_type": "anime",
        "genres": list(media.get("genres") or []),
        "anilist_id": media.get("id"),
        "source": "anilist",
        "candidate_score": float(media.get("averageScore") or 0) / 10.0,
        "why": "Suggested from AniList recommendations.",
    }


RECOMMENDATIONS_QUERY = """
query ($page: Int) {
  Page(page: $page, perPage: 25) {
    pageInfo { hasNextPage }
    recommendations(sort: RATING_DESC, onList: true) {
      rating
      mediaRecommendation {
        id
        seasonYear
        genres
        averageScore
        title { english romaji }
        format
      }
    }
  }
}
"""

MEDIA_RELATED_QUERY = """
query ($ids: [Int]) {
  Page(page: 1, perPage: 10) {
    media(id_in: $ids, type: ANIME) {
      id
      recommendations(sort: RATING_DESC, page: 1, perPage: 8) {
        nodes {
          rating
          mediaRecommendation {
            id
            seasonYear
            genres
            averageScore
            title { english romaji }
            format
          }
        }
      }
    }
  }
}
"""


async def fetch_recommendations(access_token: str, history: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen: set[int] = set()

    def _add(parsed: Optional[Dict[str, Any]], why: str) -> None:
        if not parsed:
            return
        aid = parsed.get("anilist_id")
        if aid is not None and aid in seen:
            return
        if aid is not None:
            seen.add(int(aid))
        parsed = dict(parsed)
        parsed["why"] = why
        rows.append(parsed)

    seed_ids = []
    for item in history or []:
        if item.get("source") and item.get("source") != "anilist":
            continue
        aid = item.get("anilist_id")
        if aid is None or not str(aid).isdigit():
            continue
        seed_ids.append(int(aid))
        if len(seed_ids) >= 10:
            break

    try:
        async with httpx.AsyncClient(timeout=25) as client:
            if seed_ids:
                response = await client.post(
                    ANILIST_GRAPHQL,
                    json={"query": MEDIA_RELATED_QUERY, "variables": {"ids": seed_ids}},
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if response.status_code == 200:
                    media_rows = (((response.json().get("data") or {}).get("Page")) or {}).get("media") or []
                    for media in media_rows:
                        nodes = ((media.get("recommendations") or {}).get("nodes")) or []
                        for entry in nodes:
                            parsed = parse_recommendation_media(entry.get("mediaRecommendation"))
                            _add(parsed, "Suggested from AniList titles on your list.")

            page = 1
            while len(rows) < 40 and page <= 3:
                response = await client.post(
                    ANILIST_GRAPHQL,
                    json={"query": RECOMMENDATIONS_QUERY, "variables": {"page": page}},
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if response.status_code != 200:
                    break
                payload = ((response.json().get("data") or {}).get("Page")) or {}
                for entry in payload.get("recommendations") or []:
                    parsed = parse_recommendation_media(entry.get("mediaRecommendation"))
                    _add(parsed, "Suggested from AniList recommendations on your list.")
                if not ((payload.get("pageInfo") or {}).get("hasNextPage")):
                    break
                page += 1
    except Exception as exc:
        logging.warning("AniList recommendations failed: %s", exc)
        return rows
    return rows[:40]


MEDIA_LIST_QUERY = """
query ($userName: String, $type: MediaType) {
  MediaListCollection(userName: $userName, type: $type, status_in: [COMPLETED, CURRENT, REPEATING, PAUSED, DROPPED]) {
    lists {
      entries {
        status
        score(format: POINT_10)
        updatedAt
        completedAt { year month day }
        media {
          id
          seasonYear
          startDate { year }
          genres
          title { english romaji }
          studios { nodes { name } }
          tags { name }
        }
      }
    }
  }
}
"""


async def fetch_media_list(access_token: str, user_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read the viewer's AniList anime list as history rows.

    /anilist/import only ever landed history when the user pasted a payload by hand,
    so a connected AniList account looked permanently unsynced to the job engine.
    Raises on transport and GraphQL errors so the caller can record a real sync error
    instead of silently storing an empty list.
    """
    if not user_name:
        viewer = await fetch_viewer(access_token)
        user_name = (viewer or {}).get("name")
    if not user_name:
        raise ValueError("AniList viewer lookup failed; reconnect the account")

    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.post(
            ANILIST_GRAPHQL,
            json={"query": MEDIA_LIST_QUERY, "variables": {"userName": user_name, "type": "ANIME"}},
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if response.status_code != 200:
        raise RuntimeError(f"AniList list request failed ({response.status_code})")
    payload = response.json() or {}
    if payload.get("errors"):
        message = (payload["errors"][0] or {}).get("message") or "unknown error"
        raise RuntimeError(f"AniList list error: {message}")
    return parse_media_list_collection(payload)


UPCOMING_QUERY = """
query ($page: Int) {
  Page(page: $page, perPage: 50) {
    pageInfo { hasNextPage }
    media(type: ANIME, status: NOT_YET_RELEASED, sort: POPULARITY_DESC) {
      id
      seasonYear
      startDate { year month day }
      genres
      averageScore
      format
      countryOfOrigin
      title { english romaji }
    }
  }
}
"""


async def fetch_upcoming(
    access_token: Optional[str] = None,
    min_year: Optional[int] = None,
    max_year: Optional[int] = None,
    limit: int = 80,
    max_pages: int = 5,
) -> List[Dict[str, Any]]:
    """Announced anime that has not aired yet.

    TMDb carries about a hundred upcoming animation titles in total and almost
    none of them are anime, so a "coming anime" job ran dry the moment it had
    requested that handful. AniList tracks announced seasons years ahead, which
    is where titles like a third Frieren season actually live.
    """
    rows: List[Dict[str, Any]] = []
    headers = {"Authorization": f"Bearer {access_token}"} if access_token else {}
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            for page in range(1, max_pages + 1):
                response = await client.post(
                    ANILIST_GRAPHQL,
                    json={"query": UPCOMING_QUERY, "variables": {"page": page}},
                    headers=headers,
                )
                if response.status_code != 200:
                    break
                payload = ((response.json().get("data") or {}).get("Page")) or {}
                for media in payload.get("media") or []:
                    parsed = parse_recommendation_media(media)
                    if not parsed:
                        continue
                    year = parsed.get("year")
                    # A title with no announced year cannot be placed in a window.
                    if min_year is not None and (year is None or int(year) < int(min_year)):
                        continue
                    if max_year is not None and (year is None or int(year) > int(max_year)):
                        continue
                    origin = "Donghua" if media.get("countryOfOrigin") == "CN" else "Anime"
                    parsed["original_language"] = "zh" if media.get("countryOfOrigin") == "CN" else "ja"
                    parsed["why"] = f"{origin} announced for {year}, not aired yet."
                    rows.append(parsed)
                    if len(rows) >= limit:
                        return rows
                if not ((payload.get("pageInfo") or {}).get("hasNextPage")):
                    break
    except Exception as exc:
        logging.warning("AniList upcoming failed: %s", exc)
    return rows
