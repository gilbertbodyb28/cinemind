"""Structured taste features from normalized history and feedback. Not an LLM dump.

The v1 engine divided every item's weight by the number of rows its provider
contributed, which meant a user with 400 Trakt rows could never push a single
title past the "high confidence positive" threshold: the richer the history,
the emptier the profile. v2 scores each title on its own evidence, keeps the
counts and confidences that evidence carries, and only normalizes at the end,
where scale no longer changes the ordering.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
import math
import re

from .media_identity import coerce_int, normalize_media_type, title_key, unique_taste_docs

DEFAULT_PROVIDER_WEIGHTS = {
    "plex": 1.0,
    "trakt": 1.0,
    "simkl": 1.0,
    "anilist": 1.2,
    "feedback": 1.0,
    "demo": 0.25,
}

# A title needs this much evidence before it is quoted back as something the
# user demonstrably likes. Reachable: one 9/10 rating clears it on its own.
POSITIVE_EVIDENCE = 1.8
NEGATIVE_EVIDENCE = -1.0
#: How many liked and disliked titles similarity may compare a candidate with.
LIKED_REFERENCE_LIMIT = 400
NEGATIVE_REFERENCE_LIMIT = 160
RECENT_DAYS = 240
# List-valued metadata merged across every provider row behind one title.
# `tmdb_keywords`, `cast`, `creators` and `companies` arrive from TMDb detail
# enrichment; without them the profile carried nothing but genre names.
LIST_FIELDS = ("genres", "tags", "studios", "tmdb_keywords", "cast", "creators", "companies")
# A language profile built from 44 of 633 titles cannot tell English from
# Tagalog. Below this much evidence an unseen language stays neutral instead of
# being penalised on a profile that never had the chance to mention it.
LANGUAGE_EVIDENCE_FOR_PENALTY = 30
STOPWORDS = frozenset("""
a an and are as at be but by for from has have he her his in into is it its of on or she
that the their them they this to was were will with who what when which after before
his hers our your my their one two new life world story man woman young old first last
""".split())


def taste_genres(item: Dict[str, Any]) -> List[str]:
    """One spelling per genre, the way the filters already read them.

    The profile stored genres exactly as each source spelled them, so Trakt's
    "Science-Fiction" (affinity 0.645) and TMDb's "Sci-Fi" (0.026) were two
    unrelated genres, and TMDb TV's 10765 "Sci-Fi & Fantasy" reached the
    ranking as "Sci-Fi" alone - the Fantasy half, Gilbert's third strongest
    genre at 0.905, was invisible on every TMDb TV candidate.
    """
    from .filter_engine import GENRE_ALIASES, TMDB_COMBINED_GENRE_IDS

    cached = item.get("_taste_genres")
    if cached is not None:
        return cached
    names: List[str] = []
    if not CANONICAL_GENRES:
        names = [str(value).casefold() for value in item.get("genres") or [] if value]
        item["_taste_genres"] = names
        return names
    for value in item.get("genres") or []:
        parts = re.split(r"\s*[&/]\s*", str(value).strip().casefold()) if SPLIT_COMBINED else [str(value).strip().casefold()]
        for part in parts:
            part = part.strip()
            if part:
                name = GENRE_ALIASES.get(part, part) if GENRE_ALIASING else part
                if name not in names:
                    names.append(name)
    for raw in (item.get("tmdb_genre_ids") or []) if EXPAND_COMBINED else []:
        try:
            halves = TMDB_COMBINED_GENRE_IDS.get(int(raw), ())
        except (TypeError, ValueError):
            continue
        for name in halves:
            if name not in names:
                names.append(name)
    item["_taste_genres"] = names
    return names


#: One spelling per genre across providers (see taste_genres). Measured
#: 2026-09-24, complete-data snapshot, 3 seeds x 8 folds, against raw names:
#: TV job P@5 0.425 -> 0.567 and NDCG@10 0.380 -> 0.456, Requests AUC 0.800 ->
#: 0.889; the broad Content to Watch holdout pays for it, NDCG@10 0.357 -> 0.317,
#: because every live-action sci-fi/fantasy series now matches the profile's
#: sci-fi and fantasy instead of neither. Kept: the same genre spelled two ways
#: is a bug, and the TV jobs are where recommendations had stopped working.
CANONICAL_GENRES = True
#: ... including aliases ("Science-Fiction" and "Sci-Fi" are one genre). This is
#: the half that carries the TV-job gain (P@5 0.450 without it).
GENRE_ALIASING = True
#: A genre *name* like "Sci-Fi & Fantasy" is read as both genres. This half
#: carries the Requests gain (AUC 0.801 without it).
SPLIT_COMBINED = True
#: TMDb's combined TV ids (10759 Action & Adventure, 10765 Sci-Fi & Fantasy) are
#: NOT expanded into both halves for taste. The filters must (a job asking for
#: fantasy has to find a "Sci-Fi & Fantasy" series), but for taste it over-claims:
#: every TMDb action or genre series then carried four of Gilbert's five top
#: genres and looked like a perfect match. Measured 2026-09-24 on the complete-
#: data snapshot: expanding cost holdout NDCG@10 0.311 -> 0.253 and the TV job's
#: P@5 0.558 -> 0.375. TMDb names the id after its first half ("Sci-Fi").
EXPAND_COMBINED = False
#: Episode progress (AniList, Trakt's watched sets) counted as viewing depth.
#: Off: measured 2026-09-24 on the complete-data snapshot it cost holdout P@5
#: 0.358 -> 0.300 and gained nothing on the TV job (0.567 -> 0.558).
PROGRESS_AS_PLAYS = False
#: Plan-to-watch rows stay out of the profile.
SKIP_PLANNED = True

GENRE_LABELS = {"sci-fi": "Sci-Fi", "talk show": "Talk Show", "game show": "Game Show"}


def genre_label(name: str) -> str:
    """Display spelling for a canonical genre name."""
    return GENRE_LABELS.get(name, " ".join(part.capitalize() for part in str(name).split(" ")))


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp
    except (ValueError, TypeError):
        return None


def doc_key(doc: Dict[str, Any]) -> str:
    """Stable per-title key so every provider row for a title lands together."""
    canonical = doc.get("canonical_media_id")
    if canonical:
        return str(canonical)
    media = normalize_media_type(doc.get("media_type") or doc.get("type"))
    return "%s|%s|%s" % (title_key(doc.get("title")), coerce_int(doc.get("year")), media)


def normalized_rating(item: Dict[str, Any]) -> Optional[float]:
    """The user's own score on a 0-10 scale, or None when they never rated it."""
    rating = item.get("rating")
    if rating is None:
        return None
    try:
        value = float(rating)
        scale = float(item.get("rating_scale") or 10) or 10.0
    except (TypeError, ValueError):
        return None
    if scale <= 0 or value < 0 or value > scale:
        return None
    return value / scale * 10.0


