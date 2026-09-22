"""Verified candidates from catalogues and provider payloads. The LLM is not a title database."""

from typing import Any, Dict, Iterable, List, Optional

from .exclusion_engine import identity_keys
from .media_identity import normalize_media_type, title_key


PRESERVED_METADATA = (
    "popularity", "release_date", "first_air_date", "aired_at",
    "origin_countries", "tags", "keywords", "studios", "studio",
    "format", "anime_format", "season", "anime_season", "status", "anime_status",
    "episodes", "episode_count", "streaming_providers",
    "tvdb_id", "trakt_id", "simkl_id", "plex_rating_key", "canonical_media_id",
    "provider_specific_metadata",
)


def normalize_candidate(raw: Dict[str, Any], source: str, seed: Optional[str] = None) -> Dict[str, Any]:
    media_type = normalize_media_type(raw.get("media_type") or raw.get("type") or "movie")
    candidate = {
        "title": raw.get("title") or "Unknown",
        "year": raw.get("year"),
        "type": "show" if media_type == "tv" else media_type,
        "media_type": media_type,
        "genres": list(raw.get("genres") or []),
        "poster": raw.get("poster") or raw.get("poster_url"),
        "backdrop": raw.get("backdrop"),
        "synopsis": raw.get("synopsis") or raw.get("overview") or "",
        "tmdb_rating": raw.get("tmdb_rating") or raw.get("vote_average") or 0,
        "vote_count": raw.get("vote_count"),
        "tmdb_id": raw.get("tmdb_id"),
        "imdb_id": raw.get("imdb_id"),
        "anilist_id": raw.get("anilist_id"),
        "runtime": raw.get("runtime"),
        "original_language": raw.get("original_language"),
        "country": raw.get("country"),
        "source": source,
        "source_seed": seed,
        "candidate_score": float(raw.get("candidate_score") or raw.get("match_score") or 0),
        "why": raw.get("why") or raw.get("source_reason") or "",
    }
    candidate.update({key: raw[key] for key in PRESERVED_METADATA if key in raw})
    return candidate


def expand_from_seeds(
    history: List[Dict[str, Any]],
    catalog: Iterable[Dict[str, Any]],
    media_types: Optional[List[str]] = None,
    limit: int = 40,
    taste: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Rank a supplied catalogue by taste affinity rather than raw genre counts.

    Counting genre occurrences made the most common label in the history win,
    which for any large history is whatever the user watched most episodes of,
    not what they liked.
    """
    from .taste_engine import candidate_affinity

    allowed = {normalize_media_type(item) for item in (media_types or ["movie", "tv", "anime"])}
    seen = {title_key(item.get("title")) for item in history}
    fallback: Dict[str, int] = {}
    if not (taste or {}).get("genres"):
        for item in history:
            for genre in item.get("genres") or []:
                fallback[genre.casefold()] = fallback.get(genre.casefold(), 0) + 1
        peak = max(fallback.values()) if fallback else 1

    candidates: List[Dict[str, Any]] = []
    for raw in catalog:
        media_type = normalize_media_type(raw.get("type") or raw.get("media_type"))
        if media_type not in allowed:
            continue
        if title_key(raw.get("title")) in seen:
            continue
        row = normalize_candidate(raw, "seed_expand", seed="taste_affinity")
        if (taste or {}).get("genres"):
            row["candidate_score"] = round(candidate_affinity(row, taste), 4)
        else:
            overlap = sum(fallback.get(genre.casefold(), 0) for genre in raw.get("genres") or [])
            row["candidate_score"] = round(overlap / max(peak, 1), 4)
        candidates.append(row)
    candidates.sort(key=lambda row: (-row["candidate_score"], -(row.get("tmdb_rating") or 0), row["title"]))
    return candidates[:limit]


def merge_candidate_sources(*groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen = set()
    for group in groups:
        for row in group or []:
            keys = identity_keys(row)
            duplicate = bool(keys & seen)
            seen.update(keys)
            if duplicate:
                continue
            merged.append(row)
    return merged
