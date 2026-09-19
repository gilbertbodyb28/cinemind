"""Safe schema-version upgrades. Existing user data is never wiped."""

from datetime import datetime, timezone
import logging

from database import db
from recommendation.media_identity import apply_identity_mapping, persist_identity, plan_identity_merges

SCHEMA_VERSION = 3
logger = logging.getLogger(__name__)


async def migrate_to_v3() -> None:
    """Merge split provider identities and backfill canonical IDs / feedback genres."""
    identities = await db.media_identities.find({}).to_list(20_000)
    mapping = plan_identity_merges(identities)
    merged = await apply_identity_mapping(mapping)
    if merged:
        logger.info("Merged %s duplicate media identities", merged)

    for rec in await db.recommendations.find({"canonical_media_id": {"$in": [None, ""]}}).to_list(5_000):
        identity = await persist_identity(rec)
        await db.recommendations.update_one(
            {"_id": rec["_id"]},
            {"$set": {"canonical_media_id": identity["canonical_id"]}},
        )

    for row in await db.recommendation_feedback.find({}).to_list(5_000):
        updates = {}
        if not row.get("genres") and row.get("recommendation_id"):
            rec = await db.recommendations.find_one(
                {"user_id": row.get("user_id"), "id": row["recommendation_id"]}
            )
            if rec and rec.get("genres"):
                updates["genres"] = rec["genres"]
            if rec and rec.get("canonical_media_id") and not row.get("canonical_media_id"):
                updates["canonical_media_id"] = rec["canonical_media_id"]
        if updates:
            await db.recommendation_feedback.update_one({"_id": row["_id"]}, {"$set": updates})

    seen = set()
    for row in await db.recommendation_feedback.find({}).sort("updated_at", -1).to_list(5_000):
        key = (row.get("user_id"), row.get("recommendation_id"))
        if key in seen:
            await db.recommendation_feedback.delete_one({"_id": row["_id"]})
        else:
            seen.add(key)


async def apply_migrations() -> int:
    meta = await db.app_meta.find_one({"id": "schema"}) or {}
    current = int(meta.get("version") or 0)
    while current < SCHEMA_VERSION:
        nxt = current + 1
        if nxt == 3:
            await migrate_to_v3()
        logger.info("Migrating CineMind schema from %s to %s", current, nxt)
        current = nxt
        await db.app_meta.update_one(
            {"id": "schema"},
            {"$set": {
                "id": "schema",
                "version": current,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )
    return current
