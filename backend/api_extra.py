"""Jobs, feedback, search and local request-create routes. AniList/approve stay in server.py."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import get_current_user
from database import db
from jobs.engine import (
    _parse_stamp,
    create_job,
    delete_job,
    due_jobs,
    execute_job,
    get_job,
    list_jobs,
    list_runs,
    safe_provider_error,
    update_job,
)
from models import User
from recommendation.pipeline import default_job, run_pipeline
from recommendation.search import merge_search_intent, parse_search_query

router = APIRouter()

SEED_CATALOG = [
    {"title": "Foundation", "year": 2021, "type": "show", "genres": ["Sci-Fi", "Drama"], "tmdb_rating": 7.5, "synopsis": "Galactic Empire saga."},
    {"title": "Poor Things", "year": 2023, "type": "movie", "genres": ["Sci-Fi", "Dark Comedy", "Drama"], "tmdb_rating": 8.0, "synopsis": "A reanimated odyssey."},
    {"title": "Dark", "year": 2017, "type": "show", "genres": ["Sci-Fi", "Mystery", "Thriller"], "tmdb_rating": 8.7, "synopsis": "German time-travel mystery."},
    {"title": "The Zone of Interest", "year": 2023, "type": "movie", "genres": ["Drama", "History"], "tmdb_rating": 7.4, "synopsis": "A house beside Auschwitz."},
    {"title": "Shogun", "year": 2024, "type": "show", "genres": ["Drama", "History", "Action"], "tmdb_rating": 8.6, "synopsis": "Japan in 1600."},
    {"title": "Past Lives", "year": 2023, "type": "movie", "genres": ["Drama", "Romance"], "tmdb_rating": 7.8, "synopsis": "Two friends meet again."},
    {"title": "The Diplomat", "year": 2023, "type": "show", "genres": ["Drama", "Thriller"], "tmdb_rating": 7.7, "synopsis": "A diplomat out of her depth."},
    {"title": "Oppenheimer", "year": 2023, "type": "movie", "genres": ["Drama", "History", "Thriller"], "tmdb_rating": 8.1, "synopsis": "The Manhattan Project."},
    {"title": "Frieren", "year": 2023, "type": "anime", "genres": ["Fantasy", "Drama"], "tmdb_rating": 8.8, "synopsis": "An elf after the hero's journey."},
    {"title": "Library Sentinel", "year": 2022, "type": "movie", "genres": ["Drama"], "tmdb_rating": 7.0, "synopsis": "Demo owned title for library exclusion."},
    {"title": "Shelf Drama", "year": 2021, "type": "show", "genres": ["Drama", "Thriller"], "tmdb_rating": 7.1, "synopsis": "Demo owned series for library exclusion."},
    {"title": "Owned Sci-Fi", "year": 2020, "type": "movie", "genres": ["Sci-Fi"], "tmdb_rating": 7.2, "synopsis": "Demo owned movie for library exclusion."},
]


class JobBody(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = True
    job_type: Optional[str] = "personalized"
    media_types: Optional[List[str]] = None
    taste_sources: Optional[List[str]] = None
    candidate_sources: Optional[List[str]] = None
    required_sources: Optional[List[str]] = None
    provider_weights: Optional[Dict[str, float]] = None
    filters: Optional[Dict[str, Any]] = None
    exclusions: Optional[Dict[str, Any]] = None
    ai_enabled: Optional[bool] = False
    candidate_limit: Optional[int] = 40
    final_recommendation_limit: Optional[int] = 8
    action_mode: Optional[str] = "require_approval"
    schedule: Optional[str] = "every_30m"
    schedule_offset_minutes: Optional[int] = None
    timezone: Optional[str] = "UTC"


class FeedbackBody(BaseModel):
    action: str = Field(pattern="^(like|dislike|hide|blacklist|watched)$")


class SearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=400)
    model: Optional[str] = None


class RequestBody(BaseModel):
    title: str
    year: Optional[int] = None
    type: Optional[str] = "movie"
    tmdb_id: Optional[int] = None
    poster: Optional[str] = None


class AnilistCode(BaseModel):
    code: str


class AnilistListPayload(BaseModel):
    data: Optional[Dict[str, Any]] = None


def _public(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not doc:
        return None
    return {key: value for key, value in doc.items() if key != "_id"}


@router.post("/jobs", status_code=201)
async def jobs_create(body: JobBody, user: User = Depends(get_current_user)):
    try:
        return await create_job(user.user_id, body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/jobs")
async def jobs_list(user: User = Depends(get_current_user)):
    return await list_jobs(user.user_id)


@router.get("/jobs/{job_id}")
async def jobs_get(job_id: str, user: User = Depends(get_current_user)):
    job = await get_job(user.user_id, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.put("/jobs/{job_id}")
async def jobs_update(job_id: str, body: JobBody, user: User = Depends(get_current_user)):
    try:
        job = await update_job(user.user_id, job_id, body.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.delete("/jobs/{job_id}")
async def jobs_delete(job_id: str, user: User = Depends(get_current_user)):
    if not await delete_job(user.user_id, job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"ok": True}


@router.post("/jobs/{job_id}/clone", status_code=201)
async def jobs_clone(job_id: str, user: User = Depends(get_current_user)):
    job = await get_job(user.user_id, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    payload = {
        key: value
        for key, value in job.items()
        if key not in {"id", "created_at", "updated_at", "last_run_at", "next_run_at", "schedule_offset_minutes"}
    }
    payload["name"] = f"{job.get('name')} copy"
    return await create_job(user.user_id, payload)


@router.post("/jobs/{job_id}/preview")
async def jobs_preview(job_id: str, user: User = Depends(get_current_user)):
    job = await get_job(user.user_id, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return await execute_job(user.user_id, job, "preview", SEED_CATALOG)


@router.post("/jobs/{job_id}/run")
async def jobs_run(job_id: str, user: User = Depends(get_current_user)):
    job = await get_job(user.user_id, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return await execute_job(user.user_id, job, "manual", SEED_CATALOG)


@router.get("/jobs/{job_id}/runs")
async def jobs_runs(job_id: str, user: User = Depends(get_current_user)):
    if not await get_job(user.user_id, job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return await list_runs(user.user_id, job_id)


@router.get("/overview")
async def operations_overview(user: User = Depends(get_current_user)):
    jobs = await db.jobs.find(
        {"user_id": user.user_id},
        {"_id": 0, "id": 1, "name": 1, "enabled": 1, "schedule": 1, "next_run_at": 1, "last_run_at": 1},
    ).to_list(50)
    next_jobs = sorted((job for job in jobs if job.get("next_run_at")), key=lambda job: job["next_run_at"])
    return {
        "recommendations": await db.recommendations.count_documents({"user_id": user.user_id, "dismissed": {"$ne": True}}),
        "active_jobs": sum(1 for job in jobs if job.get("enabled")),
        "next_job": next_jobs[0] if next_jobs else None,
        "recent_runs": await db.job_runs.find({"user_id": user.user_id}, {"_id": 0, "user_id": 0}).sort("started_at", -1).to_list(5),
        "pending_approvals": await db.requests.count_documents({"user_id": user.user_id, "status": {"$in": ["pending", "pending_approval", "requested"]}}),
        "recent_feedback": await db.recommendation_feedback.find({"user_id": user.user_id}, {"_id": 0, "user_id": 0}).sort("updated_at", -1).to_list(5),
        "recent_requests": await db.requests.find({"user_id": user.user_id}, {"_id": 0, "user_id": 0}).sort("updated_at", -1).to_list(5),
        "provider_warnings": await _provider_warnings(user.user_id),
    }


async def _provider_warnings(user_id: str) -> List[Dict[str, Any]]:
    conn = await db.connections.find_one({"user_id": user_id}, {"_id": 0}) or {}
    rows = []
    for provider, connected in (
        ("trakt", bool(conn.get("trakt_access_token"))),
        ("simkl", bool(conn.get("simkl_access_token"))),
        ("plex", bool(conn.get("plex_token") and conn.get("plex_url"))),
        ("anilist", bool(conn.get("anilist_access_token"))),
    ):
        state = await db.provider_sync_state.find_one({"provider": provider, "account_id": user_id}) or {}
        if connected and state.get("last_error"):
            rows.append({
                "provider": provider,
                "code": "sync_error",
                "detail": safe_provider_error(state.get("last_error")),
            })
    return rows


# Codes that mean "a provider actually broke" rather than "not set up yet".
BUG_CODE_SUFFIXES = ("_failed", "_http_error", "_unusable")


def _level_for_code(code: str) -> str:
    return "bug" if str(code or "").endswith(BUG_CODE_SUFFIXES) else "warning"


# These two say "go and sync", so a later successful sync answers them.
HISTORY_WARNING_CODES = {"history_never_synced", "history_stale"}


def _warning_already_resolved(
    warning: Dict[str, Any],
    run_stamp: Any,
    synced_at: Dict[str, Any],
) -> bool:
    """True when the provider synced after the run that raised this warning.

    The feed keeps every warning and trims only the healthy rows, so a
    "never synced" from one run used to stay pinned at the top long after the
    sync it asked for had actually run.
    """
    if (warning.get("code") or "") not in HISTORY_WARNING_CODES:
        return False
    raised = _parse_stamp(run_stamp)
    synced = _parse_stamp(synced_at.get(warning.get("source")))
    return bool(raised and synced and synced > raised)


@router.get("/runtime/logs")
async def runtime_logs(
    user: User = Depends(get_current_user),
    limit: int = 400,
) -> Dict[str, Any]:
    """One flat, newest-first feed of everything a run produced.

    The Jobs page only ever showed warnings buried inside each run card, so a
    "Finished with warnings" toast gave no way to see what the warning was.
    """
    limit = max(1, min(int(limit or 400), 1000))
    jobs = {job["id"]: job.get("name") or job["id"] for job in await list_jobs(user.user_id)}
    rows: List[Dict[str, Any]] = []

    sync_states = await db.provider_sync_state.find(
        {"account_id": user.user_id}, {"_id": 0}
    ).to_list(50)
    synced_at = {
        state.get("provider"): state.get("last_success_at")
        for state in sync_states
        if state.get("last_success_at")
    }

    runs = await db.job_runs.find({"user_id": user.user_id}, {"_id": 0}).sort("started_at", -1).to_list(limit)
    for run in runs:
        where = jobs.get(run.get("job_id"), run.get("job_id") or "job")
        stamp = run.get("finished_at") or run.get("started_at")
        base = {
            "time": stamp,
            "where": where,
            "job_id": run.get("job_id"),
            "run_id": run.get("id"),
            "trigger": run.get("trigger_type"),
        }
        if run.get("status") == "failed":
            rows.append({
                **base,
                "level": "failed",
                "source": "job",
                "code": "run_failed",
                "detail": run.get("error") or "Run failed",
            })
        for warning in run.get("warnings") or []:
            code = warning.get("code") or "warning"
            if _warning_already_resolved(warning, stamp, synced_at):
                continue
            rows.append({
                **base,
                "level": _level_for_code(code),
                "source": warning.get("source") or "job",
                "code": code,
                "detail": warning.get("detail") or WARNING_HINTS.get(code, ""),
            })
        if run.get("status") == "completed":
            rows.append({
                **base,
                "level": "ok",
                "source": "job",
                "code": "run_completed",
                "detail": f"{run.get('accepted_count') or 0} picks from {run.get('candidate_count') or 0} candidates",
            })

    for state in sync_states:
        provider = state.get("provider") or "provider"
        if state.get("last_error"):
            rows.append({
                "time": state.get("last_sync_at"),
                "level": "error",
                "source": provider,
                "where": "history sync",
                "code": "sync_error",
                "detail": safe_provider_error(state.get("last_error")),
            })
        elif state.get("last_success_at"):
            rows.append({
                "time": state.get("last_success_at"),
                "level": "ok",
                "source": provider,
                "where": "history sync",
                "code": "sync_ok",
                "detail": f"{state.get('items_synced') or 0} titles synced",
            })

    requests = await db.requests.find(
        {"user_id": user.user_id},
        {"_id": 0},
    ).sort("updated_at", -1).to_list(2000)
    for row in requests:
        status = row.get("status")
        stamp = row.get("approved_at") or row.get("updated_at")
        if row.get("delivery_status") == "not_delivered":
            rows.append({
                "time": stamp,
                "level": "error",
                "source": row.get("provider") or "mediamanager",
                "where": row.get("title") or row.get("id"),
                "code": "delivery_failed",
                "detail": row.get("delivery_error") or "Approved but never reached MediaManager",
            })
        elif status == "request_failed":
            rows.append({
                "time": stamp,
                "level": "error",
                "source": row.get("provider") or "mediamanager",
                "where": row.get("title") or row.get("id"),
                "code": "request_failed",
                "detail": "The request could not be created",
            })
        elif status in ("approved", "available", "completed"):
            rows.append({
                "time": stamp,
                "level": "approved",
                "source": row.get("provider") or "local",
                "where": row.get("title") or row.get("id"),
                "code": status,
                "detail": mediamanager_label(row),
            })

    rows.sort(key=lambda row: str(row.get("time") or ""), reverse=True)
    counts: Dict[str, int] = {level: 0 for level in ("failed", "error", "bug", "warning", "approved", "ok")}
    for row in rows:
        counts[row["level"]] = counts.get(row["level"], 0) + 1

    # Healthy runs outnumber everything else by two orders of magnitude, so a plain
    # cut would push every warning and approval off the page. Keep the rows worth
    # reading and spend what is left of the budget on the "okey" ones.
    notable = [row for row in rows if row["level"] != "ok"]
    healthy = [row for row in rows if row["level"] == "ok"]
    trimmed = notable[:limit] + healthy[: max(0, limit - len(notable[:limit]))]
    trimmed.sort(key=lambda row: str(row.get("time") or ""), reverse=True)
    return {"counts": counts, "rows": trimmed, "truncated": len(trimmed) < len(rows)}


def mediamanager_label(row: Dict[str, Any]) -> str:
    kind = str(row.get("type") or "").lower()
    library = "Movies" if kind == "movie" else "TV"
    return f"Sent to {library}"


# Plain-language hints for warning codes that carry no detail of their own.
WARNING_HINTS = {
    "history_never_synced": "Connected, but no watch history has ever been synced. Run a history sync.",
    "history_stale": "Watch history is more than 24 hours old. Run a history sync.",
    "tmdb_not_configured": "No TMDb API key is stored, so TMDb candidate sources were skipped.",
    "trakt_not_connected": "Trakt is selected as a source but no Trakt account is linked.",
    "simkl_not_connected": "Simkl is selected as a source but no Simkl account is linked.",
    "anilist_not_connected": "AniList is selected as a source but no AniList account is linked.",
    "simkl_empty_feed": "Simkl returned no recommendations for this account.",
}


@router.get("/runtime/status")
async def runtime_status(user: User = Depends(get_current_user)):
    from config import (
        ANILIST_CLIENT_ID,
        DEMO_MODE,
        REQUEST_PROVIDER_API_KEY,
        REQUEST_PROVIDER_URL,
        SIMKL_CLIENT_ID,
        TRAKT_CLIENT_ID,
        google_oauth_configured,
    )
    from migrate import SCHEMA_VERSION
    from providers.keys import artwork_configured

    conn = await db.connections.find_one({"user_id": user.user_id}, {"_id": 0}) or {}
    artwork = artwork_configured(conn)
    return {
        "schema_version": SCHEMA_VERSION,
        "demo_mode": DEMO_MODE,
        "tmdb_configured": artwork["tmdb_configured"],
        "tvdb_configured": artwork["tvdb_configured"],
        "trakt_client_configured": bool(TRAKT_CLIENT_ID),
        "simkl_client_configured": bool(SIMKL_CLIENT_ID),
        "anilist_client_configured": bool(ANILIST_CLIENT_ID),
        "google_client_configured": google_oauth_configured(),
        "request_provider_configured": bool(REQUEST_PROVIDER_URL and REQUEST_PROVIDER_API_KEY),
    }


@router.post("/jobs/tick")
async def jobs_tick(user: User = Depends(get_current_user)):
    ran = []
    for job in await due_jobs():
        if job.get("user_id") != user.user_id:
            continue
        ran.append(await execute_job(user.user_id, job, "scheduled", SEED_CATALOG))
    return {"ran": len(ran), "results": ran}


@router.post("/recommendations/{rec_id}/feedback")
async def rec_feedback(rec_id: str, body: FeedbackBody, user: User = Depends(get_current_user)):
    rec = await db.recommendations.find_one({"user_id": user.user_id, "id": rec_id}, {"_id": 0})
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    now = datetime.now(timezone.utc).isoformat()
    canonical = rec.get("canonical_media_id") or f"slug:{rec.get('title')}:{rec.get('year')}"
    await db.recommendation_feedback.update_one(
        {"user_id": user.user_id, "recommendation_id": rec_id},
        {"$set": {
            "user_id": user.user_id,
            "recommendation_id": rec_id,
            "canonical_media_id": canonical,
            "title": rec.get("title"),
            "year": rec.get("year"),
            "type": rec.get("type"),
            "genres": rec.get("genres") or [],
            "action": body.action,
            "updated_at": now,
        }},
        upsert=True,
    )
    if body.action == "hide":
        await db.recommendations.update_one(
            {"user_id": user.user_id, "id": rec_id},
            {"$set": {"dismissed": True}},
        )
    if body.action == "blacklist":
        await db.blacklist.update_one(
            {"user_id": user.user_id, "canonical_media_id": canonical},
            {"$set": {
                "user_id": user.user_id,
                "canonical_media_id": canonical,
                "title": rec.get("title"),
                "year": rec.get("year"),
                "updated_at": now,
            }},
            upsert=True,
        )
    if body.action == "watched":
        from recommendation.media_identity import attach_canonical_ids

        identified = await attach_canonical_ids(user.user_id, [{
            "title": rec.get("title"),
            "year": rec.get("year"),
            "type": rec.get("type"),
            "genres": rec.get("genres") or [],
            "tmdb_id": rec.get("tmdb_id"),
            "anilist_id": rec.get("anilist_id"),
            "trakt_id": rec.get("trakt_id"),
            "simkl_id": rec.get("simkl_id"),
            "provider": "feedback",
            "source": "feedback",
            "watched_at": now,
            "completed": True,
        }])
        item = identified[0]
        history_row = {
            **item,
            "user_id": user.user_id,
            "id": rec.get("id") or str(uuid.uuid4()),
            "source": "feedback",
            "watched_at": now,
        }
        await db.history.update_one(
            {"user_id": user.user_id, "canonical_media_id": item["canonical_media_id"]},
            {"$set": history_row},
            upsert=True,
        )
    return {"ok": True, "action": body.action, "canonical_media_id": canonical}


@router.get("/blacklist")
async def list_blacklist(user: User = Depends(get_current_user)):
    return await db.blacklist.find({"user_id": user.user_id}, {"_id": 0, "user_id": 0}).to_list(200)


@router.delete("/blacklist/{canonical_id}")
async def remove_blacklist(canonical_id: str, user: User = Depends(get_current_user)):
    result = await db.blacklist.delete_one({"user_id": user.user_id, "canonical_media_id": canonical_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Blacklist item not found")
    return {"ok": True}


@router.post("/search/ai")
async def ai_search(body: SearchBody, user: User = Depends(get_current_user)):
    import sys

    from jobs.engine import load_pipeline_inputs, rerank_verified_candidates
    from recommendation.llm_context import compact_taste_prompt
    from recommendation.media_identity import attach_canonical_ids
    from recommendation.ranking_engine import apply_rerank
    from recommendation.taste_engine import build_taste_snapshot
    from llm import generate_with_llm

    intent = parse_search_query(body.query)
    inputs = await load_pipeline_inputs(user.user_id)
    taste = build_taste_snapshot(
        inputs["history"],
        feedback=inputs["feedback"],
    )
    conn = await db.connections.find_one({"user_id": user.user_id}, {"_id": 0}) or {}
    provider, model = "deterministic", "deterministic"
    allow_llm = "pytest" not in sys.modules or bool(conn.get("ollama_url"))
    if allow_llm:
        parsed, provider, model = await generate_with_llm(
            conn,
            f"search-{user.user_id}",
            "Parse a movie/TV search into structured search intent. Return JSON only with keys media_types (movie/tv/anime), include_genres, min_year, max_year, search_text. Never invent titles.",
            f"{compact_taste_prompt(taste)}\nUser query: {body.query}",
            user.user_id,
            "ai_search",
            body.model,
        )
        if isinstance(parsed, dict):
            intent = merge_search_intent(intent, parsed)
            intent["refined"] = provider == "ollama"
    extra: List[Dict[str, Any]] = []
    if "pytest" not in sys.modules:
        try:
            from providers.keys import resolve_tmdb_api_key
            from providers.tmdb import tmdb_search_candidates

            extra = await tmdb_search_candidates(intent, api_key=resolve_tmdb_api_key(conn))
        except Exception:
            extra = []
    job = default_job()
    job["media_types"] = intent["media_types"]
    job["filters"] = {
        "include_genres": intent["include_genres"],
        "min_year": intent["min_year"],
        "max_year": intent.get("max_year"),
    }
    job["name"] = "ai-search"
    job["exclusions"]["already_watched"] = True
    result = run_pipeline(
        job,
        catalog=SEED_CATALOG,
        extra_candidates=extra,
        **inputs,
    )
    ranked = result.get("ranked") or result["accepted"]
    ai_reranked = False
    if ranked and allow_llm:
        ordered, rerank_provider, rerank_model = await rerank_verified_candidates(
            user.user_id, result["taste"], ranked, model_override=body.model
        )
        if ordered:
            ranked = apply_rerank(ranked, ordered)
            ai_reranked = rerank_provider == "ollama"
            if ai_reranked:
                provider, model = rerank_provider, rerank_model
    accepted = ranked[:8]
    accepted = await attach_canonical_ids(user.user_id, accepted, persist_history=False)
    now = datetime.now(timezone.utc).isoformat()
    search_provider = "ollama" if ai_reranked or intent.get("refined") else "deterministic"
    stored = []
    for item in accepted:
        row = {
            **item,
            "id": str(uuid.uuid4()),
            "user_id": user.user_id,
            "saved": False,
            "dismissed": False,
            "provider": search_provider if search_provider == "ollama" else "pipeline",
            "model": model if search_provider == "ollama" else "deterministic",
            "source": item.get("source") or "ai_search",
            "ai_reranked": ai_reranked,
            "created_at": now,
        }
        stored.append(row)
    if stored:
        await db.recommendations.insert_many([dict(row) for row in stored])
    public = [{key: value for key, value in row.items() if key not in {"_id", "user_id"}} for row in stored]
    return {
        "intent": intent,
        "count": len(public),
        "results": public,
        "provider": search_provider,
        "model": model if search_provider == "ollama" else "deterministic",
    }


@router.post("/requests", status_code=201)
async def create_request(body: RequestBody, user: User = Depends(get_current_user)):
    doc = {
        "id": f"req_{uuid.uuid4().hex[:12]}",
        "user_id": user.user_id,
        "title": body.title,
        "year": body.year,
        "type": body.type,
        "tmdb_id": body.tmdb_id,
        "poster": body.poster,
        "status": "pending_approval",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if not doc.get("poster"):
        from providers.keys import resolve_tmdb_api_key, resolve_tvdb_api_key
        from providers.tmdb import enrich_history_posters

        conn = await db.connections.find_one({"user_id": user.user_id}, {"_id": 0}) or {}
        await enrich_history_posters(
            [doc],
            resolve_tmdb_api_key(conn),
            tvdb_api_key=resolve_tvdb_api_key(conn),
        )
    await db.requests.insert_one(dict(doc))
    return {key: value for key, value in doc.items() if key not in {"_id", "user_id"}}
