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
QUEUE_STATUSES = {"pending_approval", "pending", "requested", "request_failed"}
# A rejection is the user's decision as well. submit() is the automated path
# (jobs, the Seer fallback); a job that met the title again used to flip the
# rejected row back to pending_approval. The manual POST /requests inserts on
# its own, so this never stops the user asking for a title again.
KEPT_STATUSES = DECIDED_STATUSES | {"rejected"}


class LocalRequestProvider:
    name = "local"

    async def submit(self, user_id: str, item: Dict[str, Any], status: str = "requested") -> Dict[str, Any]:
        request_id = item.get("id")
        query = {"user_id": user_id, "id": request_id} if request_id else {
            "user_id": user_id,
            "title": item.get("title"),
            "year": item.get("year"),
        }
        existing = await db.requests.find_one(query, {"id": 1, "status": 1}) or {}
        request_id = request_id or existing.get("id") or f"req_{uuid.uuid4().hex[:12]}"
        if existing.get("status") in KEPT_STATUSES and status in QUEUE_STATUSES:
            return {"ok": True, "status": existing["status"], "provider": self.name, "id": request_id}
        now = datetime.now(timezone.utc).isoformat()
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
        await db.requests.update_one({"user_id": user_id, "id": request_id}, {"$set": doc}, upsert=True)
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