#: Statuses that say "I intend to watch this", not "I watched it".
PLANNED_STATUSES = frozenset({"PLANNING", "PLANTOWATCH", "PLAN_TO_WATCH"})
#: Rows CineMind itself produced from the user's explicit actions. They are not a
#: history provider a job can switch off; only an empty taste_sources removes them.
OWN_SIGNAL_PROVIDERS = frozenset({"feedback", "requests"})


def row_provider(row: Dict[str, Any]) -> str:
    return str(row.get("provider") or row.get("source") or "unknown")


def is_planned(row: Dict[str, Any]) -> bool:
    return str(row.get("status") or "").upper() in PLANNED_STATUSES


def source_allowed(row: Dict[str, Any], taste_sources: Optional[Iterable[str]]) -> bool:
    """Does this row belong to a provider the job takes its taste from?

    Decided per row, before titles are merged. It used to be decided after the
    merge, on the *alphabetically first* provider of the merged title, so a
    title watched on AniList and Trakt counted as "anilist" and vanished from a
    job without AniList even though its Trakt plays and rating were allowed.
    """
    if taste_sources is None:
        return True
    allowed = set(taste_sources)
    if not allowed:
        return False
    provider = row_provider(row)
    return provider in allowed or provider in OWN_SIGNAL_PROVIDERS


