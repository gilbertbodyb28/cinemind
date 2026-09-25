"""One pipeline for preview, manual run and scheduled run."""

from typing import Any, Dict, List, Optional
import math

from .candidate_engine import expand_from_seeds, merge_candidate_sources
from .exclusion_engine import apply_exclusions, build_exclusion_context
from .filter_engine import apply_filters
from .job_intent import (
    LIVE_ACTION_SHARE,
    OFF_INTENT,
    OFF_INTENT_SHARE,
    PRIMARY,
    SECONDARY,
    intent_tier,
    is_mixed,
    job_intent,
)
from .media_identity import content_lane, title_key
from .ranking_engine import apply_diversity, apply_lane_balance, apply_rerank, score_candidates
from .taste_engine import build_taste_snapshot


def default_job() -> Dict[str, Any]:
    return {
        "job_type": "personalized",
        "media_types": ["movie", "tv"],
        "taste_sources": ["plex", "trakt", "simkl", "anilist", "demo"],
        "candidate_sources": ["seed_expand"],
        "provider_weights": {},
        "filters": {},
        "exclusions": {
            "already_watched": True,
            "already_in_library": True,
            "already_requested": True,
            "already_recommended": False,
            "recommend_again_after_days": None,
            "allow_if_feedback_changed": False,
            "blacklisted": True,
        },
        "ai_enabled": False,
        "candidate_limit": 40,
        "final_recommendation_limit": 8,
        "action_mode": "require_approval",
    }


