"""A pick stays on screen only while the title is still an open question.

A list of picks is a snapshot of one run, but the user keeps deciding after it.
On 2026-09-25 Home still showed the Content to Watch list generated 2026-09-22
17:13 UTC, and two of its eight picks had been settled since: Hunter x Hunter
(2011) - rated 10/10 on Trakt, imported by the sync - and Overlord IV, waiting
in Requests from a job. Nothing re-checked a stored pick, so a title the user
had watched, queued, approved or rejected somewhere else stayed on Home, and a
title rejected on Home stayed in Up Coming under another job's row.

`retire_settled` checks shown picks against the same records the pipeline
excludes by (watch history and personal ratings, the library, every request
except the pick's own, rejections on any list, the blacklist) and retires the
ones that are settled: `retired`, `retired_reason`, `retired_at` on the row, so
every list leaves them out and the reason can be read back. The decision itself
stays where it lives (history, requests, the dismissed row); the next run
replaces retired rows like any other.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from .exclusion_engine import identity_keys, stored_keys

IDENTITY_FIELDS = {
    "_id": 0, "id": 1, "title": 1, "year": 1, "type": 1, "media_type": 1, "format": 1, "anime_format": 1,
    "tmdb_id": 1, "tvdb_id": 1, "trakt_id": 1, "simkl_id": 1, "anilist_id": 1, "imdb_id": 1,
    "plex_rating_key": 1, "canonical_media_id": 1,
}
#: A delivery that failed is not a decision (jobs.engine.load_pipeline_inputs).
NOT_DECISIONS = ["request_failed", "failed"]


async def settled_context(user_id: str, database: Any) -> Dict[str, Any]:
    """Identity keys of everything that settles a title for this user."""
    history = await database.history.find({"user_id": user_id}, IDENTITY_FIELDS).to_list(None)
    rated = await database.media_history.find(
        {"user_id": user_id, "rating": {"$ne": None}}, IDENTITY_FIELDS).to_list(None)
    library = await database.media_library.find({"user_id": user_id}, IDENTITY_FIELDS).to_list(None)
    requests = await database.requests.find(
        {"user_id": user_id, "status": {"$nin": NOT_DECISIONS}},
        {**IDENTITY_FIELDS, "status": 1, "recommendation_id": 1},
    ).to_list(None)
    dismissed = await database.recommendations.find(
        {"user_id": user_id, "dismissed": True}, IDENTITY_FIELDS).to_list(None)
    blacklist = await database.blacklist.find({"user_id": user_id}, IDENTITY_FIELDS).to_list(None)
    requested: Dict[tuple, List[Tuple[str, Optional[str], Optional[str]]]] = {}
    for row in requests:
        for key in stored_keys(row):
            requested.setdefault(key, []).append((row.get("id"), row.get("recommendation_id"), row.get("status")))
    dismissed_keys: Dict[tuple, Set[str]] = {}
    for row in dismissed:
        for key in identity_keys(row):
            dismissed_keys.setdefault(key, set()).add(row.get("id"))
    return {
        "watched": {key for row in history + rated for key in stored_keys(row)},
        "library": {key for row in library for key in stored_keys(row)},
        "requested": requested,
        "dismissed": dismissed_keys,
        "blacklist": {key for row in blacklist for key in identity_keys(row)},
    }


def settled_reason(pick: Dict[str, Any], context: Dict[str, Any]) -> Optional[str]:
    """Why this pick is no longer an open question, or None while it still is."""
    keys = identity_keys(pick)
    if keys & context["blacklist"]:
        return "blacklisted"
    if any(context["dismissed"].get(key, set()) - {pick.get("id")} for key in keys):
        return "rejected_elsewhere"
    if keys & context["watched"]:
        return "already_watched"
    if keys & context["library"]:
        return "in_library"
    own = {pick.get("request_id")} - {None}
    statuses = {
        status
        for key in keys
        for request_id, recommendation_id, status in context["requested"].get(key, [])
        # The pick's own queue row (a require_approval job queued it) is not a
        # second decision about it.
        if request_id not in own and recommendation_id != pick.get("id")
    }
    if statuses & {"rejected", "dismissed"}:
        return "rejected_elsewhere"
    if statuses:
        return "already_requested"
    return None


async def retire_settled(user_id: str, picks: List[Dict[str, Any]], database: Any) -> List[Dict[str, Any]]:
    """The picks that are still open; the settled ones are marked retired on their rows."""
    open_picks = [pick for pick in picks if not pick.get("retired")]
    candidates = [pick for pick in open_picks if not pick.get("saved")]
    if not candidates:
        return open_picks
    context = await settled_context(user_id, database)
    now = datetime.now(timezone.utc).isoformat()
    kept = []
    for pick in open_picks:
        reason = None if pick.get("saved") else settled_reason(pick, context)
        if reason is None:
            kept.append(pick)
            continue
        await database.recommendations.update_one(
            {"user_id": user_id, "id": pick["id"]},
            {"$set": {"retired": True, "retired_reason": reason, "retired_at": now}},
        )
    return kept
