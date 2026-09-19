"""Deterministic job filters with explicit reason codes."""

from typing import Any, Dict, List, Optional, Set, Tuple

from .media_identity import normalize_media_type

# TMDb tags almost every scripted show as Drama (and many as Comedy). Hard-excluding
# those when the user also asked for Action/Sci-Fi/etc. wipes the catalogue.
COMPANION_GENRES = {"drama", "comedy"}

COUNTRY_ALIASES = {
    "us": {"us", "usa", "united states", "united states of america"},
    "usa": {"us", "usa", "united states", "united states of america"},
    "united states": {"us", "usa", "united states", "united states of america"},
    "jp": {"jp", "japan", "jpn"},
    "japan": {"jp", "japan", "jpn"},
    "cn": {"cn", "china", "chn"},
    "china": {"cn", "china", "chn"},
}


def _effective_exclude(include: Set[str], exclude: Set[str], candidate_genres: Set[str]) -> Set[str]:
    if not exclude:
        return set()
    if include and (candidate_genres & include):
        return exclude - COMPANION_GENRES
    return exclude


def _as_list(value: Any) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None and str(item).strip()]
    return [str(value)]


def _normalize_country(value: str) -> str:
    return str(value or "").strip().casefold()


def _countries_match(wanted: List[str], actual: Set[str]) -> bool:
    if not wanted:
        return True
    if not actual:
        return False
    expanded: Set[str] = set()
    for item in wanted:
        key = _normalize_country(item)
        expanded |= COUNTRY_ALIASES.get(key, {key})
    return bool(actual & expanded)


def _candidate_countries(candidate: Dict[str, Any]) -> Set[str]:
    found: Set[str] = set()
    if candidate.get("country"):
        found.add(_normalize_country(candidate["country"]))
    for item in candidate.get("origin_countries") or []:
        found.add(_normalize_country(item))
    return {item for item in found if item}


def _candidate_languages(candidate: Dict[str, Any]) -> Set[str]:
    found: Set[str] = set()
    if candidate.get("original_language"):
        found.add(str(candidate["original_language"]).casefold())
    for item in candidate.get("languages") or []:
        found.add(str(item).casefold())
    return {item for item in found if item}


def _release_stamp(candidate: Dict[str, Any]) -> Optional[str]:
    for key in ("release_date", "first_air_date", "aired_at"):
        value = candidate.get(key)
        if value and len(str(value)) >= 10:
            return str(value)[:10]
    year = candidate.get("year")
    if year is not None:
        return f"{int(year):04d}-01-01"
    return None


def _media_bucket(candidate: Dict[str, Any]) -> str:
    media = normalize_media_type(candidate.get("type") or candidate.get("media_type"))
    if media == "anime":
        return "anime"
    genres = {genre.casefold() for genre in (candidate.get("genres") or []) if genre}
    lang = (candidate.get("original_language") or "").casefold()
    # Animation in JA/ZH is treated as the anime/donghua lane for by_media_type rules.
    if "animation" in genres and lang in {"ja", "zh", "ko"}:
        return "anime"
    return media


def _merge_media_filters(filters: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    by_media = filters.get("by_media_type") or {}
    if not isinstance(by_media, dict) or not by_media:
        return filters
    bucket = _media_bucket(candidate)
    overlay = by_media.get(bucket) or by_media.get(normalize_media_type(bucket)) or {}
    if not overlay:
        return filters
    merged = dict(filters)
    for key, value in overlay.items():
        merged[key] = value
    return merged


def _never_rated_yet(candidate: Dict[str, Any], rating: Any, votes: Any) -> bool:
    """An unreleased title scores 0.0 because nobody has voted, not because it is bad.

    Without this an "upcoming" job with a rating floor rejects every single
    candidate and reports zero picks with no visible reason.
    """
    if rating not in (None, 0, 0.0):
        return False
    if votes not in (None, 0):
        return False
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc)
    release = _release_stamp(candidate)
    if release:
        return release > today.date().isoformat()
    year = candidate.get("year")
    try:
        return year is not None and int(year) > today.year
    except (TypeError, ValueError):
        return False


