"""Central exclusions that survive process restarts via persisted collections."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional, Set, Tuple

from .media_identity import title_key


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp
    except ValueError:
        return None


def _keys(item: Dict[str, Any]) -> Set[str]:
    keys = set()
    if item.get("canonical_media_id"):
        keys.add(f"id:{item['canonical_media_id']}")
    if item.get("tmdb_id"):
        keys.add(f"tmdb:{item.get('media_type') or item.get('type')}:{item['tmdb_id']}")
    if item.get("title") and item.get("year"):
        keys.add(f"slug:{title_key(item.get('title'))}:{item['year']}")
    return keys


def build_exclusion_context(
    history: Iterable[Dict[str, Any]],
    library: Iterable[Dict[str, Any]],
    recommended: Iterable[Dict[str, Any]],
    requested: Iterable[Dict[str, Any]],
    blacklist: Iterable[Dict[str, Any]],
    feedback: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    recommended_at: Dict[str, datetime] = {}
    recommended_keys: Set[str] = set()
    for item in recommended:
        stamp = _parse_dt(item.get("created_at") or item.get("updated_at"))
        for key in _keys(item):
            recommended_keys.add(key)
            if stamp:
                recommended_at[key] = stamp
    feedback_changed: Set[str] = set()
    for item in feedback or []:
        if item.get("action") in {"like", "dislike", "watched"}:
            feedback_changed.update(_keys(item))
    return {
        "watched": {key for item in history for key in _keys(item)},
        "library": {key for item in library for key in _keys(item)},
        "recommended": recommended_keys,
        "recommended_at": recommended_at,
        "requested": {key for item in requested for key in _keys(item)},
        "blacklist": {key for item in blacklist for key in _keys(item)},
        "feedback_changed": feedback_changed,
        "seen_candidates": set(),
    }


def apply_exclusions(
    candidate: Dict[str, Any],
    context: Dict[str, Set[str]],
    exclusions: Optional[Dict[str, bool]] = None,
) -> Tuple[bool, Optional[str]]:
    exclusions = {
        "already_watched": True,
        "already_in_library": True,
        "already_requested": True,
        "already_recommended": False,
        "blacklisted": True,
        "duplicates": True,
        **(exclusions or {}),
    }
    keys = _keys(candidate)
    if exclusions.get("blacklisted") and keys & context["blacklist"]:
        return False, "rejected_blacklisted"
    if exclusions.get("already_watched") and keys & context["watched"]:
        return False, "rejected_already_watched"
    if exclusions.get("already_in_library") and keys & context["library"]:
        return False, "rejected_existing_library"
    if exclusions.get("already_requested") and keys & context["requested"]:
        return False, "rejected_already_requested"
    if exclusions.get("already_recommended") and keys & context["recommended"]:
        feedback_ok = exclusions.get("allow_if_feedback_changed") and keys & (context.get("feedback_changed") or set())
        if not feedback_ok:
            days = exclusions.get("recommend_again_after_days")
            if days:
                cutoff = datetime.now(timezone.utc) - timedelta(days=int(days))
                recent = False
                for key in keys:
                    stamp = (context.get("recommended_at") or {}).get(key)
                    if stamp and stamp > cutoff:
                        recent = True
                        break
                if recent:
                    return False, "rejected_already_recommended"
            else:
                return False, "rejected_already_recommended"
    if exclusions.get("duplicates") and keys & context["seen_candidates"]:
        return False, "duplicate_candidate"
    context["seen_candidates"].update(keys)
    return True, None
