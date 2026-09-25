"""A job that meets a title again never overturns the user's decision on it.

LocalRequestProvider.submit() found the row by title + year and only kept
approved rows, so a job run flipped a rejected request back to
pending_approval - and in auto_request mode sent it to MediaManager first.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jobs.engine as jobs_engine
import mediamanager_client
import request_providers


class _Requests:
    def __init__(self, rows):
        self.rows = rows
        self.updates = []

    async def find_one(self, query, _projection=None):
        for row in self.rows:
            if all(row.get(key) == value for key, value in query.items() if key != "user_id"):
                return dict(row)
        return None

    def find(self, query, _projection=None):
        status = query.get("status")
        self._found = [dict(row) for row in self.rows if status is None or row.get("status") == status]
        return self

    async def to_list(self, _limit):
        return self._found

    async def update_one(self, query, update, upsert=False):
        self.updates.append((query, update))


def test_a_job_does_not_flip_a_rejected_request_back_to_pending(monkeypatch):
    requests = _Requests([{"id": "req_1", "title": "Dune", "year": 2021, "status": "rejected"}])
    monkeypatch.setattr(request_providers, "db", SimpleNamespace(requests=requests))

    stored = asyncio.run(request_providers.LocalRequestProvider().submit(
        "test-user", {"title": "Dune", "year": 2021, "source_job_id": "job_1"}, "pending_approval",
    ))

    assert stored["status"] == "rejected"
    assert requests.updates == []


def test_a_pending_request_is_still_refreshed_by_the_job(monkeypatch):
    requests = _Requests([{"id": "req_2", "title": "Arcane", "year": 2021, "status": "pending_approval"}])
    monkeypatch.setattr(request_providers, "db", SimpleNamespace(requests=requests))

    stored = asyncio.run(request_providers.LocalRequestProvider().submit(
        "test-user", {"title": "Arcane", "year": 2021, "match_score": 91}, "pending_approval",
    ))

    assert stored["status"] == "pending_approval"
    assert requests.updates and requests.updates[0][1]["$set"]["match_score"] == 91


def _run_action(monkeypatch, mode):
    requests = _Requests([{"id": "req_1", "title": "Dune", "year": 2021, "status": "rejected"}])
    recommendations = SimpleNamespace(update_one=AsyncMock())
    monkeypatch.setattr(jobs_engine, "db", SimpleNamespace(requests=requests, recommendations=recommendations))
    submit = AsyncMock(return_value={"id": "req_new", "status": "pending_approval"})
    monkeypatch.setattr(request_providers.LocalRequestProvider, "submit", submit)
    send = AsyncMock(return_value={"tmdb_id": 438631})
    monkeypatch.setattr(mediamanager_client, "send_item_to_library", send)
    rows = [
        {"id": "rec_dune", "title": "Dune", "year": 2021, "type": "movie"},
        {"id": "rec_new", "title": "Pluribus", "year": 2025, "type": "show"},
    ]
    asyncio.run(jobs_engine.apply_job_action_mode("test-user", {"id": "job_1", "action_mode": mode}, rows, {}))
    return submit, send, recommendations


def test_a_rejected_title_is_not_queued_again(monkeypatch):
    submit, _send, recommendations = _run_action(monkeypatch, "require_approval")

    titles = [call.args[1]["title"] for call in submit.await_args_list]
    assert titles == ["Pluribus"]
    hidden = recommendations.update_one.await_args_list[0].args
    assert hidden[0]["id"] == "rec_dune" and hidden[1]["$set"]["dismissed"] is True


def test_a_rejected_title_is_not_sent_to_mediamanager(monkeypatch):
    _submit, send, _recommendations = _run_action(monkeypatch, "auto_request")

    assert [call.args[0]["title"] for call in send.await_args_list] == ["Pluribus"]
