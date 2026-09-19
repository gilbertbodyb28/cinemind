import os
import subprocess
import time

from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values

# Resolved from the checkout, not the /app path the export was built for.
REPO_ROOT = Path(__file__).resolve().parents[2]
frontend_env = dotenv_values(REPO_ROOT / "frontend" / ".env")
backend_env = dotenv_values(REPO_ROOT / "backend" / ".env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL is missing from the process environment and frontend/.env")
BASE_URL = base_url.rstrip("/")
# The seed has to land in the database the running server actually reads.
DB_NAME = os.environ.get("DB_NAME") or backend_env.get("DB_NAME") or "cinemind"

SESSION_TOKEN = "test_session_claude_1"
USER_ID = "test-user-claude-1"


def _seed_session():
    """Seed a user + session directly in mongo per /app/auth_testing.md.

    Upsert rather than delete-then-insert: this fixture is session scoped and
    autouse, so every xdist worker runs it at once against the same database.
    The old pair raced — one worker's delete landed between another's delete and
    insert — which the unique indexes on user_id and session_token turn into a
    duplicate-key error and 89 collection errors.
    """
    script = f"""
use('{DB_NAME}');
db.users.replaceOne(
  {{user_id: '{USER_ID}'}},
  {{user_id: '{USER_ID}', email: 'test.user.claude1@example.com', name: 'Test User', picture: 'https://via.placeholder.com/150', created_at: new Date()}},
  {{upsert: true}}
);
db.user_sessions.replaceOne(
  {{session_token: '{SESSION_TOKEN}'}},
  {{user_id: '{USER_ID}', session_token: '{SESSION_TOKEN}', expires_at: new Date(Date.now() + 7*24*60*60*1000), created_at: new Date()}},
  {{upsert: true}}
);
print('seeded');
"""
    subprocess.run(["mongosh", "--quiet", "--eval", script], check=True, capture_output=True, timeout=60)


@pytest.fixture(scope="session", autouse=True)
def seeded_session():
    _seed_session()
    yield
    cleanup = f"""
use('{DB_NAME}');
db.users.deleteMany({{user_id: '{USER_ID}'}});
db.user_sessions.deleteMany({{session_token: '{SESSION_TOKEN}'}});
db.history.deleteMany({{user_id: '{USER_ID}'}});
db.recommendations.deleteMany({{user_id: '{USER_ID}'}});
db.taste_profiles.deleteMany({{user_id: '{USER_ID}'}});
db.connections.deleteMany({{user_id: '{USER_ID}'}});
"""
    subprocess.run(["mongosh", "--quiet", "--eval", cleanup], check=False, capture_output=True, timeout=60)


@pytest.fixture(scope="session")
def api_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def auth_client(seeded_session, api_client):
    s = requests.Session()
    s.headers.update({
        "Content-Type": "application/json",
        "Authorization": f"Bearer {SESSION_TOKEN}",
    })
    r = s.get(f"{BASE_URL}/api/auth/me", timeout=30)
    if r.status_code != 200:
        pytest.fail(f"Seeded session rejected: {r.status_code} {r.text[:300]}")
    return s


@pytest.fixture
def no_ollama(auth_client):
    """Keep Ollama URL empty in the document; server still falls back to localhost:11434."""
    r = auth_client.put(f"{BASE_URL}/api/connections", json={"ollama_url": None, "ollama_model": "qwen3:14b"}, timeout=30)
    assert r.status_code == 200, r.text
    time.sleep(0.2)
    return True
