"""Mongo client, database handle, and safe index creation."""

import logging

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import OperationFailure, PyMongoError

from config import DB_NAME, MONGO_URL

logger = logging.getLogger(__name__)

mongo_url = MONGO_URL
client = AsyncIOMotorClient(mongo_url)
db = client[DB_NAME]


async def _create_index(collection, keys, **kwargs) -> None:
    try:
        await collection.create_index(keys, **kwargs)
    except (OperationFailure, PyMongoError) as exc:
        logger.warning("Could not create index %s on %s: %s", keys, collection.name, exc)


async def ensure_indexes() -> None:
    """Create uniqueness and lookup indexes. Existing user data is never wiped."""
    await _create_index(db.users, "user_id", unique=True)
    await _create_index(db.users, "email", unique=True, sparse=True)
    await _create_index(db.users, "google_sub", unique=True, sparse=True)
    await _create_index(db.user_sessions, "session_token", unique=True)
    await _create_index(db.user_sessions, "user_id")
    await _create_index(db.connections, "user_id", unique=True)
    await _create_index(db.history, "user_id")
    await _create_index(db.history, [("user_id", 1), ("id", 1)])
    await _create_index(db.recommendations, "user_id")
    await _create_index(db.recommendations, [("user_id", 1), ("id", 1)])
    await _create_index(db.taste_profiles, "user_id")
    await _create_index(db.llm_usage, [("user_id", 1), ("created_at", 1)])
    await _create_index(db.media_identities, "canonical_id", unique=True)
    await _create_index(db.media_identities, [("tmdb_id", 1), ("media_type", 1)], sparse=True)
    await _create_index(db.media_identities, [("title_key", 1), ("year", 1), ("media_type", 1)])
    await _create_index(db.media_identities, "imdb_id", sparse=True)
    await _create_index(db.media_identities, "anilist_id", sparse=True)
    await _create_index(db.media_identities, [("trakt_id", 1), ("media_type", 1)], sparse=True)
    await _create_index(db.media_identities, [("simkl_id", 1), ("media_type", 1)], sparse=True)
    await _create_index(db.media_history, [("user_id", 1), ("canonical_media_id", 1), ("provider", 1)], unique=True)
    await _create_index(db.media_library, [("user_id", 1), ("provider", 1)])
    await _create_index(db.history, [("user_id", 1), ("canonical_media_id", 1)])
    await _create_index(db.jobs, [("user_id", 1), ("id", 1)], unique=True)
    await _create_index(db.job_runs, [("user_id", 1), ("job_id", 1), ("started_at", -1)])
    await _create_index(db.job_locks, "job_id", unique=True)
    await _create_index(db.blacklist, [("user_id", 1), ("canonical_media_id", 1)], unique=True)
    await _create_index(db.recommendation_feedback, [("user_id", 1), ("recommendation_id", 1)])
    await _create_index(db.requests, [("user_id", 1), ("id", 1)], unique=True)
    # The queue sorts newest-first per status; without this it is a collection scan.
    await _create_index(db.requests, [("user_id", 1), ("status", 1), ("updated_at", -1)])
    await _create_index(db.provider_sync_state, [("provider", 1), ("account_id", 1)], unique=True)
    await _create_index(db.provider_cache, "key", unique=True)
    await _create_index(db.provider_cache, "expires_at")
