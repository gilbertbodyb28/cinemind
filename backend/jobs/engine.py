"""Persist jobs, lock runs, and execute the shared recommendation pipeline."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
import asyncio
import logging
import re
import sys
import uuid

from database import db
from llm import generate_with_llm
from recommendation.llm_context import compact_taste_prompt
from recommendation.pipeline import default_job, run_pipeline
from recommendation.ranking_engine import apply_rerank

HISTORY_PROVIDERS = {"plex", "trakt", "simkl", "anilist"}
HISTORY_STALE_AFTER = timedelta(hours=24)

# Interval jobs share one 30-minute grid. Each job owns a 6-minute slot
# (0, 6, 12, 18, 24) so two jobs never start on top of each other and every
# job still runs twice an hour.
JOB_INTERVAL_MINUTES = 30
JOB_STAGGER_MINUTES = 6
JOB_STAGGER_SLOTS = JOB_INTERVAL_MINUTES // JOB_STAGGER_MINUTES

JOB_TYPES = {
    "personalized",
    "discover",
    "trakt",
    "simkl",
    "anilist",
}

JOB_TYPE_DEFAULTS = {
    "personalized": {"candidate_sources": ["seed_expand"], "media_types": ["movie", "tv"]},
    "discover": {"candidate_sources": ["tmdb_discover", "seed_expand"], "media_types": ["movie", "tv"]},
    "trakt": {"candidate_sources": ["trakt", "seed_expand"]},
    "simkl": {"candidate_sources": ["simkl", "seed_expand"]},
    "anilist": {
        "candidate_sources": ["anilist", "seed_expand"],
        "media_types": ["anime"],
        "taste_sources": ["anilist"],
        "provider_weights": {"anilist": 2.0, "plex": 0.6, "trakt": 0.6, "simkl": 0.6},
    },
}


def apply_job_type_defaults(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Union the job type's primary sources so Discover/Trakt/Simkl/AniList jobs are not no-ops."""
    defaults = JOB_TYPE_DEFAULTS.get(spec.get("job_type") or "personalized") or {}
    sources = list(spec.get("candidate_sources") or [])
    for source in defaults.get("candidate_sources") or []:
        if source not in sources:
            sources.append(source)
    spec["candidate_sources"] = sources
    provided_media = spec.get("media_types")
    if provided_media:
        media = list(provided_media)
        for kind in defaults.get("media_types") or []:
            if kind not in media:
                media.append(kind)
        spec["media_types"] = media
    elif provided_media is None and defaults.get("media_types"):
        spec["media_types"] = list(defaults["media_types"])
    taste = list(spec.get("taste_sources") or [])
    for source in defaults.get("taste_sources") or []:
        if source not in taste:
            taste.append(source)
    if taste:
        spec["taste_sources"] = taste
    if defaults.get("provider_weights") and not spec.get("provider_weights"):
        spec["provider_weights"] = dict(defaults["provider_weights"])
    return spec


def _required_sources(spec: Dict[str, Any]) -> set:
    return {item for item in (spec.get("required_sources") or []) if item}


def _provider_connected(conn: Dict[str, Any], name: str) -> bool:
    if name == "trakt":
        return bool(conn.get("trakt_access_token"))
    if name == "simkl":
        return bool(conn.get("simkl_access_token"))
    if name == "plex":
        return bool(conn.get("plex_token") and conn.get("plex_url"))
    if name == "anilist":
        return bool(conn.get("anilist_access_token"))
    return False


def _provider_account_id(conn: Dict[str, Any], name: str, user_id: str) -> str:
    if name == "trakt":
        return conn.get("trakt_username") or user_id
    if name == "simkl":
        return conn.get("simkl_username") or user_id
    if name == "anilist":
        return conn.get("anilist_username") or user_id
    return user_id


def _parse_stamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


