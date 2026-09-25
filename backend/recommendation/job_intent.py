"""What a saved job asks for, so discovery and ranking serve the job and not the profile's habits.

Nothing between the job form and the final list used to remember what the job
had asked for. Gilbert's profile is anime-heavy, so a job asking for TV in
Fantasy / Sci-Fi / Action / Adventure came back as Japanese anime, donghua and
kids' cartoons: the TV filter let anime through as "tv", the discover lane asked
TMDb for the world's most popular TV of any language and format, the "more like
this" lanes were seeded from his anime favourites, AniList was queried for a job
that never mentioned anime, and lane balancing then handed anime, donghua and
animation a guaranteed share each. Measured on 2026-09-24: 7 of the top 20 were
English live action.

The job's own settings decide three things here:

- which lanes it asks for (live action, anime, donghua, Western animation),
- which languages it serves first (the job's language, otherwise English),
- whether kids' titles belong in it.

Nothing is banned outright. A title outside the job's ask is scored down
(`job_fit`) and placed after everything the job did ask for, so it only fills
slots the primary tier could not (see `pipeline.select_final`).

Saved jobs opt in with `job_intent: True` (jobs.engine). Content to Watch, AI
Search and the offline harness do not, so their measured behaviour is unchanged.
"""

from typing import Any, Dict, List, Optional

from .filter_engine import ANIMATION_GENRES, candidate_genres, canonical_genres
from .media_identity import content_lane, normalize_media_type

#: Served first when a job names no language of its own.
PRIMARY_LANGUAGES = ("en",)
#: Live action in these languages is pushed furthest down when the job did not ask for it.
DISTANT_LANGUAGES = frozenset({"ja", "zh", "cn", "ko", "zh-cn", "zh-tw", "zh-hk", "yue"})
KIDS_GENRES = frozenset({"kids"})
FAMILY_GENRES = frozenset({"family"})

#: What the job asked for, in the language it asked for.
PRIMARY = 0
#: A lane the job asked for, in another language. Fills what PRIMARY cannot.
SECONDARY = 1
#: A lane the job never asked for (anime in a TV job), or kids' TV in a job
#: that did not ask for kids.
OFF_INTENT = 2

#: At most this share of a job's slots may go to OFF_INTENT titles, and only
#: once everything the job did ask for is used up.
OFF_INTENT_SHARE = 0.1

#: A job that asks for live action *and* anime / donghua / animation ("Upcoming
#: Tv Shows": Sci-Fi, Fantasy, Action, Adventure, Animation, Anime) still gives
#: live action the majority: at least this share of the slots it can fill, with
#: the animated lanes sharing the rest. Balancing all lanes equally gave that
#: job 8 animated titles of 12.
LIVE_ACTION_SHARE = 0.6
#: job_fit of an animated lane the job asked for, next to live action. Still
#: PRIMARY, just behind live action when everything else is equal.
MIXED_ANIMATED_FIT = 0.6

LANE_LABELS = {
    "live_action": "live action",
    "anime": "anime",
    "donghua": "donghua",
    "animation": "animation",
}


