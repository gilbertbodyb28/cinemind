"""A decision is about the title, not the row it was made on.

Measured 2026-09-25 on Gilbert's data:
- Home showed the Content to Watch list of 2026-09-22, with Hunter x Hunter
  (2011) - rated 10/10 on Trakt since - and Overlord IV, waiting in Requests.
- "Frieren" and "GIFT" waited in the queue next to a rejected copy of the same
  title (same TMDb id), and the job path looked up the waiting copy first, so
  the rejection did not stand for it.
- Nothing kept a title rejected on Home out of Up Coming under another job's row.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import jobs.engine as jobs_engine
import request_providers
from fake_mongo import fake_db
from recommendation.shown_picks import retire_settled, settled_context, settled_reason

FRIEREN_REJECTED = {"user_id": "u", "id": "req_rej", "title": "Frieren: Beyond Journey's End", "year": 2023,
                    "type": "anime", "tmdb_id": 209867, "status": "rejected", "rejected_at": "2026-09-24T10:00:00"}
FRIEREN_WAITING = {"user_id": "u", "id": "req_wait", "title": "Frieren", "year": 2023, "type": "anime",
                   "tmdb_id": 209867, "status": "pending_approval", "source_job_id": "job_tv"}


def test_a_rejection_outranks_a_waiting_copy_of_the_same_title():
    db = fake_db(requests=[FRIEREN_WAITING, FRIEREN_REJECTED])
    found = asyncio.run(request_providers.find_existing_request(
        "u", {"title": "Frieren", "year": 2023, "type": "anime", "tmdb_id": 209867}, database=db))
    assert found["id"] == "req_rej"


def test_a_waiting_title_with_no_decision_is_still_found_as_waiting():
    db = fake_db(requests=[FRIEREN_WAITING])
    found = asyncio.run(request_providers.find_existing_request(
        "u", {"title": "Frieren", "year": 2023, "type": "anime", "tmdb_id": 209867}, database=db))
    assert found["id"] == "req_wait"


def test_the_job_path_hides_a_pick_whose_title_was_rejected_under_another_row(monkeypatch):
    db = fake_db(requests=[FRIEREN_WAITING, FRIEREN_REJECTED],
                 recommendations=[{"user_id": "u", "id": "rec_1", "title": "Frieren"}])
    monkeypatch.setattr(jobs_engine, "db", db)
    submit = AsyncMock()
    monkeypatch.setattr(request_providers.LocalRequestProvider, "submit", submit)
    rows = [{"id": "rec_1", "title": "Frieren", "year": 2023, "type": "anime", "tmdb_id": 209867}]

    asyncio.run(jobs_engine.apply_job_action_mode(
        "u", {"id": "job_tv", "action_mode": "require_approval", "final_recommendation_limit": 250}, rows, {}))

    submit.assert_not_awaited()
    assert db.recommendations.rows[0]["dismissed"] is True


def test_settling_archives_waiting_copies_but_not_a_different_title_sharing_a_wrong_id():
    decided = {"user_id": "u", "id": "req_gift", "title": "Gift", "year": 2026, "type": "show", "tmdb_id": 314647,
               "status": "rejected"}
    waiting = {"user_id": "u", "id": "req_GIFT", "title": "GIFT", "year": 2026, "type": "show", "tmdb_id": 314647,
               "status": "pending_approval"}
    kenichi = {"user_id": "u", "id": "req_ken", "title": "Kenichi: The Mightiest Disciple", "year": 2006,
               "type": "anime", "tmdb_id": 37585, "status": "rejected"}
    railroad = {"user_id": "u", "id": "req_rail", "title": "Night on the Galactic Railroad", "year": 1985,
                "type": "anime", "tmdb_id": 37585, "status": "pending_approval"}
    db = fake_db(requests=[decided, waiting, kenichi, railroad])

    assert asyncio.run(request_providers.settle_duplicates("u", decided, "duplicate_of_rejected", database=db)) == ["req_GIFT"]
    assert asyncio.run(request_providers.settle_duplicates("u", kenichi, "duplicate_of_rejected", database=db)) == []

    rows = {row["id"]: row for row in db.requests.rows}
    assert rows["req_GIFT"]["status"] == "archived"
    assert (rows["req_GIFT"]["archived_from_status"], rows["req_GIFT"]["archived_reason"],
            rows["req_GIFT"]["archive_batch"]) == ("pending_approval", "duplicate_of_rejected", "dup:req_gift")
    assert rows["req_rail"]["status"] == "pending_approval"


def test_a_settled_copy_can_be_put_back_exactly(monkeypatch):
    import database
    from evaluation import queue_cleanup

    decided = {"user_id": "u", "id": "req_gift", "title": "Gift", "year": 2026, "type": "show", "tmdb_id": 314647,
               "status": "rejected"}
    waiting = {"_id": 7, "user_id": "u", "id": "req_GIFT", "title": "GIFT", "year": 2026, "type": "show",
               "tmdb_id": 314647, "status": "pending_approval", "updated_at": "2026-09-22T12:00:00"}
    db = fake_db(requests=[decided, waiting])
    monkeypatch.setattr(database, "db", db)
    asyncio.run(request_providers.settle_duplicates("u", decided, "duplicate_of_rejected", database=db))
    assert asyncio.run(queue_cleanup.revert("u", "dup:req_gift"))["restored"] == 1
    assert {row["id"]: row for row in db.requests.rows}["req_GIFT"] == waiting  # queue place and all


@pytest.fixture
def app(monkeypatch):
    import database
    import server

    def use(**collections):
        db = fake_db(**collections)
        monkeypatch.setattr(server, "db", db)
        monkeypatch.setattr(database, "db", db)
        return db

    async def same(_user_id, docs):
        return docs

    monkeypatch.setattr(server, "backfill_posters", same)
    return SimpleNamespace(server=server, use=use, user=SimpleNamespace(user_id="u"))


def test_rejecting_in_requests_takes_the_waiting_copy_out_of_the_queue(app):
    db = app.use(requests=[{**FRIEREN_REJECTED, "status": "pending_approval"}, FRIEREN_WAITING])
    result = asyncio.run(app.server.reject_request("req_rej", user=app.user))
    rows = {row["id"]: row for row in db.requests.rows}
    assert result["duplicates_archived"] == ["req_wait"]
    assert rows["req_rej"]["status"] == "rejected" and rows["req_wait"]["status"] == "archived"


def test_a_home_rejection_without_a_queue_row_takes_the_title_out_of_the_queue(app):
    pick = {"user_id": "u", "id": "rec_ctw", "job_id": "content_to_watch:u", "title": "Frieren", "year": 2023,
            "type": "anime", "tmdb_id": 209867}
    db = app.use(recommendations=[pick], requests=[FRIEREN_WAITING])
    result = asyncio.run(app.server.dismiss_rec("rec_ctw", user=app.user))
    assert result["duplicates_archived"] == ["req_wait"]
    assert db.recommendations.rows[0]["dismissed"] is True  # kept: the memory of the decision


def test_an_approval_sets_aside_the_waiting_copy(app, monkeypatch):
    from fastapi import HTTPException

    db = app.use(requests=[{**FRIEREN_REJECTED, "status": "pending_approval"}, FRIEREN_WAITING])

    async def mediamanager_down(*_args, **_kwargs):
        raise HTTPException(status_code=502, detail="MediaManager unreachable")

    async def item(_user_id, doc):
        return doc

    monkeypatch.setattr(app.server, "send_title_to_mediamanager", mediamanager_down)
    monkeypatch.setattr(app.server, "library_item_from_request", item)
    result = asyncio.run(app.server.approve_request("req_rej", user=app.user))
    rows = {row["id"]: row for row in db.requests.rows}
    assert rows["req_rej"]["status"] == "approved"  # the decision sticks without MediaManager
    assert result["duplicates_archived"] == ["req_wait"] and rows["req_wait"]["archived_reason"] == "duplicate_of_approved"


HXH = {"title": "Hunter x Hunter (2011)", "year": 2011, "type": "anime", "media_type": "anime", "tmdb_id": 46298}


def _context(**collections):
    return asyncio.run(settled_context("u", fake_db(**collections)))


def test_a_pick_rated_since_is_settled():
    context = _context(media_history=[{"user_id": "u", "title": "Hunter x Hunter", "year": 2011, "type": "show",
                                       "tmdb_id": 46298, "rating": 10.0}])
    assert settled_reason({"id": "r1", **HXH}, context) == "already_watched"


def test_a_pick_waiting_in_requests_under_another_job_is_settled_but_its_own_row_is_not():
    overlord = {"title": "Overlord IV", "year": 2022, "type": "anime", "tmdb_id": 64196}
    context = _context(requests=[{"user_id": "u", "id": "req_job", "recommendation_id": "rec_job",
                                  "status": "pending_approval", **overlord}])
    assert settled_reason({"id": "rec_home", **overlord}, context) == "already_requested"
    assert settled_reason({"id": "rec_job", "request_id": "req_job", **overlord}, context) is None


def test_a_title_rejected_on_one_list_is_settled_on_every_other():
    context = _context(recommendations=[{"user_id": "u", "id": "rec_old", "dismissed": True, **HXH}])
    assert settled_reason({"id": "rec_other", **HXH}, context) == "rejected_elsewhere"
    assert settled_reason({"id": "rec_old", **HXH}, context) is None  # its own row is the decision


def test_a_failed_delivery_does_not_settle_a_title():
    context = _context(requests=[{"user_id": "u", "id": "req_f", "status": "request_failed", **HXH}])
    assert settled_reason({"id": "r1", **HXH}, context) is None


def test_settled_picks_are_retired_on_their_rows_and_saved_ones_stay():
    db = fake_db(
        media_history=[{"user_id": "u", "title": "Hunter x Hunter", "year": 2011, "type": "show",
                        "tmdb_id": 46298, "rating": 10.0}],
        recommendations=[
            {"user_id": "u", "id": "r_hxh", **HXH},
            {"user_id": "u", "id": "r_saved", **HXH, "saved": True},
            {"user_id": "u", "id": "r_open", "title": "Willow", "year": 2022, "type": "show", "tmdb_id": 111837},
        ],
    )
    picks = [dict(row) for row in db.recommendations.rows]
    kept = asyncio.run(retire_settled("u", picks, db))
    rows = {row["id"]: row for row in db.recommendations.rows}
    assert [pick["id"] for pick in kept] == ["r_saved", "r_open"]
    assert (rows["r_hxh"]["retired"], rows["r_hxh"]["retired_reason"]) == (True, "already_watched")
    assert "retired" not in rows["r_open"]


def test_home_leaves_out_what_was_settled_since_the_list_was_made(app):
    app.use(
        media_history=[{"user_id": "u", "title": "Hunter x Hunter", "year": 2011, "type": "show",
                        "tmdb_id": 46298, "rating": 10.0}],
        recommendations=[
            {"user_id": "u", "id": "r1", "job_id": "content_to_watch:u", "rank": 1, **HXH},
            {"user_id": "u", "id": "r2", "job_id": "content_to_watch:u", "rank": 2, "title": "Willow",
             "year": 2022, "type": "show", "tmdb_id": 111837},
        ],
    )
    shown = asyncio.run(app.server.list_recs(user=app.user))
    assert [pick["id"] for pick in shown] == ["r2"]


def test_up_coming_leaves_out_a_title_rejected_under_another_jobs_row(app, monkeypatch):
    import providers.premieres as premieres

    coming = {"title": "The Rings of Power", "year": 2022, "type": "show", "tmdb_id": 84773}
    app.use(
        jobs=[{"user_id": "u", "id": "job_up", "enabled": True}],
        recommendations=[
            {"user_id": "u", "id": "r_home", "job_id": "content_to_watch:u", "dismissed": True, **coming},
            {"user_id": "u", "id": "r_job", "job_id": "job_up", **coming},
            {"user_id": "u", "id": "r_new", "job_id": "job_up", "title": "Silo", "year": 2023, "type": "show",
             "tmdb_id": 125988},
        ],
    )

    async def verified(rows, _key, now=None):
        for row in rows:
            row.update({"premiere_date": "2027-07-08", "premiere_kind": "season_premiere",
                        "premiere_checked_at": now.isoformat()})
        return len(rows)

    monkeypatch.setattr(premieres, "verify_premieres", verified)
    shown = asyncio.run(app.server.upcoming_premieres(limit=12, user=app.user))
    assert [pick["id"] for pick in shown] == ["r_new"]


GOOD_BOY_FILM = {"user_id": "u", "id": "req_film", "title": "Good Boy", "year": 2026, "type": "movie",
                 "tmdb_id": 1381027, "status": "pending_approval", "match_score": 80, "recommendation_id": "rec_film"}
GOOD_BOY_SERIES = {"title": "Good Boy", "year": 2026, "type": "show", "media_type": "tv", "tmdb_id": 306611,
                   "match_score": 68, "recommendation_id": "rec_series", "source_job_id": "job_up"}


def test_a_series_is_not_the_queued_film_of_the_same_name_and_year():
    db = fake_db(requests=[GOOD_BOY_FILM])
    assert asyncio.run(request_providers.find_existing_request("u", GOOD_BOY_SERIES, database=db)) == {}


def test_queueing_the_series_leaves_the_films_row_alone(monkeypatch):
    db = fake_db(requests=[GOOD_BOY_FILM])
    monkeypatch.setattr(request_providers, "db", db)
    stored = asyncio.run(request_providers.LocalRequestProvider().submit("u", GOOD_BOY_SERIES, "pending_approval"))
    rows = {row["id"]: row for row in db.requests.rows}
    assert stored["id"] != "req_film" and len(rows) == 2
    film = rows["req_film"]
    assert (film["match_score"], film["recommendation_id"]) == (80, "rec_film") and "last_suggested_at" not in film
