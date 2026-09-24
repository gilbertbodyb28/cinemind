from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, Cookie, Header, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from dotenv import load_dotenv
import asyncio
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import uuid
import json
import html
import random
import secrets
import httpx
import re
import bcrypt
from pathlib import Path
from urllib.parse import urlparse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Literal
from datetime import datetime, timezone, timedelta
from config import (
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    effective_ollama_model,
    is_legacy_ollama_model,
    resolve_model,
)
from llm import generate_with_llm
from providers.ollama import stream_ollama
from mediamanager_client import (
    media_type_for_mediamanager,
    mediamanager_add_title,
    mediamanager_is_configured,
    mediamanager_login,
    normalize_mediamanager_url,
    send_item_to_library,
)
from providers.google_auth import (
    authorization_url as google_authorization_url,
    exchange_code as google_exchange_code,
    google_oauth_configured,
)
from jobs.engine import safe_provider_error
from providers.anilist import (
    auth_url as anilist_auth_url,
    client_id as anilist_client_id,
    credentials_configured as anilist_credentials_configured,
    exchange_code as anilist_exchange_code,
    fetch_media_list as anilist_fetch_media_list,
    fetch_viewer as anilist_fetch_viewer,
    parse_media_list_collection,
    redirect_uri as anilist_redirect_uri,
)

ROOT_DIR = Path(__file__).parent
_BUILD_DIR_FOR_VERSION = ROOT_DIR.parent / "frontend" / "build"
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

app = FastAPI(title="CineMind AI")
api = APIRouter(prefix="/api")


