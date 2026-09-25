"""A reversible clean-up proposal for the Requests queue (HANDOFF.md, omgång 5).

The queue only grows: every pick a job accepts becomes a pending row that stays
until a person acts, and nothing ever expires. On 2026-09-25 it held 10,129
pending rows. This tool proposes which of them to *archive* - never delete -
and can put every archived row back exactly as it was.

    cd backend
    PYTHONPATH=../.runtime/python:. python3 -m evaluation.queue_cleanup --user <id> plan
    PYTHONPATH=../.runtime/python:. python3 -m evaluation.queue_cleanup --user <id> apply --batch <batch>
    PYTHONPATH=../.runtime/python:. python3 -m evaluation.queue_cleanup --user <id> revert --batch <batch>

`plan` writes nothing to the database: it writes a manifest (one line per row:
id, rule, title) under .runtime/backups/queue-archive/. `apply` archives only
the rows of that manifest that are still untouched pending suggestions, and
`revert` restores them. Rows a person has touched - approved, rejected,
delivered, or anything without a source job - are never in scope.

Rules, first match wins (pick with --rules):

- duplicate_row: the same title twice (TMDb id in the same namespace, years at
  most one apart); the row suggested last and matched best is kept.
- duplicate_of_decided: a pending copy of a title already approved or rejected.
- already_watched: the title is in the synced watch history.
- fails_job_filters: the row no longer meets its job's current media type,
  year or genre settings (for example films in a job that is now TV-only).
- disabled_job: suggested by a job that is disabled or deleted.
- older_than_days: suggested last more than --days days ago (off by default).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
BACKUPS = os.path.normpath(os.path.join(HERE, "..", "..", ".runtime", "backups", "queue-archive"))
DEFAULT_RULES = ("duplicate_row", "duplicate_of_decided", "already_watched", "fails_job_filters")
ALL_RULES = DEFAULT_RULES + ("disabled_job", "older_than_days")
#: A person acted on the row: it is theirs, not a clean-up candidate.
HUMAN_FIELDS = ("approved_at", "rejected_at", "delivery_status", "delivery_error", "external_request_id")
ARCHIVE_FIELDS = ("archived_from_status", "archived_at", "archived_reason", "archive_batch",
                  # set by request_providers.settle_duplicates (batch "dup:<decided id>")
                  "duplicate_of")


def human_touched(row: Dict[str, Any]) -> bool:
    return any(row.get(field) not in (None, "") for field in HUMAN_FIELDS) or not row.get("source_job_id")


def _suggested_at(row: Dict[str, Any]) -> str:
    return str(row.get("last_suggested_at") or row.get("updated_at") or row.get("created_at") or "")


async def plan(user_id: str, rules: Sequence[str], days: int) -> Dict[str, Any]:
    from database import db
    from recommendation.exclusion_engine import identity_keys, stored_keys
    from recommendation.filter_engine import apply_filters
    from recommendation.media_identity import coerce_int, identity_scope
    from recommendation.taste_engine import is_planned

    rows = await db.requests.find({"user_id": user_id}, {"_id": 0, "poster": 0}).to_list(None)
    jobs = {job["id"]: job for job in await db.jobs.find({"user_id": user_id}, {"_id": 0}).to_list(None)}
    pending = [row for row in rows if row.get("status") == "pending_approval"]
    in_scope = [row for row in pending if not human_touched(row)]
    found: Dict[str, set] = {rule: set() for rule in ALL_RULES}

    groups: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
    for row in pending:
        if row.get("tmdb_id") is not None:
            groups[(coerce_int(row["tmdb_id"]), identity_scope(row))].append(row)
    for members in groups.values():
        if len(members) < 2:
            continue
        years = [coerce_int(row.get("year")) or 0 for row in members]
        if max(years) - min(years) > 1:
            continue  # a remake or a new season, not the same row twice
        members.sort(key=lambda row: (not human_touched(row), _suggested_at(row), row.get("match_score") or 0),
                     reverse=True)
        found["duplicate_row"].update(row["id"] for row in members[1:])

    decided = {}
    for row in rows:
        if row.get("status") in {"approved", "available", "completed", "rejected"} and row.get("tmdb_id") is not None:
            decided[(coerce_int(row["tmdb_id"]), identity_scope(row))] = row
    for row in pending:
        other = decided.get((coerce_int(row.get("tmdb_id")), identity_scope(row)))
        if other and abs((coerce_int(other.get("year")) or 0) - (coerce_int(row.get("year")) or 0)) <= 1:
            found["duplicate_of_decided"].add(row["id"])

    history = await db.history.find({"user_id": user_id}, {"_id": 0, "provider_specific_metadata": 0}).to_list(None)
    personal = await db.media_history.find({"user_id": user_id}, {"_id": 0}).to_list(None)
    watched = set()
    for item in history + personal:
        if not is_planned(item):
            watched |= stored_keys(item)
    for row in pending:
        if identity_keys(row) & watched or stored_keys(row) & watched:
            found["already_watched"].add(row["id"])

    # A queue row carries no premiere date, so for an "Only upcoming premieres"
    # job every waiting series read as "not upcoming" and the plan proposed to
    # archive titles premiering next month (271 of Upcoming Tv Shows' 534 rows,
    # 2026-09-25). Their premieres are verified first, read-only; a row the
    # provider did not answer for is not judged on it.
    premiered = await _verified_premieres(db, user_id, pending, jobs)
    for row in pending:
        job = jobs.get(row.get("source_job_id"))
        if not job:
            found["disabled_job"].add(row["id"])
            continue
        if not job.get("enabled", True):
            found["disabled_job"].add(row["id"])
        checked = premiered.get(row["id"])
        candidate = dict(checked if checked is not None else row)
        ok, reason = apply_filters(candidate, {**(job.get("filters") or {}), "media_types": job.get("media_types")})
        if reason == "rejected_not_upcoming" and (checked is None or checked.get("_premiere_unanswered")):
            continue
        # Queue rows store the TMDb score as `rating` only when the job passed it,
        # so a missing rating is not a failed rating floor.
        if not ok and not (reason == "rejected_rating" and row.get("rating") is None):
            found["fails_job_filters"].add(row["id"])

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    found["older_than_days"] = {row["id"] for row in pending if _suggested_at(row) and _suggested_at(row) < cutoff}

    scope_ids = {row["id"] for row in in_scope}
    assigned: Dict[str, str] = {}
    for rule in ALL_RULES:
        if rule not in rules:
            continue
        for row_id in sorted(found[rule] & scope_ids):
            assigned.setdefault(row_id, rule)
    by_id = {row["id"]: row for row in pending}
    flipped = [row for row in pending if row.get("approved_at") and not row.get("delivery_status")]
    return {
        "user_id": user_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rules": list(rules),
        "days": days,
        "pending": len(pending),
        "human_touched_pending": len(pending) - len(in_scope),
        "standalone_counts": {rule: len(found[rule] & scope_ids) for rule in ALL_RULES},
        "archive_counts": dict(Counter(assigned.values()).most_common()),
        "archive_by_job": dict(Counter(by_id[row_id].get("source_job_id") for row_id in assigned).most_common()),
        "pending_after": len(pending) - len(assigned),
        "approvals_pushed_back_to_pending": len(flipped),
        "rows": [{"id": row_id, "rule": rule, "title": by_id[row_id].get("title"), "year": by_id[row_id].get("year"),
                  "type": by_id[row_id].get("type"), "job": by_id[row_id].get("source_job_id")}
                 for row_id, rule in sorted(assigned.items(), key=lambda item: (item[1], item[0]))],
        "approvals_to_restore": [{"id": row["id"], "title": row.get("title"), "approved_at": row.get("approved_at")}
                                 for row in flipped],
    }


async def _verified_premieres(db, user_id: str, pending: Sequence[Dict[str, Any]],
                              jobs: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Copies of the waiting rows of upcoming-only jobs with their premiere verified.

    Nothing is written, not even provider_cache. A copy is marked
    `_premiere_unanswered` when no provider answered for it.
    """
    from providers.keys import resolve_tmdb_api_key
    from providers.premieres import is_upcoming_job, verify_premieres

    copies = [dict(row) for row in pending if is_upcoming_job(jobs.get(row.get("source_job_id")))]
    if not copies:
        return {}
    conn = await db.connections.find_one({"user_id": user_id}, {"_id": 0}) or {}
    unanswered: List[Dict[str, Any]] = []
    await verify_premieres(copies, resolve_tmdb_api_key(conn), store=False, unanswered=unanswered)
    for row in unanswered:
        row["_premiere_unanswered"] = True
    return {row["id"]: row for row in copies}


