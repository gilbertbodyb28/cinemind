"""Complete, non-destructive history sync for Plex, Trakt, Simkl and AniList.

The `/history/sync` endpoint the Profile and Dashboard buttons call had
regressed to a demo-era version (found 2026-09-24, never run since 2026-09-08):

- Trakt: one page of 50 plays, and the show's *community* rating stored as the
  user's own. It would have replaced 10,000 real plays with 50.
- Simkl: the first 50 rows of each list - exactly the 50/50/50 in the database.
- Plex: the first 50 rows of /library/all, library membership stored as watch
  history and Plex's audience rating stored as a personal rating.
- Every provider's rows were deleted before the new ones were written, so one
  partial response took that provider's history down with it.

This module fetches everything and marks a provider "complete" only when every
page came back. It then *merges* - it never deletes:

- A stored play is recognised again by the provider's own id for it
  (`provider_play_id`: Trakt's history id, AniList's media id, ...). Rows
  written before that id existed are matched once by title id + watch time,
  as a multiset, because Trakt stamps a season marked watched in one go with
  a single timestamp. A unique index keeps re-runs from ever adding a
  duplicate.
- An empty value never overwrites a stored one, and a personal rating is never
  removed. A changed rating keeps the old one in `previous_rating`.
- Rows the provider no longer returns are kept and counted, not deleted.
- CineMind's own rows (feedback, Requests decisions) are never touched.

Personal ratings are written to `media_history` for every rated title,
including titles whose plays are older than the history window. AniList scores
are always requested on POINT_10, whatever format the user rates in.

    cd backend
    PYTHONPATH=../.runtime/python:. python3 -m providers.history_sync --user <id> --dry-run

`--dry-run` fetches, plans and reports, and writes nothing at all.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx

TRAKT_PAGE = 100
TRAKT_MAX_PAGES = 400
PLEX_PAGE = 200
PLEX_MAX_ROWS = 50000

#: Providers that keep one row per title rather than one per play.
PER_TITLE_PROVIDERS = {"anilist", "simkl"}
#: Written by enrichment, not by a provider: a sync never touches them.
PROTECTED_FIELDS = {"_id", "id", "user_id", "poster", "backdrop", "live", "updated_at"}
PLAY_INDEX = "history_provider_play_unique"
PER_PLAY_METADATA = {"history_id", "season", "episode", "episode_trakt_id", "viewed_at", "account_id"}


@dataclass
class ProviderFetch:
    provider: str
    history: List[Dict[str, Any]] = field(default_factory=list)
    personal: List[Dict[str, Any]] = field(default_factory=list)
    library: List[Dict[str, Any]] = field(default_factory=list)
    complete: bool = False
    error: Optional[str] = None
    notes: Dict[str, Any] = field(default_factory=dict)


def _safe(exc: Any) -> str:
    from jobs.engine import safe_provider_error

    return safe_provider_error(exc)


async def fetch_trakt(user_id: str, conn: Dict[str, Any]) -> ProviderFetch:
    from config import TRAKT_API
    from providers.trakt import (
        apply_user_ratings, parse_history_entry, parse_rating_entry,
        resolve_trakt_client_id, trakt_headers, trakt_token,
    )

    result = ProviderFetch("trakt")
    client_id = resolve_trakt_client_id(conn)
    token = await trakt_token(user_id, conn) if client_id else None
    if not (client_id and token):
        result.error = "trakt_not_connected"
        return result
    headers = trakt_headers(client_id, token)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            async def pages(path: str) -> Optional[List[Dict[str, Any]]]:
                rows: List[Dict[str, Any]] = []
                page = 1
                while page <= TRAKT_MAX_PAGES:
                    response = await client.get(f"{TRAKT_API}{path}", params={
                        "page": page, "limit": TRAKT_PAGE, "extended": "full"}, headers=headers)
                    if response.status_code != 200:
                        result.error = f"trakt {path} responded {response.status_code}"
                        result.notes["status"] = response.status_code
                        return None
                    batch = response.json() or []
                    rows.extend(batch)
                    count = int(response.headers.get("X-Pagination-Page-Count") or 1)
                    if page >= count or not batch:
                        result.notes.setdefault("reported_totals", {})[path] = response.headers.get(
                            "X-Pagination-Item-Count")
                        return rows
                    page += 1
                result.error = f"trakt {path} has more than {TRAKT_MAX_PAGES} pages"
                return None

            history = await pages("/sync/history")
            ratings: List[Dict[str, Any]] = []
            for kind in ("movies", "shows"):
                rows = await pages(f"/sync/ratings/{kind}")
                if rows is None:
                    return result
                ratings.extend(rows)
            if history is None:
                return result
    except Exception as exc:
        result.error = _safe(exc)
        return result
    finally:
        await _note_auth(user_id, "trakt", result)
    items = [parse_history_entry(entry) for entry in history]
    parsed_ratings = [parse_rating_entry(entry) for entry in ratings]
    apply_user_ratings(items, parsed_ratings)
    result.history = items
    # Every rating is personal evidence, including titles whose plays predate
    # the history window: those are exactly the ones the old sync lost.
    result.personal = [row for row in parsed_ratings if row.get("rating") is not None]
    result.complete = True
    result.notes.update({"plays": len(items), "ratings": len(result.personal)})
    return result


async def fetch_simkl(conn: Dict[str, Any]) -> ProviderFetch:
    from config import SIMKL_API
    from providers.simkl import (
        parse_history_entry, simkl_failure_hint, simkl_headers, simkl_params, simkl_token_client_id,
    )

    result = ProviderFetch("simkl")
    token = conn.get("simkl_access_token")
    client_id = simkl_token_client_id(conn)
    if not (token and client_id):
        result.error = "simkl_not_connected"
        return result
    try:
        async with httpx.AsyncClient(timeout=40) as client:
            response = await client.get(
                f"{SIMKL_API}/sync/all-items",
                params={**simkl_params(client_id), "extended": "full"},
                headers=simkl_headers(client_id, token),
            )
    except Exception as exc:
        result.error = _safe(exc)
        return result
    if response.status_code != 200:
        result.error = simkl_failure_hint(response)
        result.notes["status"] = response.status_code
        await _note_auth(conn.get("user_id"), "simkl", result)
        return result
    await _note_auth(conn.get("user_id"), "simkl", result)
    data = response.json() or {}
    for kind, key in (("movie", "movies"), ("show", "shows"), ("anime", "anime")):
        for entry in data.get(key) or []:
            row = parse_history_entry(kind, entry)
            if row.get("simkl_id") is not None:
                row["provider_play_id"] = "simkl:%s:%s" % (kind, row["simkl_id"])
            if entry.get("user_rating") is not None:
                row["rating"] = float(entry["user_rating"])
                row["rating_scale"] = 10
                result.personal.append(dict(row))
            if entry.get("watched_episodes_count") is not None:
                row["progress"] = int(entry.get("watched_episodes_count") or 0)
            result.history.append(row)
    result.complete = True
    result.notes = {"rows": len(result.history), "ratings": len(result.personal)}
    return result


async def fetch_plex(conn: Dict[str, Any]) -> ProviderFetch:
    from providers.plex import classify_history_payload, classify_library_payload, container_total, plex_page_headers

    result = ProviderFetch("plex")
    base, token = (conn.get("plex_url") or "").rstrip("/"), conn.get("plex_token")
    if not (base and token):
        result.error = "plex_not_connected"
        return result
    try:
        async with httpx.AsyncClient(timeout=30, verify=False) as client:
            start = 0
            while start < PLEX_MAX_ROWS:
                response = await client.get(f"{base}/status/sessions/history/all",
                                            headers=plex_page_headers(token, start, PLEX_PAGE))
                if response.status_code != 200:
                    result.error = plex_failure_hint(response.status_code)
                    result.notes["status"] = response.status_code
                    await _note_auth(conn.get("user_id"), "plex", result)
                    return result
                if start == 0:
                    await _note_auth(conn.get("user_id"), "plex", result)
                payload = response.json()
                rows = classify_history_payload(payload, limit=PLEX_PAGE)
                for row in rows:
                    viewed = (row.get("provider_specific_metadata") or {}).get("viewed_at")
                    if row.get("plex_rating_key") and viewed:
                        row["provider_play_id"] = "plex:%s:%s" % (row["plex_rating_key"], viewed)
                result.history.extend(rows)
                total = container_total(payload)
                start += PLEX_PAGE
                if not rows or start >= total:
                    break
            sections = await client.get(f"{base}/library/sections", headers=plex_page_headers(token, 0, 50))
            if sections.status_code == 200:
                for section in ((sections.json() or {}).get("MediaContainer") or {}).get("Directory") or []:
                    listing = await client.get(f"{base}/library/sections/{section.get('key')}/all",
                                               headers=plex_page_headers(token, 0, 20000))
                    if listing.status_code == 200:
                        library, _ = classify_library_payload(listing.json(), limit=20000)
                        result.library.extend(library)
    except Exception as exc:
        result.error = _safe(exc)
        return result
    result.complete = True
    result.notes = {"plays": len(result.history), "library": len(result.library)}
    return result


async def _note_auth(user_id: Optional[str], provider: str, result: ProviderFetch) -> None:
    """Record a refused sign-in on the connection, or clear it once the provider answers."""
    from providers.auth_state import clear_auth_failure, is_auth_rejection, note_auth_failure

    status = result.notes.get("status")
    if status is not None and is_auth_rejection(provider, int(status)):
        await note_auth_failure(user_id, provider, result.error or f"{provider} responded {status}")
    elif status is None and not result.error:
        await clear_auth_failure(user_id, provider)


def plex_failure_hint(status: int) -> str:
    if status == 401:
        # Measured 2026-09-25: plex.tv itself answers 401 for the stored token,
        # so it is revoked or expired, not a server-side permission problem.
        return ("plex responded 401: the saved Plex token is no longer accepted - "
                "Connect with Plex again under Sources > Plex Media Server")
    return f"plex history responded {status}"


async def fetch_anilist(conn: Dict[str, Any]) -> ProviderFetch:
    from providers.anilist import fetch_media_list

    result = ProviderFetch("anilist")
    token = conn.get("anilist_access_token")
    if not token:
        result.error = "anilist_not_connected"
        return result
    try:
        rows = await fetch_media_list(token, conn.get("anilist_username"))
    except Exception as exc:
        result.error = _safe(exc)
        return result
    for row in rows:
        if row.get("anilist_id") is not None:
            row["provider_play_id"] = "anilist:%s" % row["anilist_id"]
    result.history = [dict(row) for row in rows]
    result.personal = [dict(row) for row in rows if row.get("rating") is not None]
    result.complete = True
    result.notes = {"entries": len(rows), "ratings": len(result.personal)}
    return result


# ---------------------------------------------------------------- matching --

def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def legacy_key(provider: str, row: Dict[str, Any]) -> Optional[tuple]:
    """How a row written before `provider_play_id` existed is recognised once.

    Per-play providers need the watch time too: the same show appears once per
    episode. Trakt stamps a season marked watched in one go with one timestamp,
    so a key can stand for many rows and is matched as a multiset.
    """
    from recommendation.media_identity import title_key

    kind = str(row.get("type") or row.get("media_type") or "").casefold()
    for name in ("trakt_id", "anilist_id", "simkl_id", "plex_rating_key", "tmdb_id", "imdb_id"):
        if not _empty(row.get(name)):
            ident: tuple = (name, str(row[name]))
            break
    else:
        key = title_key(row.get("title"))
        if not key:
            return None
        ident = ("title", key, str(row.get("year")))
    if provider == "anilist":
        return ident  # AniList ids are unique across formats
    if provider in PER_TITLE_PROVIDERS:
        return (kind, *ident)
    return (kind, *ident, str(row.get("watched_at") or row.get("last_watched_at") or ""))


def _same(left: Any, right: Any) -> bool:
    try:
        return abs(float(left) - float(right)) < 1e-9
    except (TypeError, ValueError):
        return left == right


def merge_changes(stored: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    """The $set that brings a stored row up to date without losing anything.

    Empty incoming values never overwrite, lists are only filled when empty,
    and a changed personal rating keeps the previous one next to it.
    """
    changes: Dict[str, Any] = {}
    for key, value in incoming.items():
        if key in PROTECTED_FIELDS or key == "rating_scale" or _empty(value):
            continue
        current = stored.get(key)
        if key == "provider_specific_metadata" and isinstance(value, dict):
            merged = {**(current or {}), **{k: v for k, v in value.items() if not _empty(v)}}
            if merged != (current or {}):
                changes[key] = merged
            continue
        if isinstance(value, list):
            if _empty(current):
                changes[key] = value
            continue
        if key == "rating":
            if current is not None and _same(current, value):
                continue
            if current is not None:
                changes["previous_rating"] = current
                changes["previous_rating_scale"] = stored.get("rating_scale")
                changes["rating_changed_at"] = datetime.now(timezone.utc).isoformat()
            changes["rating"] = value
            changes["rating_scale"] = incoming.get("rating_scale") or 10
            continue
        if current != value:
            changes[key] = value
    return changes


def plan_history(provider: str, stored: List[Dict[str, Any]], fetched: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Which fetched play rows are new, which update a stored row, which are unchanged."""
    by_play: Dict[str, Dict[str, Any]] = {}
    legacy: Dict[tuple, List[Dict[str, Any]]] = {}
    for doc in sorted(stored, key=lambda doc: str(doc.get("_id") or doc.get("id") or "")):
        if doc.get("provider_play_id"):
            by_play[doc["provider_play_id"]] = doc
        else:
            key = legacy_key(provider, doc)
            if key is not None:
                legacy.setdefault(key, []).append(doc)
    seen: set = set()
    inserts: List[Dict[str, Any]] = []
    updates: List[Tuple[Any, Dict[str, Any]]] = []
    fields: Counter = Counter()
    unchanged = skipped = 0
    matched_ids: set = set()
    ordered = sorted(fetched, key=lambda row: str(row.get("provider_play_id") or ""))
    for row in ordered:
        play_id = row.get("provider_play_id")
        if play_id and play_id in seen:
            skipped += 1  # the provider listed the same play twice
            continue
        if play_id:
            seen.add(play_id)
        doc = by_play.get(play_id) if play_id else None
        if doc is None:
            key = legacy_key(provider, row)
            if key is None:
                skipped += 1
                continue
            bucket = legacy.get(key)
            if bucket:
                doc = bucket.pop(0)
        if doc is None:
            inserts.append(row)
            continue
        matched_ids.add(id(doc))
        changes = merge_changes(doc, row)
        if changes:
            updates.append((doc["_id"], changes))
            fields.update(changes.keys())
        else:
            unchanged += 1
    kept = [doc for doc in stored if id(doc) not in matched_ids]
    return {"inserts": inserts, "updates": updates, "unchanged": unchanged, "skipped": skipped,
            "kept": kept, "fields": fields}


