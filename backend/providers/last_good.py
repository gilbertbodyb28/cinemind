"""A provider feed's last good answer, standing in while the provider is down.

On 2026-09-27 from 18:02 UTC Trakt answered 500 to every call about the signed-in
account - /recommendations ("handler.media.getRecommendationsLegacy"),
/sync/ratings, /sync/watched - while /users/settings and its public lists still
answered, so the sign-in itself was fine. Tv and Upcoming US name Trakt a
required source, and every one of their runs failed with "Required source trakt
failed: trakt_http_error".

A feed's last good answer is kept here, and while the provider is down (a 5xx,
429 or 408, a timeout, no connection) a run uses it and completes; it says so in
the run's summary. A refused sign-in (401) is never covered this way: that needs
the user, and still says so.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Tuple

#: How old a stand-in may be. A week of Trakt recommendations is still the
#: viewer's; older than that the run goes on without the feed instead.
FALLBACK_MAX_AGE = timedelta(days=7)
_PREFIX = "lastgood:"


def is_outage(status: Any) -> bool:
    """True for "the provider is down", as opposed to "the provider said no".

    `status` is the HTTP status, or None when there was no answer at all.
    """
    if status is None:
        return True
    try:
        code = int(status)
    except (TypeError, ValueError):
        return True
    return code >= 500 or code in (408, 429)


async def remember(key: str, payload: Any, database: Any = None) -> None:
    if database is None:
        from database import db as database
    now = datetime.now(timezone.utc).isoformat()
    await database.provider_cache.update_one(
        {"key": _PREFIX + key},
        {"$set": {"key": _PREFIX + key, "payload": payload, "stored_at": now, "updated_at": now}},
        upsert=True,
    )


async def recall(key: str, max_age: timedelta = FALLBACK_MAX_AGE,
                 database: Any = None) -> Optional[Tuple[Any, str]]:
    """(payload, stored_at) of the last good answer, or None when there is none young enough."""
    if database is None:
        from database import db as database
    doc = await database.provider_cache.find_one({"key": _PREFIX + key})
    if not doc or "payload" not in doc:
        return None
    stored = str(doc.get("stored_at") or "")
    try:
        stamp = datetime.fromisoformat(stored.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - stamp > max_age:
        return None
    return doc["payload"], stored


def stand_in_note(source: str, feed: str, status: Any, stored_at: str) -> dict:
    """The summary line a run gets when a stand-in was used."""
    answer = "did not answer" if status is None else "answered %s" % status
    return {
        "code": "source_cached",
        "source": source,
        "detail": "%s %s for %s; this run used its last good list, from %s UTC." % (
            source.title(), answer, feed, stored_at[:16].replace("T", " ")),
    }
