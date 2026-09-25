"""Interval jobs must sit 2 minutes apart on the shared 15-minute grid."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from jobs.engine import (
    JOB_INTERVAL_MINUTES,
    JOB_STAGGER_MINUTES,
    normalize_schedule,
    _advance_schedule,
    next_run_at,
    next_stagger_offset,
    restagger_jobs,
    stagger_offset,
)


def _fake_db(jobs):
    fake_db = MagicMock()
    fake_db.jobs.find.return_value.to_list = AsyncMock(return_value=jobs)
    fake_db.jobs.distinct = AsyncMock(return_value=sorted({job["user_id"] for job in jobs}))
    fake_db.jobs.update_one = AsyncMock()
    return fake_db


def test_stagger_offset_walks_the_two_minute_grid():
    assert [stagger_offset(index) for index in range(8)] == [0, 2, 4, 6, 8, 10, 12, 0]
    assert JOB_STAGGER_MINUTES == 2
    assert JOB_INTERVAL_MINUTES == 15


def test_every_15m_honours_the_offset():
    stamp = datetime(2026, 9, 19, 12, 1, tzinfo=timezone.utc)
    assert next_run_at("every_15m", now=stamp, offset_minutes=2).startswith("2026-09-19T12:02")
    assert next_run_at("every_15m", now=stamp, offset_minutes=12).startswith("2026-09-19T12:12")
    assert next_run_at("every_15m", now=stamp, offset_minutes=0).startswith("2026-09-19T12:15")


def test_the_old_30_minute_key_runs_on_the_15_minute_grid():
    stamp = datetime(2026, 9, 19, 12, 1, tzinfo=timezone.utc)
    assert normalize_schedule("every_30m") == "every_15m"
    assert normalize_schedule(None) == "every_15m"
    assert normalize_schedule("daily") == "daily"
    assert next_run_at("every_30m", now=stamp, offset_minutes=0).startswith("2026-09-19T12:15")


def test_next_stagger_offset_picks_the_first_free_slot():
    fake_db = _fake_db([
        {"user_id": "u1", "schedule_offset_minutes": 0},
        {"user_id": "u1", "schedule_offset_minutes": 2},
    ])

    async def run():
        with patch("jobs.engine.db", fake_db):
            return await next_stagger_offset("u1")

    assert asyncio.run(run()) == 4


def test_next_stagger_offset_reuses_least_crowded_slot_when_full():
    fake_db = _fake_db([
        {"user_id": "u1", "schedule_offset_minutes": offset}
        for offset in (0, 0, 2, 4, 6, 8, 10, 12)
    ])

    async def run():
        with patch("jobs.engine.db", fake_db):
            return await next_stagger_offset("u1")

    assert asyncio.run(run()) == 2


def test_restagger_spreads_stacked_jobs_two_minutes_apart():
    jobs = [
        {"user_id": "u1", "id": f"job{n}", "created_at": f"2026-09-0{n}T00:00:00+00:00",
         "schedule_offset_minutes": 0, "next_run_at": "2026-09-19T08:00:00+00:00"}
        for n in range(1, 5)
    ]
    fake_db = _fake_db(jobs)

    async def run():
        with patch("jobs.engine.db", fake_db):
            await restagger_jobs()

    asyncio.run(run())
    offsets = [
        call.args[1]["$set"]["schedule_offset_minutes"]
        for call in fake_db.jobs.update_one.call_args_list
    ]
    assert offsets == [2, 4, 6]  # job1 already owns slot 0 and is left alone


def test_restagger_pulls_in_a_slot_left_on_the_old_30_minute_grid():
    """A job already on slot 0 but planned half an hour out moves to the next quarter."""
    from datetime import timedelta

    far = (datetime.now(timezone.utc) + timedelta(minutes=29)).isoformat()
    fake_db = _fake_db([{"user_id": "u1", "id": "job1", "created_at": "2026-09-01T00:00:00+00:00",
                         "schedule_offset_minutes": 0, "next_run_at": far}])

    async def run():
        with patch("jobs.engine.db", fake_db):
            await restagger_jobs()

    asyncio.run(run())
    update = fake_db.jobs.update_one.call_args.args[1]["$set"]
    assert update["next_run_at"] <= (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()


def test_failed_run_still_moves_the_job_to_its_next_slot():
    fake_db = _fake_db([])
    job = {"id": "job1", "schedule": "every_15m", "timezone": "UTC", "schedule_offset_minutes": 12}
    finished = datetime(2026, 9, 19, 12, 13, tzinfo=timezone.utc)

    async def run():
        with patch("jobs.engine.db", fake_db):
            await _advance_schedule("u1", job, finished)

    asyncio.run(run())
    update = fake_db.jobs.update_one.call_args.args[1]["$set"]
    assert update["next_run_at"].startswith("2026-09-19T12:27")
