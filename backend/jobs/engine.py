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
from recommendation.ranking_engine import apply_rerank, relevance_cut

HISTORY_PROVIDERS = {"plex", "trakt", "simkl", "anilist"}
HISTORY_STALE_AFTER = timedelta(hours=24)
HISTORY_CAP = 200000
REQUESTS_CAP = 200000
RECOMMENDED_CAP = 100000

# Interval jobs share one 15-minute grid. Each job owns a 2-minute slot
# (0, 2, 4, ... 12) so two jobs never start on top of each other and every
# job still runs four times an hour. Gilbert asked for this on 2026-09-25;
# it was a 30-minute grid with 6-minute slots before.
INTERVAL_SCHEDULE = "every_15m"
# The earlier interval key. Stored jobs are moved off it at startup
# (`migrate_jobs_to_interval`); a client that still sends it gets the new one.
LEGACY_INTERVAL_SCHEDULES = frozenset({"every_30m"})
JOB_INTERVAL_MINUTES = 15
JOB_STAGGER_MINUTES = 2
JOB_STAGGER_SLOTS = JOB_INTERVAL_MINUTES // JOB_STAGGER_MINUTES

#: What one saved job may ask for (Gilbert, 2026-09-27: "30000 kandidater, max
#: results den kan skicka 1500"): up to 30,000 candidates gathered per run and at
#: most 1,500 results - picks per run, which is also how many of its titles a job
#: keeps waiting in Requests (apply_job_action_mode). New jobs start at both. A
#: larger value is saved as the maximum; Content to Watch and AI Search keep their
#: own, much smaller, limits.
MAX_CANDIDATE_LIMIT = 30000
MAX_FINAL_LIMIT = 1500
JOB_LIMIT_DEFAULTS = {"candidate_limit": MAX_CANDIDATE_LIMIT, "final_recommendation_limit": MAX_FINAL_LIMIT}
#: From this many candidates on a job takes Trakt's whole recommendation feed (100 per kind).
TRAKT_FULL_FEED_BUDGET = 2000
#: A run of a large job takes minutes, not seconds; a second run of the same job
#: (Run now while the scheduled one works) must not start before it is done.
JOB_LOCK_SECONDS = 1800
#: Excluded and scored rows a preview answers with (its results come in full).
PREVIEW_ROWS = 200


def clamp_limits(spec: Dict[str, Any]) -> Dict[str, Any]:
    """The job with its limits inside what CineMind allows (1..MAX_*)."""
    out = dict(spec)
    for key, ceiling in (("candidate_limit", MAX_CANDIDATE_LIMIT), ("final_recommendation_limit", MAX_FINAL_LIMIT)):
        value = out.get(key)
        if value in (None, ""):
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a whole number")
        out[key] = max(1, min(number, ceiling))
    return out


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


def with_job_intent(job: Dict[str, Any]) -> Dict[str, Any]:
    """Saved jobs are served by what they ask for (recommendation.job_intent).

    Content to Watch sets job_intent False and keeps its measured behaviour.
    """
    return {**job, "job_intent": job.get("job_intent", True)}


def with_result_rules(job: Dict[str, Any]) -> Dict[str, Any]:
    """Saved jobs return at least MIN_RESULTS results, the open ones included.

    Gilbert, 2026-09-29: every run gives at least 100 results - titles that fit
    the job, new or still waiting in Requests - topped up from below the taste
    floor when fewer clear it (pipeline.MIN_RESULTS, jobs.upcoming.broaden).
    Content to Watch sets both off and keeps its measured top eight.
    """
    from recommendation.pipeline import MIN_RESULTS

    return {**job, "open_results": job.get("open_results", True), "min_results": job.get("min_results", MIN_RESULTS)}


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

    every_15m lands on a fixed :00/:15/:30/:45 grid shifted by the job's
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
    schedule = normalize_schedule(schedule)
    if schedule == INTERVAL_SCHEDULE:
        offset = int(offset_minutes or 0) % JOB_INTERVAL_MINUTES
        grid = stamp.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=offset)
        while grid <= stamp:
            grid = grid + timedelta(minutes=JOB_INTERVAL_MINUTES)
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


def normalize_schedule(schedule: Optional[str]) -> str:
    """The stored schedule key; the old interval key means the current interval."""
    schedule = schedule or INTERVAL_SCHEDULE
    return INTERVAL_SCHEDULE if schedule in LEGACY_INTERVAL_SCHEDULES else schedule


def stagger_offset(index: int) -> int:
    """Slot `index` of the 15-minute window, 2 minutes apart: 0, 2, 4, ... 12."""
    return (index % JOB_STAGGER_SLOTS) * JOB_STAGGER_MINUTES


