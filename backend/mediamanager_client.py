"""Outbound MediaManager admin API helpers used by CineMind approve flow."""
from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException


MM_TIMEOUT = httpx.Timeout(12.0, connect=2.0)

# Mirrors MediaManager's own add dialog; values are its API enums.
SERIES_TYPES = {"standard", "anime"}
METADATA_PROVIDERS = {"tmdb", "tvdb"}
MONITORING_MODES = {"monitored", "unmonitored"}
MONITOR_SCOPES = {"entire", "specific", "future", "missing"}
RESOLUTIONS = {"any", "sd", "360p", "480p", "576p", "720p", "1080p", "2160p", "4320p", "unknown"}
VIDEO_CODECS = {"h264", "h265", "av1", "unknown"}
DYNAMIC_RANGES = {"any", "sdr", "hdr10", "hdr10_plus", "dolby_vision", "hlg", "unknown"}
AUDIO_FORMATS = {
    "dolby_truehd", "dolby_truehd_atmos", "ac3", "eac3", "eac3_atmos",
    "dts", "dts_hd", "dts_hd_ma", "dts_x", "aac", "flac",
}
TOKEN_TTL_SECONDS = 20 * 60
_token_cache: Dict[str, Tuple[str, float]] = {}


def clear_mediamanager_token_cache() -> None:
    _token_cache.clear()


def mediamanager_is_configured(doc: Dict[str, Any]) -> bool:
    return bool(
        doc.get("mediamanager_url")
        and doc.get("mediamanager_email")
        and doc.get("mediamanager_password")
    )


def normalize_mediamanager_url(url: str) -> str:
    """API origin only. The Vite UI on :5173 is never the MediaManager API."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port
    scheme = parsed.scheme or "http"
    if port == 5173:
        port = 8000
    netloc = f"{host}:{port}" if port else host
    return f"{scheme}://{netloc}".rstrip("/")


def media_type_for_mediamanager(item: Dict[str, Any]) -> str:
    """Map CineMind types onto MediaManager movie vs TV libraries."""
    kind = str(item.get("type") or item.get("media_type") or "movie").casefold()
    if kind in {"show", "tv", "series", "anime"}:
        return "show"
    return "movie"


def coerce_tmdb_id(value: Any) -> Optional[int]:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _token_key(base_url: str, email: str) -> str:
    return f"{normalize_mediamanager_url(base_url)}|{email}"


def _cached_token(base_url: str, email: str) -> Optional[str]:
    row = _token_cache.get(_token_key(base_url, email))
    if not row:
        return None
    token, expires = row
    if expires <= time.monotonic():
        _token_cache.pop(_token_key(base_url, email), None)
        return None
    return token


def _store_token(base_url: str, email: str, token: str) -> None:
    _token_cache[_token_key(base_url, email)] = (token, time.monotonic() + TOKEN_TTL_SECONDS)


def _drop_token(base_url: str, email: str) -> None:
    _token_cache.pop(_token_key(base_url, email), None)


def _mm_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
        detail = body.get("detail", body) if isinstance(body, dict) else body
        text = detail if isinstance(detail, str) else str(detail)
    except Exception:
        text = response.text or f"status {response.status_code}"
    return text.strip()[:300]


async def mediamanager_login(
    hc: httpx.AsyncClient,
    base_url: str,
    email: str,
    password: str,
) -> str:
    """Obtain a MediaManager admin JWT via form login."""
    r = await hc.post(
        f"{normalize_mediamanager_url(base_url)}/api/v1/auth/jwt/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if r.status_code == 401:
        raise HTTPException(status_code=409, detail="MediaManager rejected the admin credentials")
    if r.status_code != 200:
        extra = _mm_detail(r)
        raise HTTPException(
            status_code=502,
            detail=f"MediaManager login failed ({r.status_code}): {extra}",
        )
    token = (r.json() or {}).get("access_token")
    if not token:
        raise HTTPException(status_code=502, detail="MediaManager login returned no access token")
    _store_token(base_url, email, token)
    return token


async def mediamanager_token(
    hc: httpx.AsyncClient,
    base_url: str,
    email: str,
    password: str,
    *,
    force: bool = False,
) -> str:
    if not force:
        cached = _cached_token(base_url, email)
        if cached:
            return cached
    return await mediamanager_login(hc, base_url, email, password)


async def mediamanager_search_external_id(
    hc: httpx.AsyncClient,
    base_url: str,
    token: str,
    media_type: str,
    title: str,
    year: Optional[int] = None,
) -> Optional[int]:
    """Resolve a TMDb id via MediaManager search — CineMind does not call TMDb here."""
    headers = {"Authorization": f"Bearer {token}"}
    root = normalize_mediamanager_url(base_url)
    path = f"{root}/api/v1/tv/search" if media_type == "show" else f"{root}/api/v1/movies/search"
    query = str(title or "").strip()
    if year:
        query = f"{query} {year}".strip()
    r = await hc.get(path, params={"query": query, "metadata_provider": "tmdb"}, headers=headers)
    if r.status_code == 401:
        raise HTTPException(status_code=409, detail="MediaManager admin session was rejected")
    if r.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"MediaManager search failed: {_mm_detail(r)}",
        )
    rows = r.json() if isinstance(r.json(), list) else []
    if not rows:
        return None
    want_year = int(year) if year not in (None, "") else None
    ranked = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ext = coerce_tmdb_id(row.get("external_id"))
        if not ext:
            continue
        row_year = coerce_tmdb_id(row.get("year"))
        score = 2 if want_year and row_year == want_year else 1
        ranked.append((score, ext))
    ranked.sort(reverse=True)
    return ranked[0][1] if ranked else None


def _pick(value: Any, allowed: set, fallback: str) -> str:
    text = str(value or "").strip().casefold()
    return text if text in allowed else fallback


def _allow_list(values: Any, allowed: set) -> list:
    """Empty list means Any, exactly like MediaManager's own dialog."""
    if not isinstance(values, (list, tuple, set)):
        return []
    out = []
    for item in values:
        text = str(item or "").strip().casefold()
        if text in allowed and text not in out and text not in {"any"}:
            out.append(text)
    return out


