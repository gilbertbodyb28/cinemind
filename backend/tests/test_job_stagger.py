"""Interval jobs must sit 6 minutes apart on the shared 30-minute grid."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from jobs.engine import (
    JOB_STAGGER_MINUTES,
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


def test_stagger_offset_walks_the_six_minute_grid():
    assert [stagger_offset(index) for index in range(6)] == [0, 6, 12, 18, 24, 0]
    assert JOB_STAGGER_MINUTES == 6


def test_every_30m_honours_the_offset():
    stamp = datetime(2026, 9, 19, 12, 1, tzinfo=timezone.utc)
    assert next_run_at("every_30m", now=stamp, offset_minutes=6).startswith("2026-09-19T12:06")
    assert next_run_at("every_30m", now=stamp, offset_minutes=18).startswith("2026-09-19T12:18")
    assert next_run_at("every_30m", now=stamp, offset_minutes=0).startswith("2026-09-19T12:30")


def test_next_stagger_offset_picks_the_first_free_slot():
    fake_db = _fake_db([
        {"user_id": "u1", "schedule_offset_minutes": 0},
        {"user_id": "u1", "schedule_offset_minutes": 6},
    ])

    async def run():
        with patch("jobs.engine.db", fake_db):
            return await next_stagger_offset("u1")

    assert asyncio.run(run()) == 12


def test_next_stagger_offset_reuses_least_crowded_slot_when_full():
    fake_db = _fake_db([
        {"user_id": "u1", "schedule_offset_minutes": offset}
        for offset in (0, 0, 6, 12, 18, 24)
    ])

    async def run():
        with patch("jobs.engine.db", fake_db):
            return await next_stagger_offset("u1")

    assert asyncio.run(run()) == 6


def test_restagger_spreads_stacked_jobs_six_minutes_apart():
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
    assert offsets == [6, 12, 18]  # job1 already owns slot 0 and is left alone


def test_failed_run_still_moves_the_job_to_its_next_slot():
    fake_db = _fake_db([])
    job = {"id": "job1", "schedule": "every_30m", "timezone": "UTC", "schedule_offset_minutes": 12}
    finished = datetime(2026, 9, 19, 12, 13, tzinfo=timezone.utc)

    async def run():
        with patch("jobs.engine.db", fake_db):
            await _advance_schedule("u1", job, finished)

    asyncio.run(run())
    update = fake_db.jobs.update_one.call_args.args[1]["$set"]
    assert update["next_run_at"].startswith("2026-09-19T12:42")
