"""Gay romance, Kids and the genres every saved job asks for since 2026-09-29.

Gilbert: "alla jobs bara söker genre action adventure gay romance sci-fi fantasy
animations kids" - and, asked, Gay romance is one genre: romance with an LGBTQ
theme (gay, lesbian, bi, trans, queer), never ordinary romance. No provider has
that genre, so it is read from TMDb keywords and AniList tags
(filter_engine.is_gay_romance). The keywords below are TMDb's own for these
titles, looked up 2026-09-29 and cut to the ones that decide.
"""

import asyncio

import providers.tmdb as tmdb
from providers.trakt import trakt_genre_filter
from recommendation.filter_engine import (
    GENRE_ALIASES,
    apply_filters,
    candidate_genres,
    canonical_genres,
    is_gay_romance,
)
from recommendation.job_intent import job_genre_fit, job_intent

JOB_GENRES = ["Action", "Adventure", "Gay Romance", "Sci-Fi", "Fantasy", "Animation", "Kids"]
MEDIA = ["movie", "tv", "anime"]


def _film(title, genres, keywords=(), **extra):
    return {"title": title, "media_type": "movie", "type": "movie", "original_language": "en",
            "genres": list(genres), "tmdb_keywords": list(keywords), **extra}


def _series(title, genres, keywords=(), **extra):
    return {"title": title, "media_type": "tv", "type": "show", "original_language": "en",
            "genres": list(genres), "tmdb_keywords": list(keywords), **extra}


def _filter(row, include=JOB_GENRES, **filters):
    return apply_filters(row, {"include_genres": list(include), "media_types": MEDIA, **filters})


LGBTQ_ROMANCE = [
    _film("Call Me by Your Name", ["Romance", "Drama"], ["italy", "summer", "love", "first love", "lgbt", "gay theme"]),
    _film("Portrait of a Lady on Fire", ["Drama", "Romance"], ["painter", "lesbian relationship", "lgbt", "lesbian"]),
    _film("Carol", ["Romance", "Drama"], ["1950s", "lesbian relationship", "lgbt", "lesbian"]),
    _film("Red, White & Royal Blue", ["Comedy", "Romance"], ["prince", "enemies to lovers", "boys' love (bl)"]),
    _film("Brokeback Mountain", ["Drama", "Romance"], ["cowboy", "gay romance"]),
    _film("The Birdcage", ["Comedy", "Romance"], ["drag queen", "coming out", "lgbt"]),
    _series("Heartstopper", ["Drama"], ["high school", "lgbt", "gay theme", "boys' love (bl)", "romance"]),
    _series("Fellow Travelers", ["Drama"], ["1950s", "lgbt", "gay theme", "romance", "historical romance"]),
    _series("The L Word", ["Drama", "Soap"], ["lesbian relationship", "lgbt", "lesbian"]),
]
#: Straight romances TMDb also marks for a gay friend or a side character.
STRAIGHT_ROMANCE = [
    _film("She's the Man", ["Comedy", "Romance"], ["cross dressing", "soccer", "gay theme"]),
    _film("Clueless", ["Comedy", "Romance"], ["high school", "gay theme"]),
    _film("Set It Up", ["Romance", "Comedy"], ["assistant", "workplace romance", "lgbt"]),
    _film("Rock of Ages", ["Comedy", "Drama", "Romance", "Music"], ["love triangle", "coming out"]),
    _series("Bridgerton", ["Drama"], ["regency", "love", "romance", "lgbt"]),
    _film("Notting Hill", ["Romance", "Comedy"], ["bookshop", "love", "actress"]),
]


# --- what Gay romance is ---------------------------------------------------------

def test_an_lgbtq_romance_is_gay_romance():
    for row in LGBTQ_ROMANCE:
        assert is_gay_romance(row), row["title"]
        assert "gay romance" in candidate_genres(row)
        assert _filter(row, ["Gay Romance"]) == (True, None), row["title"]


def test_ordinary_romance_is_not_gay_romance():
    for row in STRAIGHT_ROMANCE:
        assert not is_gay_romance(row), row["title"]
        assert _filter(row, ["Gay Romance"]) == (False, "rejected_genre"), row["title"]
        # Romance is not one of the jobs' genres on its own any more.
        assert _filter(row) == (False, "rejected_genre"), row["title"]


def test_an_lgbtq_title_that_is_no_romance_is_not_gay_romance():
    thriller = _film("Knock at the Cabin", ["Horror", "Thriller", "Mystery"], ["apocalypse", "gay couple", "lgbt"])
    assert not is_gay_romance(thriller)


def test_anilist_tags_say_it_too():
    def anime(tags, genres=("Drama",)):
        return {"title": "A", "media_type": "anime", "source": "anilist", "genres": list(genres), "tags": list(tags)}

    assert is_gay_romance(anime(["Boys' Love", "School"]))
    assert is_gay_romance(anime(["Yuri"]))
    assert is_gay_romance(anime(["LGBTQ+ Themes", "Transgender"], ["Romance"]))
    # One LGBTQ tag on a romance is not enough, as on TMDb.
    assert not is_gay_romance(anime(["LGBTQ+ Themes"], ["Romance"]))
    # TMDb's "yuri" is also put on magical-girl subtext: there it is one LGBTQ keyword.
    assert not is_gay_romance(_series("Magical Girls", ["Animation"], ["yuri", "romance"]))


def test_names_for_the_genre():
    assert canonical_genres(["Gay Romance", "gay romance", "LGBTQ Romance", "LGBTQ+ Romance", "Queer Romance",
                             "HBTQ romance"]) == {"gay romance"}
    # The taste profile reads GENRE_ALIASES; its spellings are unchanged.
    assert "gay romance" not in GENRE_ALIASES.values() and "lgbt romance" not in GENRE_ALIASES


