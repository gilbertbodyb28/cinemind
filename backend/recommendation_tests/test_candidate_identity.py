"""Identity and metadata regressions in recommendation candidate handling."""

from recommendation.candidate_engine import merge_candidate_sources, normalize_candidate
from recommendation.exclusion_engine import apply_exclusions, build_exclusion_context


def _context(history):
    return build_exclusion_context(history, [], [], [], [])


def test_watched_show_matches_tv_candidate_by_tmdb_id_across_title_aliases():
    watched = {"title": "Original Title", "year": 2020, "type": "show", "tmdb_id": 1234}
    candidate = {"title": "English Title", "year": 2021, "media_type": "tv", "tmdb_id": 1234}

    assert apply_exclusions(candidate, _context([watched])) == (False, "rejected_already_watched")


def test_watched_anilist_title_matches_by_id_across_title_aliases():
    watched = {"title": "Japanese Title", "year": 2020, "type": "anime", "anilist_id": 4321}
    candidate = {"title": "English Title", "year": 2021, "type": "anime", "anilist_id": 4321}

    assert apply_exclusions(candidate, _context([watched])) == (False, "rejected_already_watched")


def test_watched_title_matches_by_imdb_id_across_title_aliases():
    watched = {"title": "Original Title", "year": 2020, "type": "movie", "imdb_id": "tt0000123"}
    candidate = {"title": "English Title", "year": 2021, "type": "movie", "imdb_id": "tt0000123"}

    assert apply_exclusions(candidate, _context([watched])) == (False, "rejected_already_watched")


def test_watched_title_matches_by_other_stable_source_ids():
    for field, value in (("tvdb_id", 222), ("trakt_id", 333), ("simkl_id", 444)):
        watched = {"title": "Original Title", "year": 2020, "type": "show", field: value}
        candidate = {"title": "English Title", "year": 2021, "media_type": "tv", field: value}
        assert apply_exclusions(candidate, _context([watched])) == (False, "rejected_already_watched")


def test_watched_title_matches_by_canonical_id_across_title_aliases():
    watched = {"title": "Original Title", "year": 2020, "type": "show", "canonical_media_id": "mid_abc"}
    candidate = {"title": "English Title", "year": 2021, "media_type": "tv", "canonical_media_id": "mid_abc"}

    assert apply_exclusions(candidate, _context([watched])) == (False, "rejected_already_watched")


def test_same_title_and_year_in_different_media_types_are_distinct():
    watched = {"title": "The Example", "year": 2020, "type": "movie"}
    candidate = {"title": "Example", "year": 2020, "type": "show"}

    assert apply_exclusions(candidate, _context([watched])) == (True, None)


def test_legacy_blacklist_without_media_type_still_excludes_anime():
    blacklist = {"title": "The Example", "year": 2020, "canonical_media_id": "slug:The Example:2020"}
    context = build_exclusion_context([], [], [], [], [blacklist])
    candidate = {"title": "Example", "year": 2020, "type": "anime"}

    assert apply_exclusions(candidate, context) == (False, "rejected_blacklisted")


def test_legacy_blacklist_generator_is_not_lost_after_identity_indexing():
    blacklist = {"title": "Example", "year": 2020, "canonical_media_id": "slug:Example:2020"}
    context = build_exclusion_context([], [], [], [], (row for row in [blacklist]))

    assert apply_exclusions({"title": "Example", "year": 2020, "type": "anime"}, context) == (
        False, "rejected_blacklisted"
    )


def test_movie_and_tv_with_same_tmdb_numeric_id_both_survive_merge():
    movie = {"title": "Film", "year": 2020, "type": "movie", "tmdb_id": 900}
    show = {"title": "Series", "year": 2020, "type": "show", "tmdb_id": 900}

    assert merge_candidate_sources([movie], [show]) == [movie, show]


def test_cross_provider_aliases_with_shared_anilist_id_are_deduplicated():
    first = {"title": "Japanese Title", "year": 2020, "type": "anime", "anilist_id": 500}
    second = {"title": "English Title", "year": 2021, "type": "anime", "anilist_id": 500}

    assert merge_candidate_sources([first], [second]) == [first]


def test_normalized_candidate_keeps_metadata_used_by_filters_and_ranker():
    raw = {
        "title": "Movie", "year": 2027, "type": "movie", "tmdb_id": 123,
        "popularity": 71.2, "release_date": "2027-04-03", "origin_countries": ["JP"],
        "tags": ["Survival"], "keywords": ["Island"], "studios": ["Studio A"],
        "format": "MOVIE", "episodes": 1, "streaming_providers": ["Provider A"],
        "tvdb_id": 88, "trakt_id": 99, "simkl_id": 77, "canonical_media_id": "mid_123",
    }

    candidate = normalize_candidate(raw, "seed_expand", seed="history_genres")

    for field in (
        "popularity", "release_date", "origin_countries", "tags", "keywords", "studios",
        "format", "episodes", "streaming_providers", "tvdb_id", "trakt_id", "simkl_id",
        "canonical_media_id",
    ):
        assert candidate[field] == raw[field]
