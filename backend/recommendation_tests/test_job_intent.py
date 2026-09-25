"""A saved job is served by what it asks for, not by the profile's habits.

Regression for 2026-09-24: a job asking for TV in Fantasy / Sci-Fi / Action /
Adventure came back with 7 English live-action series in its top 20 - the rest
Japanese anime, donghua and kids' cartoons - because the TV filter let anime
through, the discover lane asked for every language and format, the similarity
lanes were seeded from anime favourites, AniList was queried for a job that
never mentioned anime, lane balancing gave each animated lane its own share, and
Trakt's recommendations never parsed at all.
"""

import asyncio

import providers.tmdb as tmdb
from providers.tmdb import _normalize_tmdb_result, job_intent_lane_filters, related_seeds
from providers.trakt import parse_recommendation_entry
from recommendation.job_intent import (
    OFF_INTENT,
    PRIMARY,
    SECONDARY,
    intent_tier,
    job_fit,
    job_intent,
    seed_fits,
)
from recommendation.media_identity import content_lane
from recommendation.pipeline import run_pipeline, select_final
from recommendation.ranking_engine import apply_lane_balance, score_candidates

FSAA = ["fantasy", "sci-fi", "action", "adventure"]


def _job(media, genres=(), **filters):
    return {"job_intent": True, "media_types": list(media),
            "filters": {"include_genres": list(genres), **filters}}


def _tmdb(media_type, lang, genre_ids, title="T", votes=400, rating=7.8):
    row = {"id": abs(hash((title, media_type))) % 10**7, "name": title, "title": title,
           "original_language": lang, "genre_ids": genre_ids, "first_air_date": "2021-03-01",
           "release_date": "2021-03-01", "vote_average": rating, "vote_count": votes}
    return _normalize_tmdb_result(row, media_type, "tmdb_discover")


ENGLISH_SHOW = lambda title="English Show": _tmdb("tv", "en", [10765, 10759, 18], title)  # noqa: E731
ANIME_SHOW = lambda title="Anime Show": _tmdb("tv", "ja", [16, 10765, 10759], title)  # noqa: E731
DONGHUA_SHOW = lambda title="Donghua Show": _tmdb("tv", "zh", [16, 10765], title)  # noqa: E731
KIDS_CARTOON = lambda title="Kids Cartoon": _tmdb("tv", "en", [16, 10762, 10759], title)  # noqa: E731
KOREAN_SHOW = lambda title="Korean Show": _tmdb("tv", "ko", [10765, 18], title)  # noqa: E731


# --- what a job asks for -----------------------------------------------------

def test_an_ordinary_tv_job_asks_for_english_live_action_only():
    intent = job_intent(_job(["tv"], FSAA))
    assert intent["lanes"] == ["live_action"]
    assert intent["languages"] == ["en"] and not intent["explicit_languages"]
    assert intent["kids"] is False


def test_animation_lanes_come_only_from_the_job():
    assert set(job_intent(_job(["anime"]))["lanes"]) == {"anime", "donghua"}
    assert set(job_intent(_job(["tv"], ["donghua"]))["lanes"]) == {"donghua"}
    assert set(job_intent(_job(["movie", "tv"], ["animation"]))["lanes"]) == {"anime", "donghua", "animation"}
    mixed = job_intent(_job(["tv", "movie"], FSAA + ["animation", "anime"]))
    assert set(mixed["lanes"]) == {"live_action", "anime", "donghua", "animation"}


def test_the_job_language_replaces_the_default():
    intent = job_intent(_job(["tv"], FSAA, language="ko"))
    assert intent["languages"] == ["ko"] and intent["explicit_languages"]
    assert intent_tier(KOREAN_SHOW(), intent) == PRIMARY
    assert intent_tier(ENGLISH_SHOW(), intent) == SECONDARY


def test_jobs_without_the_flag_have_no_intent():
    # Content to Watch, AI Search and the offline harness keep their measured behaviour.
    assert job_intent({"media_types": ["tv"], "filters": {"include_genres": FSAA}}) is None
    assert job_intent({"job_intent": False, "media_types": ["tv"]}) is None


