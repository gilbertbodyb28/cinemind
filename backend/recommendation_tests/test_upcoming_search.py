"""Upcoming is the first priority and never stops at too few picks (Gilbert, 2026-09-25).

Measured that evening on "Upcoming Tv Shows" before the change: 1,166
candidates, 229 verified premieres, 45 past the job's settings, 1 over the
taste floor. The coming seasons of his own series (The Simpsons S38, Delicious
in Dungeon S2 ...) were "already watched", AniList sequels to anime he had
watched had no link to them (specific 0.0), sequels of liked films were never
searched, and each run replaced the job's list, so Up Coming shrank to the
newest run's picks. After it, the same job traced 11 picks and "Upcoming US +
Anime/Donghua" 34 (Avengers: Doomsday, Dune: Part Three, The Rookie S9 ...).
"""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import jobs.engine as jobs_engine
import jobs.upcoming as upcoming
import providers.continuations as cont
import request_providers
import request_queue as rq
from fake_mongo import fake_db

NOW = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
TODAY = "2026-09-25"
SIMPSONS = {"title": "The Simpsons", "year": 1989, "score": 4.24, "rating": 10.0, "media_type": "tv",
            "type": "show", "tmdb_id": 456, "genres": ["Animation", "Comedy"]}


def run(coro):
    return asyncio.run(coro)


# --- providers.continuations ---------------------------------------------------


def test_a_liked_series_with_an_announced_season_is_a_continuation(monkeypatch):
    approved_unaired = {"title": "Avatar: Seven Havens", "year": 2026, "score": 2.0, "decision": "approved",
                        "media_type": "tv", "tmdb_id": 999}
    details = {
        456: {"first_air_date": "1989-12-17",
              "next_episode_to_air": {"air_date": "2026-09-27", "episode_number": 1, "season_number": 38},
              "seasons": [{"season_number": 38, "air_date": "2026-09-27"}]},
        999: {"first_air_date": "2026-10-09", "next_episode_to_air": {}, "seasons": []},
    }

    async def fake_details(_client, tmdb_id, _series, _key, store=True):
        return details[tmdb_id]

    monkeypatch.setattr(cont, "_tmdb_details", fake_details)
    rows = run(cont.series_continuations([SIMPSONS, approved_unaired], "key", NOW))

    assert [row["title"] for row in rows] == ["The Simpsons"]
    row = rows[0]
    assert row["continuation_of"]["relation"] == "season" and row["continuation_of"]["season"] == 38
    assert (row["premiere_date"], row["premiere_kind"], row["source"]) == ("2026-09-27", "season_premiere", "continuation")
    assert row["tmdb_id"] == 456 and row["type"] == "show"


def test_a_coming_film_in_a_liked_films_collection_is_a_sequel(monkeypatch):
    dune = {"title": "Dune", "year": 2021, "score": 1.82, "media_type": "movie", "type": "movie", "tmdb_id": 438631}
    sonic = {"title": "Sonic the Hedgehog 4", "year": 2027, "score": 2.0, "decision": "approved",
             "media_type": "movie", "tmdb_id": 1079091}

    async def fake_collection(_client, tmdb_id, _key):
        return {438631: {"id": 10, "name": "Dune Collection"}, 1079091: {"id": 20, "name": "Sonic Collection"}}[tmdb_id]

    async def fake_parts(_client, collection_id, _key):
        if collection_id == 20:
            return [{"id": 1079091, "title": "Sonic the Hedgehog 4", "release_date": "2027-03-11"}]
        return [{"id": 438631, "title": "Dune", "release_date": "2021-09-15"},
                {"id": 693134, "title": "Dune: Part Two", "release_date": "2024-02-27"},
                {"id": 1170608, "title": "Dune: Part Three", "release_date": "2026-12-15", "genre_ids": [878, 12],
                 "original_language": "en", "poster_path": "/dune3.jpg"}]

    monkeypatch.setattr(cont, "_film_collection", fake_collection)
    monkeypatch.setattr(cont, "_collection_parts", fake_parts)
    rows = run(cont.film_continuations([dune, sonic], "key", NOW))

    assert [row["title"] for row in rows] == ["Dune: Part Three"]
    row = rows[0]
    assert row["continuation_of"]["title"] == "Dune" and row["continuation_of"]["relation"] == "sequel"
    assert (row["collection"], row["premiere_kind"], row["type"]) == ("Dune Collection", "film_release", "movie")


