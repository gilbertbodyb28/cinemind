import math

from evaluation.model_bench import (
    completed_order, conservative_order, holm_adjusted_pvalues, paired_comparison,
    production_rerank_request, ranking_stability,
)
from evaluation.offline import _kind


def test_anime_media_types_are_reported_separately():
    assert _kind({"type": "show", "genres": ["Anime", "Action"]}) == "anime"
    assert _kind({"type": "movie", "genres": ["Donghua", "Fantasy"]}) == "anime_movie"
    assert _kind({"type": "movie", "genres": ["Animation", "Family"]}) == "movie"


def test_conservative_order_only_promotes_nearby_top_choices():
    pool = [{"bench_id": f"c{i}", "bench_rank": i - 1} for i in range(1, 13)]
    # c4 is a plausible promotion; c12 is too far from deterministic evidence.
    result = conservative_order(["c4", "c12", "c2", "c1"], pool)
    assert result[:4] == ["c4", "c2", "c1", "c3"]
    assert result[-1] == "c12"


def test_paired_comparison_uses_fold_means_not_repeats_as_independent_samples():
    baseline = [
        {"case": "f1", "ndcg_at_5": 0.2}, {"case": "f1", "ndcg_at_5": 0.4},
        {"case": "f2", "ndcg_at_5": 0.5}, {"case": "f2", "ndcg_at_5": 0.7},
    ]
    model = [
        {"case": "f1", "ndcg_at_5": 0.5}, {"case": "f1", "ndcg_at_5": 0.7},
        {"case": "f2", "ndcg_at_5": 0.6}, {"case": "f2", "ndcg_at_5": 0.8},
    ]
    result = paired_comparison(model, baseline, "ndcg_at_5")
    assert result["n_folds"] == 2
    assert math.isclose(result["mean_difference"], 0.2)
    assert result["standard_error"] > 0


def test_stability_tracks_rank_order_and_conservative_output_separately():
    full = ranking_stability([["c1", "c2", "c3"], ["c2", "c1", "c3"]])
    conservative = ranking_stability([["c1", "c2", "c3"], ["c1", "c2", "c3"]])
    assert full["stability_at_10"] == 1.0
    assert math.isclose(full["order_stability_at_10"], 1 / 3)
    assert conservative["order_stability_at_10"] == 1.0


def test_holm_adjustment_controls_multiple_comparisons():
    adjusted = holm_adjusted_pvalues({"a": 0.01, "b": 0.03, "c": 0.04})
    assert adjusted == {"a": 0.03, "b": 0.06, "c": 0.06}


def test_production_prompt_uses_same_cap_and_schema_as_live_reranker():
    pool = [
        {"bench_id": f"c{i:03d}", "bench_rank": i - 1, "title": f"Title {i}",
         "year": 2027, "type": "movie", "genres": ["Drama"]}
        for i in range(1, 15)
    ]
    prompt, handles, schema, min_coverage = production_rerank_request(
        pool, "Taste profile", redact_recent_titles=True,
    )
    assert len(handles) == 12
    assert list(handles.values()) == [f"c{i:03d}" for i in range(1, 13)]
    assert schema["properties"]["ids"]["minItems"] == 12
    assert schema["properties"]["ids"]["maxItems"] == 12
    assert min_coverage == 0.5
    assert "Title 1" not in prompt
    assert "[title withheld]" in prompt


def test_partial_or_failed_answer_stability_uses_actual_fallback_order():
    pool = [{"bench_id": f"c{i}", "bench_rank": i - 1} for i in range(1, 4)]
    assert completed_order([], pool) == ["c1", "c2", "c3"]
    assert completed_order(["c2", "c2", "invented"], pool) == ["c2", "c1", "c3"]
    assert ranking_stability([completed_order([], pool), completed_order([], pool)])["order_stability_at_10"] == 1.0