def _manifest_path(batch: str) -> str:
    return os.path.join(BACKUPS, "%s.json" % batch)


async def apply(user_id: str, batch: str) -> Dict[str, Any]:
    from pymongo import UpdateOne

    from database import db

    with open(_manifest_path(batch), encoding="utf-8") as handle:
        manifest = json.load(handle)
    if manifest.get("user_id") != user_id:
        raise SystemExit("manifest %s belongs to %s" % (batch, manifest.get("user_id")))
    now = datetime.now(timezone.utc).isoformat()
    guard = {"status": "pending_approval", **{field: {"$exists": False} for field in HUMAN_FIELDS}}
    writes = [UpdateOne(
        {"user_id": user_id, "id": row["id"], **guard},
        {"$set": {"status": "archived", "archived_from_status": "pending_approval", "archived_at": now,
                  "archived_reason": row["rule"], "archive_batch": batch}},
    ) for row in manifest["rows"]]
    result = await db.requests.bulk_write(writes, ordered=False) if writes else None
    return {"batch": batch, "planned": len(writes), "archived": result.modified_count if result else 0}


async def revert(user_id: str, batch: str) -> Dict[str, Any]:
    from database import db

    restored = 0
    async for row in db.requests.find({"user_id": user_id, "archive_batch": batch, "status": "archived"},
                                      {"_id": 1, "archived_from_status": 1}):
        await db.requests.update_one(
            {"_id": row["_id"]},
            {"$set": {"status": row.get("archived_from_status") or "pending_approval"},
             "$unset": {field: "" for field in ARCHIVE_FIELDS}},
        )
        restored += 1
    return {"batch": batch, "restored": restored}


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user", required=True)
    parser.add_argument("action", choices=("plan", "apply", "revert"))
    parser.add_argument("--batch", help="apply / revert: the batch a plan wrote")
    parser.add_argument("--rules", help="comma list; default " + ",".join(DEFAULT_RULES))
    parser.add_argument("--days", type=int, default=30, help="older_than_days: age in days")
    args = parser.parse_args(argv)

    async def run() -> Dict[str, Any]:
        if args.action == "plan":
            rules = [item for item in (args.rules or ",".join(DEFAULT_RULES)).split(",") if item]
            unknown = set(rules) - set(ALL_RULES)
            if unknown:
                raise SystemExit("unknown rules: %s" % ", ".join(sorted(unknown)))
            report = await plan(args.user, rules, args.days)
            batch = "arch_%s" % datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            os.makedirs(BACKUPS, exist_ok=True)
            with open(_manifest_path(batch), "w", encoding="utf-8") as handle:
                json.dump({**report, "batch": batch}, handle, indent=1, ensure_ascii=False)
            summary = {key: value for key, value in report.items() if key not in {"rows", "approvals_to_restore"}}
            return {**summary, "batch": batch, "manifest": _manifest_path(batch)}
        if not args.batch:
            raise SystemExit("--batch is required for %s" % args.action)
        return await (apply(args.user, args.batch) if args.action == "apply" else revert(args.user, args.batch))

    print(json.dumps(asyncio.run(run()), indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