# --- tiers and job_fit -------------------------------------------------------

def test_tiers_for_an_english_tv_job():
    intent = job_intent(_job(["tv"], FSAA))
    assert intent_tier(ENGLISH_SHOW(), intent) == PRIMARY
    assert intent_tier(KOREAN_SHOW(), intent) == SECONDARY
    assert intent_tier(ANIME_SHOW(), intent) == OFF_INTENT
    assert intent_tier(DONGHUA_SHOW(), intent) == OFF_INTENT
    assert intent_tier(KIDS_CARTOON(), intent) == OFF_INTENT
    live_kids = _tmdb("tv", "en", [10762, 10759], "Live Kids")
    assert content_lane(live_kids) == "live_action" and intent_tier(live_kids, intent) == OFF_INTENT


def test_anime_and_donghua_jobs_still_want_anime_and_donghua():
    anime_job = job_intent(_job(["anime"]))
    assert intent_tier(ANIME_SHOW(), anime_job) == PRIMARY
    assert intent_tier(DONGHUA_SHOW(), anime_job) == PRIMARY
    donghua_job = job_intent(_job(["tv", "anime"], ["donghua"]))
    assert intent_tier(DONGHUA_SHOW(), donghua_job) == PRIMARY
    assert intent_tier(ANIME_SHOW(), donghua_job) == OFF_INTENT
    # An anime job that names English still wants Japanese anime: anime is defined by origin.
    assert intent_tier(ANIME_SHOW(), job_intent(_job(["anime"], language="en"))) == PRIMARY


def test_job_fit_orders_english_over_distant_languages_over_other_lanes():
    intent = job_intent(_job(["tv"], FSAA))
    spanish = _tmdb("tv", "es", [10765], "Spanish Show")
    assert job_fit(ENGLISH_SHOW(), intent) == 1.0
    assert job_fit(spanish, intent) == -0.6
    assert job_fit(KOREAN_SHOW(), intent) == -1.0
    assert job_fit(ANIME_SHOW(), intent) == -1.0
    family = _tmdb("tv", "en", [10765, 10751], "Family Fantasy")
    assert intent_tier(family, intent) == PRIMARY and job_fit(family, intent) == 0.6


# --- scoring -----------------------------------------------------------------

def test_scoring_without_an_intent_is_unchanged():
    rows = score_candidates([ENGLISH_SHOW(), ANIME_SHOW()], {})
    assert all("job_fit" not in row["score_components"] for row in rows)


def test_job_terms_order_the_list_but_leave_match_score_alone():
    intent = job_intent(_job(["tv"], FSAA))
    plain = {row["title"]: row for row in score_candidates([ENGLISH_SHOW(), ANIME_SHOW()], {})}
    scored = score_candidates([ANIME_SHOW(), ENGLISH_SHOW()], {}, intent=intent)
    assert scored[0]["title"] == "English Show"
    for row in scored:
        assert row["match_score"] == plain[row["title"]]["match_score"]
    anime = next(row for row in scored if row["title"] == "Anime Show")
    assert anime["why_penalties"][0] == "is anime, which this job did not ask for"


# --- final selection ---------------------------------------------------------

def _anime_heavy_scored_pool(intent):
    """Anime the profile loves, plus English series it likes less."""
    pool = [ANIME_SHOW("Anime %d" % i) for i in range(30)]
    pool += [DONGHUA_SHOW("Donghua %d" % i) for i in range(8)]
    pool += [KIDS_CARTOON("Cartoon %d" % i) for i in range(8)]
    pool += [KOREAN_SHOW("Korean %d" % i) for i in range(6)]
    pool += [ENGLISH_SHOW("English %d" % i) for i in range(24)]
    taste = {"media_types": {"anime": {"affinity": 1.0, "confidence": 1.0},
                             "tv": {"affinity": 0.3, "confidence": 1.0}},
             "languages": {"ja": {"affinity": 1.0, "confidence": 1.0},
                           "en": {"affinity": 0.2, "confidence": 1.0}}}
    return score_candidates(pool, taste, intent=intent)


