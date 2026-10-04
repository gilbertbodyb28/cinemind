"""Every saved job's run sends at least 100 results to Requests (Gilbert, 2026-09-29).

On 2026-09-29 his five jobs finished with "0 picks · 0 new in Requests": Discover
had 1,753 candidates, 781 of them already waiting in Requests and 211 more that met
every setting but fell below the taste floor; Tv and Upcoming US held every new
title back because more than 650 of theirs were waiting. His rule, asked in the
session: a run's results are the titles that fit the job - new, or still waiting in
Requests for his decision - at least 100, preferably well over 1,000; the closest
titles below the taste floor fill up, marked as weaker matches; and every result
reaches Requests.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jobs.engine as jobs_engine
import jobs.upcoming as upcoming
import mediamanager_client
import request_providers
from fake_mongo import _Cursor, matches
from recommendation.exclusion_engine import apply_exclusions, build_exclusion_context
from recommendation.job_intent import OFF_INTENT, intent_tier, job_intent
from recommendation.pipeline import (
    MIN_RESULTS,
    min_results,
    result_counts,
    result_target,
    run_pipeline,
    select_final,
)


def run(coro):
    return asyncio.run(coro)


SHOW = {"title": "Andor", "year": 2022, "type": "show", "media_type": "tv", "tmdb_id": 83867}


def _request(status, **extra):
    return {**SHOW, "id": "req_%s" % status, "status": status, **extra}


# --- which titles are results ------------------------------------------------------


def test_a_title_waiting_in_requests_is_one_of_a_saved_jobs_results():
    context = build_exclusion_context([], [], [], [_request("pending_approval", source_job_id="job_other")], [])
    candidate = dict(SHOW)

    assert apply_exclusions(candidate, context, {"open_results": True}) == (True, None)
    assert candidate["open_result"] == "waiting"


def test_without_the_rule_a_waiting_title_stays_out():
    """Content to Watch and AI Search pick new titles only, as before."""
    context = build_exclusion_context([], [], [], [_request("pending_approval")], [])

    assert apply_exclusions(dict(SHOW), context, {}) == (False, "rejected_already_requested")


def test_a_decided_title_stays_out_even_while_a_copy_of_it_waits():
    for status in ("approved", "rejected", "archived"):
        context = build_exclusion_context(
            [], [], [], [_request("pending_approval"), _request(status, id="req_decided")], [])
        assert apply_exclusions(dict(SHOW), context, {"open_results": True}) == (
            False, "rejected_already_requested"), status


def test_a_waiting_title_the_user_has_since_watched_is_not_a_result():
    context = build_exclusion_context([dict(SHOW)], [], [], [_request("pending_approval")], [])

    assert apply_exclusions(dict(SHOW), context, {"open_results": True}) == (False, "rejected_already_watched")


def test_a_waiting_title_counts_even_for_a_job_that_excludes_recommended_titles():
    shown = {**SHOW, "job_id": "job_other", "created_at": "2026-09-28T00:00:00+00:00"}
    context = build_exclusion_context([], [], [shown], [_request("pending_approval")], [], job_id="job_tv")

    assert apply_exclusions(dict(SHOW), context, {"open_results": True, "already_recommended": True}) == (True, None)


def test_the_jobs_own_open_list_passes_already_recommended_but_another_jobs_list_does_not():
    mine = {**SHOW, "job_id": "job_tv", "created_at": "2026-09-28T00:00:00+00:00"}
    context = build_exclusion_context([], [], [mine], [], [], job_id="job_tv")
    candidate = dict(SHOW)
    assert apply_exclusions(candidate, context, {"open_results": True, "already_recommended": True}) == (True, None)
    assert candidate["open_result"] == "listed"

    theirs = {**SHOW, "job_id": "job_other", "created_at": "2026-09-28T00:00:00+00:00"}
    context = build_exclusion_context([], [], [theirs], [], [], job_id="job_tv")
    assert apply_exclusions(dict(SHOW), context, {"open_results": True, "already_recommended": True}) == (
        False, "rejected_already_recommended")


def test_a_dismissed_or_retired_pick_is_not_on_the_jobs_open_list():
    for flag in ("dismissed", "retired"):
        mine = {**SHOW, "job_id": "job_tv", flag: True, "created_at": "2026-09-28T00:00:00+00:00"}
        context = build_exclusion_context([], [], [mine], [], [], job_id="job_tv")
        ok, _reason = apply_exclusions(dict(SHOW), context, {"open_results": True, "already_recommended": True,
                                                             "dismissed": False})
        assert not ok, flag


def test_marks_are_redone_on_every_pipeline_run_of_the_same_rows():
    candidate = dict(SHOW)
    waiting = build_exclusion_context([], [], [], [_request("pending_approval")], [])
    apply_exclusions(candidate, waiting, {"open_results": True})
    assert candidate["open_result"] == "waiting"
    apply_exclusions(candidate, build_exclusion_context([], [], [], [], []), {"open_results": True})
    assert "open_result" not in candidate


# --- how many results, and which ----------------------------------------------------


def _scored(title, score, *, personal=3.0, specific=0.4, lane="live_action", lang="en"):
    base = {
        "live_action": {"media_type": "tv", "type": "show", "genres": ["Sci-Fi"]},
        "anime": {"media_type": "anime", "type": "anime", "genres": ["Animation"]},
    }[lane]
    return {"title": title, "year": 2026, "rank_score": score, "personal_score": personal,
            "specific_score": specific, "original_language": "ja" if lane == "anime" else lang, **base}


def _saved(limit, **extra):
    return {"job_intent": True, "media_types": ["tv"], "filters": {}, "final_recommendation_limit": limit,
            "taste_floor": 2.5, "open_results": True, "min_results": MIN_RESULTS, **extra}


def test_a_saved_job_fills_up_to_its_limit_and_never_below_its_minimum():
    assert min_results(_saved(1500)) == 100 and result_target(_saved(1500)) == 1500
    assert min_results(_saved(8)) == 8 and result_target(_saved(8)) == 8
    content_to_watch = {"final_recommendation_limit": 8, "open_results": False, "min_results": 0}
    assert min_results(content_to_watch) == 0 and result_target(content_to_watch) == 0


def test_every_title_over_the_floor_follows_the_head_of_the_list_unchanged():
    # One strong title sets the relevance cut (75 % of 10.0): the others clear the
    # taste floor but sit under it, and a short list used to stop there.
    pool = [_scored("Best", 10.0)] + [_scored("Match %02d" % i, 5.0 - i * 0.01) for i in range(40)]
    head = select_final(pool, {**_saved(150), "open_results": False, "min_results": 0})
    picked = select_final(pool, {**_saved(150), "min_results": 0})

    assert [row["title"] for row in head] == ["Best"]
    assert picked[:len(head)] == head
    assert len(picked) == 41
    assert not any(row["weak_match"] for row in picked)


def test_the_closest_titles_below_the_floor_fill_up_to_the_limit_marked_weaker():
    pool = [_scored("Match %d" % i, 9.0 - i * 0.1) for i in range(5)]
    pool += [_scored("Close %03d" % i, 4.0 - i * 0.01, personal=2.0, specific=0.1) for i in range(300)]
    picked = select_final(pool, _saved(150))

    assert len(picked) == 150
    assert [row["title"] for row in picked[:5]] == ["Match %d" % i for i in range(5)]
    assert all(not row["weak_match"] for row in picked[:5])
    assert all(row["weak_match"] for row in picked[5:])
    # Closest first: the list's own order.
    assert [row["title"] for row in picked[5:8]] == ["Close 000", "Close 001", "Close 002"]
    counts = result_counts(picked, _saved(150))
    assert (counts["results"], counts["matches"], counts["weak"]) == (150, 5, 145)


def test_lanes_the_job_never_asked_for_keep_their_share_unless_the_minimum_needs_them():
    spec = _saved(200)
    intent = job_intent(spec)
    english = [_scored("English %03d" % i, 5.0 - i * 0.01, personal=2.0, specific=0.1) for i in range(150)]
    anime = [_scored("Anime %03d" % i, 6.0 - i * 0.01, personal=2.0, specific=0.1, lane="anime") for i in range(150)]
    picked = select_final(sorted(english + anime, key=lambda row: intent_tier(row, intent)), spec)
    assert len(picked) == 170  # 150 English, then the capped 10 % (20) of anime
    assert sum(1 for row in picked if intent_tier(row, intent) == OFF_INTENT) == 20

    few = english[:30] + anime
    picked = select_final(sorted(few, key=lambda row: intent_tier(row, intent)), spec)
    # 30 English + 20 within the cap, then anime past the cap only up to the minimum of 100.
    assert len(picked) == 100
    assert sum(1 for row in picked if intent_tier(row, intent) == OFF_INTENT) == 70


def test_content_to_watch_never_takes_a_title_below_the_floor():
    pool = [_scored("Match", 9.0)] + [_scored("Close %d" % i, 4.0, personal=2.0, specific=0.1) for i in range(20)]
    picked = select_final(pool, {"final_recommendation_limit": 8, "lane_balance": False, "taste_floor": 2.5})

    assert [row["title"] for row in picked] == ["Match"]
    assert "weak_match" not in picked[0]


def test_run_pipeline_counts_new_waiting_and_weaker_results():
    job = {"id": "job_tv", "job_intent": True, "media_types": ["tv"], "final_recommendation_limit": 50,
           "open_results": True, "min_results": MIN_RESULTS, "taste_floor": 2.5,
           "exclusions": {"already_requested": True}}
    waiting = {"title": "Waiting Show", "year": 2026, "type": "show", "media_type": "tv", "tmdb_id": 11,
               "status": "pending_approval", "id": "req_w"}
    candidates = [
        {"title": "Waiting Show", "year": 2026, "type": "show", "media_type": "tv", "tmdb_id": 11,
         "genres": ["Drama"], "original_language": "en"},
        {"title": "New Show", "year": 2026, "type": "show", "media_type": "tv", "tmdb_id": 12,
         "genres": ["Drama"], "original_language": "en"},
    ]
    result = run_pipeline(job, history=[], requested=[waiting], extra_candidates=candidates,
                          taste={"liked_titles": []})

    assert {row["title"]: row.get("open_result") for row in result["accepted"]} == {
        "Waiting Show": "waiting", "New Show": None}
    assert result["results"]["waiting"] == 1 and result["results"]["new"] == 1


# --- the search widens for every saved job -------------------------------------------


def _row(title, pick=False, weak=False):
    return {"title": title, "year": 2026, "type": "show", "pick": pick, "weak": weak}


def _pipeline(rows):
    return {"accepted": [{**row, "weak_match": row["weak"]} for row in rows if row.get("pick") or row.get("weak")]}


def test_a_saved_job_short_of_its_minimum_widens_until_it_has_it():
    job = {"id": "job_tv", "final_recommendation_limit": 3, "min_results": 3, "open_results": True,
           "media_types": ["tv"], "filters": {}}
    extra = [_row("A", pick=True)]
    asked = []

    async def fetch(stage, *_args):
        asked.append(stage)
        return {"taste_window": [_row("B", pick=True)], "deeper_pages": [],
                "other_sources": [_row("C", pick=True)], "full_budget": [_row("D", pick=True)]}[stage]

    report = {}
    extra, result = run(upcoming.broaden(job, {}, {"recommended": []}, extra, _pipeline(extra), _pipeline,
                                         None, {}, report=report, fetch=fetch))

    assert asked == ["taste_window", "deeper_pages", "other_sources"]
    assert [row["title"] for row in result["accepted"]] == ["A", "B", "C"]
    assert report["results"]["matches"] == 3 and report["minimum"] == 3 and report["target"] == 3


def test_titles_below_the_floor_do_not_count_as_matches_for_the_search():
    job = {"id": "job_tv", "final_recommendation_limit": 3, "min_results": 3, "open_results": True,
           "media_types": ["tv"], "filters": {}}
    extra = [_row("A", pick=True), _row("W1", weak=True), _row("W2", weak=True)]
    fetch = AsyncMock(return_value=[])

    run(upcoming.broaden(job, {}, {"recommended": []}, extra, _pipeline(extra), _pipeline, None, {}, fetch=fetch))
    assert fetch.await_count == len(upcoming.SEARCH_STAGES)


def test_a_saved_job_with_enough_matches_searches_no_further():
    job = {"id": "job_tv", "final_recommendation_limit": 2, "min_results": 2, "open_results": True,
           "media_types": ["tv"], "filters": {}}
    extra = [_row("A", pick=True), _row("B", pick=True)]
    fetch = AsyncMock()

    run(upcoming.broaden(job, {}, {"recommended": []}, extra, _pipeline(extra), _pipeline, None, {}, fetch=fetch))
    fetch.assert_not_awaited()


def test_content_to_watch_does_not_widen():
    assert not upcoming.search_wanted({"id": "content_to_watch:u", "open_results": False, "min_results": 0,
                                       "filters": {}})
    assert upcoming.search_wanted(jobs_engine.with_result_rules({"id": "job_tv", "filters": {}}))


# --- what the run says, and what reaches Requests -----------------------------------


def test_the_run_says_how_its_results_were_made_up():
    filled = jobs_engine.result_summary({"results": {"results": 150, "matches": 40, "weak": 110, "minimum": 100}})
    assert [row["code"] for row in filled] == ["results_filled"]
    assert "40 of the 150 results" in filled[0]["detail"] and "110" in filled[0]["detail"]

    short = jobs_engine.result_summary({"results": {"results": 52, "matches": 3, "weak": 49, "minimum": 100}})
    assert [row["code"] for row in short] == ["results_filled", "results_short"]
    assert "Only 52 titles" in short[1]["detail"]

    assert jobs_engine.result_summary({"results": {"results": 300, "matches": 300, "weak": 0, "minimum": 100}}) == []
    assert {"results_filled", "results_short"} <= jobs_engine.SUMMARY_CODES


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


def test_a_weaker_match_reaches_requests_marked_as_one(monkeypatch):
    monkeypatch.setattr(jobs_engine, "db", SimpleNamespace(
        requests=_Requests([], waiting=2078), recommendations=SimpleNamespace(update_one=AsyncMock())))
    sent = []

    async def fake_submit(self, user_id, item, status="requested"):
        sent.append((item["title"], item.get("weak_match")))
        return {"id": "req_" + item["title"], "status": status}

    monkeypatch.setattr(request_providers.LocalRequestProvider, "submit", fake_submit)
    monkeypatch.setattr(mediamanager_client, "send_item_to_library", AsyncMock(return_value={"tmdb_id": 1}))
    rows = [{"id": "rec_a", "title": "Match", "year": 2026, "type": "show", "weak_match": False},
            {"id": "rec_b", "title": "Closest", "year": 2026, "type": "show", "weak_match": True}]
    outcome = {}
    warnings = run(jobs_engine.apply_job_action_mode(
        "u1", {"id": "job_tv", "action_mode": "require_approval", "final_recommendation_limit": 650},
        rows, {}, outcome=outcome))

    assert sent == [("Match", False), ("Closest", True)]
    assert outcome["queued"] == 2 and outcome["held_back"] == 0 and warnings == []


def test_a_new_queue_row_keeps_the_weaker_mark(monkeypatch):
    stored = {}

    class _Store:
        async def find_one(self, *_args, **_kwargs):
            return None

        def find(self, *_args, **_kwargs):
            return _Cursor([])

        async def update_one(self, query, update, upsert=False):
            stored.update(update.get("$set") or {})

    monkeypatch.setattr(request_providers, "db", SimpleNamespace(requests=_Store()))
    run(request_providers.LocalRequestProvider().submit(
        "u1", {"title": "Closest", "year": 2026, "type": "show", "weak_match": True}, "pending_approval"))

    assert stored["weak_match"] is True
    assert "weak_match" in request_providers.SUGGESTION_FIELDS


# --- posters for a long list of results ---------------------------------------------


class _Cache:
    def __init__(self):
        self.rows = {}

    async def find_one(self, query, _projection=None):
        row = self.rows.get(query.get("key"))
        if row and row["expires_at"] > query["expires_at"]["$gt"]:
            return row
        return None

    async def update_one(self, query, update, upsert=False):
        self.rows[query["key"]] = dict(update["$set"])


def test_tvdb_posters_are_looked_up_a_few_at_a_time_and_remembered(monkeypatch):
    import database
    import providers.tmdb as tmdb
    import providers.tvdb as tvdb

    cache = _Cache()
    monkeypatch.setattr(database, "db", SimpleNamespace(provider_cache=cache))
    asked = []

    async def lookup(title, year, kind, key):
        asked.append(title)
        return {"poster": "https://tvdb/%s.jpg" % title, "tvdb_id": 7} if title != "None Yet" else None

    monkeypatch.setattr(tvdb, "tvdb_poster_lookup", lookup)
    rows = [{"title": name, "year": 2027, "type": "show"} for name in ("A", "B", "None Yet")]
    run(tmdb.enrich_with_tmdb(rows, api_key="", tvdb_api_key="k"))
    assert [row.get("poster") for row in rows] == ["https://tvdb/A.jpg", "https://tvdb/B.jpg", None]
    assert sorted(asked) == ["A", "B", "None Yet"]

    again = [{"title": name, "year": 2027, "type": "show"} for name in ("A", "B", "None Yet")]
    run(tmdb.enrich_with_tmdb(again, api_key="", tvdb_api_key="k"))
    assert len(asked) == 3  # every answer, "no poster" too, came from the cache
    assert again[0]["poster"] == "https://tvdb/A.jpg" and again[0]["tvdb_id"] == 7


def test_a_row_with_its_own_tmdb_id_is_never_given_another_titles_id(monkeypatch):
    import database
    import providers.tmdb as tmdb

    monkeypatch.setattr(database, "db", SimpleNamespace(provider_cache=_Cache()))

    async def details(hc, tmdb_id, kind, key):
        return {"tmdb_id": tmdb_id, "poster": "https://tmdb/%s.jpg" % tmdb_id}

    async def search(*_args, **_kwargs):
        raise AssertionError("a row with a TMDb id is not searched by name")

    monkeypatch.setattr(tmdb, "tmdb_details", details)
    monkeypatch.setattr(tmdb, "tmdb_lookup", search)
    rows = [{"title": "Industry", "year": 2020, "type": "show", "tmdb_id": 94605}]
    run(tmdb.enrich_with_tmdb(rows, api_key="key"))
    assert rows[0]["tmdb_id"] == 94605 and rows[0]["poster"] == "https://tmdb/94605.jpg"


def test_a_restart_frees_the_lock_a_dying_run_left_behind(monkeypatch):
    calls = []

    class _Locks:
        async def update_many(self, query, update):
            calls.append((query, update))
            return SimpleNamespace(modified_count=1)

    monkeypatch.setattr(jobs_engine, "db", SimpleNamespace(job_locks=_Locks()))
    assert run(jobs_engine.release_stale_locks()) == 1
    assert calls == [({"lock_owner": {"$ne": None}}, {"$set": {"lock_owner": None, "expires_at": None}})]
