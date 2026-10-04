"""Coming continuations of titles the user likes: a new season, a sequel, a spin-off.

Gilbert, 2026-09-25: Upcoming is CineMind's first priority, and "an old series
with a coming season" counts as much as a new title - what matters is that it
is still to come and fits his taste, not whether the series or franchise is
old. Measured the same evening on his data ("Upcoming Tv Shows", read-only):

- 7 coming seasons of series he had watched (The Simpsons S38, The Rings of
  Power S3, Delicious in Dungeon S2 ...) were rejected as "already watched";
- 7 AniList sequels to anime he had watched (Reincarnated as a Sword S2,
  Shangri-La Frontier S3 ...) scored personal 4.4-5.2 but specific 0.0 - the
  link to the season he watched was never seen - and fell below the floor;
- 13 coming films in TMDb collections of films he liked (Avengers: Doomsday,
  Dune: Part Three, Avatar 4 ...) were never searched for at all.

A continuation row carries `continuation_of`: the liked title it continues (as
the taste profile names it), how (season / sequel / spin_off / side_story) and
the season. The ranking reads it as a franchise link to that liked title
(ranking_engine.continuation_link); the exclusions let a coming continuation
through "already watched" and "in the library" (exclusion_engine), since the
coming season is neither. The job's filters, the taste floor and the queue
rules apply to it exactly as to any other pick.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx

from providers.premieres import (
    ANILIST_GRAPHQL,
    CONCURRENCY,
    _tmdb_details,
    premiere_from_anilist,
    premiere_from_tmdb,
    premiere_label,
    today_iso,
)
from recommendation.media_identity import title_key

#: AniList relations that carry a story on from a title the viewer liked.
FORWARD_RELATIONS = {"SEQUEL": "sequel", "SPIN_OFF": "spin_off", "SIDE_STORY": "side_story"}
#: The same links read from the coming title back to what it continues.
BACKWARD_RELATIONS = {"PREQUEL": "sequel", "PARENT": "spin_off"}
#: Steps followed along a chain: season 1 -> season 2 (out) -> season 3 (coming) is two.
RELATION_DEPTH = 3
#: A film's collection hardly ever changes; a collection's parts gain dates as they are announced.
FILM_COLLECTION_CACHE_HOURS = 24 * 7
COLLECTION_PARTS_CACHE_HOURS = 12
RELATIONS_CACHE_HOURS = 24
#: An AniList sequel and TMDb's coming season of the same series are one premiere
#: when their dates are this close (AniList counts the Japanese broadcast day,
#: TMDb often the first English one).
SAME_SEASON_DAYS = 45

SERIES_BUCKETS = {"tv", "anime"}
FILM_BUCKETS = {"movie", "anime_movie"}

RELATIONS_QUERY = """
query ($ids: [Int]) {
  Page(perPage: 50) {
    media(id_in: $ids, type: ANIME) {
      id
      title { english romaji }
      relations {
        edges {
          relationType(version: 2)
          node {
            id type format status countryOfOrigin seasonYear genres averageScore popularity
            startDate { year month day }
            nextAiringEpisode { airingAt episode }
            title { english romaji }
          }
        }
      }
    }
  }
}
"""


def liked_roots(taste: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The liked titles a continuation can hang from, strongest first.

    The profile's own positives (taste_engine: evidence of at least
    POSITIVE_EVIDENCE - a rating, a rewatch, a finished run or an approval),
    never a rejected title. Watched is not liked, so a series sampled once has
    no continuation here.
    """
    rows = []
    for row in (taste or {}).get("liked_titles") or (taste or {}).get("high_confidence_positive_titles") or []:
        if not row.get("title") or row.get("decision") == "rejected":
            continue
        rows.append(row)
    return rows


def _bucket(row: Dict[str, Any]) -> str:
    from recommendation.taste_engine import media_bucket

    return str(row.get("media_type") or "") if row.get("media_type") in SERIES_BUCKETS | FILM_BUCKETS else media_bucket(row)


def _wants(media_types: Optional[Iterable[str]], *kinds: str) -> bool:
    wanted = {str(item or "").casefold() for item in (media_types or ["movie", "tv", "anime"])}
    aliases = {"movie": {"movie", "movies", "film"}, "tv": {"tv", "show", "series"}, "anime": {"anime"}}
    return any(wanted & aliases[kind] for kind in kinds)