def test_an_english_tv_job_is_english_live_action_even_for_an_anime_profile():
    job = _job(["tv"], FSAA)
    intent = job_intent(job)
    ranked = sorted(_anime_heavy_scored_pool(intent), key=lambda row: intent_tier(row, intent))
    picked = select_final(ranked, {**job, "final_recommendation_limit": 20})
    assert len(picked) == 20
    assert all(content_lane(row) == "live_action" and row["original_language"] == "en" for row in picked)
    # Without the intent the same profile hands the lanes their own shares.
    old = apply_lane_balance(_anime_heavy_scored_pool(None), 20)
    assert sum(1 for row in old if content_lane(row) != "live_action") > 10


def test_other_languages_and_other_lanes_only_fill_and_other_lanes_are_capped():
    job = _job(["tv"], FSAA)
    intent = job_intent(job)
    pool = [row for row in _anime_heavy_scored_pool(intent) if not row["title"].startswith("English")]
    pool += [row for row in _anime_heavy_scored_pool(intent) if row["title"] in {"English 0", "English 1"}]
    ranked = sorted(pool, key=lambda row: intent_tier(row, intent))
    picked = select_final(ranked, {**job, "final_recommendation_limit": 20})
    tiers = [intent_tier(row, intent) for row in picked]
    assert tiers == sorted(tiers)
    assert tiers[:2] == [PRIMARY, PRIMARY]
    assert SECONDARY in tiers
    assert tiers.count(OFF_INTENT) <= 2


def test_an_anime_job_keeps_its_anime():
    job = _job(["anime"])
    intent = job_intent(job)
    ranked = sorted(_anime_heavy_scored_pool(intent), key=lambda row: intent_tier(row, intent))
    picked = select_final(ranked, {**job, "final_recommendation_limit": 20})
    assert {content_lane(row) for row in picked} <= {"anime", "donghua"}
    assert sum(1 for row in picked if content_lane(row) == "anime") >= 10


def test_a_mixed_job_keeps_every_lane_but_gives_live_action_the_majority():
    job = _job(["tv"], FSAA + ["animation", "anime"])
    intent = job_intent(job)
    ranked = sorted(_anime_heavy_scored_pool(intent), key=lambda row: intent_tier(row, intent))
    picked = select_final(ranked, {**job, "final_recommendation_limit": 12})
    lanes = [content_lane(row) for row in picked]
    assert len(picked) == 12
    assert lanes.count("live_action") >= 7
    assert {"anime", "donghua"} <= set(lanes)
    # Kids' cartoons are animation, but the job did not ask for kids.
    assert not any(row["title"].startswith("Cartoon") for row in picked)


def test_run_pipeline_puts_the_job_tier_first():
    job = {**_job(["tv"], FSAA), "candidate_sources": ["tmdb_discover"], "final_recommendation_limit": 4,
           "exclusions": {}}
    result = run_pipeline(job, history=[], extra_candidates=[ANIME_SHOW(), KOREAN_SHOW(), ENGLISH_SHOW()])
    assert [row["title"] for row in result["ranked"]] == ["English Show", "Korean Show", "Anime Show"]
    assert [row["job_tier"] for row in result["ranked"]] == [PRIMARY, SECONDARY, OFF_INTENT]


# --- sources -----------------------------------------------------------------

def test_discover_lane_filters_follow_the_job():
    assert job_intent_lane_filters({"media_types": ["tv"], "filters": {"include_genres": FSAA}}) == {}
    tv = job_intent_lane_filters(_job(["tv"], FSAA))
    assert tv == {"discover_without_genres": [16, 10762], "preferred_languages": ["en"]}
    assert job_intent_lane_filters(_job(["tv"], FSAA, language="en")) == {"discover_without_genres": [16, 10762]}
    animation = job_intent_lane_filters(_job(["tv"], ["animation"]))
    assert 16 not in animation["discover_without_genres"]
    assert job_intent_lane_filters(_job(["tv"], ["kids"])) == {
        "discover_without_genres": [16], "preferred_languages": ["en"]}