def apply_filters(candidate: Dict[str, Any], filters: Optional[Dict[str, Any]]) -> Tuple[bool, Optional[str]]:
    filters = _merge_media_filters(filters or {}, candidate)
    media_types = filters.get("media_types") or filters.get("media_type")
    if media_types:
        allowed = {normalize_media_type(item) for item in (media_types if isinstance(media_types, list) else [media_types])}
        bucket = _media_bucket(candidate)
        # Anime lane may arrive as tv+animation; accept when anime is allowed.
        candidate_media = normalize_media_type(candidate.get("type") or candidate.get("media_type"))
        if candidate_media not in allowed and not (bucket == "anime" and "anime" in allowed):
            if not (bucket == "anime" and ("tv" in allowed or "show" in allowed)):
                return False, "rejected_media_type"

    genres = {genre.casefold() for genre in (candidate.get("genres") or []) if genre}
    include = {genre.casefold() for genre in (filters.get("include_genres") or [])}
    exclude = {genre.casefold() for genre in (filters.get("exclude_genres") or [])}
    keyword_tags = {str(item).casefold() for item in (filters.get("keywords") or [])}
    candidate_tags = {str(item).casefold() for item in (candidate.get("tags") or [])}
    # A keyword hit (e.g. lgbt) counts as an include on its own, next to the genres.
    if include and not (genres & include) and not (keyword_tags and (candidate_tags & keyword_tags)):
        return False, "rejected_genre"
    effective_exclude = _effective_exclude(include, exclude, genres)
    if effective_exclude and (genres & effective_exclude):
        return False, "rejected_genre"

    year = candidate.get("year")
    if filters.get("min_year") is not None and (year is None or int(year) < int(filters["min_year"])):
        return False, "rejected_year"
    if filters.get("max_year") is not None and (year is None or int(year) > int(filters["max_year"])):
        return False, "rejected_year"

    release = _release_stamp(candidate)
    if filters.get("min_release_date"):
        if not release or release < str(filters["min_release_date"])[:10]:
            return False, "rejected_release_date"
    if filters.get("max_release_date"):
        if not release or release > str(filters["max_release_date"])[:10]:
            return False, "rejected_release_date"

    rating = candidate.get("tmdb_rating") if candidate.get("tmdb_rating") is not None else candidate.get("rating")
    votes = candidate.get("vote_count")
    if (
        filters.get("min_rating") is not None
        and not _never_rated_yet(candidate, rating, votes)
        and (rating is None or float(rating) < float(filters["min_rating"]))
    ):
        return False, "rejected_rating"
    if filters.get("min_vote_count") is not None and (votes is None or int(votes) < int(filters["min_vote_count"])):
        return False, "rejected_vote_count"

    runtime = candidate.get("runtime")
    if filters.get("min_runtime") is not None and (runtime is None or int(runtime) < int(filters["min_runtime"])):
        return False, "rejected_runtime"
    if filters.get("max_runtime") is not None and (runtime is None or int(runtime) > int(filters["max_runtime"])):
        return False, "rejected_runtime"

    languages = [item.casefold() for item in _as_list(filters.get("languages") or filters.get("language"))]
    if languages:
        actual_langs = _candidate_languages(candidate)
        if not actual_langs or not (actual_langs & set(languages)):
            return False, "rejected_language"

    countries = _as_list(filters.get("countries") or filters.get("country"))
    if countries:
        actual_countries = _candidate_countries(candidate)
        if actual_countries:
            if not _countries_match(countries, actual_countries):
                return False, "rejected_country"
        else:
            # Missing origin metadata: only soft-allow the US lane when language is English.
            wants_us = _countries_match(countries, {"us"})
            if not wants_us or "en" not in _candidate_languages(candidate):
                return False, "rejected_country"

    if filters.get("streaming_provider") and filters["streaming_provider"] not in (candidate.get("streaming_providers") or []):
        return False, "rejected_provider"
    if filters.get("anime_only") and normalize_media_type(candidate.get("type") or candidate.get("media_type")) != "anime":
        return False, "rejected_media_type"
    if filters.get("anime_format"):
        wanted = str(filters["anime_format"]).casefold()
        actual = str(candidate.get("format") or candidate.get("anime_format") or "").casefold()
        if actual != wanted:
            return False, "rejected_format"
    if filters.get("anime_season"):
        wanted = str(filters["anime_season"]).casefold()
        actual = str(candidate.get("season") or candidate.get("anime_season") or "").casefold()
        if actual != wanted:
            return False, "rejected_season"
    if filters.get("anime_status"):
        wanted = str(filters["anime_status"]).casefold()
        actual = str(candidate.get("status") or candidate.get("anime_status") or "").casefold()
        if actual != wanted:
            return False, "rejected_status"
    if filters.get("studio"):
        studios = {str(item).casefold() for item in (candidate.get("studios") or [])}
        if candidate.get("studio"):
            studios.add(str(candidate["studio"]).casefold())
        if str(filters["studio"]).casefold() not in studios:
            return False, "rejected_studio"
    tags = {str(item).casefold() for item in (candidate.get("tags") or [])}
    include_tags = [str(item).casefold() for item in (filters.get("include_tags") or [])]
    if include_tags and not (tags & set(include_tags)):
        return False, "rejected_tag"
    episodes = candidate.get("episodes") or candidate.get("episode_count")
    if filters.get("min_episodes") is not None and (episodes is None or int(episodes) < int(filters["min_episodes"])):
        return False, "rejected_episodes"
    if filters.get("max_episodes") is not None and (episodes is None or int(episodes) > int(filters["max_episodes"])):
        return False, "rejected_episodes"
    return True, None
