"""Google sign-in from a framed CineMind: popup on localhost, session on the frame's origin."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import server

HANDOFF = "h" * 43


class _Handoffs:
    """Just enough of a Mongo collection for the handoff routes."""

    def __init__(self):
        self.rows = {}

    async def delete_many(self, query):
        cutoff = query["expires_at"]["$lt"]
        for key in [k for k, row in self.rows.items() if row["expires_at"] < cutoff]:
            del self.rows[key]

    async def replace_one(self, query, doc, upsert=False):
        self.rows[query["handoff_hash"]] = dict(doc)

    async def find_one_and_delete(self, query):
        row = self.rows.get(query["handoff_hash"])
        if not row or row["expires_at"] < query["expires_at"]["$gte"]:
            return None
        return self.rows.pop(query["handoff_hash"])


def _fake_db(state_doc=None):
    db = MagicMock()
    db.oauth_states.find_one_and_delete = AsyncMock(return_value=state_doc)
    db.oauth_states.insert_one = AsyncMock()
    db.oauth_handoffs = _Handoffs()
    db.users.find_one = AsyncMock(return_value={
        "user_id": "user_1", "email": "g@example.com", "name": "G", "created_at": "2026-09-26T00:00:00+00:00",
    })
    db.user_sessions.insert_one = AsyncMock()
    return db


def _state(handoff=HANDOFF):
    doc = {
        "state": "s1",
        "provider": "google",
        "return_to": "http://localhost:8001",
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    }
    if handoff:
        doc["handoff_hash"] = server._handoff_hash(handoff)
    return doc


def _run(coro):
    return asyncio.run(coro)


def test_start_stores_only_the_hash_of_the_handoff():
    db = _fake_db()
    request = MagicMock()
    request.headers = {"accept": "application/json"}
    with patch.object(server, "db", db), patch.object(server, "google_oauth_configured", return_value=True):
        _run(server.google_start(request, return_to="http://192.168.50.94:8001", handoff=HANDOFF))
    stored = db.oauth_states.insert_one.call_args.args[0]
    assert stored["handoff_hash"] == server._handoff_hash(HANDOFF)
    assert HANDOFF not in str(stored)


def test_start_ignores_a_malformed_handoff():
    assert server._handoff_hash("short") is None
    assert server._handoff_hash("x" * 40 + "<script>") is None


def test_popup_sign_in_is_redeemed_once_on_the_frame_origin():
    db = _fake_db(_state())
    profile = {"google_sub": "1", "email": "g@example.com", "name": "G", "picture": ""}
    with patch.object(server, "db", db), \
            patch.object(server, "google_exchange_code", AsyncMock(return_value=profile)), \
            patch.object(server, "upsert_google_user", AsyncMock(return_value={"user_id": "user_1"})):
        popup = _run(server.google_callback(code="c", state="s1"))
        # The popup ends on a page that closes itself, not on the dashboard.
        assert popup.status_code == 200 and b"Signed in" in popup.body
        assert "session_token=" in popup.headers.get("set-cookie", "")

        response = server.Response()
        redeemed = _run(server.google_handoff(server.GoogleHandoffBody(handoff=HANDOFF), response))
        assert redeemed["status"] == "ok" and redeemed["user"]["user_id"] == "user_1"
        assert "session_token=" in response.headers.get("set-cookie", "")

        again = _run(server.google_handoff(server.GoogleHandoffBody(handoff=HANDOFF), server.Response()))
        assert again == {"status": "pending"}


def test_unknown_handoff_stays_pending_and_issues_nothing():
    db = _fake_db()
    response = server.Response()
    with patch.object(server, "db", db):
        result = _run(server.google_handoff(server.GoogleHandoffBody(handoff="z" * 43), response))
    assert result == {"status": "pending"}
    assert "set-cookie" not in response.headers
    db.user_sessions.insert_one.assert_not_awaited()


def test_cancelled_popup_reports_the_reason_to_the_frame():
    db = _fake_db(_state())
    with patch.object(server, "db", db):
        popup = _run(server.google_callback(error="access_denied", state="s1"))
        assert popup.status_code == 200 and b"cancelled" in popup.body
        result = _run(server.google_handoff(server.GoogleHandoffBody(handoff=HANDOFF), server.Response()))
    assert result == {"status": "error", "error": "google_denied"}


def test_direct_sign_in_still_redirects_to_the_dashboard():
    db = _fake_db(_state(handoff=None))
    with patch.object(server, "db", db), \
            patch.object(server, "google_exchange_code", AsyncMock(return_value={})), \
            patch.object(server, "upsert_google_user", AsyncMock(return_value={"user_id": "user_1"})):
        response = _run(server.google_callback(code="c", state="s1"))
    assert response.status_code == 307
    assert response.headers["location"] == "http://localhost:8001/dashboard"
    assert db.oauth_handoffs.rows == {}