def test_discover_asks_for_the_preferred_language_first(monkeypatch):
    calls = []

    async def fake_page(path, params, api_key=None):
        calls.append(dict(params))
        if params.get("with_original_language") == "en":
            return [{"id": 1, "name": "Only English", "original_language": "en", "genre_ids": [10765],
                     "first_air_date": "2020-01-01", "vote_count": 99}], 1
        return [{"id": 2, "name": "Anything", "original_language": "es", "genre_ids": [10765],
                 "first_air_date": "2020-01-01", "vote_count": 99}], 1

    monkeypatch.setattr(tmdb, "_tmdb_page", fake_page)
    job = {"candidate_limit": 2, "filters": {"include_genres": ["fantasy"], "preferred_languages": ["en"],
                                             "discover_without_genres": [16, 10762]}}
    rows = asyncio.run(tmdb.tmdb_discover(job, "tv", api_key="k"))
    assert [row["title"] for row in rows] == ["Only English", "Anything"]
    assert calls[0]["with_original_language"] == "en"
    assert calls[0]["without_genres"].split(",")[-2:] == ["16", "10762"]
    assert "with_original_language" not in calls[-1]
    movie_calls = []

    async def fake_movie_page(path, params, api_key=None):
        movie_calls.append(dict(params))
        return [], 0

    monkeypatch.setattr(tmdb, "_tmdb_page", fake_movie_page)
    asyncio.run(tmdb.tmdb_discover(job, "movie", api_key="k"))
    # Kids (10762) exists only on /discover/tv.
    assert movie_calls[0]["without_genres"] == "16"


def test_similarity_seeds_come_from_the_lane_the_job_asks_for():
    seeds = [
        {"title": "Frieren", "type": "show", "tmdb_id": 1, "genres": ["Animation", "Fantasy"], "original_language": "ja"},
        {"title": "Solo Movie", "type": "movie", "tmdb_id": 2, "genres": ["Action"], "original_language": "en"},
        {"title": "The Expanse", "type": "show", "tmdb_id": 3, "genres": ["Sci-Fi"], "original_language": "en"},
        {"title": "Kingdom", "type": "show", "tmdb_id": 4, "genres": ["Drama"], "original_language": "ko"},
    ]
    taste = {"seed_docs": seeds, "lane_seed_docs": seeds}
    assert [row["title"] for row in related_seeds([], taste)] == ["Frieren", "Solo Movie", "The Expanse", "Kingdom"]
    assert [row["title"] for row in related_seeds([], taste, job=_job(["tv"], FSAA))] == ["The Expanse"]
    assert [row["title"] for row in related_seeds([], taste, job=_job(["anime"]))] == ["Frieren"]
    assert seed_fits(seeds[1], job_intent(_job(["movie", "tv"])), ["movie", "tv"])


def test_trakt_recommendations_parse_the_bare_objects_trakt_sends():
    show = {"title": "Attack on Titan", "year": 2013, "ids": {"trakt": 1420, "tmdb": 1429},
            "genres": ["fantasy", "anime", "action"], "language": "ja", "country": "jp",
            "rating": 8.9, "votes": 20195}
    movie = {"title": "Star Trek", "year": 2009, "ids": {"tmdb": 13475}, "genres": ["science-fiction"],
             "language": "en", "country": "us"}
    parsed_show = parse_recommendation_entry(show, "shows")
    parsed_movie = parse_recommendation_entry(movie, "movies")
    assert parsed_show["title"] == "Attack on Titan" and parsed_show["type"] == "show"
    assert parsed_show["original_language"] == "ja" and content_lane(parsed_show) == "anime"
    assert parsed_movie["type"] == "movie" and parsed_movie["tmdb_id"] == 13475
    assert parse_recommendation_entry({"movie": movie})["title"] == "Star Trek"
