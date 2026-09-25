"""Pluggable request destinations. Local queue is the default; Seer is optional."""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
import logging
import uuid

import httpx

from config import REQUEST_PROVIDER_API_KEY, REQUEST_PROVIDER_URL, env_flag
from database import db


# Once a title is approved it stays approved. A later job that recommends the
# same title again must not push it back into the Requests queue.
DECIDED_STATUSES = {"approved", "available", "completed"}
#: Everything a person decided, and what an archive batch set aside. A job run
#: never changes these rows. Until 2026-09-25 only DECIDED_STATUSES were
#: protected: a job that suggested a rejected title again flipped it back to
#: pending, and 79 approvals had been pushed back to pending the same way.
FINAL_STATUSES = DECIDED_STATUSES | {"rejected", "dismissed", "archived"}
QUEUE_STATUSES = {"pending_approval", "pending", "requested", "request_failed"}
PENDING_STATUSES = {"pending_approval", "pending", "requested"}
AUTOMATED_STATUSES = {"pending_approval", "request_failed"}
#: Refreshed when a job suggests a title that is already waiting in the queue.
SUGGESTION_FIELDS = ("match_score", "recommendation_id")


def _scope(row: Dict[str, Any]) -> Optional[str]:
    """Film or series; None for an anime row stored without its format (either)."""
    from recommendation.media_identity import identity_scope

    kind = str(row.get("media_type") or row.get("type") or "").casefold()
    if not kind:
        return None  # nothing says which it is, so it can be either
    if kind == "anime" and not (row.get("format") or row.get("anime_format")):
        return None
    return identity_scope(row)


async def find_existing_request(user_id: str, item: Dict[str, Any], database: Any = None) -> Dict[str, Any]:
    """The queue row that already stands for this title, if any.

    Exact title + year was the only lookup, so a renamed or romanised title
    ("Class Crush Crisis" / "Class Crush") became a second row. The ids a row
    carries are tried next: canonical id, AniList id, then TMDb id in the same
    namespace (a film and a series can share a TMDb number). `database` lets a
    caller use its own handle (jobs.engine reads through its own).
    """
    requests = (database if database is not None else db).requests
    fields = {"_id": 0, "id": 1, "status": 1, "type": 1, "media_type": 1, "format": 1, "source_job_id": 1,
              "approved_at": 1, "rejected_at": 1, "poster": 1, "genres": 1, "release_date": 1}
    if item.get("id"):
        return await requests.find_one({"user_id": user_id, "id": item["id"]}, fields) or {}
    found = await _first_request(requests, user_id, item, fields)
    if found and found.get("status") not in FINAL_STATUSES:
        # The row found first may be a waiting duplicate of one the person
        # already decided: "Frieren" waited in the queue while "Frieren: Beyond
        # Journey's End" (same TMDb id) was rejected, so the lookup said
        # "pending" and the rejection did not stand. The decision wins.
        decided = await _first_request(requests, user_id, item, fields, statuses=DECISION_ORDER)
        if decided:
            return decided
    return found or {}


#: Which decided row speaks for a title when there are several: an approval,
#: then a rejection, then an archived (set-aside) row.
DECISION_ORDER = (sorted(DECIDED_STATUSES), ["rejected", "dismissed"], ["archived"])


async def _first_request(requests: Any, user_id: str, item: Dict[str, Any], fields: Dict[str, Any],
                         statuses: Optional[tuple] = None) -> Dict[str, Any]:
    """The first row that is this title: exact title + year, canonical id, AniList id, TMDb id in scope."""
    wanted = _scope(item)
    for wanted_statuses in statuses or (None,):
        status = {"status": {"$in": list(wanted_statuses)}} if wanted_statuses else {}
        # Same name and year is not enough: "Good Boy" (2026), a Thai series,
        # was matched to the queued film "Good Boy" (2026) and refreshed its row
        # (2026-09-25). A film and a series are two titles, as in the exclusions.
        async for row in requests.find(
                {"user_id": user_id, "title": item.get("title"), "year": item.get("year"), **status}, fields):
            scope = _scope(row)
            if wanted is None or scope is None or scope == wanted:
                return row
        for key in ("canonical_media_id", "anilist_id"):
            if item.get(key) not in (None, ""):
                found = await requests.find_one({"user_id": user_id, key: item[key], **status}, fields)
                if found:
                    return found
        if item.get("tmdb_id") not in (None, ""):
            async for row in requests.find({"user_id": user_id, "tmdb_id": item["tmdb_id"], **status}, fields):
                scope = _scope(row)
                if wanted is None or scope is None or scope == wanted:
                    return row
    return {}


#: Years apart that still make two queue rows one title (a premiere dated
#: December by one provider and January by the next).
DUPLICATE_YEAR_SLACK = 1


