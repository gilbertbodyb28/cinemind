"""Deterministic job filters with explicit reason codes."""

from typing import Any, Dict, List, Optional, Set, Tuple
import re

from .media_identity import content_lane, identity_scope, normalize_media_type

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


# One spelling per genre. Trakt says "Science-Fiction" and "Talk-Show", TMDb TV
# says "Sci-Fi & Fantasy" and "Action & Adventure", AniList and TMDb movies say
# "Sci-Fi". Compared as raw strings, a job asking for sci-fi rejected every Trakt
# title and a job asking for fantasy or adventure rejected every TMDb TV show.
GENRE_ALIASES = {
    "science fiction": "sci-fi",
    "science-fiction": "sci-fi",
    "scifi": "sci-fi",
    "sci fi": "sci-fi",
    "animated": "animation",
    "talk": "talk show",
    "talk-show": "talk show",
    "game-show": "game show",
    "kid": "kids",
    "children": "kids",
    "chinese animation": "donghua",
}

# TMDb's TV-only combined genres. providers/tmdb.py names them after their first
# half ("Action", "Sci-Fi"); the id says both halves.
TMDB_COMBINED_GENRE_IDS = {
    10759: ("action", "adventure"),
    10765: ("sci-fi", "fantasy"),
    10768: ("war", "politics"),
}

ANIMATION_GENRES = {"animation", "anime", "donghua"}


def canonical_genres(values: Any) -> Set[str]:
    """Casefolded, alias-resolved genre names, with "A & B" split into both."""
    found: Set[str] = set()
    for value in _as_list(values):
        for part in re.split(r"\s*[&/]\s*", str(value).strip().casefold()):
            part = part.strip()
            if part:
                found.add(GENRE_ALIASES.get(part, part))
    return found


def candidate_genres(candidate: Dict[str, Any]) -> Set[str]:
    """Every genre a candidate can be matched or excluded on.

    Besides its own labels it carries its lane: every animated title is
    "animation", Japanese animation is also "anime", Chinese animation is also
    "donghua". AniList never tags anything "Animation" and no source tags a title
    "Donghua" reliably, so the lane is the only dependable way to ask for them.
    """
    genres = canonical_genres(candidate.get("genres") or [])
    for raw in candidate.get("tmdb_genre_ids") or []:
        try:
            genres.update(TMDB_COMBINED_GENRE_IDS.get(int(raw), ()))
        except (TypeError, ValueError):
            continue
    lane = content_lane(candidate)
    if lane != "live_action":
        genres.add("animation")
    if lane in {"anime", "donghua"}:
        genres.add(lane)
    return genres


def genre_matches(candidate: Dict[str, Any], include: Set[str], keywords: Optional[Set[str]] = None) -> bool:
    """True when the candidate satisfies at least one include genre (OR, never AND)."""
    wanted = canonical_genres(list(include or []))
    if not wanted:
        return True
    if candidate_genres(candidate) & wanted:
        return True
    tags = {str(item).casefold() for item in (candidate.get("tags") or [])}
    return bool(keywords and (tags & keywords))


def wants_animation_lane(filters: Dict[str, Any]) -> bool:
    """The job asked for anime, donghua or animation, by genre or by media type."""
    include = canonical_genres(filters.get("include_genres") or [])
    media = {normalize_media_type(item) for item in _as_list(filters.get("media_types") or filters.get("media_type"))}
    return bool(include & ANIMATION_GENRES) or "anime" in media


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


def _animation_exempt_from_origin(job_filters: Dict[str, Any], candidate: Dict[str, Any]) -> bool:
    """Anime and donghua are defined by where they come from.

    The TMDb anime lane already queries ja/zh on its own, whatever language the
    job names, so a job-wide "en" filter then rejected every title that lane
    found — donghua for being zh, anime for being ja. The exemption is for
    animation only: a Chinese live-action drama still meets the language filter.
    An explicit language on the anime overlay (by_media_type.anime) still wins.
    """
    if content_lane(candidate) not in {"anime", "donghua"}:
        return False
    if not wants_animation_lane(job_filters):
        return False
    overlay = (job_filters.get("by_media_type") or {}).get("anime") or {}
    return not (isinstance(overlay, dict) and (overlay.get("languages") or overlay.get("language")))


