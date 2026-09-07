from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, Cookie, Header
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv
import asyncio
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import uuid
import json
import random
import httpx
import re
from pathlib import Path
from emergentintegrations.llm.chat import LlmChat, UserMessage, TextDelta, StreamDone
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

app = FastAPI(title="CineMind AI")
api = APIRouter(prefix="/api")

EMERGENT_AUTH_URL = "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data"
TMDB_KEY = os.environ.get("TMDB_API_KEY")
TRAKT_CLIENT_ID = os.environ.get("TRAKT_CLIENT_ID")
TRAKT_CLIENT_SECRET = os.environ.get("TRAKT_CLIENT_SECRET")
TRAKT_API = "https://api.trakt.tv"
SIMKL_CLIENT_ID = os.environ.get("SIMKL_CLIENT_ID")
SIMKL_API = "https://api.simkl.com"
PLACEHOLDER_POSTER = "https://images.unsplash.com/photo-1489599849927-2ee91cede3ba?w=500"

CLAUDE_MODELS = {
    "sonnet-5": "claude-sonnet-5",
    "opus-5": "claude-opus-5",
    "haiku-4.5": "claude-haiku-4-5-20251001",
}
DEFAULT_MODEL = "sonnet-5"


def resolve_model(conn: Dict[str, Any], override: Optional[str] = None) -> tuple[str, str]:
    key = override if override in CLAUDE_MODELS else (conn.get("llm_model") if conn.get("llm_model") in CLAUDE_MODELS else DEFAULT_MODEL)
    return CLAUDE_MODELS[key], key


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
    ollama_model: Optional[str] = "llama3.2"
    llm_model: Optional[str] = DEFAULT_MODEL


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
    if isinstance(user_doc.get("created_at"), str):
        user_doc["created_at"] = datetime.fromisoformat(user_doc["created_at"])
    return User(**user_doc)