def _as_list(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return [str(item).strip() for item in values if item is not None and str(item).strip()]


def animation_lane_languages(include_genres: Optional[List[str]], media_types: Optional[List[str]]) -> List[str]:
    """Original languages the anime/donghua discover lanes should query.

    Anime is Japanese, donghua is Chinese (TMDb: zh Mandarin, cn Cantonese).
    Asking for "anime" alone no longer fetches donghua, asking for "donghua"
    finally fetches something, and "animation" or an anime media type with no
    narrower genre asks for both. Empty means no anime/donghua lane.
    """
    include = canonical_genres(list(include_genres or []))
    media = {str(item).casefold() for item in (media_types or [])}
    if include:
        anime = bool(include & {"anime", "animation"})
        donghua = bool(include & {"donghua", "animation"})
        if not (anime or donghua) and "anime" in media:
            # Anime is a selected media type but the genres are e.g. fantasy/action:
            # anime and donghua in those genres are still wanted.
            anime = donghua = True
    else:
        anime = donghua = "anime" in media
    languages: List[str] = []
    if anime:
        languages.append("ja")
    if donghua:
        languages.extend(["zh", "cn"])
    return languages


def job_intent(job: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The lanes, languages and audience a job asks for; None when it has not opted in."""
    if not job or not job.get("job_intent"):
        return None
    filters = job.get("filters") or {}
    include = canonical_genres(filters.get("include_genres") or [])
    media = {normalize_media_type(item) for item in _as_list(job.get("media_types"))}
    animated = animation_lane_languages(filters.get("include_genres"), job.get("media_types"))
    lanes = set()
    if "ja" in animated:
        lanes.add("anime")
    if "zh" in animated:
        lanes.add("donghua")
    if "animation" in include:
        lanes.add("animation")
    # A job that names only animation genres filters live action out anyway;
    # any other genre, or none at all, means live action is what it is for.
    if media & {"movie", "tv"} and (not include or include - ANIMATION_GENRES):
        lanes.add("live_action")
    languages = [item.casefold() for item in _as_list(filters.get("languages") or filters.get("language"))]
    return {
        "lanes": sorted(lanes),
        "kids": bool(include & (KIDS_GENRES | FAMILY_GENRES)),
        "languages": languages or list(PRIMARY_LANGUAGES),
        "explicit_languages": bool(languages),
        "include_genres": sorted(include),
    }


def wants_lane(intent: Dict[str, Any], lane: str) -> bool:
    return lane in (intent.get("lanes") or [])


def animated_lanes(intent: Dict[str, Any]) -> List[str]:
    return [lane for lane in intent.get("lanes") or [] if lane != "live_action"]


def is_mixed(intent: Dict[str, Any]) -> bool:
    """Live action plus at least one animated lane."""
    return wants_lane(intent, "live_action") and bool(animated_lanes(intent))


def _language(row: Dict[str, Any]) -> str:
    return str(row.get("original_language") or "").strip().casefold()


def intent_tier(row: Dict[str, Any], intent: Dict[str, Any]) -> int:
    """PRIMARY, SECONDARY or OFF_INTENT for this job."""
    lane = content_lane(row)
    if not wants_lane(intent, lane):
        return OFF_INTENT
    if not intent.get("kids") and candidate_genres(row) & KIDS_GENRES:
        return OFF_INTENT
    if lane == "live_action":
        language = _language(row)
        if language and language not in intent.get("languages", PRIMARY_LANGUAGES):
            return SECONDARY
    # Anime and donghua are defined by origin, not by the job's language:
    # an anime job that says "en" still wants Japanese anime (filter_engine
    # exempts them from the language filter for the same reason).
    return PRIMARY


def job_fit(row: Dict[str, Any], intent: Dict[str, Any]) -> float:
    """-1..1: how squarely the title is what this job asked for."""
    tier = intent_tier(row, intent)
    if tier == PRIMARY:
        lane = content_lane(row)
        if lane != "live_action":
            fit = MIXED_ANIMATED_FIT if is_mixed(intent) else 1.0
        else:
            # Unknown language is given the benefit of the doubt, not full credit.
            fit = 1.0 if _language(row) else 0.5
    elif tier == SECONDARY:
        fit = -1.0 if _language(row) in DISTANT_LANGUAGES else -0.6
    else:
        fit = -1.0
    if not intent.get("kids") and candidate_genres(row) & FAMILY_GENRES:
        fit -= 0.4
    return round(max(-1.0, min(1.0, fit)), 4)


def job_genre_fit(row: Dict[str, Any], intent: Dict[str, Any]) -> float:
    """0..1: how many of the job's genres the title carries. One is enough to pass
    the filter (OR, never AND); two or more is a closer match."""
    wanted = set(intent.get("include_genres") or [])
    if not wanted:
        return 0.0
    return round(min(1.0, len(candidate_genres(row) & wanted) / 2.0), 4)


def describe_fit(row: Dict[str, Any], intent: Dict[str, Any]) -> str:
    """What the job asked for that this title is, or is not, in plain words."""
    lane = content_lane(row)
    label = LANE_LABELS.get(lane, lane)
    if lane == "live_action" and _language(row):
        return "%s-language %s" % (_language(row), label)
    return label


def seed_fits(seed: Dict[str, Any], intent: Dict[str, Any], media_types: Optional[List[str]] = None) -> bool:
    """Should a "more like this" query be seeded from this title for this job?

    TMDb's similar/recommendations for an anime favourite are anime. Seeding a
    TV job's similarity lanes from the profile's top titles regardless of lane
    is how an anime-heavy profile filled every job with anime.
    """
    if intent_tier(seed, intent) != PRIMARY:
        return False
    if candidate_genres(seed) & FAMILY_GENRES and not intent.get("kids"):
        return False
    media = {normalize_media_type(item) for item in _as_list(media_types)}
    if not media:
        return True
    kind = normalize_media_type(seed.get("type") or seed.get("media_type"))
    if content_lane(seed) in {"anime", "donghua"}:
        # The Anime media type takes anime series and films alike.
        return bool(media & {"anime", kind})
    return kind in media