def _relations(graph):
    async def fake(ids):
        return {media_id: graph[media_id] for media_id in ids if media_id in graph}
    return fake


def test_an_anime_chain_is_followed_through_what_has_aired(monkeypatch):
    graph = {
        1: {"edges": [{"relation": "SEQUEL", "node": {"id": 2, "type": "ANIME", "status": "FINISHED",
                                                      "title": {"english": "Frontier Season 2"}}}]},
        2: {"edges": [{"relation": "SEQUEL", "node": {
            "id": 3, "type": "ANIME", "status": "NOT_YET_RELEASED", "format": "TV", "countryOfOrigin": "JP",
            "startDate": {"year": 2027, "month": 1, "day": 17}, "title": {"english": "Frontier Season 3"},
            "genres": ["Action"]}}]},
    }
    monkeypatch.setattr(cont, "_relations", _relations(graph))
    root = {"title": "Frontier", "score": 3.0, "media_type": "anime"}

    rows = run(cont.anime_continuations({1: root}, seen_ids=[1], now=NOW))
    assert [(row["anilist_id"], row["premiere_date"]) for row in rows] == [(3, "2027-01-17")]
    assert rows[0]["continuation_of"]["title"] == "Frontier"
    assert run(cont.anime_continuations({1: root}, seen_ids=[1, 3], now=NOW)) == []


def test_an_anilist_sequel_is_tied_to_the_liked_title_it_continues_by_name(monkeypatch):
    """AniList writes "Ranma1/2 (2024)"; the liked row came from Trakt as "Ranma1/2"."""
    graph = {30: {"edges": [{"relation": "PREQUEL", "node": {"id": 20, "type": "ANIME",
                                                             "title": {"english": "Ranma1/2 (2024) Season 2"}}}]}}
    monkeypatch.setattr(cont, "_relations", _relations(graph))
    rows = [{"title": "Ranma1/2 (2024) Season 3", "anilist_id": 30, "premiere_date": "2026-10-03", "type": "anime"},
            {"title": "Unrelated", "anilist_id": 40, "premiere_date": "2026-10-03", "type": "anime"}]
    roots = [{"title": "Ranma1/2", "score": 1.86, "media_type": "anime", "tmdb_id": 259140}]

    assert run(cont.link_anilist_candidates(rows, roots, NOW)) == 1
    assert rows[0]["continuation_of"]["title"] == "Ranma1/2"
    assert "continuation_of" not in rows[1]


def test_an_announced_anime_film_continues_its_franchise_and_borrows_what_it_is_about(monkeypatch):
    """2026-09-26: ONE PIECE FILM: GOD VALLEY was tied to ONE PIECE (10/10) but had no
    genres and scored 0.85 against a 2.5 floor; SAO: Integral Domain's prequel is
    "Sword Art Online: Alicization - War of Underworld Part 2", a season of the
    liked "Sword Art Online", and was never tied at all."""
    graph = {
        1: {"edges": [{"relation": "PARENT", "node": {"id": 10, "type": "ANIME", "title": {"romaji": "ONE PIECE"}}}]},
        2: {"edges": [{"relation": "PREQUEL", "node": {"id": 20, "type": "ANIME", "title": {
            "english": "Sword Art Online: Alicization - War of Underworld Part 2"}}}]},
        3: {"edges": [{"relation": "PREQUEL", "node": {"id": 30, "type": "ANIME", "title": {"english": "Re:Zero"}}}]},
    }
    monkeypatch.setattr(cont, "_relations", _relations(graph))
    rows = [{"title": "ONE PIECE FILM: GOD VALLEY", "anilist_id": 1, "premiere_date": "2027-12-31", "type": "anime",
             "format": "MOVIE", "genres": []},
            {"title": "Sword Art Online: Integral Domain", "anilist_id": 2, "premiere_date": "2028-12-31",
             "type": "anime", "format": "MOVIE", "genres": ["Action"]},
            {"title": "Re:Zero Movie", "anilist_id": 3, "premiere_date": "2027-12-31", "type": "anime"}]
    roots = [{"title": "One Piece", "score": 2.0, "media_type": "anime", "genres": ["Action", "Adventure"],
              "tmdb_keywords": ["pirate"]},
             {"title": "Sword Art Online", "score": 1.9, "media_type": "anime", "genres": ["Sci-Fi"]},
             {"title": "Re", "score": 1.0, "media_type": "anime"}]

    assert run(cont.link_anilist_candidates(rows, roots, NOW)) == 2
    assert rows[0]["continuation_of"]["title"] == "One Piece"
    assert rows[0]["genres"] == ["Action", "Adventure"] and rows[0]["tmdb_keywords"] == ["pirate"]
    assert rows[1]["continuation_of"]["title"] == "Sword Art Online"
    assert rows[1]["genres"] == ["Action"]  # its own genres are kept
    # "Re:Zero" has no franchise before a subtitle, so a liked "Re" is no link.
    assert "continuation_of" not in rows[2]


