"""Upcoming jobs keep looking until they have found enough (Gilbert, 2026-09-25).

Upcoming is CineMind's first priority: films, series, anime, new titles, new
seasons of old series, sequels and spin-offs - anything still to come that fits
the viewer's taste. Measured on his "Upcoming Tv Shows" that evening, before
this module: 1,166 candidates, 229 with a verified premiere, 45 past the job's
settings and exclusions, 1 over the taste floor - and the run ended there. The
other 44 were mostly anime sequels whose link to the season he had watched was
never seen, and new seasons of his own series were "already watched".

A run of a job with "Only upcoming premieres" now:

1. adds the coming continuations of titles the user likes to its own lanes
   (providers.continuations) - they are the most personal upcoming titles;
2. if that leaves fewer than UPCOMING_TARGET new picks, widens the search one
   step at a time (UPCOMING_STAGES): the profile's own genre pairs and themes
   inside the premiere window, deeper pages of the job's own lanes, and other
   sources (Trakt's anticipated lists and premiere calendar, AniList's announced
   anime further down, TMDb's coming films). After each step the pipeline runs
   again on everything found so far; the search stops when the target is met or
   every step has been tried, and the run records what each step found.

Nothing here lowers a bar: the job's filters, the exclusions, the taste floor
and the queue rules are applied to every step's titles exactly as before.

Since 2026-09-29 every saved job searches this way, not only upcoming ones:
Gilbert's rule is at least 100 results sent to Requests per run, preferably
well over 1,000 (pipeline.MIN_RESULTS). A run widens while it has fewer than
100 titles over the taste floor or fewer results than its limit, and the last
step reads the job's own lanes with the largest candidate budget a job may have
(full_budget). Only after the last step do titles below the floor fill the
list (pipeline._complete_results).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from database import db

#: New picks a run looks for before it stops widening the search. A run's list
#: keeps the open picks of earlier runs (carry_over), so each run only has to
#: find what is new; 20 fills Home's four Up Coming filters several times over.
UPCOMING_TARGET = 20
#: The widening steps, in order (see the module docstring). full_budget reads
#: the job's own lanes as a job gathering MAX_CANDIDATE_BUDGET candidates would.
SEARCH_STAGES = ("taste_window", "deeper_pages", "other_sources", "full_budget")
UPCOMING_STAGES = SEARCH_STAGES
#: How many queue rows one run checks for a premiere date (refresh_request_premieres).
REQUEST_PREMIERE_BATCH = 300
#: A queue row's premiere is checked again after this long.
REQUEST_PREMIERE_RECHECK = timedelta(hours=12)


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def upcoming_target(job: Dict[str, Any]) -> int:
    return max(1, min(int(job.get("final_recommendation_limit") or 8), UPCOMING_TARGET))


def _history_rows(inputs: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    return list(inputs.get("history") or []) + list(inputs.get("personal_history") or [])


async def add_continuations(
    job: Dict[str, Any],
    taste: Dict[str, Any],
    inputs: Dict[str, List[Dict[str, Any]]],
    extra: List[Dict[str, Any]],
    tmdb_key: Optional[str],
    report: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """The job's candidates with the coming continuations of liked titles first."""
    from providers.continuations import gather_continuations, merge_continuations

    found, counts = await gather_continuations(taste, _history_rows(inputs), tmdb_key, job.get("media_types"))
    if report is not None:
        report["continuations"] = counts
    logging.info("job %s: %s coming continuations of liked titles %s", job.get("id"), len(found), counts)
    return merge_continuations(extra, found)


