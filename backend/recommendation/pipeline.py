"""One pipeline for preview, manual run and scheduled run."""

from typing import Any, Dict, List, Optional

from .candidate_engine import expand_from_seeds, merge_candidate_sources
from .exclusion_engine import apply_exclusions, build_exclusion_context
from .filter_engine import apply_filters
from .media_identity import title_key
from .ranking_engine import apply_rerank, score_candidates
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
) -> Dict[str, Any]:
    spec = {**default_job(), **job}
    taste = build_taste_snapshot(
        history,
        feedback=feedback,
        provider_weights=spec.get("provider_weights"),
        taste_sources=spec.get("taste_sources"),
    )
    generated = []
    sources = spec.get("candidate_sources") or ["seed_expand"]
    if "seed_expand" in sources or spec.get("job_type") in {"personalized", "discover"}:
        generated.append(expand_from_seeds(history, catalog or [], spec.get("media_types"), spec.get("candidate_limit", 40)))
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

    ranked = score_candidates(accepted, taste)
    ranked = apply_rerank(ranked, rerank_ids)
    limit = int(spec.get("final_recommendation_limit") or 8)
    return {
        "taste": taste,
        "accepted": ranked[:limit],
        "ranked": ranked,
        "rejected": rejected,
        "candidate_count": len(accepted) + len(rejected),
        "job": spec,
    }