def _root_fields(root: Dict[str, Any]) -> Dict[str, Any]:
    """What the liked row already knows about the series, so the coming season
    is compared on what the show is about rather than on a bare id."""
    return {name: root.get(name) for name in (
        "genres", "original_language", "country", "origin_countries", "overview", "tmdb_keywords",
        "cast", "creators", "companies", "studios", "tags",
    ) if root.get(name) not in (None, "", [], {})}


def _continuation(root: Dict[str, Any], relation: str, season: Optional[int] = None) -> Dict[str, Any]:
    return {
        "title": root.get("title"),
        "year": root.get("year"),
        "relation": relation,
        "season": season,
        "score": root.get("score"),
        "rating": root.get("rating"),
    }


async def _cached(key: str) -> Optional[Any]:
    from database import db

    now = datetime.now(timezone.utc).isoformat()
    doc = await db.provider_cache.find_one({"key": key, "expires_at": {"$gt": now}})
    return doc.get("payload") if doc else None


async def _store(key: str, payload: Any, hours: float) -> None:
    from database import db

    now = datetime.now(timezone.utc)
    await db.provider_cache.update_one(
        {"key": key},
        {"$set": {"key": key, "payload": payload, "updated_at": now.isoformat(),
                  "expires_at": (now + timedelta(hours=hours)).isoformat()}},
        upsert=True,
    )