def test_titles_match_without_year_and_season_tags():
    assert "ranma12" in cont.match_keys("Ranma1/2 (2024) Season 3")
    assert "shangrila frontier" in cont.match_keys("Shangri-La Frontier Season 2")
    assert "dr stone" in cont.match_keys("Dr. STONE: 4th Season")
    assert "iceblade sorcerer shall rule the world" in cont.match_keys("The Iceblade Sorcerer Shall Rule the World II")


def test_one_row_per_coming_season_tmdb_wins_and_keeps_the_anilist_id():
    tmdb = {"title": "Reincarnated as a Sword", "tmdb_id": 1, "premiere_date": "2026-10-08",
            "continuation_of": {"title": "Reincarnated as a Sword", "relation": "season"}}
    anilist = {"title": "Reincarnated as a Sword Season 2", "anilist_id": 159042, "premiere_date": "2026-09-30",
               "continuation_of": {"title": "Reincarnated as a Sword", "relation": "sequel"}}
    later = {"title": "Reincarnated as a Sword Season 3", "anilist_id": 190000, "premiere_date": "2028-01-10",
             "continuation_of": {"title": "Reincarnated as a Sword", "relation": "sequel"}}

    kept = cont.drop_duplicate_seasons([tmdb, anilist, later])
    assert kept == [tmdb, later] and tmdb["anilist_id"] == 159042


def test_the_continuation_row_leads_a_title_a_discover_lane_also_found():
    found = [{"title": "The Simpsons", "year": 1989, "type": "show", "tmdb_id": 456,
              "continuation_of": {"title": "The Simpsons", "relation": "season", "season": 38}}]
    discovered = {"title": "The Simpsons", "year": 1989, "type": "show", "tmdb_id": 456, "source": "tmdb_discover"}

    merged = cont.merge_continuations([discovered], found)
    assert merged[0] is found[0] and discovered["continuation_of"]["season"] == 38


# --- exclusions ----------------------------------------------------------------


def _context(**records):
    from recommendation.exclusion_engine import build_exclusion_context

    return build_exclusion_context(records.get("history", []), records.get("library", []), [],
                                   records.get("requested", []), [])


def _simpsons_season(**extra):
    return {"title": "The Simpsons", "year": 1989, "type": "show", "tmdb_id": 456,
            "premiere_date": "2027-09-27", "premiere_kind": "season_premiere",
            "continuation_of": {"title": "The Simpsons", "relation": "season", "season": 39}, **extra}


def test_a_coming_season_of_a_watched_series_in_the_library_is_not_excluded():
    from recommendation.exclusion_engine import apply_exclusions

    seen = {"title": "The Simpsons", "year": 1989, "type": "show", "tmdb_id": 456}
    candidate = _simpsons_season()
    assert apply_exclusions(candidate, _context(history=[seen], library=[seen])) == (True, None)
    assert candidate["continuation_in_library"] is True
    plain = {key: value for key, value in candidate.items() if key not in {"continuation_of", "continuation_in_library"}}
    assert apply_exclusions(plain, _context(history=[seen])) == (False, "rejected_already_watched")


