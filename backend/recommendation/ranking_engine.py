"""Deterministic candidate scoring. Ollama may only reorder verified IDs.

Every component is normalized to -1..1 before it is weighted, so no single
signal can swamp the rest. v1 added TMDb's raw `popularity/10 + vote_average`
straight into the total, which put daily-updating talk shows and a German news
bulletin above everything the user had ever rated.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import math

from .media_identity import coerce_int, content_lane
from .similarity import (
    best_similarity,
    best_specific,
    franchise_affinity,
    keyword_affinity,
    keyword_idf,
    media_type_affinity,
    people_affinity,
    recency_affinity,
)
from .taste_engine import (
    LANGUAGE_EVIDENCE_FOR_PENALTY,
    candidate_affinity,
    genre_label,
    media_bucket,
    taste_genres,
)

# Taste outweighs catalogue popularity by design: the two similarity terms
# together can contribute 7.6, popularity at most 0.35.
DEFAULT_WEIGHTS = {
    "taste_similarity": 2.6,
    # Measured over three 8-fold sweeps, not reasoned about. Format matters more
    # than it used to because the format is finally correct: 113 anime titles
    # were being counted as ordinary drama series, so the signal was noise. At
    # 2.2 the enriched profile beats the genre-only one on every ranking metric
    # (holdout P@5 0.750 -> 0.825, NDCG@5 0.824 -> 0.882); at 0.8 it loses
    # badly (P@5 0.575). Higher was not chased: a format-dominated ranker would
    # simply always pick anime for this viewer.
    #
    # liked_title_similarity 4.2 -> 5.0 and people_affinity 1.1 -> 1.6
    # (2026-09-25, HANDOFF.md §34): once genres
    # were spelled one way across providers, genre-level terms stopped separating
    # a favourite from a title that merely shares its genres, and the concrete
    # signals had to carry more. Measured together with NEGATIVE_EVIDENCE_SCALE,
    # 5 seeds x 8 folds on snap_live: broad holdout P@5 0.335 -> 0.370, NDCG@10
    # 0.317 -> 0.351; Tv pool P@5 0.565 -> 0.585; Requests AUC 0.885 -> 0.881;
    # controls 0.050 / 0.100. Rejected on the way: taste_similarity 1.8,
    # people 2.0, keyword 1.8, franchise 1.0, 48 reference titles.
    "liked_title_similarity": 5.0,
    "recent_interest": 1.0,
    "keyword_affinity": 1.4,
    "people_affinity": 1.6,
    "franchise_affinity": 0.6,
    "media_type_fit": 2.2,
    "language_fit": 1.1,
    "era_fit": 0.4,
    "quality": 1.3,
    "metadata_confidence": 0.5,
    "popularity": 0.35,
    "source_confidence": 0.4,
    "negative_affinity": 2.6,
    "thin_evidence": 1.2,
    # How many of the user's own liked titles pointed at this candidate (TMDb
    # recommendations / similar seeded from them). Scored and shown in the debug
    # trace, but weighted 0: the offline harness cannot measure it fairly - its
    # held-out titles are injected without the seeds a live run would give them -
    # and the weights here only change on measurement (1.6 measured
    # TV-job NDCG@10 0.405 -> 0.347 in that biased setting).
    "seed_support": 0.0,
    # Only scored for saved jobs (recommendation.job_intent); Content to Watch
    # and the offline harness never carry these two components. job_fit is the
    # largest single job term on purpose: an English TV job must not be won by
    # an anime the profile happens to love. It moves a title 5 points between
    # "what the job asked for" and "a lane it never mentioned".
    "job_fit": 2.5,
    "job_genre_fit": 0.8,
}

#: Components that describe the job, not the viewer. They order a job's list
#: but stay out of match_score and the "why" text, which are about the viewer.
JOB_COMPONENTS = ("job_fit", "job_genre_fit")

#: What this viewer's own history and decisions say about the title. Their sum is
#: `personal_score`: the only thing match_score is calibrated on, and what the
#: taste floor of a personalised job reads. Format, language, era, quality,
#: metadata, source and popularity still order the list, but none of them can
#: make a title a match - an English sitcom used to show 96-99 % because the
#: format and language terms alone added 3.25 to every English series.
PERSONAL_COMPONENTS = (
    "taste_similarity", "liked_title_similarity", "seed_support", "recent_interest",
    "keyword_affinity", "people_affinity", "franchise_affinity", "negative_affinity",
)

# Bayesian prior for TMDb ratings: a 9.0 from four voters is not a 9.0.
RATING_PRIOR_VOTES = 200.0
RATING_PRIOR_MEAN = 6.4

SOURCE_CONFIDENCE = {
    "tmdb_recommendations": 1.0,
    # TMDb's /similar is built from genres and keywords, not from viewers, and
    # for a film like Creed III it returns Bullitt (1968) and Scarface (1932).
    # /recommendations (what other viewers went on to watch) keeps full trust.
    # Measured neutral offline; the live TV job is where it showed.
    "tmdb_similar": 0.6,
    "anilist": 0.9,
    "anilist_upcoming": 0.7,
    "trakt": 0.85,
    "simkl": 0.8,
    "taste_seeded_discover": 0.8,
    "taste_keyword_discover": 0.8,
    "tmdb_discover": 0.55,
    "seed_expand": 0.5,
    # The offline harness injects held-out positives under this source. Pinned to
    # the floor so a measured gain can never be an artefact of the label's origin.
    "heldout_challenge": 0.5,
}

METADATA_FIELDS = ("genres", "synopsis", "original_language", "year", "poster")


def _released(candidate: Dict[str, Any], today: Optional[datetime] = None) -> bool:
    today = today or datetime.now(timezone.utc)
    for key in ("release_date", "first_air_date", "aired_at"):
        value = candidate.get(key)
        if value and len(str(value)) >= 10:
            return str(value)[:10] <= today.date().isoformat()
    year = coerce_int(candidate.get("year"))
    return year is None or year <= today.year


def quality_score(candidate: Dict[str, Any]) -> float:
    """Vote-count-smoothed community rating in 0..1; unrated lands on the prior."""
    try:
        rating = float(candidate.get("tmdb_rating") or candidate.get("rating") or 0.0)
    except (TypeError, ValueError):
        rating = 0.0
    votes = float(coerce_int(candidate.get("vote_count")) or 0)
    smoothed = (votes * rating + RATING_PRIOR_VOTES * RATING_PRIOR_MEAN) / (votes + RATING_PRIOR_VOTES)
    return round(max(0.0, min(1.0, smoothed / 10.0)), 4)


def metadata_confidence(candidate: Dict[str, Any]) -> float:
    present = sum(1 for field in METADATA_FIELDS if candidate.get(field) not in (None, "", [], {}))
    return round(present / len(METADATA_FIELDS), 4)


def thin_evidence_penalty(candidate: Dict[str, Any]) -> float:
    """A released title nobody has voted on is obscure, not undiscovered.

    Unreleased titles are exempt: they are judged on metadata fit alone, which
    is the only honest way to rank something that has not aired yet. A source
    that does not publish vote counts at all (AniList, Simkl) is exempt too -
    absent is not the same as zero, and punishing it would quietly demote every
    anime candidate.
    """
    if not _released(candidate):
        return 0.0
    if candidate.get("vote_count") is None:
        return 0.0
    votes = coerce_int(candidate.get("vote_count"))
    if votes is None or votes < 10:
        return -1.0
    if votes < 50:
        return -0.4
    return 0.0


def popularity_score(candidate: Dict[str, Any]) -> float:
    try:
        value = float(candidate.get("popularity") or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    if value <= 0:
        return 0.0
    return round(min(1.0, math.log10(1.0 + value) / 3.0), 4)


def _profile_affinity(store: Dict[str, Any], key: Optional[str]) -> float:
    if not store or not key:
        return 0.0
    row = store.get(str(key)) or store.get(str(key).casefold()) or {}
    return float(row.get("affinity") or 0.0) * (0.4 + 0.6 * float(row.get("confidence") or 0.0))


#: An era the profile holds (almost) nothing from, in a profile that holds a lot.
#: Like an unseen language (language_fit), absence only counts once there is
#: enough history for it to mean something.
#: Measured neutral on every benchmark (the frozen pools rarely rank such titles
#: high); kept because the live TV job's similarity lanes did: Tabu (1931),
#: Scarface (1932) and The Quiet Man (1952) for a viewer with nothing before 1980.
UNSEEN_ERA_PENALTY = 0.6
ERA_EVIDENCE_FOR_PENALTY = 60


def era_fit(candidate: Dict[str, Any], taste: Dict[str, Any]) -> float:
    year = coerce_int(candidate.get("year"))
    if year is None:
        return 0.0
    decades = taste.get("decades") or {}
    if decades:
        key = "%ds" % ((year // 10) * 10)
        value = _profile_affinity(decades, key)
        if UNSEEN_ERA_PENALTY and value <= 0.02:
            evidence = sum(float(row.get("evidence") or 0.0) for row in decades.values())
            if evidence >= ERA_EVIDENCE_FOR_PENALTY:
                return -UNSEEN_ERA_PENALTY
        return round(value, 4)
    return 1.0 if "%ds" % ((year // 10) * 10) in (taste.get("favorite_eras") or []) else 0.0


def language_fit(candidate: Dict[str, Any], taste: Dict[str, Any]) -> float:
    """Positive for languages the user watches, negative for ones they never do.

    A language absent from the profile used to score exactly the same as their
    most-watched one - both 0.0 - so a Tagalog soap and an English drama were
    indistinguishable on this axis. Absence only counts against a title once the
    profile holds enough languages to make the absence mean something.
    """
    language = str(candidate.get("original_language") or "").casefold()
    if not language:
        return 0.0
    languages = taste.get("languages") or {}
    if languages:
        if language in languages:
            return round(_profile_affinity(languages, language), 4)
        evidence = float(taste.get("language_evidence") or 0.0) or sum(
            float(row.get("evidence") or 0.0) for row in languages.values()
        )
        return -0.7 if evidence >= LANGUAGE_EVIDENCE_FOR_PENALTY else 0.0
    preferred = {str(item).casefold() for item in (taste.get("preferred_languages") or [])}
    return 1.0 if language in preferred else 0.0


def negative_affinity(
    candidate: Dict[str, Any],
    taste: Dict[str, Any],
    negative_hit: Dict[str, Any],
    liked_hit: Dict[str, Any],
) -> float:
    """How much the candidate looks like something the user rejected, as 0..-1.

    Only the *margin* over the positive match counts. Comparing raw similarity
    flagged every single candidate, because anything in the user's genres also
    resembles the handful of titles they dropped in those same genres.
    """
    names = set(taste_genres(candidate))
    disliked = {str(name).casefold() for name in (taste.get("disliked_genres") or [])}
    score = 0.0
    if names and disliked:
        score -= min(1.0, len(names & disliked) / max(len(names), 1))
    margin = float(negative_hit.get("score") or 0.0) - float(liked_hit.get("score") or 0.0)
    if margin > 0:
        score -= min(1.0, margin * NEGATIVE_MARGIN_SCALE)
    return round(max(-1.0, score), 4)


def source_confidence(candidate: Dict[str, Any]) -> float:
    return SOURCE_CONFIDENCE.get(str(candidate.get("source") or ""), 0.6)


def seed_titles(candidate: Dict[str, Any]) -> List[str]:
    seeds = list(candidate.get("seed_titles") or [])
    if candidate.get("source_seed") and candidate["source_seed"] not in seeds:
        seeds.append(candidate["source_seed"])
    return [str(seed) for seed in seeds if seed]


def seed_support(candidate: Dict[str, Any], liked_by_title: Dict[str, Dict[str, Any]], peak: float) -> Tuple[float, List[str]]:
    """0..1: how many of the user's liked titles led a provider to this one.

    Only seeds that are liked titles in *this* profile count. The offline
    harness holds titles out of the profile, and a candidate a held-out title
    led to must not borrow that title's rating.
    """
    strengths: List[Tuple[float, str]] = []
    for title in seed_titles(candidate):
        liked = liked_by_title.get(title.casefold())
        if liked:
            strengths.append((min(1.0, max(0.0, float(liked.get("score") or 0.0)) / (peak or 1.0)), liked["title"]))
    if not strengths:
        return 0.0, []
    strengths.sort(key=lambda pair: -pair[0])
    missing = 1.0
    for strength, _ in strengths:
        missing *= 1.0 - 0.5 * strength
    return round(1.0 - missing, 4), [title for _, title in strengths]


def _matched(
    candidate: Dict[str, Any],
    fields: Tuple[str, ...],
    profile: Dict[str, Any],
    prefix: str = "",
    limit: int = 2,
) -> List[str]:
    """The profile entries this candidate actually hit, strongest first."""
    if not profile:
        return []
    hits: List[Tuple[float, str]] = []
    seen = set()
    for field in fields:
        for raw in candidate.get(field) or []:
            name = str(raw)
            row = profile.get(prefix + name.casefold())
            if not row or name.casefold() in seen:
                continue
            affinity = float(row.get("affinity") or 0.0)
            if affinity <= 0:
                continue
            seen.add(name.casefold())
            hits.append((affinity, name))
    hits.sort(key=lambda pair: -pair[0])
    return [name for _, name in hits[:limit]]


def _liked_mark(liked: Optional[Dict[str, Any]]) -> str:
    """"which you rated 10/10" / "which you watched 40 episodes of" - the evidence, not a slogan."""
    if not liked:
        return "which you rated highly"
    if liked.get("rating") is not None:
        return "which you rated %.0f/10" % float(liked["rating"])
    if liked.get("decision") == "approved":
        return "which you approved in Requests"
    plays = int(liked.get("plays") or 0)
    if plays > 1:
        return "which you watched %d episodes of" % plays
    return "which you liked"


def _explain(components: Dict[str, float], weights: Dict[str, float], candidate: Dict[str, Any],
             liked_hit: Dict[str, Any], taste: Dict[str, Any],
             intent: Optional[Dict[str, Any]] = None,
             liked_row: Optional[Dict[str, Any]] = None,
             seeds: Optional[List[str]] = None,
             negative_hit: Optional[Dict[str, Any]] = None,
             link_row: Optional[Dict[str, Any]] = None) -> Tuple[str, List[str], List[str]]:
    """Reasons taken from the contributions that actually decided the rank.

    v1 fell back to the profile's top two genres whenever nothing matched, so a
    documentary was explained as "matches Action, Adventure".

    The job terms are the same for every title the job asked for, so they say
    nothing about why this one suits the viewer; they only appear as a penalty,
    when a title is outside what the job asked for.
    """
    from .similarity import explain_match

    contributions = sorted(
        ((name, components[name] * weights.get(name, 0.0)) for name in components
         if name not in JOB_COMPONENTS),
        key=lambda pair: -pair[1],
    )
    profile = taste.get("genres") or {}
    shared = [genre_label(name) for name in taste_genres(candidate)
              if (profile.get(name) or {}).get("affinity", 0) > 0][:3]
    themes = _matched(candidate, ("tmdb_keywords", "tags"), taste.get("tmdb_keywords") or {}, limit=2)
    creators = _matched(candidate, ("creators",), taste.get("people") or {}, prefix="creator:", limit=1)
    actors = _matched(candidate, ("cast",), taste.get("people") or {}, prefix="cast:", limit=2)
    # Name the liked title the candidate is concretely linked to (shared themes,
    # people, franchise, studio) - not the one it merely shares genre labels with.
    reference = liked_row
    overlap = [part for part in explain_match(candidate, liked_row) if not part.startswith("genres ")][:2] if liked_row else []
    if link_row is not None and link_row is not liked_row:
        link_overlap = [part for part in explain_match(candidate, link_row) if not part.startswith("genres ")][:2]
        if link_overlap and not overlap:
            reference, overlap = link_row, link_overlap
    title = (reference or {}).get("title") or liked_hit.get("title")
    like_text = "plays like %s, %s" % (title, _liked_mark(reference)) if title else "resembles titles you rated highly"
    if overlap:
        like_text += " (%s)" % "; ".join(overlap)
    phrases = {
        "liked_title_similarity": like_text,
        "seed_support": "was recommended from %s, %s" % (
            " and ".join((seeds or [])[:2]), "which you rated highly" if len(seeds or []) > 1 else _liked_mark(
                ((taste.get("_liked_by_title") or {}).get(str((seeds or [""])[0]).casefold())))
        ) if seeds else "was recommended from titles you liked",
        "taste_similarity": "matches %s, a combination you keep going back to" % ", ".join(shared)
        if shared else "matches your genre profile",
        "recent_interest": "lines up with what you have been watching lately",
        "keyword_affinity": "is built on %s, which runs through your favourites" % ", ".join(themes)
        if themes else "shares themes with your favourites",
        "people_affinity": "is from %s, whose work you rate highly" % ", ".join(creators)
        if creators else ("stars %s, who you keep watching" % ", ".join(actors) if actors
                          else "shares people with titles you rated highly"),
        "franchise_affinity": "comes from %s, a franchise you follow" % candidate.get("collection")
        if candidate.get("collection") and (taste.get("collections") or {}).get(str(candidate.get("collection")).casefold())
        else "comes from a studio or network you follow",
        "media_type_fit": "is the %s format you watch most" % media_bucket(candidate).replace("_", " "),
        "language_fit": "is in a language you watch a lot",
        "era_fit": "comes from an era you favour",
        "quality": "is well rated by a large audience",
        "popularity": "is widely watched right now",
        "metadata_confidence": "has complete, reliable metadata",
        "source_confidence": "came from a source seeded by your own history",
    }
    # Personal reasons first: format, language and quality are true of almost
    # every pick in a job and never explain why this one suits the viewer.
    personal = [phrases[name] for name, value in contributions
                if value > 0.15 and name in phrases and name in PERSONAL_COMPONENTS][:3]
    context = [phrases[name] for name, value in contributions
               if value > 0.15 and name in phrases and name not in PERSONAL_COMPONENTS]
    positive = (personal + context)[:3] if personal else []
    penalties = []
    for name, value in contributions:
        if value >= -0.15:
            continue
        if name == "negative_affinity":
            hit = (negative_hit or {}).get("title")
            penalties.append("resembles %s, which you turned down" % hit if hit else "overlaps genres you rejected")
        elif name == "thin_evidence":
            penalties.append("almost nobody has rated it yet")
        elif name == "metadata_confidence":
            penalties.append("incomplete metadata")
        elif name == "language_fit":
            penalties.append("is in a language you never watch")
    if intent and components.get("job_fit", 0.0) < 0:
        from .job_intent import describe_fit

        penalties.insert(0, "is %s, which this job did not ask for" % describe_fit(candidate, intent))
    if positive:
        why = "%s %s." % (candidate.get("title"), "; ".join(positive))
    else:
        why = "%s has no clear link to your history; it fills a slot." % candidate.get("title")
    return why, positive, penalties[:3]


#: match_score is a fixed logistic calibration of personal_score, never a
#: min-max over one run: v1 rescaled each run's own range, so eight equally poor
#: picks all showed 77%. Calibrated on the real profile (see HANDOFF.md): a
#: held-out 9-10/10 title lands around 80-95 %, a pool title with no link to the
#: history around 20-40 %. Moved with pipeline.TASTE_FLOOR (2.0 -> 2.5) when the
#: weights of 2026-09-25 lifted every personal score, so a title at the floor
#: still shows about half.
MATCH_CENTER = 2.5
MATCH_SCALE = 0.9


#: How many disliked / turned-down titles a candidate is compared with. They all
#: carry the same evidence, so a short list was an arbitrary subset of them.
NEGATIVE_REFERENCES = 0
#: Compare candidates title by title with what was turned down in Requests.
REJECTIONS_AS_REFERENCES = True
#: How far a margin over the best liked match pushes a title down (x margin, capped at 1).
NEGATIVE_MARGIN_SCALE = 2.0
#: Evidence scale for the turned-down references. None was relative to the
#: strongest of them, which made a queue rejection (-1.5) count in full, as
#: much as a 2/10 rating, while liked titles are weighed against a 10/10
#: favourite: titles rated 8-10 took -0.13 on average for resembling
#: something turned down in Requests. "liked": the same absolute scale as the
#: liked titles (Tv pool P@5 0.565 -> 0.585 with the weights above).
NEGATIVE_EVIDENCE_SCALE = "liked"

#: specific_score (0..1) at which a title counts as concretely linked in full.
SPECIFIC_FULL = 0.5
#: How much of seed support counts as a concrete link (one liked seed: 0.3).
SEED_LINK_SHARE = 0.6


def personal_total(contributions: Dict[str, float]) -> float:
    return sum(float(contributions.get(name) or 0.0) for name in PERSONAL_COMPONENTS)


def match_percent(personal: float, specific: Optional[float] = None) -> int:
    """The viewer-facing percentage: personal evidence, capped when none of it is concrete.

    A title whose only link to the history is its genre labels can resemble the
    profile but is not a match: without a concrete link it tops out a little
    over half of what the same personal score would show with one.
    """
    base = 100 / (1 + math.exp(-(personal - MATCH_CENTER) / MATCH_SCALE))
    if specific is not None:
        base *= 0.55 + 0.45 * min(1.0, max(0.0, specific) / SPECIFIC_FULL)
    return max(1, min(99, int(round(base))))


def specific_evidence(candidate: Dict[str, Any], taste: Dict[str, Any], link: Dict[str, Any],
                      support: float, components: Dict[str, float]) -> float:
    """0..1: the strongest concrete reason this title belongs to this viewer.

    A shared distinctive theme, creator, cast member, franchise or studio with a
    liked title (similarity.best_specific); being recommended from a liked title
    (seed support); or a creator / franchise the profile follows.
    """
    collection = str(candidate.get("collection") or "").casefold()
    franchise = max(0.0, components.get("franchise_affinity", 0.0)) if collection and (
        taste.get("collections") or {}).get(collection) else 0.0
    # One TMDb "viewers also watched" from a liked title is a hint, not a link on
    # its own: for The Fresh Prince it is Sanford and Son (1972).
    return round(min(1.0, max(float(link.get("score") or 0.0), SEED_LINK_SHARE * support,
                              max(0.0, components.get("people_affinity", 0.0)), franchise)), 4)


def score_candidates(
    candidates: List[Dict[str, Any]],
    taste: Dict[str, Any],
    weights: Optional[Dict[str, float]] = None,
    intent: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Score and sort. `intent` (recommendation.job_intent) adds the job terms."""
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    if intent:
        from .job_intent import job_fit, job_genre_fit
    positives = list(taste.get("liked_titles") or taste.get("high_confidence_positive_titles") or [])
    negatives = [row for row in taste.get("negative_titles") or []
                 if REJECTIONS_AS_REFERENCES or row.get("decision") != "rejected"]
    liked_by_title = {}
    for liked in positives:
        liked_by_title.setdefault(str(liked.get("title") or "").casefold(), liked)
    taste["_liked_by_title"] = liked_by_title
    peak = max((float(row.get("score") or 0.0) for row in positives), default=1.0)
    negative_peak = None
    if NEGATIVE_EVIDENCE_SCALE == "liked":
        negative_peak = max([abs(peak)] + [abs(float(row.get("score") or 0.0)) for row in negatives]) or None
    # Keyword rarity over everything this run compares: a keyword on half the
    # pool ("sequel", "based on novel or book") says nothing about two titles.
    idf = keyword_idf(list(candidates) + positives + negatives)
    scored: List[Dict[str, Any]] = []
    for candidate in candidates:
        liked_hit = best_similarity(candidate, positives, idf=idf)
        negative_hit = best_similarity(candidate, negatives, limit=NEGATIVE_REFERENCES or None, idf=idf,
                                       peak=negative_peak) if negatives else {"score": 0.0}
        support, supporting = seed_support(candidate, liked_by_title, peak)
        link = best_specific(candidate, positives, idf) if positives else {"score": 0.0, "title": None}
        components = {
            "taste_similarity": candidate_affinity(candidate, taste),
            "liked_title_similarity": liked_hit["score"],
            "seed_support": support,
            "recent_interest": recency_affinity(candidate, taste),
            "keyword_affinity": keyword_affinity(candidate, taste, idf),
            "people_affinity": people_affinity(candidate, taste),
            "franchise_affinity": franchise_affinity(candidate, taste),
            "media_type_fit": media_type_affinity(candidate, taste),
            "language_fit": language_fit(candidate, taste),
            "era_fit": era_fit(candidate, taste),
            "quality": quality_score(candidate),
            "metadata_confidence": metadata_confidence(candidate),
            "popularity": popularity_score(candidate),
            "source_confidence": source_confidence(candidate),
            "negative_affinity": negative_affinity(candidate, taste, negative_hit, liked_hit),
            "thin_evidence": thin_evidence_penalty(candidate),
        }
        if intent:
            components["job_fit"] = job_fit(candidate, intent)
            components["job_genre_fit"] = job_genre_fit(candidate, intent)
        contributions = {name: value * weights.get(name, 0.0) for name, value in components.items()}
        total = sum(contributions.values())
        personal = personal_total(contributions)
        row = dict(candidate)
        for private in ("_tokens", "_sim", "_taste_genres"):
            row.pop(private, None)
        row["score_components"] = {name: round(value, 4) for name, value in components.items()}
        row["score_contributions"] = {name: round(value, 4) for name, value in contributions.items()}
        row["personal_score"] = round(personal, 4)
        row["specific_score"] = specific_evidence(candidate, taste, link, support, components)
        if link.get("title"):
            row["specific_link"] = link["title"]
        row["deterministic_score"] = round(total, 4)
        row["rank_score"] = total
        row["similar_to"] = liked_hit.get("matches") or []
        if supporting:
            row["seed_titles"] = supporting
        if negative_hit.get("title") and components["negative_affinity"] < 0:
            row["resembles_rejected"] = negative_hit.get("title")
        liked_row = liked_by_title.get(str(liked_hit.get("title") or "").casefold())
        why, evidence, penalties = _explain(
            components, weights, candidate, liked_hit, taste, intent,
            liked_row=liked_row, seeds=supporting, negative_hit=negative_hit,
            link_row=liked_by_title.get(str(link.get("title") or "").casefold()),
        )
        # The evidence-based reason always wins. A provider's generic blurb
        # ("Suggested from AniList titles on your list") told the user nothing
        # about why this title in particular reached their list.
        row["why"] = why
        if candidate.get("why"):
            row["source_note"] = candidate["why"]
        row["why_evidence"] = evidence
        row["why_penalties"] = penalties
        # The percentage is about the viewer: only the personal components
        # count. Format, language, quality, popularity and the job terms order
        # the list but cannot make a title a match.
        row["match_score"] = match_percent(personal, row["specific_score"])
        scored.append(row)
    taste.pop("_liked_by_title", None)
    scored.sort(key=lambda row: (-row["rank_score"], str(row.get("title") or "")))
    return scored


