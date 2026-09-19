"""Runtime logs feed and the AniList history sync behind it."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api_extra import WARNING_HINTS, _level_for_code, runtime_logs
from models import User
from providers.anilist import _entry_watched_at, fetch_media_list


def _user():
    return User(user_id="user_1", email="a@b.c", name="Tester", created_at=datetime.now(timezone.utc))


def _cursor(rows):
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.to_list = AsyncMock(return_value=rows)
    return cursor


def test_provider_breakages_are_bugs_and_setup_gaps_are_warnings():
    assert _level_for_code("tmdb_failed") == "bug"
    assert _level_for_code("simkl_http_error") == "bug"
    assert _level_for_code("ollama_rerank_unusable") == "bug"
    assert _level_for_code("history_never_synced") == "warning"
    assert _level_for_code("anilist_not_connected") == "warning"


def test_runtime_logs_splits_a_run_into_one_row_per_signal():
    runs = [
        {
            "id": "run_1",
            "job_id": "job_1",
            "status": "completed_with_warnings",
            "trigger_type": "manual",
            "started_at": "2026-09-19T10:00:00+00:00",
            "finished_at": "2026-09-19T10:00:09+00:00",
            "accepted_count": 3,
            "candidate_count": 40,
            "warnings": [
                {"code": "history_never_synced", "source": "anilist"},
                {"code": "tmdb_failed", "source": "tmdb", "detail": "boom"},
            ],
        },
        {
            "id": "run_2",
            "job_id": "job_1",
            "status": "failed",
            "trigger_type": "scheduled",
            "started_at": "2026-09-19T09:00:00+00:00",
            "finished_at": "2026-09-19T09:00:02+00:00",
            "error": "Required TMDb source is not configured",
            "warnings": [],
        },
    ]
    states = [
        {"provider": "anilist", "last_success_at": "2026-09-19T08:00:00+00:00", "items_synced": 44},
        {"provider": "trakt", "last_sync_at": "2026-09-19T07:00:00+00:00", "last_error": "401"},
    ]
    requests = [
        {"id": "r1", "title": "Dune", "type": "movie", "status": "approved", "updated_at": "2026-09-19T06:00:00+00:00"},
        {
            "id": "r2",
            "title": "Moonbeam",
            "status": "approved",
            "delivery_status": "not_delivered",
            "delivery_error": "Connect MediaManager first",
            "updated_at": "2026-09-19T05:00:00+00:00",
        },
    ]

    fake_db = MagicMock()
    fake_db.job_runs.find.return_value = _cursor(runs)
    fake_db.provider_sync_state.find.return_value = _cursor(states)
    fake_db.requests.find.return_value = _cursor(requests)

    with patch("api_extra.db", fake_db), patch(
        "api_extra.list_jobs", AsyncMock(return_value=[{"id": "job_1", "name": "Anime nights"}])
    ):
        out = asyncio.run(runtime_logs(user=_user()))

    by_code = {row["code"]: row for row in out["rows"]}
    assert out["counts"] == {"failed": 1, "error": 2, "bug": 1, "warning": 1, "approved": 1, "ok": 1}
    assert by_code["run_failed"]["detail"] == "Required TMDb source is not configured"
    assert by_code["history_never_synced"]["detail"] == WARNING_HINTS["history_never_synced"]
    assert by_code["tmdb_failed"]["level"] == "bug"
    assert by_code["sync_ok"]["detail"] == "44 titles synced"
    assert by_code["sync_error"]["level"] == "error"
    assert by_code["delivery_failed"]["detail"] == "Connect MediaManager first"
    assert by_code["approved"]["detail"] == "Sent to Movies"
    # Newest first, so the run that just finished is the row you read first.
    assert [row["time"] for row in out["rows"]] == sorted(
        (row["time"] for row in out["rows"]), reverse=True
    )


def test_runtime_logs_keeps_notable_rows_when_the_feed_is_trimmed():
    runs = [
        {
            "id": f"run_{i}",
            "job_id": "job_1",
            "status": "completed",
            "trigger_type": "scheduled",
            "started_at": f"2026-09-19T10:{i:02d}:00+00:00",
            "finished_at": f"2026-09-19T10:{i:02d}:05+00:00",
            "accepted_count": 1,
            "candidate_count": 2,
            "warnings": [],
        }
        for i in range(30)
    ]
    # Oldest row of the lot, so a plain newest-first cut would drop it.
    runs.append({
        "id": "run_old",
        "job_id": "job_1",
        "status": "failed",
        "trigger_type": "manual",
        "started_at": "2026-09-18T00:00:00+00:00",
        "finished_at": "2026-09-18T00:00:01+00:00",
        "error": "Required source anilist failed",
        "warnings": [],
    })

    fake_db = MagicMock()
    fake_db.job_runs.find.return_value = _cursor(runs)
    fake_db.provider_sync_state.find.return_value = _cursor([])
    fake_db.requests.find.return_value = _cursor([])

    with patch("api_extra.db", fake_db), patch("api_extra.list_jobs", AsyncMock(return_value=[])):
        out = asyncio.run(runtime_logs(user=_user(), limit=5))

    assert out["truncated"] is True
    assert len(out["rows"]) == 5
    assert any(row["code"] == "run_failed" for row in out["rows"])


def test_anilist_entry_watched_at_prefers_completed_date():
    assert _entry_watched_at({"completedAt": {"year": 2024, "month": 3, "day": 7}}).startswith("2024-03-07")
    assert _entry_watched_at({"completedAt": {"year": None}, "updatedAt": 1700000000}).startswith("2023-11-14")
    assert _entry_watched_at({}) is None


def test_anilist_list_raises_so_the_sync_records_a_real_error():
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"errors": [{"message": "Invalid token"}]}
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("providers.anilist.httpx.AsyncClient", return_value=client):
        with pytest.raises(RuntimeError, match="Invalid token"):
            asyncio.run(fetch_media_list("token", "gibbe21"))
