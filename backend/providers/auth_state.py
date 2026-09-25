"""A sign-in the provider no longer accepts is not a connection.

Sources showed Trakt, Simkl and Plex as "Connected" for as long as a token was
stored. On 2026-09-25 Trakt revoked the stored session (~10:00 UTC, the refresh
answered 400 invalid_grant "session not found"), Simkl had answered 412/401 since
the app was deleted, and Plex 401 - and all three still read "Connected", with no
Connect button to press, while every run of the Tv job failed with
trakt_http_error.

A 401 (Simkl: 401 or 412) from any live call records the provider's reason on
the connection; a new sign-in or a call that succeeds again clears it.
`connections_public` then reports the provider as not connected, so Sources
offers Connect again.

Recording only what a call happened to meet left a gap: at 13:30 UTC the same
day Simkl and Plex still read "Connected" over tokens both refused, because
nothing had called them since the new code went live. `verify_connections`
asks every provider with a stored sign-in one read-only question (who is this
account?) and records the answer, so Sources shows what the providers say now.
An outage or a timeout proves nothing either way and changes nothing.
"""

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

AUTH_PROVIDERS = ("trakt", "simkl", "plex", "anilist")
CHECK_TIMEOUT = 10

#: What a check can conclude. Only "connected" and "rejected" change the stored state.
CONNECTED, REJECTED, UNVERIFIED, NOT_CONNECTED = "connected", "rejected", "unverified", "not_connected"


def auth_error_field(provider: str) -> str:
    return f"{provider}_auth_error"


def auth_error(conn: Dict[str, Any], provider: str) -> Optional[str]:
    return conn.get(auth_error_field(provider)) or None


def is_auth_rejection(provider: str, status: int) -> bool:
    """The provider refused the stored sign-in itself (not an outage)."""
    if provider == "simkl":
        return status in (401, 412)
    return status == 401


async def note_auth_failure(user_id: Optional[str], provider: str, detail: str) -> None:
    if not user_id or provider not in AUTH_PROVIDERS:
        return
    from database import db

    await db.connections.update_one(
        {"user_id": user_id},
        {"$set": {auth_error_field(provider): detail,
                  f"{provider}_auth_error_at": datetime.now(timezone.utc).isoformat()}},
    )


async def clear_auth_failure(user_id: Optional[str], provider: str) -> None:
    if not user_id or provider not in AUTH_PROVIDERS:
        return
    from database import db

    await db.connections.update_one(
        {"user_id": user_id, auth_error_field(provider): {"$exists": True}},
        {"$unset": {auth_error_field(provider): "", f"{provider}_auth_error_at": ""}},
    )


@dataclass
class SignInCheck:
    provider: str
    state: str
    detail: str
    http_status: Optional[int] = None
    account: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _refused(provider: str, status: int) -> bool:
    # 403 is a refusal too when the question is "who am I": the call cannot work.
    return is_auth_rejection(provider, status) or status == 403


async def check_trakt(user_id: Optional[str], conn: Dict[str, Any]) -> SignInCheck:
    from config import TRAKT_API
    from providers.trakt import resolve_trakt_client_id, trakt_headers, trakt_token

    if not (conn.get("trakt_access_token") or conn.get("trakt_refresh_token")):
        return SignInCheck("trakt", NOT_CONNECTED, "Not signed in")
    client_id = resolve_trakt_client_id(conn)
    if not client_id:
        return SignInCheck("trakt", UNVERIFIED, "No Trakt app configured on the server")
    # Renews the token first when it is about to expire, exactly as every other call does.
    token = await trakt_token(user_id, conn) if user_id else conn.get("trakt_access_token")
    if not token:
        return SignInCheck("trakt", REJECTED, "Trakt refused to renew the saved sign-in: connect Trakt again")
    async with httpx.AsyncClient(timeout=CHECK_TIMEOUT) as client:
        response = await client.get(f"{TRAKT_API}/users/settings", headers=trakt_headers(client_id, token))
    if response.status_code == 200:
        account = ((response.json() or {}).get("user") or {}).get("username")
        return SignInCheck("trakt", CONNECTED, "Trakt accepts the saved sign-in", 200, account)
    if _refused("trakt", response.status_code):
        return SignInCheck("trakt", REJECTED,
                           f"Trakt no longer accepts the saved sign-in ({response.status_code}): connect Trakt again",
                           response.status_code)
    return SignInCheck("trakt", UNVERIFIED, f"Trakt answered {response.status_code}", response.status_code)


async def check_simkl(conn: Dict[str, Any]) -> SignInCheck:
    from config import SIMKL_API
    from providers.simkl import simkl_failure_hint, simkl_headers, simkl_token_client_id

    token = conn.get("simkl_access_token")
    if not token:
        return SignInCheck("simkl", NOT_CONNECTED, "Not signed in")
    client_id = simkl_token_client_id(conn)
    if not client_id:
        return SignInCheck("simkl", UNVERIFIED, "No Simkl app configured on the server")
    async with httpx.AsyncClient(timeout=CHECK_TIMEOUT) as client:
        response = await client.post(
            f"{SIMKL_API}/users/settings",
            params={"client_id": client_id, "app-name": "CineMindAI", "app-version": "1.0"},
            headers=simkl_headers(client_id, token),
        )
    if response.status_code == 200:
        account = ((response.json() or {}).get("user") or {}).get("name")
        return SignInCheck("simkl", CONNECTED, "Simkl accepts the saved sign-in", 200, account)
    if _refused("simkl", response.status_code):
        return SignInCheck("simkl", REJECTED, simkl_failure_hint(response), response.status_code)
    return SignInCheck("simkl", UNVERIFIED, f"Simkl answered {response.status_code}", response.status_code)


