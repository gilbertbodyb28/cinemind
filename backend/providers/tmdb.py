"""TMDb search and poster enrichment."""

from typing import Any, Dict, List, Optional, Tuple
import asyncio
import logging
import re

import httpx

from config import PLACEHOLDER_POSTER, TMDB_KEY


def _clean_title(title: str) -> str:
    clean = re.sub(r"\s*[\(\[:–-]\s*(season|part|vol\.?|volume)\b.*$", "", title, flags=re.I)
    clean = re.sub(r"\s*\(.*?\)\s*$", "", clean).strip()
    return clean or title


async def tmdb_lookup(
    hc: httpx.AsyncClient,
    title: str,
    year: Optional[int],
    type_: str,
    api_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    key = TMDB_KEY if api_key is None else api_key
    if not key:
        return None
    kind = str(type_ or "").casefold()
    endpoint = "tv" if kind in {"show", "tv", "series", "anime"} else "movie"
    year_param = "first_air_date_year" if endpoint == "tv" else "year"
    clean = _clean_title(title)
    attempts = [{"query": title, year_param: year} if year else None, {"query": title}]
    if clean != title:
        attempts.append({"query": clean})
    for params in attempts:
        if params is None:
            continue
        try:
            response = await hc.get(
                f"https://api.themoviedb.org/3/search/{endpoint}",
                params={"api_key": key, **params},
            )
            results = response.json().get("results") or [] if response.status_code == 200 else []
        except Exception as exc:
            logging.warning("TMDB lookup failed for %s: %s", title, exc)
            return None
        if results:
            top = results[0]
            out: Dict[str, Any] = {"tmdb_id": top.get("id")}
            if top.get("poster_path"):
                out["poster"] = f"https://image.tmdb.org/t/p/w500{top['poster_path']}"
            if top.get("backdrop_path"):
                out["backdrop"] = f"https://image.tmdb.org/t/p/w1280{top['backdrop_path']}"
            if top.get("vote_average"):
                out["tmdb_rating"] = round(float(top["vote_average"]), 1)
            return out
    return None


async def tmdb_details(
    hc: httpx.AsyncClient,
    tmdb_id: int,
    type_: str,
    api_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    key = TMDB_KEY if api_key is None else api_key
    if not key:
        return None
    kind = str(type_ or "").casefold()
    endpoint = "tv" if kind in {"show", "tv", "series", "anime"} else "movie"
    try:
        response = await hc.get(
            f"https://api.themoviedb.org/3/{endpoint}/{tmdb_id}",
            params={"api_key": key},
        )
        if response.status_code != 200:
            return None
        data = response.json()
        out: Dict[str, Any] = {"tmdb_id": tmdb_id}
        if data.get("poster_path"):
            out["poster"] = f"https://image.tmdb.org/t/p/w500{data['poster_path']}"
        if data.get("backdrop_path"):
            out["backdrop"] = f"https://image.tmdb.org/t/p/w1280{data['backdrop_path']}"
        return out
    except Exception as exc:
        logging.warning("TMDB details failed for %s: %s", tmdb_id, exc)
        return None


async def enrich_history_posters(
    items: List[Dict[str, Any]],
    api_key: Optional[str] = None,
    *,
    tvdb_api_key: Optional[str] = None,
) -> None:
    key = TMDB_KEY if api_key is None else api_key
    if not key and not tvdb_api_key:
        return
    targets = [item for item in items if not item.get("poster")]
    if not targets:
        return
    sem = asyncio.Semaphore(6)

    async def one(hc: httpx.AsyncClient, item: Dict[str, Any]):
        async with sem:
            meta = None
            if key:
                meta = await tmdb_details(hc, item["tmdb_id"], item.get("type", "movie"), key) if item.get("tmdb_id") else None
                if not (meta and meta.get("poster")):
                    meta = await tmdb_lookup(hc, item["title"], item.get("year"), item.get("type", "movie"), key)
            if meta:
                if meta.get("poster"):
                    item["poster"] = meta["poster"]
                if meta.get("tmdb_id"):
                    item["tmdb_id"] = meta["tmdb_id"]
                if meta.get("backdrop"):
                    item["backdrop"] = meta["backdrop"]
            if not item.get("poster") and tvdb_api_key:
                from .tvdb import tvdb_poster_lookup

                fallback = await tvdb_poster_lookup(
                    item.get("title") or "",
                    item.get("year"),
                    item.get("type") or item.get("media_type") or "movie",
                    tvdb_api_key,
                )
                if fallback and fallback.get("poster"):
                    item["poster"] = fallback["poster"]
                    if fallback.get("tvdb_id"):
                        item["tvdb_id"] = fallback["tvdb_id"]

    async with httpx.AsyncClient(timeout=10) as hc:
        await asyncio.gather(*[one(hc, item) for item in targets])


async def enrich_with_tmdb(
    recs: List[Dict[str, Any]],
    api_key: Optional[str] = None,
    *,
    tvdb_api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    key = TMDB_KEY if api_key is None else api_key
    if not key and not tvdb_api_key:
        return recs
    targets = [
        rec for rec in recs
        if not rec.get("poster") or rec["poster"] == PLACEHOLDER_POSTER
    ]
    if not targets:
        return recs
    async with httpx.AsyncClient(timeout=10) as hc:
        results = []
        if key:
            results = await asyncio.gather(
                *[tmdb_lookup(hc, rec["title"], rec.get("year"), rec.get("type", "movie"), key) for rec in targets]
            )
        else:
            results = [None] * len(targets)
    for rec, meta in zip(targets, results):
        if meta:
            rec.update({k: v for k, v in meta.items() if v})
        if (not rec.get("poster") or rec.get("poster") == PLACEHOLDER_POSTER) and tvdb_api_key:
            from .tvdb import tvdb_poster_lookup

            fallback = await tvdb_poster_lookup(
                rec.get("title") or "",
                rec.get("year"),
                rec.get("type") or rec.get("media_type") or "movie",
                tvdb_api_key,
            )
            if fallback and fallback.get("poster"):
                rec["poster"] = fallback["poster"]
                if fallback.get("tvdb_id"):
                    rec["tvdb_id"] = fallback["tvdb_id"]
    return recs


TMDB_MOVIE_GENRES = {
    "action": 28,
    "adventure": 12,
    "animation": 16,
    "anime": 16,
    "donghua": 16,
    "comedy": 35,
    "crime": 80,
    "documentary": 99,
    "drama": 18,
    "family": 10751,
    "fantasy": 14,
    "history": 36,
    "horror": 27,
    "kid": 10751,
    "kids": 10751,
    "music": 10402,
    "mystery": 9648,
    "romance": 10749,
    "sci-fi": 878,
    "science fiction": 878,
    "science-fiction": 878,
    "thriller": 53,
    "war": 10752,
    "western": 37,
}

# TV genre IDs differ from movies for several buckets (Action & Adventure, Sci-Fi & Fantasy, Kids).
TMDB_TV_GENRES = {
    "action": 10759,
    "adventure": 10759,
    "animation": 16,
    "anime": 16,
    "donghua": 16,
    "comedy": 35,
    "crime": 80,
    "documentary": 99,
    "drama": 18,
    "family": 10751,
    "fantasy": 10765,
    "history": 18,
    "horror": 9648,
    "kid": 10762,
    "kids": 10762,
    "mystery": 9648,
    "romance": 10749,
    "sci-fi": 10765,
    "science fiction": 10765,
    "science-fiction": 10765,
    "talk": 10767,
    "talk show": 10767,
    "reality": 10764,
    "thriller": 9648,
    "war": 10768,
    "western": 37,
}


TMDB_GENRE_ID_NAMES = {
    28: "Action",
    12: "Adventure",
    16: "Animation",
    35: "Comedy",
    80: "Crime",
    99: "Documentary",
    18: "Drama",
    10751: "Family",
    14: "Fantasy",
    36: "History",
    27: "Horror",
    10402: "Music",
    9648: "Mystery",
    10749: "Romance",
    878: "Sci-Fi",
    53: "Thriller",
    10752: "War",
    37: "Western",
    10759: "Action",
    10762: "Kids",
    10764: "Reality",
    10765: "Sci-Fi",
    10767: "Talk",
    10768: "War",
}


def _genre_ids(names: Optional[List[str]], media_type: str = "movie") -> str:
    """Map include genres to TMDb IDs.

    TMDb treats comma as AND and pipe as OR. Include lists are OR — requiring
    Action AND Sci-Fi AND Animation simultaneously returns almost nothing.
    """
    from recommendation.filter_engine import canonical_genres

    table = TMDB_TV_GENRES if media_type in {"tv", "show", "anime"} else TMDB_MOVIE_GENRES
    ids: List[str] = []
    seen = set()
    for name in names or []:
        # "Science-Fiction" (Trakt) and "Sci-Fi & Fantasy" (TMDb TV) must map too.
        for part in sorted(canonical_genres([name])):
            mapped = table.get(part)
            if mapped and mapped not in seen:
                seen.add(mapped)
                ids.append(str(mapped))
    return "|".join(ids)


def _raw_genre_ids(row: Dict[str, Any]) -> List[int]:
    """TMDb genre ids as sent. 10759 and 10765 name two genres each, which the
    display names above cannot hold; the filter reads both halves from the id."""
    ids: List[int] = []
    for raw in list(row.get("genre_ids") or []) + [item.get("id") for item in row.get("genres") or [] if isinstance(item, dict)]:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value not in ids:
            ids.append(value)
    return ids


def _genres_from_ids(row: Dict[str, Any]) -> List[str]:
    names = []
    for raw in row.get("genre_ids") or []:
        try:
            mapped = TMDB_GENRE_ID_NAMES.get(int(raw))
        except (TypeError, ValueError):
            mapped = None
        if mapped and mapped not in names:
            names.append(mapped)
    for item in row.get("genres") or []:
        if isinstance(item, dict):
            mapped = TMDB_GENRE_ID_NAMES.get(item.get("id")) or item.get("name")
        else:
            mapped = str(item)
        if mapped and mapped not in names:
            names.append(mapped)
    return names


def _normalize_tmdb_result(row: Dict[str, Any], media_type: str, source: str) -> Dict[str, Any]:
    title = row.get("title") or row.get("name") or "Unknown"
    date = row.get("release_date") or row.get("first_air_date") or ""
    year = int(date[:4]) if len(date) >= 4 and date[:4].isdigit() else None
    popularity = float(row.get("popularity") or 0)
    vote_average = float(row.get("vote_average") or 0)
    origins = [str(item) for item in (row.get("origin_country") or []) if item]
    language = row.get("original_language")
    genres = _genres_from_ids(row)
    is_anime = (
        media_type == "tv"
        and language in {"ja", "zh", "ko"}
        and any(str(g).casefold() == "animation" for g in genres)
    )
    return {
        "title": title,
        "year": year,
        "type": "anime" if is_anime else ("show" if media_type == "tv" else "movie"),
        "media_type": "anime" if is_anime else ("tv" if media_type == "tv" else "movie"),
        "genres": genres,
        "tmdb_genre_ids": _raw_genre_ids(row),
        "synopsis": row.get("overview") or "",
        "tmdb_rating": round(vote_average, 1),
        "vote_count": row.get("vote_count"),
        "popularity": popularity,
        "tmdb_id": row.get("id"),
        "poster": f"https://image.tmdb.org/t/p/w500{row['poster_path']}" if row.get("poster_path") else None,
        "backdrop": f"https://image.tmdb.org/t/p/w1280{row['backdrop_path']}" if row.get("backdrop_path") else None,
        "original_language": language,
        "origin_countries": origins,
        "country": origins[0] if origins else None,
        "release_date": date[:10] if len(date) >= 10 else (date or None),
        "first_air_date": row.get("first_air_date"),
        "source": source,
        # Prefer popularity for upcoming / low-vote titles so junk 9.0/2-vote rows don't win.
        "candidate_score": round(popularity / 10.0 + vote_average, 3),
    }


async def _tmdb_page(
    path: str,
    params: Dict[str, Any],
    api_key: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    """One page of results plus the query's real page count.

    A filtered discover query often has only a handful of pages. Without
    total_pages the page cursor walks straight past the end and every later
    run gets an empty body back for ever.
    """
    key = api_key or TMDB_KEY
    if not key:
        return [], 0
    from datetime import datetime, timedelta, timezone
    from database import db

    cache_key = "tmdbpage:" + path + ":" + "&".join(f"{k}={params[k]}" for k in sorted(params))
    now = datetime.now(timezone.utc)
    cached = await db.provider_cache.find_one({"key": cache_key, "expires_at": {"$gt": now.isoformat()}})
    if cached and isinstance(cached.get("payload"), dict):
        body = cached["payload"]
        return body.get("results") or [], int(body.get("total_pages") or 0)
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(f"https://api.themoviedb.org/3/{path}", params={"api_key": key, **params})
        if response.status_code != 200:
            return [], 0
        body = response.json() or {}
        payload = {"results": body.get("results") or [], "total_pages": int(body.get("total_pages") or 0)}
        await db.provider_cache.update_one(
            {"key": cache_key},
            {"$set": {
                "key": cache_key,
                "payload": payload,
                "expires_at": (now + timedelta(hours=6)).isoformat(),
                "updated_at": now.isoformat(),
            }},
            upsert=True,
        )
        return payload["results"], payload["total_pages"]
    except Exception as exc:
        from jobs.engine import safe_provider_error

        logging.warning("TMDb %s failed: %s", path, safe_provider_error(exc))
        return [], 0


async def _tmdb_list(path: str, params: Dict[str, Any], api_key: Optional[str] = None) -> List[Dict[str, Any]]:
    key = api_key or TMDB_KEY
    if not key:
        return []
    from datetime import datetime, timedelta, timezone
    from database import db

    cache_key = "tmdb:" + path + ":" + "&".join(f"{k}={params[k]}" for k in sorted(params))
    now = datetime.now(timezone.utc)
    cached = await db.provider_cache.find_one({"key": cache_key, "expires_at": {"$gt": now.isoformat()}})
    if cached and isinstance(cached.get("payload"), list):
        return cached["payload"]
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(f"https://api.themoviedb.org/3/{path}", params={"api_key": key, **params})
        if response.status_code != 200:
            return []
        payload = response.json().get("results") or []
        await db.provider_cache.update_one(
            {"key": cache_key},
            {"$set": {
                "key": cache_key,
                "payload": payload,
                "expires_at": (now + timedelta(hours=6)).isoformat(),
                "updated_at": now.isoformat(),
            }},
            upsert=True,
        )
        return payload
    except Exception as exc:
        from jobs.engine import safe_provider_error

        logging.warning("TMDb %s failed: %s", path, safe_provider_error(exc))
        return []


async def _keyword_ids(names: Optional[List[str]], api_key: Optional[str] = None) -> str:
    """Resolve keyword names to TMDb keyword ids (pipe-joined = OR)."""
    ids: List[str] = []
    for name in names or []:
        query = str(name).strip()
        if not query:
            continue
        rows = await _tmdb_list("search/keyword", {"query": query}, api_key=api_key)
        for row in rows[:1]:
            if row.get("id") and str(row["id"]) not in ids:
                ids.append(str(row["id"]))
    return "|".join(ids)


#: TMDb refuses page numbers above this.
TMDB_MAX_PAGE = 500
# How deep the rotating cursor is allowed to go. Walking forward every run keeps
# a job from returning the same titles for ever, but nothing stopped it: Gilbert's
# jobs had reached page 191 of a popularity-sorted query - roughly the
# 3,800th most popular title - and were recommending Nigerian and Tagalog soaps
# nobody had heard of. Freshness comes from the genre-combination, taste-seeded
# and provider lanes, not from depth, so the cursor now cycles inside the part of
# the catalogue where the ranking signals still mean something.
TMDB_CURSOR_PAGES = 25

# Unscripted TV formats. TMDb tags a late-night or sketch show simply "Comedy",
# so no ranking signal separates it from scripted comedy - it has to be kept out
# of the lane in the first place, and only for viewers whose own history shows
# no interest in it.
UNSCRIPTED_TV_GENRE_IDS = {
    10767: ("talk", "talk show", "talk-show"),
    10763: ("news",),
    10764: ("reality",),
    10766: ("soap",),
}


def unwanted_tv_genres(taste: Optional[Dict[str, Any]]) -> str:
    """TMDb `without_genres` for formats this viewer has never engaged with."""
    if not taste:
        return ""
    profile = {str(name).casefold(): row for name, row in (taste.get("genres") or {}).items()}
    if not profile:
        return ""
    unwanted = []
    for genre_id, aliases in UNSCRIPTED_TV_GENRE_IDS.items():
        rows = [profile[alias] for alias in aliases if alias in profile]
        # "Never watched" and "watched once by accident" both count as no interest.
        if not rows or all(row.get("affinity", 0) <= 0.02 for row in rows):
            unwanted.append(str(genre_id))
    return ",".join(unwanted)


def _window_is_upcoming(filters: Dict[str, Any]) -> bool:
    """True when the job asks for titles that have not been released yet."""
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc)
    start = filters.get("min_release_date") or (
        f"{int(filters['min_year'])}-01-01" if filters.get("min_year") else None
    )
    if not start:
        return False
    return str(start)[:10] > today.date().isoformat()


def default_vote_floor(filters: Dict[str, Any]) -> Optional[int]:
    """vote_count.gte for a discover query whose job names no vote floor.

    Established catalogue skips zero-vote noise at 50 votes. That rule used to
    compare against the literal year 2025, so in 2026 a window opening in 2024
    still demanded 50 votes: 176 sci-fi/fantasy series instead of 468. The
    window is now measured against the current year. A lane can lower the floor
    (`discover_vote_floor`): only 19 Chinese animated series on all of TMDb have
    50 votes, so donghua could not come through it at all.
    """
    from datetime import datetime, timezone

    if filters.get("discover_vote_floor") is not None:
        return int(filters["discover_vote_floor"]) or None
    if filters.get("min_release_date"):
        return None
    min_year = filters.get("min_year")
    if not min_year:
        return 50
    this_year = datetime.now(timezone.utc).year
    if int(min_year) >= this_year - 1:
        return None
    if int(min_year) >= this_year - 2:
        return 10
    return 50


def discover_page_span(job: Dict[str, Any]) -> int:
    """How many pages one run walks — 20 rows per page."""
    limit = max(int(job.get("candidate_limit") or 40), int(job.get("final_recommendation_limit") or 8))
    return min(10, max(1, (limit + 19) // 20))


async def tmdb_discover(
    job: Dict[str, Any],
    media_type: str,
    api_key: Optional[str] = None,
    start_page: int = 1,
    taste: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    filters = job.get("filters") or {}
    endpoint = "tv" if media_type in {"tv", "show", "anime"} else "movie"
    limit = max(int(job.get("candidate_limit") or 40), int(job.get("final_recommendation_limit") or 8))
    # TMDb returns 20 per page; cap pages so a 100-limit job actually asks for ~100 rows.
    max_pages = discover_page_span(job)
    # Every run starts where the last one stopped. Without this the same first
    # pages come back for ever, every title is already requested, and the job
    # accepts nothing no matter how long it runs.
    first_page = max(1, int(start_page or 1))
    base: Dict[str, Any] = {"sort_by": "popularity.desc", "include_adult": "false"}
    if endpoint == "tv":
        excluded = unwanted_tv_genres(taste)
        if excluded:
            base["without_genres"] = excluded
    genre_ids = _genre_ids(filters.get("include_genres"), endpoint)
    if genre_ids:
        base["with_genres"] = genre_ids
    min_date = filters.get("min_release_date")
    max_date = filters.get("max_release_date")
    if min_date:
        key = "first_air_date.gte" if endpoint == "tv" else "primary_release_date.gte"
        base[key] = str(min_date)[:10]
    elif filters.get("min_year"):
        key = "first_air_date.gte" if endpoint == "tv" else "primary_release_date.gte"
        base[key] = f"{int(filters['min_year'])}-01-01"
    if max_date:
        key = "first_air_date.lte" if endpoint == "tv" else "primary_release_date.lte"
        base[key] = str(max_date)[:10]
    elif filters.get("max_year"):
        key = "first_air_date.lte" if endpoint == "tv" else "primary_release_date.lte"
        base[key] = f"{int(filters['max_year'])}-12-31"
    if filters.get("min_rating") is not None and not _window_is_upcoming(filters):
        # Unreleased titles carry vote_average 0.0, so a rating floor on an
        # upcoming window matches nothing at all and the job silently returns 0.
        base["vote_average.gte"] = filters["min_rating"]
    if filters.get("min_vote_count") is not None:
        base["vote_count.gte"] = filters["min_vote_count"]
    else:
        floor = default_vote_floor(filters)
        if floor:
            base["vote_count.gte"] = floor
    languages = []
    if filters.get("languages"):
        languages = [str(item) for item in filters["languages"] if item]
    elif filters.get("language"):
        languages = [str(filters["language"])]
    countries = []
    if filters.get("countries"):
        countries = [str(item) for item in filters["countries"] if item]
    elif filters.get("country"):
        countries = [str(filters["country"])]
    country_code = None
    for item in countries:
        key = str(item).strip().upper()
        if key in {"US", "USA", "UNITED STATES"}:
            country_code = "US"
            break
        if len(key) == 2:
            country_code = key
            break
    if country_code:
        base["with_origin_country"] = country_code

    collected: List[Dict[str, Any]] = []
    seen_ids = set()

    async def _collect(extra_params: Dict[str, Any], cap: Optional[int] = None, tags: Optional[List[str]] = None) -> None:
        nonlocal collected
        ceiling = min(limit, cap) if cap else limit
        # Learned from the first response. Until then assume TMDb's hard ceiling.
        last_page = TMDB_MAX_PAGE
        for step in range(max_pages):
            page = ((first_page - 1 + step) % max(1, min(last_page, TMDB_CURSOR_PAGES))) + 1
            params = {**base, **extra_params, "page": page}
            params = {key: value for key, value in params.items() if value is not None}
            rows, total_pages = await _tmdb_page(f"discover/{endpoint}", params, api_key=api_key)
            if total_pages:
                last_page = total_pages
            if not rows and step == 0 and total_pages and page > total_pages:
                # The stored cursor had run past the end of this query. Wrap and retry
                # once, so a job can never be stranded on a page that does not exist.
                page = ((first_page - 1) % max(1, min(total_pages, TMDB_CURSOR_PAGES))) + 1
                params = {**base, **extra_params, "page": page}
                params = {key: value for key, value in params.items() if value is not None}
                rows, _ = await _tmdb_page(f"discover/{endpoint}", params, api_key=api_key)
            if not rows:
                break
            for row in rows:
                tid = row.get("id")
                if tid in seen_ids:
                    continue
                seen_ids.add(tid)
                normalized = _normalize_tmdb_result(row, endpoint, "tmdb_discover")
                if tags:
                    normalized["tags"] = sorted({*(normalized.get("tags") or []), *tags})
                collected.append(normalized)
                if len(collected) >= ceiling:
                    return

    keyword_ids = await _keyword_ids(filters.get("keywords"), api_key=api_key)

    async def _collect_lanes(extra: Dict[str, Any], cap: Optional[int] = None) -> None:
        # Keywords widen the job: the keyword lane runs first with a reserved
        # quota so the popular genre lane cannot fill the limit on its own.
        if keyword_ids:
            without_genres = {"with_keywords": keyword_ids}
            if "with_genres" in base:
                without_genres["with_genres"] = None
            keyword_cap = len(collected) + max(5, limit // 4)
            await _collect(
                {**extra, **without_genres},
                cap=min(keyword_cap, cap) if cap else keyword_cap,
                tags=[str(item).casefold() for item in (filters.get("keywords") or [])],
            )
        await _collect(extra, cap=cap)

    if languages:
        # Each language gets its share first. Walking them in order let the first
        # one fill the whole limit: the anime lane asks for ja then zh, Japanese
        # animation always has enough pages, and donghua was never fetched at all.
        share = max(1, -(-limit // len(languages)))
        for lang in languages:
            await _collect_lanes({"with_original_language": lang}, cap=len(collected) + share)
        for lang in languages:
            if len(collected) >= limit:
                break
            await _collect_lanes({"with_original_language": lang})
    else:
        await _collect_lanes({})
    return collected[:limit]


def related_seeds(
    history: List[Dict[str, Any]],
    taste: Optional[Dict[str, Any]] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """The titles worth asking TMDb "more like this" about.

    Taking the first rows of `history` seeded every similarity query from
    whatever the database returned first. The taste profile knows which titles
    actually carry evidence, so ask about those instead.
    """
    seeds: List[Dict[str, Any]] = []
    seen = set()
    for item in list((taste or {}).get("seed_docs") or []) + list(history or []):
        tid = item.get("tmdb_id")
        if not tid or tid in seen:
            continue
        seen.add(tid)
        seeds.append(item)
        if len(seeds) >= limit:
            break
    return seeds


async def tmdb_related(
    history: List[Dict[str, Any]],
    kind: str,
    api_key: Optional[str] = None,
    taste: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    seeds = related_seeds(history, taste)
    out: List[Dict[str, Any]] = []
    for item in seeds:
        endpoint = "tv" if (item.get("type") or item.get("media_type")) in {"show", "tv", "anime"} else "movie"
        rows = await _tmdb_list(f"{endpoint}/{item['tmdb_id']}/{kind}", {"page": 1}, api_key=api_key)
        for row in rows[:12]:
            normalized = _normalize_tmdb_result(row, endpoint, f"tmdb_{kind}")
            normalized["source_seed"] = item.get("title")
            normalized["why"] = ""
            out.append(normalized)
    return out


async def taste_seeded_discover(
    job: Dict[str, Any],
    taste: Dict[str, Any],
    api_key: Optional[str] = None,
    per_lane: int = 20,
) -> List[Dict[str, Any]]:
    """Discover lanes built from the profile's strongest genre combinations.

    Plain popularity.desc with no genre constraint is how a taste profile full
    of fantasy and anime ended up being served talk shows and a news bulletin.
    """
    pairs = sorted(
        ((name, row) for name, row in (taste.get("genre_pairs") or {}).items() if row.get("affinity", 0) > 0),
        key=lambda pair: -(pair[1]["affinity"] * pair[1].get("confidence", 0)),
    )[:4]
    if not pairs:
        return []
    media_types = job.get("media_types") or ["movie", "tv"]
    endpoints = []
    if any(item in media_types for item in ("movie", "movies")):
        endpoints.append("movie")
    if any(item in media_types for item in ("tv", "show", "anime")):
        endpoints.append("tv")
    out: List[Dict[str, Any]] = []
    for name, _row in pairs:
        wanted = [part for part in str(name).split("|") if part]
        for endpoint in endpoints:
            ids = _genre_ids(wanted, endpoint)
            if not ids or "," not in ids:
                continue
            params = {
                "sort_by": "popularity.desc",
                "include_adult": "false",
                "vote_count.gte": 80,
                # Comma means AND on TMDb: the combination, not either label.
                "with_genres": ids,
                "page": 1,
            }
            rows, _ = await _tmdb_page(f"discover/{endpoint}", params, api_key=api_key)
            for row in rows[:per_lane]:
                normalized = _normalize_tmdb_result(row, endpoint, "taste_seeded_discover")
                normalized["source_seed"] = name
                out.append(normalized)
    return out


#: TMDb original_language codes for Chinese: zh Mandarin, cn Cantonese.
DONGHUA_TMDB_LANGUAGES = {"zh", "cn"}
#: 196 Chinese animated series on TMDb have 5+ votes; 19 have 50+.
DONGHUA_VOTE_FLOOR = 5


def animation_lane_languages(include_genres: Optional[List[str]], media_types: Optional[List[str]]) -> List[str]:
    """Original languages the anime/donghua discover lanes should query.

    Anime is Japanese, donghua is Chinese (TMDb: zh Mandarin, cn Cantonese).
    Asking for "anime" alone no longer fetches donghua, asking for "donghua"
    finally fetches something, and "animation" or an anime media type with no
    narrower genre asks for both. Empty means no anime/donghua lane.
    """
    from recommendation.filter_engine import canonical_genres

    include = canonical_genres(list(include_genres or []))
    media = {str(item).casefold() for item in (media_types or [])}
    if include:
        anime = bool(include & {"anime", "animation"})
        donghua = bool(include & {"donghua", "animation"})
        if not (anime or donghua) and "anime" in media:
            # Anime is a selected media type but the genres are e.g. fantasy/action:
            # anime and donghua in those genres are still wanted.
            anime = donghua = True
    else:
        anime = donghua = "anime" in media
    languages: List[str] = []
    if anime:
        languages.append("ja")
    if donghua:
        languages.extend(["zh", "cn"])
    return languages


async def fetch_job_candidates(
    job: Dict[str, Any],
    history: List[Dict[str, Any]],
    api_key: Optional[str] = None,
    start_page: int = 1,
    taste: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    key = api_key or TMDB_KEY
    if not key:
        return []
    sources = set(job.get("candidate_sources") or [])
    wanted = sources & {"tmdb_discover", "tmdb_similar", "tmdb_recommendations"}
    if not wanted:
        return []
    extra: List[Dict[str, Any]] = []
    media_types = job.get("media_types") or ["movie", "tv"]
    limit = max(int(job.get("candidate_limit") or 40), int(job.get("final_recommendation_limit") or 8))
    filters = job.get("filters") or {}
    by_media = filters.get("by_media_type") if isinstance(filters.get("by_media_type"), dict) else {}
    discover_job = {**job, "candidate_limit": limit}
    if "tmdb_discover" in wanted:
        kinds = []
        if any(item in media_types for item in ("movie", "movies")):
            kinds.append("movie")
        if any(item in media_types for item in ("tv", "show", "anime")):
            kinds.append("tv")
        per = max(20, (limit + len(kinds) - 1) // max(len(kinds), 1)) if kinds else limit
        for kind in kinds:
            lane = dict(filters)
            if by_media:
                overlay = by_media.get(kind) or by_media.get("tv" if kind == "tv" else "movie") or {}
                # Western lane: do not inherit anime-only genre constraints.
                lane = {**filters, **overlay}
                lane.pop("by_media_type", None)
            extra.extend(
                await tmdb_discover(
                    {**discover_job, "candidate_limit": per, "filters": lane},
                    kind,
                    api_key=key,
                    start_page=start_page,
                    taste=taste,
                )
            )
        anime_langs_default = animation_lane_languages(filters.get("include_genres"), media_types)
        if anime_langs_default or by_media.get("anime"):
            anime_overlay = dict(by_media.get("anime") or {})
            anime_langs = anime_overlay.get("languages") or anime_langs_default or ["ja", "zh"]
            # Anime and donghua are queried apart: Japanese animation would
            # otherwise fill the lane before a Chinese title was ever asked for,
            # and donghua needs its own vote floor (see default_vote_floor).
            groups = [[lang for lang in anime_langs if lang not in DONGHUA_TMDB_LANGUAGES],
                      [lang for lang in anime_langs if lang in DONGHUA_TMDB_LANGUAGES]]
            for group in (group for group in groups if group):
                donghua = group[0] in DONGHUA_TMDB_LANGUAGES
                lane_filters = {
                    **{k: v for k, v in filters.items() if k != "by_media_type"},
                    **anime_overlay,
                    "include_genres": anime_overlay.get("include_genres") or ["animation"],
                    "languages": group,
                    "countries": None,
                    "country": None,
                    "language": None,
                }
                if donghua and filters.get("min_vote_count") is None:
                    lane_filters["discover_vote_floor"] = DONGHUA_VOTE_FLOOR
                anime_job = {**discover_job, "candidate_limit": min(per, 60), "filters": lane_filters}
                extra.extend(await tmdb_discover(anime_job, "tv", api_key=key, start_page=start_page, taste=taste))
                # Anime films are their own lane: an anime movie ranks nothing like
                # a 300-episode series, and nothing else in the pipeline produced one.
                anime_movie_job = {
                    **anime_job,
                    "candidate_limit": max(12, min(per // 2, 30)),
                    "filters": {**lane_filters, "include_genres": ["animation"]},
                }
                for row in await tmdb_discover(anime_movie_job, "movie", api_key=key, start_page=start_page, taste=taste):
                    row["media_type"] = "anime"
                    row["type"] = "anime"
                    row["format"] = "MOVIE"
                    extra.append(row)
        if taste and (taste.get("genre_pairs") or {}):
            extra.extend(await taste_seeded_discover(discover_job, taste, api_key=key))
    if "tmdb_similar" in wanted or "tmdb_discover" in wanted:
        extra.extend(await tmdb_related(history, "similar", api_key=key, taste=taste))
    if "tmdb_recommendations" in wanted or "tmdb_discover" in wanted:
        extra.extend(await tmdb_related(history, "recommendations", api_key=key, taste=taste))
    return extra


async def tmdb_search_candidates(intent: Dict[str, Any], api_key: Optional[str] = None) -> List[Dict[str, Any]]:
    """Discover + keyword search from structured AI Search intent."""
    key = api_key or TMDB_KEY
    if not key:
        return []
    job = {
        "filters": {
            "include_genres": intent.get("include_genres") or [],
            "min_year": intent.get("min_year"),
            "max_year": intent.get("max_year"),
        },
        "media_types": intent.get("media_types") or ["movie", "tv"],
        "candidate_sources": ["tmdb_discover"],
    }
    extra: List[Dict[str, Any]] = []
    extra.extend(await fetch_job_candidates(job, [], api_key=key))
    query = (intent.get("search_text") or intent.get("query") or "").strip()
    if len(query) >= 2:
        media = intent.get("media_types") or ["movie", "tv"]
        if any(item in media for item in ("movie", "movies")):
            rows = await _tmdb_list("search/movie", {"query": query, "include_adult": "false", "page": 1}, api_key=key)
            extra.extend(_normalize_tmdb_result(row, "movie", "tmdb_search") for row in rows[:12])
        if any(item in media for item in ("tv", "show", "anime")):
            rows = await _tmdb_list("search/tv", {"query": query, "include_adult": "false", "page": 1}, api_key=key)
            extra.extend(_normalize_tmdb_result(row, "tv", "tmdb_search") for row in rows[:12])
    return extra
