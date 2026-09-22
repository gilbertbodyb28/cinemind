"""Behavior checks for the snapshot-only recommendation evaluator."""

import json
import math

import pytest

from evaluation.offline import build_cases, evaluate_snapshot, explicit_positive_rows, score_ranking


def _row(canonical_id, title, *, year=2020, kind="movie", **extra):
    return {
        "canonical_media_id": canonical_id,
        "title": title,
        "year": year,
        "type": kind,
        "genres": ["Drama"],
        **extra,
    }


def test_only_proven_personal_scores_and_explicit_likes_become_positive_labels():
    snapshot = {
        "history": [
            _row("catalog", "Community Favorite", rating=9.8, source="plex"),
            _row("watched", "Simply Watched", completed=True, source="trakt"),
        ],
        "media_history": [
            _row("trakt-high", "Personal Favorite", provider="trakt", rating=9, rating_scale=10),
            _row("trakt-low", "Personal Dislike", provider="trakt", rating=3, rating_scale=10),
            _row("anilist-high", "Loved Anime", kind="anime", provider="anilist", rating=9, rating_scale=10),
            _row("legacy", "Unverified Favorite", provider="anilist", favorite=True),
            _row("plex-high", "Unverified Plex Score", provider="plex", rating=9, rating_scale=10),
            _row("trakt-high", "Personal Favorite", provider="trakt", rating=9, rating_scale=10),
        ],
        "feedback": [{"canonical_media_id": "liked", "title": "Explicit Like", "year": 2021, "type": "show", "action": "like"}],
        "candidates": [],
    }

    labels = explicit_positive_rows(snapshot)

    assert {row["title"] for row in labels} == {"Personal Favorite", "Loved Anime", "Explicit Like"}
    assert len(labels) == 3


def test_every_holdout_is_removed_from_all_training_inputs_and_added_to_fixed_challenge_pool():
    first = _row("first", "First Love", provider="trakt", rating=9, rating_scale=10, watched_at="2022-01-01T00:00:00Z")
    second = _row("second", "Second Love", provider="trakt", rating=9, rating_scale=10, watched_at="2023-01-01T00:00:00Z")
    snapshot = {
        "history": [first, second, _row("second", "Second Love", source="plex")],
        "media_history": [first, second],
        "feedback": [{"canonical_media_id": "first", "title": "First Love", "year": 2020, "type": "movie", "action": "like"}],
        "candidates": [_row("distractor", "Other Film")],
    }

    cases = build_cases(snapshot, folds=2, seed=7, chronological=False)

    assert len(cases) == 2
    for case in cases:
        heldout_id = case["heldout"][0]["canonical_media_id"]
        assert all(row.get("canonical_media_id") != heldout_id for row in case["train_history"])
        assert all(row.get("canonical_media_id") != heldout_id for row in case["train_media_history"])
        assert all(row.get("canonical_media_id") != heldout_id for row in case["train_feedback"])
        assert {row["canonical_media_id"] for row in case["candidate_pool"]} == {heldout_id, "distractor"}
        assert case["source_candidate_coverage"] == 0.0


def test_chronological_case_does_not_train_on_future_unlabeled_history():
    old = _row("old", "Old Favorite", provider="trakt", rating=9, rating_scale=10, watched_at="2021-01-01T00:00:00Z")
    recent = _row("recent", "Recent Favorite", provider="trakt", rating=9, rating_scale=10, watched_at="2024-01-01T00:00:00Z")
    snapshot = {
        "history": [old, recent, _row("future", "Future Unlabeled", watched_at="2025-01-01T00:00:00Z")],
        "media_history": [old, recent],
        "feedback": [],
        "candidates": [_row("distractor", "Other Film")],
    }

    case = next(case for case in build_cases(snapshot, folds=2, seed=0, chronological=True) if case["name"] == "chronological")

    assert {row["canonical_media_id"] for row in case["heldout"]} == {"recent"}
    assert {row["canonical_media_id"] for row in case["train_history"]} == {"old"}
    assert case["cutoff"] == "2024-01-01T00:00:00+00:00"


def test_metrics_count_unique_hits_and_detect_watched_duplicate_and_invalid_outputs():
    positive = _row("positive", "Positive")
    watched = _row("watched", "Watched")
    pool = [positive, watched, _row("other", "Other")]
    ranked = [watched, positive, dict(positive), _row("invented", "Invented")]

    metrics = score_ranking(ranked, [positive], [watched], pool, ks=(2, 4))

    assert metrics["precision_at_2"] == 0.5
    assert metrics["recall_at_2"] == 1.0
    assert metrics["hit_rate_at_2"] == 1.0
    assert metrics["ndcg_at_2"] == pytest.approx(1 / math.log2(3))
    assert metrics["watched_leakage_rate"] == 0.25
    assert metrics["duplicate_rate"] == 0.25
    assert metrics["invalid_candidate_rate"] == 0.25
    assert metrics["by_media_type"]["movie"]["precision_at_2"] == 0.5


def test_evaluation_runs_actual_pipeline_on_snapshot_without_database_access():
    liked = _row("liked", "Liked Film", provider="trakt", rating=9, rating_scale=10, genres=["Drama"])
    other = _row("other", "Other Film", provider="trakt", rating=9, rating_scale=10, genres=["Drama"])
    snapshot = {
        "history": [liked, other],
        "media_history": [liked, other],
        "feedback": [],
        "candidates": [_row("filler", "Filler", genres=["Comedy"], tmdb_rating=5.0, vote_count=100)],
    }

    report = evaluate_snapshot(snapshot, folds=2, seed=0, chronological=False, ks=(1, 2))

    assert report["positive_count"] == 2
    assert len(report["cases"]) == 2
    assert all(case["metrics"]["candidate_coverage"] == 1.0 for case in report["cases"])
    assert all(case["source_candidate_coverage"] == 0.0 for case in report["cases"])
    assert all(case["ranked"] for case in report["cases"])


def test_snapshot_requires_a_fixed_candidate_array():
    with pytest.raises(ValueError, match="candidates"):
        evaluate_snapshot({"history": [], "media_history": [], "feedback": []})
