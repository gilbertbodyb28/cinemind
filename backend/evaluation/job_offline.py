"""Offline benchmarks for a saved job's own pool, from a frozen snapshot.

`evaluation.offline` answers "does the ranker find held-out favourites in the
Content to Watch pool?". Two questions it cannot answer are the ones a saved
job lives or dies by:

lane_holdout
    Hold out the user's 8-10/10 titles in the job's own lane (English live
    action for a TV job), put them into the job's real candidate pool - the one
    the discover and similarity lanes actually returned - and ask whether they
    come out on top. The pool is the one the job ranks every half hour, so a
    ranker that lets generic popularity pages win shows up here.

request_decisions
    Gilbert's approvals and rejections in the Requests queue are his own
    verdicts on CineMind's picks. Folds of them are held out of the profile,
    scored side by side, and the benchmark reports how often an approved title
    is ranked above a rejected one (AUC; 0.5 is chance).

Both run the production pipeline on frozen inputs only; nothing touches the
database.

    cd backend
    PYTHONPATH=../.runtime/python:. python3 -m evaluation.job_offline \\
      --snapshot /tmp/snap.json --benchmark lane_holdout --folds 8 [--control empty_taste]
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from evaluation.offline import (
    _candidate,
    _same,
    build_case_taste,
    explicit_positive_rows,
    score_ranking,
    watched_but_unlabelled,
)


def _tv_job(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    job = dict(snapshot.get("tv_job") or {})
    if not job:
        raise SystemExit("snapshot has no tv_job; freeze one with the job's candidates first")
    return {**job, "job_intent": True}


def _lane_positives(snapshot: Dict[str, Any], job: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Explicit 8-10/10 titles that sit in the job's PRIMARY tier."""
    from recommendation.job_intent import PRIMARY, intent_tier, job_intent

    intent = job_intent(job)
    identities = snapshot.get("media_identities") or []
    rows = []
    for row in explicit_positive_rows(snapshot):
        metadata = next((item for item in identities if _same(row, item)), {})
        dressed = {**metadata, **row}
        kind = str(dressed.get("media_type") or dressed.get("type") or "")
        if kind not in {"tv", "show", "movie"}:
            continue
        if intent and intent_tier(dressed, intent) != PRIMARY:
            continue
        rows.append(row)
    return rows


def _case_inputs(snapshot: Dict[str, Any], heldout: Sequence[Dict[str, Any]], control: Optional[str], hard: Sequence[Dict[str, Any]]):
    def eligible(row: Dict[str, Any]) -> bool:
        return not any(_same(row, positive) for positive in heldout)

    history = [row for row in snapshot.get("history") or [] if eligible(row)]
    personal = [row for row in snapshot.get("media_history") or [] if eligible(row)]
    feedback = [row for row in snapshot.get("feedback") or [] if eligible(row)]
    requests = [row for row in snapshot.get("requests") or [] if row.get("status") in {"approved", "rejected"} and eligible(row)]
    if control == "empty_taste":
        return [], [], [], []
    if control == "shuffled_taste":
        from evaluation.offline import _tokens

        keep = {token for row in hard for token in _tokens(row)}
        return ([row for row in history if _tokens(row) & keep], [row for row in personal if _tokens(row) & keep], [], [])
    return history, personal, feedback, requests


def lane_holdout(snapshot: Dict[str, Any], folds: int = 8, seed: int = 0, control: Optional[str] = None,
                 ks: Sequence[int] = (5, 10, 20)) -> Dict[str, Any]:
    from recommendation.pipeline import run_pipeline

    job = _tv_job(snapshot)
    positives = _lane_positives(snapshot, job)
    shuffled = list(positives)
    random.Random(seed).shuffle(shuffled)
    count = min(max(2, folds), len(shuffled))
    identities = snapshot.get("media_identities") or []
    hard = watched_but_unlabelled(snapshot, explicit_positive_rows(snapshot), seed=seed)
    requested = [row for row in snapshot.get("requests") or [] if row.get("status") == "pending_approval"]
    reports = []
    for index in range(count):
        heldout = shuffled[index::count]
        history, personal, feedback, decisions = _case_inputs(snapshot, heldout, control, hard)
        pool = [dict(row) for row in snapshot.get("tv_job_candidates") or []]
        for positive in heldout:
            if not any(_same(positive, row) for row in pool):
                metadata = next((row for row in identities if _same(positive, row)), {})
                item = _candidate({**metadata, **positive})
                item["source"] = "heldout_challenge"
                pool.append(item)
        spec = {**job, "taste_sources": [] if control == "empty_taste" else job.get("taste_sources"),
                "final_recommendation_limit": max(ks), "taste_floor": None}
        taste = build_case_taste(spec, history, personal, feedback, decisions)
        # The job's real exclusions: what is already pending in the queue is out,
        # exactly as in production. Held-out titles are never pending.
        result = run_pipeline(spec, history=history, personal_history=personal, feedback=feedback,
                              requested=[row for row in requested if not any(_same(row, p) for p in heldout)],
                              catalog=[], extra_candidates=pool, taste=taste)
        ranked = result.get("ranked") or []
        metrics = score_ranking(ranked, heldout, history, pool, ks=ks)
        reports.append({"fold": index + 1, "heldout": len(heldout), "ranked": len(ranked), "metrics": metrics,
                        "top": [row.get("title") for row in ranked[:10]]})
    keys = [key for key, value in reports[0]["metrics"].items() if isinstance(value, (int, float))]
    summary = {key: sum(row["metrics"][key] for row in reports) / len(reports) for key in keys}
    return {"benchmark": "lane_holdout", "control": control, "positives": len(positives), "folds": count,
            "summary": summary, "cases": reports}