async def required_history_warnings(user_id: str, job: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Warn when a required history provider is connected but has no watch history.

    Default taste_sources are ignored — personalized jobs would otherwise warn on every run.
    Sync-state rows are optional; real history documents count as a successful sync.
    """
    required = _required_sources(job) & HISTORY_PROVIDERS
    if not required:
        return []
    conn = await db.connections.find_one({"user_id": user_id}, {"_id": 0}) or {}
    warnings: List[Dict[str, Any]] = []
    now = _now()
    for name in sorted(required):
        if not _provider_connected(conn, name):
            continue
        watched = await db.history.find_one({"user_id": user_id, "source": name}, {"_id": 1})
        if watched:
            continue
        accounts = {user_id, _provider_account_id(conn, name, user_id)}
        state = await db.provider_sync_state.find_one(
            {"provider": name, "account_id": {"$in": list(accounts)}}
        ) or {}
        last = _parse_stamp(state.get("last_success_at"))
        if last is None:
            warnings.append({"code": "history_never_synced", "source": name})
        elif now - last > HISTORY_STALE_AFTER:
            warnings.append({"code": "history_stale", "source": name})
    return warnings


def safe_provider_error(exc: Any) -> str:
    text = str(exc)
    text = re.sub(r"(api_key|access_token|client_secret|password)=[^&\s]+", r"\1=redacted", text, flags=re.I)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._\-]+", "Bearer redacted", text, flags=re.I)
    return text[:240]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def public_job(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in doc.items() if key != "_id"}


def next_run_at(
    schedule: Optional[str],
    now: Optional[datetime] = None,
    timezone_name: Optional[str] = None,
    offset_minutes: int = 0,
) -> Optional[str]:
    """Return next run instant in UTC ISO form.

    every_30m lands on a fixed :00/:30 grid shifted by the job's
    schedule_offset_minutes, so several jobs stay staggered instead of all
    firing together and drifting. Daily/weekly fire at 06:00 in the job's
    IANA timezone so the stored `timezone` field actually changes when the job runs.
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    stamp = now or _now()
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    if not schedule or schedule == "manual":
        return None
    if schedule == "every_30m":
        offset = int(offset_minutes or 0) % 30
        grid = stamp.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=offset)
        while grid <= stamp:
            grid = grid + timedelta(minutes=30)
        return grid.isoformat()
    try:
        zone = ZoneInfo(timezone_name or "UTC")
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
    local = stamp.astimezone(zone)
    target = local.replace(hour=6, minute=0, second=0, microsecond=0)
    if schedule == "daily":
        if target <= local:
            target = target + timedelta(days=1)
    elif schedule == "weekly":
        days = 7 - local.weekday()  # next Monday 06:00 local
        if days == 7 and target > local:
            days = 0
        target = target + timedelta(days=days if days else (0 if target > local else 7))
        if days == 0 and target <= local:
            target = target + timedelta(days=7)
    else:
        return None
    return target.astimezone(timezone.utc).isoformat()


def stagger_offset(index: int) -> int:
    """Slot `index` of the 30-minute window, 6 minutes apart: 0, 6, 12, 18, 24."""
    return (index % JOB_STAGGER_SLOTS) * JOB_STAGGER_MINUTES


async def next_stagger_offset(user_id: str) -> int:
    """Lowest free 6-minute slot for this user, so a new job never collides.

    All five slots taken means the least crowded one is reused; six or more
    interval jobs cannot all be 6 minutes apart inside half an hour.
    """
    docs = await db.jobs.find(
        {"user_id": user_id, "schedule": "every_30m"},
        {"_id": 0, "schedule_offset_minutes": 1},
    ).to_list(500)
    taken: Dict[int, int] = {}
    for doc in docs:
        offset = int(doc.get("schedule_offset_minutes") or 0) % JOB_INTERVAL_MINUTES
        taken[offset] = taken.get(offset, 0) + 1
    slots = [stagger_offset(index) for index in range(JOB_STAGGER_SLOTS)]
    for offset in slots:
        if offset not in taken:
            return offset
    return min(slots, key=lambda offset: (taken.get(offset, 0), offset))


