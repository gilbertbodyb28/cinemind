"""The queue stops growing, a rejection on Home stays a rejection, a refused sign-in is not "Connected".

Measured 2026-09-25: 10,180 titles waited for a decision (Tv 5,531 against its
limit of 250); a pick rejected on Home was deleted with the rest of the old list
and could come straight back; Sources said "Connected" over a revoked Trakt
session while every Tv run failed.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jobs.engine as jobs_engine
import mediamanager_client
import request_providers
from fake_mongo import _Cursor, matches
from recommendation.exclusion_engine import apply_exclusions, build_exclusion_context


class _Requests:
    def __init__(self, rows, waiting=0):
        self.rows = rows
        self.waiting = waiting

    async def find_one(self, query, _projection=None):
        for row in self.rows:
            if matches(row, {key: value for key, value in query.items() if key != "user_id"}):
                return dict(row)
        return None

    def find(self, query, _projection=None):
        # The queue lookup walks every row with a title's name and year, then keeps the one in its scope.
        return _Cursor([dict(row) for row in self.rows
                        if matches(row, {key: value for key, value in query.items() if key != "user_id"})])

    async def count_documents(self, _query):
        return self.waiting


def _run(monkeypatch, *, rows, stored=(), waiting=0, limit=3, mode="require_approval"):
    requests = _Requests(list(stored), waiting)
    recommendations = SimpleNamespace(update_one=AsyncMock())
    monkeypatch.setattr(jobs_engine, "db", SimpleNamespace(requests=requests, recommendations=recommendations))
    submitted = []

    async def fake_submit(self, user_id, item, status="requested"):
        submitted.append(item["title"])
        return {"id": "req_" + item["title"], "status": status}

    monkeypatch.setattr(request_providers.LocalRequestProvider, "submit", fake_submit)
    send = AsyncMock(return_value={"tmdb_id": 1})
    monkeypatch.setattr(mediamanager_client, "send_item_to_library", send)
    job = {"id": "job_1", "action_mode": mode, "final_recommendation_limit": limit}
    warnings = asyncio.run(jobs_engine.apply_job_action_mode("u1", job, rows, {}))
    return submitted, send, warnings


def _rows(*titles):
    return [{"id": "rec_" + title, "title": title, "year": 2026, "type": "show"} for title in titles]


def test_a_full_queue_takes_no_new_titles_but_refreshes_waiting_ones(monkeypatch):
    waiting = {"id": "req_old", "title": "Old", "year": 2026, "status": "pending_approval"}
    submitted, _send, warnings = _run(monkeypatch, rows=_rows("Old", "New"), stored=[waiting], waiting=3, limit=3)

    assert submitted == ["Old"]
    assert warnings and warnings[0]["code"] == "queue_full"


def test_room_is_what_is_left_under_the_limit(monkeypatch):
    submitted, _send, warnings = _run(monkeypatch, rows=_rows("A", "B", "C"), waiting=1, limit=3)

    assert submitted == ["A", "B"]
    assert warnings[0]["code"] == "queue_full"


def test_an_approved_title_is_not_sent_to_mediamanager_again(monkeypatch):
    approved = {"id": "req_a", "title": "A", "year": 2026, "status": "approved"}
    submitted, send, _warnings = _run(monkeypatch, rows=_rows("A", "B"), stored=[approved], mode="auto_request")

    assert [call.args[0]["title"] for call in send.await_args_list] == ["B"]
    assert submitted == ["B"]


def test_a_dismissed_pick_is_never_recommended_again():
    dismissed = {"title": "Reacher", "year": 2022, "type": "show", "media_type": "tv", "tmdb_id": 108978,
                 "dismissed": True, "created_at": "2026-09-01T00:00:00+00:00"}
    context = build_exclusion_context([], [], [dismissed], [], [])
    candidate = {"title": "Reacher", "year": 2022, "type": "show", "media_type": "tv", "tmdb_id": 108978}

    assert apply_exclusions(candidate, context, {"already_recommended": False}) == (False, "rejected_dismissed")


def test_a_refused_sign_in_is_not_reported_as_connected():
    import server

    shown = server.connections_public({"trakt_refresh_token": "r", "trakt_auth_error": "Trakt 401",
                                       "simkl_access_token": "s", "plex_token": "p"})
    assert (shown.trakt_connected, shown.trakt_auth_error) == (False, "Trakt 401")
    assert shown.simkl_connected is True and shown.plex_connected is True


def test_auto_request_falling_back_to_the_queue_respects_the_same_ceiling(monkeypatch):
    from fastapi import HTTPException

    requests = _Requests([], waiting=3)
    recommendations = SimpleNamespace(update_one=AsyncMock())
    monkeypatch.setattr(jobs_engine, "db", SimpleNamespace(requests=requests, recommendations=recommendations))
    submitted = []

    async def fake_submit(self, user_id, item, status="requested"):
        submitted.append((item["title"], status))
        return {"id": "req_" + item["title"], "status": status}

    monkeypatch.setattr(request_providers.LocalRequestProvider, "submit", fake_submit)
    # MediaManager not configured: every title would fall back to "waiting for approval".
    monkeypatch.setattr(mediamanager_client, "send_item_to_library",
                        AsyncMock(side_effect=HTTPException(status_code=409, detail="MediaManager not configured")))
    job = {"id": "job_1", "action_mode": "auto_request", "final_recommendation_limit": 3}
    warnings = asyncio.run(jobs_engine.apply_job_action_mode("u1", job, _rows("A", "B"), {}))

    assert submitted == []
    assert [w["code"] for w in warnings][-1] == "queue_full"