def test_a_no_in_the_queue_still_stands_for_a_coming_season():
    from recommendation.exclusion_engine import apply_exclusions

    rejected = {"title": "The Simpsons", "year": 1989, "type": "show", "tmdb_id": 456, "status": "rejected"}
    waiting = {**rejected, "status": "approved"}
    assert apply_exclusions(_simpsons_season(), _context(requested=[rejected])) == (False, "rejected_already_requested")
    assert apply_exclusions(_simpsons_season(), _context(requested=[waiting])) == (True, None)


def test_a_premiered_continuation_is_excluded_like_any_watched_title():
    from recommendation.exclusion_engine import apply_exclusions

    seen = {"title": "The Simpsons", "year": 1989, "type": "show", "tmdb_id": 456}
    aired = _simpsons_season(premiere_date="2020-09-27")
    assert apply_exclusions(aired, _context(history=[seen])) == (False, "rejected_already_watched")


# --- ranking -------------------------------------------------------------------


def _taste():
    from recommendation.taste_engine import build_taste_snapshot

    history = [
        {"title": "Delicious in Dungeon", "year": 2024, "type": "anime", "media_type": "anime", "tmdb_id": 1,
         "rating": 10, "rating_scale": 10, "genres": ["Animation", "Fantasy", "Comedy"], "original_language": "ja",
         "tmdb_keywords": ["dungeon", "cooking", "monster"], "provider": "trakt", "source": "trakt"},
    ] + [
        {"title": "Liked %d" % index, "year": 2020 + index % 5, "type": "anime", "media_type": "anime",
         "tmdb_id": 100 + index, "rating": 9, "rating_scale": 10, "genres": ["Animation", "Fantasy"],
         "original_language": "ja", "provider": "trakt", "source": "trakt"}
        for index in range(12)
    ]
    return build_taste_snapshot(history)


def test_a_continuation_is_linked_to_the_liked_title_and_says_so():
    from recommendation.ranking_engine import score_candidates

    season = {"title": "Delicious in Dungeon", "year": 2024, "type": "anime", "media_type": "anime", "tmdb_id": 1,
              "genres": ["Animation", "Fantasy", "Comedy"], "original_language": "ja",
              "premiere_date": "2027-10-01", "premiere_kind": "season_premiere", "premiere_season": 2,
              "continuation_of": {"title": "Delicious in Dungeon", "relation": "season", "season": 2}}
    row = score_candidates([season], _taste())[0]

    assert row["specific_score"] >= 0.55 and row["continuation_link"] == "Delicious in Dungeon"
    assert row["score_components"]["franchise_affinity"] >= 0.55
    assert "is season 2 of Delicious in Dungeon, which you rated 10/10" in row["why"]


def test_a_title_held_out_of_the_profile_lends_nothing_to_its_sequel():
    from recommendation.ranking_engine import score_candidates

    sequel = {"title": "Unknown Show Season 2", "year": 2027, "type": "anime", "media_type": "anime",
              "genres": ["Drama"], "original_language": "ja",
              "continuation_of": {"title": "Held Out Title", "relation": "sequel"}}
    row = score_candidates([sequel], _taste())[0]
    assert "continuation_link" not in row and row["score_components"]["franchise_affinity"] <= 0.0


def test_a_films_repeat_viewings_are_times_not_episodes():
    from recommendation.ranking_engine import _liked_mark

    assert _liked_mark({"plays": 3, "media_type": "movie"}) == "which you watched 3 times"
    assert _liked_mark({"plays": 12, "media_type": "anime"}) == "which you watched 12 episodes of"


# --- jobs.upcoming.broaden ------------------------------------------------------


def _row(title, pick=False):
    return {"title": title, "year": 2027, "type": "show", "pick": pick}


def _pipeline(rows):
    return {"accepted": [row for row in rows if row.get("pick")]}


