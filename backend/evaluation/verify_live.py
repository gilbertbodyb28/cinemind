"""Check the delivered features against the running CineMind and the real data.

One report for the points the delivery is judged on: Simkl/Plex (and the other
sources) connection tests, what each source has actually synced, what the
upcoming jobs would pick right now, the Content to Watch ranking, and whether
the Requests queue pages through every queued title exactly once.

Read-only unless --sync or --generate is given. It signs in by writing a
15-minute session for --user and deletes it again at the end. Upcoming jobs are
run as a preview, like evaluation.job_trace: no run, recommendation or request
is written and the discover cursor does not move.

    cd backend
    PYTHONPATH=../.runtime/python:. python3 -m evaluation.verify_live \
      --user <user_id> [--base-url http://localhost:8001] [--sync] [--generate] \
      [--output /tmp/verify.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

SOURCES = ("simkl", "plex", "trakt", "anilist", "ollama", "mediamanager")
QUEUE_PAGE = 200


def _check(name: str, status: str, evidence: Any) -> Dict[str, Any]:
    return {"check": name, "status": status, "evidence": evidence}


def _json(response: httpx.Response) -> Any:
    """The body, or the start of it when an error page is not JSON."""
    try:
        return response.json()
    except ValueError:
        return {"http_status": response.status_code, "body": response.text[:300]}


async def _counts(db, user_id: str) -> Dict[str, Any]:
    history = await db.history.aggregate([
        {"$match": {"user_id": user_id}}, {"$group": {"_id": "$source", "rows": {"$sum": 1}}},
    ]).to_list(None)
    personal = await db.media_history.aggregate([
        {"$match": {"user_id": user_id}}, {"$group": {"_id": "$provider", "rows": {"$sum": 1}}},
    ]).to_list(None)
    state = await db.provider_sync_state.find(
        {"account_id": user_id}, {"_id": 0, "lock_owner": 0},
    ).to_list(None)
    return {
        "history_by_source": {str(row["_id"]): row["rows"] for row in history},
        "media_history_by_provider": {str(row["_id"]): row["rows"] for row in personal},
        "last_sync": state,
    }


def _release_state(row: Dict[str, Any], today: str) -> str:
    raw = str(row.get("release_date") or row.get("first_air_date") or "")[:10]
    if len(raw) == 10 and raw[4] == "-":
        return "upcoming" if raw > today else "released"
    try:
        return "upcoming" if int(row.get("year")) > int(today[:4]) else "released"
    except (TypeError, ValueError):
        return "unknown"


def _in_window(row: Dict[str, Any], filters: Dict[str, Any]) -> bool:
    try:
        year = int(row.get("year"))
    except (TypeError, ValueError):
        return False
    low, high = filters.get("min_year"), filters.get("max_year")
    return (low in (None, "") or year >= int(low)) and (high in (None, "") or year <= int(high))


async def _upcoming(db, user_id: str) -> List[Dict[str, Any]]:
    from jobs.engine import gather_job_candidates
    from providers.tmdb import _window_is_upcoming
    from recommendation.pipeline import run_pipeline

    today = datetime.now(timezone.utc).date().isoformat()
    checks = []
    jobs = await db.jobs.find({"user_id": user_id}, {"_id": 0}).to_list(None)
    for job in jobs:
        filters = job.get("filters") or {}
        if not (_window_is_upcoming(filters) or "upcoming" in str(job.get("name") or "").lower()):
            continue
        warnings: List[Dict[str, Any]] = []
        inputs, taste, extra = await gather_job_candidates(user_id, job, "preview", warnings)
        result = run_pipeline(job, extra_candidates=list(extra), taste=taste, **inputs)
        picks = result.get("accepted") or []
        states = [_release_state(row, today) for row in picks]
        outside = [row.get("title") for row in picks if not _in_window(row, filters)]
        evidence = {
            "job": job.get("name"), "window": [filters.get("min_year"), filters.get("max_year")],
            "picks": len(picks), "slots": job.get("final_recommendation_limit"),
            "not_yet_released": states.count("upcoming"), "released": states.count("released"),
            "unknown_date": states.count("unknown"), "outside_year_window": outside,
            "warnings": [item.get("code") for item in warnings],
            "sample": [
                f"{row.get('title')} ({row.get('release_date') or row.get('year')}) {row.get('media_type')}"
                for row in picks[:10]
            ],
        }
        status = "PASS" if picks and not outside else "FAIL"
        checks.append(_check(f"upcoming: {job.get('name')}", status, evidence))
    if not checks:
        checks.append(_check("upcoming", "INFO", "no saved job asks for upcoming titles"))
    return checks


async def _queue(api: httpx.AsyncClient) -> List[Dict[str, Any]]:
    stats = _json(await api.get("/api/requests/stats"))
    ids: List[str] = []
    first: Dict[str, Any] = {}
    offset = 0
    stamps: List[str] = []
    while True:
        page = _json(await api.get("/api/requests/page", params={
            "view": "queue", "sort": "added_desc", "offset": offset, "limit": QUEUE_PAGE,
        }))
        first = first or page
        rows = page.get("items") or []
        ids.extend(row["id"] for row in rows)
        stamps.extend(str(row.get("updated_at") or row.get("created_at") or "") for row in rows)
        offset += len(rows)
        if not rows or offset >= page.get("total", 0):
            break
    approved = _json(await api.get("/api/requests/page", params={"view": "approved", "limit": 1}))
    ordered = all(stamps[index] >= stamps[index + 1] for index in range(len(stamps) - 1) if stamps[index + 1])
    evidence = {
        "stats": stats, "queue_total": first.get("total"), "pending_count": first.get("pending_count"),
        "paged_rows": len(ids), "distinct_rows": len(set(ids)), "newest_first": ordered,
        "approved_tab_total": approved.get("total"),
    }
    ok = (
        len(ids) == len(set(ids)) == first.get("total")
        and first.get("pending_count") == stats.get("pending")
        and approved.get("total") == stats.get("approved")
        and ordered
    )
    return [_check("queue: pages every queued title once", "PASS" if ok else "FAIL", evidence)]


async def verify(user_id: str, base_url: str, *, sync: bool, generate: bool) -> Dict[str, Any]:
    from database import db

    token = "verify_" + secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    await db.user_sessions.insert_one({
        "user_id": user_id, "session_token": token,
        "expires_at": now + timedelta(minutes=15), "created_at": now,
    })
    checks: List[Dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers={"Authorization": f"Bearer {token}"}, timeout=900,
        ) as api:
            for source in SOURCES:
                answer = _json(await api.post(f"/api/connections/test/{source}"))
                message = str(answer.get("message") or "")
                status = "PASS" if answer.get("ok") else ("SKIP" if message.startswith("No ") else "FAIL")
                checks.append(_check(f"connection: {source}", status, message))

            before = await _counts(db, user_id)
            checks.append(_check("sync: stored rows per source", "INFO", before))
            if sync:
                answer = _json(await api.post("/api/history/sync"))
                after = await _counts(db, user_id)
                status = "FAIL" if answer.get("errors") else "PASS"
                checks.append(_check("sync: POST /history/sync", status, {
                    "fetched": answer.get("sources"), "kept_stored": answer.get("kept"),
                    "errors": answer.get("errors"), "history_after": after["history_by_source"],
                }))

            checks.extend(await _upcoming(db, user_id))

            if generate:
                answer = await api.post("/api/recommendations/generate")
                checks.append(_check(
                    "ranking: POST /recommendations/generate",
                    "PASS" if answer.status_code == 200 else "FAIL", _json(answer),
                ))
            # Saved picks outlive their run and keep its rank, so only the live list counts.
            rows = [
                row for row in _json(await api.get("/api/recommendations"))
                if row.get("job_id") == f"content_to_watch:{user_id}" and not row.get("saved")
            ]
            ranks = [row.get("rank") for row in rows]
            evidence = {
                "rows": len(rows), "ranks": ranks,
                "models": sorted({str(row.get("model")) for row in rows}),
                "ai_reranked": sorted({bool(row.get("ai_reranked")) for row in rows}),
                "top": [
                    f"{row.get('rank')}. {row.get('title')} ({row.get('year')}) {row.get('media_type')} "
                    f"{row.get('match_score')}%" for row in rows[:8]
                ],
            }
            ok = bool(rows) and ranks == sorted(ranks) and len(set(ranks)) == len(ranks)
            checks.append(_check("ranking: Content to Watch best first", "PASS" if ok else "FAIL", evidence))

            checks.extend(await _queue(api))
    finally:
        await db.user_sessions.delete_one({"session_token": token})
    return {"user": user_id, "checked_at": now.isoformat(), "checks": checks}


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user", required=True)
    parser.add_argument("--base-url", default="http://localhost:8001")
    parser.add_argument("--sync", action="store_true", help="also press Sync (POST /history/sync)")
    parser.add_argument("--generate", action="store_true", help="also regenerate Content to Watch")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    report = asyncio.run(verify(args.user, args.base_url, sync=args.sync, generate=args.generate))
    for item in report["checks"]:
        print(f"[{item['status']:4}] {item['check']}")
        print("       " + json.dumps(item["evidence"], ensure_ascii=False, default=str)[:1200])
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False, default=str)


if __name__ == "__main__":
    main()
