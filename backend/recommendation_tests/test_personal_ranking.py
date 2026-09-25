"""Discovery sources may suggest titles, but personal fit controls final order."""

from recommendation.ranking_engine import apply_rerank, score_candidates
from recommendation.taste_engine import build_taste_snapshot


def test_popular_discovery_title_stays_below_a_personal_match():
    taste = build_taste_snapshot([{
        "title": "Loved Space Story", "year": 2022, "media_type": "tv",
        "provider": "trakt", "rating": 10, "rating_scale": 10,
        "genres": ["Science Fiction", "Adventure"],
        "tmdb_keywords": ["space opera", "alien civilization"],
        "original_language": "en",
    }])
    candidates = [
        {"title": "Trending Hit", "media_type": "tv", "source": "tmdb_discover",
         "genres": ["Science Fiction", "Adventure"], "popularity": 1000,
         "tmdb_rating": 9.5, "vote_count": 100000},
        {"title": "Personal Match", "media_type": "tv", "source": "tmdb_similar",
         "genres": ["Science Fiction", "Adventure"],
         "tmdb_keywords": ["space opera", "alien civilization"],
         "original_language": "en", "tmdb_rating": 7, "vote_count": 100},
    ]

    ranked = score_candidates(candidates, taste)

    assert [row["title"] for row in ranked] == ["Personal Match", "Trending Hit"]
    assert {row["title"] for row in ranked} == {row["title"] for row in candidates}


def test_ai_cannot_promote_a_weak_match_over_a_strong_personal_match():
    ranked = [
        {"candidate_id": "personal", "title": "Personal Match", "rank_score": 8.0},
        {"candidate_id": "trending", "title": "Trending Hit", "rank_score": 3.0},
    ]

    reranked = apply_rerank(ranked, ["trending", "personal"])

    assert [row["candidate_id"] for row in reranked] == ["personal", "trending"]
    assert reranked[1]["ai_rank"] == 1


def test_ai_can_break_a_close_personal_tie():
    ranked = [
        {"candidate_id": "first", "title": "First", "rank_score": 8.0},
        {"candidate_id": "second", "title": "Second", "rank_score": 7.9},
    ]

    reranked = apply_rerank(ranked, ["second", "first"])

    assert [row["candidate_id"] for row in reranked] == ["second", "first"]