def test_the_search_widens_step_by_step_and_one_failing_source_does_not_end_it():
    job = {"id": "job_up", "final_recommendation_limit": 3, "media_types": ["tv"], "filters": {"upcoming_only": True}}
    extra = [_row("A", pick=True)]
    asked = []

    async def fetch(stage, *_args):
        asked.append(stage)
        if stage == "deeper_pages":
            raise RuntimeError("TMDb timeout")
        return {"taste_window": [_row("A", pick=True), _row("B", pick=True)],
                "other_sources": [_row("C", pick=True), _row("D"), _row("E", pick=True)]}[stage]

    report = {}
    extra, result = run(upcoming.broaden(job, {}, {"recommended": []}, extra, _pipeline(extra), _pipeline,
                                         None, {}, report=report, fetch=fetch))

    assert asked == ["taste_window", "deeper_pages", "other_sources"]
    assert [row["title"] for row in result["accepted"]] == ["A", "B", "C", "E"]
    assert [step["stage"] for step in report["stages"]] == ["job_lanes", "taste_window", "deeper_pages", "other_sources"]
    assert report["stages"][1]["new"] == 1  # A was already in the pool
    assert report["stages"][2] == {"stage": "deeper_pages", "error": "RuntimeError"}
    assert report["new_picks"] == 4 and report["target"] == 3


def test_a_run_that_already_has_enough_new_picks_searches_no_further():
    job = {"id": "job_up", "final_recommendation_limit": 2, "media_types": ["tv"], "filters": {"upcoming_only": True}}
    extra = [_row("A", pick=True), _row("B", pick=True)]
    fetch = AsyncMock()

    run(upcoming.broaden(job, {}, {"recommended": []}, extra, _pipeline(extra), _pipeline, None, {}, fetch=fetch))
    fetch.assert_not_awaited()


def test_picks_already_on_the_jobs_list_do_not_count_as_new():
    job = {"id": "job_up", "final_recommendation_limit": 2, "media_types": ["tv"], "filters": {"upcoming_only": True}}
    extra = [_row("A", pick=True), _row("B", pick=True)]
    listed = {"recommended": [{**_row("A"), "job_id": "job_up"}, {**_row("B"), "job_id": "job_up"}]}
    fetch = AsyncMock(return_value=[])

    run(upcoming.broaden(job, {}, listed, extra, _pipeline(extra), _pipeline, None, {}, fetch=fetch))
    assert fetch.await_count == len(upcoming.UPCOMING_STAGES)


# --- keeping earlier picks, and the queue ---------------------------------------


def test_an_upcoming_jobs_open_picks_stay_until_their_premiere():
    base = {"user_id": "u", "job_id": "job_up", "type": "show"}
    db = fake_db(recommendations=[
        {**base, "id": "r1", "title": "Silo", "year": 2023, "tmdb_id": 1, "premiere_date": "2027-07-08", "match_score": 90},
        {**base, "id": "r2", "title": "Old", "year": 2020, "tmdb_id": 2, "premiere_date": "2026-01-01", "match_score": 95},
        {**base, "id": "r3", "title": "Gone", "year": 2027, "tmdb_id": 3, "premiere_date": "2027-02-01", "dismissed": True},
        {**base, "id": "r4", "title": "Again", "year": 2027, "tmdb_id": 4, "premiere_date": "2027-03-01", "match_score": 70},
        {**base, "id": "r5", "title": "Weak", "year": 2027, "tmdb_id": 5, "premiere_date": "2027-04-01", "match_score": 40},
        {**base, "id": "r6", "title": "Other job", "year": 2027, "tmdb_id": 6, "premiere_date": "2027-04-01",
         "job_id": "job_tv"},
    ])
    new = [{"id": "n1", "title": "Again", "year": 2027, "type": "show", "tmdb_id": 4}]
    job = {"id": "job_up", "final_recommendation_limit": 3}

    kept = run(upcoming.carry_over("u", job, new, database=db))
    assert [row["id"] for row in kept] == ["r1", "r5"]


def test_a_coming_season_of_a_series_in_the_library_is_never_queued(monkeypatch):
    db = fake_db(recommendations=[{"user_id": "u", "id": "rec_1", "title": "The Simpsons"}])
    monkeypatch.setattr(jobs_engine, "db", db)
    submit = AsyncMock()
    monkeypatch.setattr(request_providers.LocalRequestProvider, "submit", submit)
    rows = [{"id": "rec_1", "title": "The Simpsons", "year": 1989, "type": "show", "tmdb_id": 456,
             "continuation_in_library": True}]

    run(jobs_engine.apply_job_action_mode(
        "u", {"id": "job_up", "action_mode": "require_approval", "final_recommendation_limit": 650}, rows, {}))
    submit.assert_not_awaited()


