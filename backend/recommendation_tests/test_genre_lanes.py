"""Genre OR-matching, anime/donghua/animation lanes and lane-balanced job picks.

Regression for 2026-09-24: Gilbert's jobs asked for Anime + Animation + Fantasy
+ Sci-Fi + Action + Adventure and came back as anime films only, donghua was
nearly never fetched, and TMDb/Trakt genre spellings failed the genre filter.
"""

from datetime import datetime, timezone

from providers.anilist import parse_recommendation_media
from providers.tmdb import _genre_ids, _normalize_tmdb_result, animation_lane_languages, default_vote_floor
from recommendation.filter_engine import apply_filters, candidate_genres, genre_matches
from recommendation.media_identity import content_lane
from recommendation.pipeline import select_final
from recommendation.ranking_engine import apply_diversity, apply_lane_balance, selection_lane

ALL_SEVEN = ["anime", "animation", "fantasy", "sci-fi", "action", "adventure", "donghua"]
MEDIA = ["movie", "tv", "anime"]


def _tmdb(media_type, lang, genre_ids, title="T"):
    row = {"id": hash(title) % 10**6, "name": title, "title": title, "original_language": lang,
           "genre_ids": genre_ids, "first_air_date": "2025-03-01", "release_date": "2025-03-01",
           "vote_average": 7.5, "vote_count": 100}
    return _normalize_tmdb_result(row, media_type, "tmdb_discover")


def _passes(candidate, include=None, media=MEDIA, **filters):
    ok, _reason = apply_filters(candidate, {"include_genres": include or [], "media_types": media, **filters})
    return ok


# --- lanes -----------------------------------------------------------------

def test_content_lane_tells_anime_donghua_animation_and_live_action_apart():
    assert content_lane(_tmdb("tv", "ja", [16, 10759])) == "anime"
    assert content_lane(_tmdb("tv", "zh", [16, 10765])) == "donghua"
    assert content_lane(_tmdb("movie", "cn", [16, 14])) == "donghua"
    assert content_lane(_tmdb("movie", "en", [16, 12])) == "animation"
    # Origin alone never makes a title donghua: Chinese live action stays live action.
    assert content_lane(_tmdb("tv", "zh", [18, 10759])) == "live_action"
    assert content_lane(_tmdb("tv", "en", [10765])) == "live_action"


def test_anilist_rows_keep_their_country_of_origin():
    donghua = parse_recommendation_media({"id": 1, "title": {"romaji": "Mu Shen Ji"}, "genres": ["Action"],
                                          "countryOfOrigin": "CN"})
    anime = parse_recommendation_media({"id": 2, "title": {"romaji": "Frieren"}, "genres": ["Fantasy"],
                                        "countryOfOrigin": "JP"})
    unknown = parse_recommendation_media({"id": 3, "title": {"romaji": "Old"}, "genres": ["Drama"]})
    assert content_lane(donghua) == "donghua"
    assert content_lane(anime) == "anime"
    assert content_lane(unknown) == "anime"
    # Ranking reads original_language; this fix must not feed it a new value.
    assert "original_language" not in donghua


# --- genre OR-matching -----------------------------------------------------

def test_tmdb_combined_tv_genres_match_both_halves():
    fantasy_show = _tmdb("tv", "en", [10765, 18])      # "Sci-Fi & Fantasy", Drama
    adventure_show = _tmdb("tv", "en", [10759])        # "Action & Adventure"
    assert _passes(fantasy_show, ["fantasy"])
    assert _passes(adventure_show, ["adventure"])
    assert {"sci-fi", "fantasy"} <= candidate_genres(fantasy_show)


def test_provider_spellings_are_one_genre():
    trakt = {"title": "X", "media_type": "tv", "genres": ["Science-Fiction", "Drama"]}
    details = {"title": "Y", "media_type": "tv", "genres": ["Action & Adventure", "Sci-Fi & Fantasy"]}
    assert _passes(trakt, ["sci-fi"])
    assert _passes(trakt, ["Science Fiction"])
    assert _passes(details, ["adventure"]) and _passes(details, ["fantasy"])
    assert _passes({"title": "Z", "media_type": "tv", "genres": ["Talk-Show"]}, exclude_genres=["talk show"]) is False


def test_include_genres_are_or_never_and():
    only_scifi = {"title": "S", "media_type": "movie", "genres": ["Sci-Fi"], "original_language": "en"}
    assert _passes(only_scifi, ALL_SEVEN)
    assert genre_matches(only_scifi, set(ALL_SEVEN))
    comedy = {"title": "C", "media_type": "movie", "genres": ["Comedy"], "original_language": "en"}
    assert not _passes(comedy, ALL_SEVEN)


def test_fantasy_scifi_action_adventure_do_not_require_animation():
    for genre_ids, include in (([10765], ["fantasy", "sci-fi"]), ([10759], ["action", "adventure"])):
        assert _passes(_tmdb("tv", "en", genre_ids), include)
    assert _passes(_tmdb("movie", "en", [28, 12]), ["action", "adventure"])
    assert _passes(_tmdb("movie", "en", [878]), ["sci-fi"])


def test_anime_donghua_and_animation_are_their_own_categories():
    anime, donghua = _tmdb("tv", "ja", [16, 10759]), _tmdb("tv", "zh", [16, 10765])
    western = _tmdb("movie", "en", [16, 35])
    assert _passes(anime, ["anime"]) and not _passes(donghua, ["anime"])
    assert _passes(donghua, ["donghua"]) and not _passes(anime, ["donghua"])
    assert all(_passes(row, ["animation"]) for row in (anime, donghua, western))
    assert not _passes(western, ["anime"]) and not _passes(western, ["donghua"])
    # AniList never says "Animation"; an AniList anime still is animation.
    anilist = {"title": "A", "media_type": "anime", "genres": ["Action"], "source": "anilist"}
    assert _passes(anilist, ["animation"])