# --- Kids -------------------------------------------------------------------

def test_kids_takes_family_films_and_kids_series():
    family_film = tmdb._normalize_tmdb_result(
        {"id": 1, "title": "Paddington", "genre_ids": [10751, 35], "original_language": "en",
         "release_date": "2014-11-24", "vote_count": 900}, "movie", "tmdb_discover")
    kids_series = tmdb._normalize_tmdb_result(
        {"id": 2, "name": "Kids Show", "genre_ids": [10762], "original_language": "en",
         "first_air_date": "2020-01-01", "vote_count": 90}, "tv", "tmdb_discover")
    family_drama = tmdb._normalize_tmdb_result(
        {"id": 3, "name": "Family Drama", "genre_ids": [10751, 18], "original_language": "en",
         "first_air_date": "2020-01-01", "vote_count": 90}, "tv", "tmdb_discover")
    trakt_children = _series("Trakt Kids", ["children"])
    assert _filter(family_film, ["Kids"]) == (True, None)
    assert _filter(kids_series, ["Kids"]) == (True, None)
    assert _filter(trakt_children, ["Kids"]) == (True, None)
    # A series has a Kids genre of its own; a family drama is not one.
    assert _filter(family_drama, ["Kids"]) == (False, "rejected_genre")


# --- the jobs' seven genres ------------------------------------------------------

def test_the_seven_genres_take_what_they_name_and_nothing_else():
    wanted = [
        _film("Action Film", ["Action", "Thriller"]),
        _series("Space Series", ["Sci-Fi & Fantasy", "Drama"]),
        {"title": "Frieren", "media_type": "anime", "source": "anilist", "genres": ["Drama"]},
        _film("Pixar Film", ["Animation", "Comedy"]),
        _series("Kids Series", ["Kids"]),
        LGBTQ_ROMANCE[0],
    ]
    unwanted = [
        _series("Crime Drama", ["Crime", "Drama"]),
        _film("Documentary", ["Documentary"]),
        _film("Comedy", ["Comedy"]),
        STRAIGHT_ROMANCE[-1],
    ]
    for row in wanted:
        assert _filter(row, exclude_genres=["horror"]) == (True, None), row["title"]
    for row in unwanted:
        assert _filter(row, exclude_genres=["horror"]) == (False, "rejected_genre"), row["title"]


def test_what_the_seven_genres_ask_of_a_job():
    intent = job_intent({"job_intent": True, "media_types": MEDIA, "filters": {"include_genres": JOB_GENRES}})
    assert intent["lanes"] == ["animation", "anime", "donghua", "live_action"]
    assert intent["kids"] is True
    assert "gay romance" in intent["include_genres"]
    # Gay romance and a family film count among the job's genres when the list is ordered.
    assert job_genre_fit(LGBTQ_ROMANCE[0], intent) == 0.5
    assert job_genre_fit(_film("Family Adventure", ["Family", "Adventure"]), intent) == 1.0


# --- where the jobs look ---------------------------------------------------------

def test_tmdb_is_asked_for_gay_romance_by_keyword():
    film_lanes = tmdb.theme_lanes(JOB_GENRES, "movie")
    assert film_lanes[0]["with_genres"] == "10749" and "158718" in film_lanes[0]["with_keywords"].split("|")
    assert film_lanes[1]["with_genres"] is None and "289844" in film_lanes[1]["with_keywords"].split("|")
    (series_lane,) = tmdb.theme_lanes(JOB_GENRES, "tv")
    assert {"158718", "289844", "240305"} <= set(series_lane["with_keywords"].split("|"))
    assert series_lane["with_genres"] is None
    assert tmdb.theme_lanes(["Romance", "Action"], "movie") == []


def test_discover_runs_the_gay_romance_lanes_next_to_the_genre_lane(monkeypatch):
    calls = []

    async def fake_page(path, params, api_key=None):
        calls.append(dict(params))
        return [], 0

    monkeypatch.setattr(tmdb, "_tmdb_page", fake_page)
    job = {"candidate_limit": 40, "filters": {"include_genres": JOB_GENRES}}
    asyncio.run(tmdb.tmdb_discover(job, "movie", api_key="k"))
    keyword_calls = [call for call in calls if "with_keywords" in call]
    assert any(call.get("with_genres") == "10749" and "158718" in call["with_keywords"] for call in keyword_calls)
    assert any("with_genres" not in call and "289844" in call["with_keywords"] for call in keyword_calls)
    genre_lane = [call for call in calls if "with_keywords" not in call][0]
    assert set(genre_lane["with_genres"].split("|")) == {"28", "12", "878", "14", "16", "10751"}
    calls.clear()
    asyncio.run(tmdb.tmdb_discover(job, "tv", api_key="k"))
    assert any("with_genres" not in call and "158718" in call.get("with_keywords", "") for call in calls)
    genre_lane = [call for call in calls if "with_keywords" not in call][0]
    assert set(genre_lane["with_genres"].split("|")) == {"10759", "10765", "16", "10762"}


def test_trakt_is_asked_for_kids_the_way_each_list_names_it():
    assert trakt_genre_filter(["Kids", "Action"], "shows") == "action,children,family"
    assert trakt_genre_filter(["Kids", "Action"], "movies") == "action,family"
    assert trakt_genre_filter(["Kids"]) == "family"
    # Trakt has no such genre; TMDb's keyword lanes look for it.
    assert trakt_genre_filter(["Gay Romance"]) is None
