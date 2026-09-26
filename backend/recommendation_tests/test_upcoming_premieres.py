"""Upcoming = a verified premiere after today, a new season of an older series included.

Gilbert's decision of 2026-09-25 (HANDOFF.md, omgång 6). "Up Coming" on Home
showed Content to Watch picks 2-5, and "Upcoming Tv Shows" took anything first
released inside 2026-2029, including series that premiered in March.
"""

from datetime import datetime, timezone

from providers.premieres import premiere_from_anilist, premiere_from_tmdb, premiere_label, premiere_window
from providers.tmdb import upcoming_lanes
from recommendation.filter_engine import apply_filters

TODAY = "2026-09-25"


def test_a_film_counts_only_with_a_full_release_day_after_today():
    assert premiere_from_tmdb({"release_date": "2026-12-18"}, False, TODAY)["premiere_kind"] == "film_release"
    assert premiere_from_tmdb({"release_date": "2026-09-25"}, False, TODAY) is None
    assert premiere_from_tmdb({"release_date": "2027"}, False, TODAY) is None


def test_a_new_season_of_an_older_series_is_a_premiere():
    details = {
        "first_air_date": "2019-12-20",
        "next_episode_to_air": {"air_date": "2027-03-04", "episode_number": 1, "season_number": 5},
        "seasons": [{"season_number": 0, "air_date": "2030-01-01"},
                    {"season_number": 4, "air_date": "2025-10-30"},
                    {"season_number": 5, "air_date": "2027-03-04"}],
    }
    found = premiere_from_tmdb(details, True, TODAY)
    assert found == {"premiere_date": "2027-03-04", "premiere_kind": "season_premiere", "premiere_season": 5,
                     "premiere_source": "tmdb:next_episode_to_air", "premiere_precision": "day"}


def test_the_next_weekly_episode_of_a_running_season_is_not_a_premiere():
    details = {
        "first_air_date": "2024-01-10",
        "next_episode_to_air": {"air_date": "2026-10-01", "episode_number": 6, "season_number": 3},
        "seasons": [{"season_number": 3, "air_date": "2026-08-27"}],
    }
    assert premiere_from_tmdb(details, True, TODAY) is None


def test_a_series_premiere_after_today():
    found = premiere_from_tmdb({"first_air_date": "2026-11-02", "seasons": []}, True, TODAY)
    assert (found["premiere_kind"], found["premiere_season"]) == ("series_premiere", 1)


def test_anilist_needs_episode_one_or_an_announced_start():
    stamp = int(datetime(2026, 10, 3, 15, tzinfo=timezone.utc).timestamp())
    assert premiere_from_anilist({"nextAiringEpisode": {"airingAt": stamp, "episode": 1}}, TODAY)["premiere_date"] == "2026-10-03"
    assert premiere_from_anilist({"nextAiringEpisode": {"airingAt": stamp, "episode": 7}}, TODAY) is None
    found = premiere_from_anilist(
        {"status": "NOT_YET_RELEASED", "startDate": {"year": 2027, "month": 4, "day": 5}}, TODAY)
    assert (found["premiere_date"], found["premiere_precision"]) == ("2027-04-05", "day")
    # No year announced, or not NOT_YET_RELEASED: nothing to place.
    assert premiere_from_anilist({"status": "NOT_YET_RELEASED", "startDate": {}}, TODAY) is None
    assert premiere_from_anilist({"status": "RELEASING", "startDate": {"year": 2027, "month": 4}}, TODAY) is None


def test_an_anime_announced_by_month_or_year_is_coming():
    """Gilbert, 2026-09-26: a year or a month counts. Frieren S3 (October 2026) and
    Demon Slayer's Infinity Castle part 2 (a year) were never upcoming before."""
    month = premiere_from_anilist({"status": "NOT_YET_RELEASED", "format": "TV", "startDate": {"year": 2026, "month": 10}}, TODAY)
    assert (month["premiere_date"], month["premiere_precision"], month["premiere_kind"]) == ("2026-10-31", "month", "series_premiere")
    year = premiere_from_anilist({"status": "NOT_YET_RELEASED", "format": "MOVIE", "startDate": {"year": 2027}}, TODAY)
    assert (year["premiere_date"], year["premiere_precision"], year["premiere_kind"]) == ("2027-12-31", "year", "film_release")
    # February of a leap year ends on the 29th; this month still counts while it is not out.
    assert premiere_from_anilist({"status": "NOT_YET_RELEASED", "startDate": {"year": 2028, "month": 2}}, TODAY)["premiere_date"] == "2028-02-29"
    assert premiere_from_anilist({"status": "NOT_YET_RELEASED", "startDate": {"year": 2026, "month": 9}}, TODAY)["premiere_date"] == "2026-09-30"
    # A stale announcement for a period already over is not coming.
    assert premiere_from_anilist({"status": "NOT_YET_RELEASED", "startDate": {"year": 2026, "month": 8}}, TODAY) is None
    assert premiere_from_anilist({"status": "NOT_YET_RELEASED", "startDate": {"year": 2025}}, TODAY) is None
    assert [premiere_label(month), premiere_label(year)] == ["October 2026", "2027"]


def test_an_announced_premiere_is_inside_an_upcoming_jobs_window():
    assert apply_filters(_series(premiere_date="2029-12-31", premiere_precision="year"), FILTERS) == (True, None)
    assert apply_filters(_series(premiere_date="2030-12-31", premiere_precision="year"), FILTERS) == (False, "rejected_year")


