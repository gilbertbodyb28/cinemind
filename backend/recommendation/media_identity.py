"""Provider-independent canonical media identity."""

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence
import re
import uuid

from database import db

EXTERNAL_IDS = (
    "tmdb_id",
    "imdb_id",
    "tvdb_id",
    "trakt_id",
    "simkl_id",
    "anilist_id",
    "plex_rating_key",
)


def coerce_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_media_type(value: Optional[str]) -> str:
    kind = (value or "movie").strip().lower()
    if kind in {"show", "tv", "series"}:
        return "tv"
    if kind == "anime":
        return "anime"
    return "movie"


DONGHUA_LANGUAGES = {"zh", "cn", "zh-cn", "zh-tw", "zh-hk", "yue", "chinese", "mandarin", "cantonese"}
DONGHUA_COUNTRIES = {"cn", "tw", "hk", "china", "taiwan", "hong kong"}
ANIME_LANGUAGES = {"ja", "japanese"}
ANIME_COUNTRIES = {"jp", "japan"}


def _lower_set(values: Iterable[Any]) -> set:
    return {str(item).strip().casefold() for item in values if item not in (None, "")}


def content_lane(candidate: Dict[str, Any]) -> str:
    """Which of the four lanes a title belongs to: anime, donghua, animation or live_action.

    Anime and donghua are both animation; they are told apart by origin, not by
    genre, because neither TMDb nor AniList tags a title "Donghua". A Chinese
    live-action drama is live_action — origin alone never makes a title donghua.
    """
    genres = _lower_set(candidate.get("genres") or [])
    media = normalize_media_type(candidate.get("media_type") or candidate.get("type"))
    animated = bool(genres & {"animation", "anime", "donghua"}) or media == "anime"
    if not animated:
        return "live_action"
    languages = _lower_set([candidate.get("original_language"), *(candidate.get("languages") or [])])
    countries = _lower_set([
        candidate.get("country"),
        candidate.get("country_of_origin"),
        *(candidate.get("origin_countries") or []),
    ])
    if "donghua" in genres or languages & DONGHUA_LANGUAGES or countries & DONGHUA_COUNTRIES:
        return "donghua"
    if "anime" in genres or languages & ANIME_LANGUAGES or countries & ANIME_COUNTRIES:
        return "anime"
    # AniList only lists anime-style media; without an origin it is Japanese by default.
    if media == "anime" and not languages and not countries:
        return "anime"
    return "animation"


def title_key(title: Optional[str]) -> str:
    text = (title or "").lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if text.startswith("the "):
        text = text[4:]
    return text


def display_type(media_type: str) -> str:
    return "show" if media_type == "tv" else media_type


def new_canonical_id() -> str:
    return f"mid_{uuid.uuid4().hex[:16]}"


def incoming_ids(item: Dict[str, Any]) -> Dict[str, Any]:
    ids: Dict[str, Any] = {}
    tmdb_id = coerce_int(item.get("tmdb_id"))
    if tmdb_id is not None:
        ids["tmdb_id"] = tmdb_id
    tvdb_id = coerce_int(item.get("tvdb_id"))
    if tvdb_id is not None:
        ids["tvdb_id"] = tvdb_id
    trakt_id = coerce_int(item.get("trakt_id"))
    if trakt_id is not None:
        ids["trakt_id"] = trakt_id
    simkl_id = coerce_int(item.get("simkl_id"))
    if simkl_id is not None:
        ids["simkl_id"] = simkl_id
    anilist_id = coerce_int(item.get("anilist_id"))
    if anilist_id is not None:
        ids["anilist_id"] = anilist_id
    if item.get("imdb_id"):
        ids["imdb_id"] = str(item["imdb_id"])
    if item.get("plex_rating_key"):
        ids["plex_rating_key"] = str(item["plex_rating_key"])
    return ids