def media_type_allowed(candidate: Dict[str, Any], media_types: Any) -> bool:
    """Does the job's Movies / TV / Anime choice admit this title?

    Anime is a lane, not a format. An anime *film* is a film: it passes a job
    that allows Movies or Anime, never a TV-only job. Until 2026-09-25 every
    anime title counted as TV, so "Upcoming Tv Shows" filled up with anime
    films (Milky Subway, ALL YOU NEED IS KILL, Mononoke the Movie ...).
    """
    allowed = {normalize_media_type(item) for item in _as_list(media_types)}
    if not allowed:
        return True
    film_or_series = identity_scope(candidate)
    if _media_bucket(candidate) == "anime":
        return "anime" in allowed or film_or_series in allowed
    candidate_media = normalize_media_type(candidate.get("type") or candidate.get("media_type"))
    return candidate_media in allowed or film_or_series in allowed


def apply_filters(candidate: Dict[str, Any], filters: Optional[Dict[str, Any]]) -> Tuple[bool, Optional[str]]:
    job_filters = filters or {}
    origin_exempt = _animation_exempt_from_origin(job_filters, candidate)
    filters = _merge_media_filters(job_filters, candidate)
    media_types = filters.get("media_types") or filters.get("media_type")
    if media_types and not media_type_allowed(candidate, media_types):
        return False, "rejected_media_type"

    genres = candidate_genres(candidate)
    include = canonical_genres(filters.get("include_genres") or [])
    exclude = canonical_genres(filters.get("exclude_genres") or [])
    keyword_tags = {str(item).casefold() for item in (filters.get("keywords") or [])}
    # A keyword hit (e.g. lgbt) counts as an include on its own, next to the genres.
    if not genre_matches(candidate, include, keyword_tags):
        return False, "rejected_genre"
    effective_exclude = _effective_exclude(include, exclude, genres)
    if effective_exclude and (genres & effective_exclude):
        return False, "rejected_genre"

    year = candidate.get("year")
    release = _release_stamp(candidate)
    if filters.get("upcoming_only"):
        # A job for coming premieres measures its window on the verified premiere
        # (providers.premieres), not on the year the title first came out: a
        # series from 2019 with a season premiering in 2027 belongs in a
        # 2026-2029 window, a series that premiered in March 2026 does not.
        from datetime import datetime, timezone

        premiere = str(candidate.get("premiere_date") or "")[:10]
        if len(premiere) != 10 or premiere <= datetime.now(timezone.utc).date().isoformat():
            return False, "rejected_not_upcoming"
        year, release = int(premiere[:4]), premiere
    if filters.get("min_year") is not None and (year is None or int(year) < int(filters["min_year"])):
        return False, "rejected_year"
    if filters.get("max_year") is not None and (year is None or int(year) > int(filters["max_year"])):
        return False, "rejected_year"

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
    # A floor of 0 is no requirement at all, but the old "is not None" check turned
    # it into "must report a count", which rejected every source that does not
    # publish vote counts - AniList among them.
    min_votes = filters.get("min_vote_count") or 0
    if (
        int(min_votes) > 0
        and not _never_rated_yet(candidate, rating, votes)
        and (votes is None or int(votes) < int(min_votes))
    ):
        return False, "rejected_vote_count"

    runtime = candidate.get("runtime")
    if filters.get("min_runtime") is not None and (runtime is None or int(runtime) < int(filters["min_runtime"])):
        return False, "rejected_runtime"
    if filters.get("max_runtime") is not None and (runtime is None or int(runtime) > int(filters["max_runtime"])):
        return False, "rejected_runtime"

    languages = [item.casefold() for item in _as_list(filters.get("languages") or filters.get("language"))]
    if languages and not origin_exempt:
        actual_langs = _candidate_languages(candidate)
        if not actual_langs or not (actual_langs & set(languages)):
            return False, "rejected_language"

    countries = _as_list(filters.get("countries") or filters.get("country"))
    if countries and not origin_exempt:
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
