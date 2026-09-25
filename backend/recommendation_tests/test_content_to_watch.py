"""Regression checks for the user-facing Content to Watch generation path."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import server
import jobs.engine as jobs_engine


class _History:
    async def count_documents(self, _query):
        return 5

    def find(self, *_args, **_kwargs):
        return self

    async def to_list(self, _limit):
        return [{"title": "Already Watched", "year": 2020, "type": "movie"}]


class _Connections:
    async def find_one(self, *_args, **_kwargs):
        return {}


class _Recommendations:
    def __init__(self):
        self.deleted = False
        self.inserted = False

    async def delete_many(self, _query):
        self.deleted = True

    async def insert_many(self, _rows):
        self.inserted = True


def test_generation_never_falls_back_to_demo_titles(monkeypatch):
    recs = _Recommendations()
    monkeypatch.setattr(server, "db", SimpleNamespace(
        connections=_Connections(), history=_History(), recommendations=recs,
    ))
    monkeypatch.setattr(server, "ensure_history", AsyncMock())
    legacy_llm = AsyncMock(return_value=(None, "fallback", "missing"))
    monkeypatch.setattr(server, "generate_with_llm", legacy_llm)
    pipeline = AsyncMock(return_value={"status": "ok", "accepted": [], "warnings": []})
    monkeypatch.setattr(jobs_engine, "execute_job", pipeline)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(server.generate_recs(None, SimpleNamespace(user_id="test-user")))

    assert exc.value.status_code == 503
    assert not recs.deleted and not recs.inserted
    assert not legacy_llm.await_count
    assert pipeline.await_count == 1


def test_generation_uses_verified_catalog_job(monkeypatch):
    recs = _Recommendations()
    monkeypatch.setattr(server, "db", SimpleNamespace(
        connections=_Connections(), history=_History(), recommendations=recs,
    ))
    monkeypatch.setattr(server, "ensure_history", AsyncMock())
    legacy_llm = AsyncMock(return_value=(None, "fallback", "missing"))
    monkeypatch.setattr(server, "generate_with_llm", legacy_llm)
    pipeline = AsyncMock(return_value={
        "status": "ok",
        "accepted": [{"title": "Verified Pick", "tmdb_id": 123, "score_components": {"genre_affinity": 2}}],
        "warnings": [],
    })
    monkeypatch.setattr(jobs_engine, "execute_job", pipeline)

    result = asyncio.run(server.generate_recs(None, SimpleNamespace(user_id="test-user")))

    assert result["count"] == 1
    assert pipeline.await_count == 1
    job = pipeline.await_args.args[1]
    assert "tmdb_discover" in job["candidate_sources"]
    assert job["action_mode"] == "recommendations_only"
    assert job["exclusions"]["already_watched"] is True
    assert not legacy_llm.await_count


def test_job_rerank_honors_selected_model(monkeypatch):
    candidate = {"title": "Verified Pick", "candidate_id": "movie:123", "tmdb_id": 123}
    job = {
        "id": "content_to_watch:test-user",
        "candidate_sources": [],
        "final_recommendation_limit": 1,
        "action_mode": "recommendations_only",
        "ai_enabled": True,
    }
    monkeypatch.setattr(jobs_engine, "acquire_job_lock", AsyncMock(return_value="owner"))
    monkeypatch.setattr(jobs_engine, "release_job_lock", AsyncMock())
    monkeypatch.setattr(jobs_engine, "load_pipeline_inputs", AsyncMock(return_value={
        "history": [], "library": [], "recommended": [], "requested": [], "blacklist": [], "feedback": [],
    }))
    monkeypatch.setattr(jobs_engine, "required_history_warnings", AsyncMock(return_value=[]))
    monkeypatch.setattr(jobs_engine, "run_pipeline", lambda *_args, **_kwargs: {
        "taste": {}, "ranked": [candidate], "accepted": [candidate], "rejected": [],
        "candidate_count": 1, "job": job,
    })
    rerank = AsyncMock(return_value=(["movie:123"], "ollama", "gemma4:12b"))
    monkeypatch.setattr(jobs_engine, "rerank_verified_candidates", rerank)
    monkeypatch.setattr(jobs_engine, "persist_run_results", AsyncMock(return_value=[]))
    monkeypatch.setattr(jobs_engine, "_advance_schedule", AsyncMock())
    monkeypatch.setattr(jobs_engine, "db", SimpleNamespace(
        job_runs=SimpleNamespace(insert_one=AsyncMock()),
        # Every run resolves the TMDb key so history and candidates can be
        # enriched with keywords, cast and language before the profile is built.
        connections=SimpleNamespace(find_one=AsyncMock(return_value={})),
    ))

    result = asyncio.run(jobs_engine.execute_job(
        "test-user", job, "manual", catalog=[], model_override="gemma4:12b",
    ))

    assert result["status"] == "ok"
    assert rerank.await_args.kwargs["model_override"] == "gemma4:12b"
    assert result["run"]["model"] == "gemma4:12b"


def test_rerank_keeps_the_model_top_five_and_the_deterministic_tail(monkeypatch):
    """Gemma 4 decides positions 1-5; the deterministic order keeps the rest.

    Letting it order the whole pool measured -0.022 nDCG@10 against this split
    (HANDOFF.md section 18). It still has to answer with every handle.
    """
    pool = [
        {"title": f"Pick {index}", "candidate_id": f"movie:{index}", "tmdb_id": index}
        for index in range(1, 13)
    ]
    reversed_handles = ["r%02d" % index for index in range(12, 0, -1)]
    monkeypatch.setattr(jobs_engine, "db", SimpleNamespace(
        connections=SimpleNamespace(find_one=AsyncMock(return_value={})),
    ))
    monkeypatch.setattr(jobs_engine, "generate_with_llm", AsyncMock(
        return_value=({"ids": reversed_handles}, "ollama", "gemma4:12b-it-qat"),
    ))

    ordered, provider, _model = asyncio.run(jobs_engine.rerank_verified_candidates("test-user", {}, pool))

    assert provider == "ollama"
    assert ordered == ["movie:12", "movie:11", "movie:10", "movie:9", "movie:8"]
    ranked = [row["title"] for row in jobs_engine.apply_rerank(pool, ordered)]
    assert ranked[:5] == ["Pick 12", "Pick 11", "Pick 10", "Pick 9", "Pick 8"]
    assert ranked[5:] == [f"Pick {index}" for index in range(1, 8)]


def test_rerank_that_drops_most_handles_is_still_discarded(monkeypatch):
    pool = [{"title": f"Pick {index}", "candidate_id": f"movie:{index}"} for index in range(1, 13)]
    monkeypatch.setattr(jobs_engine, "db", SimpleNamespace(
        connections=SimpleNamespace(find_one=AsyncMock(return_value={})),
    ))
    # Five handles clear the top-5 cut but not the coverage floor for 12.
    monkeypatch.setattr(jobs_engine, "generate_with_llm", AsyncMock(
        return_value=({"ids": ["r01", "r02", "r03", "r04", "r05"]}, "ollama", "gemma4:12b-it-qat"),
    ))

    ordered, _provider, _model = asyncio.run(jobs_engine.rerank_verified_candidates("test-user", {}, pool))

    assert ordered is None


def test_recommendations_are_returned_best_first():
    """Rows are written in rank order; reading them back must not reverse it.

    `list_recs` sorted by created_at descending, and every row carries a later
    timestamp than the one ranked above it, so the weakest of eight picks was
    the hero card on Home and the strongest was last.
    """
    newest_but_worst = {"title": "Rank 8", "rank": 8, "created_at": "2026-09-22T12:00:08"}
    oldest_but_best = {"title": "Rank 1", "rank": 1, "created_at": "2026-09-22T12:00:01"}

    ordered = server.by_rank([newest_but_worst, oldest_but_best])

    assert [row["title"] for row in ordered] == ["Rank 1", "Rank 8"]


def test_rows_written_before_rank_existed_stay_newest_first():
    legacy_new = {"title": "Legacy new", "created_at": "2026-09-22T12:00:09"}
    legacy_old = {"title": "Legacy old", "created_at": "2026-09-22T12:00:01"}
    ranked = {"title": "Ranked", "rank": 3, "created_at": "2026-09-20T09:00:00"}

    ordered = server.by_rank([legacy_new, ranked, legacy_old])

    assert ordered[0]["title"] == "Ranked"
    assert [row["title"] for row in ordered[1:]] == ["Legacy old", "Legacy new"]
