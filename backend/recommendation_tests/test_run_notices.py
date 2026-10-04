"""A full queue share and a run with nothing new are notices, not warnings.

Measured 2026-09-25 on Gilbert's jobs: every run of Tv, Upcoming US + Anime/Donghua
and Upcoming Tv Shows finished "with warnings" because their share of Requests
was full (5,530 / 4,021 / 534 waiting against 250 / 200 / 12), the toast said
"2 sent to Requests" for two picks the full queue had held back, and the Tv run
at 16:34 UTC was reported as "413 fell on rejected_year. No candidate fell inside
the job's year window" while 436 candidates were already in Requests and 18 met
every setting but fell below the taste floor. Gilbert chose: at most 650 waiting
per job, a full share shown as information.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jobs.engine as jobs_engine
import mediamanager_client
import request_providers
from fake_mongo import _Cursor, matches
from jobs.engine import REJECTION_HINTS, empty_result_warnings, split_notices


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
        return _Cursor([dict(row) for row in self.rows
                        if matches(row, {key: value for key, value in query.items() if key != "user_id"})])

    async def count_documents(self, _query):
        return self.waiting


def _rows(*titles):
    return [{"id": "rec_" + title, "title": title, "year": 2026, "type": "show"} for title in titles]


def _queue(monkeypatch, *, stored=(), waiting=0):
    requests = _Requests(list(stored), waiting)
    monkeypatch.setattr(jobs_engine, "db", SimpleNamespace(
        requests=requests, recommendations=SimpleNamespace(update_one=AsyncMock())))

    async def fake_submit(self, user_id, item, status="requested"):
        for row in requests.rows:
            if row["title"] == item["title"]:
                return dict(row)
        return {"id": "req_" + item["title"], "status": status}

    monkeypatch.setattr(request_providers.LocalRequestProvider, "submit", fake_submit)
    monkeypatch.setattr(mediamanager_client, "send_item_to_library", AsyncMock(return_value={"tmdb_id": 1}))


def test_a_run_reports_what_actually_reached_requests(monkeypatch):
    waiting = {"id": "req_Old", "title": "Old", "year": 2026, "status": "pending_approval"}
    _queue(monkeypatch, stored=[waiting], waiting=649)
    outcome = {}
    job = {"id": "job_tv", "action_mode": "require_approval", "final_recommendation_limit": 650}

    messages = asyncio.run(jobs_engine.apply_job_action_mode(
        "u1", job, _rows("Old", "New", "Later"), {}, outcome=outcome))

    # No cap holds a result back any more (Gilbert, 2026-09-29): both new titles
    # are queued and the waiting one is refreshed.
    assert outcome == {"mode": "require_approval", "limit": 650, "waiting": 651, "queued": 2,
                       "refreshed": 1, "held_back": 0, "sent": 0}
    assert messages == []


def test_a_run_with_room_queues_every_new_pick_and_says_nothing(monkeypatch):
    _queue(monkeypatch, waiting=37)
    outcome = {}
    job = {"id": "job_upcoming", "action_mode": "require_approval", "final_recommendation_limit": 650}

    messages = asyncio.run(jobs_engine.apply_job_action_mode(
        "u1", job, _rows(*"ABCDEFGHI"), {}, outcome=outcome))

    assert messages == []
    assert (outcome["queued"], outcome["held_back"], outcome["waiting"]) == (9, 0, 46)


def test_a_full_share_is_a_notice_and_a_provider_failure_stays_a_warning():
    full = {"code": "queue_full", "source": "requests", "detail": "full"}
    nothing_new = {"code": "no_new_picks", "source": "job", "detail": "nothing new"}
    failed = {"code": "tmdb_failed", "source": "tmdb", "detail": "timeout"}

    assert split_notices([full, failed, nothing_new]) == ([failed], [full, nothing_new])


def test_runtime_logs_list_a_full_share_as_okey_old_runs_included():
    from api_extra import _level_for_code

    assert _level_for_code("queue_full") == "ok"
    assert _level_for_code("no_new_picks") == "ok"
    assert _level_for_code("no_picks") == "warning"
    assert _level_for_code("tmdb_failed") == "bug"


def _tv_run_of_16_34():
    """Shaped like Tv at 16:34 UTC: 1,041 candidates, 413 outside the years, 18 below the floor."""
    rejected = ([{"filter_outcome": "rejected_already_requested"}] * 436
                + [{"filter_outcome": "rejected_year"}] * 413
                + [{"filter_outcome": "rejected_already_watched"}] * 96
                + [{"filter_outcome": "rejected_rating"}] * 58
                + [{"filter_outcome": "rejected_genre"}] * 19
                + [{"filter_outcome": "rejected_already_recommended"}] * 1)
    ranked = [{"title": f"Weak {index}", "personal_score": 1.9} for index in range(18)]
    return {"accepted": [], "rejected": rejected, "ranked": ranked, "taste_floor": 2.5, "below_taste_floor": 18}


def test_a_saturated_run_is_a_notice_that_names_what_really_held_it_back():
    rows = empty_result_warnings({"candidate_sources": ["tmdb_discover"]}, _tv_run_of_16_34(), [{"title": "x"}])

    assert [row["code"] for row in rows] == ["no_new_picks"]
    detail = rows[0]["detail"]
    assert detail.startswith("Nothing new this run: 0 of 1041 candidates picked")
    assert "436 already in Requests" in detail and "96 already watched" in detail
    assert "413 outside the year window" in detail and "58 below the minimum rating" in detail
    assert "18 met every setting but none had a close enough link to what you liked" in detail
    assert REJECTION_HINTS["rejected_year"] not in detail


def test_a_filter_that_removes_every_candidate_is_still_a_warning():
    result = {"accepted": [], "rejected": [{"filter_outcome": "rejected_year"}] * 12, "ranked": []}

    rows = empty_result_warnings({"candidate_sources": ["seed_expand"]}, result, [{"title": "x"}])

    assert [row["code"] for row in rows] == ["no_picks"]
    assert rows[0]["detail"] == ("0 of 12 candidates accepted: 12 outside the year window. "
                                 + REJECTION_HINTS["rejected_year"])


def test_a_hint_is_only_given_when_it_is_true():
    """"No candidate fell inside the year window" was said while 1 in 10 fell on genre."""
    result = {"accepted": [], "rejected": [{"filter_outcome": "rejected_year"}] * 9
              + [{"filter_outcome": "rejected_genre"}], "ranked": []}

    rows = empty_result_warnings({"candidate_sources": ["seed_expand"]}, result, [{"title": "x"}])

    assert [row["code"] for row in rows] == ["no_picks"]
    assert REJECTION_HINTS["rejected_year"] not in rows[0]["detail"]
    assert "9 outside the year window, 1 outside the job's genres" in rows[0]["detail"]
    assert "No candidate got past the job's settings" in rows[0]["detail"]
