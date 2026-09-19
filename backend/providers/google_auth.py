"""Google OAuth for CineMind accounts. Tokens never go to the browser."""

from typing import Any, Dict
from urllib.parse import urlencode
import logging
import os

import httpx

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def _client_id() -> str:
    return (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()


def _client_secret() -> str:
    return (os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip()


def _redirect_uri() -> str:
    # Google rejects private LAN IPs (192.168.x) as redirect_uri. Loopback is required.
    raw = (
        os.environ.get("GOOGLE_REDIRECT_URI")
        or "http://localhost:8001/api/auth/google/callback"
    ).strip()
    raw = raw.replace("127.0.0.1", "localhost")
    if "://192.168." in raw or "://10." in raw or "://172." in raw:
        return "http://localhost:8001/api/auth/google/callback"
    return raw


def google_oauth_configured() -> bool:
    client_id = _client_id()
    secret = _client_secret()
    if not client_id or not secret:
        return False
    if client_id.startswith("your-") or secret.startswith("your-"):
        return False
    return True


def authorization_url(state: str) -> str:
    params = {
        "client_id": _client_id(),
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "include_granted_scopes": "true",
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> Dict[str, Any]:
    """Exchange an auth code for verified Google profile fields."""
    async with httpx.AsyncClient(timeout=15) as client:
        token_response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": _client_id(),
                "client_secret": _client_secret(),
                "redirect_uri": _redirect_uri(),
                "grant_type": "authorization_code",
            },
        )
        if token_response.status_code != 200:
            logging.warning("Google token exchange failed: %s", token_response.status_code)
            raise ValueError("Google token exchange failed")
        access_token = (token_response.json() or {}).get("access_token")
        if not access_token:
            raise ValueError("Google token missing")
        profile_response = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if profile_response.status_code != 200:
            logging.warning("Google userinfo failed: %s", profile_response.status_code)
            raise ValueError("Google profile lookup failed")
    profile = profile_response.json() or {}
    email = str(profile.get("email") or "").strip().lower()
    if not email or not profile.get("email_verified"):
        raise ValueError("Google account has no verified email")
    return {
        "google_sub": str(profile.get("sub") or ""),
        "email": email,
        "name": (str(profile.get("name") or "").strip() or email.split("@")[0])[:80],
        "picture": str(profile.get("picture") or "")[:500],
    }