def test_queue_rows_get_a_premiere_and_nothing_else(monkeypatch):
    import providers.premieres as premieres

    db = fake_db(requests=[
        {"user_id": "u", "id": "req_new", "title": "Avengers: Doomsday", "type": "movie", "tmdb_id": 1,
         "status": "approved", "updated_at": "2026-09-01"},
        {"user_id": "u", "id": "req_old", "title": "Silo", "type": "show", "tmdb_id": 2, "status": "pending_approval",
         "premiere_checked_at": "2026-09-20T00:00:00+00:00"},
        {"user_id": "u", "id": "req_fresh", "title": "Fresh", "type": "show", "tmdb_id": 3, "status": "pending_approval",
         "premiere_checked_at": "2999-01-01T00:00:00+00:00"},
        {"user_id": "u", "id": "req_no", "title": "No", "type": "show", "tmdb_id": 4, "status": "rejected"},
    ])

    async def verified(rows, _key, now=None, *, store=True, unanswered=None):
        for row in rows:
            row.update({"premiere_date": None, "premiere_checked_at": "2026-09-25T12:00:00+00:00"})
            if row["id"] == "req_new":
                row.update({"premiere_date": "2026-12-16", "premiere_kind": "film_release"})
            if row["id"] == "req_old":
                unanswered.append(row)
        return 1

    monkeypatch.setattr(premieres, "verify_premieres", verified)
    written = run(upcoming.refresh_request_premieres("u", "key", database=db))

    rows = {row["id"]: row for row in db.requests.rows}
    assert written == 1
    assert rows["req_new"]["premiere_date"] == "2026-12-16" and rows["req_new"]["status"] == "approved"
    assert rows["req_new"]["updated_at"] == "2026-09-01"
    assert "premiere_date" not in rows["req_old"] and "premiere_date" not in rows["req_no"]
    assert "premiere_date" not in rows["req_fresh"]


def test_requests_list_coming_premieres_first_soonest_first():
    rows = [
        {"id": "a", "updated_at": "2026-09-25", "premiere_date": None},
        {"id": "b", "updated_at": "2026-09-20", "premiere_date": "2027-01-01"},
        {"id": "c", "updated_at": "2026-09-24", "premiere_date": "2026-10-01"},
        {"id": "d", "updated_at": "2026-09-23", "premiere_date": "2026-01-01"},
    ]
    assert [row["id"] for row in rq.sort_rows(rows, "upcoming_first", TODAY)] == ["c", "b", "a", "d"]
    assert rq.release_bucket({"release_date": "2019-12-20", "premiere_date": "2027-03-04"}, TODAY) == "upcoming"
    assert rq.release_bucket({"release_date": "2019-12-20", "premiere_date": None}, TODAY) == "released"


# --- providers -------------------------------------------------------------------


def test_taste_lanes_ask_what_they_asked_before_unless_the_job_is_upcoming():
    from providers.tmdb import window_params

    assert window_params("tv", None) == [{}]
    film = window_params("movie", {"from": "2026-09-26", "to": "2029-12-31"})
    assert film == [{"primary_release_date.gte": "2026-09-26", "primary_release_date.lte": "2029-12-31",
                     "vote_count.gte": None}]
    series = window_params("tv", {"from": "2026-09-26"})
    assert [sorted(part) for part in series] == [["first_air_date.gte", "vote_count.gte"],
                                                 ["air_date.gte", "vote_count.gte"]]


def test_trakt_is_asked_for_the_jobs_genres():
    from providers.trakt import trakt_genre_filter

    assert set(trakt_genre_filter(["sci-fi", "fantasy", "animation", "anime"]).split(",")) == {
        "science-fiction", "fantasy", "animation", "anime"}
    assert trakt_genre_filter([]) is None


