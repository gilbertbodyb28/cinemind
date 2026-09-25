"""Job action_mode: pending Requests vs MediaManager auto-add."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from jobs.engine import apply_job_action_mode


def _no_requests(fake_db):
    """apply_job_action_mode looks the title up in Requests and counts the job's
    waiting rows first; none here."""
    fake_db.requests.find_one = AsyncMock(return_value=None)
    fake_db.requests.count_documents = AsyncMock(return_value=0)
    fake_db.requests.find.return_value.__aiter__.return_value = iter([])
    return fake_db


def _row(**extra):
    return {
        "id": "rec1",
        "title": "Fight Club",
        "year": 1999,
        "type": "movie",
        "tmdb_id": 550,
        "poster": "https://image.tmdb.org/t/p/w500/p.jpg",
        **extra,
    }


def test_require_approval_queues_pending_request_with_poster():
    fake_db = _no_requests(MagicMock())
    fake_db.recommendations.update_one = AsyncMock()
    submitted = []

    async def fake_submit(self, user_id, item, status="requested"):
        submitted.append((user_id, item, status))
        return {"ok": True, "status": status, "provider": "local", "id": "req_abc"}

    async def run():
        with (
            patch("jobs.engine.db", fake_db),
            patch("request_providers.LocalRequestProvider.submit", fake_submit),
        ):
            return await apply_job_action_mode(
                "u1",
                {"id": "job1", "action_mode": "require_approval"},
                [_row()],
                {},
            )

    warnings = asyncio.run(run())
    assert warnings == []
    assert submitted[0][2] == "pending_approval"
    assert submitted[0][1]["poster"].startswith("https://")
    assert submitted[0][1]["recommendation_id"] == "rec1"
    assert "id" not in submitted[0][1]
    fake_db.recommendations.update_one.assert_awaited()


def test_auto_request_sends_to_mediamanager():
    fake_db = _no_requests(MagicMock())
    fake_db.recommendations.update_one = AsyncMock()
    submitted = []
    send_calls = []

    async def fake_submit(self, user_id, item, status="requested"):
        submitted.append((item, status))
        return {"ok": True, "status": status, "provider": item.get("provider") or "local", "id": "req_mm"}

    async def fake_send(item, conn, tmdb_api_key=None):
        send_calls.append(item["title"])
        return {
            "ok": True,
            "created": True,
            "already_existed": False,
            "tmdb_id": 550,
            "media_type": "movie",
        }

    async def run():
        with (
            patch("jobs.engine.db", fake_db),
            patch("request_providers.LocalRequestProvider.submit", fake_submit),
            patch("mediamanager_client.send_item_to_library", fake_send),
        ):
            return await apply_job_action_mode(
                "u1",
                {"id": "job1", "action_mode": "auto_request"},
                [_row()],
                {
                    "mediamanager_url": "http://mm:8000",
                    "mediamanager_email": "a@b.c",
                    "mediamanager_password": "secret",
                },
            )

    warnings = asyncio.run(run())
    assert warnings == []
    assert send_calls == ["Fight Club"]
    assert submitted[0][1] == "approved"
    assert submitted[0][0]["provider"] == "mediamanager"


def test_auto_request_without_mediamanager_falls_back_to_pending():
    fake_db = _no_requests(MagicMock())
    fake_db.recommendations.update_one = AsyncMock()
    submitted = []

    async def fake_submit(self, user_id, item, status="requested"):
        submitted.append(status)
        return {"ok": True, "status": status, "provider": "local", "id": "req_pend"}

    async def fake_send(item, conn, tmdb_api_key=None):
        raise HTTPException(status_code=409, detail="Connect MediaManager first")

    async def run():
        with (
            patch("jobs.engine.db", fake_db),
            patch("request_providers.LocalRequestProvider.submit", fake_submit),
            patch("mediamanager_client.send_item_to_library", fake_send),
        ):
            return await apply_job_action_mode(
                "u1",
                {"id": "job1", "action_mode": "auto_request"},
                [_row()],
                {},
            )

    warnings = asyncio.run(run())
    assert warnings
    assert submitted == ["pending_approval"]


def test_recommendations_only_does_not_queue():
    fake_db = MagicMock()

    async def run():
        with patch("jobs.engine.db", fake_db):
            return await apply_job_action_mode(
                "u1",
                {"id": "job1", "action_mode": "recommendations_only"},
                [_row()],
                {},
            )

    warnings = asyncio.run(run())
    assert warnings == []
    fake_db.recommendations.update_one.assert_not_called()


def test_every_30m_schedule_is_half_hour_from_now():
    from datetime import datetime, timedelta, timezone

    from jobs.engine import next_run_at, validate_job

    stamp = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    assert next_run_at("every_30m", now=stamp) == (stamp + timedelta(minutes=30)).isoformat()
    assert next_run_at("manual", now=stamp) is None
    validate_job({
        "job_type": "personalized",
        "media_types": ["movie"],
        "candidate_sources": ["seed_expand"],
        "candidate_limit": 40,
        "final_recommendation_limit": 8,
        "schedule": "every_30m",
    })


def test_history_warning_skipped_when_watch_history_exists():
    from jobs.engine import required_history_warnings

    fake_db = MagicMock()
    fake_db.connections.find_one = AsyncMock(return_value={"trakt_access_token": "t", "trakt_username": "g"})
    fake_db.history.find_one = AsyncMock(return_value={"_id": "h1"})

    async def run():
        with patch("jobs.engine.db", fake_db):
            return await required_history_warnings("u1", {"required_sources": ["trakt"]})

    assert asyncio.run(run()) == []
    fake_db.provider_sync_state.find_one.assert_not_called()


def test_optional_disconnected_source_does_not_warn():
    from jobs.engine import fetch_linked_provider_candidates

    fake_db = MagicMock()
    fake_db.connections.find_one = AsyncMock(return_value={})

    async def run():
        with patch("jobs.engine.db", fake_db):
            return await fetch_linked_provider_candidates("u1", {"trakt", "simkl"}, set())

    extra, warnings = asyncio.run(run())
    assert extra == []
    assert warnings == []
