"""The demo shelf, and how to recognise it once it has leaked into a real profile.

With DEMO_MODE on, an empty history is seeded with these twelve titles so the app
is explorable before a provider is linked. The seed also went through
`attach_canonical_ids`, so it landed in `media_history` as *personal ratings*
attributed to Plex, Trakt and Simkl. Measured on Gilbert's data (2026-09-24):
eight of those rows were still there - Chernobyl 9.4 "on Simkl", Severance 8.7
"on Trakt", Parasite 8.5 "on Plex" - although none of the eight exists in his
real Trakt history or ratings. They were the only personal ratings Plex and
Simkl contributed, and two of them even counted as offline evaluation labels.

A seeded row is recognised by its exact fingerprint - title, year and the demo
rating - on a row that carries none of the provider's own ids. A real Trakt or
Simkl rating is a whole number and arrives with the provider's id, so it cannot
collide with 9.4, 8.7 or 7.9.
"""

from typing import Any, Dict, Optional

from .media_identity import coerce_int, title_key

DEMO_HISTORY = [
    {"title": "Blade Runner 2049", "year": 2017, "type": "movie", "genres": ["Sci-Fi", "Neo-Noir"], "rating": 8.0, "poster": "https://image.tmdb.org/t/p/w500/gajva2L0rPYkEWjzgFlBXCAVBE5.jpg", "source": "trakt"},
    {"title": "Dune: Part Two", "year": 2024, "type": "movie", "genres": ["Sci-Fi", "Adventure"], "rating": 8.5, "poster": "https://image.tmdb.org/t/p/w500/6izwz7rsy95ARzTR3poZ8H6c5pp.jpg", "source": "trakt"},
    {"title": "Severance", "year": 2022, "type": "show", "genres": ["Sci-Fi", "Thriller", "Drama"], "rating": 8.7, "poster": "https://image.tmdb.org/t/p/w500/pPHpeI2X1qEd1CS1SeyrdhZ4qnT.jpg", "source": "trakt"},
    {"title": "The Bear", "year": 2022, "type": "show", "genres": ["Drama", "Comedy"], "rating": 8.6, "poster": "https://image.tmdb.org/t/p/w500/eKfVzzEazSIjJMrw9ADa2x8ksLz.jpg", "source": "simkl"},
    {"title": "Everything Everywhere All at Once", "year": 2022, "type": "movie", "genres": ["Sci-Fi", "Action", "Comedy"], "rating": 8.0, "poster": "https://image.tmdb.org/t/p/w500/u68AjlvlutfEIcpmbYpKcdi09ut.jpg", "source": "plex"},
    {"title": "Arrival", "year": 2016, "type": "movie", "genres": ["Sci-Fi", "Drama"], "rating": 7.9, "poster": "https://image.tmdb.org/t/p/w500/pEzNVQfdzYDzVK0XqxERIw2x2se.jpg", "source": "plex"},
    {"title": "Mr. Robot", "year": 2015, "type": "show", "genres": ["Thriller", "Drama", "Crime"], "rating": 8.5, "poster": "https://image.tmdb.org/t/p/w500/kv1nRqgebSsREnd7vdC2pSGjpLo.jpg", "source": "trakt"},
    {"title": "Chernobyl", "year": 2019, "type": "show", "genres": ["Drama", "History"], "rating": 9.4, "poster": "https://image.tmdb.org/t/p/w500/hlLXt2tOPT6RRnjiUmoxyG1LTFi.jpg", "source": "simkl"},
    {"title": "Parasite", "year": 2019, "type": "movie", "genres": ["Thriller", "Drama"], "rating": 8.5, "poster": "https://image.tmdb.org/t/p/w500/7IiTTgloJzvGI1TAYymCfbfl3vT.jpg", "source": "plex"},
    {"title": "Fargo", "year": 2014, "type": "show", "genres": ["Crime", "Drama", "Dark Comedy"], "rating": 8.9, "poster": "https://image.tmdb.org/t/p/w500/a3VW6khsyUVKrG0GBCWFG3NzWPX.jpg", "source": "trakt"},
    {"title": "The Menu", "year": 2022, "type": "movie", "genres": ["Thriller", "Dark Comedy"], "rating": 7.2, "poster": "https://image.tmdb.org/t/p/w500/fPtUgMcLIboqlTlPrq0bQpKK8eq.jpg", "source": "simkl"},
    {"title": "Andor", "year": 2022, "type": "show", "genres": ["Sci-Fi", "Drama"], "rating": 8.4, "poster": "https://image.tmdb.org/t/p/w500/khZqmwHQicTYoS7Flreb9EddFZC.jpg", "source": "trakt"},
]

#: (title key, year) -> the rating the demo shelf gave it.
DEMO_FINGERPRINTS = {
    (title_key(row["title"]), row["year"]): float(row["rating"]) for row in DEMO_HISTORY
}

#: The id each provider stamps on every real row it syncs.
NATIVE_IDS = {
    "trakt": ("trakt_id", "imdb_id"),
    "simkl": ("simkl_id",),
    "plex": ("plex_rating_key",),
    "anilist": ("anilist_id",),
}


def is_demo_seed(row: Dict[str, Any]) -> bool:
    """True for a demo-shelf row, wherever it ended up."""
    if row.get("demo") or (row.get("source") or row.get("provider")) == "demo":
        return True
    expected = DEMO_FINGERPRINTS.get((title_key(row.get("title")), coerce_int(row.get("year"))))
    if expected is None:
        return False
    rating: Optional[float]
    try:
        rating = float(row["rating"]) if row.get("rating") is not None else None
    except (TypeError, ValueError):
        rating = None
    if rating is None or abs(rating - expected) > 0.01:
        return False
    provider = str(row.get("provider") or row.get("source") or "")
    return not any(row.get(field) for field in NATIVE_IDS.get(provider, ()))
