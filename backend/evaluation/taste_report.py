"""Debug-only answers to "why was this recommended?" - never part of the UI.

Four read-only reports over one user's real data:

    cd backend
    PYTHONPATH=../.runtime/python:. python3 -m evaluation.taste_report --user <id> sources [--live]
    PYTHONPATH=../.runtime/python:. python3 -m evaluation.taste_report --user <id> profile [--taste-sources plex,trakt]
    PYTHONPATH=../.runtime/python:. python3 -m evaluation.taste_report --user <id> explain --job <job_id> [--top 20] [--llm]
    PYTHONPATH=../.runtime/python:. python3 -m evaluation.taste_report --user <id> compare --job <job_id>

`sources` separates "connected" (a token is stored), "synced" (rows exist, and
when) and "used" (rows that reached the taste profile). A connection that is
stored but rejected by the provider looks connected in the app; `--live` asks
each provider, read-only, whether the token still works and how much history it
really holds. `explain` runs a job exactly like a preview (no cursor move, no
run, no recommendation, no request is written) and prints the score breakdown
of every pick with the history it matched. `--spec '<job json>'` replaces
`--job` for an unsaved job.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

PROVIDERS = ("plex", "trakt", "simkl", "anilist")

#: The breakdown Gilbert asked for, mapped onto the ranking components.
BREAKDOWN = (
    ("TASTE / GENRE MATCH", ("taste_similarity",)),
    ("HISTORY SIMILARITY", ("liked_title_similarity",)),
    ("SEED SUPPORT", ("seed_support",)),
    ("THEME / KEYWORD MATCH", ("keyword_affinity",)),
    ("CREATOR / CAST MATCH", ("people_affinity",)),
    ("FRANCHISE / STUDIO MATCH", ("franchise_affinity",)),
    ("RECENT INTEREST", ("recent_interest",)),
    ("NEGATIVE FEEDBACK PENALTY", ("negative_affinity",)),
    ("MEDIA TYPE PREFERENCE", ("media_type_fit",)),
    ("LANGUAGE MATCH", ("language_fit",)),
    ("ERA MATCH", ("era_fit",)),
    ("RATING / QUALITY", ("quality", "thin_evidence")),
    ("METADATA", ("metadata_confidence",)),
    ("SOURCE SCORE", ("source_confidence",)),
    ("POPULARITY", ("popularity",)),
    ("JOB FIT (not taste)", ("job_fit", "job_genre_fit")),
)


def _stamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "generation_time"):
        return value.generation_time.astimezone(timezone.utc).isoformat()[:19]
    return str(value)[:19]


async def sources_report(user_id: str, live: bool = False) -> Dict[str, Any]:
    """Connected / synced / available / used, per provider."""
    from database import db
    from jobs.engine import _provider_connected, load_pipeline_inputs
    from recommendation.taste_engine import build_taste_snapshot, merge_taste_docs

    from providers.live_history import anilist_live, overlay_rows, trakt_live

    conn = await db.connections.find_one({"user_id": user_id}, {"_id": 0}) or {}
    inputs = await load_pipeline_inputs(user_id)
    merged = merge_taste_docs(inputs["history"], inputs["personal_history"])
    # What a job run actually uses: the synced rows plus the read-only live
    # overlay (providers.live_history), and the Requests decisions.
    extra_history, extra_personal, overlay = overlay_rows(
        inputs["history"], inputs["personal_history"], await trakt_live(user_id, conn), await anilist_live(conn))
    taste = build_taste_snapshot(
        inputs["history"] + extra_history, feedback=inputs.get("feedback"),
        personal_history=inputs["personal_history"] + extra_personal,
        requests=inputs.get("requested"),
    )
    synced_only = build_taste_snapshot(
        inputs["history"], feedback=inputs.get("feedback"),
        personal_history=inputs["personal_history"], requests=inputs.get("requested"),
    )
    used = taste.get("provider_usage") or {}
    used_synced = synced_only.get("provider_usage") or {}
    report: Dict[str, Any] = {}
    for name in PROVIDERS:
        rows = [row for row in inputs["history"] if (row.get("source") or row.get("provider")) == name]
        personal = [row for row in inputs["personal_history"] if row.get("provider") == name]
        first = await db.history.find({"user_id": user_id, "source": name}, {"_id": 1}).sort("_id", 1).limit(1).to_list(1)
        last = await db.history.find({"user_id": user_id, "source": name}, {"_id": 1}).sort("_id", -1).limit(1).to_list(1)
        watched = sorted(str(row.get("watched_at") or row.get("last_watched_at")) for row in rows
                         if row.get("watched_at") or row.get("last_watched_at"))
        titles = {row.get("canonical_media_id") or (row.get("title"), row.get("year")) for row in rows}
        report[name] = {
            "connected": _provider_connected(conn, name),
            "synced_at": _stamp(last[0]["_id"]) if last else None,
            "first_synced_at": _stamp(first[0]["_id"]) if first else None,
            "history_rows": len(rows),
            "distinct_titles": len(titles),
            "types": dict(Counter(str(row.get("type") or row.get("media_type")) for row in rows)),
            "statuses": dict(Counter(str(row.get("status")) for row in rows if row.get("status"))),
            "watched_range": [watched[0][:10], watched[-1][:10]] if watched else None,
            "personal_rows": len(personal),
            "personal_ratings": sum(1 for row in personal if row.get("rating") is not None),
            "used_by_engine_synced_rows_only": used_synced.get(name, {}),
            "used_by_engine_with_live_overlay": used.get(name, {}),
        }
    report["_live_overlay"] = overlay
    report["_requests"] = used.get("requests", {})
    report["_merged_titles"] = len(merged)
    report["_taste_items"] = taste.get("item_count")
    report["_ignored"] = taste.get("ignored_rows") or {}
    if live:
        report["_live"] = await live_probe(conn)
    return report


async def live_probe(conn: Dict[str, Any]) -> Dict[str, Any]:
    """Read-only: does each stored token still work, and how much does the provider hold?"""
    import httpx
    from config import SIMKL_API, SIMKL_CLIENT_ID, TRAKT_API
    from providers.simkl import simkl_headers, simkl_params
    from providers.trakt import resolve_trakt_client_id, trakt_headers

    out: Dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        if conn.get("trakt_access_token"):
            headers = trakt_headers(resolve_trakt_client_id(conn), conn["trakt_access_token"])
            try:
                row: Dict[str, Any] = {}
                response = await client.get(f"{TRAKT_API}/sync/history", params={"limit": 1}, headers=headers)
                row["token_ok"] = response.status_code == 200
                row["plays"] = int(response.headers.get("X-Pagination-Item-Count") or 0)
                for kind in ("movies", "shows", "seasons", "episodes"):
                    response = await client.get(f"{TRAKT_API}/sync/ratings/{kind}", params={"limit": 1}, headers=headers)
                    row[f"ratings_{kind}"] = int(response.headers.get("X-Pagination-Item-Count") or 0)
                out["trakt"] = row
            except Exception as exc:  # a probe reports, it never raises
                out["trakt"] = {"error": exc.__class__.__name__}
        if conn.get("simkl_access_token"):
            client_id = conn.get("simkl_client_id") or SIMKL_CLIENT_ID
            try:
                response = await client.get(
                    f"{SIMKL_API}/sync/all-items", params=simkl_params(client_id),
                    headers=simkl_headers(client_id, conn["simkl_access_token"]),
                )
                row = {"token_ok": response.status_code == 200, "status": response.status_code}
                if response.status_code == 200:
                    body = response.json() or {}
                    row.update({key: len(body.get(key) or []) for key in ("movies", "shows", "anime")})
                else:
                    row["error"] = (response.json() or {}).get("error") if "json" in response.headers.get("content-type", "") else None
                out["simkl"] = row
            except Exception as exc:
                out["simkl"] = {"error": exc.__class__.__name__}
        if conn.get("anilist_access_token"):
            query = ("query ($u: String) { User(name: $u) { mediaListOptions { scoreFormat } } "
                     "MediaListCollection(userName: $u, type: ANIME) { lists { status entries { score } } } }")
            try:
                response = await client.post(
                    "https://graphql.anilist.co",
                    json={"query": query, "variables": {"u": conn.get("anilist_username")}},
                    headers={"Authorization": "Bearer " + conn["anilist_access_token"]},
                )
                data = (response.json() or {}).get("data") or {}
                lists = ((data.get("MediaListCollection") or {}).get("lists")) or []
                statuses: Counter = Counter()
                scored = 0
                for bucket in lists:
                    for entry in bucket.get("entries") or []:
                        statuses[bucket.get("status")] += 1
                        scored += bool(entry.get("score"))
                out["anilist"] = {
                    "token_ok": response.status_code == 200,
                    "score_format": ((data.get("User") or {}).get("mediaListOptions") or {}).get("scoreFormat"),
                    "entries": dict(statuses), "scored": scored,
                }
            except Exception as exc:
                out["anilist"] = {"error": exc.__class__.__name__}
        if conn.get("plex_url") and conn.get("plex_token"):
            try:
                response = await client.get(
                    conn["plex_url"].rstrip("/") + "/status/sessions/history/all",
                    headers={"X-Plex-Token": conn["plex_token"], "Accept": "application/json",
                             "X-Plex-Container-Start": "0", "X-Plex-Container-Size": "0"},
                )
                row = {"token_ok": response.status_code == 200, "status": response.status_code}
                if response.status_code == 200:
                    container = (response.json() or {}).get("MediaContainer") or {}
                    row["plays"] = container.get("totalSize") or container.get("size")
                out["plex"] = row
            except Exception as exc:
                out["plex"] = {"error": exc.__class__.__name__}
    return out


def _top(store: Dict[str, Dict[str, float]], limit: int, sign: int = 1, prefix: str = "") -> List[str]:
    rows = [(name, row) for name, row in (store or {}).items()
            if row.get("affinity", 0) * sign > 0 and name.startswith(prefix)]
    rows.sort(key=lambda pair: -pair[1]["affinity"] * sign)
    return ["%s %+.2f (n=%d)" % (name[len(prefix):], row["affinity"], row.get("evidence", 0)) for name, row in rows[:limit]]


def profile_summary(taste: Dict[str, Any], limit: int = 12) -> Dict[str, Any]:
    """The profile as Gilbert asked to see it, from the real snapshot."""
    positives = taste.get("liked_titles") or taste.get("high_confidence_positive_titles") or []

    def mark(row: Dict[str, Any]) -> str:
        if row.get("rating") is not None:
            return "%.0f/10" % row["rating"]
        if row.get("decision") == "approved":
            return "approved in Requests"
        return "%s plays" % row.get("plays")

    def examples(kind: Iterable[str]) -> List[str]:
        wanted = set(kind)
        return ["%s (%s) %s" % (row["title"], row.get("year"), mark(row))
                for row in positives if row.get("media_type") in wanted][:limit]

    return {
        "TOP GENRES": _top(taste.get("genres"), limit),
        "TOP SUBGENRES (genre pairs)": _top(taste.get("genre_pairs"), limit),
        "TOP THEMES (TMDb keywords)": _top(taste.get("tmdb_keywords"), limit),
        "TOP FRANCHISES": _top(taste.get("collections"), limit),
        "TOP STUDIOS / NETWORKS": _top(taste.get("companies"), limit) + _top(taste.get("studios"), 4),
        "TOP CREATORS": _top(taste.get("people"), limit, prefix="creator:"),
        "TOP ACTORS": _top(taste.get("people"), limit, prefix="cast:"),
        "PREFERRED LANGUAGES": _top(taste.get("languages"), 6),
        "FORMATS": _top(taste.get("media_types"), 4),
        "ANIME PREFERENCES": examples(["anime", "anime_movie"]),
        "TV PREFERENCES": examples(["tv"]),
        "MOVIE PREFERENCES": examples(["movie"]),
        "NEGATIVE PREFERENCES": {
            "genres": _top(taste.get("genres"), 8, sign=-1),
            "themes": _top(taste.get("tmdb_keywords"), 8, sign=-1),
            "titles": ["%s (%s) %s" % (row.get("title"), row.get("year"), row.get("reason") or "")
                       for row in (taste.get("negative_titles") or [])[:limit]],
        },
        "COUNTS": {key: taste.get(key) for key in (
            "item_count", "rated_count", "positive_count", "negative_count",
            "approved_count", "rejected_count", "language_evidence")},
    }


def _shared(candidate: Dict[str, Any], liked: Dict[str, Any], field: str) -> List[str]:
    left = {str(value).casefold(): str(value) for value in candidate.get(field) or []}
    right = {str(value).casefold() for value in liked.get(field) or []}
    return [left[key] for key in left if key in right]


def matched_because(row: Dict[str, Any], taste: Dict[str, Any], limit: int = 3) -> List[str]:
    """Concrete overlaps with the history titles this pick resembles most."""
    from recommendation.similarity import explain_match

    lines: List[str] = []
    references = {str(item.get("title")): item for item in (taste.get("liked_titles") or taste.get("high_confidence_positive_titles") or [])}
    for match in (row.get("similar_to") or [])[:limit]:
        liked = references.get(str(match.get("title")))
        if not liked:
            continue
        detail = explain_match(row, liked)
        mark = ("%.0f/10" % liked["rating"]) if liked.get("rating") is not None else "%s plays" % liked.get("plays")
        lines.append("like %s (%s, %s) %.2f: %s" % (
            liked.get("title"), liked.get("year"), mark, float(match.get("similarity") or 0.0),
            "; ".join(detail) or "genres only"))
    link = references.get(str(row.get("specific_link")))
    if link and row.get("specific_link") not in [match.get("title") for match in (row.get("similar_to") or [])[:limit]]:
        mark = ("%.0f/10" % link["rating"]) if link.get("rating") is not None else "%s plays" % link.get("plays")
        lines.insert(0, "concrete link: %s (%s, %s): %s" % (
            link.get("title"), link.get("year"), mark, "; ".join(explain_match(row, link)) or "-"))
    for seed in (row.get("seed_titles") or [])[:limit]:
        lines.append("recommended from %s" % seed)
    lines.append("specific evidence %.2f, personal score %.2f" % (float(row.get("specific_score") or 0.0), float(row.get("personal_score") or 0.0)))
    return lines


def breakdown(row: Dict[str, Any]) -> Dict[str, float]:
    contributions = row.get("score_contributions") or {}
    out: Dict[str, float] = {}
    named = set()
    for label, names in BREAKDOWN:
        present = [name for name in names if name in contributions]
        named.update(names)
        if present:
            out[label] = round(sum(float(contributions[name]) for name in present), 3)
    for name, value in contributions.items():
        if name not in named:
            out[name] = round(float(value), 3)
    return out


def explain_row(row: Dict[str, Any], taste: Dict[str, Any]) -> Dict[str, Any]:
    from recommendation.job_intent import LANE_LABELS
    from recommendation.media_identity import content_lane
    from recommendation.taste_engine import media_bucket

    return {
        "title": "%s (%s)" % (row.get("title"), row.get("year")),
        "media_type": media_bucket(row), "lane": LANE_LABELS.get(content_lane(row), content_lane(row)),
        "language": row.get("original_language"), "source": row.get("source"),
        "personal_score": row.get("personal_score"), "final_score": round(float(row.get("rank_score") or 0.0), 3),
        "match_score": row.get("match_score"), "ai_rank": row.get("ai_rank"),
        "breakdown": breakdown(row),
        "matched_because": matched_because(row, taste),
        "why": row.get("why"),
        "penalties": row.get("why_penalties"),
    }


def compare_titles(history: Sequence[Dict[str, Any]], picks: Sequence[Dict[str, Any]], taste: Dict[str, Any]) -> Dict[str, Any]:
    """Side by side: what the user loves versus what the engine picked."""
    from recommendation.similarity import best_similarity
    from recommendation.taste_engine import candidate_affinity, media_bucket

    references = list(taste.get("liked_titles") or taste.get("high_confidence_positive_titles") or [])

    def card(row: Dict[str, Any], with_similarity: bool) -> Dict[str, Any]:
        out = {
            "title": "%s (%s)" % (row.get("title"), row.get("year")),
            "media_type": media_bucket(row), "language": row.get("original_language"),
            "genres": (row.get("genres") or [])[:5],
            "themes": (row.get("tmdb_keywords") or [])[:6],
            "creators": (row.get("creators") or [])[:3], "cast": (row.get("cast") or [])[:4],
            "studio": (row.get("companies") or row.get("studios") or [])[:3],
            "franchise": row.get("collection"),
            "taste_score": candidate_affinity(row, taste),
        }
        if with_similarity:
            others = [item for item in references if item.get("title") != row.get("title")]
            hit = best_similarity(row, others)
            out["history_similarity"] = hit["score"]
            out["closest_history_title"] = hit.get("title")
        return out

    return {"history": [card(row, True) for row in history], "picks": [card(row, True) for row in picks]}


async def explain_job(user_id: str, job_id: Optional[str], spec: Optional[Dict[str, Any]] = None,
                      top: int = 20, llm: bool = False) -> Dict[str, Any]:
    """One job, run like a preview, every pick explained - and every source traced."""
    from database import db
    from jobs.engine import (
        apply_model_order, gather_job_candidates, rerank_keep, rerank_verified_candidates, with_job_intent,
    )
    from recommendation.pipeline import default_job, run_pipeline, select_final

    if spec is not None:
        job = {**default_job(), "id": "trace_spec", "name": "trace spec", **spec}
    else:
        job = await db.jobs.find_one({"user_id": user_id, "id": job_id}, {"_id": 0})
        if not job:
            raise SystemExit("job %s not found for %s" % (job_id, user_id))
    job = with_job_intent(json.loads(json.dumps(job, default=str)))
    warnings: List[Dict[str, Any]] = []
    inputs, taste, extra = await gather_job_candidates(user_id, job, "preview", warnings)
    result = run_pipeline(job, extra_candidates=list(extra), taste=taste, **inputs)
    ranked = result["ranked"]
    final = result["accepted"]
    by_source: Dict[str, Dict[str, int]] = {}
    final_keys = {id(row) for row in final}
    top20 = {id(row) for row in final[:20]}
    top50 = {id(row) for row in final[:50]}
    for row in extra:
        by_source.setdefault(row.get("source") or "?", {"raw": 0})["raw"] += 1
    for row in ranked:
        stats = by_source.setdefault(row.get("source") or "?", {"raw": 0})
        stats["after_filtering"] = stats.get("after_filtering", 0) + 1
        if float(row.get("personal_score") or 0.0) >= float(result.get("taste_floor") or 0.0):
            stats["passes_taste_floor"] = stats.get("passes_taste_floor", 0) + 1
        stats["final"] = stats.get("final", 0) + (id(row) in final_keys)
        stats["top50"] = stats.get("top50", 0) + (id(row) in top50)
        stats["top20"] = stats.get("top20", 0) + (id(row) in top20)
    rejected = Counter(row.get("filter_outcome") for row in result["rejected"])
    report: Dict[str, Any] = {
        "job": {key: job.get(key) for key in ("id", "name", "media_types", "taste_sources", "candidate_sources", "filters")},
        "warnings": warnings,
        "sources": by_source,
        "rejected": dict(rejected.most_common()),
        "taste_floor": result.get("taste_floor"),
        "held_back_below_taste_floor": result.get("below_taste_floor"),
        "provider_usage": taste.get("provider_usage"),
        "deterministic": [explain_row(row, taste) for row in final[:top]],
    }
    if llm and ranked:
        ordered, provider, model = await rerank_verified_candidates(
            user_id, taste, ranked, job=result["job"], keep=rerank_keep(result["job"]))
        if ordered:
            # Exactly as execute_job applies it: bounded for a saved job, free for Content to Watch.
            reranked = select_final(apply_model_order(ranked, ordered, result["job"]), result["job"])
            report["llm"] = {"model": "%s:%s" % (provider, model),
                             "picks": [explain_row(row, taste) for row in reranked[:top]]}
        else:
            report["llm"] = {"model": "%s:%s" % (provider, model), "picks": None}
    return report


def _print(report: Any) -> None:
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user", required=True)
    parser.add_argument("report", choices=("sources", "profile", "explain", "compare"))
    parser.add_argument("--live", action="store_true", help="sources: also ask each provider, read-only")
    parser.add_argument("--taste-sources", help="profile: comma list, as a job's taste_sources")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--job")
    group.add_argument("--spec", help="an unsaved job as JSON, or @path")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--llm", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    async def run() -> Any:
        if args.report == "sources":
            return await sources_report(args.user, live=args.live)
        spec = None
        if args.spec:
            text = open(args.spec[1:], encoding="utf-8").read() if args.spec.startswith("@") else args.spec
            spec = json.loads(text)
        if args.report == "profile":
            from jobs.engine import load_pipeline_inputs
            from recommendation.taste_engine import build_taste_snapshot

            from database import db
            from providers.live_history import anilist_live, overlay_rows, trakt_live

            inputs = await load_pipeline_inputs(args.user)
            conn = await db.connections.find_one({"user_id": args.user}, {"_id": 0}) or {}
            # The profile a job run builds: synced rows + the read-only live overlay.
            extra_history, extra_personal, _ = overlay_rows(
                inputs["history"], inputs["personal_history"],
                await trakt_live(args.user, conn), await anilist_live(conn))
            from providers.keys import resolve_tmdb_api_key
            from providers.tmdb_enrich import enrich_rows

            decided = [row for row in inputs.get("requested") or [] if row.get("status") in {"approved", "rejected"}]
            key = resolve_tmdb_api_key(conn)
            if key:
                # Exactly what gather_job_candidates enriches before a run.
                for rows in (inputs["history"], inputs["personal_history"], extra_history, extra_personal, decided):
                    await enrich_rows(rows, key)
            sources = args.taste_sources.split(",") if args.taste_sources else None
            taste = build_taste_snapshot(
                inputs["history"] + extra_history, feedback=inputs.get("feedback"), taste_sources=sources,
                personal_history=inputs["personal_history"] + extra_personal, requests=decided,
            )
            return profile_summary(taste)
        if not (args.job or spec):
            raise SystemExit("--job or --spec is required for %s" % args.report)
        report = await explain_job(args.user, args.job, spec=spec, top=args.top, llm=args.llm)
        if args.report == "compare":
            from database import db
            from jobs.engine import gather_job_candidates, with_job_intent
            from recommendation.pipeline import default_job, run_pipeline

            job = spec and {**default_job(), "id": "trace_spec", **spec} or await db.jobs.find_one(
                {"user_id": args.user, "id": args.job}, {"_id": 0})
            job = with_job_intent(json.loads(json.dumps(job, default=str)))
            inputs, taste, extra = await gather_job_candidates(args.user, job, "preview", [])
            result = run_pipeline(job, extra_candidates=list(extra), taste=taste, **inputs)
            from recommendation.job_intent import job_intent, intent_tier, PRIMARY

            intent = job_intent(job)
            loved = [row for row in (taste.get("liked_titles") or []) if not intent or intent_tier(row, intent) == PRIMARY]
            return compare_titles(loved[: args.top // 2 or 10], result["accepted"][: args.top // 2 or 10], taste)
        return report

    report = asyncio.run(run())
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False, default=str)
    _print(report)


if __name__ == "__main__":
    main()