# ---------- Auth routes ----------
@api.post("/auth/session")
async def create_session(request: Request, response: Response):
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

    session_token = data["session_token"]
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    await db.user_sessions.insert_one({
        "user_id": user_id,
        "session_token": session_token,
        "expires_at": expires_at.isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    response.set_cookie(
        key="session_token",
        value=session_token,
        httponly=True,
        secure=True,
        samesite="none",
        path="/",
        max_age=7 * 24 * 3600,
    )
    return {"user_id": user_id, "email": email, "name": data["name"], "picture": data.get("picture", "")}


from fastapi import Depends


@api.get("/auth/me", response_model=User)
async def me(user: User = Depends(get_current_user)):
    return user


@api.post("/auth/logout")
async def logout(response: Response, session_token: Optional[str] = Cookie(None)):
    if session_token:
        await db.user_sessions.delete_one({"session_token": session_token})
    response.delete_cookie("session_token", path="/")
    return {"ok": True}


# ---------- Connections ----------
@api.get("/connections", response_model=Connections)
async def get_connections(user: User = Depends(get_current_user)):
    doc = await db.connections.find_one({"user_id": user.user_id}, {"_id": 0, "user_id": 0}) or {}
    doc["trakt_connected"] = bool(doc.get("trakt_refresh_token"))
    doc["simkl_connected"] = bool(doc.get("simkl_access_token"))
    return Connections(**doc)


@api.put("/connections", response_model=Connections)
async def update_connections(payload: Connections, user: User = Depends(get_current_user)):
    data = payload.model_dump(exclude={"trakt_connected", "simkl_connected"})
    await db.connections.update_one(
        {"user_id": user.user_id},
        {"$set": {**data, "user_id": user.user_id}},
        upsert=True,
    )
    payload.trakt_connected = bool(data.get("trakt_refresh_token"))
    payload.simkl_connected = bool(data.get("simkl_access_token"))
    return payload


# ---------- Simkl PIN OAuth ----------
def simkl_params(client_id: str) -> Dict[str, str]:
    return {"client_id": client_id, "app-name": "CineMindAI", "app-version": "1.0"}


def simkl_headers(client_id: str, token: Optional[str] = None) -> Dict[str, str]:
    h = {"User-Agent": "CineMindAI/1.0", "simkl-api-key": client_id, "Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


class SimklPoll(BaseModel):
    user_code: str


@api.post("/simkl/pin/start")
async def simkl_pin_start(user: User = Depends(get_current_user)):
    if not SIMKL_CLIENT_ID:
        raise HTTPException(status_code=503, detail="Simkl app credentials not configured on server")
    async with httpx.AsyncClient(timeout=15) as hc:
        r = await hc.get(f"{SIMKL_API}/oauth/pin", params=simkl_params(SIMKL_CLIENT_ID), headers=simkl_headers(SIMKL_CLIENT_ID))
    data = r.json() if r.status_code == 200 else {}
    if data.get("result") != "OK" or not data.get("user_code"):
        raise HTTPException(status_code=502, detail=f"Simkl responded {r.status_code}")
    return {
        "user_code": data["user_code"],
        "verification_url": data.get("verification_url") or data.get("verification_uri") or "https://simkl.com/pin",
        "expires_in": int(data.get("expires_in", 900)),
        "interval": int(data.get("interval", 5)),
    }


@api.post("/simkl/pin/poll")
async def simkl_pin_poll(body: SimklPoll, user: User = Depends(get_current_user)):
    async with httpx.AsyncClient(timeout=15) as hc:
        r = await hc.get(f"{SIMKL_API}/oauth/pin/{body.user_code}", params=simkl_params(SIMKL_CLIENT_ID), headers=simkl_headers(SIMKL_CLIENT_ID))
        data = r.json() if r.status_code == 200 else {}
        if data.get("result") == "KO":
            return {"status": "pending"}
        if data.get("result") == "OK" and data.get("access_token"):
            token = data["access_token"]
            username = None
            try:
                me = await hc.post(f"{SIMKL_API}/users/settings", params=simkl_params(SIMKL_CLIENT_ID), headers=simkl_headers(SIMKL_CLIENT_ID, token))
                if me.status_code == 200:
                    username = (me.json().get("user") or {}).get("name")
            except Exception as e:
                logging.warning(f"Simkl settings fetch failed: {e}")
            await db.connections.update_one(
                {"user_id": user.user_id},
                {"$set": {"user_id": user.user_id, "simkl_client_id": SIMKL_CLIENT_ID, "simkl_access_token": token, "simkl_username": username}},
                upsert=True,
            )
            return {"status": "authorized", "username": username}
        if "device_code" in data:
            return {"status": "expired"}
    return {"status": "invalid"}


@api.post("/simkl/disconnect")
async def simkl_disconnect(user: User = Depends(get_current_user)):
    await db.connections.update_one(
        {"user_id": user.user_id},
        {"$set": {"simkl_access_token": None, "simkl_username": None}},
    )
    return {"ok": True}


# ---------- Trakt device OAuth ----------
def trakt_headers(client_id: str, token: Optional[str] = None) -> Dict[str, str]:
    h = {"Content-Type": "application/json", "trakt-api-key": client_id, "trakt-api-version": "2", "User-Agent": "CineMindAI/1.0"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


class DevicePoll(BaseModel):
    device_code: str


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


@api.post("/connections/test/ollama")
async def test_ollama(user: User = Depends(get_current_user)):
    conn = await db.connections.find_one({"user_id": user.user_id}, {"_id": 0}) or {}
    url = conn.get("ollama_url")
    model = conn.get("ollama_model") or "llama3.2"
    if not url:
        return {"ok": False, "message": "No Ollama URL configured"}
    try:
        async with httpx.AsyncClient(timeout=8) as hc:
            r = await hc.get(f"{url.rstrip('/')}/api/tags")
        if r.status_code == 200:
            tags = r.json().get("models", [])
            return {"ok": True, "message": f"Connected. {len(tags)} model(s) available.", "models": [m.get("name") for m in tags]}
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
    """Fetch history from configured sources; falls back to demo if none configured or all fail."""
    conn = await db.connections.find_one({"user_id": user.user_id}, {"_id": 0}) or {}
    items: List[Dict[str, Any]] = []

    # Trakt
    trakt_cid = conn.get("trakt_client_id") or TRAKT_CLIENT_ID
    trakt_tok = await trakt_token(user.user_id, conn) if trakt_cid else None
    if trakt_cid and trakt_tok:
        try:
            async with httpx.AsyncClient(timeout=12) as hc:
                r = await hc.get(
                    f"{TRAKT_API}/sync/history?limit=50&extended=full",
                    headers=trakt_headers(trakt_cid, trakt_tok),
                )
                if r.status_code == 200:
                    for entry in r.json():
                        m = entry.get("movie") or entry.get("show") or {}
                        items.append({
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
                else:
                    logging.warning(f"Trakt history responded {r.status_code}")
        except Exception as e:
            logging.warning(f"Trakt sync failed: {e}")

    # Simkl
    simkl_cid = conn.get("simkl_client_id") or SIMKL_CLIENT_ID
    if simkl_cid and conn.get("simkl_access_token"):
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
                            items.append({
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
                else:
                    logging.warning(f"Simkl all-items responded {r.status_code}")
        except Exception as e:
            logging.warning(f"Simkl sync failed: {e}")

    # Plex
    if conn.get("plex_url") and conn.get("plex_token"):
        try:
            async with httpx.AsyncClient(timeout=12, verify=False) as hc:
                r = await hc.get(
                    f"{conn['plex_url'].rstrip('/')}/library/all",
                    headers={"X-Plex-Token": conn["plex_token"], "Accept": "application/json"},
                )
                if r.status_code == 200:
                    data = r.json().get("MediaContainer", {}).get("Metadata", [])[:50]
                    for m in data:
                        items.append({
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
        except Exception as e:
            logging.warning(f"Plex sync failed: {e}")

    used_demo = False
    if not items:
        items = [{**h, "id": str(uuid.uuid4()), "watched_at": (datetime.now(timezone.utc) - timedelta(days=i*3)).isoformat()} for i, h in enumerate(DEMO_HISTORY)]
        used_demo = True
    else:
        await enrich_history_posters(items)

    await db.history.delete_many({"user_id": user.user_id})
    if items:
        await db.history.insert_many([{**it, "user_id": user.user_id} for it in items])
    return {"count": len(items), "demo": used_demo}


@api.get("/history")
async def get_history(user: User = Depends(get_current_user)):
    docs = await db.history.find({"user_id": user.user_id}, {"_id": 0, "user_id": 0}).to_list(500)
    if not docs:
        # auto-seed demo on first load
        seeded = [{**h, "id": str(uuid.uuid4()), "watched_at": (datetime.now(timezone.utc) - timedelta(days=i*3)).isoformat(), "user_id": user.user_id} for i, h in enumerate(DEMO_HISTORY)]
        await db.history.insert_many(seeded)
        docs = [{k: v for k, v in d.items() if k not in ("user_id", "_id")} for d in seeded]
    missing = [d for d in docs if not d.get("poster") and not d.get("poster_checked")][:24]
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
async def call_ollama(url: str, model: str, prompt: str, system: str = "") -> Optional[str]:
    try:
        async with httpx.AsyncClient(timeout=90) as hc:
            r = await hc.post(
                f"{url.rstrip('/')}/api/generate",
                json={"model": model, "prompt": prompt, "system": system, "stream": False, "format": "json"},
            )
            if r.status_code == 200:
                return r.json().get("response", "")
    except Exception as e:
        logging.warning(f"Ollama call failed: {e}")
    return None


async def record_usage(user_id: str, model_key: str, action: str, prompt_chars: int, output_chars: int) -> None:
    await db.llm_usage.insert_one({
        "user_id": user_id,
        "model": model_key,
        "action": action,
        "est_tokens": (prompt_chars + output_chars) // 4,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


async def call_claude(session_id: str, prompt: str, system: str = "", model_id: str = "claude-sonnet-5",
                      user_id: str = "", model_key: str = DEFAULT_MODEL, action: str = "generate") -> Optional[str]:
    """Non-streaming Claude call via Emergent LLM key. Returns raw text."""
    key = os.environ.get("EMERGENT_LLM_KEY")
    if not key:
        return None
    try:
        chat = LlmChat(
            api_key=key,
            session_id=session_id,
            system_message=system or "You are a helpful assistant.",
        ).with_model("anthropic", model_id)
        text = await chat.send_message(UserMessage(text=prompt))
        text = text if isinstance(text, str) else str(text)
        await record_usage(user_id, model_key, action, len(prompt) + len(system), len(text))
        return text
    except Exception as e:
        logging.warning(f"Claude call failed ({model_id}): {e}")
        return None


# ---------- TMDB enrichment ----------
async def tmdb_lookup(hc: httpx.AsyncClient, title: str, year: Optional[int], type_: str) -> Optional[Dict[str, Any]]:
    if not TMDB_KEY:
        return None
    endpoint = "tv" if type_ == "show" else "movie"
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
    targets = [it for it in items if not it.get("poster")]
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


def _extract_json(raw: str) -> Optional[Any]:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", raw)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


async def generate_with_llm(conn: Dict[str, Any], session_id: str, system: str, prompt: str,
                            user_id: str = "", action: str = "generate", model_override: Optional[str] = None) -> tuple[Optional[Any], str, str]:
    """Try Ollama first (if configured), then Claude with one retry. Returns (parsed_json_or_none, provider, model_key)."""
    url = conn.get("ollama_url")
    model = conn.get("ollama_model") or "llama3.2"
    if url:
        raw = await call_ollama(url, model, prompt, system)
        parsed = _extract_json(raw) if raw else None
        if parsed is not None:
            return parsed, "ollama", model
        if raw:
            logging.warning(f"Ollama unparseable: {raw[:400]}")

    model_id, model_key = resolve_model(conn, model_override)
    for attempt in (1, 2):
        raw = await call_claude(f"{session_id}-{attempt}", prompt, system, model_id, user_id, model_key, action)
        parsed = _extract_json(raw) if raw else None
        if parsed is not None:
            return parsed, "claude", model_key
        if raw:
            logging.warning(f"Claude unparseable (attempt {attempt}): {raw[:400]}")
    return None, "demo", model_key


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
    conn = await db.connections.find_one({"user_id": user.user_id}, {"_id": 0}) or {}
    await ensure_history(user.user_id)
    docs = await db.history.find({"user_id": user.user_id}, {"_id": 0, "user_id": 0}).to_list(200)
    titles = ", ".join([f"{d['title']}" for d in docs[:30]])

    system = "You are a movie/show recommender. Given the user's watch history, return exactly 8 personalized recommendations. Respond ONLY with a JSON object with a 'recommendations' key whose value is a list of objects: {title, year, type ('movie'|'show'), genres (list of strings), synopsis (1-2 sentences), tmdb_rating (float 6.0-9.5), match_score (int 70-99), why (2 sentence personalized reason referencing watched titles)}. No prose outside JSON."
    prompt = f"Watch history: {titles}\nGenerate 8 diverse recommendations they haven't seen. Return JSON only."
    parsed, provider, model_key = await generate_with_llm(conn, f"recs-{user.user_id}", system, prompt, user.user_id, "recommendations", payload.model if payload else None)

    recs = None
    if parsed is not None:
        arr = parsed.get("recommendations") if isinstance(parsed, dict) else (parsed if isinstance(parsed, list) else None)
        if isinstance(arr, list) and arr:
            recs = []
            for r in arr[:8]:
                if not isinstance(r, dict):
                    continue
                recs.append({
                    "id": str(uuid.uuid4()),
                    "title": r.get("title", "Untitled"),
                    "year": int(r.get("year", 2020)) if str(r.get("year", "")).isdigit() else 2020,
                    "type": r.get("type", "movie"),
                    "genres": r.get("genres", []) if isinstance(r.get("genres"), list) else [],
                    "poster": r.get("poster") or PLACEHOLDER_POSTER,
                    "backdrop": r.get("backdrop"),
                    "synopsis": r.get("synopsis", ""),
                    "match_score": int(r.get("match_score", 85)),
                    "why": r.get("why", ""),
                    "tmdb_rating": float(r.get("tmdb_rating", 7.5)),
                    "saved": False,
                    "dismissed": False,
                    "provider": provider,
                    "model": model_key,
                })
            recs = await enrich_with_tmdb(recs)

    if not recs:
        recs = [{**r, "id": str(uuid.uuid4()), "saved": False, "dismissed": False, "provider": "demo"} for r in DEMO_RECS]
        random.shuffle(recs)
        provider = "demo"

    await db.recommendations.delete_many({"user_id": user.user_id, "saved": {"$ne": True}})
    await db.recommendations.insert_many([{**r, "user_id": user.user_id} for r in recs])
    return {"count": len(recs), "provider": provider, "model": model_key, "demo": provider == "demo"}


@api.get("/recommendations")
async def list_recs(user: User = Depends(get_current_user)):
    docs = await db.recommendations.find(
        {"user_id": user.user_id, "dismissed": {"$ne": True}},
        {"_id": 0, "user_id": 0},
    ).to_list(50)
    if not docs:
        # auto-seed demo
        seeded = [{**r, "id": str(uuid.uuid4()), "saved": False, "dismissed": False, "user_id": user.user_id} for r in DEMO_RECS]
        await db.recommendations.insert_many(seeded)
        docs = [{k: v for k, v in d.items() if k not in ("user_id", "_id")} for d in seeded]
    return await backfill_posters(user.user_id, docs)


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
    await db.recommendations.update_one(
        {"user_id": user.user_id, "id": rec_id},
        {"$set": {"dismissed": True}},
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
    await db.recommendations.update_one(
        {"user_id": user.user_id, "id": rec_id},
        {"$set": {"trailer_key": key, "trailer_name": name, **({"tmdb_id": tmdb_id} if tmdb_id else {})}},
    )
    return {"key": key, "name": name, "cached": False}


@api.get("/recommendations/{rec_id}/reason/stream")
async def stream_reason(rec_id: str, model: Optional[str] = None, user: User = Depends(get_current_user)):
    rec = await db.recommendations.find_one({"user_id": user.user_id, "id": rec_id}, {"_id": 0})
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    conn = await db.connections.find_one({"user_id": user.user_id}, {"_id": 0}) or {}
    model_id, model_key = resolve_model(conn, model)

    async def gen():
        if rec.get("deep_why"):
            yield _sse({"t": rec["deep_why"], "cached": True})
            yield _sse({"done": True, "model": rec.get("deep_why_model", model_key), "cached": True})
            return
        key = os.environ.get("EMERGENT_LLM_KEY")
        if not key:
            yield _sse({"error": "LLM key not configured"})
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
        chat = LlmChat(api_key=key, session_id=f"reason-{user.user_id}-{rec_id}", system_message=system).with_model("anthropic", model_id)
        acc: List[str] = []
        try:
            async for ev in chat.stream_message(UserMessage(text=prompt)):
                if isinstance(ev, TextDelta):
                    acc.append(ev.content)
                    yield _sse({"t": ev.content})
                elif isinstance(ev, StreamDone):
                    break
        except Exception as e:
            logging.warning(f"Reason stream failed ({model_id}): {e}")
            yield _sse({"error": "Claude stream failed"})
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


app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