def _select(rows: List[Dict[str, Any]], limit: int, spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    if spec.get("lane_balance", True):
        return apply_lane_balance(rows, limit)
    return apply_diversity(rows, limit)


def _select_mixed(rows: List[Dict[str, Any]], limit: int, spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Live action first in a job that also asks for animated lanes.

    Live action takes up to LIVE_ACTION_SHARE of the slots, the animated lanes
    it named share what is left, and live action tops up anything they cannot
    fill. Every lane the job named still appears; none of the animated ones can
    take the majority while live action has picks to offer.
    """
    live = [row for row in rows if content_lane(row) == "live_action"]
    animated = [row for row in rows if content_lane(row) != "live_action"]
    chosen = _select(live, math.ceil(limit * LIVE_ACTION_SHARE), spec) if live else []
    if animated and len(chosen) < limit:
        chosen += _select(animated, limit - len(chosen), spec)
    if len(chosen) < limit:
        picked = {id(row) for row in chosen}
        rest = [row for row in live if id(row) not in picked]
        if rest:
            chosen += _select(rest, limit - len(chosen), spec)
    order = {id(row): index for index, row in enumerate(rows)}
    return sorted(chosen, key=lambda row: order[id(row)])


#: The least personal evidence a pick needs (ranking_engine.PERSONAL_COMPONENTS).
#: Measured 2026-09-24 on Gilbert's Tv job: once the titles his favourites point
#: to were all in the Requests queue, the job filled 250 slots from popularity
#: pages - Three's Company, The Lucy Show, RuPaul's Drag Race, Gold Rush - each
#: shown at 96-99 %. A title below the floor has no demonstrable link to the
#: history; a job now returns fewer picks rather than those. Calibrated on the
#: complete-data snapshot (HANDOFF.md, omgång 4): 2.0 kept 97 % of the held-out
#: 8-10/10 live-action titles in the TV job's own pool (88 % across all formats)
#: and 45 % of the rest of that pool. Recalibrated to 2.5 with the weights of
#: 2026-09-25 (§34), which lift every personal score: at 2.5 it again keeps 97 %
#: of those favourites (86 % across formats) and 37 % of the rest (was 38 %), and
#: 93 % of approved Requests against 44 % of rejected ones (was 93 / 46 %).
TASTE_FLOOR = 2.5
#: Below this many liked titles there is no personal evidence to hold a pick to
#: (a new account, a job whose taste sources hold almost nothing): the floor is off
#: rather than emptying the list.
MIN_LIKED_FOR_FLOOR = 10


def taste_floor(spec: Dict[str, Any]) -> Optional[float]:
    """The floor this job applies; None (or absent) applies none.

    run_pipeline decides it once per run, from the profile it scored with, and
    stores it on the spec it returns - which is the spec select_final and the
    re-rank read afterwards.
    """
    value = spec.get("taste_floor")
    return None if value is None else float(value)


#: ... and at least this concrete a link to a liked title (ranking_engine.
#: specific_evidence): a shared distinctive theme, creator, cast member,
#: franchise or studio, or a recommendation from a liked title. Genre labels,
#: language and format never count as a link.
#: 0.30 is two shared distinctive themes, a shared creator, or one theme plus a
#: shared studio - never a lone format label like "sitcom". Calibrated like the
#: taste floor: it keeps 97 % of held-out live-action favourites in the TV pool.
SPECIFIC_FLOOR = 0.3
#: A title from an era the profile holds nothing from (ranking_engine.era_fit)
#: needs this strong a link - a franchise, a creator, several themes - because
#: "shares the theme sitcom with How I Met Your Mother" is how The Odd Couple
#: (1970) and Sanford and Son (1972) reached a 2020s viewer's TV list.
UNSEEN_ERA_SPECIFIC_FLOOR = 0.6


def clears_taste_floor(row: Dict[str, Any], floor: float) -> bool:
    personal = row.get("personal_score")
    # An unscored list (a caller's own ordering) is not held to a score it never had.
    if personal is None:
        return True
    if float(personal) < floor:
        return False
    specific = row.get("specific_score")
    if specific is None:
        return True
    unseen_era = float((row.get("score_components") or {}).get("era_fit", 0.0)) < 0
    return float(specific) >= (UNSEEN_ERA_SPECIFIC_FLOOR if unseen_era else SPECIFIC_FLOOR)


def select_final(ranked: List[Dict[str, Any]], spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The final slots, after scoring or after the model's re-rank.

    Saved jobs balance their slots across lanes (anime, donghua, animation,
    live action; series and films). Content to Watch opts out with
    lane_balance=False: its top-eight ranking is the measured one in HANDOFF.md.

    A job with an intent (recommendation.job_intent) fills its slots in tiers:
    first what it asked for - lane-balanced across the lanes it named, and only
    those, with live action taking the majority when it is one of them - then,
    if slots are left, the same lanes in other languages, then at most
    OFF_INTENT_SHARE of the slots from lanes it never asked for. Balancing used
    to hand anime, donghua and animation a guaranteed share of an English TV job
    because they happened to be in the pool.

    Nothing below the job's taste floor is selected at all (see taste_floor):
    a job asking for 250 titles used to take whatever survived the filters.
    """
    limit = int(spec.get("final_recommendation_limit") or 8)
    floor = taste_floor(spec)
    if floor is not None:
        ranked = [row for row in ranked if clears_taste_floor(row, floor)]
    if not spec.get("diversity", True):
        return ranked[:limit]
    intent = job_intent(spec)
    if intent is None:
        return _select(ranked, limit, spec)
    tiers: Dict[int, List[Dict[str, Any]]] = {PRIMARY: [], SECONDARY: [], OFF_INTENT: []}
    for row in ranked:
        tiers[intent_tier(row, intent)].append(row)
    select_primary = _select_mixed if is_mixed(intent) else _select
    chosen = select_primary(tiers[PRIMARY], limit, spec)
    for tier, cap in ((SECONDARY, limit), (OFF_INTENT, int(limit * OFF_INTENT_SHARE))):
        room = min(limit - len(chosen), cap)
        if room > 0 and tiers[tier]:
            # Each tier is measured against its own best: a fill is the strongest
            # of what is left, never a weak pick pulled up to look varied.
            chosen.extend(apply_diversity(tiers[tier], room))
    return chosen


def run_pipeline(
    job: Dict[str, Any],
    *,
    history: List[Dict[str, Any]],
    library: Optional[List[Dict[str, Any]]] = None,
    recommended: Optional[List[Dict[str, Any]]] = None,
    requested: Optional[List[Dict[str, Any]]] = None,
    blacklist: Optional[List[Dict[str, Any]]] = None,
    feedback: Optional[List[Dict[str, Any]]] = None,
    catalog: Optional[List[Dict[str, Any]]] = None,
    extra_candidates: Optional[List[Dict[str, Any]]] = None,
    rerank_ids: Optional[List[str]] = None,
    personal_history: Optional[List[Dict[str, Any]]] = None,
    taste: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    spec = {**default_job(), **job}
    # Callers that generate candidates from the profile pass the snapshot they
    # already built, so it is not computed twice per run.
    taste = taste or build_taste_snapshot(
        history,
        feedback=feedback,
        provider_weights=spec.get("provider_weights"),
        taste_sources=spec.get("taste_sources"),
        personal_history=personal_history,
        requests=requested,
    )
    liked = taste.get("liked_titles") or taste.get("high_confidence_positive_titles") or []
    if "taste_floor" not in spec:
        spec["taste_floor"] = TASTE_FLOOR if len(liked) >= MIN_LIKED_FOR_FLOOR else None
    generated = []
    sources = spec.get("candidate_sources") or ["seed_expand"]
    if ("seed_expand" in sources or spec.get("job_type") in {"personalized", "discover"}) and catalog:
        generated.append(expand_from_seeds(history, catalog, spec.get("media_types"), spec.get("candidate_limit", 40), taste=taste))
    if extra_candidates:
        generated.append(extra_candidates)

    # A title the user rated has been seen, whether or not a play was logged:
    # Legion is 10/10 on Trakt with no play, and came back as a recommendation.
    rated = [row for row in personal_history or [] if row.get("rating") is not None]
    context = build_exclusion_context(
        list(history) + rated, library or [], recommended or [], requested or [], blacklist or [], feedback=feedback
    )
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for candidate in merge_candidate_sources(*generated):
        candidate["candidate_id"] = candidate.get("candidate_id") or f"{title_key(candidate.get('title'))}:{candidate.get('year')}"
        ok, reason = apply_filters(candidate, {**(spec.get("filters") or {}), "media_types": spec.get("media_types")})
        if not ok:
            rejected.append({**candidate, "filter_outcome": reason})
            continue
        ok, reason = apply_exclusions(candidate, context, spec.get("exclusions"))
        if not ok:
            rejected.append({**candidate, "filter_outcome": reason})
            continue
        accepted.append({**candidate, "filter_outcome": "accepted"})

    intent = job_intent(spec)
    ranked = score_candidates(accepted, taste, weights=spec.get("score_weights"), intent=intent)
    if intent:
        # What the job asked for first, then the rest; the order inside a tier
        # is the score's. The model's re-rank pool is the head of this list, so
        # it spends its slots on titles the job can actually use.
        for row in ranked:
            row["job_tier"] = intent_tier(row, intent)
        ranked.sort(key=lambda row: row["job_tier"])
    ranked = apply_rerank(ranked, rerank_ids)
    selected = select_final(ranked, spec)
    floor = taste_floor(spec)
    return {
        "taste": taste,
        "accepted": selected,
        "ranked": ranked,
        "rejected": rejected,
        "candidate_count": len(accepted) + len(rejected),
        "taste_floor": floor,
        "below_taste_floor": 0 if floor is None else sum(1 for row in ranked if not clears_taste_floor(row, floor)),
        "job": spec,
    }