def _size_bytes(value: Any) -> Optional[int]:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def build_release_rules(options: Optional[Dict[str, Any]], media_type: str) -> Dict[str, Any]:
    """Translate the add dialog's quality picks into MediaManager release rules."""
    raw = dict((options or {}).get("release_rules") or {})
    rules: Dict[str, Any] = {
        "minimum_resolution": _pick(raw.get("minimum_resolution"), RESOLUTIONS, "any"),
        "maximum_resolution": _pick(raw.get("maximum_resolution"), RESOLUTIONS, "any"),
        "allowed_video_codecs": _allow_list(raw.get("allowed_video_codecs"), VIDEO_CODECS),
        "allowed_dynamic_ranges": _allow_list(raw.get("allowed_dynamic_ranges"), DYNAMIC_RANGES),
        "allowed_audio_formats": _allow_list(raw.get("allowed_audio_formats"), AUDIO_FORMATS),
    }
    if media_type == "show":
        rules["minimum_episode_size_bytes"] = _size_bytes(raw.get("minimum_episode_size_bytes"))
        rules["maximum_episode_size_bytes"] = _size_bytes(raw.get("maximum_episode_size_bytes"))
        rules["minimum_season_pack_size_bytes"] = _size_bytes(raw.get("minimum_season_pack_size_bytes"))
        rules["maximum_season_pack_size_bytes"] = _size_bytes(raw.get("maximum_season_pack_size_bytes"))
    else:
        rules["minimum_movie_size_bytes"] = _size_bytes(raw.get("minimum_movie_size_bytes"))
        rules["maximum_movie_size_bytes"] = _size_bytes(raw.get("maximum_movie_size_bytes"))
    return rules


def _int_list(values: Any) -> Optional[list]:
    if not isinstance(values, (list, tuple, set)):
        return None
    out = []
    for item in values:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out or None


def _str_list(values: Any) -> Optional[list]:
    if not isinstance(values, (list, tuple, set)):
        return None
    out = [str(item) for item in values if str(item).strip()]
    return out or None


