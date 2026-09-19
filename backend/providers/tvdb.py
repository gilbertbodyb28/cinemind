"""TheTVDB v4 — optional poster fallback when TMDb has no artwork."""

from typing import Any, Dict, Optional
import logging

import httpx

_TVDB_TOKEN: Optional[str] = None
_TVDB_TOKEN_KEY: Optional[str] = None


async def _tvdb_token(api_key: str) -> Optional[str]:
    global _TVDB_TOKEN, _TVDB_TOKEN_KEY
    if _TVDB_TOKEN and _TVDB_TOKEN_KEY == api_key:
        return _TVDB_TOKEN
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                "https://api4.thetvdb.com/v4/login",
                json={"apikey": api_key},
            )
        if response.status_code != 200:
            return None
        token = (response.json().get("data") or {}).get("token")
        if token:
            _TVDB_TOKEN = token
            _TVDB_TOKEN_KEY = api_key
        return token
    except Exception as exc:
        logging.warning("TVDb login failed: %s", exc)
        return None


def _poster_from_hit(hit: Dict[str, Any]) -> Optional[str]:
    for key in ("image_url", "thumbnail", "image"):
        url = hit.get(key)
        if url and str(url).startswith("http"):
            return str(url)
    return None


async def tvdb_poster_lookup(
    title: str,
    year: Optional[int],
    media_type: str,
    api_key: Optional[str],
) -> Optional[Dict[str, Any]]:
    if not api_key or not title:
        return None
    token = await _tvdb_token(api_key)
    if not token:
        return None
    kind = "series" if media_type in {"show", "tv", "anime"} else "movie"
    params: Dict[str, Any] = {"query": title, "type": kind}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                "https://api4.thetvdb.com/v4/search",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
        if response.status_code != 200:
            return None
        hits = response.json().get("data") or []
        if not hits:
            return None
        chosen = hits[0]
        if year:
            for hit in hits:
                raw_year = hit.get("year") or (hit.get("first_air_time") or "")[:4]
                try:
                    if int(raw_year) == int(year):
                        chosen = hit
                        break
                except (TypeError, ValueError):
                    continue
        poster = _poster_from_hit(chosen)
        if not poster:
            return None
        out: Dict[str, Any] = {"poster": poster, "source": "tvdb"}
        if chosen.get("tvdb_id") or chosen.get("id"):
            out["tvdb_id"] = chosen.get("tvdb_id") or chosen.get("id")
        return out
    except Exception as exc:
        logging.warning("TVDb lookup failed for %s: %s", title, exc)
        return None


async def test_tvdb_key(api_key: Optional[str]) -> Dict[str, Any]:
    if not api_key:
        return {"ok": False, "message": "No TVDb API key configured"}
    token = await _tvdb_token(api_key)
    if not token:
        return {"ok": False, "message": "TVDb rejected the API key"}
    return {"ok": True, "message": "TVDb API key accepted"}