def test_exclusions_still_apply():
    horror_scifi = _tmdb("movie", "en", [27, 878])
    assert not _passes(horror_scifi, ["sci-fi"], exclude_genres=["horror"])


# --- language / country ------------------------------------------------------

def test_language_filter_does_not_remove_donghua_when_the_job_wants_animation():
    donghua, anime = _tmdb("tv", "zh", [16, 14]), _tmdb("tv", "ja", [16, 14])
    chinese_drama = _tmdb("tv", "zh", [10759, 18])
    for include in (["donghua"], ["anime"], ["animation"], ALL_SEVEN):
        assert _passes(donghua, include, language="en") or include == ["anime"]
    assert _passes(donghua, ALL_SEVEN, language="en", country="US")
    assert _passes(anime, ALL_SEVEN, language="en")
    # Live action is not exempt, whatever its origin.
    assert not _passes(chinese_drama, ALL_SEVEN, language="en")
    # A job that asked for no animation at all keeps its language filter.
    assert not _passes(donghua, ["action"], media=["movie", "tv"], language="en")


def test_explicit_anime_overlay_language_wins():
    donghua = _tmdb("tv", "zh", [16, 14])
    filters = {"by_media_type": {"anime": {"languages": ["ja"]}}}
    assert not _passes(donghua, ["animation"], **filters)


# --- TMDb queries ------------------------------------------------------------

def test_animation_lane_languages():
    assert animation_lane_languages(["anime"], MEDIA) == ["ja"]
    assert animation_lane_languages(["donghua"], MEDIA) == ["zh", "cn"]
    assert animation_lane_languages(["animation"], ["movie"]) == ["ja", "zh", "cn"]
    assert animation_lane_languages([], MEDIA) == ["ja", "zh", "cn"]
    assert animation_lane_languages(["fantasy"], MEDIA) == ["ja", "zh", "cn"]
    assert animation_lane_languages(["action"], ["movie", "tv"]) == []


def test_genre_ids_are_or_joined_and_understand_spellings():
    assert _genre_ids(["Science-Fiction", "donghua"], "movie") == "878|16"
    assert set(_genre_ids(ALL_SEVEN, "tv").split("|")) == {"16", "10765", "10759"}
    assert "," not in _genre_ids(ALL_SEVEN, "movie")


def test_vote_floor_follows_the_calendar_not_a_hard_coded_year():
    year = datetime.now(timezone.utc).year
    assert default_vote_floor({}) == 50
    assert default_vote_floor({"min_year": year - 5}) == 50
    assert default_vote_floor({"min_year": year - 2}) == 10
    assert default_vote_floor({"min_year": year}) is None
    assert default_vote_floor({"min_year": year - 5, "discover_vote_floor": 5}) == 5


# --- final selection ---------------------------------------------------------

def _row(title, lane, score, kind="series"):
    base = {
        "anime": {"media_type": "anime", "genres": ["Animation"], "original_language": "ja"},
        "donghua": {"media_type": "anime", "genres": ["Animation"], "original_language": "zh"},
        "animation": {"media_type": "tv", "genres": ["Animation"], "original_language": "en"},
        "live_action": {"media_type": "tv", "genres": ["Sci-Fi"], "original_language": "en"},
    }[lane]
    row = {"title": title, "rank_score": score, **base}
    if kind == "movie":
        row.update({"format": "MOVIE"} if lane in {"anime", "donghua"} else {"media_type": "movie"})
    return row


def _anime_heavy_pool():
    pool = [_row(f"Anime Film {i}", "anime", 9.7 - i * 0.05, "movie") for i in range(30)]
    pool += [_row(f"Live {i}", "live_action", 7.2 - i * 0.1) for i in range(8)]
    pool += [_row(f"Donghua {i}", "donghua", 7.4 - i * 0.1) for i in range(6)]
    pool += [_row(f"Western {i}", "animation", 6.8 - i * 0.1, "movie") for i in range(4)]
    return sorted(pool, key=lambda row: -row["rank_score"])


def test_old_diversity_collapses_to_one_lane():
    picked = apply_diversity(_anime_heavy_pool(), 12)
    lanes = {selection_lane(row) for row in picked}
    assert "live_action:series" not in lanes and "animation:movie" not in lanes


def test_lane_balance_mixes_lanes_and_keeps_the_best_pick_first():
    pool = _anime_heavy_pool()
    picked = apply_lane_balance(pool, 12)
    lanes = {selection_lane(row) for row in picked}
    assert {"anime:movie", "live_action:series", "donghua:series", "animation:movie"} <= lanes
    assert len(picked) == 12
    assert picked[0] is pool[0]
    order = [pool.index(row) for row in picked]
    assert order == sorted(order)


def test_lane_balance_leaves_a_weak_lane_out():
    pool = _anime_heavy_pool() + [_row("Awful", "live_action", 1.0, "movie")]
    assert all(row["title"] != "Awful" for row in apply_lane_balance(pool, 40))


def test_single_lane_pool_is_unchanged():
    pool = [_row(f"A{i}", "anime", 9 - i * 0.1) for i in range(20)]
    assert apply_lane_balance(pool, 8) == apply_diversity(pool, 8)


def test_content_to_watch_keeps_the_measured_selection():
    pool = _anime_heavy_pool()
    spec = {"final_recommendation_limit": 8, "lane_balance": False}
    assert select_final(pool, spec) == apply_diversity(pool, 8)
    assert select_final(pool, {"final_recommendation_limit": 8}) == apply_lane_balance(pool, 8)