async def mediamanager_add_title(
    hc: httpx.AsyncClient,
    base_url: str,
    token: str,
    media_type: str,
    tmdb_id: int,
    options: Optional[Dict[str, Any]] = None,
) -> tuple[bool, bool]:
    """Add a movie or show to the MediaManager library only. No CineMind TMDb fetch."""
    headers = {"Authorization": f"Bearer {token}"}
    root = normalize_mediamanager_url(base_url)
    opts = options or {}
    if not opts:
        # No add dialog involved: keep the original minimal add contract.
        if media_type == "show":
            r = await hc.post(
                f"{root}/api/v1/tv/shows",
                params={"show_id": tmdb_id, "metadata_provider": "tmdb", "monitoring": "unmonitored"},
                headers=headers,
            )
        else:
            r = await hc.post(
                f"{root}/api/v1/movies",
                params={"movie_id": tmdb_id, "metadata_provider": "tmdb"},
                headers=headers,
            )
        return _add_outcome(r)
    provider = _pick(opts.get("metadata_provider"), METADATA_PROVIDERS, "tmdb")
    search_now = bool(opts.get("search_now"))
    body = {"release_rules": build_release_rules(opts, media_type)}
    if media_type == "show":
        params: Dict[str, Any] = {
            "show_id": tmdb_id,
            "metadata_provider": provider,
            "series_type": _pick(opts.get("series_type"), SERIES_TYPES, "standard"),
            "monitoring": _pick(opts.get("monitoring"), MONITORING_MODES, "unmonitored"),
            "monitor_scope": _pick(opts.get("monitor_scope"), MONITOR_SCOPES, "entire"),
            "search_now": str(search_now).lower(),
        }
        seasons = _int_list(opts.get("monitor_season"))
        episodes = _str_list(opts.get("monitor_episode"))
        if params["monitor_scope"] == "specific":
            if seasons:
                params["monitor_season"] = seasons
            if episodes:
                params["monitor_episode"] = episodes
        r = await hc.post(f"{root}/api/v1/tv/shows", params=params, json=body, headers=headers)
    else:
        r = await hc.post(
            f"{root}/api/v1/movies",
            params={
                "movie_id": tmdb_id,
                "metadata_provider": provider,
                "monitored": str(_pick(opts.get("monitoring"), MONITORING_MODES, "monitored") == "monitored").lower(),
                "search_now": str(search_now).lower(),
            },
            json=body,
            headers=headers,
        )
    return _add_outcome(r)


def _add_outcome(r: httpx.Response) -> tuple[bool, bool]:
    if r.status_code == 401:
        raise HTTPException(status_code=409, detail="MediaManager admin session was rejected")
    if r.status_code not in (200, 201):
        raise HTTPException(status_code=502, detail=f"MediaManager add failed: {_mm_detail(r)}")
    return r.status_code == 201, r.status_code == 200


async def send_item_to_library(
    item: Dict[str, Any],
    conn: Dict[str, Any],
    *,
    tmdb_api_key: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Add the title to MediaManager Movies or TV. Does not call TMDb from CineMind."""
    del tmdb_api_key
    if not mediamanager_is_configured(conn):
        raise HTTPException(status_code=409, detail="Connect MediaManager first")
    media_type = media_type_for_mediamanager(item)
    tmdb_id = coerce_tmdb_id(item.get("tmdb_id"))
    url = normalize_mediamanager_url(conn["mediamanager_url"])
    email = conn["mediamanager_email"]
    password = conn["mediamanager_password"]
    try:
        async with httpx.AsyncClient(timeout=MM_TIMEOUT) as hc:
            token = await mediamanager_token(hc, url, email, password)
            created, already_existed = await _add_with_optional_search(
                hc, url, token, media_type, tmdb_id, item, options
            )
    except HTTPException as exc:
        if exc.status_code != 409 or "session was rejected" not in str(exc.detail):
            raise
        _drop_token(url, email)
        try:
            async with httpx.AsyncClient(timeout=MM_TIMEOUT) as hc:
                token = await mediamanager_token(hc, url, email, password, force=True)
                created, already_existed = await _add_with_optional_search(
                    hc, url, token, media_type, tmdb_id, item, options
                )
        except HTTPException:
            raise
        except httpx.TimeoutException as timeout_exc:
            raise HTTPException(
                status_code=502,
                detail="MediaManager did not respond. Use API port 8000, not the Vite UI on 5173.",
            ) from timeout_exc
        except httpx.HTTPError as http_exc:
            raise HTTPException(status_code=502, detail=f"Could not reach MediaManager: {http_exc}") from http_exc
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=502,
            detail="MediaManager did not respond. Use API port 8000, not the Vite UI on 5173.",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach MediaManager: {exc}") from exc
    resolved = coerce_tmdb_id(item.get("tmdb_id"))
    if not resolved:
        raise HTTPException(status_code=422, detail="MediaManager could not match this title")
    return {
        "ok": True,
        "created": created,
        "already_existed": already_existed,
        "tmdb_id": int(resolved),
        "media_type": media_type,
    }


async def _add_with_optional_search(
    hc: httpx.AsyncClient,
    url: str,
    token: str,
    media_type: str,
    tmdb_id: Optional[int],
    item: Dict[str, Any],
    options: Optional[Dict[str, Any]] = None,
) -> tuple[bool, bool]:
    resolved = tmdb_id
    if not resolved:
        resolved = await mediamanager_search_external_id(
            hc,
            url,
            token,
            media_type,
            item.get("title") or "",
            item.get("year"),
        )
        if resolved:
            item["tmdb_id"] = resolved
    if not resolved:
        raise HTTPException(
            status_code=422,
            detail="MediaManager could not match this title",
        )
    item["tmdb_id"] = resolved
    if options:
        return await mediamanager_add_title(hc, url, token, media_type, int(resolved), options)
    return await mediamanager_add_title(hc, url, token, media_type, int(resolved))