def _same_title(row: Dict[str, Any], other: Dict[str, Any]) -> bool:
    from recommendation.exclusion_engine import stored_keys
    from recommendation.media_identity import coerce_int

    if not stored_keys(row) & stored_keys(other):
        return False
    # A wrong TMDb match can give two different titles one id ("Night on the
    # Galactic Railroad" 1985 and "Kenichi" 2006 share 37585 in the queue):
    # far-apart years are two titles, whatever the id says.
    years = coerce_int(row.get("year")), coerce_int(other.get("year"))
    return None in years or abs(years[0] - years[1]) <= DUPLICATE_YEAR_SLACK


async def settle_duplicates(user_id: str, decided: Dict[str, Any], reason: str, database: Any = None) -> list:
    """Set aside the waiting copies of a title the person just decided.

    A decision is about the title, not the row: a rejected title kept waiting
    in the queue under a second row, and an approved one could be approved -
    and sent to MediaManager - twice. The copies are archived the way
    evaluation.queue_cleanup archives (status, previous status, reason, batch
    "dup:<decided id>"), so `queue_cleanup revert --batch dup:<id>` puts them back.
    """
    try:
        return await _settle_duplicates(user_id, decided, reason, database)
    except Exception as exc:  # the decision itself already stands; the copies wait for the next one
        logging.warning("Could not set aside queue copies of %s: %s", decided.get("title"), exc)
        return []


async def _settle_duplicates(user_id: str, decided: Dict[str, Any], reason: str, database: Any) -> list:
    requests = (database if database is not None else db).requests
    if not decided or not decided.get("id"):
        return []
    import re

    probes = [{"tmdb_id": decided["tmdb_id"]}] if decided.get("tmdb_id") not in (None, "") else []
    for key in ("canonical_media_id", "anilist_id"):
        if decided.get(key) not in (None, ""):
            probes.append({key: decided[key]})
    if decided.get("title"):
        probes.append({"title": {"$regex": f"^{re.escape(str(decided['title']))}$", "$options": "i"}})
    if not probes:
        return []
    fields = {"_id": 0, "id": 1, "title": 1, "year": 1, "type": 1, "media_type": 1, "format": 1, "status": 1,
              "tmdb_id": 1, "anilist_id": 1, "canonical_media_id": 1}
    waiting = await requests.find(
        {"user_id": user_id, "status": {"$in": sorted(PENDING_STATUSES)}, "id": {"$ne": decided["id"]}, "$or": probes},
        fields,
    ).to_list(None)
    now = datetime.now(timezone.utc).isoformat()
    settled = []
    for row in waiting:
        if not _same_title(row, decided):
            continue
        await requests.update_one(
            {"user_id": user_id, "id": row["id"], "status": row.get("status")},
            # updated_at is left alone (it is the queue's "added" order), so a
            # revert puts the row back exactly where it was.
            {"$set": {"status": "archived", "archived_from_status": row.get("status"), "archived_at": now,
                      "archived_reason": reason, "archive_batch": f"dup:{decided['id']}",
                      "duplicate_of": decided["id"]}},
        )
        settled.append(row["id"])
    return settled


def is_user_rejection(existing: Dict[str, Any]) -> bool:
    """The person said no to this title: rejected or dismissed it.

    A later "Request" of their own may overrule that (the row is then waiting or
    approved again), so an old `rejected_at` on such a row does not count.
    """
    status = existing.get("status")
    if status in {"rejected", "dismissed"}:
        return True
    return bool(existing.get("rejected_at")) and status not in PENDING_STATUSES | DECIDED_STATUSES


