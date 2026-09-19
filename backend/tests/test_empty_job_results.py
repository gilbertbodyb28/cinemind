"""The two ways a job used to report zero picks without saying why."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from jobs.engine import empty_result_warnings
from providers.tmdb import _window_is_upcoming, tmdb_discover
from recommendation.filter_engine import apply_filters


def _next_year() -> int:
    return datetime.now(timezone.utc).year + 1


def test_upcoming_window_is_recognised():
    assert _window_is_upcoming({"min_year": _next_year()}) is True
    assert _window_is_upcoming({"min_year": 2000}) is False
    assert _window_is_upcoming({}) is False
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
    assert _window_is_upcoming({"min_release_date": tomorrow}) is True


def test_rating_floor_does_not_reject_a_title_nobody_has_rated_yet():
    filters = {"min_rating": 7, "min_year": _next_year()}
    unreleased = {"title": "Neuromancer", "year": _next_year(), "tmdb_rating": 0.0, "vote_count": 0}
    assert apply_filters(unreleased, filters) == (True, None)


def test_rating_floor_still_rejects_a_title_that_was_rated_badly():
    filters = {"min_rating": 7}
    rated_low = {"title": "Shelf Filler", "year": 2020, "tmdb_rating": 4.2, "vote_count": 900}
    assert apply_filters(rated_low, filters) == (False, "rejected_rating")


def test_unrated_but_already_released_title_still_falls_on_the_rating_floor():
    filters = {"min_rating": 7}
    old_unrated = {"title": "Forgotten", "year": 1998, "tmdb_rating": 0.0, "vote_count": 0}
    assert apply_filters(old_unrated, filters) == (False, "rejected_rating")


def test_empty_run_names_the_filter_that_ate_everything():
    result = {
        "accepted": [],
        "rejected": [{"filter_outcome": "rejected_year"}] * 9 + [{"filter_outcome": "rejected_genre"}],
    }
    rows = empty_result_warnings({"candidate_sources": ["seed_expand"]}, result, [{"title": "x"}])
    assert [row["code"] for row in rows] == ["no_picks"]
    assert "9 fell on rejected_year" in rows[0]["detail"]
    assert "year window" in rows[0]["detail"]


def test_empty_run_flags_tmdb_when_it_returned_nothing_at_all():
    result = {"accepted": [], "rejected": []}
    rows = empty_result_warnings({"candidate_sources": ["tmdb_discover"]}, result, [])
    assert [row["code"] for row in rows] == ["tmdb_no_candidates"]


def test_a_run_that_picked_something_warns_about_nothing():
    result = {"accepted": [{"title": "Dune"}], "rejected": [{"filter_outcome": "rejected_year"}]}
    assert empty_result_warnings({"candidate_sources": ["tmdb_discover"]}, result, []) == []


def test_a_cursor_past_the_last_page_wraps_instead_of_returning_nothing():
    """The cursor advances every run; a filtered query may only have 2 pages.

    Asking TMDb for page 161 of a 2-page query answers with an empty body, so
    the job used to report zero candidates on every run from then on.
    """
    asked = []

    async def fake_page(path, params, api_key=None):
        asked.append(params["page"])
        if params["page"] > 2:
            return [], 2
        return [{"id": 100 + params["page"], "name": f"Show {params['page']}", "first_air_date": "2027-05-01"}], 2

    job = {"candidate_limit": 40, "final_recommendation_limit": 8, "filters": {"min_year": 2027}}
    with patch("providers.tmdb._tmdb_page", AsyncMock(side_effect=fake_page)):
        rows = asyncio.run(tmdb_discover(job, "tv", api_key="k", start_page=161))

    assert rows, "a cursor past the end must wrap, not strand the job"
    assert asked[0] == 161, "it still tries the stored cursor first"
    assert all(page <= 2 for page in asked[1:]), asked


def test_a_cursor_inside_the_range_is_used_as_is():
    asked = []

    async def fake_page(path, params, api_key=None):
        asked.append(params["page"])
        return [{"id": params["page"], "name": f"Show {params['page']}", "first_air_date": "2023-05-01"}], 500

    job = {"candidate_limit": 40, "final_recommendation_limit": 8, "filters": {"min_year": 2020}}
    with patch("providers.tmdb._tmdb_page", AsyncMock(side_effect=fake_page)):
        asyncio.run(tmdb_discover(job, "tv", api_key="k", start_page=7))

    assert asked[0] == 7