def _env_flag(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


COOKIE_SECURE = _env_flag("COOKIE_SECURE", "false")
COOKIE_SAMESITE = os.environ.get("COOKIE_SAMESITE", "lax").strip().lower() or "lax"
SESSION_TTL_DAYS = int(os.environ.get("SESSION_TTL_DAYS", "7"))
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000").rstrip("/")

EMERGENT_AUTH_URL = "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data"
TMDB_KEY = os.environ.get("TMDB_API_KEY")
TRAKT_CLIENT_ID = os.environ.get("TRAKT_CLIENT_ID")
TRAKT_CLIENT_SECRET = os.environ.get("TRAKT_CLIENT_SECRET")
TRAKT_API = "https://api.trakt.tv"
SIMKL_CLIENT_ID = os.environ.get("SIMKL_CLIENT_ID")
# Read alongside the client id: the device flow exchanges the code for a token
# with both, and without this name the exchange raised NameError and answered
# 500 instead of the 503 the missing-credentials path is meant to return.
SIMKL_CLIENT_SECRET = os.environ.get("SIMKL_CLIENT_SECRET")
SIMKL_API = "https://api.simkl.com"
PLACEHOLDER_POSTER = "https://images.unsplash.com/photo-1489599849927-2ee91cede3ba?w=500"


class GenerateBody(BaseModel):
    model: Optional[str] = None


# ---------- Models ----------
class User(BaseModel):
    user_id: str
    email: str
    name: str
    picture: Optional[str] = None
    created_at: datetime


class Connections(BaseModel):
    trakt_client_id: Optional[str] = None
    trakt_access_token: Optional[str] = None
    trakt_refresh_token: Optional[str] = None
    trakt_expires_at: Optional[int] = None
    trakt_username: Optional[str] = None
    trakt_connected: bool = False
    simkl_client_id: Optional[str] = None
    simkl_access_token: Optional[str] = None
    simkl_username: Optional[str] = None
    simkl_connected: bool = False
    plex_url: Optional[str] = None
    plex_token: Optional[str] = None
    ollama_url: Optional[str] = None
    ollama_model: Optional[str] = OLLAMA_MODEL
    llm_model: Optional[str] = None
    mediamanager_url: Optional[str] = None
    mediamanager_email: Optional[str] = None
    mediamanager_password: Optional[str] = None  # write-only; never returned
    mediamanager_configured: bool = False
    anilist_username: Optional[str] = None
    anilist_connected: bool = False
    anilist_client_configured: bool = False
    plex_connected: bool = False
    tmdb_configured: bool = False
    # Vision, Apple and Spatial, plus the eight-theme spatial collection.
    # normalize_ui_theme() is what guards the value on the way in and out.
    ui_theme: Literal[
        "vision", "apple", "spatial",
        "spatial-01", "spatial-02", "spatial-03", "spatial-04",
        "spatial-05", "spatial-06", "spatial-07", "spatial-08", "spatial-09",
    ] = "vision"
    ui_mode: Literal["dark", "light"] = "dark"
    glass_intensity: int = 78
    wallpaper: str = "poster"
    sidebar_icon_size: int = 40


class AddOptionsBody(BaseModel):
    """Choices from the Add to MediaManager dialog; omitted fields keep MM defaults."""

    series_type: Optional[Literal["standard", "anime"]] = None
    metadata_provider: Optional[Literal["tmdb", "tvdb"]] = None
    monitoring: Optional[Literal["monitored", "unmonitored"]] = None
    monitor_scope: Optional[Literal["entire", "specific", "future", "missing"]] = None
    monitor_season: Optional[List[int]] = None
    monitor_episode: Optional[List[str]] = None
    search_now: Optional[bool] = False
    release_rules: Optional[Dict[str, Any]] = None


class BulkRequestsBody(BaseModel):
    ids: List[str]
    action: Literal["approve", "reject"]
    options: Optional[AddOptionsBody] = None


DEFAULT_GLASS_INTENSITY = 78

# Icon rail button size in px. 40 is the original rail; 98 is the largest the
# bar can grow to before the buttons stop reading as one row of controls.
DEFAULT_SIDEBAR_ICON_SIZE = 40
MIN_SIDEBAR_ICON_SIZE = 28
MAX_SIDEBAR_ICON_SIZE = 98


UI_THEMES = {"vision", "apple", "spatial"} | {f"spatial-{n:02d}" for n in range(1, 10)}


UI_MODES = {"dark", "light"}


def normalize_ui_mode(value: Any) -> str:
    """Dark is the default, so an account that never picks one is unchanged."""
    name = str(value or "").strip().lower()
    return name if name in UI_MODES else "dark"


def normalize_ui_theme(value: Any) -> str:
    name = str(value or "").strip().lower()
    return name if name in UI_THEMES else "vision"


# Selectable background gradients; "poster" is the original poster-lit wash.
WALLPAPERS = {"poster", "midnight", "ember", "dusk", "mocha", "aurora", "graphite"}


def normalize_wallpaper(value: Any) -> str:
    name = str(value or "").strip().lower()
    return name if name in WALLPAPERS else "poster"


def normalize_glass_intensity(value: Any) -> int:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return DEFAULT_GLASS_INTENSITY
    return max(0, min(100, n))


def normalize_sidebar_icon_size(value: Any) -> int:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return DEFAULT_SIDEBAR_ICON_SIZE
    return max(MIN_SIDEBAR_ICON_SIZE, min(MAX_SIDEBAR_ICON_SIZE, n))


def connections_public(doc: Dict[str, Any]) -> Connections:
    """Serialize connections without leaking write-only secrets."""
    data = {**doc}
    data["trakt_connected"] = bool(doc.get("trakt_refresh_token"))
    data["simkl_connected"] = bool(doc.get("simkl_access_token"))
    data["anilist_connected"] = bool(doc.get("anilist_access_token"))
    data["anilist_username"] = doc.get("anilist_username")
    data["anilist_client_configured"] = anilist_credentials_configured() or bool(anilist_client_id())
    data["plex_connected"] = bool(doc.get("plex_token"))
    data["tmdb_configured"] = bool(TMDB_KEY)
    data["mediamanager_configured"] = mediamanager_is_configured(doc)
    if data.get("mediamanager_url"):
        data["mediamanager_url"] = normalize_mediamanager_url(data["mediamanager_url"])
    data["mediamanager_password"] = None
    data["trakt_access_token"] = None
    data["trakt_refresh_token"] = None
    data["simkl_access_token"] = None
    data["plex_token"] = None
    data["ollama_url"] = doc.get("ollama_url") or OLLAMA_BASE_URL
    data["ollama_model"] = resolve_model(doc)[1]
    data["ui_theme"] = normalize_ui_theme(doc.get("ui_theme"))
    data["ui_mode"] = normalize_ui_mode(doc.get("ui_mode"))
    data["glass_intensity"] = normalize_glass_intensity(doc.get("glass_intensity"))
    data["wallpaper"] = normalize_wallpaper(doc.get("wallpaper"))
    data["sidebar_icon_size"] = normalize_sidebar_icon_size(doc.get("sidebar_icon_size"))
    return Connections(**{k: data.get(k) for k in Connections.model_fields})


class HistoryItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    year: Optional[int] = None
    type: str  # movie | show
    genres: List[str] = []
    rating: Optional[float] = None
    poster: Optional[str] = None
    watched_at: Optional[str] = None
    source: str  # trakt | simkl | plex | demo


class Recommendation(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    year: int
    type: str
    genres: List[str]
    poster: str
    backdrop: Optional[str] = None
    synopsis: str
    match_score: int
    why: str
    tmdb_rating: float
    saved: bool = False
    dismissed: bool = False


# ---------- Auth helpers ----------
class LoginBody(BaseModel):
    email: str
    password: str


class RegisterBody(BaseModel):
    name: str
    email: str
    password: str = Field(min_length=6)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _user_from_doc(user_doc: Dict[str, Any]) -> User:
    created_at = user_doc.get("created_at")
    if isinstance(created_at, str):
        created_at = datetime.fromisoformat(created_at)
    return User(
        user_id=user_doc["user_id"],
        email=user_doc["email"],
        name=user_doc["name"],
        picture=user_doc.get("picture") or None,
        created_at=created_at,
    )


def _auth_payload(user_doc: Dict[str, Any]) -> Dict[str, Any]:
    return _user_from_doc(user_doc).model_dump(mode="json")


async def issue_session(user_id: str, response: Response) -> str:
    session_token = secrets.token_urlsafe(48)
    now = datetime.now(timezone.utc)
    await db.user_sessions.insert_one({
        "user_id": user_id,
        "session_token": session_token,
        "expires_at": (now + timedelta(days=SESSION_TTL_DAYS)).isoformat(),
        "created_at": now.isoformat(),
    })
    response.set_cookie(
        key="session_token",
        value=session_token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        path="/",
        max_age=SESSION_TTL_DAYS * 24 * 3600,
    )
    return session_token


async def upsert_google_user(profile: Dict[str, Any]) -> Dict[str, Any]:
    email = str(profile.get("email") or "").strip().lower()
    sub = str(profile.get("google_sub") or "").strip()
    if not email:
        raise ValueError("Google profile missing email")
    existing = None
    if sub:
        existing = await db.users.find_one({"google_sub": sub}, {"_id": 0})
    if not existing:
        existing = await db.users.find_one({"email": email}, {"_id": 0})
    if existing:
        updates: Dict[str, Any] = {}
        if sub and existing.get("google_sub") != sub:
            updates["google_sub"] = sub
        if profile.get("name") and not existing.get("name"):
            updates["name"] = profile["name"]
        if profile.get("picture") and not existing.get("picture"):
            updates["picture"] = profile["picture"]
        if updates:
            await db.users.update_one({"user_id": existing["user_id"]}, {"$set": updates})
            existing.update(updates)
        return existing
    user_doc = {
        "user_id": f"user_{uuid.uuid4().hex[:12]}",
        "email": email,
        "name": (profile.get("name") or email.split("@")[0])[:80],
        "picture": profile.get("picture") or "",
        "google_sub": sub or None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.users.insert_one(dict(user_doc))
    return user_doc


async def get_current_user(
    session_token: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
) -> User:
    token = session_token
    if not token and authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    session = await db.user_sessions.find_one({"session_token": token}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")

    expires_at = session["expires_at"]
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Session expired")

    user_doc = await db.users.find_one({"user_id": session["user_id"]}, {"_id": 0})
    if not user_doc:
        raise HTTPException(status_code=401, detail="User not found")
    return _user_from_doc(user_doc)


def _frontend_auth_redirect(reason: str, frontend_base: Optional[str] = None) -> RedirectResponse:
    base = (frontend_base or FRONTEND_URL).rstrip("/")
    return RedirectResponse(f"{base}/?auth_error={reason}")


def _safe_frontend_origin(candidate: Optional[str]) -> str:
    """Allow localhost / private LAN origins only; fall back to FRONTEND_URL."""
    raw = (candidate or "").strip()
    if not raw:
        return FRONTEND_URL
    try:
        parsed = urlparse(raw)
    except Exception:
        return FRONTEND_URL
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return FRONTEND_URL
    host = parsed.hostname.lower()
    if host in ("localhost", "127.0.0.1"):
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    parts = host.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        a, b = int(parts[0]), int(parts[1])
        private = a == 10 or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31)
        if private:
            # Google cookies land on localhost; send the browser back there, same port.
            port = f":{parsed.port}" if parsed.port else ""
            return f"{parsed.scheme}://localhost{port}"
    return FRONTEND_URL


# ---------- Auth routes ----------
@api.post("/auth/register", status_code=201)
async def register(body: RegisterBody, response: Response):
    email = body.email.strip().lower()
    if await db.users.find_one({"email": email}, {"_id": 1}):
        raise HTTPException(status_code=409, detail="An account with that email already exists")
    user_doc = {
        "user_id": f"user_{uuid.uuid4().hex[:12]}",
        "email": email,
        "name": body.name.strip(),
        "picture": "",
        "password_hash": hash_password(body.password),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.users.insert_one(dict(user_doc))
    await issue_session(user_doc["user_id"], response)
    return _auth_payload(user_doc)


@api.post("/auth/login")
async def login(body: LoginBody, response: Response):
    email = body.email.strip().lower()
    user_doc = await db.users.find_one({"email": email}, {"_id": 0})
    if not user_doc or not verify_password(body.password, user_doc.get("password_hash") or ""):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    await issue_session(user_doc["user_id"], response)
    return _auth_payload(user_doc)


@api.post("/auth/session")
async def create_session(request: Request, response: Response):
    """Legacy Emergent session exchange (kept for compatibility)."""
    body = await request.json()
    session_id = body.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")

    async with httpx.AsyncClient(timeout=15) as hc:
        r = await hc.get(EMERGENT_AUTH_URL, headers={"X-Session-ID": session_id})
    if r.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid session_id")
    data = r.json()

    email = data["email"]
    existing = await db.users.find_one({"email": email}, {"_id": 0})
    if existing:
        user_id = existing["user_id"]
        await db.users.update_one(
            {"user_id": user_id},
            {"$set": {"name": data["name"], "picture": data.get("picture", "")}},
        )
    else:
        user_id = f"user_{uuid.uuid4().hex[:12]}"
        await db.users.insert_one({
            "user_id": user_id,
            "email": email,
            "name": data["name"],
            "picture": data.get("picture", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    await issue_session(user_id, response)
    return {
        "user_id": user_id,
        "email": email,
        "name": data["name"],
        "picture": data.get("picture", ""),
    }


@api.get("/auth/google/start")
async def google_start(request: Request, return_to: Optional[str] = None):
    if not google_oauth_configured():
        return JSONResponse(
            {"detail": "Google sign-in is not configured on this server yet."},
            status_code=503,
        )
    state = secrets.token_urlsafe(24)
    await db.oauth_states.insert_one({
        "state": state,
        "provider": "google",
        "return_to": _safe_frontend_origin(return_to),
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    authorization_url = google_authorization_url(state)
    # Browser navigation (not fetch) should bounce straight to Google, never sit on :8001 JSON.
    accept = (request.headers.get("accept") or "").lower()
    if "text/html" in accept and "application/json" not in accept.split(",")[0]:
        return RedirectResponse(authorization_url)
    return {"authorization_url": authorization_url}


@api.get("/auth/google/callback")
async def google_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    frontend_base = FRONTEND_URL
    if error:
        return _frontend_auth_redirect("google_denied", frontend_base)
    if not code or not state:
        return _frontend_auth_redirect("google_state", frontend_base)
    stored = await db.oauth_states.find_one_and_delete({"state": state, "provider": "google"})
    if stored:
        frontend_base = _safe_frontend_origin(stored.get("return_to"))
    if not stored:
        return _frontend_auth_redirect("google_state", frontend_base)
    expires_at = stored.get("expires_at")
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at and expires_at < datetime.now(timezone.utc):
        return _frontend_auth_redirect("google_state", frontend_base)
    try:
        profile = await google_exchange_code(code)
        user_doc = await upsert_google_user(profile)
    except Exception:
        logging.warning("Google sign-in failed", exc_info=True)
        return _frontend_auth_redirect("google_failed", frontend_base)
    redirect = RedirectResponse(f"{frontend_base}/dashboard")
    await issue_session(user_doc["user_id"], redirect)
    return redirect


@api.get("/auth/me", response_model=User)
async def me(user: User = Depends(get_current_user)):
    return user


@api.post("/auth/logout")
async def logout(response: Response, session_token: Optional[str] = Cookie(None)):
    if session_token:
        await db.user_sessions.delete_one({"session_token": session_token})
    response.delete_cookie(
        "session_token",
        path="/",
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
    )
    return {"ok": True}


# ---------- Connections ----------
@api.get("/connections", response_model=Connections)
async def get_connections(user: User = Depends(get_current_user)):
    doc = await db.connections.find_one({"user_id": user.user_id}, {"_id": 0, "user_id": 0}) or {}
    return connections_public(doc)


@api.put("/connections", response_model=Connections)
async def update_connections(payload: Connections, user: User = Depends(get_current_user)):
    data = payload.model_dump(
        exclude_unset=True,
        exclude={
            "trakt_connected",
            "simkl_connected",
            "mediamanager_configured",
            "anilist_connected",
            "anilist_client_configured",
            "plex_connected",
            "tmdb_configured",
        },
    )
    # Blank secrets keep stored values; explicit null would clear.
    for secret in (
        "mediamanager_password",
        "plex_token",
        "trakt_access_token",
        "simkl_access_token",
    ):
        if secret in data and data[secret] == "":
            data.pop(secret)
    if data.get("mediamanager_url"):
        data["mediamanager_url"] = normalize_mediamanager_url(data["mediamanager_url"])
    if "ollama_model" in data:
        data["ollama_model"] = effective_ollama_model({"ollama_model": data.get("ollama_model")})
    if "ollama_url" in data and not str(data.get("ollama_url") or "").strip():
        data["ollama_url"] = OLLAMA_BASE_URL
    if "ui_theme" in data:
        data["ui_theme"] = normalize_ui_theme(data.get("ui_theme"))
    if "ui_mode" in data:
        data["ui_mode"] = normalize_ui_mode(data.get("ui_mode"))
    if "glass_intensity" in data:
        data["glass_intensity"] = normalize_glass_intensity(data.get("glass_intensity"))
    if "wallpaper" in data:
        data["wallpaper"] = normalize_wallpaper(data.get("wallpaper"))
    if "sidebar_icon_size" in data:
        data["sidebar_icon_size"] = normalize_sidebar_icon_size(data.get("sidebar_icon_size"))
    await db.connections.update_one(
        {"user_id": user.user_id},
        {"$set": {**data, "user_id": user.user_id}},
        upsert=True,
    )
    doc = await db.connections.find_one({"user_id": user.user_id}, {"_id": 0, "user_id": 0}) or {}
    return connections_public(doc)


# ---------- Simkl PIN OAuth ----------
class DevicePoll(BaseModel):
    device_code: str


def simkl_params(client_id: str) -> Dict[str, str]:
    return {"client_id": client_id, "app-name": "CineMindAI", "app-version": "1.0"}


def simkl_headers(client_id: str, token: Optional[str] = None) -> Dict[str, str]:
    h = {"User-Agent": "CineMindAI/1.0", "simkl-api-key": client_id, "Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def resolve_simkl_client_id(user_id: str) -> str:
    """The manual field in Sources wins, exactly as the sync paths already treat it."""
    conn = await db.connections.find_one({"user_id": user_id}, {"_id": 0, "simkl_client_id": 1}) or {}
    return (conn.get("simkl_client_id") or SIMKL_CLIENT_ID or "").strip()


def simkl_error_detail(response: httpx.Response) -> str:
    """Pass Simkl's own wording through; "responded 412" tells nobody anything."""
    try:
        payload = response.json() or {}
    except Exception:
        payload = {}
    message = payload.get("message") or payload.get("error")
    if message:
        return f"Simkl: {message} (HTTP {response.status_code})"
    return f"Simkl responded {response.status_code}"


@api.post("/simkl/pin/start")
async def simkl_pin_start(user: User = Depends(get_current_user)):
    client_id = await resolve_simkl_client_id(user.user_id)
    if not client_id:
        raise HTTPException(status_code=503, detail="Simkl app credentials not configured on server")
    if not SIMKL_CLIENT_SECRET:
        raise HTTPException(status_code=503, detail="Simkl app secret not configured on server")
    # OAuth 2.0 device flow. The legacy GET /oauth/pin now answers OAuth2 apps with
    # 400 "use POST /oauth2/device instead", so this mirrors the Trakt device flow.
    async with httpx.AsyncClient(timeout=15) as hc:
        r = await hc.get(f"{SIMKL_API}/oauth2/device", params={"client_id": client_id}, headers=simkl_headers(client_id))
    data = r.json() if r.status_code == 200 else {}
    if r.status_code != 200 or not data.get("device_code") or not data.get("user_code"):
        raise HTTPException(status_code=502, detail=simkl_error_detail(r))
    return {
        "device_code": data["device_code"],
        "user_code": data["user_code"],
        "verification_url": data.get("verification_uri") or data.get("verification_url") or "https://simkl.com/pin",
        "expires_in": int(data.get("expires_in", 900)),
        "interval": int(data.get("interval", 5)),
    }


@api.post("/simkl/pin/poll")
async def simkl_pin_poll(body: DevicePoll, user: User = Depends(get_current_user)):
    client_id = await resolve_simkl_client_id(user.user_id)
    async with httpx.AsyncClient(timeout=15) as hc:
        r = await hc.post(
            f"{SIMKL_API}/oauth2/token",
            json={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": client_id,
                "client_secret": SIMKL_CLIENT_SECRET,
                "device_code": body.device_code,
            },
            headers=simkl_headers(client_id),
        )
        try:
            data = r.json() or {}
        except Exception:
            data = {}
        if r.status_code == 200 and data.get("access_token"):
            token = data["access_token"]
            username = None
            try:
                me = await hc.post(f"{SIMKL_API}/users/settings", params=simkl_params(client_id), headers=simkl_headers(client_id, token))
                if me.status_code == 200:
                    username = (me.json().get("user") or {}).get("name")
            except Exception as e:
                logging.warning(f"Simkl settings fetch failed: {e}")
            await db.connections.update_one(
                {"user_id": user.user_id},
                # Only the token is stored. Persisting client_id here used to pin the
                # account to whichever app was live at connect time, so rotating
                # SIMKL_CLIENT_ID in .env had no effect until the row was cleared by hand.
                # A manual client id still lives in the Sources form via PUT /connections.
                {"$set": {"user_id": user.user_id, "simkl_access_token": token, "simkl_username": username}},
                upsert=True,
            )
            return {"status": "authorized", "username": username}
        error = (data.get("error") or "").lower()
        if error == "authorization_pending":
            return {"status": "pending"}
        if error == "slow_down":
            return {"status": "slow_down"}
        if error in ("expired_token", "expired"):
            return {"status": "expired"}
        if error == "access_denied":
            return {"status": "denied"}
    return {"status": "invalid"}


@api.post("/simkl/disconnect")
async def simkl_disconnect(user: User = Depends(get_current_user)):
    await db.connections.update_one(
        {"user_id": user.user_id},
        {"$set": {"simkl_access_token": None, "simkl_username": None}},
    )
    return {"ok": True}


# ---------- AniList OAuth ----------
class AnilistCode(BaseModel):
    code: str


class AnilistListPayload(BaseModel):
    data: Optional[Dict[str, Any]] = None


@api.get("/anilist/oauth/start")
async def anilist_oauth_start(user: User = Depends(get_current_user), mode: str = "code"):
    cid = anilist_client_id()
    if not cid:
        raise HTTPException(status_code=503, detail="AniList app credentials are not configured on the server")
    response_type = "token" if mode == "token" else "code"
    callback = anilist_redirect_uri()
    return {
        "authorization_url": anilist_auth_url(cid, callback, response_type=response_type),
        "redirect_uri": callback,
        "mode": response_type,
    }


@api.get("/anilist/oauth/callback")
@api.get("/anilist/auth/callback")
async def anilist_oauth_callback(code: Optional[str] = None):
    safe = html.escape(code or "")
    frontend = html.escape(FRONTEND_URL.rstrip("/"))
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8"><title>CineMind AniList</title>
<style>body{{font-family:system-ui,sans-serif;max-width:40rem;margin:2rem auto;padding:0 1rem;background:#0b0f0c;color:#e7e5e4}}
code,textarea{{display:block;width:100%;word-break:break-all;background:#111;border:1px solid #333;border-radius:8px;padding:12px;color:#f5f5f4;margin:12px 0}}
p,.status{{line-height:1.5;color:#a8a29e}}.ok{{color:#6ee7b7}}.err{{color:#fb7185}}
a{{color:#fda4af}}</style></head><body>
<h1>AniList</h1>
<p id="status" class="status">Finishing sign-in…</p>
<textarea id="value" rows="3" readonly>{safe}</textarea>
<p><a id="back" href="{frontend}/connections">Back to CineMind Sources</a></p>
<script>
(async function () {{
  var status = document.getElementById("status");
  var box = document.getElementById("value");
  var hash = new URLSearchParams((location.hash || "").replace(/^#/, ""));
  var token = hash.get("access_token");
  var code = new URLSearchParams(location.search).get("code") || box.value.trim();
  var payload = token || code;
  if (!payload) {{
    status.className = "status err";
    status.textContent = "Missing code from AniList. Close this tab and click Connect with AniList again.";
    return;
  }}
  box.value = payload;
  try {{
    var res = await fetch("/api/anilist/oauth/exchange", {{
      method: "POST",
      credentials: "include",
      headers: {{ "Content-Type": "application/json", "Accept": "application/json" }},
      body: JSON.stringify({{ code: payload }})
    }});
    var data = await res.json().catch(function () {{ return {{}}; }});
    if (!res.ok) {{
      status.className = "status err";
      status.textContent = (data && data.detail) || ("Exchange failed (" + res.status + ")");
      return;
    }}
    status.className = "status ok";
    status.textContent = data.status === "authorized"
      ? ("Connected" + (data.username ? (" as @" + data.username) : "") + ". You can close this tab.")
      : (data.detail || "Saved. Return to CineMind Sources.");
    setTimeout(function () {{ location.href = "{frontend}/connections"; }}, 1200);
  }} catch (err) {{
    status.className = "status err";
    status.textContent = "Could not reach CineMind API to finish AniList login.";
  }}
}})();
</script>
</body></html>"""
    )


@api.post("/anilist/oauth/exchange")
async def anilist_oauth_exchange(body: AnilistCode, user: User = Depends(get_current_user)):
    if not body.code.strip():
        raise HTTPException(status_code=422, detail="Missing authorization code")
    raw = body.code.strip()
    if raw.count(".") >= 2 and " " not in raw and len(raw) > 40:
        update = {
            "user_id": user.user_id,
            "anilist_access_token": raw,
            "anilist_oauth_code": None,
        }
        viewer = await anilist_fetch_viewer(raw)
        if viewer and viewer.get("name"):
            update["anilist_username"] = viewer["name"]
        await db.connections.update_one({"user_id": user.user_id}, {"$set": update}, upsert=True)
        return {"status": "authorized", "username": update.get("anilist_username")}

    token_payload, error = await anilist_exchange_code(raw)
    update: Dict[str, Any] = {
        "user_id": user.user_id,
        "anilist_oauth_code": raw,
    }
    if token_payload and token_payload.get("access_token"):
        update["anilist_access_token"] = token_payload["access_token"]
        viewer = await anilist_fetch_viewer(token_payload["access_token"])
        if viewer and viewer.get("name"):
            update["anilist_username"] = viewer["name"]
        await db.connections.update_one({"user_id": user.user_id}, {"$set": update}, upsert=True)
        return {"status": "authorized", "username": update.get("anilist_username")}
    if error:
        raise HTTPException(status_code=401, detail=error)
    await db.connections.update_one({"user_id": user.user_id}, {"$set": update}, upsert=True)
    return {
        "status": "stored",
        "detail": "Authorization code stored server-side. Live token exchange runs when AniList credentials are present.",
    }


@api.post("/anilist/import")
async def anilist_import(payload: AnilistListPayload, user: User = Depends(get_current_user)):
    items = parse_media_list_collection(payload.model_dump())
    docs = [{**item, "id": str(uuid.uuid4()), "user_id": user.user_id} for item in items]
    if docs:
        await db.history.insert_many(docs)
    return {"count": len(docs)}


@api.post("/anilist/disconnect")
async def anilist_disconnect(user: User = Depends(get_current_user)):
    await db.connections.update_one(
        {"user_id": user.user_id},
        {"$set": {"anilist_access_token": None, "anilist_username": None, "anilist_oauth_code": None}},
    )
    return {"ok": True}


@api.post("/connections/test/anilist")
async def test_anilist(user: User = Depends(get_current_user)):
    conn = await db.connections.find_one({"user_id": user.user_id}, {"_id": 0}) or {}
    if conn.get("anilist_access_token"):
        return {"ok": True, "message": "AniList token stored server-side"}
    if anilist_client_id():
        return {"ok": False, "message": "AniList app configured but not authorized — connect AniList"}
    return {"ok": False, "message": "No AniList client id or token configured"}


# ---------- Trakt device OAuth ----------
def trakt_headers(client_id: str, token: Optional[str] = None) -> Dict[str, str]:
    h = {"Content-Type": "application/json", "trakt-api-key": client_id, "trakt-api-version": "2", "User-Agent": "CineMindAI/1.0"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


@api.post("/trakt/device/start")
async def trakt_device_start(user: User = Depends(get_current_user)):
    if not (TRAKT_CLIENT_ID and TRAKT_CLIENT_SECRET):
        raise HTTPException(status_code=503, detail="Trakt app credentials not configured on server")
    async with httpx.AsyncClient(timeout=15) as hc:
        r = await hc.post(f"{TRAKT_API}/oauth/device/code", json={"client_id": TRAKT_CLIENT_ID}, headers=trakt_headers(TRAKT_CLIENT_ID))
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Trakt responded {r.status_code}")
    return r.json()


@api.post("/trakt/device/poll")
async def trakt_device_poll(body: DevicePoll, user: User = Depends(get_current_user)):
    async with httpx.AsyncClient(timeout=15) as hc:
        r = await hc.post(
            f"{TRAKT_API}/oauth/device/token",
            json={"code": body.device_code, "client_id": TRAKT_CLIENT_ID, "client_secret": TRAKT_CLIENT_SECRET},
            headers=trakt_headers(TRAKT_CLIENT_ID),
        )
        if r.status_code == 400:
            return {"status": "pending"}
        if r.status_code == 429:
            return {"status": "slow_down"}
        if r.status_code == 410:
            return {"status": "expired"}
        if r.status_code == 418:
            return {"status": "denied"}
        if r.status_code == 404:
            return {"status": "invalid"}
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Trakt responded {r.status_code}")
        t = r.json()
        username = None
        try:
            me = await hc.get(f"{TRAKT_API}/users/settings", headers=trakt_headers(TRAKT_CLIENT_ID, t["access_token"]))
            if me.status_code == 200:
                username = me.json().get("user", {}).get("username")
        except Exception as e:
            logging.warning(f"Trakt settings fetch failed: {e}")
    await db.connections.update_one(
        {"user_id": user.user_id},
        {"$set": {
            "user_id": user.user_id,
            "trakt_client_id": TRAKT_CLIENT_ID,
            "trakt_access_token": t["access_token"],
            "trakt_refresh_token": t["refresh_token"],
            "trakt_expires_at": int(datetime.now(timezone.utc).timestamp()) + int(t.get("expires_in", 7 * 86400)),
            "trakt_username": username,
        }},
        upsert=True,
    )
    return {"status": "authorized", "username": username}


@api.post("/trakt/disconnect")
async def trakt_disconnect(user: User = Depends(get_current_user)):
    await db.connections.update_one(
        {"user_id": user.user_id},
        {"$set": {"trakt_access_token": None, "trakt_refresh_token": None, "trakt_expires_at": None, "trakt_username": None}},
    )
    return {"ok": True}


async def trakt_token(user_id: str, conn: Dict[str, Any]) -> Optional[str]:
    """Return a valid Trakt access token, refreshing (single-use refresh token) when near expiry."""
    token = conn.get("trakt_access_token")
    refresh = conn.get("trakt_refresh_token")
    expires_at = conn.get("trakt_expires_at")
    if not refresh or not expires_at or not TRAKT_CLIENT_SECRET:
        return token
    if expires_at > int(datetime.now(timezone.utc).timestamp()) + 60:
        return token
    async with httpx.AsyncClient(timeout=15) as hc:
        r = await hc.post(
            f"{TRAKT_API}/oauth/token",
            json={"refresh_token": refresh, "client_id": TRAKT_CLIENT_ID, "client_secret": TRAKT_CLIENT_SECRET, "grant_type": "refresh_token"},
            headers=trakt_headers(TRAKT_CLIENT_ID),
        )
    if r.status_code != 200:
        logging.warning(f"Trakt refresh failed {r.status_code}; user must reconnect")
        await db.connections.update_one({"user_id": user_id}, {"$set": {"trakt_access_token": None, "trakt_refresh_token": None, "trakt_expires_at": None}})
        return None
    t = r.json()
    await db.connections.update_one(
        {"user_id": user_id},
        {"$set": {
            "trakt_access_token": t["access_token"],
            "trakt_refresh_token": t["refresh_token"],
            "trakt_expires_at": int(datetime.now(timezone.utc).timestamp()) + int(t.get("expires_in", 7 * 86400)),
        }},
    )
    return t["access_token"]


@api.get("/connections/ollama/models")
async def list_ollama_models(user: User = Depends(get_current_user)):
    """Every model the user's own Ollama host has pulled, for the picker.

    The picker must show what is actually installed, not a hard-coded list:
    a name that is not on the host produces a run that fails at generate time.
    `current` is what this account resolves to today, which is not always the
    stored value - retired defaults fall back (see `effective_ollama_model`).

    Retired models are left out even when the host still has them pulled:
    saving one writes the configured default instead, so offering it means a
    Save that reports success and stores something else.
    """
    conn = await db.connections.find_one({"user_id": user.user_id}, {"_id": 0}) or {}
    url, current = resolve_model(conn)
    try:
        async with httpx.AsyncClient(timeout=8) as hc:
            r = await hc.get(f"{url.rstrip('/')}/api/tags")
    except Exception as e:
        return {"ok": False, "url": url, "current": current, "models": [],
                "message": f"Unreachable: {e.__class__.__name__}"}
    if r.status_code != 200:
        return {"ok": False, "url": url, "current": current, "models": [],
                "message": f"Ollama responded {r.status_code}"}
    models = [
        {
            "name": m.get("name"),
            "size": m.get("size"),
            "parameter_size": ((m.get("details") or {}).get("parameter_size")),
            "quantization": ((m.get("details") or {}).get("quantization_level")),
        }
        for m in (r.json().get("models") or [])
        if m.get("name") and not is_legacy_ollama_model(m.get("name"))
    ]
    models.sort(key=lambda m: (m["name"] or "").lower())
    return {"ok": True, "url": url, "current": current, "models": models,
            "message": f"{len(models)} model(s) available."}


@api.post("/connections/test/ollama")
async def test_ollama(user: User = Depends(get_current_user)):
    conn = await db.connections.find_one({"user_id": user.user_id}, {"_id": 0}) or {}
    url, model = resolve_model(conn)
    try:
        async with httpx.AsyncClient(timeout=8) as hc:
            r = await hc.get(f"{url.rstrip('/')}/api/tags")
        if r.status_code == 200:
            tags = r.json().get("models", [])
            names = [m.get("name") for m in tags]
            present = any(model == n or (n or "").startswith(f"{model}") for n in names)
            extra = f" {model} is ready." if present else f" Pull {model} with `ollama pull {model}` if it is missing."
            return {"ok": True, "message": f"Connected. {len(tags)} model(s) available.{extra}", "models": names}
        return {"ok": False, "message": f"Ollama responded {r.status_code}"}
    except Exception as e:
        return {"ok": False, "message": f"Unreachable: {e.__class__.__name__}"}


@api.post("/connections/test/trakt")
async def test_trakt(user: User = Depends(get_current_user)):
    conn = await db.connections.find_one({"user_id": user.user_id}, {"_id": 0}) or {}
    cid = conn.get("trakt_client_id") or TRAKT_CLIENT_ID
    if not cid:
        return {"ok": False, "message": "No Trakt client id configured"}
    tok = await trakt_token(user.user_id, conn)
    try:
        async with httpx.AsyncClient(timeout=8) as hc:
            if tok:
                r = await hc.get(f"{TRAKT_API}/users/settings", headers=trakt_headers(cid, tok))
                if r.status_code == 200:
                    return {"ok": True, "message": f"Authorized as @{r.json().get('user', {}).get('username')}"}
                return {"ok": False, "message": f"Token rejected ({r.status_code}) — reconnect Trakt"}
            r = await hc.get(f"{TRAKT_API}/movies/trending?limit=1", headers=trakt_headers(cid))
        return {"ok": r.status_code == 200, "message": f"Trakt responded {r.status_code} (not authorized — click Connect)"}
    except Exception as e:
        return {"ok": False, "message": f"Unreachable: {e.__class__.__name__}"}


@api.post("/connections/test/simkl")
async def test_simkl(user: User = Depends(get_current_user)):
    conn = await db.connections.find_one({"user_id": user.user_id}, {"_id": 0}) or {}
    cid = conn.get("simkl_client_id") or SIMKL_CLIENT_ID
    if not cid:
        return {"ok": False, "message": "No Simkl client id configured"}
    tok = conn.get("simkl_access_token")
    try:
        async with httpx.AsyncClient(timeout=8) as hc:
            if tok:
                r = await hc.post(f"{SIMKL_API}/users/settings", params=simkl_params(cid), headers=simkl_headers(cid, tok))
                if r.status_code == 200:
                    return {"ok": True, "message": f"Authorized as {(r.json().get('user') or {}).get('name') or 'Simkl user'}"}
                return {"ok": False, "message": f"Token rejected ({r.status_code}) — reconnect Simkl"}
            r = await hc.get(f"{SIMKL_API}/movies/trending", params=simkl_params(cid), headers=simkl_headers(cid))
        return {"ok": r.status_code == 200, "message": f"Simkl responded {r.status_code} (not authorized — click Connect)"}
    except Exception as e:
        return {"ok": False, "message": f"Unreachable: {e.__class__.__name__}"}


@api.post("/connections/test/plex")
async def test_plex(user: User = Depends(get_current_user)):
    conn = await db.connections.find_one({"user_id": user.user_id}, {"_id": 0}) or {}
    url = conn.get("plex_url")
    tok = conn.get("plex_token")
    if not (url and tok):
        return {"ok": False, "message": "No Plex URL or token configured"}
    try:
        async with httpx.AsyncClient(timeout=8, verify=False) as hc:
            r = await hc.get(f"{url.rstrip('/')}/", headers={"X-Plex-Token": tok, "Accept": "application/json"})
        return {"ok": r.status_code == 200, "message": f"Plex responded {r.status_code}"}
    except Exception as e:
        return {"ok": False, "message": f"Unreachable: {e.__class__.__name__}"}


@api.post("/connections/test/mediamanager")
async def test_mediamanager(user: User = Depends(get_current_user)):
    conn = await db.connections.find_one({"user_id": user.user_id}, {"_id": 0}) or {}
    if not mediamanager_is_configured(conn):
        return {"ok": False, "message": "No MediaManager URL, email, or password configured"}
    try:
        async with httpx.AsyncClient(timeout=12) as hc:
            token = await mediamanager_login(
                hc,
                conn["mediamanager_url"],
                conn["mediamanager_email"],
                conn["mediamanager_password"],
            )
            health = await hc.get(
                f"{normalize_mediamanager_url(conn['mediamanager_url'])}/api/v1/health",
                headers={"Authorization": f"Bearer {token}"},
            )
        if health.status_code == 200:
            return {"ok": True, "message": f"Connected as {conn['mediamanager_email']}"}
        return {"ok": True, "message": f"Login succeeded (health {health.status_code})"}
    except HTTPException as e:
        return {"ok": False, "message": e.detail if isinstance(e.detail, str) else "MediaManager auth failed"}
    except Exception as e:
        return {"ok": False, "message": f"Unreachable: {e.__class__.__name__}"}


# ---------- Demo Data ----------
DEMO_HISTORY: List[Dict[str, Any]] = [
    {"title": "Blade Runner 2049", "year": 2017, "type": "movie", "genres": ["Sci-Fi", "Neo-Noir"], "rating": 8.0, "poster": "https://image.tmdb.org/t/p/w500/gajva2L0rPYkEWjzgFlBXCAVBE5.jpg", "source": "trakt"},
    {"title": "Dune: Part Two", "year": 2024, "type": "movie", "genres": ["Sci-Fi", "Adventure"], "rating": 8.5, "poster": "https://image.tmdb.org/t/p/w500/6izwz7rsy95ARzTR3poZ8H6c5pp.jpg", "source": "trakt"},
    {"title": "Severance", "year": 2022, "type": "show", "genres": ["Sci-Fi", "Thriller", "Drama"], "rating": 8.7, "poster": "https://image.tmdb.org/t/p/w500/pPHpeI2X1qEd1CS1SeyrdhZ4qnT.jpg", "source": "trakt"},
    {"title": "The Bear", "year": 2022, "type": "show", "genres": ["Drama", "Comedy"], "rating": 8.6, "poster": "https://image.tmdb.org/t/p/w500/eKfVzzEazSIjJMrw9ADa2x8ksLz.jpg", "source": "simkl"},
    {"title": "Everything Everywhere All at Once", "year": 2022, "type": "movie", "genres": ["Sci-Fi", "Action", "Comedy"], "rating": 8.0, "poster": "https://image.tmdb.org/t/p/w500/u68AjlvlutfEIcpmbYpKcdi09ut.jpg", "source": "plex"},
    {"title": "Arrival", "year": 2016, "type": "movie", "genres": ["Sci-Fi", "Drama"], "rating": 7.9, "poster": "https://image.tmdb.org/t/p/w500/pEzNVQfdzYDzVK0XqxERIw2x2se.jpg", "source": "plex"},
    {"title": "Mr. Robot", "year": 2015, "type": "show", "genres": ["Thriller", "Drama", "Crime"], "rating": 8.5, "poster": "https://image.tmdb.org/t/p/w500/kv1nRqgebSsREnd7vdC2pSGjpLo.jpg", "source": "trakt"},
    {"title": "Chernobyl", "year": 2019, "type": "show", "genres": ["Drama", "History"], "rating": 9.4, "poster": "https://image.tmdb.org/t/p/w500/hlLXt2tOPT6RRnjiUmoxyG1LTFi.jpg", "source": "simkl"},
    {"title": "Parasite", "year": 2019, "type": "movie", "genres": ["Thriller", "Drama"], "rating": 8.5, "poster": "https://image.tmdb.org/t/p/w500/7IiTTgloJzvGI1TAYymCfbfl3vT.jpg", "source": "plex"},
    {"title": "Fargo", "year": 2014, "type": "show", "genres": ["Crime", "Drama", "Dark Comedy"], "rating": 8.9, "poster": "https://image.tmdb.org/t/p/w500/a3VW6khsyUVKrG0GBCWFG3NzWPX.jpg", "source": "trakt"},
    {"title": "The Menu", "year": 2022, "type": "movie", "genres": ["Thriller", "Dark Comedy"], "rating": 7.2, "poster": "https://image.tmdb.org/t/p/w500/fPtUgMcLIboqlTlPrq0bQpKK8eq.jpg", "source": "simkl"},
    {"title": "Andor", "year": 2022, "type": "show", "genres": ["Sci-Fi", "Drama"], "rating": 8.4, "poster": "https://image.tmdb.org/t/p/w500/khZqmwHQicTYoS7Flreb9EddFZC.jpg", "source": "trakt"},
]

DEMO_RECS: List[Dict[str, Any]] = [
    {"title": "Foundation", "year": 2021, "type": "show", "genres": ["Sci-Fi", "Drama"], "poster": "https://image.tmdb.org/t/p/w500/tg9I5pOY4M9CKj8U0cxVBTsm5eh.jpg", "backdrop": "https://image.tmdb.org/t/p/w1280/7NNNXo0qG2SqH4JoG7GPvJ2hzes.jpg", "synopsis": "A complex saga of humans scattered on planets throughout the galaxy all living under the rule of the Galactic Empire.", "tmdb_rating": 7.5, "match_score": 96, "why": "Your love for cerebral sci-fi (Blade Runner 2049, Arrival) and long-arc political drama (Andor) makes Foundation a near-perfect match — dense worldbuilding, quiet menace, and slow-burn character work."},
    {"title": "Poor Things", "year": 2023, "type": "movie", "genres": ["Sci-Fi", "Dark Comedy", "Drama"], "poster": "https://image.tmdb.org/t/p/w500/kCGlIMHnOm8JPXq3rXM6c5wMxcT.jpg", "backdrop": "https://image.tmdb.org/t/p/w1280/zh6IdheEYinU4TPtorWsjx6qPQE.jpg", "synopsis": "A young woman brought back to life by an unorthodox scientist embarks on an odyssey of self-discovery.", "tmdb_rating": 8.0, "match_score": 93, "why": "Blends the surreal absurdist tone of Everything Everywhere with the meticulous production design and moral queasiness of The Menu — right in your wheelhouse."},
    {"title": "Dark", "year": 2017, "type": "show", "genres": ["Sci-Fi", "Mystery", "Thriller"], "poster": "https://image.tmdb.org/t/p/w500/apbrbWs8M9lyOpJYU5WXrpFbk1Z.jpg", "backdrop": "https://image.tmdb.org/t/p/w1280/3jDXL4Xvj3AzDOF6UH1xeyHW8MH.jpg", "synopsis": "A missing child causes four families to help solve the mystery that ties them in this German thriller.", "tmdb_rating": 8.7, "match_score": 92, "why": "Given how much time you spent with Severance and Mr. Robot, Dark's puzzle-box structure, cold color palette, and existential dread will land hard."},
    {"title": "The Zone of Interest", "year": 2023, "type": "movie", "genres": ["Drama", "History"], "poster": "https://image.tmdb.org/t/p/w500/hUu9zyZmDd8VZegKi1iK1Vk0RYS.jpg", "backdrop": "https://image.tmdb.org/t/p/w1280/pnTSOKcYnvdpQNQElAtJM1rWOxH.jpg", "synopsis": "The commandant of Auschwitz and his family strive to build a dream life next to the camp.", "tmdb_rating": 7.4, "match_score": 90, "why": "Chernobyl and Parasite showed you gravitate toward morally weighty, formally rigorous cinema — this is the same level of unsettling craft."},
    {"title": "Shogun", "year": 2024, "type": "show", "genres": ["Drama", "History", "Action"], "poster": "https://image.tmdb.org/t/p/w500/7O4iVfOMQmdCSxhOg1WnzG1AgYT.jpg", "backdrop": "https://image.tmdb.org/t/p/w1280/bwSmgmd90hCWwqOKQYTEraeOZhJ.jpg", "synopsis": "In Japan in the year 1600, at the dawn of a century-defining civil war, Lord Yoshii Toranaga is fighting for his life.", "tmdb_rating": 8.6, "match_score": 89, "why": "You loved the political scheming of Andor and the meticulous world of Dune — Shogun serves both with samurai-era gravitas."},
    {"title": "Past Lives", "year": 2023, "type": "movie", "genres": ["Drama", "Romance"], "poster": "https://image.tmdb.org/t/p/w500/k3waqVXSnvCZWfJYNtdamTgTtTA.jpg", "backdrop": "https://image.tmdb.org/t/p/w1280/7HR38hMBl23lf38MAN63y4pKsHz.jpg", "synopsis": "Two childhood friends are reunited in New York for one fateful week as they confront notions of destiny, love, and choices.", "tmdb_rating": 7.8, "match_score": 87, "why": "Arrival's aching meditation on time and choice + Bear's emotional restraint = you'll deeply feel this quiet A24 gem."},
    {"title": "The Diplomat", "year": 2023, "type": "show", "genres": ["Drama", "Thriller"], "poster": "https://image.tmdb.org/t/p/w500/cOKXV0FalCYixNmZYCfHXgyQ0VX.jpg", "backdrop": "https://image.tmdb.org/t/p/w1280/v6f9FUDDQfGIv8MLRQwlL0zvRjI.jpg", "synopsis": "A career diplomat lands in a high-profile job for which she is not suited.", "tmdb_rating": 7.7, "match_score": 84, "why": "Sharp dialogue and geopolitical tension in the spirit of Andor, with the wit and pacing that made Fargo binge-able."},
    {"title": "Oppenheimer", "year": 2023, "type": "movie", "genres": ["Drama", "History", "Thriller"], "poster": "https://image.tmdb.org/t/p/w500/8Gxv8gSFCU0XGDykEGv7zR1n2ua.jpg", "backdrop": "https://image.tmdb.org/t/p/w1280/neeNHeXjMF5fXoCJRsOmkNGC7q.jpg", "synopsis": "The story of American scientist J. Robert Oppenheimer and his role in the development of the atomic bomb.", "tmdb_rating": 8.1, "match_score": 82, "why": "Chernobyl-scale historical stakes with Blade Runner's brooding photography — engineered for your taste."},
]


# ---------- History ----------
@api.post("/history/sync")
async def sync_history(user: User = Depends(get_current_user)):
    """Fetch history from every connected source; falls back to demo when none is configured.

    Each provider is committed on its own. A provider that fails keeps the history it
    synced last time instead of having every row wiped by the next partial sync, and
    every attempt writes provider_sync_state so the job engine can tell "never synced"
    apart from "synced and genuinely empty".
    """
    from providers.sync_lock import record_sync_result

    conn = await db.connections.find_one({"user_id": user.user_id}, {"_id": 0}) or {}
    synced: Dict[str, List[Dict[str, Any]]] = {}
    errors: Dict[str, str] = {}

    # Trakt
    trakt_cid = conn.get("trakt_client_id") or TRAKT_CLIENT_ID
    trakt_tok = await trakt_token(user.user_id, conn) if trakt_cid else None
    if trakt_cid and trakt_tok:
        rows: List[Dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(timeout=12) as hc:
                r = await hc.get(
                    f"{TRAKT_API}/sync/history?limit=50&extended=full",
                    headers=trakt_headers(trakt_cid, trakt_tok),
                )
                if r.status_code == 200:
                    for entry in r.json():
                        m = entry.get("movie") or entry.get("show") or {}
                        rows.append({
                            "id": str(uuid.uuid4()),
                            "title": m.get("title", "Unknown"),
                            "year": m.get("year"),
                            "type": "movie" if entry.get("movie") else "show",
                            "genres": [g.title() for g in (m.get("genres") or [])],
                            "rating": round(float(m["rating"]), 1) if m.get("rating") else None,
                            "poster": None,
                            "tmdb_id": (m.get("ids") or {}).get("tmdb"),
                            "watched_at": entry.get("watched_at"),
                            "source": "trakt",
                        })
                    synced["trakt"] = rows
                else:
                    errors["trakt"] = f"Trakt history responded {r.status_code}"
        except Exception as e:
            errors["trakt"] = safe_provider_error(e)
        if "trakt" in errors:
            logging.warning("Trakt sync failed: %s", errors["trakt"])

    # Simkl
    simkl_cid = conn.get("simkl_client_id") or SIMKL_CLIENT_ID
    if simkl_cid and conn.get("simkl_access_token"):
        rows = []
        try:
            async with httpx.AsyncClient(timeout=20) as hc:
                r = await hc.get(
                    f"{SIMKL_API}/sync/all-items",
                    params={**simkl_params(simkl_cid), "extended": "full"},
                    headers=simkl_headers(simkl_cid, conn["simkl_access_token"]),
                )
                if r.status_code == 200:
                    data = r.json() or {}
                    for kind, key in (("movie", "movies"), ("show", "shows"), ("show", "anime")):
                        for entry in (data.get(key) or [])[:50]:
                            m = entry.get(kind) or entry.get("show") or {}
                            rows.append({
                                "id": str(uuid.uuid4()),
                                "title": m.get("title", "Unknown"),
                                "year": m.get("year"),
                                "type": kind,
                                "genres": [g.title() for g in (m.get("genres") or [])],
                                "rating": None,
                                "poster": f"https://simkl.in/posters/{m['poster']}_m.jpg" if m.get("poster") else None,
                                "tmdb_id": int((m.get("ids") or {}).get("tmdb")) if str((m.get("ids") or {}).get("tmdb", "")).isdigit() else None,
                                "watched_at": entry.get("last_watched_at"),
                                "source": "simkl",
                            })
                    synced["simkl"] = rows
                else:
                    errors["simkl"] = f"Simkl all-items responded {r.status_code}"
        except Exception as e:
            errors["simkl"] = safe_provider_error(e)
        if "simkl" in errors:
            logging.warning("Simkl sync failed: %s", errors["simkl"])

    # Plex
    if conn.get("plex_url") and conn.get("plex_token"):
        rows = []
        try:
            async with httpx.AsyncClient(timeout=12, verify=False) as hc:
                r = await hc.get(
                    f"{conn['plex_url'].rstrip('/')}/library/all",
                    headers={"X-Plex-Token": conn["plex_token"], "Accept": "application/json"},
                )
                if r.status_code == 200:
                    data = r.json().get("MediaContainer", {}).get("Metadata", [])[:50]
                    for m in data:
                        rows.append({
                            "id": str(uuid.uuid4()),
                            "title": m.get("title", "Unknown"),
                            "year": m.get("year"),
                            "type": "movie" if m.get("type") == "movie" else "show",
                            "genres": [g.get("tag") for g in m.get("Genre", [])],
                            "rating": round(float(m["audienceRating"]), 1) if m.get("audienceRating") else None,
                            "poster": None,
                            "watched_at": datetime.fromtimestamp(m["lastViewedAt"], tz=timezone.utc).isoformat() if m.get("lastViewedAt") else None,
                            "source": "plex",
                        })
                    synced["plex"] = rows
                else:
                    errors["plex"] = f"Plex library responded {r.status_code}"
        except Exception as e:
            errors["plex"] = safe_provider_error(e)
        if "plex" in errors:
            logging.warning("Plex sync failed: %s", errors["plex"])

    # AniList — previously only reachable through a hand-pasted /anilist/import payload,
    # which left every connected account looking permanently unsynced to the job engine.
    if conn.get("anilist_access_token"):
        try:
            rows = [
                {**item, "id": str(uuid.uuid4())}
                for item in await anilist_fetch_media_list(
                    conn["anilist_access_token"], conn.get("anilist_username")
                )
            ]
            synced["anilist"] = rows
        except Exception as e:
            errors["anilist"] = safe_provider_error(e)
            logging.warning("AniList sync failed: %s", errors["anilist"])

    for provider, rows in synced.items():
        await record_sync_result(provider, user.user_id, items_synced=len(rows))
    for provider, message in errors.items():
        await record_sync_result(provider, user.user_id, error=message)

    items = [row for rows in synced.values() for row in rows]
    used_demo = False
    if not synced and not errors:
        # Nothing is connected at all — seed the demo shelf so the app is explorable.
        items = [{**h, "id": str(uuid.uuid4()), "watched_at": (datetime.now(timezone.utc) - timedelta(days=i*3)).isoformat()} for i, h in enumerate(DEMO_HISTORY)]
        used_demo = True
        await db.history.delete_many({"user_id": user.user_id})
        await db.history.insert_many([{**it, "user_id": user.user_id} for it in items])
        return {"count": len(items), "demo": True, "sources": {}, "errors": {}}

    if items:
        await enrich_history_posters(items)
    # Replace only the sources that actually came back, so a failing provider does not
    # take the other providers' history down with it.
    for provider, rows in synced.items():
        await db.history.delete_many({"user_id": user.user_id, "source": provider})
        if rows:
            await db.history.insert_many([{**it, "user_id": user.user_id} for it in rows])

    return {
        "count": len(items),
        "demo": used_demo,
        "sources": {provider: len(rows) for provider, rows in synced.items()},
        "errors": errors,
    }


@api.get("/history")
async def get_history(user: User = Depends(get_current_user)):
    docs = await db.history.find({"user_id": user.user_id}, {"_id": 0, "user_id": 0}).to_list(500)
    if not docs:
        # auto-seed demo on first load
        seeded = [{**h, "id": str(uuid.uuid4()), "watched_at": (datetime.now(timezone.utc) - timedelta(days=i*3)).isoformat(), "user_id": user.user_id} for i, h in enumerate(DEMO_HISTORY)]
        await db.history.insert_many(seeded)
        docs = [{k: v for k, v in d.items() if k not in ("user_id", "_id")} for d in seeded]
    # A row seeded with a poster still needs its tmdb_id: the trailer lookup and
    # the watchlist push both key off it, and selecting on a missing poster alone
    # meant demo history - which ships with posters - never got one.
    missing = [d for d in docs if (not d.get("poster") or not d.get("tmdb_id")) and not d.get("poster_checked")][:24]
    if missing:
        await enrich_history_posters(missing)
        for d in missing:
            await db.history.update_one(
                {"user_id": user.user_id, "id": d["id"]},
                {"$set": {"poster": d.get("poster"), "poster_checked": True, **({"tmdb_id": d["tmdb_id"]} if d.get("tmdb_id") else {})}},
            )
    return docs


@api.get("/history/stats")
async def history_stats(user: User = Depends(get_current_user)):
    docs = await db.history.find({"user_id": user.user_id}, {"_id": 0, "user_id": 0}).to_list(1000)
    if not docs:
        return {"total": 0, "movies": 0, "shows": 0, "genres": [], "sources": [], "avg_rating": 0}
    movies = sum(1 for d in docs if d.get("type") == "movie")
    shows = len(docs) - movies
    genre_counts: Dict[str, int] = {}
    for d in docs:
        for g in d.get("genres", []) or []:
            genre_counts[g] = genre_counts.get(g, 0) + 1
    genres = sorted([{"name": k, "count": v} for k, v in genre_counts.items()], key=lambda x: -x["count"])[:8]
    source_counts: Dict[str, int] = {}
    for d in docs:
        source_counts[d.get("source", "unknown")] = source_counts.get(d.get("source", "unknown"), 0) + 1
    sources = [{"name": k, "count": v} for k, v in source_counts.items()]
    ratings = [d["rating"] for d in docs if d.get("rating")]
    return {
        "total": len(docs),
        "movies": movies,
        "shows": shows,
        "genres": genres,
        "sources": sources,
        "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else 0,
        "hours_estimate": movies * 2 + shows * 10,
    }


# ---------- LLM helpers ----------
async def record_usage(user_id: str, model_key: str, action: str, prompt_chars: int, output_chars: int) -> None:
    await db.llm_usage.insert_one({
        "user_id": user_id,
        "model": model_key,
        "action": action,
        "est_tokens": (prompt_chars + output_chars) // 4,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


# ---------- TMDB enrichment ----------
async def tmdb_lookup(hc: httpx.AsyncClient, title: str, year: Optional[int], type_: str) -> Optional[Dict[str, Any]]:
    if not TMDB_KEY:
        return None
    kind = str(type_ or "movie").casefold()
    endpoint = "tv" if kind in {"show", "tv", "series", "anime"} else "movie"
    year_param = "first_air_date_year" if endpoint == "tv" else "year"
    clean = re.sub(r"\s*[\(\[:–-]\s*(season|part|vol\.?|volume)\b.*$", "", title, flags=re.I)
    clean = re.sub(r"\s*\(.*?\)\s*$", "", clean).strip() or title
    attempts = [{"query": title, year_param: year} if year else None, {"query": title}]
    if clean != title:
        attempts.append({"query": clean})
    for params in attempts:
        if params is None:
            continue
        try:
            r = await hc.get(f"https://api.themoviedb.org/3/search/{endpoint}", params={"api_key": TMDB_KEY, **params})
            results = r.json().get("results") or [] if r.status_code == 200 else []
        except Exception as e:
            logging.warning(f"TMDB lookup failed for {title}: {e}")
            return None
        if results:
            top = results[0]
            out: Dict[str, Any] = {"tmdb_id": top.get("id")}
            if top.get("poster_path"):
                out["poster"] = f"https://image.tmdb.org/t/p/w500{top['poster_path']}"
            if top.get("backdrop_path"):
                out["backdrop"] = f"https://image.tmdb.org/t/p/w1280{top['backdrop_path']}"
            if top.get("vote_average"):
                out["tmdb_rating"] = round(float(top["vote_average"]), 1)
            return out
    return None


async def tmdb_details(hc: httpx.AsyncClient, tmdb_id: int, type_: str) -> Optional[Dict[str, Any]]:
    endpoint = "tv" if type_ == "show" else "movie"
    try:
        r = await hc.get(f"https://api.themoviedb.org/3/{endpoint}/{tmdb_id}", params={"api_key": TMDB_KEY})
        if r.status_code != 200:
            return None
        d = r.json()
        out: Dict[str, Any] = {"tmdb_id": tmdb_id}
        if d.get("poster_path"):
            out["poster"] = f"https://image.tmdb.org/t/p/w500{d['poster_path']}"
        if d.get("backdrop_path"):
            out["backdrop"] = f"https://image.tmdb.org/t/p/w1280{d['backdrop_path']}"
        return out
    except Exception as e:
        logging.warning(f"TMDB details failed for {tmdb_id}: {e}")
        return None


async def enrich_history_posters(items: List[Dict[str, Any]]) -> None:
    """Fill missing posters on history items via TMDB (by id when known, else title search)."""
    if not TMDB_KEY:
        return
    targets = [it for it in items if not it.get("poster") or not it.get("tmdb_id")]
    if not targets:
        return
    sem = asyncio.Semaphore(6)

    async def one(hc: httpx.AsyncClient, it: Dict[str, Any]):
        async with sem:
            meta = await tmdb_details(hc, it["tmdb_id"], it.get("type", "movie")) if it.get("tmdb_id") else None
            if not (meta and meta.get("poster")):
                meta = await tmdb_lookup(hc, it["title"], it.get("year"), it.get("type", "movie"))
            if meta:
                if meta.get("poster"):
                    it["poster"] = meta["poster"]
                if meta.get("tmdb_id"):
                    it["tmdb_id"] = meta["tmdb_id"]

    async with httpx.AsyncClient(timeout=10) as hc:
        await asyncio.gather(*[one(hc, it) for it in targets])


async def enrich_with_tmdb(recs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fill poster/backdrop/rating from TMDB for recs still using the placeholder poster."""
    if not TMDB_KEY:
        return recs
    targets = [r for r in recs if not r.get("poster") or r["poster"] == PLACEHOLDER_POSTER]
    if not targets:
        return recs
    async with httpx.AsyncClient(timeout=10) as hc:
        results = await asyncio.gather(*[tmdb_lookup(hc, r["title"], r.get("year"), r.get("type", "movie")) for r in targets])
    for r, meta in zip(targets, results):
        if meta:
            r.update({k: v for k, v in meta.items() if v})
    return recs


async def backfill_posters(user_id: str, docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    missing = [d for d in docs if d.get("poster") == PLACEHOLDER_POSTER]
    if not missing:
        return docs
    await enrich_with_tmdb(missing)
    for d in missing:
        if d.get("poster") != PLACEHOLDER_POSTER:
            await db.recommendations.update_one(
                {"user_id": user_id, "id": d["id"]},
                {"$set": {k: d[k] for k in ("poster", "backdrop", "tmdb_rating", "tmdb_id") if d.get(k)}},
            )
    return docs


async def ensure_history(user_id: str) -> None:
    """Seed demo history if the user has none yet, so LLM prompts always have real titles."""
    count = await db.history.count_documents({"user_id": user_id})
    if count == 0:
        seeded = [
            {**h, "id": str(uuid.uuid4()),
             "watched_at": (datetime.now(timezone.utc) - timedelta(days=i * 3)).isoformat(),
             "user_id": user_id}
            for i, h in enumerate(DEMO_HISTORY)
        ]
        await db.history.insert_many(seeded)


# ---------- Taste Profile ----------
@api.post("/taste-profile/generate")
async def generate_taste(payload: Optional[GenerateBody] = None, user: User = Depends(get_current_user)):
    conn = await db.connections.find_one({"user_id": user.user_id}, {"_id": 0}) or {}
    await ensure_history(user.user_id)
    docs = await db.history.find({"user_id": user.user_id}, {"_id": 0, "user_id": 0}).to_list(200)
    titles = ", ".join([f"{d['title']} ({d.get('year','')})" for d in docs[:40]])
    genres: Dict[str, int] = {}
    for d in docs:
        for g in d.get("genres", []) or []:
            genres[g] = genres.get(g, 0) + 1
    top_genres = sorted(genres.items(), key=lambda x: -x[1])[:5]

    system = "You are a discerning film critic writing a warm, insightful taste profile. Respond ONLY with a JSON object with keys: cinematic_dna (2-3 sentence summary string), key_tropes (list of 5 short strings), mood (one of: cerebral, cathartic, playful, dark, dreamy, adrenaline), narrative_complexity (integer 1-10). No prose outside JSON."
    prompt = f"Watch history: {titles}\nTop genres: {[g for g,_ in top_genres]}\nWrite my cinematic DNA as JSON."
    parsed, provider, model_key = await generate_with_llm(conn, f"taste-{user.user_id}", system, prompt, user.user_id, "taste_profile", payload.model if payload else None)

    if isinstance(parsed, dict) and parsed.get("cinematic_dna"):
        profile = parsed
        profile["top_genres"] = [g for g, _ in top_genres]
        profile["provider"] = provider
        profile["model"] = model_key
        profile["used_demo"] = False
    else:
        profile = {
            "cinematic_dna": "You gravitate toward cerebral, atmospheric storytelling — worlds that trust the audience, characters carrying quiet weight, and craft that lingers. Genre is a costume; theme is the meal.",
            "key_tropes": ["Slow-burn dread", "Puzzle-box structure", "Morally weighty craft", "Rich production design", "Melancholic sci-fi"],
            "mood": "cerebral",
            "narrative_complexity": 8,
            "top_genres": [g for g, _ in top_genres] or ["Sci-Fi", "Drama", "Thriller"],
            "provider": "demo",
            "used_demo": True,
        }

    await db.taste_profiles.update_one(
        {"user_id": user.user_id},
        {"$set": {"user_id": user.user_id, "profile": profile, "updated_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )
    return profile


@api.get("/taste-profile")
async def get_taste(user: User = Depends(get_current_user)):
    doc = await db.taste_profiles.find_one({"user_id": user.user_id}, {"_id": 0})
    return doc.get("profile") if doc else None


# ---------- Recommendations ----------
@api.post("/recommendations/generate")
async def generate_recs(payload: Optional[GenerateBody] = None, user: User = Depends(get_current_user)):
    from jobs.engine import execute_job
    from recommendation.pipeline import default_job

    history_count = await db.history.count_documents({"user_id": user.user_id})
    if not history_count and not await db.media_history.count_documents({"user_id": user.user_id}):
        raise HTTPException(status_code=409, detail="Sync viewing history before generating personal picks.")

    job = default_job()
    job.update({
        "id": f"content_to_watch:{user.user_id}",
        "name": "Content to Watch",
        "job_type": "discover",
        "media_types": ["movie", "tv", "anime"],
        "taste_sources": None,
        "candidate_sources": [
            "tmdb_discover", "tmdb_similar", "tmdb_recommendations",
            "trakt", "simkl", "anilist",
        ],
        "candidate_limit": 120,
        "final_recommendation_limit": 8,
        "ai_enabled": True,
        "action_mode": "recommendations_only",
        # The home feed keeps the measured single-floor diversity; lane balancing
        # is for saved jobs, which name the categories they want.
        "lane_balance": False,
    })
    job["exclusions"] = {
        **job["exclusions"],
        "already_watched": True,
        "already_recommended": True,
        "recommend_again_after_days": 90,
    }
    result = await execute_job(
        user.user_id, job, "manual", catalog=[],
        model_override=payload.model if payload else None,
    )
    accepted = result.get("accepted") or []
    if not accepted:
        detail = (result.get("detail") or "No high-confidence unseen titles were available from connected catalogs.")
        raise HTTPException(status_code=503, detail=detail)
    run = result.get("run") or {}
    return {
        "count": len(accepted),
        "provider": run.get("provider") or "pipeline",
        "model": run.get("model") or "deterministic",
        "demo": False,
        "warnings": result.get("warnings") or [],
    }


def by_rank(docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Best pick first.

    Rows are written in rank order, so sorting them by created_at descending
    handed the UI the list upside down - the lowest-scoring title became the
    hero card. Rows written before `rank` existed fall back to newest-first.
    """
    return sorted(
        docs,
        key=lambda row: (
            row.get("rank") if isinstance(row.get("rank"), int) else 10**6,
            str(row.get("created_at") or ""),
        ),
    )


@api.get("/recommendations")
async def list_recs(user: User = Depends(get_current_user)):
    visible = {"user_id": user.user_id, "dismissed": {"$ne": True}}
    main_job = f"content_to_watch:{user.user_id}"
    docs = await db.recommendations.find(
        {**visible, "job_id": main_job},
        {"_id": 0, "user_id": 0},
    ).sort("created_at", -1).to_list(50)
    if not docs:
        docs = await db.recommendations.find(
            visible, {"_id": 0, "user_id": 0},
        ).sort("created_at", -1).to_list(50)
    return await backfill_posters(user.user_id, by_rank(docs))


@api.post("/recommendations/{rec_id}/save")
async def save_rec(rec_id: str, user: User = Depends(get_current_user)):
    r = await db.recommendations.update_one(
        {"user_id": user.user_id, "id": rec_id},
        {"$set": {"saved": True, "saved_at": datetime.now(timezone.utc).isoformat()}},
    )
    if r.matched_count == 0:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    return {"ok": True}


@api.post("/recommendations/{rec_id}/unsave")
async def unsave_rec(rec_id: str, user: User = Depends(get_current_user)):
    await db.recommendations.update_one(
        {"user_id": user.user_id, "id": rec_id},
        {"$set": {"saved": False}, "$unset": {"saved_at": ""}},
    )
    return {"ok": True}


@api.post("/recommendations/{rec_id}/dismiss")
async def dismiss_rec(rec_id: str, user: User = Depends(get_current_user)):
    rec = await db.recommendations.find_one({"user_id": user.user_id, "id": rec_id}, {"_id": 0})
    await db.recommendations.update_one(
        {"user_id": user.user_id, "id": rec_id},
        {"$set": {"dismissed": True, "needs_approval": False}},
    )
    request_filter = {
        "user_id": user.user_id,
        "status": {"$in": ["pending_approval", "pending", "requested"]},
    }
    if rec and rec.get("request_id"):
        await db.requests.update_one(
            {**request_filter, "id": rec["request_id"]},
            {"$set": {"status": "rejected", "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
    else:
        await db.requests.update_one(
            {**request_filter, "recommendation_id": rec_id},
            {"$set": {"status": "rejected", "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
    return {"ok": True}


@api.post("/recommendations/{rec_id}/watchlist")
async def add_to_trakt_watchlist(rec_id: str, user: User = Depends(get_current_user)):
    rec = await db.recommendations.find_one({"user_id": user.user_id, "id": rec_id}, {"_id": 0})
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    conn = await db.connections.find_one({"user_id": user.user_id}, {"_id": 0}) or {}
    cid = conn.get("trakt_client_id") or TRAKT_CLIENT_ID
    tok = await trakt_token(user.user_id, conn) if cid else None
    if not tok:
        raise HTTPException(status_code=409, detail="Connect Trakt first")
    tmdb_id = rec.get("tmdb_id")
    async with httpx.AsyncClient(timeout=12) as hc:
        if not tmdb_id and TMDB_KEY:
            meta = await tmdb_lookup(hc, rec["title"], rec.get("year"), rec.get("type", "movie"))
            tmdb_id = meta.get("tmdb_id") if meta else None
        if not tmdb_id:
            raise HTTPException(status_code=422, detail="Could not identify this title on TMDB")
        bucket = "shows" if rec.get("type") == "show" else "movies"
        r = await hc.post(f"{TRAKT_API}/sync/watchlist", json={bucket: [{"ids": {"tmdb": int(tmdb_id)}}]}, headers=trakt_headers(cid, tok))
    if r.status_code not in (200, 201):
        logging.warning(f"Trakt watchlist responded {r.status_code}: {r.text[:200]}")
        raise HTTPException(status_code=502, detail=f"Trakt responded {r.status_code}")
    body = r.json()
    kind = bucket
    added = (body.get("added") or {}).get(kind, 0)
    existing = (body.get("existing") or {}).get(kind, 0)
    not_found = (body.get("not_found") or {}).get(kind) or []
    if not added and not existing:
        raise HTTPException(status_code=422, detail="Trakt could not match this title" if not_found else "Trakt did not add the title")
    await db.recommendations.update_one(
        {"user_id": user.user_id, "id": rec_id},
        {"$set": {"in_watchlist": True, "tmdb_id": int(tmdb_id), "watchlisted_at": datetime.now(timezone.utc).isoformat()}},
    )
    return {"ok": True, "added": bool(added), "existing": bool(existing)}


async def send_title_to_mediamanager(
    item: Dict[str, Any],
    conn: Dict[str, Any],
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Login to MediaManager and add the title to the movie or TV library."""
    from providers.keys import resolve_tmdb_api_key

    return await send_item_to_library(
        item,
        conn,
        tmdb_api_key=resolve_tmdb_api_key(conn) or TMDB_KEY,
        options=options,
    )


async def library_item_from_request(user_id: str, doc: Dict[str, Any]) -> Dict[str, Any]:
    """Fill missing TMDb / type fields from the linked recommendation so add is a single MM call."""
    item = dict(doc)
    rec_id = doc.get("recommendation_id")
    if not rec_id:
        return item
    rec = await db.recommendations.find_one({"user_id": user_id, "id": rec_id}, {"_id": 0}) or {}
    for key in ("tmdb_id", "type", "year", "title", "poster", "match_score"):
        if item.get(key) in (None, "") and rec.get(key) not in (None, ""):
            item[key] = rec[key]
    return item


def clamp_match_score(value: Any) -> Optional[int]:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(0, min(100, n))


async def attach_request_match_scores(user_id: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fill taste match % from the request, linked recommendation, title match, or taste rank."""
    for row in rows:
        score = clamp_match_score(row.get("match_score"))
        if score is not None:
            row["match_score"] = score

    missing = [row for row in rows if row.get("match_score") is None]
    if not missing:
        return rows

    rec_ids = [row["recommendation_id"] for row in missing if row.get("recommendation_id")]
    titles = [row.get("title") for row in missing if row.get("title")]
    query: Dict[str, Any] = {"user_id": user_id}
    clauses = []
    if rec_ids:
        clauses.append({"id": {"$in": rec_ids}})
    if titles:
        clauses.append({"title": {"$in": titles}})
    recs: List[Dict[str, Any]] = []
    if clauses:
        query["$or"] = clauses
        recs = await db.recommendations.find(
            query,
            {"_id": 0, "id": 1, "title": 1, "year": 1, "match_score": 1, "genres": 1},
        ).to_list(500)

    by_id = {row["id"]: clamp_match_score(row.get("match_score")) for row in recs}
    by_title: Dict[tuple, int] = {}
    genres_by_title: Dict[tuple, List[str]] = {}
    for rec in recs:
        score = clamp_match_score(rec.get("match_score"))
        key = (str(rec.get("title") or "").casefold(), rec.get("year"))
        loose = (str(rec.get("title") or "").casefold(), None)
        if rec.get("genres"):
            genres_by_title[key] = rec["genres"]
            genres_by_title[loose] = rec["genres"]
        if score is None:
            continue
        by_title[key] = score
        by_title[loose] = score

    still = []
    for row in rows:
        if row.get("match_score") is not None:
            continue
        title_key = (str(row.get("title") or "").casefold(), row.get("year"))
        loose = (str(row.get("title") or "").casefold(), None)
        if not row.get("genres"):
            row["genres"] = genres_by_title.get(title_key) or genres_by_title.get(loose) or []
        score = by_id.get(row.get("recommendation_id")) or by_title.get(title_key) or by_title.get(loose)
        if score is not None:
            row["match_score"] = score
        else:
            still.append(row)

    if still:
        taste = await db.taste_profiles.find_one({"user_id": user_id}, {"_id": 0}) or {}
        from recommendation.ranking_engine import score_candidates
        ranked = score_candidates(still, taste)
        lookup = {
            (str(item.get("title") or "").casefold(), item.get("year")): clamp_match_score(item.get("match_score"))
            for item in ranked
        }
        for row in still:
            score = lookup.get((str(row.get("title") or "").casefold(), row.get("year")))
            if score is not None:
                row["match_score"] = score
    return rows


@api.post("/recommendations/{rec_id}/approve")
async def approve_to_mediamanager(
    rec_id: str,
    user: User = Depends(get_current_user),
    body: Optional[AddOptionsBody] = None,
):
    """Approve a recommendation into MediaManager's movie or TV library."""
    rec = await db.recommendations.find_one({"user_id": user.user_id, "id": rec_id}, {"_id": 0})
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    conn = await db.connections.find_one({"user_id": user.user_id}, {"_id": 0}) or {}
    result = await send_title_to_mediamanager(rec, conn, body.model_dump(exclude_none=True) if body else None)
    now = datetime.now(timezone.utc).isoformat()
    await db.recommendations.update_one(
        {"user_id": user.user_id, "id": rec_id},
        {
            "$set": {
                "in_library": True,
                "tmdb_id": result["tmdb_id"],
                "approved_at": now,
                "needs_approval": False,
            }
        },
    )
    request_filter = {"user_id": user.user_id, "status": {"$in": ["pending_approval", "pending", "requested"]}}
    if rec.get("request_id"):
        await db.requests.update_one(
            {**request_filter, "id": rec["request_id"]},
            {
                "$set": {
                    "status": "approved",
                    "provider": "mediamanager",
                    "tmdb_id": result["tmdb_id"],
                    "approved_at": now,
                    "updated_at": now,
                }
            },
        )
    else:
        await db.requests.update_one(
            {**request_filter, "recommendation_id": rec_id},
            {
                "$set": {
                    "status": "approved",
                    "provider": "mediamanager",
                    "tmdb_id": result["tmdb_id"],
                    "approved_at": now,
                    "updated_at": now,
                }
            },
        )
    return result


PENDING_STATUSES = ["pending_approval", "pending", "requested"]
# One queue page. Matches the tally in /requests/stats so the header count and
# the list on screen cannot disagree.
REQUEST_LIST_CAP = 5000


@api.get("/requests")
async def list_requests(user: User = Depends(get_current_user)):
    """Newest first, pending ahead of everything else.

    The sort used to run in Python *after* .to_list(500). Mongo returns rows in
    natural order, so that cap took an arbitrary 500 of the collection and the
    reorder only shuffled those. Titles a job had just written sat past the cap
    and never reached the queue, however often the tab polled for them.
    """
    base = {"user_id": user.user_id}
    pending_rows = await db.requests.find(
        {**base, "status": {"$in": PENDING_STATUSES}},
        {"_id": 0, "user_id": 0},
    ).sort("updated_at", -1).to_list(REQUEST_LIST_CAP)
    other_rows = await db.requests.find(
        {**base, "status": {"$nin": [*PENDING_STATUSES, "rejected"]}},
        {"_id": 0, "user_id": 0},
    ).sort("updated_at", -1).to_list(REQUEST_LIST_CAP)
    return await attach_request_match_scores(user.user_id, pending_rows + other_rows)


@api.get("/build")
async def build_version():
    """Which frontend build this server is serving.

    Safari in particular holds on to the app shell hard, so a deployed change
    could sit on disk for hours while the open tab kept running the previous
    bundle. The page compares this to the script it actually loaded and reloads
    itself when they differ, instead of waiting for someone to know the right
    cache-bypass shortcut.
    """
    bundle = ""
    js_dir = _BUILD_DIR_FOR_VERSION / "static" / "js"
    if js_dir.is_dir():
        names = sorted(item.name for item in js_dir.glob("main.*.js"))
        bundle = names[-1] if names else ""
    return {"bundle": bundle}


@api.get("/requests/version")
async def requests_version(user: User = Depends(get_current_user)):
    """Cheap change token so an open queue can poll often without refetching 1 MB.

    The tab compares this to what it holds and only pulls the full list when a
    job has actually written something.
    """
    query = {"user_id": user.user_id, "status": {"$nin": ["rejected"]}}
    latest = await db.requests.find(query, {"_id": 0, "updated_at": 1}).sort(
        "updated_at", -1
    ).limit(1).to_list(1)
    return {
        "count": await db.requests.count_documents(query),
        "latest": (latest[0].get("updated_at") if latest else None),
    }


@api.get("/requests/stats")
async def request_stats(user: User = Depends(get_current_user)):
    """Tally for the Requests header.

    GET /requests hides rejected rows, so the queue cannot count them itself.
    """
    rows = await db.requests.find(
        {"user_id": user.user_id}, {"_id": 0, "status": 1}
    ).to_list(5000)
    pending = {"pending_approval", "pending", "requested"}
    approved = {"approved", "available", "completed"}
    tally = {"total": len(rows), "pending": 0, "approved": 0, "rejected": 0, "failed": 0}
    for row in rows:
        status = row.get("status")
        if status in pending:
            tally["pending"] += 1
        elif status in approved:
            tally["approved"] += 1
        elif status == "rejected":
            tally["rejected"] += 1
        else:
            tally["failed"] += 1
    tally["blacklisted"] = await db.blacklist.count_documents({"user_id": user.user_id})
    return tally


@api.post("/requests/bulk")
async def bulk_requests(payload: BulkRequestsBody, user: User = Depends(get_current_user)):
    ids = [str(item).strip() for item in (payload.ids or []) if str(item).strip()][:50]
    if not ids:
        raise HTTPException(status_code=422, detail="Select at least one title")
    docs = await db.requests.find({"user_id": user.user_id, "id": {"$in": ids}}, {"_id": 0}).to_list(50)
    if payload.action == "reject":
        now = datetime.now(timezone.utc).isoformat()
        rec_ids = [doc.get("recommendation_id") for doc in docs if doc.get("recommendation_id")]
        await db.requests.update_many(
            {"user_id": user.user_id, "id": {"$in": [doc["id"] for doc in docs]}},
            {"$set": {"status": "rejected", "updated_at": now}},
        )
        if rec_ids:
            await db.recommendations.update_many(
                {"user_id": user.user_id, "id": {"$in": rec_ids}},
                {"$set": {"dismissed": True, "needs_approval": False}},
            )
        return {"ok": True, "action": "reject", "ids": [doc["id"] for doc in docs]}

    pending = {"pending_approval", "pending", "requested"}
    targets = [doc for doc in docs if doc.get("status") in pending]
    results = []
    for doc in targets:
        try:
            result = await approve_request(doc["id"], user, payload.options)
            results.append({"id": doc["id"], "ok": True, **{k: result.get(k) for k in ("created", "already_existed", "tmdb_id", "media_type") if k in result}})
        except HTTPException as exc:
            results.append({"id": doc["id"], "ok": False, "error": str(exc.detail)})
    return {"ok": True, "action": "approve", "results": results}


async def _load_request(user_id: str, request_id: str) -> Optional[Dict[str, Any]]:
    doc = await db.requests.find_one({"user_id": user_id, "id": request_id}, {"_id": 0})
    if doc:
        return doc
    doc = await db.requests.find_one({"user_id": user_id, "recommendation_id": request_id}, {"_id": 0})
    if doc:
        return doc
    rec = await db.recommendations.find_one(
        {"user_id": user_id, "$or": [{"id": request_id}, {"request_id": request_id}]},
        {"_id": 0},
    )
    if rec:
        linked = None
        if rec.get("request_id"):
            linked = await db.requests.find_one({"user_id": user_id, "id": rec["request_id"]}, {"_id": 0})
        if not linked:
            linked = await db.requests.find_one({"user_id": user_id, "recommendation_id": rec["id"]}, {"_id": 0})
        if linked:
            return linked
        return {"_from_recommendation": rec}
    return None


@api.post("/requests/{request_id}/approve")
async def approve_request(
    request_id: str,
    user: User = Depends(get_current_user),
    body: Optional[AddOptionsBody] = None,
):
    loaded = await _load_request(user.user_id, request_id)
    if not loaded:
        raise HTTPException(status_code=404, detail="Request not found")
    if loaded.get("_from_recommendation"):
        rec = loaded["_from_recommendation"]
        if body:
            return await approve_to_mediamanager(rec["id"], user, body)
        return await approve_to_mediamanager(rec["id"], user)
    doc = loaded
    if doc.get("status") not in {
        "pending_approval",
        "pending",
        "requested",
        "approved",
        "request_failed",
        "failed",
    }:
        raise HTTPException(status_code=422, detail="This request cannot be sent to MediaManager")
    conn = await db.connections.find_one({"user_id": user.user_id}, {"_id": 0}) or {}
    # Approving is the user's decision and always sticks: the title leaves the
    # queue for the Approved tab even when MediaManager cannot take it right
    # now. The delivery outcome is recorded so the row can be retried.
    delivery_error: Optional[str] = None
    try:
        result = await send_title_to_mediamanager(
            await library_item_from_request(user.user_id, doc),
            conn,
            body.model_dump(exclude_none=True) if body else None,
        )
    except HTTPException as exc:
        result = {"ok": False, "created": False, "already_existed": False,
                  "tmdb_id": doc.get("tmdb_id"), "media_type": doc.get("type") or "movie"}
        delivery_error = str(exc.detail)
    now = datetime.now(timezone.utc).isoformat()
    approved_fields = {
        "status": "approved",
        "provider": "mediamanager" if not delivery_error else (doc.get("provider") or "local"),
        "approved_at": now,
        "updated_at": now,
        "delivery_status": "not_delivered" if delivery_error else "delivered",
    }
    if result.get("tmdb_id"):
        approved_fields["tmdb_id"] = result["tmdb_id"]
    if delivery_error:
        approved_fields["delivery_error"] = delivery_error
    await db.requests.update_one(
        {"user_id": user.user_id, "id": doc["id"]},
        {"$set": approved_fields, **({} if delivery_error else {"$unset": {"delivery_error": ""}})},
    )
    rec_id = doc.get("recommendation_id")
    if rec_id:
        await db.recommendations.update_one(
            {"user_id": user.user_id, "id": rec_id},
            {
                "$set": {
                    "in_library": True,
                    "tmdb_id": result["tmdb_id"],
                    "approved_at": now,
                    "needs_approval": False,
                }
            },
        )
    updated = await db.requests.find_one({"user_id": user.user_id, "id": doc["id"]}, {"_id": 0, "user_id": 0})
    payload = {**result, "status": "approved", "request": updated}
    if delivery_error:
        payload["delivery_error"] = delivery_error
    return payload


@api.post("/requests/{request_id}/reject")
async def reject_request(request_id: str, user: User = Depends(get_current_user)):
    doc = await db.requests.find_one({"user_id": user.user_id, "id": request_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Request not found")
    now = datetime.now(timezone.utc).isoformat()
    await db.requests.update_one(
        {"user_id": user.user_id, "id": request_id},
        {"$set": {"status": "rejected", "updated_at": now}},
    )
    rec_id = doc.get("recommendation_id")
    if rec_id:
        await db.recommendations.update_one(
            {"user_id": user.user_id, "id": rec_id},
            {"$set": {"dismissed": True, "needs_approval": False}},
        )
    return {"ok": True, "status": "rejected"}


@api.post("/requests/{request_id}/retry")
async def retry_request(request_id: str, user: User = Depends(get_current_user)):
    doc = await db.requests.find_one({"user_id": user.user_id, "id": request_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Request not found")
    if doc.get("status") not in {"request_failed", "failed"}:
        raise HTTPException(status_code=422, detail="Only failed requests can be retried")
    conn = await db.connections.find_one({"user_id": user.user_id}, {"_id": 0}) or {}
    result = await send_title_to_mediamanager(await library_item_from_request(user.user_id, doc), conn)
    now = datetime.now(timezone.utc).isoformat()
    await db.requests.update_one(
        {"user_id": user.user_id, "id": request_id},
        {
            "$set": {
                "status": "approved",
                "provider": "mediamanager",
                "tmdb_id": result["tmdb_id"],
                "approved_at": now,
                "updated_at": now,
            }
        },
    )
    rec_id = doc.get("recommendation_id")
    if rec_id:
        await db.recommendations.update_one(
            {"user_id": user.user_id, "id": rec_id},
            {
                "$set": {
                    "in_library": True,
                    "tmdb_id": result["tmdb_id"],
                    "approved_at": now,
                    "needs_approval": False,
                }
            },
        )
    updated = await db.requests.find_one({"user_id": user.user_id, "id": request_id}, {"_id": 0, "user_id": 0})
    return {**result, "status": "approved", "request": updated}


@api.get("/recommendations/saved")
async def list_saved(user: User = Depends(get_current_user)):
    docs = await db.recommendations.find(
        {"user_id": user.user_id, "saved": True},
        {"_id": 0, "user_id": 0},
    ).to_list(200)
    return await backfill_posters(user.user_id, docs)


def _sse(data: Dict[str, Any]) -> str:
    return f"data: {json.dumps(data)}\n\n"


@api.get("/recommendations/{rec_id}/trailer")
async def get_trailer(rec_id: str, user: User = Depends(get_current_user)):
    rec = await db.recommendations.find_one({"user_id": user.user_id, "id": rec_id}, {"_id": 0})
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    if "trailer_key" in rec:
        return {"key": rec["trailer_key"], "name": rec.get("trailer_name"), "cached": True}
    if not TMDB_KEY:
        return {"key": None}
    endpoint = "tv" if rec.get("type") == "show" else "movie"
    key = name = None
    async with httpx.AsyncClient(timeout=10) as hc:
        tmdb_id = rec.get("tmdb_id")
        if not tmdb_id:
            meta = await tmdb_lookup(hc, rec["title"], rec.get("year"), rec.get("type", "movie"))
            tmdb_id = meta.get("tmdb_id") if meta else None
        if tmdb_id:
            try:
                r = await hc.get(f"https://api.themoviedb.org/3/{endpoint}/{tmdb_id}/videos", params={"api_key": TMDB_KEY})
                vids = [v for v in (r.json().get("results") or []) if v.get("site") == "YouTube"] if r.status_code == 200 else []
                rank = lambda v: (v.get("type") != "Trailer", not v.get("official"), v.get("type") != "Teaser")
                best = sorted(vids, key=rank)[0] if vids else None
                if best:
                    key, name = best["key"], best.get("name")
            except Exception as e:
                logging.warning(f"TMDB videos failed for {rec['title']}: {e}")
    # Only a hit is cached. Storing a miss as `trailer_key: None` made the
    # `"trailer_key" in rec` check above answer None for ever, so one transient
    # TMDB failure left a title permanently trailerless. The tmdb_id is worth
    # keeping either way - it is what the next lookup starts from.
    stored: Dict[str, Any] = {"tmdb_id": tmdb_id} if tmdb_id else {}
    if key:
        stored.update({"trailer_key": key, "trailer_name": name})
    if stored:
        await db.recommendations.update_one({"user_id": user.user_id, "id": rec_id}, {"$set": stored})
    return {"key": key, "name": name, "cached": False}


@api.get("/requests/{request_id}/details")
async def request_details(request_id: str, user: User = Depends(get_current_user)):
    """Overview, cast and YouTube trailer for a queued title, cached for a day."""
    doc = await db.requests.find_one({"user_id": user.user_id, "id": request_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Request not found")
    tmdb_id = doc.get("tmdb_id")
    if not tmdb_id or not TMDB_KEY:
        return {"id": request_id, "title": doc.get("title"), "overview": None, "trailer": None, "cast": []}

    endpoint = "tv" if str(doc.get("type") or "").lower() in {"show", "tv", "series", "anime"} else "movie"
    cache_key = f"tmdb:details:{endpoint}:{tmdb_id}"
    now = datetime.now(timezone.utc)
    cached = await db.provider_cache.find_one({"key": cache_key, "expires_at": {"$gt": now.isoformat()}})
    if cached and isinstance(cached.get("payload"), dict):
        return {"id": request_id, **cached["payload"]}

    try:
        async with httpx.AsyncClient(timeout=10) as hc:
            r = await hc.get(
                f"https://api.themoviedb.org/3/{endpoint}/{tmdb_id}",
                params={"api_key": TMDB_KEY, "append_to_response": "videos,credits"},
            )
        data = r.json() if r.status_code == 200 else {}
    except httpx.HTTPError as exc:
        logging.warning("TMDb details failed for %s: %s", doc.get("title"), exc)
        data = {}

    videos = [v for v in ((data.get("videos") or {}).get("results") or []) if v.get("site") == "YouTube"]
    videos.sort(key=lambda v: (v.get("type") != "Trailer", not v.get("official"), v.get("type") != "Teaser"))
    trailer = {"key": videos[0]["key"], "name": videos[0].get("name")} if videos else None

    cast = [
        {
            "name": row.get("name"),
            "character": row.get("character")
            or ((row.get("roles") or [{}])[0].get("character") if row.get("roles") else None),
            "profile": f"https://image.tmdb.org/t/p/w185{row['profile_path']}" if row.get("profile_path") else None,
        }
        for row in ((data.get("credits") or {}).get("cast") or [])[:12]
    ]

    payload = {
        "title": data.get("name") or data.get("title") or doc.get("title"),
        "overview": data.get("overview") or None,
        "tagline": data.get("tagline") or None,
        "genres": [g.get("name") for g in (data.get("genres") or []) if g.get("name")],
        "rating": data.get("vote_average"),
        "vote_count": data.get("vote_count"),
        "runtime": data.get("runtime") or ((data.get("episode_run_time") or [None])[0]),
        "seasons": data.get("number_of_seasons"),
        "episodes": data.get("number_of_episodes"),
        "status": data.get("status"),
        "release_date": data.get("first_air_date") or data.get("release_date"),
        "networks": [n.get("name") for n in (data.get("networks") or []) if n.get("name")][:3],
        "trailer": trailer,
        "cast": cast,
    }
    if data:
        await db.provider_cache.update_one(
            {"key": cache_key},
            {"$set": {
                "key": cache_key,
                "payload": payload,
                "expires_at": (now + timedelta(hours=24)).isoformat(),
                "updated_at": now.isoformat(),
            }},
            upsert=True,
        )
    return {"id": request_id, **payload}


@api.get("/recommendations/{rec_id}/reason/stream")
async def stream_reason(rec_id: str, model: Optional[str] = None, user: User = Depends(get_current_user)):
    rec = await db.recommendations.find_one({"user_id": user.user_id, "id": rec_id}, {"_id": 0})
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    conn = await db.connections.find_one({"user_id": user.user_id}, {"_id": 0}) or {}
    url, model_key = resolve_model(conn, model)

    async def gen():
        if rec.get("deep_why"):
            yield _sse({"t": rec["deep_why"], "cached": True})
            yield _sse({"done": True, "model": rec.get("deep_why_model", model_key), "cached": True})
            return
        docs = await db.history.find({"user_id": user.user_id}, {"_id": 0, "user_id": 0}).to_list(60)
        titles = ", ".join(f"{d['title']} ({d.get('year', '')})" for d in docs[:30])
        system = "You are a perceptive film critic. Write in second person, warm and specific, 3 short paragraphs max, plain prose (no markdown, no headings, no lists)."
        prompt = (
            f"The user's watch history: {titles}.\n"
            f"They were recommended '{rec['title']}' ({rec.get('year')}, {rec.get('type')}; genres: {', '.join(rec.get('genres') or [])}). "
            f"Synopsis: {rec.get('synopsis')}\nShort reason given so far: {rec.get('why')}\n"
            "Explain in depth why this pick fits their taste: connect specific titles they watched to specific qualities of this one (tone, themes, craft, pacing), and mention one thing that might surprise them."
        )
        acc: List[str] = []
        try:
            async for chunk in stream_ollama(url, model_key, prompt, system):
                acc.append(chunk)
                yield _sse({"t": chunk})
        except Exception as e:
            logging.warning("Reason stream failed (%s): %s", model_key, e)
            yield _sse({"error": "Ollama stream failed"})
            return
        text = "".join(acc).strip()
        if text:
            await db.recommendations.update_one(
                {"user_id": user.user_id, "id": rec_id},
                {"$set": {"deep_why": text, "deep_why_model": model_key}},
            )
            await record_usage(user.user_id, model_key, "reasoning", len(prompt) + len(system), len(text))
        yield _sse({"done": True, "model": model_key})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------- Usage ----------
@api.get("/usage")
async def get_usage(user: User = Depends(get_current_user)):
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    docs = await db.llm_usage.find(
        {"user_id": user.user_id, "created_at": {"$gte": month_start}}, {"_id": 0}
    ).to_list(5000)
    by_model: Dict[str, Dict[str, int]] = {}
    for d in docs:
        m = by_model.setdefault(d.get("model", "unknown"), {"calls": 0, "est_tokens": 0})
        m["calls"] += 1
        m["est_tokens"] += int(d.get("est_tokens", 0))
    return {
        "month": now.strftime("%B %Y"),
        "calls": len(docs),
        "est_tokens": sum(int(d.get("est_tokens", 0)) for d in docs),
        "by_model": [{"model": k, **v} for k, v in sorted(by_model.items(), key=lambda x: -x[1]["calls"])],
        "last_call_at": max((d["created_at"] for d in docs), default=None),
    }


@api.get("/")
async def root():
    return {"app": "CineMind AI", "status": "ok"}


from api_extra import router as extra_router
api.include_router(extra_router)

app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=[
        o.strip().strip('"').strip("'")
        for o in os.environ.get(
            "CORS_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000,http://192.168.50.223:3000",
        ).split(",")
        if o.strip()
    ],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3})(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

_scheduler_task = None


@app.on_event("startup")
async def start_job_scheduler():
    global _scheduler_task
    from database import ensure_indexes
    from jobs.engine import migrate_jobs_to_interval, start_scheduler
    try:
        await ensure_indexes()
    except Exception:
        logging.exception("Could not create indexes")
    try:
        await migrate_jobs_to_interval()
    except Exception:
        logging.exception("Could not migrate jobs to 30-minute schedule")
    _scheduler_task = start_scheduler()


# Serve the built frontend when it exists, so one process runs the whole app in
# the background. Mounted last: /api keeps priority, everything else falls
# through to index.html for client-side routing.
_BUILD_DIR = Path(__file__).resolve().parents[1] / "frontend" / "build"
if _BUILD_DIR.is_dir():
    from fastapi.staticfiles import StaticFiles
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from starlette.responses import FileResponse
    from starlette.types import Scope

    class SPAStaticFiles(StaticFiles):
        """Unknown paths fall back to index.html so client-side routes work.

        index.html is served no-cache. It names the hashed bundle, so a cached
        shell keeps loading the previous build and every frontend change stays
        invisible until someone hard-reloads. The hashed files under /static/
        never change contents, so those are safe to keep for a year.
        """

        @staticmethod
        def _cache(response, path: str):
            if path.startswith("static/"):
                response.headers["cache-control"] = "public, max-age=31536000, immutable"
            else:
                response.headers["cache-control"] = "no-cache"
            return response

        async def get_response(self, path: str, scope: Scope):
            try:
                return self._cache(await super().get_response(path, scope), path)
            except StarletteHTTPException as exc:
                if exc.status_code != 404:
                    raise
                return self._cache(FileResponse(_BUILD_DIR / "index.html"), "index.html")

    app.mount("/", SPAStaticFiles(directory=str(_BUILD_DIR), html=True), name="frontend")
    logger.info("Serving frontend build from %s", _BUILD_DIR)


@app.on_event("shutdown")
async def shutdown_db_client():
    if _scheduler_task is not None:
        _scheduler_task.cancel()
    client.close()