def merge_taste_docs(
    history: Iterable[Dict[str, Any]],
    personal: Optional[Iterable[Dict[str, Any]]] = None,
    ignored: Optional[Dict[str, int]] = None,
) -> List[Dict[str, Any]]:
    """One row per canonical title, merging every provider row behind it.

    Raw Trakt history is one row per episode, so the number of rows behind a
    title is how much of it the user actually watched - the strongest signal
    the catalogue hands us for free. Keeping only the first row threw that away
    together with ratings that just one provider reported.

    Two kinds of row never enter the merge. Demo-shelf rows are not the user's
    (recommendation.demo_seed). A plan-to-watch row is not history at all, and
    its status used to win the merge: Family Guy (389 episodes, 10/10),
    Naruto Shippuden (501), Supernatural (328) and Friends (229) all carried a
    Simkl "plantowatch" and were therefore scored as zero evidence.
    `ignored` receives the counts, so a report can say what was left out.
    """
    from .demo_seed import is_demo_seed

    merged: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    skipped = ignored if ignored is not None else {}

    def _absorb(row: Dict[str, Any], is_personal: bool) -> None:
        if is_demo_seed(row):
            skipped["demo_seed"] = skipped.get("demo_seed", 0) + 1
            return
        if SKIP_PLANNED and is_planned(row):
            skipped["plan_to_watch"] = skipped.get("plan_to_watch", 0) + 1
            return
        key = doc_key(row)
        current = merged.get(key)
        if current is None:
            current = {
                "plays": 0,
                "providers": set(),
                **{field: [] for field in LIST_FIELDS},
            }
            merged[key] = current
            order.append(key)
        current["plays"] += 0 if is_personal else 1
        # AniList and Simkl keep one row per title; their episode progress is
        # the same depth signal Trakt's one-row-per-episode history gives.
        progress = coerce_int(row.get("progress")) if PROGRESS_AS_PLAYS else None
        if progress:
            current["progress"] = max(current.get("progress") or 0, progress)
        provider = row.get("provider") or row.get("source")
        if provider:
            current["providers"].add(provider)
        for field in LIST_FIELDS:
            for value in row.get(field) or []:
                if value and value not in current[field]:
                    current[field].append(value)
        for field in (
            "title", "year", "type", "media_type", "canonical_media_id", "original_language",
            "country", "tmdb_id", "imdb_id", "anilist_id", "trakt_id", "simkl_id", "tvdb_id",
            "overview", "synopsis", "format", "collection", "runtime",
        ):
            if current.get(field) in (None, "", []) and row.get(field) not in (None, "", []):
                current[field] = row[field]
        # A personal rating outranks whatever a catalogue row carried.
        rating = normalized_rating(row)
        if rating is not None and (is_personal or current.get("rating_source") != "personal"):
            current["rating"] = rating
            current["rating_scale"] = 10
            current["rating_source"] = "personal" if is_personal else "history"
            current["rating_provider"] = row_provider(row)
        for field, combine in (
            ("watch_count", max), ("favorite", max), ("completed", max), ("rewatched", max),
        ):
            value = row.get(field)
            if value is not None:
                current[field] = combine(current.get(field) or 0, int(bool(value)) if field != "watch_count" else int(value or 1))
        # A drop is a judgement and outranks any other provider's status.
        status = str(row.get("status") or "").upper()
        if status and (not current.get("status") or status == "DROPPED"):
            current["status"] = row["status"]
        if row.get("decision") and not current.get("decision"):
            current["decision"] = row["decision"]
        stamp = _parse_dt(row.get("last_watched_at") or row.get("watched_at") or row.get("rated_at"))
        if stamp and (current.get("_stamp") is None or stamp > current["_stamp"]):
            current["_stamp"] = stamp
            current["last_watched_at"] = row.get("last_watched_at") or row.get("watched_at")

    for row in history or []:
        _absorb(row, False)
    for row in personal or []:
        _absorb(row, True)

    output = []
    for key in order:
        row = merged[key]
        providers = sorted(row.pop("providers") or [])
        # `providers` is the truth; `provider` stays for the per-provider weight
        # and older readers, and prefers a real history provider over CineMind's own rows.
        row["providers"] = providers
        history_providers = [name for name in providers if name not in OWN_SIGNAL_PROVIDERS]
        row["provider"] = (history_providers or providers or ["unknown"])[0]
        row.pop("_stamp", None)
        row["plays"] = max(1, row.get("plays") or 1, row.get("progress") or 0)
        output.append(row)
    return output


#: Evidence a decision in the Requests queue carries. An approval is a wish,
#: weaker than a rating of 8; a rejection is a "no", weaker than a rating of 4.
APPROVED_EVIDENCE = 2.0
REJECTED_EVIDENCE = -1.5


