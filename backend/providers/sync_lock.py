"""Database-backed provider sync claims. Process memory is not enough."""

from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid

from database import db

DEFAULT_TTL_SECONDS = 120


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _expired(doc: Optional[dict], now: datetime) -> bool:
    if not doc:
        return True
    if doc.get("sync_status") != "running":
        return True
    expires = doc.get("lock_expires_at")
    if not expires:
        return True
    try:
        exp = datetime.fromisoformat(expires)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return exp <= now


async def acquire_sync_lock(provider: str, account_id: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> Optional[str]:
    now = _now()
    existing = await db.provider_sync_state.find_one({"provider": provider, "account_id": account_id})
    if existing and not _expired(existing, now):
        return None
    owner = uuid.uuid4().hex
    expires = now + timedelta(seconds=ttl_seconds)
    await db.provider_sync_state.update_one(
        {"provider": provider, "account_id": account_id},
        {"$set": {
            "provider": provider,
            "account_id": account_id,
            "sync_status": "running",
            "lock_owner": owner,
            "lock_acquired_at": now.isoformat(),
            "lock_expires_at": expires.isoformat(),
        }},
        upsert=True,
    )
    claimed = await db.provider_sync_state.find_one({"provider": provider, "account_id": account_id})
    if claimed and claimed.get("lock_owner") == owner:
        return owner
    return None


async def release_sync_lock(
    provider: str,
    account_id: str,
    owner: str,
    error: Optional[str] = None,
    items_synced: int = 0,
) -> None:
    now = _now().isoformat()
    update = {
        "sync_status": "error" if error else "idle",
        "lock_owner": None,
        "lock_expires_at": None,
        "last_sync_at": now,
        "last_error": error,
        "items_synced": items_synced,
    }
    if not error:
        update["last_success_at"] = now
    await db.provider_sync_state.update_one(
        {"provider": provider, "account_id": account_id, "lock_owner": owner},
        {"$set": update},
    )


async def record_sync_result(
    provider: str,
    account_id: str,
    *,
    items_synced: int = 0,
    error: Optional[str] = None,
) -> None:
    now = _now().isoformat()
    update = {
        "provider": provider,
        "account_id": account_id,
        "sync_status": "error" if error else "idle",
        "last_sync_at": now,
        "last_error": error,
        "items_synced": items_synced,
    }
    if not error:
        update["last_success_at"] = now
    await db.provider_sync_state.update_one(
        {"provider": provider, "account_id": account_id},
        {"$set": update},
        upsert=True,
    )


def public_sync_state(doc: Optional[dict]) -> dict:
    running = bool(doc) and not _expired(doc, _now())
    return {
        "running": running,
        "last_sync_at": (doc or {}).get("last_sync_at"),
        "last_success_at": (doc or {}).get("last_success_at"),
        "last_error": (doc or {}).get("last_error"),
        "items_synced": (doc or {}).get("items_synced") or 0,
    }


async def sync_lock_busy(provider: str, account_id: str) -> bool:
    existing = await db.provider_sync_state.find_one({"provider": provider, "account_id": account_id})
    return not _expired(existing, _now())