def _franchise_key(row: Dict[str, Any]) -> str:
    """Group sequels and spin-offs. "Pokemon Journeys" and "Pokemon Horizons"
    are the same franchise; splitting only on ":" left them looking distinct."""
    from .media_identity import title_key

    words = title_key(row.get("title")).split()
    if not words:
        return str(row.get("title") or "").casefold()
    head = words[0]
    if len(head) < 4 and len(words) > 1:
        head = "%s %s" % (head, words[1])
    return head


def _diversity_key(row: Dict[str, Any]) -> str:
    return "%s|%s" % (media_bucket(row), _franchise_key(row))


RELEVANCE_FLOOR = 0.75


def relevance_cut(candidates: List[Dict[str, Any]], relevance_floor: float = RELEVANCE_FLOOR) -> float:
    """The score a pick needs to count as strong: within `relevance_floor` of the best."""
    best = max((row.get("rank_score", 0.0) for row in candidates), default=0.0)
    return best - abs(best) * (1.0 - relevance_floor)


def apply_diversity(
    candidates: List[Dict[str, Any]],
    limit: int,
    per_franchise: int = 2,
    relevance_floor: float = RELEVANCE_FLOOR,
    bucket_share: float = 0.6,
) -> List[Dict[str, Any]]:
    """Spread the final slots across franchises and formats, inside the strong pool.

    Diversity runs after relevance. It may skip a third entry from one
    franchise, or a sixth anime in an eight-slot list, only while something
    above `relevance_floor` of the best score is still available to take
    instead; once the strong pool is exhausted it stops varying and falls back
    to pure relevance, so it can never pull a weak pick up just to look varied.
    """
    if not candidates or limit <= 0:
        return candidates[:limit]
    floor = relevance_cut(candidates, relevance_floor)
    per_bucket = max(2, int(math.ceil(limit * bucket_share)))
    chosen: List[Dict[str, Any]] = []
    picked = set()
    franchises: Dict[str, int] = {}
    buckets: Dict[str, int] = {}
    for row in candidates:
        if len(chosen) >= limit:
            break
        if row.get("rank_score", 0.0) < floor:
            break
        franchise, bucket = _franchise_key(row), media_bucket(row)
        if franchises.get(franchise, 0) >= per_franchise or buckets.get(bucket, 0) >= per_bucket:
            continue
        franchises[franchise] = franchises.get(franchise, 0) + 1
        buckets[bucket] = buckets.get(bucket, 0) + 1
        chosen.append(row)
        picked.add(id(row))
    # Second pass: put back what the franchise and format caps skipped. It obeys
    # the same floor as the first - without that a job asking for 250 titles
    # emptied the pool into the list, floor and all, and the request for a long
    # list silently became a request for every candidate that was not excluded.
    for row in candidates:
        if len(chosen) >= limit:
            break
        if row.get("rank_score", 0.0) < floor:
            break
        if id(row) not in picked:
            chosen.append(row)
            picked.add(id(row))
    return chosen[:limit]


