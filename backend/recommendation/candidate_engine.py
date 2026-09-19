"""Verified candidates from catalogues and provider payloads. The LLM is not a title database."""

from typing import Any, Dict, Iterable, List, Optional

from .media_identity import normalize_media_type, title_key


def normalize_candidate(raw: Dict[str, Any], source: str, seed: Optional[str] = None) -> Dict[str, Any]:
    media_type = normalize_media_type(raw.get("type") or raw.get("media_type") or "movie")
    return {
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


def expand_from_seeds(
    history: List[Dict[str, Any]],
    catalog: Iterable[Dict[str, Any]],
    media_types: Optional[List[str]] = None,
    limit: int = 40,
) -> List[Dict[str, Any]]:
    allowed = {normalize_media_type(item) for item in (media_types or ["movie", "tv", "anime"])}
    seen = {title_key(item.get("title")) for item in history}
    genre_affinity: Dict[str, int] = {}
    for item in history:
        for genre in item.get("genres") or []:
            genre_affinity[genre.casefold()] = genre_affinity.get(genre.casefold(), 0) + 1

    candidates: List[Dict[str, Any]] = []
    for raw in catalog:
        media_type = normalize_media_type(raw.get("type") or raw.get("media_type"))
        if media_type not in allowed:
            continue
        if title_key(raw.get("title")) in seen:
            continue
        overlap = sum(genre_affinity.get(genre.casefold(), 0) for genre in raw.get("genres") or [])
        row = normalize_candidate(raw, "seed_expand", seed="history_genres")
        row["candidate_score"] = float(overlap)
        candidates.append(row)
    candidates.sort(key=lambda row: (-row["candidate_score"], -(row.get("tmdb_rating") or 0), row["title"]))
    return candidates[:limit]


def merge_candidate_sources(*groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen = set()
    for group in groups:
        for row in group or []:
            key = row.get("tmdb_id") or f"{title_key(row.get('title'))}:{row.get('year')}"
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
    return merged