def _title_summary(plays: List[Dict[str, Any]], ratings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """One media_history row per title: plays, last watch and the personal rating.

    `watch_count` is how often the title was watched *through*, not how many
    episode plays it has: a film on three different days is three, a series is
    its plays per distinct episode. Counting episode plays made every series a
    "rewatch" and rating rows counted as plays.
    """
    rows = plays or ratings
    base = dict(max(rows, key=lambda row: sum(0 if _empty(value) else 1 for value in row.values())))
    for key in ("provider_play_id", "live"):
        base.pop(key, None)
    # Per-play details (history id, episode) describe one play, not the title.
    metadata = {key: value for key, value in (base.get("provider_specific_metadata") or {}).items()
                if key not in PER_PLAY_METADATA}
    stamps = sorted(str(row.get("watched_at") or row.get("last_watched_at")) for row in plays
                    if row.get("watched_at") or row.get("last_watched_at"))
    if stamps:
        base["watched_at"] = stamps[0]
        base["last_watched_at"] = stamps[-1]
    else:
        base.pop("watched_at", None)
        base.pop("last_watched_at", None)
    per_title = [int(row.get("watch_count") or 1) for row in plays if row.get("watch_count")]
    episodes = {((row.get("provider_specific_metadata") or {}).get("season"),
                 (row.get("provider_specific_metadata") or {}).get("episode")) for row in plays}
    episodes.discard((None, None))
    if len(plays) > 1:
        metadata["plays"] = len(plays)
    if episodes:
        metadata["episodes_watched"] = len(episodes)
    if per_title:
        base["watch_count"] = max(per_title)
    elif episodes:
        base["watch_count"] = max(1, len(plays) // len(episodes))
    else:
        base["watch_count"] = max(1, len({stamp[:10] for stamp in stamps}))
    rated = [row for row in ratings if row.get("rating") is not None] or \
            [row for row in plays if row.get("rating") is not None]
    if rated:
        base["rating"] = rated[0]["rating"]
        base["rating_scale"] = rated[0].get("rating_scale") or 10
        if rated[0].get("rated_at"):
            metadata["rated_at"] = rated[0]["rated_at"]
    base["provider_specific_metadata"] = metadata
    return base


def merge_title_changes(stored: Dict[str, Any], row: Dict[str, Any]) -> Dict[str, Any]:
    """merge_changes for a title row: counts only grow, the watch window only widens."""
    changes = merge_changes(stored, row)
    for key, pick in (("watch_count", max), ("progress", max)):
        if key in changes and stored.get(key) is not None:
            best = pick(int(stored[key] or 0), int(changes[key] or 0))
            if best == stored.get(key):
                changes.pop(key)
            else:
                changes[key] = best
    for key, pick in (("watched_at", min), ("last_watched_at", max)):
        if key in changes and stored.get(key):
            best = pick(str(stored[key]), str(changes[key]))
            if best == stored.get(key):
                changes.pop(key)
            else:
                changes[key] = best
    if "watch_count" in changes:
        changes["rewatched"] = bool(stored.get("rewatched")) or int(changes["watch_count"]) > 1
    return changes


# ------------------------------------------------------------------ commit --

async def ensure_play_index() -> Optional[str]:
    """One stored row per provider play, enforced by the database itself."""
    from database import db

    try:
        await db.history.create_index(
            [("user_id", 1), ("source", 1), ("provider_play_id", 1)],
            name=PLAY_INDEX, unique=True,
            partialFilterExpression={"provider_play_id": {"$exists": True}},
        )
        return None
    except Exception as exc:  # duplicates already present: report, never delete
        return exc.__class__.__name__ + ": " + str(exc)[:200]


async def commit(user_id: str, fetched: ProviderFetch, *, dry_run: bool = False) -> Dict[str, Any]:
    """Merge one provider's complete fetch into CineMind. Never on a partial one."""
    from pymongo import InsertOne, UpdateOne

    from database import db
    from providers.sync_lock import record_sync_result
    from recommendation.history_normalizer import normalize_history_item
    from recommendation.media_identity import persist_identity

    provider = fetched.provider
    stored_count = await db.history.count_documents({"user_id": user_id, "source": provider})
    if not fetched.complete:
        if not dry_run:
            await record_sync_result(provider, user_id, error=fetched.error or "incomplete")
        return {"committed": False, "kept_rows": stored_count, "error": fetched.error}
    if not fetched.history and stored_count:
        # An empty answer from a provider that held rows yesterday is far more
        # likely an outage than a user who deleted everything.
        if not dry_run:
            await record_sync_result(provider, user_id, error="empty response; kept previous rows")
        return {"committed": False, "kept_rows": stored_count, "error": "empty response"}

    failures: List[str] = []
    if not dry_run:
        # Identity first: resolving a title can merge two identities and remap
        # the stored rows, so they are read only after every title is resolved.
        for rows in _title_groups(list(fetched.history) + list(fetched.personal)).values():
            try:
                identity = await persist_identity(dict(rows[0]))
            except Exception as exc:
                failures.append(_safe(exc))
                continue
            for row in rows:
                row["canonical_media_id"] = identity["canonical_id"]
                if identity.get("tmdb_id") and not row.get("tmdb_id"):
                    row["tmdb_id"] = identity["tmdb_id"]
        index_error = await ensure_play_index()
        if index_error:
            failures.append(index_error)
    stored = await db.history.find({"user_id": user_id, "source": provider}).to_list(None)
    plan = plan_history(provider, stored, fetched.history)
    report: Dict[str, Any] = {
        "committed": not dry_run,
        "plays": {
            "fetched": len(fetched.history), "stored_before": len(stored),
            "imported": len(plan["inserts"]), "updated": len(plan["updates"]),
            "unchanged": plan["unchanged"], "skipped": plan["skipped"], "failed": 0,
            "kept_not_on_provider": len(plan["kept"]),
            "updated_fields": dict(plan["fields"].most_common()),
        },
    }
    stored_titles = await db.media_history.find({"user_id": user_id, "provider": provider}).to_list(None)
    if dry_run:
        # Identities are not resolved in a dry run (that writes), so the title
        # plan is matched on provider ids and is an estimate.
        known = {legacy_key("simkl", doc) for doc in stored_titles}
        rated_known = {legacy_key("simkl", doc) for doc in stored_titles if doc.get("rating") is not None}
        rated = {legacy_key("simkl", row) for row in fetched.personal}
        report["ratings"] = {"fetched": len(fetched.personal), "stored_before": len(rated_known),
                             "new_estimate": len(rated - rated_known)}
        report["titles"] = {"stored_before": len(stored_titles),
                            "new_estimate": len({legacy_key("simkl", row) for row in
                                                 list(fetched.history) + list(fetched.personal)} - known)}
        return report

    now = datetime.now(timezone.utc).isoformat()
    writes = [InsertOne({**row, "id": str(uuid.uuid4()), "user_id": user_id, "synced_at": now})
              for row in plan["inserts"] if row.get("canonical_media_id")]
    writes += [UpdateOne({"_id": doc_id}, {"$set": {**changes, "synced_at": now}})
               for doc_id, changes in plan["updates"]]
    failed = len(plan["inserts"]) - sum(1 for row in plan["inserts"] if row.get("canonical_media_id"))
    if writes:
        try:
            await db.history.bulk_write(writes, ordered=False)
        except Exception as exc:
            details = getattr(exc, "details", {}) or {}
            failed += len(details.get("writeErrors") or []) or len(writes)
            failures.append(_safe(exc))
    report["plays"].update({
        "imported": len(plan["inserts"]), "updated": len(plan["updates"]), "unchanged": plan["unchanged"],
        "skipped": plan["skipped"], "failed": failed, "kept_not_on_provider": len(plan["kept"]),
        "updated_fields": dict(plan["fields"].most_common()),
    })

    # Titles and personal ratings (media_history), keyed by canonical id.
    plays_by_title: Dict[str, List[Dict[str, Any]]] = {}
    ratings_by_title: Dict[str, List[Dict[str, Any]]] = {}
    for row in fetched.history:
        if row.get("canonical_media_id"):
            plays_by_title.setdefault(row["canonical_media_id"], []).append(row)
    for row in fetched.personal:
        if row.get("canonical_media_id"):
            ratings_by_title.setdefault(row["canonical_media_id"], []).append(row)
    by_canonical = {doc["canonical_media_id"]: doc for doc in stored_titles if doc.get("canonical_media_id")}
    titles = Counter()
    ratings = Counter()
    title_fields: Counter = Counter()
    title_writes = []
    for canonical in sorted(set(plays_by_title) | set(ratings_by_title)):
        summary = _title_summary(plays_by_title.get(canonical, []), ratings_by_title.get(canonical, []))
        row = normalize_history_item(summary, canonical, provider)
        row = {key: value for key, value in row.items() if not _empty(value) or key == "canonical_media_id"}
        doc = by_canonical.get(canonical)
        if doc is None:
            titles["imported"] += 1
            if row.get("rating") is not None:
                ratings["imported"] += 1
            # Upsert, not insert: never a second row for a title, even under a race.
            title_writes.append(UpdateOne(
                {"user_id": user_id, "canonical_media_id": canonical, "provider": provider},
                {"$setOnInsert": {**normalize_history_item(summary, canonical, provider),
                                  "user_id": user_id, "updated_at": now}},
                upsert=True,
            ))
            continue
        changes = merge_title_changes(doc, row)
        if row.get("rating") is not None:
            if doc.get("rating") is None:
                ratings["imported"] += 1
            elif "rating" in changes:
                ratings["updated"] += 1
            else:
                ratings["unchanged"] += 1
        if changes:
            titles["updated"] += 1
            title_fields.update(changes.keys())
            title_writes.append(UpdateOne({"_id": doc["_id"]}, {"$set": {**changes, "updated_at": now}}))
        else:
            titles["unchanged"] += 1
    touched = set(plays_by_title) | set(ratings_by_title)
    titles["kept_not_on_provider"] = sum(1 for canonical in by_canonical if canonical not in touched)
    ratings["kept_not_on_provider"] = sum(1 for canonical, doc in by_canonical.items()
                                          if canonical not in touched and doc.get("rating") is not None)
    if title_writes:
        try:
            await db.media_history.bulk_write(title_writes, ordered=False)
        except Exception as exc:
            details = getattr(exc, "details", {}) or {}
            titles["failed"] += len(details.get("writeErrors") or []) or len(title_writes)
            failures.append(_safe(exc))
    report["titles"] = {"stored_before": len(stored_titles), **{k: titles.get(k, 0) for k in (
        "imported", "updated", "unchanged", "kept_not_on_provider", "failed")},
        "updated_fields": dict(title_fields.most_common())}
    report["ratings"] = {"fetched": len(fetched.personal), **{k: ratings.get(k, 0) for k in (
        "imported", "updated", "unchanged", "kept_not_on_provider")}}
    if fetched.library:
        # Library membership is a snapshot of what is on the server, not history.
        from recommendation.history_normalizer import replace_library

        report["library_rows"] = await replace_library(user_id, provider, fetched.library)
    report["rows_after"] = await db.history.count_documents({"user_id": user_id, "source": provider})
    report["errors"] = failures[:10]
    await record_sync_result(provider, user_id, items_synced=report["rows_after"],
                             error="; ".join(failures[:3]) if failures else None)
    return report


def _title_groups(rows: Sequence[Dict[str, Any]]) -> Dict[tuple, List[Dict[str, Any]]]:
    """Rows that are one title, so a title's identity is resolved once, not per episode."""
    from recommendation.media_identity import identity_link_keys

    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    alias: Dict[tuple, tuple] = {}
    for row in rows:
        keys = identity_link_keys(row)
        if not keys:
            continue
        anchor = next((alias[key] for key in keys if key in alias), keys[0])
        groups.setdefault(anchor, []).append(row)
        for key in keys:
            alias.setdefault(key, anchor)
    return groups


async def sync_user_history(user_id: str, *, dry_run: bool = False,
                            providers: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    from database import db

    conn = await db.connections.find_one({"user_id": user_id}, {"_id": 0}) or {}
    wanted = set(providers or ("trakt", "simkl", "plex", "anilist"))
    fetchers = []
    if "trakt" in wanted and conn.get("trakt_access_token"):
        fetchers.append(fetch_trakt(user_id, conn))
    if "simkl" in wanted and conn.get("simkl_access_token"):
        fetchers.append(fetch_simkl(conn))
    if "plex" in wanted and conn.get("plex_url") and conn.get("plex_token"):
        fetchers.append(fetch_plex(conn))
    if "anilist" in wanted and conn.get("anilist_access_token"):
        fetchers.append(fetch_anilist(conn))
    report: Dict[str, Any] = {"dry_run": dry_run, "providers": {}}
    for fetched in await asyncio.gather(*fetchers):
        row: Dict[str, Any] = {"complete": fetched.complete, "error": fetched.error, "fetched": fetched.notes}
        # Providers one at a time: identity merges must not race each other.
        row["result"] = await commit(user_id, fetched, dry_run=dry_run)
        report["providers"][fetched.provider] = row
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user", required=True)
    parser.add_argument("--dry-run", action="store_true", help="fetch, plan and report; write nothing")
    parser.add_argument("--providers", help="comma list, default all connected")
    args = parser.parse_args(argv)
    if not args.dry_run:
        logging.warning("history_sync: merging history for %s", args.user)
    report = asyncio.run(sync_user_history(
        args.user, dry_run=args.dry_run,
        providers=[item for item in (args.providers or "").split(",") if item] or None,
    ))
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
