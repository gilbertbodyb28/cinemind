"""Iteration 4: Trakt device OAuth, trailer preview, saved_at stamping."""
import subprocess
import time
import uuid

import pytest
import requests

from conftest import BASE_URL, USER_ID


# ---------- Trakt device OAuth ----------
class TestTraktDevice:
    def test_device_start_requires_auth(self):
        r = requests.post(f"{BASE_URL}/api/trakt/device/start", timeout=30)
        assert r.status_code == 401, r.text

    def test_device_start_returns_code(self, auth_client):
        r = auth_client.post(f"{BASE_URL}/api/trakt/device/start", timeout=40)
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ("device_code", "user_code", "verification_url", "expires_in", "interval"):
            assert k in d, f"missing {k} in {d}"
        assert isinstance(d["user_code"], str) and len(d["user_code"]) == 8, d["user_code"]
        assert d["verification_url"] == "https://auth.trakt.tv/activate"
        assert isinstance(d["expires_in"], int) and d["expires_in"] > 0
        assert isinstance(d["interval"], int) and d["interval"] > 0
        pytest.device_code = d["device_code"]

    def test_device_poll_pending(self, auth_client):
        code = getattr(pytest, "device_code", None)
        if not code:
            r = auth_client.post(f"{BASE_URL}/api/trakt/device/start", timeout=40)
            code = r.json()["device_code"]
        r = auth_client.post(f"{BASE_URL}/api/trakt/device/poll", json={"device_code": code}, timeout=40)
        assert r.status_code == 200, r.text
        assert r.json().get("status") in ("pending", "slow_down"), r.json()

    def test_device_poll_bogus(self, auth_client):
        time.sleep(6)  # respect polling interval
        r = auth_client.post(f"{BASE_URL}/api/trakt/device/poll", json={"device_code": "bogus"}, timeout=40)
        assert r.status_code == 200, r.text
        assert r.json().get("status") == "invalid", r.json()

    def test_device_poll_requires_auth(self):
        r = requests.post(f"{BASE_URL}/api/trakt/device/poll", json={"device_code": "bogus"}, timeout=30)
        assert r.status_code == 401, r.text

    def test_device_poll_validation(self, auth_client):
        r = auth_client.post(f"{BASE_URL}/api/trakt/device/poll", json={}, timeout=30)
        assert r.status_code == 422, r.text


class TestTraktDisconnectAndTest:
    def test_disconnect_clears_state(self, auth_client):
        r = auth_client.post(f"{BASE_URL}/api/trakt/disconnect", timeout=30)
        assert r.status_code == 200, r.text
        assert r.json().get("ok") is True

        c = auth_client.get(f"{BASE_URL}/api/connections", timeout=30)
        assert c.status_code == 200, c.text
        data = c.json()
        assert data.get("trakt_connected") is False, data
        assert data.get("trakt_username") is None, data
        assert data.get("trakt_refresh_token") is None, data
        assert "trakt_expires_at" in data, data
        assert "_id" not in data

    def test_test_trakt_unauthorized_message(self, auth_client):
        r = auth_client.post(f"{BASE_URL}/api/connections/test/trakt", timeout=40)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("ok") is True, d
        assert "not authorized" in (d.get("message") or "").lower(), d


# ---------- Trailer ----------
class TestTrailer:
    @pytest.fixture(scope="class")
    def rec_ids(self, auth_client):
        r = auth_client.get(f"{BASE_URL}/api/recommendations", timeout=60)
        assert r.status_code == 200, r.text
        recs = r.json()
        assert len(recs) >= 2, recs
        return recs

    def test_trailer_requires_auth(self, rec_ids):
        r = requests.get(f"{BASE_URL}/api/recommendations/{rec_ids[0]['id']}/trailer", timeout=30)
        assert r.status_code == 401, r.text

    def test_trailer_unknown_id_404(self, auth_client):
        r = auth_client.get(f"{BASE_URL}/api/recommendations/{uuid.uuid4()}/trailer", timeout=30)
        assert r.status_code == 404, r.text

    def test_trailer_returns_key_and_caches(self, auth_client, rec_ids):
        rec = rec_ids[0]
        r = auth_client.get(f"{BASE_URL}/api/recommendations/{rec['id']}/trailer", timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert isinstance(d.get("key"), str) and len(d["key"]) > 5, d
        assert d.get("name"), d
        # second call is served from cache
        r2 = auth_client.get(f"{BASE_URL}/api/recommendations/{rec['id']}/trailer", timeout=30)
        assert r2.status_code == 200, r2.text
        d2 = r2.json()
        assert d2.get("cached") is True, d2
        assert d2["key"] == d["key"]

    def test_trailer_no_tmdb_match_returns_null_key(self, auth_client):
        rid = f"TEST_{uuid.uuid4()}"
        script = f"""
use('test_database');
db.recommendations.insertOne({{user_id: '{USER_ID}', id: '{rid}', title: 'TEST_Zzqqxx Nonsense Title 9999', year: 1899, type: 'movie', genres: ['Drama'], poster: null, synopsis: 'n/a', tmdb_rating: 0, match_score: 1, why: 'n/a', saved: false, dismissed: false}});
"""
        subprocess.run(["mongosh", "--quiet", "--eval", script], check=True, capture_output=True, timeout=60)
        try:
            r = auth_client.get(f"{BASE_URL}/api/recommendations/{rid}/trailer", timeout=60)
            assert r.status_code == 200, r.text
            assert r.json().get("key") is None, r.json()
        finally:
            subprocess.run(
                ["mongosh", "--quiet", "--eval", f"use('test_database'); db.recommendations.deleteOne({{id: '{rid}'}});"],
                check=False, capture_output=True, timeout=60,
            )


# ---------- saved_at ----------
class TestSavedAt:
    def test_save_stamps_saved_at(self, auth_client):
        recs = auth_client.get(f"{BASE_URL}/api/recommendations", timeout=60).json()
        movie = next(r for r in recs if r["type"] == "movie")
        show = next(r for r in recs if r["type"] == "show")
        for rec in (movie, show):
            s = auth_client.post(f"{BASE_URL}/api/recommendations/{rec['id']}/save", timeout=30)
            assert s.status_code == 200, s.text

        saved = auth_client.get(f"{BASE_URL}/api/recommendations/saved", timeout=60)
        assert saved.status_code == 200, saved.text
        items = {i["id"]: i for i in saved.json()}
        for rec in (movie, show):
            assert rec["id"] in items, f"{rec['title']} not in saved list"
            ts = items[rec["id"]].get("saved_at")
            assert isinstance(ts, str) and ts.startswith("20"), ts
            from datetime import datetime
            datetime.fromisoformat(ts)  # must be ISO parseable
            assert items[rec["id"]]["saved"] is True
