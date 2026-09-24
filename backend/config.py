"""Process-wide settings. Secrets stay in environment variables and are never logged."""

from pathlib import Path
from typing import Any, Dict, List, Optional
import os

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")


def env_flag(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000").rstrip("/")
LOCAL_UI_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000")
CORS_ORIGIN_REGEX = r"https?://(localhost|127\.0\.0\.1)(:\d+)?$"

# Cookies must not be Secure/SameSite=None over plain http://localhost or the browser
# drops them silently. Set both to the stricter values when serving over HTTPS.
COOKIE_SECURE = env_flag("COOKIE_SECURE", "false")
COOKIE_SAMESITE = os.environ.get("COOKIE_SAMESITE", "lax").strip().lower()
SESSION_TTL_DAYS = int(os.environ.get("SESSION_TTL_DAYS", "7"))

# Seeds sample history/recommendations when no provider is connected, so the app is
# explorable before linking Plex/Trakt/Simkl. Disable for real use.
DEMO_MODE = env_flag("DEMO_MODE", "true")

# Server-wide Ollama defaults; per-user overrides live in the `connections` collection.
# Gemma 4 runs conservatively because providers/ollama.py fixes temperature at 0.
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:12b-it-qat")

TMDB_KEY = os.environ.get("TMDB_API_KEY")
TVDB_API_KEY = os.environ.get("TVDB_API_KEY")
TRAKT_CLIENT_ID = os.environ.get("TRAKT_CLIENT_ID")
TRAKT_CLIENT_SECRET = os.environ.get("TRAKT_CLIENT_SECRET")
TRAKT_API = "https://api.trakt.tv"
SIMKL_CLIENT_ID = os.environ.get("SIMKL_CLIENT_ID")
SIMKL_CLIENT_SECRET = os.environ.get("SIMKL_CLIENT_SECRET")
SIMKL_API = "https://api.simkl.com"
ANILIST_CLIENT_ID = os.environ.get("ANILIST_CLIENT_ID")
ANILIST_CLIENT_SECRET = os.environ.get("ANILIST_CLIENT_SECRET")
ANILIST_REDIRECT_URI = os.environ.get("ANILIST_REDIRECT_URI", "http://localhost:8000/api/anilist/auth/callback")
REQUEST_PROVIDER_URL = os.environ.get("REQUEST_PROVIDER_URL")
REQUEST_PROVIDER_API_KEY = os.environ.get("REQUEST_PROVIDER_API_KEY")

GOOGLE_CLIENT_ID = (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
GOOGLE_CLIENT_SECRET = (os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip()
GOOGLE_REDIRECT_URI = os.environ.get(
    "GOOGLE_REDIRECT_URI",
    "http://localhost:8000/api/auth/google/callback",
).strip()

PLACEHOLDER_POSTER = "https://images.unsplash.com/photo-1489599849927-2ee91cede3ba?w=500"


def allowed_cors_origins() -> List[str]:
    origins = [item.strip() for item in CORS_ORIGINS.split(",") if item.strip()]
    for origin in LOCAL_UI_ORIGINS:
        if origin not in origins:
            origins.append(origin)
    return origins


def google_oauth_configured() -> bool:
    client_id = GOOGLE_CLIENT_ID
    secret = GOOGLE_CLIENT_SECRET
    if not client_id or not secret:
        return False
    if client_id.startswith("your-") or secret.startswith("your-"):
        return False
    return True


CLAUDE_MODEL_KEYS = frozenset({
    "sonnet-5",
    "opus-5",
    "haiku-4.5",
    "claude-sonnet-5",
    "claude-opus-5",
    "claude-haiku-4-5-20251001",
})
# Models that were once the shipped default but are no longer offered in the UI.
# A connection still pinned to one of these cannot be changed by the user - the
# picker does not list it - so it silently overrides the configured default for
# ever. The former Qwen defaults are included so a stored connection cannot
# silently override the new Gemma default.
LEGACY_OLLAMA_MODELS = frozenset({
    "llama3.2", "llama3.2:latest", "llama3",
    "qwen-suggestarr", "qwen2.5:7b-instruct-q6_k",
    "qwen3:14b", "qwen3:14b-latest",
})


def ollama_model_key(name: Optional[str]) -> str:
    """Normalise a model name for comparison.

    `ollama pull qwen-suggestarr` installs a model the API then reports as
    `qwen-suggestarr:latest`, so an exact-string check against the retired set
    misses it and the retirement is silently bypassed. Comparing on the bare
    name catches both spellings.
    """
    key = (name or "").strip().lower()
    return key[: -len(":latest")] if key.endswith(":latest") else key


# The retired names as they compare: `llama3.2` and `llama3.2:latest` are one
# model, and the set is written with both spellings in places.
_LEGACY_MODEL_KEYS = frozenset(ollama_model_key(name) for name in LEGACY_OLLAMA_MODELS)


def is_legacy_ollama_model(name: Optional[str]) -> bool:
    """True when this name is a retired default, in any tag spelling."""
    key = ollama_model_key(name)
    return bool(key) and key in _LEGACY_MODEL_KEYS


def is_claude_model(name: Optional[str]) -> bool:
    if not name:
        return False
    key = name.strip().lower()
    return key in CLAUDE_MODEL_KEYS or key.startswith("claude")


def effective_ollama_model(conn: Dict[str, Any], override: Optional[str] = None) -> str:
    """Pick the Ollama model. Retired defaults and Claude keys use OLLAMA_MODEL."""
    raw = (override or conn.get("ollama_model") or OLLAMA_MODEL or "").strip()
    if not raw or is_claude_model(raw) or is_legacy_ollama_model(raw):
        return OLLAMA_MODEL
    return raw


def resolve_model(conn: Dict[str, Any], override: Optional[str] = None) -> tuple[str, str]:
    """Resolve the Ollama (base_url, model) pair for a user, honouring a per-request override."""
    url = (conn.get("ollama_url") or OLLAMA_BASE_URL).rstrip("/")
    return url, effective_ollama_model(conn, override)