class LocalRequestProvider:
    name = "local"

    async def submit(self, user_id: str, item: Dict[str, Any], status: str = "requested") -> Dict[str, Any]:
        existing = await find_existing_request(user_id, item)
        request_id = item.get("id") or existing.get("id") or f"req_{uuid.uuid4().hex[:12]}"
        # Jobs write "pending_approval" and "request_failed"; a person's own
        # request arrives as "requested" and may overrule an old rejection.
        automated = status in AUTOMATED_STATUSES
        if existing.get("status") in DECIDED_STATUSES and status in QUEUE_STATUSES:
            return {"ok": True, "status": existing["status"], "provider": self.name, "id": request_id}
        if automated and (existing.get("status") in FINAL_STATUSES or existing.get("rejected_at")):
            return {"ok": True, "status": existing["status"], "provider": self.name, "id": request_id}
        now = datetime.now(timezone.utc).isoformat()
        if status == "pending_approval" and existing.get("status") in PENDING_STATUSES:
            # Suggested again while it waits: not new, so it keeps its place in the
            # queue (updated_at is the queue's "added" order) and its first job.
            refresh: Dict[str, Any] = {"last_suggested_at": now}
            for field in SUGGESTION_FIELDS:
                if item.get(field) is not None:
                    refresh[field] = item[field]
            if refresh.get("match_score") is not None:
                try:
                    refresh["match_score"] = max(0, min(100, int(round(float(refresh["match_score"])))))
                except (TypeError, ValueError):
                    refresh.pop("match_score")
            for field in ("poster", "genres", "release_date"):
                if not existing.get(field) and item.get(field):
                    refresh[field] = item[field]
            if not existing.get("source_job_id") and (item.get("job_id") or item.get("source_job_id")):
                refresh["source_job_id"] = item.get("job_id") or item.get("source_job_id")
            await db.requests.update_one(
                {"user_id": user_id, "id": request_id},
                {"$set": refresh, "$inc": {"suggested_count": 1}},
            )
            return {"ok": True, "status": existing["status"], "provider": self.name, "id": request_id,
                    "already_queued": True}
        doc = {
            "user_id": user_id,
            "id": request_id,
            "title": item.get("title"),
            "year": item.get("year"),
            "type": item.get("type"),
            "tmdb_id": item.get("tmdb_id"),
            "poster": item.get("poster") or item.get("poster_url"),
            "status": status,
            "provider": item.get("provider") or self.name,
            "source_job_id": item.get("job_id") or item.get("source_job_id"),
            "recommendation_id": item.get("recommendation_id"),
            "updated_at": now,
        }
        # Carried so the Requests tab can filter by genre/type and sort by release date.
        genres = [str(g) for g in (item.get("genres") or []) if g]
        if genres:
            doc["genres"] = genres
        release = item.get("release_date") or item.get("first_air_date") or item.get("aired_at")
        if release:
            doc["release_date"] = str(release)[:10]
        if item.get("original_language"):
            doc["original_language"] = str(item["original_language"])
        rating = item.get("tmdb_rating") if item.get("tmdb_rating") is not None else item.get("rating")
        if rating is not None:
            try:
                doc["rating"] = round(float(rating), 1)
            except (TypeError, ValueError):
                pass

        score = item.get("match_score")
        if score is not None:
            try:
                doc["match_score"] = max(0, min(100, int(round(float(score)))))
            except (TypeError, ValueError):
                pass
        if item.get("external_request_id"):
            doc["external_request_id"] = item["external_request_id"]
        # What the title *is*, so the already-requested check matches it again:
        # an anime film stored as plain "anime" looked like a series, was never
        # recognised as queued, and came back in every run.
        for field in ("canonical_media_id", "media_type", "format", "anilist_id", "original_title"):
            if item.get(field) not in (None, ""):
                doc[field] = item[field]
        await db.requests.update_one(
            {"user_id": user_id, "id": request_id},
            {"$set": doc, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        return {"ok": True, "status": status, "provider": self.name, "id": request_id}


class SeerRequestProvider:
    name = "seer"

    def __init__(self, url: str, api_key: str):
        self.url = url.rstrip("/")
        self.api_key = api_key

    async def submit(self, user_id: str, item: Dict[str, Any], status: str = "requested") -> Dict[str, Any]:
        local = LocalRequestProvider()
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                response = await client.post(
                    f"{self.url}/api/v1/request",
                    headers={"X-Api-Key": self.api_key},
                    json={
                        "mediaType": "tv" if item.get("type") in {"show", "tv", "anime"} else "movie",
                        "mediaId": item.get("tmdb_id"),
                        "title": item.get("title"),
                        "year": item.get("year"),
                    },
                )
            if response.status_code not in (200, 201):
                await local.submit(user_id, {**item, "job_id": item.get("source_job_id")}, "request_failed")
                return {"ok": False, "status": "request_failed", "provider": self.name, "id": item.get("id")}
            external_id = None
            try:
                payload = response.json() or {}
                external_id = payload.get("id") or payload.get("requestId") or payload.get("request_id")
            except Exception:
                external_id = None
            stored = await local.submit(
                user_id,
                {
                    **item,
                    "external_request_id": str(external_id) if external_id is not None else None,
                    "provider": self.name,
                },
                "requested",
            )
            if external_id is not None:
                await db.requests.update_one(
                    {"user_id": user_id, "id": stored["id"]},
                    {"$set": {"external_request_id": str(external_id), "provider": self.name}},
                )
                stored["external_request_id"] = str(external_id)
            stored["external"] = True
            stored["provider"] = self.name
            return stored
        except Exception as exc:
            logging.warning("Seer request failed: %s", exc)
            await local.submit(user_id, item, "request_failed")
            return {"ok": False, "status": "request_failed", "provider": self.name, "id": item.get("id")}


def get_request_provider(conn: Optional[Dict[str, Any]] = None):
    conn = conn or {}
    url = conn.get("seer_url") or conn.get("request_provider_url") or REQUEST_PROVIDER_URL
    key = conn.get("seer_api_key") or conn.get("request_provider_api_key") or REQUEST_PROVIDER_API_KEY
    if url and key and not env_flag("REQUEST_PROVIDER_DISABLED", "false"):
        return SeerRequestProvider(url, key)
    return LocalRequestProvider()
