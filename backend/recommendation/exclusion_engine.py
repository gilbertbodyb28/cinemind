"""Central exclusions that survive process restarts via persisted collections."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional, Set, Tuple

from .media_identity import coerce_int, identity_link_keys, title_key


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


def identity_keys(item: Dict[str, Any]) -> Set[tuple]:
    """Comparable identities across providers, with media-scoped numeric IDs."""
    keys = set(identity_link_keys(item))
    if item.get("canonical_media_id"):
        keys.add(("canonical", str(item["canonical_media_id"])))
    return keys


def _title_year(item: Dict[str, Any]) -> Optional[tuple]:
    title = title_key(item.get("title"))
    year = coerce_int(item.get("year"))
    return (title, year) if title and year is not None else None


def build_exclusion_context(
    history: Iterable[Dict[str, Any]],
    library: Iterable[Dict[str, Any]],
    recommended: Iterable[Dict[str, Any]],
    requested: Iterable[Dict[str, Any]],
    blacklist: Iterable[Dict[str, Any]],
    feedback: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    blacklist_rows = list(blacklist)
    recommended_at: Dict[tuple, datetime] = {}
    recommended_keys: Set[tuple] = set()
    for item in recommended:
        stamp = _parse_dt(item.get("created_at") or item.get("updated_at"))
        for key in identity_keys(item):
            recommended_keys.add(key)
            if stamp:
                recommended_at[key] = stamp
    feedback_changed: Set[tuple] = set()
    for item in feedback or []:
        if item.get("action") in {"like", "dislike", "watched"}:
            feedback_changed.update(identity_keys(item))
    return {
        "watched": {key for item in history for key in identity_keys(item)},
        "library": {key for item in library for key in identity_keys(item)},
        "recommended": recommended_keys,
        "recommended_at": recommended_at,
        "requested": {key for item in requested for key in identity_keys(item)},
        "blacklist": {key for item in blacklist_rows for key in identity_keys(item)},
        # Older feedback blacklist rows did not store media type. Match their
        # title/year without making all candidate deduplication type-blind.
        "blacklist_untyped_titles": {
            key for item in blacklist_rows
            if not (item.get("media_type") or item.get("type"))
            for key in [_title_year(item)] if key is not None
        },
        "feedback_changed": feedback_changed,
        "seen_candidates": set(),
    }


def apply_exclusions(
    candidate: Dict[str, Any],
    context: Dict[str, Any],
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
    keys = identity_keys(candidate)
    legacy_blacklisted = _title_year(candidate) in context.get("blacklist_untyped_titles", set())
    if exclusions.get("blacklisted") and (keys & context["blacklist"] or legacy_blacklisted):
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