async def link_verified(taste: Dict[str, Any], rows: List[Dict[str, Any]],
                        report: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """After the premiere check: tie AniList's coming sequels to the liked seasons they
    continue, and keep one row per coming season."""
    from providers.continuations import drop_duplicate_seasons, link_anilist_candidates, liked_roots

    marked = await link_anilist_candidates(rows, liked_roots(taste))
    if report is not None:
        report.setdefault("continuations", {})["anilist_linked"] = \
            report.get("continuations", {}).get("anilist_linked", 0) + marked
    return drop_duplicate_seasons(rows)


def _job_list_keys(job: Dict[str, Any], inputs: Dict[str, List[Dict[str, Any]]]) -> set:
    from recommendation.exclusion_engine import identity_keys

    return {
        key for row in inputs.get("recommended") or []
        if row.get("job_id") == job.get("id") and not row.get("dismissed")
        for key in identity_keys(row)
    }


def new_picks(result: Dict[str, Any], listed: set) -> int:
    """Picks over the taste floor that are new: not on the job's list from an
    earlier run and not already waiting in Requests."""
    from recommendation.exclusion_engine import identity_keys

    return sum(1 for row in result.get("accepted") or []
               if not row.get("open_result") and not row.get("weak_match") and not identity_keys(row) & listed)


def search_wanted(job: Dict[str, Any]) -> bool:
    """Does a run of this job widen its search when it comes up short?

    Upcoming jobs always have (UPCOMING_TARGET new picks); every saved job does
    since 2026-09-29, when a run must reach its minimum of results
    (jobs.engine.with_result_rules).
    """
    from providers.premieres import is_upcoming_job

    return is_upcoming_job(job) or bool(job.get("min_results")) or bool(job.get("open_results"))


def enough(job: Dict[str, Any], result: Dict[str, Any], listed: set) -> bool:
    """Has this run found what it looks for, so the search can stop?

    At least the minimum over the taste floor (pipeline.min_results), a full
    list (pipeline.result_target: the job's limit), and for an upcoming job at
    least UPCOMING_TARGET new picks. Titles below the floor that fill the list
    in the meantime do not count as matches.
    """
    from providers.premieres import is_upcoming_job
    from recommendation.pipeline import min_results, result_counts, result_target

    spec = {**job, **(result.get("job") or {})}
    counts = result_counts(result.get("accepted") or [], spec)
    if counts["matches"] < min_results(spec) or counts["results"] < result_target(spec):
        return False
    return not is_upcoming_job(job) or new_picks(result, listed) >= upcoming_target(job)


async def _stage_rows(stage: str, job: Dict[str, Any], taste: Dict[str, Any],
                      inputs: Dict[str, List[Dict[str, Any]]], tmdb_key: Optional[str],
                      conn: Dict[str, Any]) -> List[Dict[str, Any]]:
    from providers.premieres import is_upcoming_job, premiere_window
    from providers.tmdb import (
        LARGE_BUDGET, MAX_CANDIDATE_BUDGET, TMDB_CURSOR_PAGES, candidate_budget, discover_page_span,
        fetch_job_candidates, taste_keyword_discover, taste_seeded_discover, upcoming_movies,
    )

    filters = job.get("filters") or {}
    upcoming = is_upcoming_job(job)
    window = premiere_window(filters) if upcoming else None
    media = {str(item or "").casefold() for item in job.get("media_types") or []}
    tmdb_lanes = set(job.get("candidate_sources") or []) & {"tmdb_discover", "tmdb_similar", "tmdb_recommendations"}
    rows: List[Dict[str, Any]] = []
    if stage == "taste_window" and tmdb_key:
        # "Neighbouring genres" and "related titles" from the viewer's own
        # favourites: the genre pairs they keep returning to and the themes
        # their liked titles share - inside the premiere window for an upcoming
        # job, and deeper than the job's own lanes read them for any other.
        spec = {**job, "candidate_limit": max(40, int(job.get("candidate_limit") or 40))}
        if upcoming:
            rows.extend(await taste_seeded_discover(spec, taste, api_key=tmdb_key, per_lane=20,
                                                    window=window, pairs_limit=8))
            rows.extend(await taste_keyword_discover(spec, taste, api_key=tmdb_key, per_lane=20, window=window))
        else:
            rows.extend(await taste_seeded_discover(spec, taste, api_key=tmdb_key, pairs_limit=12, pages=5))
            rows.extend(await taste_keyword_discover(spec, taste, api_key=tmdb_key, pages=10))
    elif stage == "deeper_pages" and tmdb_key and (job.get("candidate_sources") or []):
        # The job's own lanes one page span further on. A preview does not move
        # the stored cursor, and neither does this: the next run starts where
        # the cursor says, as before.
        span = discover_page_span(job)
        if span >= TMDB_CURSOR_PAGES:
            # A large job's lanes already read every page they reach (providers.tmdb).
            return rows
        start = max(1, int(job.get("tmdb_page_cursor") or 1)) + span
        if start > TMDB_CURSOR_PAGES:
            start = ((start - 1) % TMDB_CURSOR_PAGES) + 1
        deeper = {**job, "candidate_sources": sorted(set(job.get("candidate_sources") or []) | {"tmdb_discover"})}
        rows.extend(await fetch_job_candidates(deeper, inputs.get("history") or [], api_key=tmdb_key,
                                               start_page=start, taste=taste))
    elif stage == "other_sources":
        from providers.trakt import fetch_list_titles, fetch_upcoming_titles, resolve_trakt_client_id

        large = candidate_budget(job) >= LARGE_BUDGET
        if upcoming:
            from providers.anilist import fetch_upcoming, upcoming_depth

            rows.extend(await fetch_upcoming_titles(
                resolve_trakt_client_id(conn), job.get("media_types"), start=window["from"],
                genres=filters.get("include_genres"),
                # A large job reads Trakt's anticipated lists ten pages down and a year of premieres.
                pages=10 if large else 2, days=365 if large else 99,
            ))
            if media & {"anime", "tv", "movie"}:
                # Further down AniList's announced list than the job's own lane; the
                # pages are cached, so this costs AniList's budget once per 12 hours.
                depth, pages = upcoming_depth(job)
                rows.extend(await fetch_upcoming(
                    conn.get("anilist_access_token"), min_year=filters.get("min_year"),
                    max_year=filters.get("max_year"), limit=max(300, depth), max_pages=max(8, pages),
                ))
            if tmdb_key and media & {"movie", "movies", "anime"}:
                rows.extend(await upcoming_movies(tmdb_key, pages=20 if large else 3))
        else:
            # What Trakt's viewers watch and wait for, inside the job's genres and years.
            rows.extend(await fetch_list_titles(
                resolve_trakt_client_id(conn), job.get("media_types"), genres=filters.get("include_genres"),
                min_year=filters.get("min_year"), max_year=filters.get("max_year"), pages=10 if large else 3,
            ))
        if tmdb_key and taste and not large:
            # The creators and cast of liked titles; a large job's own lanes already ask them.
            from providers.people import people_candidates

            rows.extend(await people_candidates({**job, "candidate_limit": LARGE_BUDGET}, taste, tmdb_key,
                                                window=window))
    elif stage == "full_budget" and tmdb_key and tmdb_lanes and candidate_budget(job) < MAX_CANDIDATE_BUDGET:
        # The job's own lanes as the largest job reads them: every page a lane
        # reaches, twelve genre pairs, every liked title as a "more like this"
        # seed and the viewer's own people (providers.tmdb LARGE_BUDGET).
        widest = {**job, "candidate_limit": MAX_CANDIDATE_BUDGET}
        rows.extend(await fetch_job_candidates(widest, inputs.get("history") or [], api_key=tmdb_key,
                                               start_page=max(1, int(job.get("tmdb_page_cursor") or 1)),
                                               taste=taste))
    return rows


async def broaden(
    job: Dict[str, Any],
    taste: Dict[str, Any],
    inputs: Dict[str, List[Dict[str, Any]]],
    extra: List[Dict[str, Any]],
    result: Dict[str, Any],
    run: Callable[[List[Dict[str, Any]]], Dict[str, Any]],
    tmdb_key: Optional[str],
    conn: Dict[str, Any],
    report: Optional[Dict[str, Any]] = None,
    stages: Tuple[str, ...] = SEARCH_STAGES,
    fetch: Optional[Callable[..., Awaitable[List[Dict[str, Any]]]]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Widen a job's search step by step until it has found enough (see enough()).

    `run(extra)` is the pipeline on a candidate list (execute_job and the trace
    pass the same one). Returns the final candidates and pipeline result.
    """
    from providers.premieres import is_upcoming_job, verify_premieres
    from providers.tmdb_enrich import enrich_rows
    from recommendation.exclusion_engine import identity_keys
    from recommendation.filter_engine import media_type_allowed
    from recommendation.pipeline import min_results, result_counts, result_target

    fetch = fetch or _stage_rows
    upcoming = is_upcoming_job(job)
    listed = _job_list_keys(job, inputs)

    def _counts(current: Dict[str, Any]) -> Dict[str, int]:
        return result_counts(current.get("accepted") or [], {**job, **(current.get("job") or {})})

    steps: List[Dict[str, Any]] = []
    first = new_picks(result, listed)
    counts = _counts(result)
    step = {
        "stage": "job_lanes",
        "candidates": len(extra),
        "picks": len(result.get("accepted") or []),
        "new_picks": first,
        "matches": counts["matches"],
    }
    if upcoming:
        step["verified"] = sum(1 for row in extra if row.get("premiere_date"))
        step["continuations"] = sum(1 for row in extra if row.get("continuation_of"))
    steps.append(step)
    seen = {key for row in extra for key in identity_keys(row)}
    found = first
    for stage in stages:
        if enough(job, result, listed):
            break
        try:
            rows = await fetch(stage, job, taste, inputs, tmdb_key, conn)
        except Exception as exc:  # one source failing must not end the search
            logging.warning("search stage %s failed for %s: %s", stage, job.get("id"), exc.__class__.__name__)
            steps.append({"stage": stage, "error": exc.__class__.__name__})
            continue
        fresh = []
        for row in rows:
            keys = identity_keys(row)
            if keys & seen:
                continue
            seen |= keys
            fresh.append(row)
        verified = 0
        if fresh:
            if tmdb_key:
                await enrich_rows(fresh, tmdb_key)
            if upcoming:
                wanted = [row for row in fresh if media_type_allowed(row, job.get("media_types"))]
                verified = await verify_premieres(wanted, tmdb_key)
                await link_verified(taste, fresh, report)
                # One row per coming season across the whole pool, not only this step's.
                from providers.continuations import drop_duplicate_seasons

                extra = drop_duplicate_seasons(list(extra) + fresh)
            else:
                extra = list(extra) + fresh
            result = run(extra)
        found = new_picks(result, listed)
        counts = _counts(result)
        step = {"stage": stage, "found": len(rows), "new": len(fresh),
                "picks": len(result.get("accepted") or []), "new_picks": found, "matches": counts["matches"]}
        if upcoming:
            step["verified"] = verified
        steps.append(step)
        logging.info("job %s search stage %s: %s new candidates, %s matches, %s results, %s new picks",
                     job.get("id"), stage, len(fresh), counts["matches"], counts["results"], found)
    if report is not None:
        spec = {**job, **(result.get("job") or {})}
        report["target"] = upcoming_target(job) if upcoming else result_target(spec)
        report["minimum"] = min_results(spec)
        report["stages"] = steps
        report["new_picks"] = found
        report["results"] = _counts(result)
    return extra, result


def _still_upcoming(row: Dict[str, Any], today: str) -> bool:
    premiere = str(row.get("premiere_date") or "")[:10]
    return len(premiere) == 10 and premiere > today


async def carry_over(user_id: str, job: Dict[str, Any], new_rows: List[Dict[str, Any]],
                     database: Any = None) -> List[Dict[str, Any]]:
    """The earlier picks of an upcoming job that stay on its list next to this run's.

    Each run used to replace the job's whole list with its own new picks. On
    2026-09-25 the 9 picks of 17:46 UTC went to Requests, the next run found 1
    new title, and Up Coming would have dropped to that one card. An open pick
    now stays until its premiere has passed: not dismissed, not retired, not
    picked again by this run (the new row replaces it). The job's limit still
    holds; the weakest earlier picks give way first.
    """
    from recommendation.exclusion_engine import identity_keys

    database = database or db
    today = _today()
    fresh_keys = {key for row in new_rows for key in identity_keys(row)}
    earlier = await database.recommendations.find(
        {"user_id": user_id, "job_id": job.get("id"), "dismissed": {"$ne": True},
         "saved": {"$ne": True}, "retired": {"$ne": True}},
        {"_id": 0},
    ).to_list(None)
    kept = [row for row in earlier if _still_upcoming(row, today) and not identity_keys(row) & fresh_keys]
    room = max(0, int(job.get("final_recommendation_limit") or 8) - len(new_rows))
    kept.sort(key=lambda row: (-(row.get("match_score") or 0), str(row.get("premiere_date") or "")))
    return kept[:room]


async def refresh_request_premieres(user_id: str, tmdb_key: Optional[str], database: Any = None,
                                    limit: int = REQUEST_PREMIERE_BATCH) -> int:
    """Put a verified premiere on the queue's waiting and approved rows, a batch per run.

    Up Coming shows what the user approved or is still deciding on (Gilbert,
    2026-09-25), and Requests lists coming premieres first; both read the
    premiere fields on the request row. Only those fields are written - never
    the status, never updated_at, so the queue's order and decisions stand.
    Rows never checked go first, then the oldest checks.
    """
    from providers.premieres import PREMIERE_FIELDS, verify_premieres
    from request_providers import DECIDED_STATUSES, PENDING_STATUSES

    database = database or db
    stale_before = (datetime.now(timezone.utc) - REQUEST_PREMIERE_RECHECK).isoformat()
    query = {
        "user_id": user_id,
        "status": {"$in": sorted(PENDING_STATUSES | DECIDED_STATUSES)},
        "$or": [{"tmdb_id": {"$nin": [None, ""]}}, {"anilist_id": {"$nin": [None, ""]}}],
    }
    fields = {"_id": 0, "id": 1, "tmdb_id": 1, "anilist_id": 1, "type": 1, "media_type": 1, "format": 1,
              "premiere_checked_at": 1, "title": 1, "year": 1}
    never = await database.requests.find({**query, "premiere_checked_at": {"$exists": False}}, fields).to_list(limit)
    rows = list(never)
    if len(rows) < limit:
        older = await database.requests.find(
            {**query, "premiere_checked_at": {"$lt": stale_before}}, fields,
        ).sort("premiere_checked_at", 1).to_list(limit - len(rows))
        rows.extend(older)
    # An id verify_premieres cannot ask with is left for the next run's rows.
    rows = [row for row in rows if row.get("tmdb_id") not in (None, "") or str(row.get("anilist_id") or "").isdigit()]
    if not rows:
        return 0
    unanswered: List[Dict[str, Any]] = []
    await verify_premieres(rows, tmdb_key, unanswered=unanswered)
    skipped = {id(row) for row in unanswered}
    written = 0
    for row in rows:
        if id(row) in skipped:
            # No answer is not "no premiere": ask again next run.
            continue
        await database.requests.update_one(
            {"user_id": user_id, "id": row["id"]},
            {"$set": {field: row.get(field) for field in PREMIERE_FIELDS}},
        )
        written += 1
    return written


async def search_more(
    user_id: str,
    job: Dict[str, Any],
    taste: Dict[str, Any],
    inputs: Dict[str, List[Dict[str, Any]]],
    extra: List[Dict[str, Any]],
    result: Dict[str, Any],
    run: Callable[[List[Dict[str, Any]]], Dict[str, Any]],
    report: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """broaden() with the user's own connection: what execute_job and job_trace call.

    A job that neither is upcoming nor has a minimum of results (search_wanted)
    keeps its first pass as it was.
    """
    if not search_wanted(job):
        return extra, result
    from providers.keys import resolve_tmdb_api_key

    conn = await db.connections.find_one({"user_id": user_id}, {"_id": 0}) or {}
    return await broaden(job, taste, inputs, extra, result, run, resolve_tmdb_api_key(conn), conn, report=report)


#: The name execute_job and job_trace used before every saved job searched this way.
search_upcoming = search_more


async def _backfill(user_id: str, batch: int) -> None:
    from providers.keys import resolve_tmdb_api_key

    conn = await db.connections.find_one({"user_id": user_id}, {"_id": 0}) or {}
    key = resolve_tmdb_api_key(conn)
    total = 0
    while True:
        written = await refresh_request_premieres(user_id, key, limit=batch)
        total += written
        print("premieres written: %d (total %d)" % (written, total), flush=True)
        if not written:
            break
    coming = await db.requests.count_documents({"user_id": user_id, "premiere_date": {"$gt": _today()}})
    print("queue rows with a coming premiere: %d" % coming)


def main(argv: Optional[List[str]] = None) -> None:
    """Put a verified premiere on every waiting and approved queue row now,
    instead of REQUEST_PREMIERE_BATCH rows per upcoming run. Writes only the
    premiere fields (never a status or updated_at); back the queue up first.

        cd backend
        MONGO_URL=... PYTHONPATH=../.runtime/python:. python3 -m jobs.upcoming --user <id>
    """
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--user", required=True)
    parser.add_argument("--batch", type=int, default=500)
    args = parser.parse_args(argv)
    asyncio.run(_backfill(args.user, args.batch))


if __name__ == "__main__":
    main()
