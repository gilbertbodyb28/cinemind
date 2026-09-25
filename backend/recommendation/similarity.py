"""Similarity between a candidate and the titles the user demonstrably likes.

Genre-name overlap alone is far too broad - "Action" matches half the catalogue.
This layer compares genre *combinations*, TMDb keywords, the people who made a
title, its studio and franchise, language, era and the words of the synopsis,
so a title can rank because it resembles something the user actually rated
rather than because it shares one generic label.

Deliberately dependency-free: set overlaps over enriched TMDb metadata are cheap
and honest, and anything heavier has to earn its place in the evaluation
harness first. Several heavier-looking variants were measured and lost; they are
listed next to the constants below.
"""

from typing import Any, Dict, Iterable, List, Optional, Sequence
import math

from .media_identity import coerce_int, content_lane
from .taste_engine import genre_label, genre_pair, media_bucket, overview_tokens, taste_genres

# Genre labels used to carry 0.42 of the comparison and everything else was
# structurally dead - measured on real data, two unrelated "Action, Drama"
# shows came out bit-identical to four decimals. TMDb keywords and people are
# what actually separate titles inside a genre, so they carry most of it now.
GENRE_WEIGHT = 0.26
PAIR_WEIGHT = 0.12
KEYWORD_WEIGHT = 0.22
PEOPLE_WEIGHT = 0.14
TEXT_WEIGHT = 0.09
LANGUAGE_WEIGHT = 0.06
COMPANY_WEIGHT = 0.05
BUCKET_WEIGHT = 0.03
COLLECTION_WEIGHT = 0.02
ERA_WEIGHT = 0.01
# Sharing five specific keywords ("space opera", "chosen one") is as strong a
# statement as two titles ever make. Jaccard over 24-term lists would price
# that at 0.1 and let the genre label win again.
KEYWORD_SATURATION = 5.0
IDF_FLOOR = 0.15

# Measured and rejected on 2026-09-24 (HANDOFF.md, omgång 4), 3 seeds x 8
# folds on the complete-data snapshot. Each looked right on paper and each
# measured worse than the weights above, so they stay switched off - do not
# re-try them without the harness:
#   - genre weight down / keywords, people and franchise up
#     (0.12/0.06/0.30/0.20/0.12): holdout NDCG@10 0.335 -> 0.253
#   - keyword rarity (IDF) in the pairwise comparison: holdout P@5 0.258 -> 0.142
#   - comparing each lane only with itself, 90 references per lane: Western
#     animation (X-Men '97, rated 10/10) was compared with Japanese anime only
#     and fell from 1st to 35th; holdout P@5 0.258 -> 0.117
#   - best match blended with the best three (kNN): P@5 0.258 -> 0.217
#: "hard" (lane against lane), "soft" (discounted across lanes) or "none".
LANE_MODE = "none"
CROSS_LANE_FACTOR = 0.8
#: How many of the strongest liked titles a candidate is compared with.
REFERENCES_PER_LANE = 24
#: Share of the best match in the blend with the best NEIGHBOURS (1.0 = best only).
NEIGHBOURS = 3
BEST_SHARE = 1.0
#: A reference title's pull scales from this share (little evidence) to 1.0 (the
#: strongest title in the list).
EVIDENCE_BASE = 0.55
#: Weight of synopsis words shared with the favourites, next to TMDb keywords.
#: Dropped once on the reasoning that its top words read as noise ("family",
#: "where", "journey"); measured, it carries signal for this viewer, so it stays.
SYNOPSIS_SHARE = 0.5
#: Keyword rarity (IDF) in keyword_affinity / in the pairwise comparison. Off: see above.
KEYWORD_IDF = False
PAIR_IDF = False
#: Never compare a title with itself (a rewatch candidate, or the offline
#: harness scoring a watched title with the watched filter switched off).
SKIP_SELF = True


def _names(row: Dict[str, Any], field: str = "genres") -> set:
    if field == "genres":
        return set(taste_genres(row))
    return {str(name).casefold() for name in (row.get(field) or []) if name}


def _jaccard(left: set, right: set) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _pairs(names: Iterable[str]) -> set:
    ordered = sorted(names)
    return {
        genre_pair(ordered[first], ordered[second])
        for first in range(len(ordered))
        for second in range(first + 1, len(ordered))
    }


