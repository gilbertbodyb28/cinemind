"""The queue must sort in Mongo, not after the row cap."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from models import User
from server import REQUEST_LIST_CAP, list_requests, requests_version


def _user():
    return User(user_id="user_1", email="a@b.c", name="Tester", created_at=datetime.now(timezone.utc))


class _Cursor:
    """Records what the caller asked Mongo for."""

    def __init__(self, rows, calls):
        self._rows = rows
        self._calls = calls
        self.sorted_on = None
        self.limit = None

    def sort(self, field, direction):
        self.sorted_on = (field, direction)
        return self

    def limit(self, n):  # noqa: F811 - mirrors motor's cursor API
        return self

    async def to_list(self, n):
        self._calls.append({"query": self._query, "sort": self.sorted_on, "cap": n})
        return self._rows


def _db(pending, other, calls):
    db = MagicMock()

    def find(query, projection=None):
        rows = pending if "$in" in str(query.get("status", "")) else other
        cursor = _Cursor(rows, calls)
        cursor._query = query
        return cursor

    db.requests.find.side_effect = find
    return db


def test_the_queue_asks_mongo_for_newest_first():
    calls = []
    pending = [{"id": "a", "title": "New", "status": "pending_approval", "updated_at": "2026-09-19T16:00:00+00:00"}]
    other = [{"id": "b", "title": "Old", "status": "approved", "updated_at": "2026-09-01T10:00:00+00:00"}]

    with patch("server.db", _db(pending, other, calls)), patch(
        "server.attach_request_match_scores", AsyncMock(side_effect=lambda uid, rows: rows)
    ):
        rows = asyncio.run(list_requests(user=_user()))

    # Both lanes sort server-side. Sorting after .to_list() only reordered the
    # arbitrary slice the cap had already taken, so new rows never arrived.
    assert [call["sort"] for call in calls] == [("updated_at", -1), ("updated_at", -1)]
    assert all(call["cap"] == REQUEST_LIST_CAP for call in calls)
    # Pending stays ahead of everything already decided.
    assert [row["id"] for row in rows] == ["a", "b"]


def test_rejected_rows_stay_out_of_both_lanes():
    calls = []
    with patch("server.db", _db([], [], calls)), patch(
        "server.attach_request_match_scores", AsyncMock(side_effect=lambda uid, rows: rows)
    ):
        asyncio.run(list_requests(user=_user()))

    decided = calls[1]["query"]["status"]["$nin"]
    assert "rejected" in decided


def test_version_token_reports_count_and_newest_stamp():
    db = MagicMock()
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(return_value=[{"updated_at": "2026-09-19T16:00:40+00:00"}])
    db.requests.find.return_value = cursor
    db.requests.count_documents = AsyncMock(return_value=2380)

    with patch("server.db", db):
        out = asyncio.run(requests_version(user=_user()))

    assert out == {"count": 2380, "latest": "2026-09-19T16:00:40+00:00"}