def validate_job(spec: Dict[str, Any]) -> None:
    job_type = spec.get("job_type") or "personalized"
    if job_type not in JOB_TYPES:
        raise ValueError(f"Unknown job type: {job_type}")
    media = [item for item in (spec.get("media_types") or []) if item]
    if not media:
        raise ValueError("Select at least one media type")
    sources = [item for item in (spec.get("candidate_sources") or []) if item]
    if not sources:
        raise ValueError("Select at least one candidate source")
    filters = spec.get("filters") or {}
    min_year, max_year = filters.get("min_year"), filters.get("max_year")
    if min_year not in (None, "") and max_year not in (None, "") and int(min_year) > int(max_year):
        raise ValueError("minimum_year must be <= maximum_year")
    min_runtime, max_runtime = filters.get("min_runtime"), filters.get("max_runtime")
    if min_runtime not in (None, "") and max_runtime not in (None, "") and int(min_runtime) > int(max_runtime):
        raise ValueError("minimum_runtime must be <= maximum_runtime")
    candidate_limit = int(spec.get("candidate_limit") or 0)
    final_limit = int(spec.get("final_recommendation_limit") or 0)
    if candidate_limit < final_limit:
        raise ValueError("candidate_limit must be >= final_recommendation_limit")
    schedule = spec.get("schedule") or "every_30m"
    if schedule not in {"manual", "daily", "weekly", "every_30m"}:
        raise ValueError("Invalid schedule")
    offset = spec.get("schedule_offset_minutes")
    if offset not in (None, ""):
        if int(offset) < 0 or int(offset) > 29:
            raise ValueError("schedule_offset_minutes must be between 0 and 29")
    if spec.get("action_mode") not in {None, "recommendations_only", "require_approval", "auto_request"}:
        raise ValueError("Invalid action mode")
    unknown_required = _required_sources(spec) - set(spec.get("candidate_sources") or [])
    if unknown_required:
        raise ValueError(f"Required sources must also be selected: {', '.join(sorted(unknown_required))}")