def decision_docs(
    requests: Optional[Iterable[Dict[str, Any]]],
    watched: Optional[Iterable[Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Approved and rejected Requests as explicit feedback on CineMind's own picks.

    Measured 2026-09-24: 234 rejections and 55 approvals, never read by the
    taste profile - only used to keep those exact titles from coming back, so
    the next CSI, Monk or Castle was recommended right after the last one was
    turned down.

    A rejection of something the user has already watched is not a dislike -
    Logan, The Dark Knight Rises and Avatar were rejected, and all three are
    rated 10/10 on Trakt. Those rejections are skipped; so is anything the
    user has since rated, because the rating is the better evidence.
    """
    from .exclusion_engine import identity_keys

    seen = set()
    for row in watched or []:
        seen.update(identity_keys(row))
    docs: List[Dict[str, Any]] = []
    counts = {"approved": 0, "rejected": 0, "rejected_but_watched": 0}
    for row in requests or []:
        status = str(row.get("status") or "")
        if status not in {"approved", "rejected"}:
            continue
        if not row.get("title"):
            continue
        if status == "rejected" and identity_keys(row) & seen:
            counts["rejected_but_watched"] += 1
            continue
        if status == "approved" and identity_keys(row) & seen:
            # Already in the history, where plays and ratings say more.
            continue
        counts[status] += 1
        docs.append({
            **{key: value for key, value in row.items() if key not in {"rating", "rating_scale", "status", "provider", "source", "match_score"}},
            "provider": "requests",
            "decision": status,
        })
    return docs, counts


def evidence_score(item: Dict[str, Any], feedback_by_id: Optional[Dict[str, str]] = None) -> float:
    """How strongly this title says "more like this", on a roughly -5..+5 scale.

    Watched is not liked. A rating, a rewatch or a hundred finished episodes are
    evidence; one sampled episode is barely any.
    """
    feedback_by_id = feedback_by_id or {}
    status = (item.get("status") or "").upper()
    if status in PLANNED_STATUSES:
        return 0.0

    canonical = item.get("canonical_media_id")
    action = feedback_by_id.get(canonical or "", "")
    if action == "blacklist":
        return -5.0
    if action == "dislike":
        return -3.0

    score = 0.0
    rating = normalized_rating(item)
    if rating is not None:
        if rating >= 9:
            score = 3.4
        elif rating >= 8:
            score = 2.6
        elif rating >= 7:
            score = 1.2
        elif rating >= 5:
            score = 0.2
        else:
            score = -2.4
    elif item.get("decision") == "rejected":
        # Turned down in the Requests queue: a "no" on a title never watched,
        # so there are no plays or recency to add to it.
        return REJECTED_EVIDENCE
    elif item.get("decision") == "approved":
        score = APPROVED_EVIDENCE
    else:
        # No rating: engagement depth is the only honest evidence we have.
        score = 0.35

    plays = int(item.get("plays") or 1)
    if plays > 1:
        # log so a 700-episode sitcom outweighs a 12-episode season without
        # burying every film the user has ever rated.
        score += min(1.9, math.log2(plays) * 0.42)
    if int(item.get("watch_count") or 1) > 1 or item.get("rewatched"):
        score += 0.8
    if item.get("favorite"):
        score = max(score, 2.8)
    if status == "REPEATING":
        score += 0.7
    if item.get("completed") or status == "COMPLETED":
        score += 0.3
    if status == "DROPPED" and (rating is None or rating <= 5):
        score = min(score, -2.0)
    if action == "like":
        score = max(score, 2.8)

    stamp = _parse_dt(item.get("last_watched_at") or item.get("watched_at"))
    if stamp and score > 0:
        days = (datetime.now(timezone.utc) - stamp).days
        if days <= 90:
            score *= 1.15
        elif days > 365 * 3:
            score *= 0.8
    return round(score, 4)


def stated_opinion(item: Dict[str, Any], feedback_by_id: Optional[Dict[str, str]] = None) -> bool:
    """Did the user actually pass judgement on this title, rather than watch it?

    Watched is not liked. Episode depth is real evidence of engagement, but it
    is not an opinion, and the precise signals - keywords, people, studios -
    are only trustworthy when an opinion stands behind them.
    """
    if normalized_rating(item) is not None:
        return True
    if item.get("favorite") or item.get("decision"):
        return True
    action = (feedback_by_id or {}).get(item.get("canonical_media_id") or "", "")
    return action in {"like", "dislike", "blacklist"}


def is_recent(item: Dict[str, Any], days: int = RECENT_DAYS) -> bool:
    stamp = _parse_dt(item.get("last_watched_at") or item.get("watched_at"))
    if stamp is None:
        return False
    return (datetime.now(timezone.utc) - stamp).days <= days


# TMDb tags animation-specific formats explicitly. Trakt and AniList report
# anime genres as "Action, Adventure, Comedy" with no "Animation" among them, so
# the genre test alone left 113 of Gilbert's 204 Japanese titles - Boruto, My
# Hero Academia, Slime - counted as ordinary drama series, and his anime_movie
# lane completely empty. Only formats that cannot exist in live action are
# listed, so a live-action manga adaptation is not swept up with them.
ANIMATED_FORMAT_KEYWORDS = frozenset({
    "anime", "donghua", "original net animation (ona)",
    "original video animation (ova)", "aeni",
})


def media_bucket(item: Dict[str, Any]) -> str:
    """movie / tv / anime / anime_movie - anime films rank nothing like anime series."""
    media = normalize_media_type(item.get("media_type") or item.get("type"))
    fmt = str(item.get("format") or item.get("anime_format") or "").upper()
    genres = {str(g).casefold() for g in (item.get("genres") or [])}
    language = str(item.get("original_language") or "").casefold()
    if media != "anime" and "animation" in genres and language in {"ja", "zh", "ko"}:
        media = "anime"
    if media != "anime" and ANIMATED_FORMAT_KEYWORDS & {
        str(name).casefold() for name in (item.get("tmdb_keywords") or [])
    }:
        media = "anime"
    # `normalize_media_type` defaults an absent value to "movie", so testing it
    # against a missing `type` filed every anime row that carried no separate
    # type - most of AniList's - as an anime film. The format has to be stated.
    stated_type = item.get("type")
    if media == "anime" and (
        fmt in {"MOVIE", "FILM"}
        or (stated_type and normalize_media_type(stated_type) == "movie")
    ):
        return "anime_movie"
    return media


def _accumulate(store: Dict[str, Dict[str, float]], name: str, weight: float) -> None:
    row = store.setdefault(name, {"score": 0.0, "evidence": 0.0, "positive": 0.0, "negative": 0.0})
    row["score"] += weight
    row["evidence"] += 1
    if weight >= 0:
        row["positive"] += 1
    else:
        row["negative"] += 1


def _finalize(store: Dict[str, Dict[str, float]], floor: float = 1.0, per_item: bool = False) -> Dict[str, Dict[str, float]]:
    """Map raw sums onto -1..1 affinity with an evidence-based confidence.

    Normalizing here rather than per item is what keeps one large provider from
    dominating without making individual evidence unreachably small.

    `per_item` divides by the number of titles first, for the few dimensions
    where volume is not preference. There are only a handful of formats, so the
    format with the most rows would otherwise always come out on top: once anime
    series were classified correctly they outnumbered everything else and took
    affinity 1.0 purely on count, which measured worse than not classifying them
    at all (NDCG@5 0.770 -> 0.573).
    """
    if not store:
        return {}
    value = (lambda row: row["score"] / max(row["evidence"], 1.0)) if per_item else (lambda row: row["score"])
    peak = max(abs(value(row)) for row in store.values()) or 1.0
    output: Dict[str, Dict[str, float]] = {}
    for name, row in store.items():
        evidence = row["evidence"]
        output[name] = {
            "affinity": round(max(-1.0, min(1.0, value(row) / max(peak, floor))), 4),
            "evidence": int(evidence),
            # Wilson-ish: 5 observations is where we start trusting a preference.
            "confidence": round(evidence / (evidence + 5.0), 4),
            "positive": int(row["positive"]),
            "negative": int(row["negative"]),
        }
    return output


def _top(store: Dict[str, Dict[str, float]], limit: int, sign: int = 1) -> List[str]:
    rows = [(name, row) for name, row in store.items() if row["affinity"] * sign > 0]
    rows.sort(key=lambda pair: -pair[1]["affinity"] * sign)
    return [name for name, _ in rows[:limit]]


def overview_tokens(text: Optional[str], limit: int = 24) -> List[str]:
    if not text:
        return []
    words = re.findall(r"[a-zA-Z][a-zA-Z'-]{3,}", str(text).casefold())
    seen: List[str] = []
    for word in words:
        if word in STOPWORDS or word in seen:
            continue
        seen.append(word)
        if len(seen) >= limit:
            break
    return seen


def build_taste_snapshot(
    history: List[Dict[str, Any]],
    feedback: Optional[List[Dict[str, Any]]] = None,
    provider_weights: Optional[Dict[str, float]] = None,
    taste_sources: Optional[List[str]] = None,
    personal_history: Optional[List[Dict[str, Any]]] = None,
    requests: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """The viewer's taste, built from every provider's rows and their own decisions.

    `taste_sources` selects providers per row (None: all; []: none at all).
    `requests` are the Requests queue: approvals and rejections are explicit
    feedback on CineMind's own picks (see decision_docs).
    """
    weights = {**DEFAULT_PROVIDER_WEIGHTS, **(provider_weights or {})}
    feedback_by_id = {
        row["canonical_media_id"]: row.get("action")
        for row in (feedback or [])
        if row.get("canonical_media_id")
    }

    ignored: Dict[str, int] = {}
    history_rows = [row for row in history or [] if source_allowed(row, taste_sources)]
    personal_rows = [row for row in personal_history or [] if source_allowed(row, taste_sources)]
    ignored["other_taste_sources"] = (len(history or []) - len(history_rows)) + (
        len(personal_history or []) - len(personal_rows))
    decisions, decision_counts = decision_docs(
        requests if taste_sources is None or taste_sources else [],
        watched=list(history or []) + list(personal_history or []),
    )
    docs = merge_taste_docs(history_rows, personal_rows + decisions, ignored=ignored)

    genres: Dict[str, Dict[str, float]] = {}
    pairs: Dict[str, Dict[str, float]] = {}
    languages: Dict[str, Dict[str, float]] = {}
    decades: Dict[str, Dict[str, float]] = {}
    studios: Dict[str, Dict[str, float]] = {}
    tags: Dict[str, Dict[str, float]] = {}
    buckets: Dict[str, Dict[str, float]] = {}
    recent_genres: Dict[str, Dict[str, float]] = {}
    longterm_genres: Dict[str, Dict[str, float]] = {}
    keywords: Dict[str, Dict[str, float]] = {}
    tmdb_keywords: Dict[str, Dict[str, float]] = {}
    people: Dict[str, Dict[str, float]] = {}
    companies: Dict[str, Dict[str, float]] = {}
    collections: Dict[str, Dict[str, float]] = {}

    positives: List[Dict[str, Any]] = []
    negatives: List[Dict[str, Any]] = []
    rewatched: List[str] = []
    rated_count = 0
    usage: Dict[str, Dict[str, int]] = {}

    def _use(names: Iterable[str], field: str) -> None:
        for name in names:
            row = usage.setdefault(name, {"titles": 0, "rated_titles": 0, "positives": 0, "negatives": 0, "seeds": 0})
            row[field] += 1

    for item in docs:
        weight = evidence_score(item, feedback_by_id) * weights.get(item.get("provider") or "unknown", 1.0)
        providers = item.get("providers") or [item.get("provider") or "unknown"]
        _use(providers, "titles")
        if normalized_rating(item) is not None:
            rated_count += 1
            _use([item.get("rating_provider") or providers[0]], "rated_titles")
        decision = item.get("decision")
        # A rejection says "not this one"; it is precise about the title, its
        # themes and its people, and says nothing reliable about whole genres,
        # languages or formats - 234 rejections of crime procedurals would
        # otherwise have taken Drama down with them.
        consumption = not decision
        names = taste_genres(item)
        if consumption or decision == "approved":
            recent = is_recent(item)
            for name in names:
                _accumulate(genres, name, weight)
                if consumption:
                    _accumulate(recent_genres if recent else longterm_genres, name, weight)
            for first in range(len(names)):
                for second in range(first + 1, len(names)):
                    _accumulate(pairs, genre_pair(names[first], names[second]), weight)
        if consumption:
            if item.get("original_language"):
                _accumulate(languages, str(item["original_language"]).casefold(), weight)
            year = coerce_int(item.get("year"))
            if year:
                _accumulate(decades, "%ds" % ((year // 10) * 10), weight)
            _accumulate(buckets, media_bucket(item), weight)
        for studio in item.get("studios") or []:
            _accumulate(studios, str(studio), weight)
        for tag in item.get("tags") or []:
            _accumulate(tags, str(tag), weight)
        # Keywords, people and studios are precise enough that finishing a show
        # without ever rating it pollutes them. Episode depth alone can carry an
        # unrated title past POSITIVE_EVIDENCE, and those titles then matched
        # their own terms back. These stores therefore take only titles the user
        # actually judged - a rating, a like, a dislike, a favourite or a
        # decision in the Requests queue. Genres still take every row; they need
        # the volume to mean anything.
        if stated_opinion(item, feedback_by_id):
            for keyword in item.get("tmdb_keywords") or []:
                _accumulate(tmdb_keywords, str(keyword).casefold(), weight)
            # A director says more about taste than a fourth-billed actor, so
            # they are accumulated separately rather than as one "people" bag.
            for person in item.get("creators") or []:
                _accumulate(people, "creator:%s" % str(person).casefold(), weight)
            for person in item.get("cast") or []:
                _accumulate(people, "cast:%s" % str(person).casefold(), weight * 0.5)
            for company in item.get("companies") or []:
                _accumulate(companies, str(company).casefold(), weight)
            if item.get("collection"):
                _accumulate(collections, str(item["collection"]).casefold(), weight)

        title = item.get("title")
        if not title:
            continue
        row = {
            "title": title,
            "year": item.get("year"),
            "score": round(weight, 3),
            "genres": names[:6],
            "media_type": media_bucket(item),
            "type": item.get("type") or item.get("media_type"),
            "original_language": item.get("original_language"),
            "country": item.get("country"),
            "origin_countries": item.get("origin_countries"),
            "plays": item.get("plays"),
            "rating": normalized_rating(item),
            "decision": decision,
            "decided_at": item.get("updated_at") if decision else None,
            "providers": providers,
            "tmdb_id": item.get("tmdb_id"),
            # Carried so a candidate can be compared on what a title is actually
            # about, not only on which genre labels it happens to share.
            "overview": item.get("overview") or item.get("synopsis"),
            "tmdb_keywords": (item.get("tmdb_keywords") or [])[:24],
            "cast": (item.get("cast") or [])[:8],
            "creators": (item.get("creators") or [])[:8],
            "companies": (item.get("companies") or [])[:8],
            "collection": item.get("collection"),
            "studios": (item.get("studios") or [])[:6],
            "tags": (item.get("tags") or [])[:12],
        }
        if weight >= POSITIVE_EVIDENCE:
            positives.append(row)
            _use(providers, "positives")
            for token in overview_tokens(item.get("overview") or item.get("synopsis")):
                _accumulate(keywords, token, weight)
        elif weight <= NEGATIVE_EVIDENCE:
            row["reason"] = ("rejected in Requests" if decision == "rejected"
                             else "rated %.0f/10" % row["rating"] if row["rating"] is not None
                             else "dropped" if str(item.get("status") or "").upper() == "DROPPED"
                             else "disliked")
            negatives.append(row)
            _use(providers, "negatives")
        if item.get("rewatched") or int(item.get("watch_count") or 1) > 1:
            rewatched.append(title)

    # Explicit feedback is a direct instruction, so it lands on top of whatever
    # the watch history implied rather than being averaged into it.
    preference_reasons: List[Dict[str, Any]] = []
    seen_feedback: set = set()
    for row in feedback or []:
        action = row.get("action")
        title = row.get("title")
        if action not in {"like", "dislike", "blacklist"} or not title:
            continue
        if title in seen_feedback and action != "blacklist":
            continue
        seen_feedback.add(title)
        delta = 3.5 if action == "like" else (-4.5 if action == "blacklist" else -3.5)
        names = taste_genres(row)
        for name in names:
            _accumulate(genres, name, delta)
        entry = {"title": title, "year": row.get("year"), "score": delta, "genres": names[:6],
                 "type": row.get("type"), "media_type": media_bucket(row), "reason": action}
        if action == "like":
            positives.append(entry)
        else:
            negatives.append(entry)
        preference_reasons.append({
            "kind": "liked" if action == "like" else ("disliked" if action == "dislike" else "blacklisted"),
            "title": title,
            "effect": ", ".join(genre_label(name) for name in names[:3]) or ("positive signal" if action == "like" else "negative signal"),
        })

    genre_profile = _finalize(genres)
    positives.sort(key=lambda row: -row["score"])
    # Equal evidence (every rejection weighs the same) falls back to the most
    # recent decision first: what the user turned down last week says more
    # about their taste now than a rejection from months ago.
    negatives.sort(key=lambda row: str(row.get("decided_at") or ""), reverse=True)
    negatives.sort(key=lambda row: row["score"])
    # Provider IDs for the titles with the most evidence behind them, so
    # "more like this" queries are seeded by what the user actually loved
    # rather than by whichever rows the database happened to return first.
    seeds = sorted(
        (doc for doc in docs if doc.get("tmdb_id") or doc.get("anilist_id")),
        key=lambda doc: -evidence_score(doc, feedback_by_id),
    )

    def _seed_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "title": doc.get("title"),
            "year": doc.get("year"),
            "type": doc.get("type") or doc.get("media_type"),
            "media_type": media_bucket(doc),
            "tmdb_id": doc.get("tmdb_id"),
            "anilist_id": doc.get("anilist_id"),
            "genres": (doc.get("genres") or [])[:6],
            # Lets a job seed only from the lane it asks for (job_intent.seed_fits).
            "original_language": doc.get("original_language"),
            "country": doc.get("country"),
            "evidence": round(evidence_score(doc, feedback_by_id), 3),
            "providers": doc.get("providers"),
        }

    seed_docs = [_seed_doc(doc) for doc in seeds[:24] if evidence_score(doc, feedback_by_id) > 0]
    # The same ordering, much further down. The top 24 of an anime-heavy
    # profile hold only a few English live-action series, so a job that asks
    # for those needs to look past them for its "more like this" seeds.
    lane_seed_docs = [
        _seed_doc(doc) for doc in seeds[:240]
        if doc.get("tmdb_id") and evidence_score(doc, feedback_by_id) > 0
    ]
    for doc in lane_seed_docs:
        _use(doc.get("providers") or [], "seeds")
    snapshot: Dict[str, Any] = {
        # --- v1 keys, still read by the UI, the LLM prompt and older jobs ---
        "favorite_genres": _top(genre_profile, 8),
        "least_preferred_genres": _top(genre_profile, 5, sign=-1),
        "liked_genres": _top(genre_profile, 8),
        "disliked_genres": _top(genre_profile, 8, sign=-1),
        "favorite_eras": _top(_finalize(decades), 4),
        "preferred_languages": _top(_finalize(languages), 4),
        "high_confidence_positive_titles": positives[:24],
        "negative_titles": negatives[:NEGATIVE_REFERENCE_LIMIT],
        "frequently_rewatched_titles": list(dict.fromkeys(rewatched))[:12],
        "preference_reasons": preference_reasons[:12],
        "provider_weights": weights,
        "item_count": len(docs),
        # --- v2 structured profile ---
        # Every title with enough evidence, strongest first. Similarity used to
        # compare candidates with the top 24 only - 15 of them anime - so an
        # English TV job measured every sitcom against How I Met Your Mother.
        "liked_titles": positives[:LIKED_REFERENCE_LIMIT],
        "genres": genre_profile,
        "genre_pairs": _finalize(pairs),
        "languages": _finalize(languages),
        "decades": _finalize(decades),
        "studios": _finalize(studios),
        "tags": _finalize(tags),
        "media_types": _finalize(buckets, per_item=True),
        "recent_genres": _finalize(recent_genres),
        "longterm_genres": _finalize(longterm_genres),
        "keywords": _finalize(keywords),
        "tmdb_keywords": _finalize(tmdb_keywords),
        "people": _finalize(people),
        "companies": _finalize(companies),
        "collections": _finalize(collections),
        "language_evidence": sum(row["evidence"] for row in languages.values()),
        "rated_count": rated_count,
        "positive_count": len(positives),
        "negative_count": len(negatives),
        "approved_count": decision_counts["approved"],
        "rejected_count": decision_counts["rejected"],
        "rejected_but_watched": decision_counts["rejected_but_watched"],
        "ignored_rows": {key: value for key, value in ignored.items() if value},
        "provider_usage": usage,
        "seed_docs": seed_docs,
        "lane_seed_docs": lane_seed_docs,
        "engine": "structured_taste_v3",
    }
    return snapshot


def genre_pair(left: str, right: str) -> str:
    return "|".join(sorted((str(left).casefold(), str(right).casefold())))


def _affinity(store: Dict[str, Dict[str, float]], name: str) -> Tuple[float, float]:
    row = store.get(name) or store.get(str(name).casefold()) or {}
    return float(row.get("affinity") or 0.0), float(row.get("confidence") or 0.0)


def _lookup_casefold(store: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
    return {str(name).casefold(): row for name, row in (store or {}).items()}


def candidate_affinity(candidate: Dict[str, Any], taste: Dict[str, Any]) -> float:
    """Confidence-weighted taste match in roughly -1..1.

    v1 returned an unbounded sum of fixed per-genre bonuses, which let any
    Action/Adventure title score as highly as something the user actually rated.
    """
    genre_profile = _lookup_casefold(taste.get("genres") or {})
    if not genre_profile:
        # Legacy snapshot: fall back to the ordered v1 lists.
        favorites = [str(name).casefold() for name in (taste.get("favorite_genres") or [])]
        least = {str(name).casefold() for name in (taste.get("least_preferred_genres") or [])}
        names = {str(name).casefold() for name in (candidate.get("genres") or [])}
        if not names:
            return 0.0
        hit = sum(1.0 - index * 0.1 for index, name in enumerate(favorites) if name in names)
        return max(-1.0, min(1.0, (hit - 1.5 * len(names & least)) / max(len(names), 1)))

    names = taste_genres(candidate)
    if not names:
        return 0.0
    total = 0.0
    weight_sum = 0.0
    for name in names:
        affinity, confidence = _affinity(genre_profile, name)
        total += affinity * (0.35 + 0.65 * confidence)
        weight_sum += 1.0
    score = total / max(weight_sum, 1.0)

    pair_profile = _lookup_casefold(taste.get("genre_pairs") or {})
    if pair_profile and len(names) > 1:
        best = 0.0
        for first in range(len(names)):
            for second in range(first + 1, len(names)):
                affinity, confidence = _affinity(pair_profile, genre_pair(names[first], names[second]))
                best = max(best, affinity * (0.3 + 0.7 * confidence)) if affinity > 0 else best
        score = score * 0.75 + best * 0.25
    return round(max(-1.0, min(1.0, score)), 4)