def test_metadata_of_a_title_not_yet_out_is_fetched_again_after_a_week():
    from providers.tmdb_enrich import unreleased_when_fetched

    now = NOW
    assert unreleased_when_fetched({"fetched_at": "2026-09-01T10:00:00", "payload": {"release_date": "2026-12-01"}}, now)
    assert not unreleased_when_fetched({"fetched_at": "2026-09-01T10:00:00", "payload": {"release_date": "2026-08-01"}}, now)
    assert not unreleased_when_fetched({"fetched_at": "2026-09-22T10:00:00", "payload": {"release_date": "2026-12-01"}}, now)
    assert unreleased_when_fetched({"fetched_at": "2026-09-01T10:00:00", "payload": {"year": 2026}}, now)
    assert not unreleased_when_fetched({"fetched_at": "2026-09-01T10:00:00", "payload": {"year": 2020}}, now)


# --- GET /api/upcoming ------------------------------------------------------------


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


FRESH = "2999-01-01T00:00:00+00:00"


def test_up_coming_shows_everything_coming_that_is_the_users(app):
    """Picks, approved and waiting queue rows, and a coming season of a watched series in the library."""
    simpsons = {"title": "The Simpsons", "year": 1989, "type": "show", "tmdb_id": 456}
    app.use(
        jobs=[{"user_id": "u", "id": "job_up", "enabled": True}],
        history=[{"user_id": "u", **simpsons}],
        media_library=[{"user_id": "u", **simpsons}],
        recommendations=[
            {"user_id": "u", "id": "r_season", "job_id": "job_up", **simpsons, "premiere_date": "2027-09-27",
             "premiere_kind": "season_premiere", "premiere_season": 39, "premiere_checked_at": FRESH,
             "continuation_of": {"title": "The Simpsons", "relation": "season", "season": 39},
             "request_id": "req_own", "match_score": 88},
            {"user_id": "u", "id": "r_seen", "job_id": "job_up", "title": "Seen Film", "year": 2027, "type": "movie",
             "tmdb_id": 7, "premiere_date": "2027-02-01", "premiere_kind": "film_release", "premiere_checked_at": FRESH},
        ],
        requests=[
            {"user_id": "u", "id": "req_own", "title": "The Simpsons", "year": 1989, "type": "show", "tmdb_id": 456,
             "status": "pending_approval", "premiere_date": "2027-09-27", "recommendation_id": "r_season"},
            {"user_id": "u", "id": "req_ok", "title": "Avengers: Doomsday", "year": 2026, "type": "movie",
             "tmdb_id": 1003596, "status": "approved", "premiere_date": "2027-12-16", "premiere_kind": "film_release"},
            {"user_id": "u", "id": "req_wait", "title": "PSYREN", "year": 2026, "type": "anime", "tmdb_id": 5,
             "status": "pending_approval", "premiere_date": "2027-10-05", "premiere_kind": "series_premiere"},
            {"user_id": "u", "id": "req_no", "title": "Rejected Show", "year": 2027, "type": "show", "tmdb_id": 6,
             "status": "rejected", "premiere_date": "2027-03-01"},
        ],
        media_history=[{"user_id": "u", "title": "Seen Film", "year": 2027, "type": "movie", "tmdb_id": 7, "rating": 6}],
    )
    shown = asyncio.run(app.server.upcoming_premieres(limit=12, user=app.user))

    assert [card["id"] for card in shown] == ["r_season", "req_wait", "req_ok"]
    season, waiting, approved = shown
    assert season["status"] == "pending_approval" and season["upcoming_kind"] == "tv"
    assert waiting["request_id"] == "req_wait" and waiting["needs_approval"] is True
    assert approved["in_library"] is True and approved["upcoming_kind"] == "movie"


def test_up_coming_keeps_each_kind_its_own_soonest_titles(app):
    rows = [{"user_id": "u", "id": "req_%d" % index, "title": "Show %d" % index, "year": 2027, "type": "show",
             "tmdb_id": 100 + index, "status": "pending_approval", "premiere_date": "2027-01-%02d" % (index + 1)}
            for index in range(5)]
    rows.append({"user_id": "u", "id": "req_film", "title": "Film", "year": 2027, "type": "movie", "tmdb_id": 999,
                 "status": "pending_approval", "premiere_date": "2027-06-01"})
    app.use(requests=rows)

    shown = asyncio.run(app.server.upcoming_premieres(limit=2, user=app.user))
    assert [card["id"] for card in shown] == ["req_0", "req_1", "req_film"]