PLEX_TV_USER = "https://plex.tv/api/v2/user"


async def check_plex(conn: Dict[str, Any]) -> SignInCheck:
    token = conn.get("plex_token")
    if not token:
        return SignInCheck("plex", NOT_CONNECTED, "Not signed in")
    headers = {"X-Plex-Token": token, "Accept": "application/json"}
    base = (conn.get("plex_url") or "").rstrip("/")
    server_status: Optional[int] = None
    if base:
        try:
            async with httpx.AsyncClient(timeout=CHECK_TIMEOUT) as client:
                response = await client.get(f"{base}/library/sections", headers=headers)
            server_status = response.status_code
        except httpx.HTTPError:
            server_status = None
        if server_status == 200:
            return SignInCheck("plex", CONNECTED, "The Plex server accepts the saved token", 200,
                               conn.get("plex_username"))
        if server_status is not None and _refused("plex", server_status):
            return SignInCheck("plex", REJECTED,
                               f"The Plex server refused the saved token ({server_status}): connect with Plex again",
                               server_status)
    # The server did not answer (or no URL is saved): plex.tv can still say
    # whether the account token itself is valid.
    async with httpx.AsyncClient(timeout=CHECK_TIMEOUT) as client:
        response = await client.get(PLEX_TV_USER, headers={
            **headers, "X-Plex-Client-Identifier": conn.get("plex_client_identifier") or "cinemind",
            "X-Plex-Product": "CineMind",
        })
    if response.status_code == 200:
        body = response.json() or {}
        detail = "plex.tv accepts the saved token" + ("; the server did not answer" if base else "")
        return SignInCheck("plex", CONNECTED, detail, 200, conn.get("plex_username") or body.get("username"))
    if _refused("plex", response.status_code):
        return SignInCheck("plex", REJECTED,
                           f"Plex no longer accepts the saved token ({response.status_code}): connect with Plex again",
                           response.status_code)
    return SignInCheck("plex", UNVERIFIED, f"Plex answered {response.status_code}", response.status_code)


ANILIST_GRAPHQL = "https://graphql.anilist.co"


async def check_anilist(conn: Dict[str, Any]) -> SignInCheck:
    token = conn.get("anilist_access_token")
    if not token:
        return SignInCheck("anilist", NOT_CONNECTED, "Not signed in")
    async with httpx.AsyncClient(timeout=CHECK_TIMEOUT) as client:
        response = await client.post(
            ANILIST_GRAPHQL,
            json={"query": "query { Viewer { id name } }"},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
    try:
        body = response.json() or {}
    except ValueError:
        body = {}
    viewer = (body.get("data") or {}).get("Viewer") if isinstance(body.get("data"), dict) else None
    if response.status_code == 200 and viewer:
        return SignInCheck("anilist", CONNECTED, "AniList accepts the saved sign-in", 200, viewer.get("name"))
    messages = " ".join(str(error.get("message") or "") for error in body.get("errors") or [] if isinstance(error, dict))
    # AniList answers a revoked or broken token with 400 "Invalid token".
    if response.status_code in (401, 403) or (response.status_code == 400 and "invalid token" in messages.lower()):
        return SignInCheck("anilist", REJECTED,
                           f"AniList no longer accepts the saved sign-in ({response.status_code}): connect AniList again",
                           response.status_code)
    return SignInCheck("anilist", UNVERIFIED, f"AniList answered {response.status_code}", response.status_code)


async def check_provider(provider: str, user_id: Optional[str], conn: Dict[str, Any]) -> SignInCheck:
    checks = {
        "trakt": lambda: check_trakt(user_id, conn),
        "simkl": lambda: check_simkl(conn),
        "plex": lambda: check_plex(conn),
        "anilist": lambda: check_anilist(conn),
    }
    if provider not in checks:
        raise ValueError(f"unknown provider {provider}")
    try:
        return await checks[provider]()
    except httpx.HTTPError as exc:
        return SignInCheck(provider, UNVERIFIED, f"{provider} could not be reached ({exc.__class__.__name__})")
    except ValueError as exc:  # an answer that is not the JSON the provider documents
        return SignInCheck(provider, UNVERIFIED, f"{provider} gave an unreadable answer ({exc.__class__.__name__})")


async def record_check(user_id: Optional[str], check: SignInCheck) -> None:
    """Keep the stored state in line with what the provider just said; an unanswered question changes nothing."""
    if check.state == CONNECTED:
        await clear_auth_failure(user_id, check.provider)
    elif check.state == REJECTED:
        await note_auth_failure(user_id, check.provider, check.detail)


async def verify_connections(user_id: str, providers=AUTH_PROVIDERS) -> Dict[str, Dict[str, Any]]:
    """Ask every provider with a stored sign-in whether it still accepts it (read-only calls)."""
    from database import db

    conn = await db.connections.find_one({"user_id": user_id}, {"_id": 0}) or {}
    checks = await asyncio.gather(*(check_provider(provider, user_id, conn) for provider in providers))
    for check in checks:
        await record_check(user_id, check)
    return {check.provider: check.as_dict() for check in checks}
