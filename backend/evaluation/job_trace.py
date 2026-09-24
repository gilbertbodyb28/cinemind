"""Trace one job through the real pipeline and show where candidates are lost.

Read-only against the job: it runs the same candidate gathering as a preview
(the discover cursor does not move), writes no run, no recommendation and no
request. Filters can be overridden per invocation so several genre mixes can be
checked against the same saved job without creating new ones.

    cd backend
    PYTHONPATH=../.runtime/python:. python3 -m evaluation.job_trace \
      --user <user_id> --job <job_id> --include anime,animation [--llm]
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional

from recommendation.media_identity import content_lane


def _lanes(rows: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    return dict(Counter(content_lane(row) for row in rows).most_common())


def _kinds(rows: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    return dict(Counter(f"{content_lane(row)}/{row.get('media_type') or row.get('type')}" for row in rows).most_common())


def _genre_hits(rows: List[Dict[str, Any]], include: List[str]) -> Dict[str, int]:
    from recommendation.filter_engine import genre_matches

    return {name: sum(1 for row in rows if genre_matches(row, {name.casefold()})) for name in include}


def _sample(rows: List[Dict[str, Any]], count: int) -> List[str]:
    out = []
    for row in rows[:count]:
        genres = "/".join(row.get("genres") or [])
        out.append(
            f"{row.get('title')} ({row.get('year')}) [{content_lane(row)} {row.get('media_type')}"
            f" lang={row.get('original_language')}] {genres}"
        )
    return out


def _stage(name: str, rows: List[Dict[str, Any]], include: List[str], sample: int) -> Dict[str, Any]:
    stage = {"stage": name, "count": len(rows), "lanes": _lanes(rows), "lane_media": _kinds(rows)}
    if include:
        stage["genre_hits"] = _genre_hits(rows, include)
    if sample:
        stage["sample"] = _sample(rows, sample)
    return stage


async def trace_job(
    user_id: str,
    job_id: str,
    *,
    include: Optional[List[str]] = None,
    media_types: Optional[List[str]] = None,
    min_year: Any = "keep",
    max_year: Any = "keep",
    llm: bool = False,
    sample: int = 12,
) -> Dict[str, Any]:
    from database import db
    from jobs.engine import gather_job_candidates, rerank_verified_candidates
    from recommendation.pipeline import run_pipeline, select_final
    from recommendation.ranking_engine import apply_rerank

    job = await db.jobs.find_one({"user_id": user_id, "id": job_id}, {"_id": 0})
    if not job:
        raise SystemExit(f"job {job_id} not found for {user_id}")
    job = json.loads(json.dumps(job))
    filters = dict(job.get("filters") or {})
    if include is not None:
        filters["include_genres"] = include
    if min_year != "keep":
        filters["min_year"] = min_year
    if max_year != "keep":
        filters["max_year"] = max_year
    job["filters"] = filters
    if media_types:
        job["media_types"] = media_types
    wanted = list(filters.get("include_genres") or [])

    warnings: List[Dict[str, Any]] = []
    inputs, taste, extra = await gather_job_candidates(user_id, job, "preview", warnings)
    raw_by_source = dict(Counter(row.get("source") or "?" for row in extra).most_common())
    result = run_pipeline(job, extra_candidates=list(extra), taste=taste, **inputs)

    merged = [row for row in result["ranked"]] + [row for row in result["rejected"]]
    filter_codes = {
        "rejected_media_type", "rejected_genre", "rejected_year", "rejected_release_date",
        "rejected_rating", "rejected_vote_count", "rejected_runtime", "rejected_language",
        "rejected_country", "rejected_provider", "rejected_format", "rejected_season",
        "rejected_status", "rejected_studio", "rejected_tag", "rejected_episodes",
    }
    filter_rejected = [row for row in result["rejected"] if row.get("filter_outcome") in filter_codes]
    exclusion_rejected = [row for row in result["rejected"] if row.get("filter_outcome") not in filter_codes]
    by_reason: Dict[str, Dict[str, int]] = {}
    for row in result["rejected"]:
        bucket = by_reason.setdefault(row["filter_outcome"], {})
        lane = content_lane(row)
        bucket[lane] = bucket.get(lane, 0) + 1

    limit = int(job.get("final_recommendation_limit") or 8)
    ranked = result["ranked"]
    deterministic = select_final(ranked, result["job"])
    stages = [
        {"stage": "RAW (all sources, before dedupe)", "count": len(extra), "by_source": raw_by_source,
         "lanes": _lanes(extra)},
        _stage("BEFORE FILTERING (after dedupe)", merged, wanted, 0),
        {"stage": "FILTER REJECTED", "count": len(filter_rejected), "lanes": _lanes(filter_rejected)},
        {"stage": "EXCLUSION REJECTED (watched/library/requested/...)", "count": len(exclusion_rejected),
         "lanes": _lanes(exclusion_rejected)},
        _stage("AFTER FILTERING", ranked, wanted, 0),
        _stage("AFTER SCORING (top %d before diversity)" % limit, ranked[:limit], wanted, sample),
        _stage("FINAL deterministic (after lane balance / diversity)", deterministic, wanted, sample),
    ]
    rerank_note = "not requested"
    if llm and ranked:
        ordered, provider, model = await rerank_verified_candidates(user_id, result["taste"], ranked)
        if ordered:
            reranked = select_final(apply_rerank(ranked, ordered), result["job"])
            stages.append(_stage(f"AFTER LLM RERANKING ({provider}:{model})", reranked, wanted, sample))
            rerank_note = f"{provider}:{model}"
        else:
            rerank_note = f"{provider}:{model} returned nothing usable; deterministic order kept"
    return {
        "job": {"id": job_id, "name": job.get("name"), "media_types": job.get("media_types"),
                "include_genres": wanted, "exclude_genres": filters.get("exclude_genres"),
                "min_year": filters.get("min_year"), "max_year": filters.get("max_year"),
                "candidate_sources": job.get("candidate_sources")},
        "warnings": warnings,
        "dedupe_dropped": len(extra) - len(merged),
        "rejected_by_reason_and_lane": by_reason,
        "stages": stages,
        "llm": rerank_note,
    }


def _csv(value: Optional[str]) -> Optional[List[str]]:
    if value is None:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _year(value: Optional[str]) -> Any:
    if value is None:
        return "keep"
    return None if value.lower() in {"none", ""} else int(value)


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--include", help="comma list overriding include_genres ('' for none)")
    parser.add_argument("--media", help="comma list overriding media_types")
    parser.add_argument("--min-year")
    parser.add_argument("--max-year")
    parser.add_argument("--llm", action="store_true", help="also run the Ollama re-rank")
    parser.add_argument("--sample", type=int, default=12)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    report = asyncio.run(trace_job(
        args.user, args.job,
        include=_csv(args.include), media_types=_csv(args.media),
        min_year=_year(args.min_year), max_year=_year(args.max_year),
        llm=args.llm, sample=args.sample,
    ))
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text)
    print(text)


if __name__ == "__main__":
    main()