def test_tmdb_without_a_date_asks_anilist(monkeypatch):
    """TMDb lists a coming anime season without a date; AniList has its month."""
    import asyncio
    from providers import premieres

    async def details(client, tmdb_id, series, key, store=True):
        return {"first_air_date": "2023-09-29", "seasons": [{"season_number": 3, "air_date": None}]}

    async def media(ids, store=True):
        return {154587: {"id": 154587, "status": "NOT_YET_RELEASED", "format": "TV", "startDate": {"year": 2026, "month": 10}}}

    monkeypatch.setattr(premieres, "_tmdb_details", details)
    monkeypatch.setattr(premieres, "_anilist_media", media)
    rows = [{"title": "Frieren", "type": "anime", "tmdb_id": 209867, "anilist_id": 154587},
            {"title": "Other", "type": "show", "tmdb_id": 1}]
    found = asyncio.run(premieres.verify_premieres(rows, "key", now=datetime(2026, 9, 26, tzinfo=timezone.utc)))
    assert found == 1
    assert (rows[0]["premiere_date"], rows[0]["premiere_precision"]) == ("2026-10-31", "month")
    assert rows[1]["premiere_date"] is None and rows[1]["premiere_precision"] is None


def _series(**extra):
    return {"title": "The Witcher", "year": 2019, "type": "show", "media_type": "tv", "genres": ["Fantasy"], **extra}


FILTERS = {"upcoming_only": True, "min_year": 2026, "max_year": 2029, "media_types": ["tv"]}


def test_an_upcoming_job_measures_its_window_on_the_premiere():
    ok, reason = apply_filters(_series(premiere_date="2027-03-04"), FILTERS)
    assert (ok, reason) == (True, None)
    # The same series without a verified coming season is not upcoming at all ...
    assert apply_filters(_series(), FILTERS) == (False, "rejected_not_upcoming")
    # ... and neither is a series that premiered earlier this year.
    assert apply_filters(_series(year=2026, premiere_date="2026-03-01"), FILTERS) == (False, "rejected_not_upcoming")
    # A premiere after the window is outside it.
    assert apply_filters(_series(premiere_date="2031-01-10"), FILTERS) == (False, "rejected_year")


def test_a_job_without_the_setting_keeps_its_year_filter():
    filters = {"min_year": 2026, "max_year": 2029, "media_types": ["tv"]}
    assert apply_filters(_series(premiere_date="2027-03-04"), filters) == (False, "rejected_year")


def test_upcoming_lanes_start_tomorrow_and_add_a_returning_series_lane():
    window = premiere_window({"min_year": 2026, "max_year": 2029}, now=datetime(2026, 9, 25, tzinfo=timezone.utc))
    assert window == {"from": "2026-09-26", "to": "2029-12-31"}
    premiering, returning = upcoming_lanes({"min_year": 2026, "max_year": 2029, "include_genres": ["fantasy"]}, "tv", window)
    assert premiering == {"include_genres": ["fantasy"], "min_release_date": "2026-09-26", "max_release_date": "2029-12-31"}
    assert returning == {"include_genres": ["fantasy"], "air_date_from": "2026-09-26", "air_date_to": "2029-12-31"}
    assert len(upcoming_lanes({"min_year": 2026}, "movie", window)) == 1


def test_the_queue_clean_up_verifies_an_upcoming_jobs_premieres_before_judging_them(monkeypatch):
    """A queue row carries no premiere date. `queue_cleanup plan` read every waiting
    series of "Upcoming Tv Shows" as not upcoming (271 of 534 rows, 2026-09-25) and
    proposed archiving titles premiering next month. It now asks TMDb first - without
    writing, not even the cache - and leaves a row the provider did not answer for."""
    import asyncio

    import database
    import providers.premieres as premieres
    from evaluation import queue_cleanup
    from fake_mongo import fake_db

    job = {"user_id": "u", "id": "job_up", "name": "Upcoming Tv Shows", "enabled": True, "media_types": ["tv"],
           "filters": {"upcoming_only": True, "min_year": 2026, "max_year": 2029, "include_genres": ["fantasy"]}}

    def waiting(row_id, tmdb_id, **extra):
        return {"user_id": "u", "id": row_id, "title": row_id, "year": 2026, "type": "show", "tmdb_id": tmdb_id,
                "genres": ["Fantasy"], "status": "pending_approval", "source_job_id": "job_up", **extra}

    db = fake_db(jobs=[job], connections=[{"user_id": "u", "tmdb_api_key": "key"}], requests=[
        waiting("coming", 1), waiting("aired", 2), waiting("no_answer", 3), waiting("film", 4, type="movie"),
    ])
    monkeypatch.setattr(database, "db", db)
    answers = {1: {"first_air_date": "2027-02-01", "seasons": []}, 2: {"first_air_date": "2026-03-01", "seasons": []}}

    async def details(_client, tmdb_id, _series, _key, store=True):
        assert store is False
        return answers.get(tmdb_id)  # 3: TMDb did not answer

    async def no_writes(*_args, **_kwargs):
        raise AssertionError("plan must not write provider_cache")

    monkeypatch.setattr(premieres, "_tmdb_details", details)
    monkeypatch.setattr(premieres, "_store", no_writes)

    report = asyncio.run(queue_cleanup.plan("u", ["fails_job_filters"], 30))

    assert sorted(row["id"] for row in report["rows"]) == ["aired", "film"]
    assert report["pending_after"] == 2
    assert all(row["status"] == "pending_approval" for row in db.requests.rows)