def candidate_queries(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    media_type = normalize_media_type(item.get("media_type") or item.get("type"))
    ids = incoming_ids(item)
    queries: List[Dict[str, Any]] = []
    if "tmdb_id" in ids:
        queries.append({"tmdb_id": ids["tmdb_id"], "media_type": media_type})
    if "imdb_id" in ids:
        queries.append({"imdb_id": ids["imdb_id"]})
    if "tvdb_id" in ids:
        queries.append({"tvdb_id": ids["tvdb_id"], "media_type": media_type})
    if "trakt_id" in ids:
        queries.append({"trakt_id": ids["trakt_id"], "media_type": media_type})
    if "simkl_id" in ids:
        queries.append({"simkl_id": ids["simkl_id"], "media_type": media_type})
    if "anilist_id" in ids:
        queries.append({"anilist_id": ids["anilist_id"]})
    if "plex_rating_key" in ids:
        queries.append({"plex_rating_key": ids["plex_rating_key"]})
    year = coerce_int(item.get("year"))
    key = title_key(item.get("title"))
    if key and year is not None:
        queries.append({"title_key": key, "year": year, "media_type": media_type})
    return queries


def identity_scope(item: Dict[str, Any]) -> str:
    """The namespace a provider's ID actually lives in: series or film.

    "anime" is a classification, not a namespace - TMDb serves the same show
    under /tv whether or not it is animated. Scoping the keys by it meant Trakt
    history stored ("tmdb", 82684, "tv") while the TMDb candidate for the very
    same show produced ("tmdb", 82684, "anime"), so the watched filter did not
    match and "That Time I Got Reincarnated as a Slime" came back as a fresh
    recommendation for a viewer who had already finished it.
    """
    media_type = normalize_media_type(item.get("media_type") or item.get("type"))
    if media_type == "anime":
        # An anime film is still served from /movie, so it must not land in the
        # series namespace with the rest of the anime.
        stated = item.get("type") if item.get("media_type") else None
        fmt = str(item.get("format") or item.get("anime_format") or "").upper()
        if fmt in {"MOVIE", "FILM"} or (stated and normalize_media_type(stated) == "movie"):
            return "movie"
        return "tv"
    return "movie" if media_type == "movie" else "tv"


def identity_link_keys(item: Dict[str, Any]) -> List[tuple]:
    """Stable keys that mean 'this is the same title' across providers."""
    media_type = identity_scope(item)
    ids = incoming_ids(item)
    keys: List[tuple] = []
    if "tmdb_id" in ids:
        keys.append(("tmdb", ids["tmdb_id"], media_type))
    if "imdb_id" in ids:
        keys.append(("imdb", ids["imdb_id"]))
    if "tvdb_id" in ids:
        keys.append(("tvdb", ids["tvdb_id"], media_type))
    if "trakt_id" in ids:
        keys.append(("trakt", ids["trakt_id"], media_type))
    if "simkl_id" in ids:
        keys.append(("simkl", ids["simkl_id"], media_type))
    if "anilist_id" in ids:
        keys.append(("anilist", ids["anilist_id"]))
    if "plex_rating_key" in ids:
        keys.append(("plex", ids["plex_rating_key"]))
    year = coerce_int(item.get("year"))
    key = title_key(item.get("title"))
    if key and year is not None:
        keys.append(("title", key, year, media_type))
    return keys


def pick_keeper(members: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    return sorted(
        members,
        key=lambda doc: (
            -len(incoming_ids(doc)),
            doc.get("created_at") or "",
            doc.get("canonical_id") or "",
        ),
    )[0]


def plan_identity_merges(docs: Sequence[Dict[str, Any]]) -> Dict[str, str]:
    """Map loser canonical_id -> keeper canonical_id when providers split one title."""
    parent: Dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        if parent[node] != node:
            parent[node] = find(parent[node])
        return parent[node]

    def union(left: str, right: str) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    by_key: Dict[tuple, str] = {}
    by_id = {doc["canonical_id"]: doc for doc in docs if doc.get("canonical_id")}
    for doc in by_id.values():
        cid = doc["canonical_id"]
        parent.setdefault(cid, cid)
        for key in identity_link_keys(doc):
            if key in by_key:
                union(by_key[key], cid)
            else:
                by_key[key] = cid

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for cid in by_id:
        groups.setdefault(find(cid), []).append(by_id[cid])

    mapping: Dict[str, str] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        keeper = pick_keeper(members)
        keeper_id = keeper["canonical_id"]
        for member in members:
            loser = member["canonical_id"]
            if loser != keeper_id:
                mapping[loser] = keeper_id
    return mapping


def identity_matches(existing: Dict[str, Any], item: Dict[str, Any]) -> bool:
    return any(
        all(existing.get(field) == value for field, value in query.items())
        for query in candidate_queries(item)
    )


def find_match(identities: Sequence[Dict[str, Any]], item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for identity in identities:
        if identity_matches(identity, item):
            return identity
    return None


def merge_identity_fields(existing: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
    updates: Dict[str, Any] = {}
    for field, value in incoming_ids(item).items():
        if value is not None and existing.get(field) in (None, "", []):
            updates[field] = value
    if item.get("title") and not existing.get("title"):
        updates["title"] = item["title"]
    if item.get("poster") and not existing.get("poster_url"):
        updates["poster_url"] = item["poster"]
    incoming_genres = item.get("genres") or []
    if incoming_genres:
        merged = list(dict.fromkeys([*(existing.get("genres") or []), *incoming_genres]))
        if merged != (existing.get("genres") or []):
            updates["genres"] = merged
    if updates:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    return updates


def build_identity(item: Dict[str, Any]) -> Dict[str, Any]:
    media_type = normalize_media_type(item.get("media_type") or item.get("type"))
    year = coerce_int(item.get("year"))
    now = datetime.now(timezone.utc).isoformat()
    identity = {
        "canonical_id": new_canonical_id(),
        "title": item.get("title") or "Unknown",
        "original_title": item.get("original_title"),
        "title_key": title_key(item.get("title")),
        "year": year,
        "media_type": media_type,
        "genres": list(item.get("genres") or []),
        "keywords": list(item.get("keywords") or []),
        "original_language": item.get("original_language"),
        "country": item.get("country"),
        "runtime": item.get("runtime"),
        "release_date": item.get("release_date"),
        "poster_url": item.get("poster") or item.get("poster_url"),
        "overview": item.get("overview") or item.get("synopsis"),
        "created_at": now,
        "updated_at": now,
    }
    identity.update(incoming_ids(item))
    return identity


def unique_taste_docs(docs: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One row per canonical title so providers cannot triple-count the same film."""
    seen: Dict[str, Dict[str, Any]] = {}
    seen_title: Dict[str, str] = {}
    for doc in docs:
        title_fingerprint = (
            f"{title_key(doc.get('title'))}|{coerce_int(doc.get('year'))}|"
            f"{normalize_media_type(doc.get('type') or doc.get('media_type'))}"
        )
        key = doc.get("canonical_media_id") or title_fingerprint
        if key in seen or title_fingerprint in seen_title:
            continue
        seen[key] = doc
        if title_key(doc.get("title")) and coerce_int(doc.get("year")) is not None:
            seen_title[title_fingerprint] = key
    return list(seen.values())


async def _find_identity_matches(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for query in candidate_queries(item):
        for found in await db.media_identities.find(query).to_list(50):
            cid = found.get("canonical_id")
            if cid and cid not in seen:
                seen.add(cid)
                matches.append(found)
    return matches


async def remap_canonical_references(old_id: str, new_id: str) -> None:
    if old_id == new_id:
        return
    unique_pairs = {
        "media_history": ("user_id", "canonical_media_id", "provider"),
        "blacklist": ("user_id", "canonical_media_id"),
        "recommendation_feedback": ("user_id", "canonical_media_id"),
    }
    for name, fields in unique_pairs.items():
        collection = getattr(db, name)
        losers = await collection.find({"canonical_media_id": old_id}).to_list(1000)
        for row in losers:
            query = {field: (new_id if field == "canonical_media_id" else row.get(field)) for field in fields}
            exists = await collection.find_one(query)
            if exists:
                await collection.delete_one({"_id": row["_id"]})
            else:
                await collection.update_one({"_id": row["_id"]}, {"$set": {"canonical_media_id": new_id}})
    for name in ("history", "media_library", "recommendations"):
        await getattr(db, name).update_many(
            {"canonical_media_id": old_id},
            {"$set": {"canonical_media_id": new_id}},
        )


async def apply_identity_mapping(mapping: Dict[str, str]) -> int:
    merged = 0
    for loser, keeper in mapping.items():
        loser_doc = await db.media_identities.find_one({"canonical_id": loser})
        keeper_doc = await db.media_identities.find_one({"canonical_id": keeper})
        if not loser_doc or not keeper_doc:
            continue
        updates = merge_identity_fields(keeper_doc, loser_doc)
        for field, value in incoming_ids(loser_doc).items():
            if value is not None and keeper_doc.get(field) in (None, "", []):
                updates[field] = value
        aliases = list(dict.fromkeys([
            *(keeper_doc.get("aliases") or []),
            loser,
            *(loser_doc.get("aliases") or []),
        ]))
        updates["aliases"] = aliases
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        await db.media_identities.update_one({"canonical_id": keeper}, {"$set": updates})
        await remap_canonical_references(loser, keeper)
        await db.media_identities.delete_one({"canonical_id": loser})
        merged += 1
    return merged


async def persist_identity(item: Dict[str, Any]) -> Dict[str, Any]:
    matches = await _find_identity_matches(item)
    if matches:
        planning = []
        for match in matches:
            bridged = dict(match)
            bridged.update(incoming_ids(item))
            if item.get("title"):
                bridged["title"] = bridged.get("title") or item["title"]
                bridged["title_key"] = title_key(bridged.get("title"))
            if coerce_int(item.get("year")) is not None:
                bridged["year"] = coerce_int(item.get("year"))
            if item.get("media_type") or item.get("type"):
                bridged["media_type"] = normalize_media_type(item.get("media_type") or item.get("type"))
            planning.append(bridged)
        mapping = plan_identity_merges(planning)
        if mapping:
            await apply_identity_mapping(mapping)
            matches = await _find_identity_matches(item) or matches
        found = pick_keeper(matches)
        found = await db.media_identities.find_one({"canonical_id": found["canonical_id"]}) or found
        updates = merge_identity_fields(found, item)
        if updates:
            await db.media_identities.update_one(
                {"canonical_id": found["canonical_id"]},
                {"$set": updates},
            )
            found.update(updates)
        return found
    created = build_identity(item)
    await db.media_identities.insert_one(dict(created))
    return created


async def attach_canonical_ids(
    user_id: str,
    items: List[Dict[str, Any]],
    *,
    persist_history: bool = True,
) -> List[Dict[str, Any]]:
    from .history_normalizer import persist_normalized_history

    for item in items:
        identity = await persist_identity(item)
        item["canonical_media_id"] = identity["canonical_id"]
        if identity.get("tmdb_id") and not item.get("tmdb_id"):
            item["tmdb_id"] = identity["tmdb_id"]
        if persist_history:
            await persist_normalized_history(user_id, item, identity["canonical_id"])
    return items
