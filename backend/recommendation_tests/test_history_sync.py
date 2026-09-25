"""The complete history sync merges; it never deletes or clobbers (HANDOFF.md, omgång 5)."""

from providers.history_sync import _title_summary, merge_changes, merge_title_changes, plan_history
from providers.trakt import apply_user_ratings, parse_history_entry
from recommendation.media_identity import merge_personal_fields


def _play(history_id, show="Arrow", trakt_id=1403, watched_at="2021-01-01T00:00:00.000Z", season=1, number=1):
    return parse_history_entry({
        "id": history_id, "watched_at": watched_at, "action": "watch", "type": "episode",
        "episode": {"season": season, "number": number, "ids": {"trakt": history_id * 10}},
        "show": {"title": show, "year": 2012, "ids": {"trakt": trakt_id, "tmdb": 1412}, "genres": ["drama"]},
    })


def _stored(row, index, **extra):
    doc = {key: value for key, value in row.items() if key not in {"provider_play_id"}}
    doc["provider_specific_metadata"] = {"action": "watch"}
    return {**doc, "_id": index, "id": "uuid-%d" % index, "poster": "p.jpg", **extra}


def test_a_rerun_imports_nothing_and_changes_nothing():
    fetched = [_play(1), _play(2, number=2)]
    stored = [{**row, "_id": index} for index, row in enumerate(fetched)]
    plan = plan_history("trakt", stored, [dict(row) for row in fetched])
    assert plan["inserts"] == [] and plan["updates"] == [] and plan["unchanged"] == 2 and plan["kept"] == []


def test_rows_from_before_play_ids_are_matched_once_as_a_multiset():
    # A season marked watched in one go: four plays, one timestamp.
    fetched = [_play(index, number=index) for index in range(1, 5)]
    stored = [_stored(fetched[index], index) for index in range(3)]
    plan = plan_history("trakt", stored, fetched)
    assert len(plan["inserts"]) == 1 and len(plan["updates"]) == 3 and plan["kept"] == []
    # The matched rows learn their play id, and nothing a provider does not own is touched.
    assert all("provider_play_id" in changes and "poster" not in changes and "id" not in changes
               for _, changes in plan["updates"])


def test_rows_the_provider_no_longer_returns_are_kept():
    stored = [_stored(_play(1), 0), _stored(_play(2, watched_at="2020-01-01T00:00:00.000Z"), 1)]
    plan = plan_history("trakt", stored, [_play(1)])
    assert [doc["_id"] for doc in plan["kept"]] == [1]


def test_a_personal_rating_is_never_removed_and_a_changed_one_keeps_the_old_value():
    stored = {"rating": 9.0, "rating_scale": 10, "genres": ["Drama"], "title": "Arrow"}
    assert merge_changes(stored, {"rating": None, "genres": [], "title": "Arrow"}) == {}
    changes = merge_changes(stored, {"rating": 7.0, "rating_scale": 10})
    assert changes["rating"] == 7.0 and changes["previous_rating"] == 9.0 and changes["previous_rating_scale"] == 10


def test_watch_count_is_times_watched_through_not_episode_plays():
    season = [_play(index, number=index, watched_at="2021-01-%02dT00:00:00.000Z" % index) for index in range(1, 11)]
    assert _title_summary(season, [])["watch_count"] == 1
    rewatch = season + [_play(100 + index, number=index, watched_at="2023-01-%02dT00:00:00.000Z" % index)
                        for index in range(1, 11)]
    summary = _title_summary(rewatch, [])
    assert summary["watch_count"] == 2
    assert summary["watched_at"].startswith("2021-01-01") and summary["last_watched_at"].startswith("2023-01-10")
    # Per-play details stay on the play rows.
    assert "history_id" not in summary["provider_specific_metadata"]
    rating = [{"title": "Arrow", "type": "show", "rating": 10.0, "rating_scale": 10, "rated_at": "2024-01-01"}]
    assert _title_summary(season, rating)["watch_count"] == 1  # a rating is not a play
    assert _title_summary(season, rating)["rating"] == 10.0


def test_title_rows_only_grow():
    stored = {"watch_count": 3, "watched_at": "2019-01-01", "last_watched_at": "2024-01-01", "rating": 8.0}
    changes = merge_title_changes(stored, {"watch_count": 2, "watched_at": "2021-01-01",
                                           "last_watched_at": "2025-01-01", "rating": 8.0})
    assert changes == {"last_watched_at": "2025-01-01"}


def test_a_rated_film_does_not_rate_the_series_with_the_same_tmdb_id():
    plays = [{"title": "Show", "year": 2011, "type": "show", "tmdb_id": 1399, "trakt_id": 1390}]
    apply_user_ratings(plays, [{"title": "Film", "year": 2011, "type": "movie", "tmdb_id": 1399,
                                "trakt_id": 7, "rating": 3.0}])
    assert plays[0].get("rating") is None


def test_merging_two_identities_keeps_the_rating_of_the_row_that_goes():
    keeper = {"rating": None, "watch_count": 1, "watched_at": "2022-01-01", "last_watched_at": "2022-01-01"}
    other = {"rating": 10.0, "rating_scale": 10, "watch_count": 2, "watched_at": "2020-01-01",
             "last_watched_at": "2021-01-01"}
    assert merge_personal_fields(keeper, other) == {"rating": 10.0, "rating_scale": 10, "watch_count": 2,
                                                    "watched_at": "2020-01-01"}