async def next_stagger_offset(user_id: str) -> int:
    """Lowest free 2-minute slot for this user, so a new job never collides.

    All seven slots taken means the least crowded one is reused; eight or more
    interval jobs cannot all be 2 minutes apart inside a quarter of an hour.
    """
    docs = await db.jobs.find(
        {"user_id": user_id, "schedule": INTERVAL_SCHEDULE},
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
    schedule = normalize_schedule(spec.get("schedule"))
    if schedule not in {"manual", "daily", "weekly", INTERVAL_SCHEDULE}:
        raise ValueError("Invalid schedule")
    offset = spec.get("schedule_offset_minutes")
    if offset not in (None, ""):
        if int(offset) < 0 or int(offset) > JOB_INTERVAL_MINUTES - 1:
            raise ValueError(f"schedule_offset_minutes must be between 0 and {JOB_INTERVAL_MINUTES - 1}")
    if spec.get("action_mode") not in {None, "recommendations_only", "require_approval", "auto_request"}:
        raise ValueError("Invalid action mode")
    unknown_required = _required_sources(spec) - set(spec.get("candidate_sources") or [])
    if unknown_required:
        raise ValueError(f"Required sources must also be selected: {', '.join(sorted(unknown_required))}")


async def create_job(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    spec = clamp_limits(apply_job_type_defaults({**default_job(), **JOB_LIMIT_DEFAULTS, **payload}))
    validate_job(spec)
    schedule = normalize_schedule(spec.get("schedule"))
    offset = spec.get("schedule_offset_minutes")
    if offset in (None, "") and schedule == INTERVAL_SCHEDULE:
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
        "schedule": schedule,
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
    if "schedule" in data:
        data["schedule"] = normalize_schedule(data.get("schedule"))
    merged = clamp_limits(apply_job_type_defaults({**current, **data}))
    validate_job(merged)
    for key in ("candidate_limit", "final_recommendation_limit"):
        # A value above the maximum is saved as the maximum, also on a job that
        # held one from before the maximum existed.
        if merged.get(key) != current.get(key) or key in data:
            data[key] = merged.get(key)
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


async def acquire_job_lock(job_id: str, ttl_seconds: int = JOB_LOCK_SECONDS) -> Optional[str]:
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
    # Caps sized well above the real collections. Gilbert's history had grown to
    # 10,210 rows against a 10,000 cap, so the 210 most recent watch events were
    # dropped from every run - exactly the rows `recent_interest` is built from -
    # and 4,000 of his 9,041 requests fell outside the request cap, which let
    # already-requested titles come back as fresh recommendations.
    history = await db.history.find({"user_id": user_id}, {"_id": 0, "user_id": 0}).to_list(HISTORY_CAP)
    if not history:
        history = await db.media_history.find({"user_id": user_id}, {"_id": 0, "user_id": 0}).to_list(HISTORY_CAP)
    library = await db.media_library.find({"user_id": user_id}, {"_id": 0, "user_id": 0}).to_list(50000)
    # Every saved job now keeps up to 1,500 results a run on its list and sends
    # them to Requests (Gilbert, 2026-09-29), so both collections grow faster
    # than before; a row past the cap is a title the exclusions cannot see.
    recommended = await db.recommendations.find({"user_id": user_id}, {"_id": 0, "user_id": 0}).to_list(
        RECOMMENDED_CAP)
    # A delivery that failed is not a decision, so it must not block the title
    # for ever. Pending, approved and rejected rows all stay excluded.
    requested = await db.requests.find(
        {"user_id": user_id, "status": {"$nin": ["request_failed", "failed"]}},
        {"_id": 0, "user_id": 0},
    ).to_list(REQUESTS_CAP)
    # No cap: a blacklist or feedback row past a 500-row cap was simply not
    # applied, and the title it was about could come back.
    blacklist = await db.blacklist.find({"user_id": user_id}, {"_id": 0, "user_id": 0}).to_list(None)
    feedback = await db.recommendation_feedback.find({"user_id": user_id}, {"_id": 0, "user_id": 0}).to_list(None)
    # Personal ratings live in media_history, raw watch events in history. v1 read
    # one or the other, so a user with a full history never had their own ratings
    # reach the taste profile at all.
    personal = await db.media_history.find({"user_id": user_id}, {"_id": 0, "user_id": 0}).to_list(20000)
    return {
        "history": history,
        "personal_history": personal,
        "library": library,
        "recommended": recommended,
        "requested": requested,
        "blacklist": blacklist,
        "feedback": feedback,
    }


# Keep Ollama prompts bounded - large job runs otherwise time out / return junk
# JSON. Benchmarked on this user's own history (evaluation/model_bench.py):
# 12 scored NDCG@5 0.572 / P@5 0.520 with zero invented IDs, against 0.514 /
# 0.440 at 24 and 0.484 / 0.400 at 20. A 7B model ranks a short list well and
# a long one carelessly, and the deterministic order behind it is now strong,
# so there is nothing to gain from handing over more.
RERANK_CANDIDATE_CAP = 12
# Below this share of the list the answer says more about the model running out
# of patience than about the ranking, so the deterministic order is kept.
RERANK_MIN_COVERAGE = 0.5
# Gemma 4 re-ranks the tail too aggressively. Keeping only its top 5 and letting
# the deterministic order hold positions 6-10 measured +0.022 +/- 0.008 nDCG@10
# (t=2.64) on Gilbert's snapshot, 2026-09-23, for the model's own order - the
# way Content to Watch uses it. The model still has to return all
# RERANK_CANDIDATE_CAP handles for the answer to count; positions 1-5, and so the
# hero card, stay the model's - but only for picks inside the relevance floor
# (see rerank_verified_candidates). Saved jobs re-rank bounded over the model's
# whole order, as measured (ranking_engine.RERANK_MAX_BOOST), so they do not cut
# it. Measured once, not confirmed with a second fold count: 12 undoes it.
RERANK_LLM_KEEP = 5


def rerank_bounded(job: Optional[Dict[str, Any]]) -> bool:
    """A saved job's list is ordered by what the job asked for and the model may
    only break near-ties there; Content to Watch lets it reorder its above-floor
    pool (measured, ranking_engine.RERANK_MAX_BOOST)."""
    from recommendation.job_intent import job_intent

    return job is not None and job_intent(job) is not None


def apply_model_order(ranked: List[Dict[str, Any]], ordered: Optional[List[str]],
                      job: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The model's answer over the ranked list, bounded or free as the job re-ranks."""
    from recommendation.ranking_engine import RERANK_MAX_BOOST

    return apply_rerank(ranked, ordered, max_boost=RERANK_MAX_BOOST if rerank_bounded(job) else None)


def rerank_keep(job: Optional[Dict[str, Any]]) -> Optional[int]:
    """How many of the model's picks lead the list: its top five, or its whole
    (floor-checked) order for a bounded re-rank."""
    return None if rerank_bounded(job) else RERANK_LLM_KEEP


def rerank_schema(count: int) -> Dict[str, Any]:
    """Constrain decoding to the answer shape, and to a complete answer.

    Without a schema the model sometimes starts explaining its reasoning in
    prose and the whole rerank is discarded. Without the length bound it
    sometimes stops after one or two IDs and the rest silently keep their
    deterministic order.
    """
    return {
        "type": "object",
        "properties": {
            "ids": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": count,
                "maxItems": count,
            }
        },
        "required": ["ids"],
    }


def rerank_pool(candidates: List[Dict[str, Any]], job: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """What the model may reorder: the head of the list, above the job's taste floor.

    A title below the floor is never selected (pipeline.select_final), so the
    model's slots are not spent on it.
    """
    from recommendation.pipeline import clears_taste_floor, taste_floor

    floor = taste_floor(job or {}) if job is not None else None
    pool = [row for row in candidates if floor is None or clears_taste_floor(row, floor)]
    return pool[:RERANK_CANDIDATE_CAP]


def rerank_lines(pool: List[Dict[str, Any]], taste: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """Short handles and one line per candidate, shared with evaluation/model_bench.py."""
    from recommendation.llm_context import candidate_evidence

    # Short opaque handles, not the candidate slugs. The slugs are long, accented
    # and full of spaces ("pokemon horizons the series:2023"); asked to echo a
    # dozen of them back exactly, a 7B model gave up after the first one and the
    # whole re-rank was discarded on every run.
    handles = {"r%02d" % index: row for index, row in enumerate(pool, start=1)}
    lines = []
    for handle, row in handles.items():
        detail = [
            "%s (%s)" % (row.get("title"), row.get("year")),
            row.get("media_type") or row.get("type") or "",
            "genres=%s" % ",".join(row.get("genres") or []) if row.get("genres") else "",
        ]
        themes = [str(name) for name in (row.get("tmdb_keywords") or [])[:5]]
        if themes:
            detail.append("themes=%s" % ",".join(themes))
        evidence = candidate_evidence(row, taste)
        if evidence:
            detail.append("evidence=%s" % evidence)
        else:
            similar = [item.get("title") for item in (row.get("similar_to") or [])[:2] if item.get("title")]
            if similar:
                detail.append("resembles=%s" % "; ".join(similar))
        if row.get("original_language"):
            detail.append("lang=%s" % row["original_language"])
        lines.append("%s | %s" % (handle, " | ".join(part for part in detail if part)))
    return handles, lines


async def rerank_verified_candidates(
    user_id: str,
    taste: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    model_override: Optional[str] = None,
    job: Optional[Dict[str, Any]] = None,
    keep: Optional[int] = RERANK_LLM_KEEP,
) -> Tuple[Optional[List[str]], str, str]:
    """The model's order over the head of the list, as candidate ids.

    `keep` is how many of its picks may lead (None: its whole order, for a
    bounded re-rank). Picks below the relevance floor are left out either way:
    they keep their deterministic place.
    """
    if not candidates:
        return None, "fallback", "deterministic"
    from recommendation.job_intent import job_intent

    conn = await db.connections.find_one({"user_id": user_id}, {"_id": 0}) or {}
    pool = rerank_pool(candidates, job)
    if len(pool) < 2:
        return None, "fallback", "deterministic"
    handles, lines = rerank_lines(pool, taste)
    system = (
        "You re-rank a verified candidate list for one viewer. Use only the given "
        "handles, never invent one, never drop one, never repeat one. Put the titles "
        "this viewer is most likely to genuinely enjoy first. Popularity is not the "
        'goal; fit to the stated taste is. Reply with JSON only: {"ids": ["<handle>", ...]}.'
    )
    prompt = (
        compact_taste_prompt(taste, job_intent(job))
        + "\n\nVerified candidates (%d):\n" % len(lines)
        + "\n".join(lines)
        + "\n\nReturn all %d handles above, ordered best first for this viewer." % len(lines)
    )
    parsed, provider, model = await generate_with_llm(
        conn, "rerank", system, prompt, user_id=user_id, action="rerank",
        model_override=model_override, response_schema=rerank_schema(len(lines)),
    )
    if not isinstance(parsed, dict):
        return None, provider, model
    raw_ids = parsed.get("ids") or parsed.get("candidate_ids") or parsed.get("ranking") or []
    ordered: List[str] = []
    scores: Dict[str, float] = {}
    seen_handles = set()
    for item in raw_ids:
        handle = str(item).strip()
        row = handles.get(handle)
        if row is None or handle in seen_handles:
            continue
        seen_handles.add(handle)
        key = str(row.get("candidate_id") or row.get("tmdb_id") or row["title"])
        ordered.append(key)
        scores[key] = row.get("rank_score") or 0.0
    if len(ordered) < max(1, int(len(lines) * RERANK_MIN_COVERAGE)):
        logging.warning(
            "Ollama rerank returned %s of %s handles; keeping deterministic order",
            len(ordered), len(lines),
        )
        return None, provider, model
    # The model may reorder the strong pool, never lift a weak taste match over
    # it: a pick below the relevance floor goes back to the deterministic order,
    # where the floor keeps it behind every stronger title. Left where the model
    # put it, it also ended apply_diversity's walk there and cut the list short -
    # at position 1 Content to Watch came back empty. The floor is the pool's, the
    # same cut select_final's apply_diversity makes over the titles above the
    # taste floor.
    floor = relevance_cut(pool)
    head = ordered[:keep] if keep else ordered
    kept = [key for key in head if scores[key] >= floor]
    if len(kept) < len(head):
        logging.warning(
            "Ollama rerank put %s weak match(es) in its top %s; they keep their deterministic place",
            len(head) - len(kept), len(head),
        )
    if not kept:
        return None, provider, model
    return kept, provider, model


async def persist_run_results(
    user_id: str,
    job: Dict[str, Any],
    accepted: List[Dict[str, Any]],
    trigger: str,
    *,
    provider: str = "pipeline",
    model: str = "deterministic",
    ai_reranked: bool = False,
    outcome: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    if trigger == "preview":
        return []
    from recommendation.media_identity import attach_canonical_ids

    from recommendation.ranking_engine import strip_private

    identified = await attach_canonical_ids(user_id, [strip_private(item) for item in accepted], persist_history=False)
    from providers.keys import resolve_tmdb_api_key, resolve_tvdb_api_key
    from providers.tmdb import enrich_with_tmdb

    conn = await db.connections.find_one({"user_id": user_id}, {"_id": 0}) or {}
    identified = await enrich_with_tmdb(
        identified,
        resolve_tmdb_api_key(conn),
        tvdb_api_key=resolve_tvdb_api_key(conn),
    )
    rows = []
    for position, item in enumerate(identified, start=1):
        rows.append({
            **item,
            # Explicit rank. Reading the list back by created_at reversed it,
            # so the weakest pick was the one shown first on Home.
            "rank": position,
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
    kept: List[Dict[str, Any]] = []
    if rows:
        from providers.premieres import is_upcoming_job

        if is_upcoming_job(job):
            # An upcoming job's earlier picks stay until their premiere
            # (jobs.upcoming.carry_over); they follow this run's in the list.
            from jobs.upcoming import carry_over

            kept = await carry_over(user_id, job, rows)
        # A dismissed row stays behind (hidden everywhere) as the memory of that
        # decision: deleting it with the rest of the old list let the next run
        # recommend the rejected title again (exclusion_engine "dismissed").
        await db.recommendations.delete_many({
            "user_id": user_id, "saved": {"$ne": True}, "dismissed": {"$ne": True}, "job_id": job.get("id"),
            "id": {"$nin": [row["id"] for row in kept]},
        })
        await db.recommendations.insert_many(rows)
        for position, row in enumerate(kept, start=len(rows) + 1):
            await db.recommendations.update_one({"user_id": user_id, "id": row["id"]}, {"$set": {"rank": position}})
    # A kept pick that never reached the queue (it waited for room) is offered again.
    waiting_for_room = [row for row in kept if not row.get("request_id")]
    return await apply_job_action_mode(user_id, job, rows + waiting_for_room, conn, outcome=outcome)


async def apply_job_action_mode(
    user_id: str,
    job: Dict[str, Any],
    rows: List[Dict[str, Any]],
    conn: Dict[str, Any],
    outcome: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Queue posters for approval or send them into MediaManager.

    require_approval → pending Requests with posters + approve/reject buttons.
    auto_request → same MediaManager path as the Approve button.
    recommendations_only → stay on Home / AI Picks (PNG buttons still work there).

    `outcome`, when given, is filled with what actually happened in Requests:
    new titles queued, waiting ones refreshed, and the job's waiting count
    afterwards. The run toast said "2 sent to Requests" for picks that the full
    queue had held back.

    Every result goes to Requests (Gilbert, 2026-09-29: each run sends at least
    100 results to the Requests tab, preferably well over 1,000). Until then a
    job kept at most its limit waiting and held every new title back beyond it:
    Tv (2,078 waiting against 650) and Upcoming US (3,603) sent nothing new on
    any run. `held_back` stays in the outcome, always 0, for older readers.
    """
    mode = job.get("action_mode") or "require_approval"
    if mode not in {"require_approval", "auto_request"}:
        return []
    if not rows:
        if outcome is not None:
            outcome.update({"mode": mode, "limit": int(job.get("final_recommendation_limit") or 8),
                            "queued": 0, "refreshed": 0, "held_back": 0, "sent": 0})
        return []

    from fastapi import HTTPException
    from providers.keys import resolve_tmdb_api_key
    from request_providers import (
        DECIDED_STATUSES, FINAL_STATUSES, PENDING_STATUSES, LocalRequestProvider,
        find_existing_request, is_user_rejection,
    )
    from mediamanager_client import send_item_to_library

    local = LocalRequestProvider()
    warnings: List[Dict[str, Any]] = []
    tmdb_key = resolve_tmdb_api_key(conn)
    limit = int(job.get("final_recommendation_limit") or 8)
    # The job's titles waiting for a decision, reported with the outcome. It no
    # longer caps anything: every result reaches Requests (see above).
    waiting = await db.requests.count_documents({
        "user_id": user_id, "source_job_id": job.get("id"), "status": {"$in": sorted(PENDING_STATUSES)},
    })
    held_back = added = refreshed = sent = 0

    for row in rows:
        if row.get("continuation_in_library"):
            # A coming season of a series already in the library: Up Coming shows
            # it, and the library's own series brings the season in.
            continue
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
            # What the title is, so the queue recognises it next run (request_providers).
            "canonical_media_id": row.get("canonical_media_id"),
            "media_type": row.get("media_type"),
            "format": row.get("format") or row.get("anime_format"),
            "anilist_id": row.get("anilist_id"),
            # Below the taste floor, filling the run's results up (pipeline._complete_results).
            "weak_match": bool(row.get("weak_match")),
        }
        # The user's decisions stand. A title the exclusions miss (a changed
        # media type or identity) is neither queued again nor sent to
        # MediaManager; a rejected one is hidden the way POST
        # /requests/{id}/reject hides it.
        existing = await find_existing_request(user_id, payload, database=db)
        if is_user_rejection(existing):
            await db.recommendations.update_one(
                {"user_id": user_id, "id": row["id"]},
                {"$set": {"dismissed": True, "needs_approval": False, "request_id": existing.get("id")}},
            )
            continue
        if mode == "require_approval":
            status = existing.get("status")
            adds_to_queue = not existing or status not in PENDING_STATUSES | FINAL_STATUSES
            stored = await local.submit(user_id, payload, "pending_approval")
            if adds_to_queue and stored.get("status") in PENDING_STATUSES:
                added += 1
            elif status in PENDING_STATUSES:
                refreshed += 1
            await db.recommendations.update_one(
                {"user_id": user_id, "id": row["id"]},
                {"$set": {
                    "request_id": stored["id"],
                    "needs_approval": stored["status"] == "pending_approval",
                }},
            )
            continue

        if existing.get("status") in DECIDED_STATUSES:
            # Approved already: it went to MediaManager then, not a second time now.
            await db.recommendations.update_one(
                {"user_id": user_id, "id": row["id"]},
                {"$set": {"request_id": existing.get("id"), "needs_approval": False}},
            )
            continue
        try:
            result = await send_item_to_library(row, conn, tmdb_api_key=tmdb_key)
            stored = await local.submit(
                user_id,
                {**payload, "tmdb_id": result["tmdb_id"], "provider": "mediamanager"},
                "approved",
            )
            sent += 1
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
            adds_to_queue = queued and (not existing or existing.get("status") not in PENDING_STATUSES | FINAL_STATUSES)
            stored = await local.submit(user_id, payload, status)
            if adds_to_queue and stored.get("status") in PENDING_STATUSES:
                added += 1
            elif queued and existing.get("status") in PENDING_STATUSES:
                refreshed += 1
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
    waiting += added
    if outcome is not None:
        outcome.update({"mode": mode, "limit": limit, "waiting": waiting, "queued": added,
                        "refreshed": refreshed, "held_back": held_back, "sent": sent})
    return warnings


async def _advance_schedule(user_id: str, job: Dict[str, Any], finished: datetime) -> None:
    """Move the job to its next slot. Runs after success AND after failure.

    Skipping this on failure left next_run_at in the past, so the 60-second
    scheduler tick picked the job up again every minute instead of every 15.
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


REJECTION_HINTS = {
    "rejected_year": "No candidate fell inside the job's year window. Widen it, or add a source that can reach those years.",
    "rejected_genre": "No candidate matched the job's genres.",
    "rejected_rating": "Every candidate scored below the job's minimum rating.",
    "rejected_vote_count": "Every candidate had fewer votes than the job requires.",
    "rejected_media_type": "No candidate matched the job's media types.",
    "rejected_language": "No candidate matched the job's languages.",
    "rejected_runtime": "No candidate matched the job's runtime window.",
    "rejected_release_date": "No candidate fell inside the job's release-date window.",
    "rejected_already_requested": "Every candidate had already been requested. The job needs fresh candidates.",
    "rejected_already_watched": "Every candidate was already in your watch history.",
    "rejected_already_in_library": "Every candidate was already in your library.",
    "rejected_not_upcoming": "No candidate had a verified premiere date after today inside the job's window.",
}

#: What a candidate falls on when it is already settled by the user's own
#: history (exclusion_engine), as opposed to the job's settings (filter_engine).
SETTLED_OUTCOMES = frozenset({
    "rejected_already_requested", "rejected_already_watched", "rejected_existing_library",
    "rejected_already_recommended", "rejected_dismissed", "rejected_blacklisted",
})

OUTCOME_LABELS = {
    "rejected_already_requested": "already in Requests",
    "rejected_already_watched": "already watched",
    "rejected_existing_library": "already in the library",
    "rejected_already_recommended": "recommended recently",
    "rejected_dismissed": "dismissed before",
    "rejected_blacklisted": "blocked",
    "rejected_year": "outside the year window",
    "rejected_genre": "outside the job's genres",
    "rejected_rating": "below the minimum rating",
    "rejected_vote_count": "too few votes",
    "rejected_media_type": "another media type",
    "rejected_language": "another language",
    "rejected_country": "another country",
    "rejected_runtime": "outside the runtime window",
    "rejected_release_date": "outside the release-date window",
    "rejected_not_upcoming": "no verified premiere after today",
}

#: Messages that describe a job working as designed, not something to fix: the
#: job's share of Requests is full, or a run found nothing it had not already
#: settled. They are kept on the run as notices; a run with only notices has
#: completed, and Runtime logs lists them as "okey" rows. Gilbert, 2026-09-25:
#: a full share is information, not a warning.
NOTICE_CODES = frozenset({"queue_full", "no_new_picks", "source_cached", "results_filled", "results_short"})
#: Notices that are simply how a run went, reported as the run's summary line
#: rather than as a chip beside it. Gilbert, 2026-09-27: "den alltid ger varning
#: vid varje job run" - 3 of his 4 jobs showed a queue_full or no_new_picks chip
#: on every one of their runs for a day. The same words now sit in the run's
#: summary (the run history's text, Runtime logs' run_completed row, the toast),
#: so a run that went as designed looks like one. Old runs read the same way
#: (normalize_run).
SUMMARY_CODES = frozenset({"queue_full", "no_new_picks", "source_cached", "results_filled", "results_short"})


def split_notices(messages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(warnings, notices) of a run's messages, in their original order."""
    warnings = [row for row in messages if row.get("code") not in NOTICE_CODES]
    notices = [row for row in messages if row.get("code") in NOTICE_CODES]
    return warnings, notices


def split_summary(notices: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(notices, summary): the lines that are the run's own summary, taken out of its notices."""
    summary = [row for row in notices if row.get("code") in SUMMARY_CODES]
    return [row for row in notices if row.get("code") not in SUMMARY_CODES], summary


def normalize_run(run: Dict[str, Any]) -> Dict[str, Any]:
    """A stored run as the pages read it: summary lines out of notices and warnings.

    Runs before 2026-09-27 kept queue_full / no_new_picks among their notices (and
    before 2026-09-25 among their warnings); read here, they show like a new run.
    Nothing is written back.
    """
    moved = [row for row in list(run.get("warnings") or []) + list(run.get("notices") or [])
             if row.get("code") in SUMMARY_CODES]
    if not moved:
        run.setdefault("summary", [])
        return run
    out = dict(run)
    out["warnings"] = [row for row in run.get("warnings") or [] if row.get("code") not in SUMMARY_CODES]
    out["notices"] = [row for row in run.get("notices") or [] if row.get("code") not in SUMMARY_CODES]
    out["summary"] = list(run.get("summary") or []) + [row for row in moved if row not in (run.get("summary") or [])]
    if out.get("status") == "completed_with_warnings" and not out["warnings"]:
        out["status"] = "completed"
    return out


def summary_text(run: Dict[str, Any]) -> str:
    """The run's summary lines as one sentence-by-sentence string."""
    return " ".join(str(row.get("detail") or "").strip() for row in normalize_run(run).get("summary") or []
                    if row.get("detail")).strip()


def _outcome_breakdown(counts: Dict[str, int]) -> str:
    return ", ".join(f"{hits} {OUTCOME_LABELS.get(outcome, outcome)}"
                     for outcome, hits in sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def empty_result_warnings(
    job: Dict[str, Any],
    result: Dict[str, Any],
    extra: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Explain a run that picked nothing, instead of reporting a silent zero.

    A job can return 0 for entirely mundane reasons, but it can also be stuck —
    a page cursor past the end of its TMDb query, or a filter no candidate can
    ever satisfy. Both used to look identical from the outside.

    Only the second is a warning (`no_picks`): nothing got past the job's own
    settings. When candidates did meet them but every one was already settled
    (watched, waiting in Requests, ...) or had no concrete link to what the user
    liked (pipeline.TASTE_FLOOR), the job is saturated, not broken, and says so
    as a notice (`no_new_picks`). Gilbert's Tv run of 2026-09-25 16:34 UTC was
    reported as "413 fell on rejected_year. No candidate fell inside the job's
    year window" while 436 candidates were already in Requests and 18 met every
    setting but fell below the taste floor.
    """
    if result.get("accepted"):
        return []
    rows: List[Dict[str, Any]] = []
    sources = set(job.get("candidate_sources") or [])
    if sources & {"tmdb_discover", "tmdb_similar", "tmdb_recommendations"} and not extra:
        rows.append({
            "code": "tmdb_no_candidates",
            "source": "tmdb",
            "detail": "TMDb returned no candidates for this job's filters.",
        })

    counts: Dict[str, int] = {}
    for row in result.get("rejected") or []:
        outcome = row.get("filter_outcome") or "rejected"
        counts[outcome] = counts.get(outcome, 0) + 1
    # Candidates that met every setting and were settled by nothing: scored,
    # and then held back by the taste floor (or the job's lanes).
    passed = len(result.get("ranked") or [])
    total = sum(counts.values()) + passed
    filtered = {outcome: hits for outcome, hits in counts.items() if outcome not in SETTLED_OUTCOMES}
    settled = {outcome: hits for outcome, hits in counts.items() if outcome in SETTLED_OUTCOMES}
    if filtered and sum(filtered.values()) == total:
        outcome, hits = max(filtered.items(), key=lambda item: item[1])
        # A hint says "no candidate ...", so it is only given when it is true.
        hint = (REJECTION_HINTS.get(outcome, "") if hits == total else
                "No candidate got past the job's settings. Widen them, or add a source that reaches those titles.")
        rows.append({
            "code": "no_picks",
            "source": "filters",
            "detail": f"0 of {total} candidates accepted: {_outcome_breakdown(filtered)}. {hint}".strip(),
        })
    elif total:
        parts = []
        if settled:
            parts.append(_outcome_breakdown(settled))
        if filtered:
            parts.append(_outcome_breakdown(filtered))
        if passed:
            below = int(result.get("below_taste_floor") or 0)
            if result.get("taste_floor") is not None and below >= passed:
                parts.append(f"{passed} met every setting but none had a close enough link to what you liked "
                             "(taste floor)")
            elif below:
                parts.append(f"{passed} met every setting; {below} fell below the taste floor and the rest did "
                             "not fit the job's lanes")
            else:
                parts.append(f"{passed} met every setting but did not fit the job's lanes")
        rows.append({
            "code": "no_new_picks",
            "source": "job",
            "detail": f"Nothing new this run: 0 of {total} candidates picked; {'; '.join(parts)}.",
        })
    elif not rows:
        rows.append({
            "code": "no_candidates",
            "source": "job",
            "detail": "No candidate source returned anything for this job.",
        })
    return rows


def result_summary(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """How a saved job's results were made up, as summary lines (SUMMARY_CODES).

    `results_filled`: some results are the closest titles below the taste floor
    (Gilbert, 2026-09-29: fill up rather than return fewer). `results_short`:
    even after every widening step fewer titles passed the job's own settings
    and exclusions than its minimum of results - only the settings can change that.
    """
    counts = result.get("results") or {}
    rows: List[Dict[str, Any]] = []
    if counts.get("weak"):
        rows.append({
            "code": "results_filled",
            "source": "job",
            "detail": (f"{counts['matches']} of the {counts['results']} results had a close enough link to what "
                       f"you liked; the other {counts['weak']} are the closest titles below the taste floor "
                       "(weaker matches)."),
        })
    if counts.get("minimum") and counts.get("results", 0) < counts["minimum"]:
        rows.append({
            "code": "results_short",
            "source": "job",
            "detail": (f"Only {counts.get('results', 0)} titles passed this job's settings and exclusions after "
                       f"every widening step, fewer than its minimum of {counts['minimum']}. Widen the job's "
                       "years, genres or rating for more."),
        })
    return rows


def excluded_taste_source_warnings(job: Dict[str, Any], inputs: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Say so when a job leaves out a provider that holds the user's ratings.

    Taste sources are the job's own choice and are honoured row by row. But a
    job that leaves out Trakt leaves out almost every personal rating: measured
    2026-09-24, with Trakt switched off the taste floor kept 69 % of Gilbert's
    approved Requests instead of 93 %.
    """
    chosen = job.get("taste_sources")
    if chosen is None:
        return []
    allowed = set(chosen)
    rows: List[Dict[str, Any]] = []
    for name in sorted(HISTORY_PROVIDERS - allowed):
        rated = sum(1 for row in inputs.get("personal_history") or []
                    if row.get("provider") == name and row.get("rating") is not None)
        watched = sum(1 for row in inputs.get("history") or [] if (row.get("source") or row.get("provider")) == name)
        # Only a provider that carries real taste evidence is worth a warning on
        # every run: AniList's two ratings are not, Trakt's 169 are.
        if rated >= 10:
            rows.append({
                "code": "taste_source_excluded",
                "source": name,
                "detail": f"{name} holds {rated} ratings and {watched} watch rows; this job's taste sources leave it out.",
            })
    return rows


async def gather_job_candidates(
    user_id: str,
    job: Dict[str, Any],
    trigger: str,
    warnings: List[Dict[str, Any]],
    report: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Any], List[Dict[str, Any]]]:
    """Everything a run does before the pipeline: inputs, taste profile, candidates.

    Split out of execute_job so evaluation/job_trace.py can walk exactly the
    same code and report where candidates are lost. Previews never move the
    discover page cursor.
    """
    inputs = await load_pipeline_inputs(user_id)
    warnings.extend(await required_history_warnings(user_id, job))
    # Keywords, cast, creators and language for the titles behind the
    # profile. Without them the profile is genre names and nothing else, and
    # every title sharing one label scores identically.
    from providers.keys import resolve_tmdb_api_key
    from providers.tmdb_enrich import enrich_rows

    connection = await db.connections.find_one({"user_id": user_id}, {"_id": 0}) or {}
    tmdb_key = resolve_tmdb_api_key(connection)
    live_report: Dict[str, Any] = {}
    if job.get("live_overlay", True):
        # Trakt's own ratings and watched sets, and the AniList list on its real
        # scale, read (cached) for this run and never written back. The synced
        # copy was missing 74 of 176 Trakt ratings and 174 watched titles.
        from providers.live_history import anilist_live, overlay_rows, trakt_live

        extra_history, extra_personal, live_report = overlay_rows(
            inputs.get("history") or [], inputs.get("personal_history") or [],
            await trakt_live(user_id, connection), await anilist_live(connection),
        )
        inputs["history"] = list(inputs.get("history") or []) + extra_history
        inputs["personal_history"] = list(inputs.get("personal_history") or []) + extra_personal
    # Approved and rejected Requests are taste evidence too (taste_engine.
    # decision_docs); they need the same keywords and people as history rows.
    decided = [row for row in inputs.get("requested") or [] if row.get("status") in {"approved", "rejected"}]
    if tmdb_key:
        await enrich_rows(inputs.get("history") or [], tmdb_key)
        await enrich_rows(inputs.get("personal_history") or [], tmdb_key)
        await enrich_rows(decided, tmdb_key)
    # Build the profile before generating candidates: the similarity and
    # discover lanes are seeded from the titles the user actually rated.
    from recommendation.taste_engine import build_taste_snapshot

    taste = build_taste_snapshot(
        inputs["history"],
        feedback=inputs.get("feedback"),
        provider_weights=job.get("provider_weights"),
        taste_sources=job.get("taste_sources"),
        personal_history=inputs.get("personal_history"),
        requests=decided,
    )
    taste["live_overlay"] = live_report
    warnings.extend(excluded_taste_source_warnings(job, inputs))
    extra: List[Dict[str, Any]] = []
    sources = set(job.get("candidate_sources") or [])
    required = _required_sources(job)
    tmdb_wanted = sources & {"tmdb_discover", "tmdb_similar", "tmdb_recommendations"}
    if tmdb_wanted:
        from providers.tmdb import fetch_job_candidates

        if not tmdb_key:
            warnings.append({"code": "tmdb_not_configured", "source": "tmdb"})
            if tmdb_wanted & required:
                raise ValueError("Required TMDb source is not configured")
        else:
            # Walk the discover pages forward every run. Staying on page 1
            # meant the same titles came back for ever, all of them already
            # requested, so the job accepted nothing.
            from providers.tmdb import TMDB_CURSOR_PAGES, discover_page_span

            span = discover_page_span(job)
            start_page = max(1, int(job.get("tmdb_page_cursor") or 1))
            if start_page > TMDB_CURSOR_PAGES:
                start_page = ((start_page - 1) % TMDB_CURSOR_PAGES) + 1
            try:
                extra = await fetch_job_candidates(
                    job, inputs["history"], api_key=tmdb_key, start_page=start_page, taste=taste
                )
                if trigger != "preview":
                    next_page = start_page + span
                    if next_page > TMDB_CURSOR_PAGES:
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
        linked, linked_warnings = await fetch_linked_provider_candidates(
            user_id, sources, required, job=job
        )
        extra.extend(linked)
        warnings.extend(linked_warnings)
    from providers.premieres import is_upcoming_job, verify_premieres

    if is_upcoming_job(job):
        # The coming seasons, sequels and spin-offs of titles the user likes lead
        # an upcoming job's pool (jobs.upcoming, providers.continuations).
        from jobs.upcoming import add_continuations

        extra = await add_continuations(job, taste, inputs, extra, tmdb_key, report)
    if tmdb_key and extra:
        await enrich_rows(extra, tmdb_key)

    if is_upcoming_job(job) and extra:
        # Only a verified premiere after today passes an upcoming job's filter
        # (filter_engine "rejected_not_upcoming"); rows of a media type the job
        # does not take are not worth a TMDb call.
        from recommendation.filter_engine import media_type_allowed

        wanted = [row for row in extra if media_type_allowed(row, job.get("media_types"))]
        verified = await verify_premieres(wanted, tmdb_key)
        logging.info("job %s: %s of %s candidates have a verified premiere", job.get("id"), verified, len(wanted))
        from jobs.upcoming import link_verified

        extra = await link_verified(taste, extra, report)
    return inputs, taste, extra


async def execute_job(
    user_id: str,
    job: Dict[str, Any],
    trigger: str,
    catalog: Optional[List[Dict[str, Any]]] = None,
    model_override: Optional[str] = None,
) -> Dict[str, Any]:
    owner = await acquire_job_lock(job["id"])
    if not owner:
        return {"status": "locked", "detail": "Job is already running"}
    job = with_result_rules(with_job_intent(clamp_limits(job)))
    started = _now()
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    warnings: List[Dict[str, Any]] = []
    search: Dict[str, Any] = {}
    try:
        inputs, taste, extra = await gather_job_candidates(user_id, job, trigger, warnings, report=search)
        result = run_pipeline(job, catalog=catalog or [], extra_candidates=extra, taste=taste, **inputs)
        from providers.premieres import is_upcoming_job
        from jobs.upcoming import search_more

        # Too few results is not the end of a run: an upcoming job, and every
        # saved job short of its minimum or its limit, widens its search step by
        # step (jobs.upcoming.broaden) before titles below the floor fill up.
        extra, result = await search_more(
            user_id, job, taste, inputs, extra, result,
            lambda rows: run_pipeline(job, catalog=catalog or [], extra_candidates=rows, taste=taste, **inputs),
            report=search,
        )
        ranked = result.get("ranked") or result["accepted"]
        provider = "pipeline"
        model = "deterministic"
        ai_reranked = False
        if job.get("ai_enabled") and ranked:
            spec = result.get("job") or job
            ordered, provider, model = await rerank_verified_candidates(
                user_id, result["taste"], ranked, model_override=model_override, job=spec, keep=rerank_keep(spec),
            )
            if ordered:
                ranked = apply_model_order(ranked, ordered, spec)
                ai_reranked = provider == "ollama"
                result["ranked"] = ranked
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
        if ai_reranked:
            from recommendation.pipeline import result_counts, select_final

            result["accepted"] = select_final(ranked, result.get("job") or job)
            result["results"] = result_counts(result["accepted"], result.get("job") or job)
        warnings.extend(empty_result_warnings(job, result, extra))
        warnings.extend(result_summary(result))
        result["ai_reranked"] = ai_reranked
        requests_outcome: Dict[str, Any] = {}
        action_warnings = await persist_run_results(
            user_id,
            job,
            result["accepted"],
            trigger,
            provider="ollama" if ai_reranked else "pipeline",
            model=model if ai_reranked else "deterministic",
            ai_reranked=ai_reranked,
            outcome=requests_outcome,
        )
        if action_warnings:
            warnings.extend(action_warnings)
        warnings, notices = split_notices(warnings)
        notices, summary = split_summary(notices)
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
            "provider": provider,
            "model": model,
            "warnings": warnings,
            "notices": notices,
            # How the run went, in words: nothing new, a full share of Requests, a
            # provider's last good list standing in (SUMMARY_CODES). Never a warning.
            "summary": summary,
            # What reached Requests / MediaManager: new, refreshed, held back for room.
            "requests": requests_outcome or None,
            # An upcoming job's search: continuations found and what each widening step added.
            "upcoming_search": (search or None) if is_upcoming_job(job) else None,
            # Every saved job's search: what each widening step added (jobs.upcoming.broaden).
            "search": search or None,
            # What the results are: matches over the taste floor, weaker fill-ups,
            # new ones and ones still waiting in Requests (pipeline.result_counts).
            "result_counts": result.get("results"),
            # One line per result, previews too: a run of 1,500 full rows (scores,
            # cast, keywords, reasons) was megabytes in one job_runs document.
            # The preview's own answer below still carries the full rows.
            "results": [
                {"id": row.get("title"), "title": row.get("title"), "year": row.get("year"),
                 "type": row.get("type"), "media_type": row.get("media_type"),
                 "format": row.get("format") or row.get("anime_format"),
                 "match_score": row.get("match_score"), "weak_match": bool(row.get("weak_match")),
                 "open_result": row.get("open_result")}
                for row in result["accepted"]
            ],
            # What the job asked for when it ran. Every run overwrites the job's
            # updated_at, so 2024 titles queued by a 2026-2029 job could not be
            # traced back to the settings of the day (HANDOFF.md §31).
            "settings": {
                "media_types": job.get("media_types"),
                "taste_sources": job.get("taste_sources"),
                "final_recommendation_limit": job.get("final_recommendation_limit"),
                "filters": {key: value for key, value in (job.get("filters") or {}).items()
                            if value not in (None, "", [], {})},
            },
        }
        await db.job_runs.insert_one(dict(run))
        if trigger != "preview":
            await _advance_schedule(user_id, job, finished)
            if is_upcoming_job(job):
                # Up Coming and the Requests order read a premiere on queue rows.
                try:
                    from jobs.upcoming import refresh_request_premieres
                    from providers.keys import resolve_tmdb_api_key

                    conn = await db.connections.find_one({"user_id": user_id}, {"_id": 0}) or {}
                    await refresh_request_premieres(user_id, resolve_tmdb_api_key(conn))
                except Exception as exc:
                    logging.warning("request premieres not refreshed: %s", safe_provider_error(exc))
        run.pop("_id", None)
        payload = {
            "status": "ok" if not warnings else "completed_with_warnings",
            "run": {k: v for k, v in run.items() if k != "_id"},
            "accepted": result["accepted"],
            "warnings": warnings,
            "notices": notices,
            "summary": summary,
            "requests": requests_outcome or None,
            "result_counts": result.get("results"),
        }
        if trigger == "preview":
            # The page shows the first excluded titles; a large job's whole pool
            # (8,000+ scored rows) made the answer tens of megabytes.
            payload["rejected"] = (result.get("rejected") or [])[:PREVIEW_ROWS]
            payload["ranked"] = (result.get("ranked") or result["accepted"])[:PREVIEW_ROWS]
            payload["upcoming_search"] = search or None
        return payload
    except Exception as exc:
        detail = safe_provider_error(exc)
        logging.warning("Job %s failed: %s", job.get("id"), detail)
        finished = _now()
        warnings, notices = split_notices(warnings)
        notices, summary = split_summary(notices)
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
            "notices": notices,
            "summary": summary,
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
    runs = await db.job_runs.find(query, {"_id": 0}).sort("started_at", -1).to_list(100)
    return [normalize_run(run) for run in runs]


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
    job: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Pull live recommendation feeds only when the user already has tokens."""
    from recommendation.job_intent import job_intent, wants_lane

    required = required or set()
    conn = await db.connections.find_one({"user_id": user_id}, {"_id": 0}) or {}
    extra: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    intent = job_intent(job)
    media = set((job or {}).get("media_types") or ["movie", "tv", "anime"])
    wants_anime = intent is None or wants_lane(intent, "anime") or wants_lane(intent, "donghua")

    async def _required_or_warn(source: str, code: str, detail: str = "") -> None:
        row = {"code": code, "source": source}
        if detail:
            row["detail"] = detail
        if source in required:
            warnings.append(row)
            raise ValueError(f"Required source {source} failed: {code}")
        logging.warning("optional source %s: %s %s", source, code, detail)

    from providers.last_good import recall, remember, stand_in_note

    async def _stand_in(source: str, key: str, feed: str, status: Any) -> bool:
        """The feed's last good rows in place of a failed or empty answer (providers.last_good)."""
        stored = await recall(key)
        if not stored:
            return False
        rows, stored_at = stored
        extra.extend(rows or [])
        warnings.append(stand_in_note(source, feed, status, stored_at))
        logging.warning("%s %s -> %s; using its rows from %s", source, feed, status, stored_at)
        return True

    simkl_key = "simkl:%s" % user_id
    anilist_key = "anilist:%s:%s" % (user_id, (job or {}).get("id") or "job")

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
                    from providers.last_good import is_outage
                    from providers.tmdb import candidate_budget

                    kinds = [kind for kind, wanted in (
                        ("movies", bool(media & {"movie", "movies"})),
                        ("shows", bool(media & {"tv", "show", "anime"})),
                    ) if wanted]
                    # A job serving one lane needs a deeper feed: most of Trakt's
                    # first 20 for this viewer are anime. A large job takes all
                    # Trakt gives (100 per kind).
                    limit = 50 if intent else 20
                    if candidate_budget(job or {}) >= TRAKT_FULL_FEED_BUDGET:
                        limit = 100

                    def _parsed(items: Any, kind: str) -> List[Dict[str, Any]]:
                        return [
                            row for row in (parse_recommendation_entry(item, kind) for item in items or [])
                            if row and row.get("title") and row["title"] != "Unknown"
                        ]

                    async with httpx.AsyncClient(timeout=10) as client:
                        for kind in kinds:
                            feed = f"/recommendations/{kind}"
                            stand_in_key = "trakt:%s:%s" % (conn.get("trakt_username") or user_id, kind)
                            try:
                                response = await client.get(
                                    f"{TRAKT_API}{feed}",
                                    headers=trakt_headers(client_id, token),
                                    params={"limit": limit},
                                )
                                status: Optional[int] = response.status_code
                            except httpx.HTTPError as exc:
                                response, status = None, None
                                logging.warning("Trakt %s did not answer: %s", feed, exc.__class__.__name__)
                            if response is not None and status == 200:
                                items = response.json() or []
                                extra.extend(_parsed(items, kind))
                                await remember(stand_in_key, items)
                            elif status == 401:
                                # Trakt refused the stored sign-in itself: Sources must offer
                                # Connect again rather than keep saying "Connected".
                                from providers.auth_state import note_auth_failure

                                await note_auth_failure(user_id, "trakt", "Trakt no longer accepts the saved sign-in (401)")
                                await _required_or_warn(
                                    "trakt", "trakt_signin_rejected",
                                    "Trakt no longer accepts the saved sign-in: connect Trakt again under Sources",
                                )
                            elif is_outage(status):
                                # Trakt itself is down (2026-09-27: 500 on every
                                # account call). Its last good list stands in; with
                                # none young enough the run goes on without Trakt -
                                # an outage is no reason to fail the whole job.
                                stored = await recall(stand_in_key)
                                if stored:
                                    items, stored_at = stored
                                    extra.extend(_parsed(items, kind))
                                    warnings.append(stand_in_note("trakt", feed, status, stored_at))
                                    logging.warning("Trakt %s -> %s; using its list from %s", feed, status, stored_at)
                                elif "trakt" in required:
                                    answer = "did not answer" if status is None else f"answered {status}"
                                    warnings.append({
                                        "code": "trakt_unavailable",
                                        "source": "trakt",
                                        "detail": f"Trakt {answer} for {feed} (Trakt is down; the sign-in is fine). "
                                                  "This run used the other sources.",
                                    })
                                    logging.warning("Trakt %s -> %s; no earlier list, running without it", feed, status)
                                else:
                                    logging.warning("optional source trakt: %s %s, no earlier list", feed, status)
                            else:
                                await _required_or_warn("trakt", "trakt_http_error", str(status))
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
                from providers.simkl import fetch_recommendations, simkl_token_client_id

                client_id = simkl_token_client_id(conn)
                history = await db.history.find(
                    {"user_id": user_id, "source": "simkl"},
                    {"_id": 0, "simkl_id": 1, "type": 1, "media_type": 1, "title": 1, "source": 1},
                ).sort("watched_at", -1).to_list(40)
                buckets = None
                if intent:
                    # Trending movies filled a TV job's Simkl share before a single
                    # series was asked for; ask only for what the job can use.
                    buckets = [bucket for bucket, wanted in (
                        ("movies", "movie" in media),
                        ("tv", "tv" in media and wants_lane(intent, "live_action")),
                        ("anime", wants_anime),
                    ) if wanted]
                rows = await fetch_recommendations(
                    client_id,
                    conn["simkl_access_token"],
                    history,
                    limit=40,
                    buckets=buckets,
                )
                if rows:
                    extra.extend(rows)
                    await remember(simkl_key, rows)
                elif not await _stand_in("simkl", simkl_key, "its recommendations", "no rows"):
                    await _required_or_warn("simkl", "simkl_empty_feed")
            except ValueError:
                raise
            except Exception as exc:
                detail = safe_provider_error(exc)
                code = "simkl_http_error" if "failed" in str(detail).lower() or str(detail).isdigit() else "simkl_failed"
                if not await _stand_in("simkl", simkl_key, "its recommendations", detail):
                    await _required_or_warn("simkl", code, detail)
    if "anilist" in sources and not wants_anime:
        # AniList lists only anime-style media. For a job that asks for no anime
        # or donghua every row it returns is outside the job, and it was 40 of
        # the English TV job's 520 raw candidates.
        logging.info("anilist skipped: job %s asks for no anime or donghua", (job or {}).get("id"))
    elif "anilist" in sources:
        if not conn.get("anilist_access_token"):
            await _required_or_warn("anilist", "anilist_not_connected")
        else:
            try:
                from providers.anilist import fetch_by_origin, fetch_recommendations, fetch_upcoming
                from providers.tmdb import _window_is_upcoming, animation_lane_languages

                history = await db.history.find(
                    {"user_id": user_id, "source": "anilist"},
                    {"_id": 0, "anilist_id": 1, "source": 1, "title": 1},
                ).sort("watched_at", -1).to_list(40)
                anilist_rows: List[Dict[str, Any]] = []
                anilist_rows.extend(await fetch_recommendations(conn["anilist_access_token"], history))
                filters = (job or {}).get("filters") or {}
                if "zh" in animation_lane_languages(filters.get("include_genres"), (job or {}).get("media_types")):
                    # The recommendation feeds follow the viewer's own, Japanese,
                    # list; donghua has to be asked for by origin.
                    anilist_rows.extend(await fetch_by_origin(
                        "CN",
                        conn["anilist_access_token"],
                        min_year=filters.get("min_year"),
                        max_year=filters.get("max_year"),
                    ))
                from providers.premieres import is_upcoming_job

                if _window_is_upcoming(filters) or is_upcoming_job(job):
                    # Recommendations are built from titles that already aired, so a
                    # job asking for future years got nothing it could ever accept.
                    from providers.anilist import upcoming_depth

                    limit, pages = upcoming_depth(job or {})
                    anilist_rows.extend(await fetch_upcoming(
                        conn["anilist_access_token"],
                        min_year=filters.get("min_year"),
                        max_year=filters.get("max_year"),
                        limit=limit,
                        max_pages=pages,
                    ))
                if anilist_rows:
                    extra.extend(anilist_rows)
                    await remember(anilist_key, anilist_rows)
                else:
                    # AniList answers nothing at all when it is rate-limited or down.
                    await _stand_in("anilist", anilist_key, "its lists", "no rows")
            except ValueError:
                raise
            except Exception as exc:
                if not await _stand_in("anilist", anilist_key, "its lists", safe_provider_error(exc)):
                    await _required_or_warn("anilist", "anilist_failed", safe_provider_error(exc))
    return extra, warnings


async def restagger_jobs() -> None:
    """Give every user's interval jobs their own 2-minute slot.

    Jobs created before auto-staggering existed all sat on offset 0 and fired
    in one burst. Anything off the 2-minute grid is reassigned here, oldest job
    first, so the order is stable across restarts and already-correct jobs keep
    their next_run_at untouched - unless it lies further out than one interval,
    which is a slot left over from the old 30-minute grid.
    """
    now = _now()
    horizon = (now + timedelta(minutes=JOB_INTERVAL_MINUTES)).isoformat()
    user_ids = await db.jobs.distinct("user_id", {"schedule": INTERVAL_SCHEDULE})
    for user_id in user_ids:
        jobs = await db.jobs.find(
            {"user_id": user_id, "schedule": INTERVAL_SCHEDULE},
            {"_id": 0, "id": 1, "created_at": 1, "schedule_offset_minutes": 1, "next_run_at": 1},
        ).to_list(500)
        jobs.sort(key=lambda job: (job.get("created_at") or "", job.get("id") or ""))
        for index, job in enumerate(jobs):
            offset = stagger_offset(index)
            planned = job.get("next_run_at")
            if int(job.get("schedule_offset_minutes") or 0) == offset and planned and str(planned) <= horizon:
                continue
            await db.jobs.update_one(
                {"user_id": user_id, "id": job["id"]},
                {"$set": {
                    "schedule_offset_minutes": offset,
                    "next_run_at": next_run_at(INTERVAL_SCHEDULE, now=now, offset_minutes=offset),
                    "updated_at": now.isoformat(),
                }},
            )


async def migrate_jobs_to_interval() -> None:
    """Existing jobs keep running, but on a 15-minute clock, 2 minutes apart."""
    await db.jobs.update_many(
        {"schedule": {"$nin": [INTERVAL_SCHEDULE]}},
        {"$set": {"schedule": INTERVAL_SCHEDULE, "enabled": True}},
    )
    await restagger_jobs()


async def release_stale_locks() -> int:
    """Free the locks of runs that died with the previous process.

    A run holds its job's lock for up to JOB_LOCK_SECONDS (30 minutes since a
    large run takes minutes). A deploy or restart in the middle of a run left
    that lock behind, and the job skipped every tick until it ran out. One
    CineMind process runs the jobs, so at startup every held lock is such a
    leftover.
    """
    result = await db.job_locks.update_many(
        {"lock_owner": {"$ne": None}}, {"$set": {"lock_owner": None, "expires_at": None}},
    )
    return int(getattr(result, "modified_count", 0) or 0)


async def scheduler_loop() -> None:
    from api_extra import SEED_CATALOG

    await asyncio.sleep(3)
    try:
        freed = await release_stale_locks()
        if freed:
            logging.warning("Released %s job lock(s) left by runs of the previous process", freed)
    except Exception:
        logging.exception("Could not release stale job locks")
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
