"""Resolve per-user artwork provider keys without leaking them to the browser."""

from typing import Any, Dict, Optional

from config import TMDB_KEY, TVDB_API_KEY


def resolve_tmdb_api_key(conn: Optional[Dict[str, Any]] = None) -> Optional[str]:
    if conn and conn.get("tmdb_api_key"):
        return str(conn["tmdb_api_key"]).strip() or None
    return (TMDB_KEY or None) and str(TMDB_KEY).strip() or None


def resolve_tvdb_api_key(conn: Optional[Dict[str, Any]] = None) -> Optional[str]:
    if conn and conn.get("tvdb_api_key"):
        return str(conn["tvdb_api_key"]).strip() or None
    return (TVDB_API_KEY or None) and str(TVDB_API_KEY).strip() or None


def artwork_configured(conn: Optional[Dict[str, Any]] = None) -> Dict[str, bool]:
    return {
        "tmdb_configured": bool(resolve_tmdb_api_key(conn)),
        "tvdb_configured": bool(resolve_tvdb_api_key(conn)),
    }