async def series_continuations(roots: List[Dict[str, Any]], api_key: str,
                               now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """The coming season of every liked series TMDb has announced one for.

    Only a season after the first: a liked series whose own premiere is still
    to come is an approved request, not a continuation, and Up Coming shows it
    from Requests.
    """
    today = today_iso(now)
    series = [root for root in roots if root.get("tmdb_id") and _bucket(root) in SERIES_BUCKETS]
    if not series or not api_key:
        return []
    gate = asyncio.Semaphore(CONCURRENCY)
    found: List[Dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=10) as client:
        async def one(root: Dict[str, Any]) -> None:
            async with gate:
                details = await _tmdb_details(client, root["tmdb_id"], True, api_key)
            if not details or details.get("missing"):
                return
            premiere = premiere_from_tmdb(details, True, today)
            if not premiere or premiere.get("premiere_kind") != "season_premiere":
                return
            season = int(premiere.get("premiere_season") or 0)
            if season < 2:
                return
            anime = _bucket(root) == "anime"
            found.append({
                **_root_fields(root),
                "title": root["title"],
                "year": root.get("year"),
                "type": "anime" if anime else "show",
                "media_type": "anime" if anime else "tv",
                "tmdb_id": root["tmdb_id"],
                "source": "continuation",
                "collection": root.get("collection"),
                "continuation_of": _continuation(root, "season", season),
                "why": "Season %s of %s premieres %s." % (season, root["title"], premiere_label(premiere)),
                **premiere,
                "premiere_checked_at": (now or datetime.now(timezone.utc)).isoformat(),
            })

        await asyncio.gather(*(one(root) for root in series))
    return found


async def _film_collection(client: httpx.AsyncClient, tmdb_id: Any, api_key: str) -> Optional[Dict[str, Any]]:
    key = "continuation-film:%s" % tmdb_id
    cached = await _cached(key)
    if isinstance(cached, dict):
        return cached
    try:
        response = await client.get("https://api.themoviedb.org/3/movie/%s" % tmdb_id, params={"api_key": api_key})
    except httpx.HTTPError as exc:
        logging.warning("continuation lookup failed for film %s: %s", tmdb_id, exc.__class__.__name__)
        return None
    if response.status_code not in (200, 404):
        return None
    collection = ((response.json() or {}).get("belongs_to_collection") or {}) if response.status_code == 200 else {}
    payload = {"id": collection.get("id"), "name": collection.get("name")}
    await _store(key, payload, FILM_COLLECTION_CACHE_HOURS)
    return payload


PART_FIELDS = ("id", "title", "release_date", "poster_path", "backdrop_path", "overview", "genre_ids",
               "original_language", "vote_average", "vote_count", "popularity")


async def _collection_parts(client: httpx.AsyncClient, collection_id: Any, api_key: str) -> List[Dict[str, Any]]:
    key = "continuation-collection:%s" % collection_id
    cached = await _cached(key)
    if isinstance(cached, list):
        return cached
    try:
        response = await client.get("https://api.themoviedb.org/3/collection/%s" % collection_id,
                                    params={"api_key": api_key})
    except httpx.HTTPError as exc:
        logging.warning("collection %s failed: %s", collection_id, exc.__class__.__name__)
        return []
    if response.status_code != 200:
        return []
    parts = [{name: part.get(name) for name in PART_FIELDS} for part in (response.json() or {}).get("parts") or []]
    await _store(key, parts, COLLECTION_PARTS_CACHE_HOURS)
    return parts


async def film_continuations(roots: List[Dict[str, Any]], api_key: str,
                             now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Coming films in the TMDb collection of a liked film (Dune: Part Three for Dune)."""
    from providers.tmdb import _normalize_tmdb_result

    today = today_iso(now)
    films = [root for root in roots if root.get("tmdb_id") and _bucket(root) in FILM_BUCKETS]
    if not films or not api_key:
        return []
    liked_ids = {str(root["tmdb_id"]) for root in films}
    gate = asyncio.Semaphore(CONCURRENCY)
    by_collection: Dict[Any, Tuple[str, Dict[str, Any]]] = {}

    async with httpx.AsyncClient(timeout=10) as client:
        async def lookup(root: Dict[str, Any]) -> None:
            async with gate:
                collection = await _film_collection(client, root["tmdb_id"], api_key)
            if not collection or not collection.get("id"):
                return
            held = by_collection.get(collection["id"])
            # The strongest liked film in the collection is the one it continues.
            if held is None or float(root.get("score") or 0) > float(held[1].get("score") or 0):
                by_collection[collection["id"]] = (collection.get("name") or "", root)

        await asyncio.gather(*(lookup(root) for root in films))
        found: List[Dict[str, Any]] = []
        for collection_id, (name, root) in by_collection.items():
            async with gate:
                parts = await _collection_parts(client, collection_id, api_key)
            for part in parts:
                if str(part.get("id")) in liked_ids:
                    # A liked film that is itself still to come is an approved
                    # request; Up Coming shows it from Requests.
                    continue
                premiere = premiere_from_tmdb(part, False, today)
                if not premiere:
                    continue
                row = _normalize_tmdb_result(part, "movie", "continuation")
                genres = {str(item).casefold() for item in row.get("genres") or []}
                if "animation" in genres and str(row.get("original_language") or "") in {"ja", "zh", "cn"}:
                    # An anime film, as the anime-film discover lane marks one.
                    row.update({"type": "anime", "media_type": "anime", "format": "MOVIE"})
                row.update({
                    "collection": name or None,
                    "continuation_of": _continuation(root, "sequel"),
                    "why": "Next in %s after %s; out %s." % (name or "the series", root["title"], premiere_label(premiere)),
                    **premiere,
                    "premiere_checked_at": (now or datetime.now(timezone.utc)).isoformat(),
                })
                found.append(row)
    return found


def _titles(media: Dict[str, Any]) -> List[str]:
    names = (media or {}).get("title") or {}
    return [str(value) for value in (names.get("english"), names.get("romaji")) if value]


_YEAR_TAG = re.compile(r"\s*\((?:19|20)\d{2}\)\s*$")
_SEASON_TAG = re.compile(
    r"\s*(?::|-)?\s*(?:season\s*\d+|\d+(?:st|nd|rd|th)\s+season|part\s*\d+|cour\s*\d+|"
    r"\d+(?:st|nd|rd|th)\s+cour|(?<=\s)(?:ii|iii|iv|v|vi)\b)\s*$",
    re.IGNORECASE,
)


def match_keys(name: Optional[str]) -> set:
    """The ways a title can name the same series: as written, without a year tag
    ("Ranma1/2 (2024)" is Trakt's "Ranma1/2") and without a season tag."""
    text = str(name or "").strip()
    keys = {title_key(text)}
    for _ in range(3):
        # "Ranma1/2 (2024) Season 3": the season tag first, then the year under it.
        bare = _YEAR_TAG.sub("", _SEASON_TAG.sub("", text))
        keys.add(title_key(bare))
        if bare == text:
            break
        text = bare
    return {key for key in keys if key}


_FRANCHISE_PREFIX = re.compile(r"^(.{4,}?):\s")


def chain_keys(name: Optional[str]) -> set:
    """match_keys, plus the franchise before a subtitle. Read only along an AniList
    PREQUEL / PARENT chain, where the titles are already one story: "Sword Art
    Online: Alicization - War of Underworld Part 2" is a season of the liked
    "Sword Art Online" on TMDb, and SAO: Integral Domain continues it."""
    keys = match_keys(name)
    prefix = _FRANCHISE_PREFIX.match(str(name or "").strip())
    if prefix:
        keys |= match_keys(prefix.group(1))
    return keys


async def _relations(ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """AniList relations per id, cached a day; ids AniList did not answer for are left out."""
    found: Dict[int, Dict[str, Any]] = {}
    missing = []
    for media_id in ids:
        cached = await _cached("anilist-relations:%s" % media_id)
        if isinstance(cached, dict):
            found[media_id] = cached
        else:
            missing.append(media_id)
    if not missing:
        return found
    async with httpx.AsyncClient(timeout=25) as client:
        for start in range(0, len(missing), 50):
            batch = missing[start:start + 50]
            try:
                response = await client.post(ANILIST_GRAPHQL, json={"query": RELATIONS_QUERY, "variables": {"ids": batch}})
            except httpx.HTTPError as exc:
                logging.warning("AniList relations failed: %s", exc.__class__.__name__)
                break
            if response.status_code != 200:
                # 429 when the minute's budget is spent: what was found stands.
                logging.warning("AniList relations answered %s", response.status_code)
                break
            for media in (((response.json() or {}).get("data") or {}).get("Page") or {}).get("media") or []:
                payload = relations_payload(media)
                found[int(media["id"])] = payload
                await _store("anilist-relations:%s" % media["id"], payload, RELATIONS_CACHE_HOURS)
    return found


def relations_payload(media: Dict[str, Any]) -> Dict[str, Any]:
    """What _relations caches for one AniList title (also filled from AniList's
    announced list, which asks for the same relations: providers.anilist)."""
    return {
        "id": media["id"],
        "titles": _titles(media),
        "edges": [
            {"relation": edge.get("relationType"), "node": edge.get("node") or {}}
            for edge in ((media.get("relations") or {}).get("edges") or [])
            if (edge.get("node") or {}).get("type") == "ANIME"
        ],
    }


def _anilist_row(node: Dict[str, Any], premiere: Dict[str, Any], root: Dict[str, Any], relation: str,
                 now: Optional[datetime]) -> Optional[Dict[str, Any]]:
    from providers.anilist import parse_recommendation_media

    row = parse_recommendation_media(node)
    if not row:
        return None
    origin = str(node.get("countryOfOrigin") or "").upper()
    row["original_language"] = "zh" if origin == "CN" else ("ko" if origin == "KR" else "ja")
    row.update({
        "source": "continuation",
        "continuation_of": _continuation(root, relation),
        "why": "%s of %s; starts %s." % (
            {"sequel": "Sequel", "spin_off": "Spin-off", "side_story": "Side story"}.get(relation, "Continuation"),
            root["title"], premiere_label(premiere)),
        **premiere,
        "premiere_checked_at": (now or datetime.now(timezone.utc)).isoformat(),
    })
    return row


def _by_match_key(roots: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Liked titles by every key they answer to; the stronger title keeps a shared key."""
    out: Dict[str, Dict[str, Any]] = {}
    for root in sorted(roots, key=lambda row: -float(row.get("score") or 0)):
        for key in match_keys(root.get("title")):
            out.setdefault(key, root)
    return out


def liked_anime_ids(roots: List[Dict[str, Any]], rows: Iterable[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """AniList ids of the liked anime, found through the user's own rows.

    The profile names a liked title but keeps no AniList id, and most of this
    viewer's anime reached it through Trakt, Simkl and Plex. A history or
    rating row with an AniList id stands for the liked title with the same
    name or the same TMDb id.
    """
    anime = [root for root in roots if _bucket(root) in {"anime", "anime_movie"}]
    by_title = _by_match_key(anime)
    by_tmdb = {str(root["tmdb_id"]): root for root in anime if root.get("tmdb_id")}
    out: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        raw = row.get("anilist_id")
        try:
            anilist_id = int(raw)
        except (TypeError, ValueError):
            continue
        root = next((by_title[key] for key in match_keys(row.get("title")) if key in by_title), None) \
            or by_tmdb.get(str(row.get("tmdb_id") or ""))
        if root is not None and anilist_id not in out:
            out[anilist_id] = root
    return out


async def anime_continuations(roots_by_id: Dict[int, Dict[str, Any]], seen_ids: Iterable[int] = (),
                              now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Coming sequels, spin-offs and side stories of liked anime, along AniList's relations.

    A chain is followed through what has already aired (season 2 finished,
    season 3 announced), up to RELATION_DEPTH steps from the liked title.
    """
    today = today_iso(now)
    known = {int(value) for value in seen_ids}
    frontier = dict(roots_by_id)
    visited = set(frontier)
    found: Dict[int, Dict[str, Any]] = {}
    for _depth in range(RELATION_DEPTH):
        if not frontier:
            break
        relations = await _relations(sorted(frontier))
        following: Dict[int, Dict[str, Any]] = {}
        for media_id, root in frontier.items():
            for edge in (relations.get(media_id) or {}).get("edges") or []:
                relation = FORWARD_RELATIONS.get(str(edge.get("relation") or ""))
                node = edge.get("node") or {}
                if not relation or not node.get("id"):
                    continue
                node_id = int(node["id"])
                premiere = premiere_from_anilist(node, today)
                if premiere and node_id not in known and node_id not in found:
                    row = _anilist_row(node, premiere, root, relation, now)
                    if row:
                        found[node_id] = row
                if node_id not in visited and node.get("status") in {"FINISHED", "RELEASING"} and relation != "side_story":
                    visited.add(node_id)
                    following[node_id] = root
        frontier = following
    return list(found.values())


async def link_anilist_candidates(rows: List[Dict[str, Any]], roots: List[Dict[str, Any]],
                                  now: Optional[datetime] = None) -> int:
    """Mark AniList candidates that continue a liked title; returns how many.

    Walks each coming AniList title back along PREQUEL / PARENT to what it
    continues, and matches every step by name against the liked titles - most
    of them came from TMDb, Trakt or Simkl, so an AniList id is rarely there
    to match. "Shangri-La Frontier Season 3" -> "... Season 2" -> "Shangri-La
    Frontier", which the viewer rated.
    """
    by_title = _by_match_key(roots)
    pending: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        if row.get("continuation_of") or row.get("tmdb_id") or not row.get("premiere_date"):
            continue
        try:
            pending[int(row["anilist_id"])] = row
        except (KeyError, TypeError, ValueError):
            continue
    if not pending or not by_title:
        return 0
    marked = 0
    # Per AniList id: the candidates walking through it, and how the chain reached each.
    frontier: Dict[int, List[Tuple[Dict[str, Any], str]]] = {
        media_id: [(row, "sequel")] for media_id, row in pending.items()}
    visited: Dict[int, set] = {media_id: {id(row)} for media_id, row in pending.items()}
    for _depth in range(RELATION_DEPTH):
        if not frontier:
            break
        relations = await _relations(sorted(frontier))
        following: Dict[int, List[Tuple[Dict[str, Any], str]]] = {}
        for media_id, walkers in frontier.items():
            for row, how in walkers:
                if row.get("continuation_of"):
                    continue
                for edge in (relations.get(media_id) or {}).get("edges") or []:
                    relation = BACKWARD_RELATIONS.get(str(edge.get("relation") or ""))
                    node = edge.get("node") or {}
                    if not relation or not node.get("id"):
                        continue
                    kind = "spin_off" if relation == "spin_off" or how == "spin_off" else "sequel"
                    root = next((by_title[key] for name in _titles(node) for key in chain_keys(name)
                                 if key in by_title), None)
                    if root is not None:
                        row["continuation_of"] = _continuation(root, kind)
                        # An announced AniList title has hardly any metadata yet (ONE PIECE
                        # FILM: GOD VALLEY had no genres) and scored far below the floor
                        # while continuing a 10/10 series; it is about what its root is about.
                        for name, value in _root_fields(root).items():
                            if row.get(name) in (None, "", [], {}):
                                row[name] = value
                        marked += 1
                        break
                    node_id = int(node["id"])
                    if id(row) not in visited.setdefault(node_id, set()):
                        visited[node_id].add(id(row))
                        following.setdefault(node_id, []).append((row, kind))
        frontier = following
    return marked


def _same_premiere(row: Dict[str, Any], twin: Dict[str, Any]) -> bool:
    """Whether an AniList season and a TMDb season premiere together. An AniList
    date announced by year or month is the last day of that period, so the
    TMDb day only has to fall inside it."""
    date, other = str(row.get("premiere_date") or "")[:10], str(twin.get("premiere_date") or "")[:10]
    if row.get("premiere_precision") == "year":
        return bool(date) and date[:4] == other[:4]
    if row.get("premiere_precision") == "month":
        return bool(date) and date[:7] == other[:7]
    apart = _days_apart(date, other)
    return apart is not None and apart <= SAME_SEASON_DAYS


def _days_apart(left: Any, right: Any) -> Optional[int]:
    try:
        first = datetime.fromisoformat(str(left)[:10])
        second = datetime.fromisoformat(str(right)[:10])
    except ValueError:
        return None
    return abs((first - second).days)


def drop_duplicate_seasons(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One row per coming season: TMDb's season of a liked series wins over AniList's entry for it.

    TMDb keeps every season under the series' own id - the id the history,
    the library and MediaManager know - while AniList gives each season its
    own entry. Both reached the pool for Reincarnated as a Sword S2
    (2026-09-30 on AniList, 2026-10-08 on TMDb). The AniList id is kept on
    the TMDb row, so the queue still recognises either.
    """
    seasons: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        info = row.get("continuation_of") or {}
        if row.get("tmdb_id") and info.get("relation") == "season":
            for key in match_keys(info.get("title")):
                seasons.setdefault(key, row)
    if not seasons:
        return rows
    kept = []
    for row in rows:
        info = row.get("continuation_of") or {}
        twin = next((seasons[key] for key in match_keys(info.get("title")) if key in seasons), None) \
            if info and not row.get("tmdb_id") and row.get("anilist_id") else None
        if twin is not None:
            if _same_premiere(row, twin):
                twin.setdefault("anilist_id", row.get("anilist_id"))
                continue
        kept.append(row)
    return kept


def merge_continuations(extra: List[Dict[str, Any]], found: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The continuation rows first, then the rest of the pool.

    merge_candidate_sources keeps the first row of a title, so the coming season
    of a liked series found by a discover lane (The Simpsons, from the air-date
    lane) is the continuation row, with the discover lane kept among its
    sources, rather than an anonymous "already watched" one.
    """
    from recommendation.exclusion_engine import identity_keys

    if not found:
        return extra
    index: Dict[tuple, Dict[str, Any]] = {}
    for row in found:
        for key in identity_keys(row):
            index.setdefault(key, row)
    for row in extra:
        twin = next((index[key] for key in identity_keys(row) if key in index), None)
        if twin is not None and not row.get("continuation_of"):
            row["continuation_of"] = twin["continuation_of"]
    return list(found) + list(extra)


async def gather_continuations(
    taste: Optional[Dict[str, Any]],
    rows: Iterable[Dict[str, Any]],
    api_key: Optional[str],
    media_types: Optional[Iterable[str]] = None,
    now: Optional[datetime] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Every coming continuation of a liked title the job can take, and a count per kind.

    `rows` are the user's history and rating rows (for the AniList ids of the
    liked anime). Films are only looked for when the job takes films or anime,
    series when it takes TV or anime.
    """
    roots = liked_roots(taste)
    report = {"liked_titles": len(roots), "seasons": 0, "films": 0, "anime": 0}
    if not roots:
        return [], report
    found: List[Dict[str, Any]] = []
    rows = list(rows)
    try:
        if _wants(media_types, "tv", "anime") and api_key:
            seasons = await series_continuations(roots, api_key, now)
            report["seasons"] = len(seasons)
            found.extend(seasons)
        if _wants(media_types, "movie", "anime") and api_key:
            films = await film_continuations(roots, api_key, now)
            report["films"] = len(films)
            found.extend(films)
        if _wants(media_types, "tv", "anime", "movie"):
            by_id = liked_anime_ids(roots, rows)
            seen = [row.get("anilist_id") for row in rows if str(row.get("anilist_id") or "").isdigit()]
            anime = await anime_continuations(by_id, seen, now)
            report["anime"] = len(anime)
            found.extend(anime)
    except Exception as exc:  # a provider outage must not cost the run its other lanes
        logging.warning("continuations failed: %s", exc.__class__.__name__)
    return drop_duplicate_seasons(found), report
