import os
import subprocess
import time

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL is missing from the process environment and /app/frontend/.env")
BASE_URL = base_url.rstrip("/")

SESSION_TOKEN = "test_session_claude_1"
USER_ID = "test-user-claude-1"


def _seed_session():
    """Seed a user + session directly in mongo per /app/auth_testing.md."""
    script = f"""
use('test_database');
db.users.deleteMany({{user_id: '{USER_ID}'}});
db.user_sessions.deleteMany({{session_token: '{SESSION_TOKEN}'}});
db.users.insertOne({{user_id: '{USER_ID}', email: 'test.user.claude1@example.com', name: 'Test User', picture: 'https://via.placeholder.com/150', created_at: new Date()}});
db.user_sessions.insertOne({{user_id: '{USER_ID}', session_token: '{SESSION_TOKEN}', expires_at: new Date(Date.now() + 7*24*60*60*1000), created_at: new Date()}});
print('seeded');
"""
    subprocess.run(["mongosh", "--quiet", "--eval", script], check=True, capture_output=True, timeout=60)


@pytest.fixture(scope="session", autouse=True)
def seeded_session():
    _seed_session()
    yield
    cleanup = f"""
use('test_database');
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
    """Ensure ollama_url is unset so Claude fallback path is exercised."""
    r = auth_client.put(f"{BASE_URL}/api/connections", json={"ollama_url": None, "ollama_model": "llama3.2"}, timeout=30)
    assert r.status_code == 200, r.text
    time.sleep(0.2)
    return True
