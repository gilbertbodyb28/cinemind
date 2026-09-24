"""One pipeline for preview, manual run and scheduled run."""

from typing import Any, Dict, List, Optional

from .candidate_engine import expand_from_seeds, merge_candidate_sources
from .exclusion_engine import apply_exclusions, build_exclusion_context
from .filter_engine import apply_filters
from .media_identity import title_key
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


def select_final(ranked: List[Dict[str, Any]], spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The final slots, after scoring or after the model's re-rank.

    Saved jobs balance their slots across lanes (anime, donghua, animation,
    live action; series and films). Content to Watch opts out with
    lane_balance=False: its top-eight ranking is the measured one in HANDOFF.md.
    """
    limit = int(spec.get("final_recommendation_limit") or 8)
    if not spec.get("diversity", True):
        return ranked[:limit]
    if spec.get("lane_balance", True):
        return apply_lane_balance(ranked, limit)
    return apply_diversity(ranked, limit)


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
    )
    generated = []
    sources = spec.get("candidate_sources") or ["seed_expand"]
    if ("seed_expand" in sources or spec.get("job_type") in {"personalized", "discover"}) and catalog:
        generated.append(expand_from_seeds(history, catalog, spec.get("media_types"), spec.get("candidate_limit", 40), taste=taste))
    if extra_candidates:
        generated.append(extra_candidates)

    context = build_exclusion_context(
        history, library or [], recommended or [], requested or [], blacklist or [], feedback=feedback
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

    ranked = score_candidates(accepted, taste, weights=spec.get("score_weights"))
    ranked = apply_rerank(ranked, rerank_ids)
    selected = select_final(ranked, spec)
    return {
        "taste": taste,
        "accepted": selected,
        "ranked": ranked,
        "rejected": rejected,
        "candidate_count": len(accepted) + len(rejected),
        "job": spec,
    }
