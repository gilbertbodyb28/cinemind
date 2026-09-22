"""Export one user's real recommendation inputs to a frozen JSON snapshot.

The snapshot is what the offline harness replays, so evaluation never touches a
live database and BEFORE/AFTER runs see byte-identical inputs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

COLLECTIONS = (
    "history",
    "media_history",
    "media_library",
    "recommendations",
    "requests",
    "blacklist",
    "recommendation_feedback",
)


def _clean(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{key: value for key, value in row.items() if key != "_id"} for row in rows]


async def export_snapshot(
    user_id: str,
    *,
    with_live_candidates: bool = True,
    job_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from database import db

    snapshot: Dict[str, Any] = {"user_id": user_id}
    for name in COLLECTIONS:
        snapshot[name] = _clean(await getattr(db, name).find({"user_id": user_id}).to_list(50000))
    snapshot["feedback"] = snapshot["recommendation_feedback"]

    canonical = {
        row.get("canonical_media_id")
        for name in ("history", "media_history", "recommendations")
        for row in snapshot[name]
        if row.get("canonical_media_id")
    }
    snapshot["media_identities"] = _clean(
        await db.media_identities.find({"canonical_id": {"$in": sorted(canonical)}}).to_list(50000)
    )

    if with_live_candidates:
        snapshot["candidates"] = await live_candidate_pool(user_id, job_override)
    return snapshot


def content_to_watch_job(user_id: str) -> Dict[str, Any]:
    """The exact job `/recommendations/generate` builds for Content to Watch."""
    from recommendation.pipeline import default_job

    job = default_job()
    job.update({
        "id": f"content_to_watch:{user_id}",
        "name": "Content to Watch",
        "job_type": "discover",
        "media_types": ["movie", "tv", "anime"],
        "taste_sources": None,
        "candidate_sources": [
            "tmdb_discover", "tmdb_similar", "tmdb_recommendations",
            "trakt", "simkl", "anilist",
        ],
        "candidate_limit": 120,
        "final_recommendation_limit": 8,
        "ai_enabled": True,
        "action_mode": "recommendations_only",
    })
    job["exclusions"] = {
        **job["exclusions"],
        "already_watched": True,
        "already_recommended": True,
        "recommend_again_after_days": 90,
    }
    return job


async def live_candidate_pool(user_id: str, job_override: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Call the production candidate sources once and freeze what they returned."""
    from database import db
    from jobs.engine import fetch_linked_provider_candidates
    from providers.keys import resolve_tmdb_api_key
    from providers.tmdb import fetch_job_candidates

    job = job_override or content_to_watch_job(user_id)
    history = await db.history.find({"user_id": user_id}, {"_id": 0, "user_id": 0}).to_list(10000)
    conn = await db.connections.find_one({"user_id": user_id}, {"_id": 0}) or {}
    rows: List[Dict[str, Any]] = []
    try:
        rows.extend(await fetch_job_candidates(
            job, history, api_key=resolve_tmdb_api_key(conn), start_page=1
        ))
    except Exception as exc:  # a frozen pool is still useful without one source
        print(f"tmdb candidate fetch failed: {exc.__class__.__name__}: {exc}")
    try:
        linked, _ = await fetch_linked_provider_candidates(
            user_id, set(job.get("candidate_sources") or []), set(), job=job
        )
        rows.extend(linked)
    except Exception as exc:
        print(f"linked candidate fetch failed: {exc.__class__.__name__}: {exc}")
    return rows


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Freeze one user's recommendation inputs")
    parser.add_argument("--user", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--no-live-candidates", action="store_true")
    args = parser.parse_args(argv)
    snapshot = asyncio.get_event_loop().run_until_complete(
        export_snapshot(args.user, with_live_candidates=not args.no_live_candidates)
    )
    args.output.write_text(json.dumps(snapshot, ensure_ascii=False, default=str), encoding="utf-8")
    counts = {key: len(value) for key, value in snapshot.items() if isinstance(value, list)}
    print(json.dumps(counts, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
