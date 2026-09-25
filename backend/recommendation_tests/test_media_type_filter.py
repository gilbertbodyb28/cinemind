"""A TV job gets series. An anime film is a film (HANDOFF.md, omgång 5)."""

from providers.anilist import parse_recommendation_media
from providers.tmdb import anime_films_wanted, tmdb_kind
from recommendation.filter_engine import apply_filters, media_type_allowed

ANIME_FILM = {"title": "Mononoke the Movie: Chapter III", "year": 2026, "type": "anime", "media_type": "anime",
              "format": "MOVIE", "original_language": "ja", "genres": ["Animation", "Fantasy"]}
ANIME_SERIES = {"title": "Frieren", "year": 2026, "type": "anime", "media_type": "anime",
                "original_language": "ja", "genres": ["Animation", "Fantasy"]}
JAPANESE_ANIMATED_MOVIE = {"title": "Chimney Town", "year": 2026, "type": "movie", "media_type": "movie",
                           "original_language": "ja", "genres": ["Animation", "Fantasy"]}
SERIES = {"title": "Beta Crystal", "year": 2026, "type": "show", "media_type": "tv",
          "original_language": "en", "genres": ["Sci-Fi"]}


def test_a_tv_only_job_never_admits_a_film_of_any_lane():
    for film in (ANIME_FILM, JAPANESE_ANIMATED_MOVIE):
        assert not media_type_allowed(film, ["tv"])
        assert apply_filters(film, {"media_types": ["tv"]}) == (False, "rejected_media_type")
    assert media_type_allowed(ANIME_SERIES, ["tv"]) and media_type_allowed(SERIES, ["tv"])


def test_films_pass_where_the_job_takes_films_or_anime():
    assert media_type_allowed(ANIME_FILM, ["movie"]) and media_type_allowed(ANIME_FILM, ["tv", "movie"])
    assert media_type_allowed(ANIME_FILM, ["anime"]) and media_type_allowed(ANIME_SERIES, ["anime"])
    assert not media_type_allowed(SERIES, ["movie"])


def test_the_anime_film_lane_only_runs_for_jobs_that_take_films():
    assert not anime_films_wanted(["tv"])
    assert anime_films_wanted(["tv", "movie"]) and anime_films_wanted(["anime"])


def test_an_anime_film_is_looked_up_under_movie_and_keeps_its_format():
    assert tmdb_kind(ANIME_FILM) == "movie" and tmdb_kind(ANIME_SERIES) == "anime"
    row = parse_recommendation_media({"id": 1, "title": {"english": "A Film"}, "format": "MOVIE"})
    assert row["format"] == "MOVIE"
