"""Benchmark Ollama models on CineMind's real re-ranking task.

Every model sees the same taste profile, the same candidate list and the same
instructions; only the model (or an explicitly named prompt/option variant)
changes. Ground truth is the held-out positives the offline harness builds, so
a model is measured on titles it was never told the user liked.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx

from .offline import _same, build_cases

RERANK_SYSTEM = (
    "You only reorder verified candidate IDs. Never invent titles or IDs. "
    'Return JSON {"ids": ["id1", "id2"]}.'
)


def candidate_line(row: Dict[str, Any]) -> str:
    return (
        f"{row['bench_id']}: {row.get('title')} ({row.get('year')}) "
        f"genres={','.join(row.get('genres') or [])}"
    )


def build_bench_pool(
    case: Dict[str, Any],
    ranked: Sequence[Dict[str, Any]],
    size: int,
    seed: int,
    max_positives: int = 4,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """A realistic re-rank pool: labelled positives plus deterministic distractors.

    Held-out positives that are not sampled as labels are removed outright -
    left in, they would count as wrong answers and punish a model for ranking
    something the user actually loved.

    Rows keep `bench_rank`, their position in the deterministic order, so a
    model that returns a partial list is scored the way production treats it:
    whatever it omits keeps the deterministic ranking behind the answer.
    """
    positives = [dict(row) for row in case["heldout"]]
    random.Random(seed + 991).shuffle(positives)
    # Use the SCORED row the pipeline produced for each label, not the raw
    # history row: without rank_score a label sorts to the bottom and the
    # deterministic baseline looks like it found nothing.
    labels: List[Dict[str, Any]] = []
    for positive in positives[:max(1, max_positives)]:
        scored = next((dict(row) for row in ranked if _same(row, positive)), None)
        labels.append(scored or dict(positive))

    distractors: List[Dict[str, Any]] = []
    for row in ranked:
        if any(_same(row, positive) for positive in positives):
            continue
        if any(_same(row, picked) for picked in distractors):
            continue
        distractors.append(dict(row))
        if len(distractors) >= max(0, size - len(labels)):
            break

    pool = labels + distractors
    # Deterministic order over exactly this pool, so bench_rank is honest.
    pool.sort(key=lambda row: -(row.get("rank_score") or 0.0))
    for index, row in enumerate(pool):
        row["bench_rank"] = index
    presented = list(pool)
    random.Random(seed).shuffle(presented)
    for index, row in enumerate(presented, start=1):
        row["bench_id"] = "c%03d" % index
    return presented, labels


def call_model(
    base_url: str,
    model: str,
    system: str,
    prompt: str,
    options: Dict[str, Any],
    timeout: float = 300.0,
) -> Tuple[Optional[str], float]:
    payload = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": False,
        # Ranking a fixed list is not a reasoning puzzle: thinking mode costs
        # minutes per call and makes the JSON less reliable, not more.
        "think": False,
        "options": options,
    }
    started = time.monotonic()
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(f"{base_url.rstrip('/')}/api/generate", json=payload)
        elapsed = time.monotonic() - started
        if response.status_code != 200:
            return None, elapsed
        body = response.json()
        text = (body.get("response") or "").strip() or str(body.get("thinking") or "")
        return text, elapsed
    except Exception:
        return None, time.monotonic() - started


def parse_ids(raw: Optional[str]) -> Optional[List[str]]:
    from llm import extract_json

    parsed = extract_json(raw or "")
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    if isinstance(parsed, dict):
        for key in ("ids", "candidate_ids", "ranking", "order"):
            value = parsed.get(key)
            if isinstance(value, list):
                return [str(item) for item in value]
    return None


def rank_metrics(order: Sequence[str], pool: Sequence[Dict[str, Any]], positives: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    by_id = {row["bench_id"]: row for row in pool}
    ranked = [by_id[key] for key in order if key in by_id]
    seen: set = set()
    deduped = []
    for row in ranked:
        if row["bench_id"] in seen:
            continue
        seen.add(row["bench_id"])
        deduped.append(row)
    # Exactly what production does with a partial answer: everything the model
    # left out keeps the deterministic ranking, behind what it did return.
    leftover = [row for row in pool if row["bench_id"] not in seen]
    leftover.sort(key=lambda row: row.get("bench_rank", 0))
    deduped += leftover

    output: Dict[str, float] = {}
    hit_positions: List[int] = []
    for position, row in enumerate(deduped, start=1):
        if any(_same(row, positive) for positive in positives):
            hit_positions.append(position)
    for k in (5, 10):
        hits = [p for p in hit_positions if p <= k]
        gain = sum(1 / math.log2(p + 1) for p in hits)
        ideal = sum(1 / math.log2(p + 1) for p in range(1, min(k, len(positives)) + 1))
        output[f"precision_at_{k}"] = len(hits) / k
        output[f"recall_at_{k}"] = len(hits) / len(positives) if positives else 0.0
        output[f"hit_rate_at_{k}"] = float(bool(hits))
        output[f"ndcg_at_{k}"] = gain / ideal if ideal else 0.0
    output["mrr"] = 1 / min(hit_positions) if hit_positions else 0.0
    return output


def evaluate_model(
    snapshot: Dict[str, Any],
    *,
    base_url: str,
    model: str,
    pool_size: int = 24,
    folds: int = 3,
    seed: int = 0,
    options: Optional[Dict[str, Any]] = None,
    system: str = RERANK_SYSTEM,
    taste_prompt: Optional[Any] = None,
    repeats: int = 2,
    max_positives: int = 5,
) -> Dict[str, Any]:
    from recommendation.llm_context import compact_taste_prompt
    from recommendation.pipeline import run_pipeline

    # Mirror production: providers/ollama.py sends exactly these.
    options = {
        "temperature": 0, "top_p": 1, "top_k": 1, "seed": 11,
        "repeat_penalty": 1.0, "num_ctx": 8192, "num_predict": 1024,
        **(options or {}),
    }
    taste_prompt = taste_prompt or compact_taste_prompt
    cases = build_cases(snapshot, folds=folds, seed=seed)
    rows: List[Dict[str, Any]] = []
    for case in cases:
        result = run_pipeline(
            {
                "job_type": "discover",
                "candidate_sources": ["snapshot_fixed_pool"],
                "media_types": ["movie", "tv", "anime"],
                "final_recommendation_limit": pool_size,
            },
            history=case["train_history"],
            feedback=case["train_feedback"],
            personal_history=case["train_media_history"],
            catalog=[],
            extra_candidates=[dict(row) for row in case["candidate_pool"]],
        )
        pool, positives = build_bench_pool(
            case, result.get("ranked") or [], pool_size, seed, max_positives=max_positives
        )
        prompt = (
            taste_prompt(result["taste"])
            + "\nVerified candidates:\n"
            + "\n".join(candidate_line(row) for row in pool)
            + "\nReturn only those IDs, best first."
        )
        allowed = {row["bench_id"] for row in pool}
        answers: List[Optional[List[str]]] = []
        for attempt in range(max(1, repeats)):
            if model == "__deterministic__":
                # The arm to beat: no model at all, just the pipeline's own order.
                raw, elapsed = json.dumps({"ids": []}), 0.0
            elif model == "__shuffled__":
                # Floor: what a model that understands nothing would score.
                order = [row["bench_id"] for row in pool]
                # Deliberately unrelated to the seed that shuffled the
                # presentation order, or this "random" arm reproduces it.
                random.Random(seed * 131 + attempt + 7919).shuffle(order)
                raw, elapsed = json.dumps({"ids": order}), 0.0
            else:
                raw, elapsed = call_model(base_url, model, system, prompt, options)
            ids = parse_ids(raw)
            answers.append(ids)
            valid = [key for key in (ids or []) if key in allowed]
            rows.append({
                "case": case["name"],
                "latency_s": round(elapsed, 2),
                "parsed": ids is not None,
                "returned": len(ids or []),
                "valid_returned": len(valid),
                "hallucinated": len([key for key in (ids or []) if key not in allowed]),
                "coverage": len(set(valid)) / len(allowed) if allowed else 0.0,
                "prompt_chars": len(prompt),
                "positives_in_pool": len(positives),
                **rank_metrics(valid, pool, positives),
            })
        if len(answers) > 1:
            first, second = answers[0] or [], answers[1] or []
            width = min(10, len(first), len(second))
            rows[-1]["stability_at_10"] = (
                sum(1 for a, b in zip(first[:width], second[:width]) if a == b) / width if width else 0.0
            )
    numeric = sorted({
        key for row in rows for key, value in row.items() if isinstance(value, (int, float))
    })
    summary = {
        key: round(statistics.fmean([row[key] for row in rows if key in row]), 4)
        for key in numeric
        if any(key in row for row in rows)
    }
    summary["json_parse_rate"] = round(statistics.fmean([float(row["parsed"]) for row in rows]), 4) if rows else 0.0
    return {"model": model, "options": options, "pool_size": pool_size, "summary": summary, "runs": rows}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark Ollama models on CineMind reranking")
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--models", required=True, nargs="+")
    parser.add_argument("--base-url", default="http://192.168.50.94:11434")
    parser.add_argument("--pool-size", type=int, default=24)
    parser.add_argument("--folds", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--max-positives", type=int, default=5)
    parser.add_argument("--num-ctx", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    options = {"num_ctx": args.num_ctx} if args.num_ctx else {}
    reports = []
    for model in args.models:
        report = evaluate_model(
            snapshot, base_url=args.base_url, model=model, pool_size=args.pool_size,
            folds=args.folds, options=options, repeats=args.repeats,
            max_positives=args.max_positives,
        )
        reports.append(report)
        print(f"{model}: {json.dumps(report['summary'], sort_keys=True)}", flush=True)
    encoded = json.dumps(reports, indent=2, ensure_ascii=False, default=str)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
