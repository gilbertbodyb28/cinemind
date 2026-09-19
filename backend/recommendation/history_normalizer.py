"""Normalized, provider-independent watch history."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from database import db
from .media_identity import display_type, incoming_ids, normalize_media_type


def normalize_history_item(
    item: Dict[str, Any],
    canonical_id: str,
    provider: Optional[str] = None,
) -> Dict[str, Any]:
    media_type = normalize_media_type(item.get("media_type") or item.get("type"))
    watched_at = item.get("watched_at") or item.get("last_watched_at")
    watch_count = int(item.get("watch_count") or 1)
    source = provider or item.get("provider") or item.get("source") or "unknown"
    row = {
        "canonical_media_id": canonical_id,
        "title": item.get("title") or "Unknown",
        "year": item.get("year"),
        "media_type": media_type,
        "type": item.get("type") or display_type(media_type),
        "provider": source,
        "provider_account_id": item.get("provider_account_id"),
        "watched_at": watched_at,
        "last_watched_at": item.get("last_watched_at") or watched_at,
        "watch_count": watch_count,
        "progress": item.get("progress"),
        "completed": item.get("completed"),
        "rewatched": bool(item.get("rewatched") or watch_count > 1),
        "rating": item.get("rating"),
        "rating_scale": item.get("rating_scale") or (10 if item.get("rating") is not None else None),
        "favorite": item.get("favorite"),
        "status": item.get("status"),
        "genres": list(item.get("genres") or []),
        "tags": list(item.get("tags") or []),
        "keywords": list(item.get("keywords") or []),
        "studios": list(item.get("studios") or []),
        "original_language": item.get("original_language"),
        "country": item.get("country"),
        "provider_specific_metadata": dict(item.get("provider_specific_metadata") or {}),
    }
    row.update(incoming_ids(item))
    return row


async def persist_normalized_history(user_id: str, item: Dict[str, Any], canonical_id: str) -> Dict[str, Any]:
    row = normalize_history_item(item, canonical_id)
    row["user_id"] = user_id
    row["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.media_history.update_one(
        {
            "user_id": user_id,
            "canonical_media_id": canonical_id,
            "provider": row["provider"],
        },
        {"$set": row},
        upsert=True,
    )
    return row


async def replace_library(user_id: str, provider: str, items: List[Dict[str, Any]]) -> int:
    await db.media_library.delete_many({"user_id": user_id, "provider": provider})
    if not items:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    rows = [{**item, "user_id": user_id, "in_library": True, "updated_at": now} for item in items]
    await db.media_library.insert_many(rows)
    return len(rows)