def _era_distance(left: Dict[str, Any], right: Dict[str, Any]) -> float:
    first, second = coerce_int(left.get("year")), coerce_int(right.get("year"))
    if first is None or second is None:
        return 0.0
    return max(0.0, 1.0 - abs(first - second) / 30.0)


def _text(row: Dict[str, Any]) -> set:
    cached = row.get("_tokens")
    if cached is not None:
        return cached
    tokens = set(overview_tokens(row.get("overview") or row.get("synopsis")))
    row["_tokens"] = tokens
    return tokens


def lane_group(row: Dict[str, Any]) -> str:
    """live_action or animated: the two worlds a comparison must not mix."""
    return "live_action" if content_lane(row) == "live_action" else "animated"


def _features(row: Dict[str, Any], cache: bool = True) -> Dict[str, Any]:
    """The sets a comparison reads, computed once per row.

    `cache=False` for rows that leave the scoring step (the re-rank prompt, the
    debug trace): a cached set on a row that is later saved is not BSON, and
    it failed every AI-reranked job run on 2026-09-24 21:30 UTC.
    """
    cached = row.get("_sim")
    if cached is not None:
        return cached
    genres = _names(row)
    features = {
        "genres": genres,
        "pairs": _pairs(genres),
        "themes": _names(row, "tmdb_keywords") | _names(row, "tags"),
        "creators": _names(row, "creators"),
        "cast": _names(row, "cast"),
        "companies": _names(row, "companies") | _names(row, "studios"),
        "language": str(row.get("original_language") or "").casefold(),
        "collection": str(row.get("collection") or "").casefold(),
        "bucket": media_bucket(row),
        "lane": lane_group(row),
    }
    if cache:
        row["_sim"] = features
    return features