async def create_job(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    spec = apply_job_type_defaults({**default_job(), **payload})
    validate_job(spec)
    schedule = spec.get("schedule") or "every_30m"
    offset = spec.get("schedule_offset_minutes")
    if offset in (None, "") and schedule == "every_30m":
        offset = await next_stagger_offset(user_id)
    offset = int(offset or 0)
    now = _now().isoformat()
    job = {
        **spec,
        "id": spec.get("id") or f"job_{uuid.uuid4().hex[:12]}",
        "user_id": user_id,
        "name": (spec.get("name") or "Untitled job").strip(),
        "description": spec.get("description") or "",
        "enabled": bool(spec.get("enabled", True)),
        "schedule": spec.get("schedule") or "every_30m",
        "timezone": spec.get("timezone") or "UTC",
        "created_at": now,
        "updated_at": now,
        "last_run_at": None,
        "schedule_offset_minutes": offset,
        "next_run_at": next_run_at(
            schedule,
            timezone_name=spec.get("timezone") or "UTC",
            offset_minutes=offset,
        ),
    }
    await db.jobs.insert_one(dict(job))
    return public_job(job)


async def list_jobs(user_id: str) -> List[Dict[str, Any]]:
    docs = await db.jobs.find({"user_id": user_id}, {"_id": 0}).to_list(200)
    return docs


async def get_job(user_id: str, job_id: str) -> Optional[Dict[str, Any]]:
    return await db.jobs.find_one({"user_id": user_id, "id": job_id}, {"_id": 0})


async def update_job(user_id: str, job_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    current = await get_job(user_id, job_id)
    if not current:
        return None
    data = {key: value for key, value in payload.items() if key not in {"id", "user_id", "created_at"}}
    merged = apply_job_type_defaults({**current, **data})
    validate_job(merged)
    if "job_type" in data:
        data["candidate_sources"] = merged["candidate_sources"]
        data["media_types"] = merged["media_types"]
        data["taste_sources"] = merged.get("taste_sources") or current.get("taste_sources")
        data["required_sources"] = [
            source for source in (merged.get("required_sources") or [])
            if source in (data["candidate_sources"] or [])
        ]
    if "schedule" in data or "timezone" in data or "schedule_offset_minutes" in data:
        data["next_run_at"] = next_run_at(
            data.get("schedule") or merged.get("schedule") or "manual",
            timezone_name=data.get("timezone") or merged.get("timezone") or "UTC",
            offset_minutes=int(
                data.get("schedule_offset_minutes")
                if data.get("schedule_offset_minutes") is not None
                else (merged.get("schedule_offset_minutes") or 0)
            ),
        )
    data["updated_at"] = _now().isoformat()
    await db.jobs.update_one({"user_id": user_id, "id": job_id}, {"$set": data})
    return await get_job(user_id, job_id)


async def delete_job(user_id: str, job_id: str) -> bool:
    result = await db.jobs.delete_one({"user_id": user_id, "id": job_id})
    return result.deleted_count > 0


async def acquire_job_lock(job_id: str, ttl_seconds: int = 180) -> Optional[str]:
    now = _now()
    existing = await db.job_locks.find_one({"job_id": job_id})
    if existing and existing.get("expires_at"):
        try:
            exp = datetime.fromisoformat(existing["expires_at"])
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp > now and existing.get("lock_owner"):
                return None
        except ValueError:
            pass
    owner = uuid.uuid4().hex
    await db.job_locks.update_one(
        {"job_id": job_id},
        {"$set": {
            "job_id": job_id,
            "lock_owner": owner,
            "acquired_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
        }},
        upsert=True,
    )
    claimed = await db.job_locks.find_one({"job_id": job_id})
    return owner if claimed and claimed.get("lock_owner") == owner else None


async def release_job_lock(job_id: str, owner: str) -> None:
    await db.job_locks.update_one(
        {"job_id": job_id, "lock_owner": owner},
        {"$set": {"lock_owner": None, "expires_at": None}},
    )


async def load_pipeline_inputs(user_id: str) -> Dict[str, List[Dict[str, Any]]]:
    history = await db.history.find({"user_id": user_id}, {"_id": 0, "user_id": 0}).to_list(10000)
    if not history:
        history = await db.media_history.find({"user_id": user_id}, {"_id": 0, "user_id": 0}).to_list(10000)
    library = await db.media_library.find({"user_id": user_id}, {"_id": 0, "user_id": 0}).to_list(20000)
    recommended = await db.recommendations.find({"user_id": user_id}, {"_id": 0, "user_id": 0}).to_list(2000)
    # A delivery that failed is not a decision, so it must not block the title
    # for ever. Pending, approved and rejected rows all stay excluded.
    requested = await db.requests.find(
        {"user_id": user_id, "status": {"$nin": ["request_failed", "failed"]}},
        {"_id": 0, "user_id": 0},
    ).to_list(5000)
    blacklist = await db.blacklist.find({"user_id": user_id}, {"_id": 0, "user_id": 0}).to_list(500)
    feedback = await db.recommendation_feedback.find({"user_id": user_id}, {"_id": 0, "user_id": 0}).to_list(500)
    return {
        "history": history,
        "library": library,
        "recommended": recommended,
        "requested": requested,
        "blacklist": blacklist,
        "feedback": feedback,
    }


# Keep Ollama prompts bounded — large job runs otherwise time out / return junk JSON.
RERANK_CANDIDATE_CAP = 40


async def rerank_verified_candidates(
    user_id: str,
    taste: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    model_override: Optional[str] = None,
) -> tuple[Optional[List[str]], str, str]:
    if not candidates:
        return None, "fallback", "deterministic"
    conn = await db.connections.find_one({"user_id": user_id}, {"_id": 0}) or {}
    rerank_pool = candidates[:RERANK_CANDIDATE_CAP]
    allowed = [str(row.get("candidate_id") or row.get("tmdb_id") or row["title"]) for row in candidates]
    lines = [
        f"{row.get('candidate_id')}: {row.get('title')} ({row.get('year')}) genres={','.join(row.get('genres') or [])}"
        for row in rerank_pool
    ]
    system = (
        "You only reorder verified candidate IDs. Never invent titles or IDs. "
        'Return JSON {"ids": ["id1", "id2"]}.'
    )
    prompt = (
        compact_taste_prompt(taste)
        + "\nVerified candidates:\n"
        + "\n".join(lines)
        + "\nReturn only those IDs, best first."
    )
    parsed, provider, model = await generate_with_llm(
        conn, "rerank", system, prompt, user_id=user_id, action="rerank", model_override=model_override
    )
    if not isinstance(parsed, dict):
        return None, provider, model
    raw_ids = parsed.get("ids") or parsed.get("candidate_ids") or []
    allowed_set = set(allowed)
    ordered = [str(item) for item in raw_ids if str(item) in allowed_set]
    return (ordered or None), provider, model


async def persist_run_results(
    user_id: str,
    job: Dict[str, Any],
    accepted: List[Dict[str, Any]],
    trigger: str,
    *,
    provider: str = "pipeline",
    model: str = "deterministic",
    ai_reranked: bool = False,
) -> List[Dict[str, Any]]:
    if trigger == "preview":
        return []
    from recommendation.media_identity import attach_canonical_ids

    identified = await attach_canonical_ids(user_id, [{**item} for item in accepted], persist_history=False)
    from providers.keys import resolve_tmdb_api_key, resolve_tvdb_api_key
    from providers.tmdb import enrich_with_tmdb

    conn = await db.connections.find_one({"user_id": user_id}, {"_id": 0}) or {}
    identified = await enrich_with_tmdb(
        identified,
        resolve_tmdb_api_key(conn),
        tvdb_api_key=resolve_tvdb_api_key(conn),
    )
    rows = []
    for item in identified:
        rows.append({
            **item,
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "saved": False,
            "dismissed": False,
            "in_library": False,
            "needs_approval": False,
            "provider": provider,
            "model": model,
            "ai_reranked": ai_reranked,
            "job_id": job.get("id"),
            "created_at": _now().isoformat(),
        })
    if rows:
        await db.recommendations.delete_many({"user_id": user_id, "saved": {"$ne": True}, "job_id": job.get("id")})
        await db.recommendations.insert_many(rows)
    return await apply_job_action_mode(user_id, job, rows, conn)


async def apply_job_action_mode(
    user_id: str,
    job: Dict[str, Any],
    rows: List[Dict[str, Any]],
    conn: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Queue posters for approval or send them into MediaManager.

    require_approval → pending Requests with posters + approve/reject buttons.
    auto_request → same MediaManager path as the Approve button.
    recommendations_only → stay on Home / AI Picks (PNG buttons still work there).
    """
    mode = job.get("action_mode") or "require_approval"
    if mode not in {"require_approval", "auto_request"} or not rows:
        return []

    from fastapi import HTTPException
    from providers.keys import resolve_tmdb_api_key
    from request_providers import LocalRequestProvider
    from mediamanager_client import send_item_to_library

    local = LocalRequestProvider()
    warnings: List[Dict[str, Any]] = []
    tmdb_key = resolve_tmdb_api_key(conn)

    for row in rows:
        payload = {
            "title": row.get("title"),
            "year": row.get("year"),
            "type": row.get("type"),
            "tmdb_id": row.get("tmdb_id"),
            "poster": row.get("poster") or row.get("poster_url"),
            "genres": row.get("genres"),
            "release_date": row.get("release_date") or row.get("first_air_date"),
            "original_language": row.get("original_language"),
            "tmdb_rating": row.get("tmdb_rating"),
            "source_job_id": job.get("id"),
            "recommendation_id": row.get("id"),
            "match_score": row.get("match_score"),
        }
        if mode == "require_approval":
            stored = await local.submit(user_id, payload, "pending_approval")
            await db.recommendations.update_one(
                {"user_id": user_id, "id": row["id"]},
                {"$set": {"request_id": stored["id"], "needs_approval": True}},
            )
            continue

        try:
            result = await send_item_to_library(row, conn, tmdb_api_key=tmdb_key)
            stored = await local.submit(
                user_id,
                {**payload, "tmdb_id": result["tmdb_id"], "provider": "mediamanager"},
                "approved",
            )
            await db.recommendations.update_one(
                {"user_id": user_id, "id": row["id"]},
                {
                    "$set": {
                        "in_library": True,
                        "tmdb_id": result["tmdb_id"],
                        "request_id": stored["id"],
                        "needs_approval": False,
                    }
                },
            )
        except HTTPException as exc:
            queued = exc.status_code == 409
            status = "pending_approval" if queued else "request_failed"
            stored = await local.submit(user_id, payload, status)
            await db.recommendations.update_one(
                {"user_id": user_id, "id": row["id"]},
                {
                    "$set": {
                        "request_id": stored["id"],
                        "needs_approval": queued,
                    }
                },
            )
            warnings.append({
                "code": "mediamanager_auto_request_failed",
                "source": "mediamanager",
                "detail": f"{row.get('title')}: {exc.detail}",
            })
            logging.warning("auto_request failed for %s: %s", row.get("title"), exc.detail)
        except Exception as exc:
            stored = await local.submit(user_id, payload, "request_failed")
            await db.recommendations.update_one(
                {"user_id": user_id, "id": row["id"]},
                {"$set": {"request_id": stored["id"], "needs_approval": False}},
            )
            warnings.append({
                "code": "mediamanager_auto_request_failed",
                "source": "mediamanager",
                "detail": f"{row.get('title')}: {exc}",
            })
            logging.warning("auto_request failed for %s: %s", row.get("title"), exc)
    return warnings


async def _advance_schedule(user_id: str, job: Dict[str, Any], finished: datetime) -> None:
    """Move the job to its next slot. Runs after success AND after failure.

    Skipping this on failure left next_run_at in the past, so the 60-second
    scheduler tick picked the job up again every minute instead of every 30.
    """
    await db.jobs.update_one(
        {"user_id": user_id, "id": job["id"]},
        {"$set": {
            "last_run_at": finished.isoformat(),
            "next_run_at": next_run_at(
                job.get("schedule"),
                now=finished,
                timezone_name=job.get("timezone") or "UTC",
                offset_minutes=int(job.get("schedule_offset_minutes") or 0),
            ),
            "updated_at": finished.isoformat(),
        }},
    )


async def execute_job(
    user_id: str,
    job: Dict[str, Any],
    trigger: str,
    catalog: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    owner = await acquire_job_lock(job["id"])
    if not owner:
        return {"status": "locked", "detail": "Job is already running"}
    started = _now()
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    warnings: List[Dict[str, Any]] = []
    try:
        inputs = await load_pipeline_inputs(user_id)
        warnings.extend(await required_history_warnings(user_id, job))
        extra: List[Dict[str, Any]] = []
        sources = set(job.get("candidate_sources") or [])
        required = _required_sources(job)
        tmdb_wanted = sources & {"tmdb_discover", "tmdb_similar", "tmdb_recommendations"}
        if tmdb_wanted:
            from providers.keys import resolve_tmdb_api_key
            from providers.tmdb import fetch_job_candidates

            conn_for_key = await db.connections.find_one({"user_id": user_id}, {"_id": 0}) or {}
            tmdb_key = resolve_tmdb_api_key(conn_for_key)
            if not tmdb_key:
                warnings.append({"code": "tmdb_not_configured", "source": "tmdb"})
                if tmdb_wanted & required:
                    raise ValueError("Required TMDb source is not configured")
            else:
                # Walk the discover pages forward every run. Staying on page 1
                # meant the same titles came back for ever, all of them already
                # requested, so the job accepted nothing.
                from providers.tmdb import TMDB_MAX_PAGE, discover_page_span

                span = discover_page_span(job)
                start_page = max(1, int(job.get("tmdb_page_cursor") or 1))
                try:
                    extra = await fetch_job_candidates(
                        job, inputs["history"], api_key=tmdb_key, start_page=start_page
                    )
                    if trigger != "preview":
                        next_page = start_page + span
                        if next_page > TMDB_MAX_PAGE:
                            next_page = 1
                        await db.jobs.update_one(
                            {"user_id": user_id, "id": job["id"]},
                            {"$set": {"tmdb_page_cursor": next_page}},
                        )
                except Exception as exc:
                    warnings.append({"code": "tmdb_failed", "source": "tmdb", "detail": safe_provider_error(exc)})
                    if tmdb_wanted & required:
                        raise
        linked_wanted = sources & {"trakt", "simkl", "anilist"}
        if linked_wanted:
            linked, linked_warnings = await fetch_linked_provider_candidates(user_id, sources, required)
            extra.extend(linked)
            warnings.extend(linked_warnings)
        result = run_pipeline(job, catalog=catalog or [], extra_candidates=extra, **inputs)
        ranked = result.get("ranked") or result["accepted"]
        provider = "pipeline"
        model = "deterministic"
        ai_reranked = False
        if job.get("ai_enabled") and ranked:
            ordered, provider, model = await rerank_verified_candidates(user_id, result["taste"], ranked)
            if ordered:
                ranked = apply_rerank(ranked, ordered)
                ai_reranked = provider == "ollama"
            else:
                if provider == "ollama":
                    warnings.append({
                        "code": "ollama_rerank_unusable",
                        "source": "ollama",
                        "detail": "Model returned no verified candidate IDs; using deterministic order.",
                    })
                logging.warning("Ollama rerank skipped (%s); using deterministic order", provider)
                provider = "pipeline"
                model = "deterministic"
        limit = int((result.get("job") or job).get("final_recommendation_limit") or 8)
        result["accepted"] = ranked[:limit]
        result["ai_reranked"] = ai_reranked
        action_warnings = await persist_run_results(
            user_id,
            job,
            result["accepted"],
            trigger,
            provider="ollama" if ai_reranked else "pipeline",
            model=model if ai_reranked else "deterministic",
            ai_reranked=ai_reranked,
        )
        if action_warnings:
            warnings.extend(action_warnings)
        finished = _now()
        status = "completed_with_warnings" if warnings else "completed"
        run = {
            "id": run_id,
            "job_id": job["id"],
            "user_id": user_id,
            "trigger_type": trigger,
            "status": status,
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "candidate_count": result["candidate_count"],
            "accepted_count": len(result["accepted"]),
            "rejected_count": len(result["rejected"]),
            "action_mode": job.get("action_mode"),
            "ai_reranked": ai_reranked,
            "warnings": warnings,
            "results": result["accepted"] if trigger == "preview" else [{"id": row.get("title"), "title": row.get("title")} for row in result["accepted"]],
        }
        await db.job_runs.insert_one(dict(run))
        if trigger != "preview":
            await _advance_schedule(user_id, job, finished)
        run.pop("_id", None)
        payload = {
            "status": "ok" if not warnings else "completed_with_warnings",
            "run": {k: v for k, v in run.items() if k != "_id"},
            "accepted": result["accepted"],
            "warnings": warnings,
        }
        if trigger == "preview":
            payload["rejected"] = result.get("rejected") or []
            payload["ranked"] = result.get("ranked") or result["accepted"]
        return payload
    except Exception as exc:
        detail = safe_provider_error(exc)
        logging.warning("Job %s failed: %s", job.get("id"), detail)
        finished = _now()
        run = {
            "id": run_id,
            "job_id": job["id"],
            "user_id": user_id,
            "trigger_type": trigger,
            "status": "failed",
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "error": detail,
            "warnings": warnings,
        }
        await db.job_runs.insert_one(dict(run))
        if trigger != "preview":
            await _advance_schedule(user_id, job, finished)
        run.pop("_id", None)
        return {"status": "failed", "detail": detail, "run": run, "accepted": [], "warnings": warnings}
    finally:
        await release_job_lock(job["id"], owner)


async def list_runs(user_id: str, job_id: Optional[str] = None) -> List[Dict[str, Any]]:
    query: Dict[str, Any] = {"user_id": user_id}
    if job_id:
        query["job_id"] = job_id
    return await db.job_runs.find(query, {"_id": 0}).sort("started_at", -1).to_list(100)


async def due_jobs() -> List[Dict[str, Any]]:
    now = _now().isoformat()
    return await db.jobs.find(
        {"enabled": True, "schedule": {"$ne": "manual"}, "next_run_at": {"$lte": now}},
        {"_id": 0},
    ).to_list(100)


async def fetch_linked_provider_candidates(
    user_id: str,
    sources: set,
    required: Optional[set] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Pull live recommendation feeds only when the user already has tokens."""
    required = required or set()
    conn = await db.connections.find_one({"user_id": user_id}, {"_id": 0}) or {}
    extra: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    async def _required_or_warn(source: str, code: str, detail: str = "") -> None:
        row = {"code": code, "source": source}
        if detail:
            row["detail"] = detail
        if source in required:
            warnings.append(row)
            raise ValueError(f"Required source {source} failed: {code}")
        logging.warning("optional source %s: %s %s", source, code, detail)

    if "trakt" in sources:
        if not conn.get("trakt_access_token"):
            await _required_or_warn("trakt", "trakt_not_connected")
        else:
            try:
                from providers.trakt import parse_recommendation_entry, resolve_trakt_client_id, trakt_headers, trakt_token
                from config import TRAKT_API

                token = await trakt_token(user_id, conn)
                client_id = resolve_trakt_client_id(conn)
                if token and client_id:
                    import httpx

                    async with httpx.AsyncClient(timeout=10) as client:
                        for kind in ("movies", "shows"):
                            response = await client.get(
                                f"{TRAKT_API}/recommendations/{kind}",
                                headers=trakt_headers(client_id, token),
                                params={"limit": 20},
                            )
                            if response.status_code == 200:
                                extra.extend(
                                    row for row in (
                                        parse_recommendation_entry(item) for item in response.json() or []
                                    ) if row and row.get("title") and row["title"] != "Unknown"
                                )
                            else:
                                await _required_or_warn("trakt", "trakt_http_error", str(response.status_code))
                else:
                    await _required_or_warn("trakt", "trakt_not_connected")
            except ValueError:
                raise
            except Exception as exc:
                await _required_or_warn("trakt", "trakt_failed", safe_provider_error(exc))
    if "simkl" in sources:
        if not conn.get("simkl_access_token"):
            await _required_or_warn("simkl", "simkl_not_connected")
        else:
            try:
                from providers.simkl import fetch_recommendations
                from config import SIMKL_CLIENT_ID

                client_id = conn.get("simkl_client_id") or SIMKL_CLIENT_ID
                history = await db.history.find(
                    {"user_id": user_id, "source": "simkl"},
                    {"_id": 0, "simkl_id": 1, "type": 1, "media_type": 1, "title": 1, "source": 1},
                ).sort("watched_at", -1).to_list(40)
                rows = await fetch_recommendations(
                    client_id,
                    conn["simkl_access_token"],
                    history,
                    limit=40,
                )
                if rows:
                    extra.extend(rows)
                else:
                    await _required_or_warn("simkl", "simkl_empty_feed")
            except ValueError:
                raise
            except Exception as exc:
                detail = safe_provider_error(exc)
                code = "simkl_http_error" if "failed" in str(detail).lower() or str(detail).isdigit() else "simkl_failed"
                await _required_or_warn("simkl", code, detail)
    if "anilist" in sources:
        if not conn.get("anilist_access_token"):
            await _required_or_warn("anilist", "anilist_not_connected")
        else:
            try:
                from providers.anilist import fetch_recommendations

                history = await db.history.find(
                    {"user_id": user_id, "source": "anilist"},
                    {"_id": 0, "anilist_id": 1, "source": 1, "title": 1},
                ).sort("watched_at", -1).to_list(40)
                extra.extend(await fetch_recommendations(conn["anilist_access_token"], history))
            except ValueError:
                raise
            except Exception as exc:
                await _required_or_warn("anilist", "anilist_failed", safe_provider_error(exc))
    return extra, warnings


async def restagger_jobs() -> None:
    """Give every user's interval jobs their own 6-minute slot.

    Jobs created before auto-staggering existed all sat on offset 0 and fired
    in one burst. Anything off the 6-minute grid is reassigned here, oldest job
    first, so the order is stable across restarts and already-correct jobs keep
    their next_run_at untouched.
    """
    now = _now()
    user_ids = await db.jobs.distinct("user_id", {"schedule": "every_30m"})
    for user_id in user_ids:
        jobs = await db.jobs.find(
            {"user_id": user_id, "schedule": "every_30m"},
            {"_id": 0, "id": 1, "created_at": 1, "schedule_offset_minutes": 1, "next_run_at": 1},
        ).to_list(500)
        jobs.sort(key=lambda job: (job.get("created_at") or "", job.get("id") or ""))
        for index, job in enumerate(jobs):
            offset = stagger_offset(index)
            if int(job.get("schedule_offset_minutes") or 0) == offset and job.get("next_run_at"):
                continue
            await db.jobs.update_one(
                {"user_id": user_id, "id": job["id"]},
                {"$set": {
                    "schedule_offset_minutes": offset,
                    "next_run_at": next_run_at("every_30m", now=now, offset_minutes=offset),
                    "updated_at": now.isoformat(),
                }},
            )


async def migrate_jobs_to_interval() -> None:
    """Existing jobs keep running, but on a 30-minute clock, 6 minutes apart."""
    await db.jobs.update_many(
        {"schedule": {"$nin": ["every_30m"]}},
        {"$set": {"schedule": "every_30m", "enabled": True}},
    )
    await restagger_jobs()


async def scheduler_loop() -> None:
    from api_extra import SEED_CATALOG

    await asyncio.sleep(3)
    while True:
        try:
            for job in await due_jobs():
                await execute_job(job["user_id"], job, "scheduled", SEED_CATALOG)
        except Exception:
            logging.exception("Scheduled job tick failed")
        await asyncio.sleep(60)


def start_scheduler():
    if "pytest" in sys.modules:
        return None
    return asyncio.create_task(scheduler_loop())