def _auc(scored: Sequence[Dict[str, Any]], good: Sequence[Dict[str, Any]], bad: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    position = {id(row): index for index, row in enumerate(scored)}

    def where(target: Dict[str, Any]) -> Optional[int]:
        for row in scored:
            if _same(row, target):
                return position[id(row)]
        return None

    good_at = [value for value in (where(row) for row in good) if value is not None]
    bad_at = [value for value in (where(row) for row in bad) if value is not None]
    pairs = len(good_at) * len(bad_at)
    wins = sum(1 for g in good_at for b in bad_at if g < b)
    return {"auc": wins / pairs if pairs else 0.0, "pairs": pairs, "approved_ranked": len(good_at), "rejected_ranked": len(bad_at)}


def request_decisions(snapshot: Dict[str, Any], folds: int = 5, seed: int = 0, control: Optional[str] = None,
                      live_action_only: bool = False) -> Dict[str, Any]:
    from recommendation.media_identity import content_lane
    from recommendation.pipeline import run_pipeline

    decided = [row for row in snapshot.get("requests") or [] if row.get("status") in {"approved", "rejected"} and row.get("title")]
    if live_action_only:
        decided = [row for row in decided if content_lane(row) == "live_action"]
    shuffled = list(decided)
    random.Random(seed).shuffle(shuffled)
    count = min(max(2, folds), len(shuffled))
    history_all = snapshot.get("history") or []
    reports = []
    for index in range(count):
        heldout = shuffled[index::count]
        good = [row for row in heldout if row["status"] == "approved"]
        bad = [row for row in heldout if row["status"] == "rejected"]
        if not good or not bad:
            continue
        rest = [row for row in decided if not any(_same(row, other) for other in heldout)]
        history = history_all
        personal = snapshot.get("media_history") or []
        feedback = snapshot.get("feedback") or []
        if control == "empty_taste":
            history, personal, feedback, rest = [], [], [], []
        spec = {"job_type": "trakt", "candidate_sources": ["snapshot_fixed_pool"], "media_types": ["movie", "tv", "anime"],
                "taste_sources": [] if control == "empty_taste" else None, "final_recommendation_limit": len(heldout),
                "diversity": False, "taste_floor": None,
                "exclusions": {"already_watched": False, "already_in_library": False, "already_requested": False,
                               "already_recommended": False, "blacklisted": False}}
        taste = build_case_taste(spec, history, personal, feedback, rest)
        pool = [dict(_candidate({**row, "source": "heldout_challenge"})) for row in heldout]
        result = run_pipeline(spec, history=history, personal_history=personal, feedback=feedback,
                              catalog=[], extra_candidates=pool, taste=taste)
        reports.append({"fold": index + 1, "approved": len(good), "rejected": len(bad),
                        **_auc(result.get("ranked") or [], good, bad)})
    pairs = sum(row["pairs"] for row in reports) or 1
    return {"benchmark": "request_decisions", "control": control, "live_action_only": live_action_only,
            "decisions": len(decided), "folds": len(reports),
            "auc": sum(row["auc"] * row["pairs"] for row in reports) / pairs, "cases": reports}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--benchmark", choices=("lane_holdout", "request_decisions"), required=True)
    parser.add_argument("--folds", type=int, default=8)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--control", choices=("empty_taste", "shuffled_taste"))
    parser.add_argument("--live-action-only", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    if args.benchmark == "lane_holdout":
        report = lane_holdout(snapshot, folds=args.folds, seed=args.seed, control=args.control)
    else:
        report = request_decisions(snapshot, folds=args.folds, seed=args.seed, control=args.control,
                                   live_action_only=args.live_action_only)
    text = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    summary = report.get("summary") or {key: report[key] for key in ("auc", "decisions", "folds") if key in report}
    print(json.dumps({"benchmark": report["benchmark"], "control": args.control, **{k: round(v, 4) if isinstance(v, float) else v for k, v in summary.items()}}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