def keyword_idf(rows: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    """0..1 rarity of every keyword across the rows at hand (candidates and history).

    "duringcreditsstinger", "sequel" and "based on novel or book" sit on a large
    part of any pool and say almost nothing about two titles being alike.
    """
    frequency: Dict[str, int] = {}
    total = 0
    for row in rows:
        themes = _features(row)["themes"]
        if not themes:
            continue
        total += 1
        for name in themes:
            frequency[name] = frequency.get(name, 0) + 1
    if not total:
        return {}
    peak = math.log(1.0 + total)
    return {
        name: round(max(IDF_FLOOR, math.log(1.0 + total / count) / peak), 4)
        for name, count in frequency.items()
    }


def _weighted_overlap(shared: Iterable[str], idf: Optional[Dict[str, float]], saturation: float) -> float:
    weights = [(idf or {}).get(name, 1.0) for name in shared]
    return min(1.0, sum(weights) / saturation) if weights else 0.0


def pair_similarity(candidate: Dict[str, Any], liked: Dict[str, Any], idf: Optional[Dict[str, float]] = None) -> float:
    """0..1 similarity between one candidate and one liked title."""
    left, right = _features(candidate), _features(liked)
    score = GENRE_WEIGHT * _jaccard(left["genres"], right["genres"])
    score += PAIR_WEIGHT * _jaccard(left["pairs"], right["pairs"])
    score += KEYWORD_WEIGHT * _weighted_overlap(left["themes"] & right["themes"], idf if PAIR_IDF else None, KEYWORD_SATURATION)
    creators = left["creators"] & right["creators"]
    cast = left["cast"] & right["cast"]
    if creators or cast:
        score += PEOPLE_WEIGHT * min(1.0, 0.75 * len(creators) + 0.25 * len(cast))
    score += TEXT_WEIGHT * _jaccard(_text(candidate), _text(liked))
    if left["language"] and left["language"] == right["language"]:
        score += LANGUAGE_WEIGHT
    if left["companies"] & right["companies"]:
        score += COMPANY_WEIGHT
    if left["bucket"] == right["bucket"]:
        score += BUCKET_WEIGHT
    if left["collection"] and left["collection"] == right["collection"]:
        score += COLLECTION_WEIGHT
    score += ERA_WEIGHT * _era_distance(candidate, liked)
    return round(min(1.0, score), 4)


#: A concrete link between two titles: shared *distinctive* themes, the same
#: creator or cast, the same franchise, the same studio. Genres, language,
#: format and era are not links - "Tabu (1931) plays like Heated Rivalry:
#: genres Drama, Romance" is how a job filled its list with old films. This is
#: read by the taste floor and the match percentage only; the ranking weights
#: above are left exactly as measured.
SPECIFIC_THEME_SATURATION = 2.0
SPECIFIC_COMPANY_SHARE = 0.4
#: A keyword that only restates a genre is no link beyond the genre: "Sodor516
#: plays like Amphibia (themes fantasy, comedy)" put a zero-vote title in
#: "Upcoming Tv Shows" on 2026-09-25. Such keywords are left out of the
#: concrete link and of the "themes ..." reason, never out of the ranking.
#: Measured on snap_live: the taste floor still keeps 97 % of held-out
#: favourites in the Tv pool, 86 % across formats and 93 % of approved
#: Requests, exactly as before; it only stops titles whose sole "link" was a
#: genre word.
SPECIFIC_SKIPS_GENRE_WORDS = True
GENRE_WORD_THEMES = frozenset({
    "action", "adventure", "action & adventure", "animation", "anime", "comedy", "crime",
    "documentary", "drama", "family", "fantasy", "history", "horror", "kids", "music",
    "musical", "mystery", "romance", "sci-fi", "scifi", "sci fi", "science fiction",
    "science-fiction", "sci-fi & fantasy", "superhero", "thriller", "war", "war & politics",
    "western", "reality", "soap", "talk", "news",
})


def _link_themes(features: Dict[str, Any]) -> set:
    themes = features["themes"]
    return themes - GENRE_WORD_THEMES if SPECIFIC_SKIPS_GENRE_WORDS else themes


def specific_link(candidate: Dict[str, Any], liked: Dict[str, Any], idf: Optional[Dict[str, float]] = None) -> float:
    """0..1: how concretely two titles are linked beyond their genre labels."""
    left, right = _features(candidate), _features(liked)
    themes = _weighted_overlap(_link_themes(left) & _link_themes(right), idf, SPECIFIC_THEME_SATURATION)
    creators = left["creators"] & right["creators"]
    cast = left["cast"] & right["cast"]
    people = min(1.0, 0.75 * len(creators) + 0.35 * len(cast))
    franchise = 1.0 if left["collection"] and left["collection"] == right["collection"] else 0.0
    company = SPECIFIC_COMPANY_SHARE if left["companies"] & right["companies"] else 0.0
    return round(min(1.0, max(themes, people, franchise) + 0.5 * company), 4)


def best_specific(
    candidate: Dict[str, Any],
    titles: Sequence[Dict[str, Any]],
    idf: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """The liked title this candidate is most concretely linked to, over every liked title.

    Every liked title counts here, not just the strongest few the ranking
    compares with: a Supernatural spin-off is concretely linked to Supernatural
    even when 328 unrated episodes leave it outside the top 24. The link is
    discounted by how little evidence stands behind the liked title.
    """
    peak = max((abs(float(row.get("score") or 0)) for row in titles), default=1.0) or 1.0
    itself = _identity(candidate) if SKIP_SELF else None
    best, best_title = 0.0, None
    for liked in titles:
        if itself and _identity(liked) == itself:
            continue
        link = specific_link(candidate, liked, idf)
        if link <= best:
            continue
        evidence = min(1.0, abs(float(liked.get("score") or 0)) / peak)
        link *= EVIDENCE_BASE + (1.0 - EVIDENCE_BASE) * evidence
        if link > best:
            best, best_title = link, liked.get("title")
    return {"score": round(min(1.0, best), 4), "title": best_title}


def explain_match(candidate: Dict[str, Any], liked: Dict[str, Any], limit: int = 3) -> List[str]:
    """What two titles actually share, in words - the debug trace's "matched because"."""
    left, right = _features(candidate, cache=False), _features(liked, cache=False)

    def _original(field: str, keys: Iterable[str]) -> List[str]:
        wanted = set(keys)
        return [str(value) for value in (candidate.get(field) or []) if str(value).casefold() in wanted][:limit]

    parts: List[str] = []
    shared = _link_themes(left) & _link_themes(right)
    themes = _original("tmdb_keywords", shared) or _original("tags", shared)
    if themes:
        parts.append("themes " + ", ".join(themes))
    creators = _original("creators", left["creators"] & right["creators"])
    if creators:
        parts.append("creator " + ", ".join(creators))
    cast = _original("cast", left["cast"] & right["cast"])
    if cast:
        parts.append("cast " + ", ".join(cast))
    if left["collection"] and left["collection"] == right["collection"]:
        parts.append("franchise " + str(candidate.get("collection")))
    companies = [str(value) for value in list(candidate.get("companies") or []) + list(candidate.get("studios") or [])
                 if str(value).casefold() in (left["companies"] & right["companies"])][:2]
    if companies:
        parts.append("studio/network " + ", ".join(companies))
    genres = sorted(left["genres"] & right["genres"])[:3]
    if genres:
        parts.append("genres " + ", ".join(genre_label(name) for name in genres))
    return parts


def _identity(row: Dict[str, Any]) -> Optional[tuple]:
    from .media_identity import title_key

    key = title_key(row.get("title"))
    return (key, coerce_int(row.get("year"))) if key else None


def _references(candidate: Dict[str, Any], titles: Sequence[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """The liked titles a candidate is compared with, strongest first.

    LANE_MODE "hard" compares live action only with live action and animation
    only with animation; "soft" compares with everything and discounts a match
    across that line (see best_similarity); "none" ignores lanes.
    """
    if LANE_MODE != "hard":
        return list(titles[:limit])
    lane = _features(candidate)["lane"]
    same = [row for row in titles if _features(row)["lane"] == lane]
    # A lane with almost no history of its own still gets compared with
    # something, rather than scoring zero for the lack of it.
    if len(same) < 5:
        return list(titles[:limit])
    return same[:limit]


def best_similarity(
    candidate: Dict[str, Any],
    titles: Sequence[Dict[str, Any]],
    limit: Optional[int] = None,
    idf: Optional[Dict[str, float]] = None,
    peak: Optional[float] = None,
) -> Dict[str, Any]:
    """Strongest match against the reference titles, with the titles that matched.

    Weighted by how much evidence stands behind each reference title, so a 10/10
    rating pulls harder than something merely finished once. `peak` is the
    evidence that counts as full weight; by default the strongest reference in
    `titles` (see ranking_engine.NEGATIVE_EVIDENCE_SCALE for why that is not
    always right).
    """
    references = _references(candidate, titles, limit or REFERENCES_PER_LANE)
    peak_evidence = peak or max((abs(float(row.get("score") or 0)) for row in references), default=1.0) or 1.0
    runners: List[Dict[str, Any]] = []
    itself = _identity(candidate) if SKIP_SELF else None
    for liked in references:
        # A title is not evidence for itself (a rewatch candidate, or the
        # offline harness scoring a watched title with the filter switched off).
        if itself and _identity(liked) == itself:
            continue
        raw = pair_similarity(candidate, liked, idf)
        if raw <= 0:
            continue
        if LANE_MODE == "soft" and _features(liked)["lane"] != _features(candidate)["lane"]:
            raw *= CROSS_LANE_FACTOR
        evidence = min(1.0, abs(float(liked.get("score") or 0)) / peak_evidence)
        weighted = raw * (EVIDENCE_BASE + (1.0 - EVIDENCE_BASE) * evidence)
        runners.append({"title": liked.get("title"), "year": liked.get("year"), "similarity": round(weighted, 4)})
    runners.sort(key=lambda row: -row["similarity"])
    if not runners:
        return {"score": 0.0, "title": None, "year": None, "matches": []}
    best = runners[0]["similarity"]
    top = [row["similarity"] for row in runners[:NEIGHBOURS]]
    top += [0.0] * (NEIGHBOURS - len(top))
    blended = BEST_SHARE * best + (1.0 - BEST_SHARE) * (sum(top) / NEIGHBOURS)
    return {
        "score": round(min(1.0, blended), 4),
        "best": round(best, 4),
        "title": runners[0]["title"],
        "year": runners[0]["year"],
        "matches": runners[:3],
    }


def keyword_affinity(candidate: Dict[str, Any], taste: Dict[str, Any], idf: Optional[Dict[str, float]] = None) -> float:
    """How strongly the candidate's themes run through the user's judged titles.

    Two parts: TMDb keywords (weighted by how rare they are in the pool) against
    the keyword profile, and - at half weight - synopsis words shared with the
    favourites' synopses. The synopsis part looks like noise when its top words
    are read ("family", "where", "journey", "fight"), and it was dropped on that
    reasoning; measured, it carries real signal for this viewer (holdout P@5
    0.217 -> see SYNOPSIS_SHARE), so it stays.

    Deliberately one-sided: an absent overlap counts for nothing rather than
    against the title. Centring it on an expected coverage was tried and
    measured worse (holdout P@5 1.000 -> 0.800, NDCG@10 0.929 -> 0.693),
    because a title with few keywords is not a bad match - it is an unknown
    one, and `metadata_confidence` already prices that in.
    """
    themes = _features(candidate)["themes"]
    keyword_profile = taste.get("tmdb_keywords") or {}
    total = 0.0
    weight = 0.0
    for name in themes:
        rarity = (idf or {}).get(name, 1.0) if KEYWORD_IDF else 1.0
        row = keyword_profile.get(name)
        if row:
            total += rarity * float(row.get("affinity") or 0.0) * (0.4 + 0.6 * float(row.get("confidence") or 0.0))
        weight += rarity
    profile = taste.get("keywords") or {}
    if profile and SYNOPSIS_SHARE:
        tokens = _text(candidate)
        for token in tokens:
            row = profile.get(token)
            if row:
                total += SYNOPSIS_SHARE * float(row.get("affinity") or 0.0) * (0.4 + 0.6 * float(row.get("confidence") or 0.0))
        weight += len(tokens)
    if not weight:
        return 0.0
    return round(max(-1.0, min(1.0, total / math.sqrt(weight))), 4)


def people_affinity(candidate: Dict[str, Any], taste: Dict[str, Any]) -> float:
    """Directors, creators and recurring cast the user keeps coming back to.

    The strongest single match wins rather than the sum: one favourite director
    should not need a full cast behind them, and a large ensemble should not
    out-score them by volume alone.
    """
    profile = taste.get("people") or {}
    if not profile:
        return 0.0
    best = 0.0
    for field, prefix in (("creators", "creator"), ("cast", "cast")):
        for name in _names(candidate, field):
            row = profile.get("%s:%s" % (prefix, name))
            if not row:
                continue
            value = float(row.get("affinity") or 0.0) * (0.4 + 0.6 * float(row.get("confidence") or 0.0))
            best = value if abs(value) > abs(best) else best
    return round(max(-1.0, min(1.0, best)), 4)


def franchise_affinity(candidate: Dict[str, Any], taste: Dict[str, Any]) -> float:
    """Same collection or same studio as something the user rated highly."""
    collections = taste.get("collections") or {}
    collection = str(candidate.get("collection") or "").casefold()
    if collection and collections.get(collection):
        row = collections[collection]
        return round(max(-1.0, min(1.0, float(row.get("affinity") or 0.0))), 4)
    profile = taste.get("companies") or {}
    studios = taste.get("studios") or {}
    best = 0.0
    for field, store in (("companies", profile), ("studios", studios)):
        if not store:
            continue
        for name in _names(candidate, field):
            row = store.get(name) or store.get(str(name))
            if not row:
                continue
            value = float(row.get("affinity") or 0.0) * (0.4 + 0.6 * float(row.get("confidence") or 0.0))
            best = value if abs(value) > abs(best) else best
    return round(max(-1.0, min(1.0, best)), 4)


def media_type_affinity(candidate: Dict[str, Any], taste: Dict[str, Any]) -> float:
    """Anime, anime films, series and films are separate lanes with separate taste."""
    profile = taste.get("media_types") or {}
    row = profile.get(media_bucket(candidate)) or {}
    return round(float(row.get("affinity") or 0.0) * (0.4 + 0.6 * float(row.get("confidence") or 0.0)), 4)


def recency_affinity(candidate: Dict[str, Any], taste: Dict[str, Any]) -> float:
    """What the user has been watching lately, kept separate from lifetime taste."""
    profile = {str(name).casefold(): row for name, row in (taste.get("recent_genres") or {}).items()}
    if not profile:
        return 0.0
    names = _names(candidate)
    if not names:
        return 0.0
    total = sum(
        float((profile.get(name) or {}).get("affinity") or 0.0)
        * (0.4 + 0.6 * float((profile.get(name) or {}).get("confidence") or 0.0))
        for name in names
    )
    return round(max(-1.0, min(1.0, total / len(names))), 4)