def selection_lane(row: Dict[str, Any]) -> str:
    """anime / donghua / animation / live_action, split into series and films."""
    kind = "movie" if media_bucket(row) in {"movie", "anime_movie"} else "series"
    return "%s:%s" % (content_lane(row), kind)


def apply_lane_balance(
    candidates: List[Dict[str, Any]],
    limit: int,
    per_franchise: int = 2,
    lane_share: float = 0.5,
    lane_floor: float = 0.75,
    pool_floor: float = 0.5,
    relevance_floor: float = RELEVANCE_FLOOR,
) -> List[Dict[str, Any]]:
    """Give every lane the job's filters let through a share of a job's slots.

    apply_diversity measures every pick against the single best score. For a
    viewer whose history is mostly anime that best score is always an anime
    film, the floor below it sits above every live-action series, donghua and
    Western animation, and a job asking for Anime + Fantasy + Sci-Fi + Action +
    Adventure came back as anime films and nothing else - 35 of 37 picks for
    Gilbert's "Tv" job on 2026-09-24.

    Here each lane is measured against its own best instead: up to
    `lane_share` of the slots are handed out round-robin to the lanes, taking
    only picks within `lane_floor` of that lane's best, and only lanes whose best
    reaches `pool_floor` of the overall best, so a lane with nothing good in it
    stays empty. Further slots go round-robin to the rest of those strong picks;
    anything still open goes to pure relevance under apply_diversity's rule. The list keeps its relevance order (or the model's), so the first
    pick is exactly what it would have been without balancing.
    """
    if not candidates or limit <= 0:
        return candidates[:limit]
    best = max(row.get("rank_score", 0.0) for row in candidates)
    pool_cut = best - abs(best) * (1.0 - pool_floor)
    lanes: Dict[str, List[Dict[str, Any]]] = {}
    for row in candidates:
        lanes.setdefault(selection_lane(row), []).append(row)
    eligible: List[List[Dict[str, Any]]] = []
    for rows in lanes.values():
        # The lane's best by score, not its first row: after a re-rank the first
        # row is the model's pick, and measuring the lane from it let rows below
        # the lane's real floor through, ahead of stronger rows in other lanes.
        lane_best = max(row.get("rank_score", 0.0) for row in rows)
        if lane_best < pool_cut:
            continue
        cut = lane_best - abs(lane_best) * (1.0 - lane_floor)
        eligible.append([row for row in rows if row.get("rank_score", 0.0) >= cut])
    if len(eligible) <= 1:
        return apply_diversity(candidates, limit, per_franchise=per_franchise, relevance_floor=relevance_floor)
    eligible.sort(key=lambda rows: -max(row.get("rank_score", 0.0) for row in rows))
    quota = max(1, int(limit * lane_share) // len(eligible))
    picked: Dict[int, Dict[str, Any]] = {}
    franchises: Dict[str, int] = {}

    def _take(row: Dict[str, Any]) -> bool:
        franchise = _franchise_key(row)
        if id(row) in picked or franchises.get(franchise, 0) >= per_franchise:
            return False
        franchises[franchise] = franchises.get(franchise, 0) + 1
        picked[id(row)] = row
        return True

    taken = {index: 0 for index in range(len(eligible))}
    cursors = {index: 0 for index in range(len(eligible))}

    def _round_robin(cap: Optional[int]) -> None:
        progressed = True
        while progressed and len(picked) < limit:
            progressed = False
            for index, rows in enumerate(eligible):
                if len(picked) >= limit or (cap is not None and taken[index] >= cap):
                    continue
                while cursors[index] < len(rows):
                    row = rows[cursors[index]]
                    cursors[index] += 1
                    if _take(row):
                        taken[index] += 1
                        progressed = True
                        break

    # Guaranteed share first, then the rest of every lane's strong picks, which
    # are strong by the same 75% rule apply_diversity uses, just per lane.
    _round_robin(quota)
    _round_robin(None)
    if len(picked) < limit:
        remaining = [row for row in candidates if id(row) not in picked]
        for row in apply_diversity(remaining, limit - len(picked), per_franchise=per_franchise,
                                   relevance_floor=relevance_floor):
            picked[id(row)] = row
    order = {id(row): index for index, row in enumerate(candidates)}
    return sorted(picked.values(), key=lambda row: order[id(row)])[:limit]


#: How far the model's order may move a title, in rank_score points, for a job
#: that re-ranks bounded. Measured 2026-09-24 against held-out favourites (16
#: folds, gemma4:12b-it-qat): on a saved TV job's pool the model's own order cut
#: MRR 0.969 -> 0.721, bounded it is neutral (+0.003 NDCG@5); on the broad
#: Content to Watch pool the model's own order lifted MRR 0.483 -> 0.802 and
#: NDCG@5 0.328 -> 0.475, and bounding threw that away (+0.014).
RERANK_MAX_BOOST = 0.3


def strip_private(row: Dict[str, Any]) -> Dict[str, Any]:
    """A copy without the underscore-prefixed working state scoring keeps on rows."""
    return {key: value for key, value in row.items() if not str(key).startswith("_")}


def apply_rerank(candidates: List[Dict[str, Any]], ordered_ids: Optional[List[str]],
                 max_boost: Optional[float] = RERANK_MAX_BOOST) -> List[Dict[str, Any]]:
    """The model's order over verified rows. `max_boost=None` lets it reorder
    its pool freely; a number lets it only break near-ties (see above)."""
    if not ordered_ids:
        return candidates
    by_id = {str(row.get("candidate_id") or row.get("tmdb_id") or row["title"]): row for row in candidates}
    ordered = []
    seen = set()
    for index, key in enumerate(ordered_ids):
        row = by_id.get(str(key))
        if row and str(key) not in seen:
            ranked = strip_private(row)
            ranked["ai_rank"] = index + 1
            ranked["ai_score"] = max(1, 100 - index)
            ordered.append(ranked)
            seen.add(str(key))
    for row in candidates:
        marker = str(row.get("candidate_id") or row.get("tmdb_id") or row["title"])
        if marker not in seen:
            leftover = strip_private(row)
            leftover.setdefault("ai_rank", None)
            ordered.append(leftover)
    # The model may resolve close calls, but cannot replace a large measured
    # taste gap with its own preference. Source popularity and quality already
    # enter rank_score with bounded weights; keep that personal order as the
    # foundation of the final list. Missing scores retain the legacy behavior
    # for callers that supply an unscored list.
    if max_boost is None or any(row.get("rank_score") is None for row in ordered):
        return ordered
    boost_step = max_boost / max(len(ordered_ids) - 1, 1)
    original_position = {
        str(row.get("candidate_id") or row.get("tmdb_id") or row["title"]): index
        for index, row in enumerate(candidates)
    }
    return sorted(
        ordered,
        key=lambda row: (
            -(float(row["rank_score"]) + (
                max_boost - (row["ai_rank"] - 1) * boost_step if row.get("ai_rank") else 0.0
            )),
            original_position[str(row.get("candidate_id") or row.get("tmdb_id") or row["title"])],
        ),
    )
