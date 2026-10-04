"""A job's minimum rating is for titles that are out; a title still to come has none.

Gilbert, 2026-09-29: "nuvarande släppta serier anime filmer som redan har släppt
ska ha ratings på 8.0 medans kommande ska ha 0.0 på alla jobs". A coming title
used to be spared the floor only while nobody at all had voted on it, and TMDb
was asked with vote_average.gte for the whole window, which no unreleased title
(vote_average 0.0) can meet.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import providers.tmdb as tmdb
from recommendation.filter_engine import apply_filters, not_released_yet

TODAY = datetime.now(timezone.utc).date()
THIS_YEAR = TODAY.year
FLOOR = {"min_rating": 8.0}


def _day(offset):
    return (TODAY + timedelta(days=offset)).isoformat()


def _film(title, rating, votes, release, **extra):
    return {"title": title, "media_type": "movie", "type": "movie", "year": int(release[:4]),
            "release_date": release, "tmdb_rating": rating, "vote_count": votes, **extra}


def _passes(row, **filters):
    return apply_filters(row, {**FLOOR, **filters})


# --- out: held to the floor -----------------------------------------------------

def test_a_released_title_needs_the_minimum_rating():
    assert _passes(_film("Almost", 7.9, 900, "2024-05-01")) == (False, "rejected_rating")
    assert _passes(_film("Exactly", 8.0, 900, "2024-05-01")) == (True, None)
    assert _passes(_film("Loved", 8.6, 4000, "2019-11-12")) == (True, None)
    # A released title nobody rated still falls on the floor.
    assert _passes(_film("Forgotten", 0.0, 0, "2021-02-02")) == (False, "rejected_rating")
    assert _passes({"title": "No rating", "media_type": "tv", "year": 2020, "rating": None}) == (False, "rejected_rating")


def test_a_premiere_that_has_happened_is_out():
    series = {"title": "Aired", "media_type": "tv", "year": 2019, "first_air_date": "2019-03-01",
              "premiere_date": _day(-1), "tmdb_rating": 7.2, "vote_count": 3000}
    assert _passes(series) == (False, "rejected_rating")


# --- still to come: no floor ----------------------------------------------------

def test_a_coming_title_is_never_held_to_the_floor():
    early_votes = _film("Festival Darling", 6.1, 12, _day(30))
    nobody_voted = {"title": "Next Year", "media_type": "tv", "year": THIS_YEAR + 1, "tmdb_rating": 0.0, "vote_count": 0}
    assert _passes(early_votes) == (True, None)
    assert _passes(nobody_voted) == (True, None)


def test_a_coming_season_of_an_older_series_is_still_to_come():
    season = {"title": "Returning", "media_type": "tv", "year": 2019, "first_air_date": "2019-03-01",
              "premiere_date": _day(40), "tmdb_rating": 7.2, "vote_count": 3000}
    assert not_released_yet(season)
    assert _passes(season, upcoming_only=True) == (True, None)
    assert _passes(season) == (True, None)


def test_rows_with_only_a_year_or_a_status():
    # Trakt's anticipated list: this year, nobody has rated it yet.
    anticipated = {"title": "Coming Soon", "media_type": "movie", "year": THIS_YEAR, "tmdb_rating": None,
                   "vote_count": 0, "source": "trakt"}
    out_this_year = {"title": "Out This Year", "media_type": "movie", "year": THIS_YEAR, "tmdb_rating": 6.0,
                     "vote_count": 2000, "source": "trakt"}
    announced = {"title": "Announced", "media_type": "anime", "year": THIS_YEAR, "status": "NOT_YET_RELEASED"}
    assert _passes(anticipated) == (True, None)
    assert _passes(out_this_year) == (False, "rejected_rating")
    assert _passes(announced) == (True, None)


def test_anilist_rows_are_held_to_anilists_own_score():
    def anilist(score):
        return {"title": "Anime", "media_type": "anime", "type": "anime", "year": 2024, "source": "anilist",
                "genres": ["Action"], "candidate_score": score}

    assert _passes(anilist(8.4)) == (True, None)
    assert _passes(anilist(7.1)) == (False, "rejected_rating")


# --- where the jobs look on TMDb -------------------------------------------------

def test_a_window_reaching_past_today_is_asked_in_two_parts():
    released, coming = tmdb.rating_windows({"min_rating": 8.0, "min_year": 2018, "max_year": 2029})
    assert released["min_rating"] == 8.0 and released["max_release_date"] == TODAY.isoformat()
    assert coming["min_release_date"] == _day(1)
    assert coming["min_rating"] is None and coming["min_vote_count"] is None
    assert tmdb.default_vote_floor(coming) is None
    # All out, all to come, or no floor at all: one query as before.
    assert tmdb.rating_windows({"min_rating": 8.0, "min_year": 2018, "max_year": 2020}) == [
        {"min_rating": 8.0, "min_year": 2018, "max_year": 2020}]
    assert tmdb.rating_windows({"min_rating": 8.0, "min_year": THIS_YEAR + 1}) == [
        {"min_rating": 8.0, "min_year": THIS_YEAR + 1}]
    assert tmdb.rating_windows({"min_year": 2018}) == [{"min_year": 2018}]
    # An upcoming job's lane of coming episodes (new seasons) is all still to come.
    assert tmdb.rating_windows({"min_rating": 8.0, "air_date_from": _day(1)}) == [
        {"min_rating": None, "air_date_from": _day(1)}]


def test_discover_asks_for_what_is_out_with_the_floor_and_for_what_is_coming_without(monkeypatch):
    calls = []

    async def fake_page(path, params, api_key=None):
        calls.append(dict(params))
        return [], 0

    monkeypatch.setattr(tmdb, "_tmdb_page", fake_page)
    job = {"candidate_limit": 40, "filters": {"min_rating": 8.0, "min_year": 2018, "max_year": 2029}}
    asyncio.run(tmdb.tmdb_discover(job, "tv", api_key="k"))
    out = [call for call in calls if call.get("vote_average.gte") == 8.0]
    coming = [call for call in calls if call.get("first_air_date.gte") == _day(1)]
    assert out and all(call["first_air_date.lte"] == TODAY.isoformat() for call in out)
    assert coming and all("vote_average.gte" not in call and "vote_count.gte" not in call for call in coming)
    assert coming[0]["first_air_date.lte"] == "2029-12-31"
