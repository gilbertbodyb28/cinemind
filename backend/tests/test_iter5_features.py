"""Iteration 5: history poster backfill, Simkl PIN sign-in, Trakt watchlist push."""
import subprocess
import time

import pytest
import requests

from conftest import BASE_URL

FE_TOKEN = "test_session_fe_claude"
FE_USER = "test-user-fe-claude"


@pytest.fixture(scope="module")
def fe_client():
    """Persistent seeded UI session (per /app/memory/test_credentials.md)."""
    script = f"""
use('test_database');
db.user_sessions.updateOne({{session_token:"{FE_TOKEN}"}},{{$set:{{expires_at:new Date(Date.now()+7*864e5).toISOString()}}}});
"""
    subprocess.run(["mongosh", "--quiet", "--eval", script], check=False, capture_output=True, timeout=60)
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json", "Authorization": f"Bearer {FE_TOKEN}"})
    r = s.get(f"{BASE_URL}/api/auth/me", timeout=30)
    if r.status_code != 200:
        pytest.fail(f"FE session rejected: {r.status_code} {r.text[:300]}")
    return s


def _mongosh(script):
    return subprocess.run(["mongosh", "--quiet", "--eval", script], check=False, capture_output=True, timeout=60)


# ---------- Feature 1: History posters ----------
class TestHistoryPosters:
    def test_backfill_posters_on_get_history(self, fe_client):
        _mongosh(f"""use('test_database');
print(db.history.updateMany({{user_id:'{FE_USER}'}},{{$unset:{{poster:'',poster_checked:'',tmdb_id:''}}}}).modifiedCount);""")
        r = fe_client.get(f"{BASE_URL}/api/history", timeout=90)
        assert r.status_code == 200, r.text
        docs = r.json()
        assert isinstance(docs, list) and len(docs) >= 1
        assert all("_id" not in d for d in docs), "MongoDB _id leaked"
        missing = [d.get("title") for d in docs if not d.get("poster")]
        assert not missing, f"Items without poster after backfill: {missing}"
        assert all(str(d["poster"]).startswith("https://image.tmdb.org/") for d in docs), \
            [d["poster"] for d in docs]
        no_tmdb = [d.get("title") for d in docs if not d.get("tmdb_id")]
        assert not no_tmdb, f"Items without tmdb_id: {no_tmdb}"

    def test_second_get_is_fast_and_persisted(self, fe_client):
        t0 = time.time()
        r = fe_client.get(f"{BASE_URL}/api/history", timeout=60)
        elapsed = time.time() - t0
        assert r.status_code == 200
        docs = r.json()
        assert all(d.get("poster") for d in docs)
        assert elapsed < 5, f"Second /api/history took {elapsed:.1f}s — poster_checked not persisted?"
        # verify persistence flag in db
        out = _mongosh(f"""use('test_database');
print(db.history.countDocuments({{user_id:'{FE_USER}', poster_checked:true}}));""")
        assert out.stdout.decode().strip().splitlines()[-1] != "0"

    def test_history_unauth(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/history", timeout=30)
        assert r.status_code == 401


# ---------- Feature 2: Simkl PIN sign-in ----------
class TestSimklPin:
    codes = {}

    def test_pin_start(self, auth_client):
        r = auth_client.post(f"{BASE_URL}/api/simkl/pin/start", timeout=40)
        assert r.status_code == 200, r.text
        d = r.json()
        assert isinstance(d["user_code"], str) and len(d["user_code"]) >= 5, d
        assert isinstance(d["device_code"], str) and len(d["device_code"]) >= 10, d
        assert "simkl.com/pin" in d["verification_url"], d
        assert isinstance(d["expires_in"], int) and d["expires_in"] > 0
        assert isinstance(d["interval"], int) and d["interval"] > 0
        TestSimklPin.codes["device"] = d["device_code"]

    def test_pin_start_unauth(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/simkl/pin/start", timeout=30)
        assert r.status_code == 401

    def test_pin_poll_pending(self, auth_client):
        code = TestSimklPin.codes.get("device")
        assert code, "pin/start must run first"
        time.sleep(6)
        r = auth_client.post(f"{BASE_URL}/api/simkl/pin/poll", json={"device_code": code}, timeout=40)
        assert r.status_code == 200, r.text
        assert r.json().get("status") == "pending", r.text

    def test_pin_poll_bogus_code(self, auth_client):
        time.sleep(6)
        r = auth_client.post(f"{BASE_URL}/api/simkl/pin/poll", json={"device_code": "0000000000deadbeef"}, timeout=40)
        assert r.status_code == 200, r.text
        assert r.json().get("status") in ("expired", "invalid", "pending"), r.text

    def test_pin_poll_validation(self, auth_client):
        r = auth_client.post(f"{BASE_URL}/api/simkl/pin/poll", json={}, timeout=30)
        assert r.status_code == 422

    def test_disconnect_and_connections(self, auth_client):
        r = auth_client.post(f"{BASE_URL}/api/simkl/disconnect", timeout=30)
        assert r.status_code == 200 and r.json().get("ok") is True
        c = auth_client.get(f"{BASE_URL}/api/connections", timeout=30)
        assert c.status_code == 200
        d = c.json()
        assert d["simkl_connected"] is False
        assert d.get("simkl_username") is None
        assert d["trakt_connected"] is False
        assert "_id" not in d and "user_id" not in d

    def test_connections_test_simkl(self, auth_client):
        r = auth_client.post(f"{BASE_URL}/api/connections/test/simkl", timeout=40)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("ok") is True, d
        assert "not authorized" in (d.get("message") or "").lower(), d


# ---------- Feature 3: Trakt watchlist push ----------
class TestWatchlistPush:
    def test_unknown_rec_404(self, auth_client):
        r = auth_client.post(f"{BASE_URL}/api/recommendations/does-not-exist/watchlist", timeout=30)
        assert r.status_code == 404, r.text

    def test_unauth_401(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/recommendations/abc/watchlist", timeout=30)
        assert r.status_code == 401

    def test_409_when_trakt_not_connected(self, fe_client):
        recs = fe_client.get(f"{BASE_URL}/api/recommendations", timeout=60)
        assert recs.status_code == 200, recs.text
        items = recs.json()
        if not items:
            pytest.skip("no existing recommendations for fe user")
        rec_id = items[0]["id"]
        r = fe_client.post(f"{BASE_URL}/api/recommendations/{rec_id}/watchlist", timeout=40)
        assert r.status_code == 409, f"{r.status_code} {r.text[:300]}"
        assert "Connect Trakt first" in r.json().get("detail", ""), r.text
