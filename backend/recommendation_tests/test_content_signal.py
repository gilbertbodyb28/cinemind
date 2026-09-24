"""Regressions for the content signal the profile was missing entirely.

Measured on Gilbert's real data before this work: 0 of 633 history titles
carried a synopsis and only 44 carried a language, so every title sharing one
genre label scored identically. Two unrelated shows - a Spanish thriller and a
Tagalog soap - came out bit-identical to four decimals and were both explained
as "plays like Daredevil: Born Again".
"""

from providers.tmdb_enrich import apply_enrichment, enrichment_key
from recommendation.ranking_engine import apply_diversity, language_fit
from recommendation.similarity import pair_similarity
from recommendation.taste_engine import build_taste_snapshot, media_bucket


def _watched(title, **extra):
    return {
        "title": title, "year": 2020, "media_type": "tv", "provider": "trakt",
        "canonical_media_id": title.lower().replace(" ", "-"),
        "genres": ["Action", "Drama"], "original_language": "en",
        "rating": 9, "rating_scale": 10, **extra,
    }


def test_two_titles_sharing_only_genres_are_no_longer_identical():
    liked = _watched("Reference", tmdb_keywords=["superhero", "vigilante", "new york city"],
                     cast=["Charlie Cox"], creators=["Dario Scardapane"])
    same_genres_only = {"title": "A", "year": 2022, "media_type": "tv", "genres": ["Action", "Drama"]}
    real_match = {"title": "B", "year": 2022, "media_type": "tv", "genres": ["Action", "Drama"],
                  "tmdb_keywords": ["superhero", "vigilante", "new york city"], "cast": ["Charlie Cox"]}

    weak = pair_similarity(same_genres_only, liked)
    strong = pair_similarity(real_match, liked)
    assert strong > weak, (strong, weak)


def test_a_language_the_viewer_never_watches_counts_against_a_title():
    history = [_watched("Show %d" % index) for index in range(40)]
    taste = build_taste_snapshot(history)

    assert language_fit({"original_language": "en"}, taste) > 0
    assert language_fit({"original_language": "tl"}, taste) < 0, "an unwatched language must not score as neutral"


def test_a_thin_language_profile_stays_neutral_rather_than_guessing():
    """Two titles is not enough to declare every other language wrong."""
    taste = build_taste_snapshot([_watched("Only One"), _watched("Only Two")])
    assert language_fit({"original_language": "tl"}, taste) == 0.0


def test_anime_is_recognised_without_an_animation_genre():
    """Trakt and AniList report anime genres as "Action, Adventure" - no "Animation"."""
    boruto = {"title": "Boruto", "media_type": "tv", "type": "show",
              "genres": ["Action", "Adventure", "Fantasy"], "original_language": "ja",
              "tmdb_keywords": ["anime", "based on manga", "ninja"]}
    assert media_bucket(boruto) == "anime"

    film = {**boruto, "title": "Demon Slayer: Mugen Train", "media_type": "movie", "type": "movie"}
    assert media_bucket(film) == "anime_movie"


def test_a_live_action_manga_adaptation_is_not_swept_up_as_anime():
    live = {"title": "Rurouni Kenshin", "media_type": "movie", "type": "movie",
            "genres": ["Action", "Drama"], "original_language": "ja",
            "tmdb_keywords": ["based on manga", "samurai", "meiji era"]}
    assert media_bucket(live) == "movie"


def test_format_affinity_is_preference_not_row_count():
    """205 anime rows must not outrank a format the viewer rates higher per title."""
    history = [_watched("Anime %d" % index, media_type="anime", rating=7) for index in range(30)]
    history += [_watched("Film %d" % index, media_type="movie", type="movie", rating=10) for index in range(4)]
    taste = build_taste_snapshot(history)
    assert taste["media_types"]["movie"]["affinity"] > taste["media_types"]["anime"]["affinity"]


def test_a_long_list_does_not_drag_the_weak_pool_in_behind_it():
    """A job asking for 250 titles used to empty the candidate pool into the list."""
    rows = [{"title": "Strong %d" % index, "rank_score": 6.0, "media_type": "tv"} for index in range(4)]
    rows += [{"title": "Weak %d" % index, "rank_score": 0.2, "media_type": "tv"} for index in range(200)]
    chosen = apply_diversity(rows, limit=250)
    assert all(row["rank_score"] >= 4.0 for row in chosen), [row["title"] for row in chosen[-3:]]


def test_enrichment_fills_gaps_without_overwriting_what_a_provider_gave():
    row = {"title": "Known", "tmdb_id": 1, "media_type": "movie", "type": "movie",
           "genres": ["Comedy"], "original_language": "en"}
    apply_enrichment(row, {"overview": "A synopsis.", "original_language": "fr",
                           "genres": ["Drama"], "tmdb_keywords": ["heist"]})
    assert row["original_language"] == "en", "a provider's own value wins"
    assert row["genres"] == ["Comedy"]
    assert row["synopsis"] == "A synopsis."
    assert row["tmdb_keywords"] == ["heist"]


def test_anime_films_are_looked_up_under_the_movie_endpoint():
    assert enrichment_key({"tmdb_id": 7, "media_type": "anime", "type": "movie"}) == "movie:7"
    assert enrichment_key({"tmdb_id": 7, "media_type": "anime", "type": "show"}) == "tv:7"
    assert enrichment_key({"title": "No id"}) is None
