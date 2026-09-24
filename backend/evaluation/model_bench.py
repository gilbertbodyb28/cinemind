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

from .offline import _kind, _same, build_cases

RERANK_SYSTEM = (
    "You only reorder verified candidate IDs. Never invent titles or IDs. "
    'Return JSON {"ids": ["id1", "id2"]}.'
)
PRODUCTION_RERANK_SYSTEM = (
    "You re-rank a verified candidate list for one viewer. Use only the given "
    "handles, never invent one, never drop one, never repeat one. Put the titles "
    "this viewer is most likely to genuinely enjoy first. Popularity is not the "
    'goal; fit to the stated taste is. Reply with JSON only: {"ids": ["<handle>", ...]}.'
)


def production_rerank_request(pool: Sequence[Dict[str, Any]], taste_text: str,
                              *, redact_recent_titles: bool = False) -> Tuple[str, Dict[str, str], Dict[str, Any], float]:
    """Mirror the 12-candidate prompt and schema in jobs.engine without DB calls."""
    from jobs.engine import RERANK_CANDIDATE_CAP, RERANK_MIN_COVERAGE, rerank_schema

    ranked = sorted(pool, key=lambda row: row.get("bench_rank", 0))[:RERANK_CANDIDATE_CAP]
    handles = {"r%02d" % index: row["bench_id"] for index, row in enumerate(ranked, start=1)}
    lines = []
    for handle, row in zip(handles, ranked):
        title = "[title withheld]" if redact_recent_titles and _year(row) >= 2024 else row.get("title")
        detail = [
            "%s (%s)" % (title, row.get("year")),
            row.get("media_type") or row.get("type") or "",
            "genres=%s" % ",".join(row.get("genres") or []) if row.get("genres") else "",
        ]
        themes = [str(name) for name in (row.get("tmdb_keywords") or [])[:5]]
        if themes:
            detail.append("themes=%s" % ",".join(themes))
        similar = [item.get("title") for item in (row.get("similar_to") or [])[:2] if item.get("title")]
        if similar:
            detail.append("resembles=%s" % "; ".join(similar))
        if row.get("original_language"):
            detail.append("lang=%s" % row["original_language"])
        lines.append("%s | %s" % (handle, " | ".join(part for part in detail if part)))
    prompt = (
        taste_text + "\n\nVerified candidates (%d):\n" % len(lines)
        + "\n".join(lines)
        + "\n\nReturn all %d handles above, ordered best first for this viewer." % len(lines)
    )
    return prompt, handles, rerank_schema(len(lines)), RERANK_MIN_COVERAGE


def _year(row: Dict[str, Any]) -> int:
    try:
        return int(str(row.get("year") or "0")[:4])
    except (TypeError, ValueError):
        return 0


def candidate_line(row: Dict[str, Any], *, redact_recent_title: bool = False) -> str:
    def values(*fields: str, limit: int = 8) -> str:
        found: List[str] = []
        for field in fields:
            raw = row.get(field) or []
            raw = raw if isinstance(raw, list) else [raw]
            for item in raw:
                if item and str(item) not in found:
                    found.append(str(item))
        return ",".join(found[:limit])

    synopsis = str(row.get("synopsis") or row.get("overview") or "").replace("\n", " ")[:420]
    title = "[recent title withheld]" if redact_recent_title and _year(row) >= 2024 else row.get("title")
    return " | ".join(part for part in (
        f"{row['bench_id']}: {title} ({row.get('year')})",
        f"type={_kind(row)}",
        f"genres={values('genres')}",
        f"keywords={values('tmdb_keywords', 'keywords', 'tags')}",
        f"cast={values('cast', limit=5)}",
        f"creators={values('creators', 'directors', limit=4)}",
        f"studio={values('studios', 'companies', limit=4)}",
        f"franchise={row.get('collection') or row.get('franchise') or ''}",
        f"source_material={values('source_material')}",
        f"language={row.get('original_language') or ''}",
        f"release={row.get('release_date') or row.get('first_air_date') or ''}",
        f"synopsis={synopsis}",
    ) if not part.endswith("=") and not part.endswith("synopsis="))


def conservative_order(order: Sequence[str], pool: Sequence[Dict[str, Any]], promotions: int = 2,
                       deterministic_window: int = 10) -> List[str]:
    """Promote only strong model choices already supported by deterministic evidence."""
    deterministic = [row["bench_id"] for row in sorted(pool, key=lambda row: row.get("bench_rank", 0))]
    near = set(deterministic[:deterministic_window])
    promoted: List[str] = []
    for key in order:
        if key in near and key not in promoted:
            promoted.append(key)
        if len(promoted) >= promotions:
            break
    return promoted + [key for key in deterministic if key not in promoted]


def completed_order(order: Sequence[str], pool: Sequence[Dict[str, Any]]) -> List[str]:
    """Complete a partial model answer with the remaining deterministic order."""
    deterministic = [row["bench_id"] for row in sorted(pool, key=lambda row: row.get("bench_rank", 0))]
    allowed = set(deterministic)
    valid = list(dict.fromkeys(key for key in order if key in allowed))
    return valid + [key for key in deterministic if key not in valid]


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
    response_schema: Optional[Dict[str, Any]] = None,
    think: Optional[bool] = False,
) -> Tuple[Optional[str], float, Dict[str, Any]]:
    payload = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": False,
        # Ranking a fixed list is not a reasoning puzzle: thinking mode costs
        # minutes per call and makes the JSON less reliable, not more.
        "options": options,
    }
    if think is not None:
        payload["think"] = think
    if response_schema:
        payload["format"] = response_schema
    started = time.monotonic()
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(f"{base_url.rstrip('/')}/api/generate", json=payload)
        elapsed = time.monotonic() - started
        if response.status_code != 200:
            return None, elapsed, {"error": "http_%s" % response.status_code}
        body = response.json()
        answer = (body.get("response") or "").strip() or str(body.get("thinking") or "")
        generated = int(body.get("eval_count") or 0)
        eval_seconds = float(body.get("eval_duration") or 0) / 1e9
        stats = {
            "prompt_tokens": int(body.get("prompt_eval_count") or 0),
            "generation_tokens": generated,
            "generation_tokens_per_second": generated / eval_seconds if eval_seconds else 0.0,
            "ollama_total_duration_s": float(body.get("total_duration") or 0) / 1e9,
            "ollama_load_duration_s": float(body.get("load_duration") or 0) / 1e9,
            "error": None,
        }
        return answer, elapsed, stats
    except httpx.TimeoutException:
        return None, time.monotonic() - started, {"error": "timeout"}
    except Exception as exc:
        return None, time.monotonic() - started, {"error": exc.__class__.__name__}


def loaded_model_memory(base_url: str, model: str) -> Dict[str, int]:
    """Read Ollama's loaded model memory figures without exposing user inputs."""
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(f"{base_url.rstrip('/')}/api/ps")
        response.raise_for_status()
        entry = next((row for row in response.json().get("models", []) if row.get("name") == model), None)
        if entry:
            total = int(entry.get("size") or 0)
            vram = int(entry.get("size_vram") or 0)
            return {"loaded_model_bytes": total, "loaded_model_vram_bytes": vram,
                    "loaded_model_ram_bytes": max(0, total - vram)}
    except Exception:
        pass
    return {}


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


def ranking_stability(orders: Sequence[Sequence[str]], limit: int = 10) -> Dict[str, float]:
    """Compare repeated answers by both membership and rank position."""
    membership: List[float] = []
    position: List[float] = []
    for left in range(len(orders)):
        for right in range(left + 1, len(orders)):
            first, second = list(orders[left][:limit]), list(orders[right][:limit])
            union = set(first) | set(second)
            membership.append(len(set(first) & set(second)) / len(union) if union else 0.0)
            position.append(sum(a == b for a, b in zip(first, second)) / max(len(first), len(second), 1))
    return {
        "stability_at_10": statistics.fmean(membership) if membership else 1.0,
        "order_stability_at_10": statistics.fmean(position) if position else 1.0,
    }


def rank_metrics(order: Sequence[str], pool: Sequence[Dict[str, Any]], positives: Sequence[Dict[str, Any]],
                 train_history: Sequence[Dict[str, Any]] = ()) -> Dict[str, float]:
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
    shown = deduped[:10]
    output["watched_leakage_rate"] = (
        sum(any(_same(row, seen) for seen in train_history) for row in shown) / len(shown) if shown else 0.0
    )
    for kind in ("movie", "tv", "anime", "anime_movie"):
        type_positives = [row for row in positives if _kind(row) == kind]
        if not type_positives:
            continue
        positions = [
            position for position, row in enumerate(deduped, start=1)
            if any(_same(row, positive) for positive in type_positives)
        ]
        for k in (5, 10):
            hits = [position for position in positions if position <= k]
            gain = sum(1 / math.log2(position + 1) for position in hits)
            ideal = sum(1 / math.log2(position + 1) for position in range(1, min(k, len(type_positives)) + 1))
            output[f"{kind}_precision_at_{k}"] = len(hits) / k
            output[f"{kind}_ndcg_at_{k}"] = gain / ideal if ideal else 0.0
        output[f"{kind}_mrr"] = 1 / min(positions) if positions else 0.0
    # Current-title slice: only held-out positives released in 2024+ with
    # cached synopsis and genre metadata count as relevant. In the matching
    # prompt arm, their titles are redacted to avoid pretrained title recall.
    fresh_positives = [
        row for row in positives
        if _year(row) >= 2024 and (row.get("synopsis") or row.get("overview")) and row.get("genres")
    ]
    if fresh_positives:
        fresh_positions = [
            position for position, row in enumerate(deduped, start=1)
            if any(_same(row, positive) for positive in fresh_positives)
        ]
        output["fresh_label_count"] = float(len(fresh_positives))
        for k in (5, 10):
            hits = [position for position in fresh_positions if position <= k]
            gain = sum(1 / math.log2(position + 1) for position in hits)
            ideal = sum(1 / math.log2(position + 1) for position in range(1, min(k, len(fresh_positives)) + 1))
            output[f"fresh_precision_at_{k}"] = len(hits) / k
            output[f"fresh_ndcg_at_{k}"] = gain / ideal if ideal else 0.0
        output["fresh_mrr"] = 1 / min(fresh_positions) if fresh_positions else 0.0
    return output


def paired_comparison(model_runs: Sequence[Dict[str, Any]], baseline_runs: Sequence[Dict[str, Any]],
                      metric: str) -> Dict[str, Any]:
    """Paired inference over folds; repeats are averaged inside each fold."""
    def fold_means(rows: Sequence[Dict[str, Any]]) -> Dict[str, float]:
        grouped: Dict[str, List[float]] = {}
        for row in rows:
            if metric in row:
                grouped.setdefault(str(row["case"]), []).append(float(row[metric]))
        return {key: statistics.fmean(values) for key, values in grouped.items()}

    model, baseline = fold_means(model_runs), fold_means(baseline_runs)
    common = sorted(set(model) & set(baseline))
    differences = [model[key] - baseline[key] for key in common]
    count = len(differences)
    mean = statistics.fmean(differences) if differences else 0.0
    standard_error = statistics.stdev(differences) / math.sqrt(count) if count > 1 else 0.0
    # Exact two-sided 95% Student t critical values for df 1..31.
    t95 = (
        12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306,
        2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131, 2.120,
        2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064,
        2.060, 2.056, 2.052, 2.048, 2.045, 2.042, 2.040,
    )
    critical = t95[count - 2] if 2 <= count <= 32 else 1.96
    low, high = mean - critical * standard_error, mean + critical * standard_error
    # Deterministic paired sign-flip test. Exact for small n; Monte Carlo otherwise.
    observed = abs(mean)
    if not differences or all(value == 0 for value in differences):
        p_value = 1.0
    else:
        trials = (1 << count) if count <= 18 else 100000
        extreme = 0
        rng = random.Random(20260923)
        for mask in range(trials):
            signed = [
                value * (1 if ((mask >> index) & 1) else -1)
                if count <= 18 else value * (1 if rng.random() >= 0.5 else -1)
                for index, value in enumerate(differences)
            ]
            extreme += abs(statistics.fmean(signed)) >= observed - 1e-12
        p_value = extreme / trials if count <= 18 else (extreme + 1) / (trials + 1)
    return {
        "n_folds": count,
        "mean_difference": round(mean, 6),
        "standard_error": round(standard_error, 6),
        "ci95": [round(low, 6), round(high, 6)],
        "p_value": round(p_value, 6),
        "significant": bool(low > 0 or high < 0) and p_value < 0.05,
    }


def holm_adjusted_pvalues(p_values: Dict[str, float]) -> Dict[str, float]:
    """Control family-wise error for a prespecified set of comparisons."""
    ordered = sorted(p_values, key=lambda key: p_values[key])
    adjusted: Dict[str, float] = {}
    running = 0.0
    for index, key in enumerate(ordered):
        running = max(running, min(1.0, p_values[key] * (len(ordered) - index)))
        adjusted[key] = round(running, 6)
    return adjusted


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
    redact_recent_titles: bool = False,
    prompt_mode: str = "production",
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
    if prompt_mode not in {"production", "research"}:
        raise ValueError("prompt_mode must be production or research")
    cases = build_cases(snapshot, folds=folds, seed=seed, chronological=False)
    rows: List[Dict[str, Any]] = []
    conservative_rows: List[Dict[str, Any]] = []
    memory: Dict[str, int] = {}
    memory_checked = False
    for case_index, case in enumerate(cases):
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
            case, result.get("ranked") or [], pool_size, seed + case_index * 1009,
            max_positives=max_positives
        )
        if prompt_mode == "production":
            prompt, handles, schema, min_coverage = production_rerank_request(
                pool, taste_prompt(result["taste"]), redact_recent_titles=redact_recent_titles
            )
            allowed = set(handles)
            model_system = PRODUCTION_RERANK_SYSTEM
        else:
            prompt = (
                taste_prompt(result["taste"])
                + "\nVerified candidates:\n"
                + "\n".join(candidate_line(row, redact_recent_title=redact_recent_titles) for row in pool)
                + "\nReturn only those IDs, best first."
            )
            handles, schema, min_coverage = {}, None, 0.0
            allowed = {row["bench_id"] for row in pool}
            model_system = system
        answers: List[List[str]] = []
        conservative_answers: List[List[str]] = []
        case_rows: List[Dict[str, Any]] = []
        for attempt in range(max(1, repeats)):
            if model == "__deterministic__":
                # The arm to beat: no model at all, just the pipeline's own order.
                raw, elapsed, telemetry = json.dumps({"ids": []}), 0.0, {}
            elif model == "__shuffled__":
                # Floor: what a model that understands nothing would score.
                order = list(handles) if handles else [row["bench_id"] for row in pool]
                # Deliberately unrelated to the seed that shuffled the
                # presentation order, or this "random" arm reproduces it.
                random.Random(seed * 131 + attempt + 7919).shuffle(order)
                raw, elapsed, telemetry = json.dumps({"ids": order}), 0.0, {}
            else:
                raw, elapsed, telemetry = call_model(
                    base_url, model, model_system, prompt, options,
                    response_schema=schema, think=None if prompt_mode == "production" else False,
                )
                if not memory_checked:
                    memory = loaded_model_memory(base_url, model)
                    memory_checked = True
            ids = parse_ids(raw)
            valid_handles = [key for key in (ids or []) if key in allowed]
            valid = [handles[key] for key in valid_handles] if handles else valid_handles
            if handles and len(set(valid_handles)) < max(1, int(len(handles) * min_coverage)):
                valid = []  # production keeps deterministic order below coverage threshold
            answers.append(completed_order(valid, pool))
            metrics = rank_metrics(valid, pool, positives, case["train_history"])
            row = {
                "case": case["name"],
                "repeat": attempt + 1,
                "latency_s": round(elapsed, 2),
                "parsed": ids is not None,
                "returned": len(ids or []),
                "valid_returned": len(valid_handles),
                "hallucinated": len([key for key in (ids or []) if key not in allowed]),
                "duplicates": len(valid_handles) - len(set(valid_handles)),
                "coverage": len(set(valid_handles)) / len(allowed) if allowed else 0.0,
                "prompt_chars": len(prompt),
                "error": telemetry.get("error"),
                "timeout": telemetry.get("error") == "timeout",
                "inference_failed": bool(telemetry.get("error")),
                **{key: telemetry[key] for key in (
                    "prompt_tokens", "generation_tokens", "generation_tokens_per_second",
                    "ollama_total_duration_s", "ollama_load_duration_s",
                ) if key in telemetry},
                "positives_in_pool": len(positives),
                "returned_ids": ids or [],
                **metrics,
            }
            rows.append(row)
            case_rows.append(row)
            conservative = conservative_order(valid, pool)
            conservative_answers.append(conservative)
            conservative_rows.append({
                **{key: value for key, value in row.items() if key not in metrics and key != "returned_ids"},
                "returned_ids": conservative,
                **rank_metrics(conservative, pool, positives, case["train_history"]),
            })
        if len(answers) > 1:
            full_stability = ranking_stability(answers)
            conservative_stability = ranking_stability(conservative_answers)
            for row in case_rows:
                row.update(full_stability)
            for row in conservative_rows[-len(case_rows):]:
                row.update(conservative_stability)
    numeric = sorted({
        key for row in rows for key, value in row.items() if isinstance(value, (int, float))
    })
    summary = {
        key: round(statistics.fmean([row[key] for row in rows if key in row]), 4)
        for key in numeric
        if any(key in row for row in rows)
    }
    summary["json_parse_rate"] = round(statistics.fmean([float(row["parsed"]) for row in rows]), 4) if rows else 0.0
    conservative_numeric = sorted({
        key for row in conservative_rows for key, value in row.items() if isinstance(value, (int, float))
    })
    conservative_summary = {
        key: round(statistics.fmean([row[key] for row in conservative_rows if key in row]), 4)
        for key in conservative_numeric if any(key in row for row in conservative_rows)
    }
    return {
        "model": model, "options": options, "pool_size": pool_size, "folds": len(cases),
        "redact_recent_titles": redact_recent_titles,
        "prompt_mode": prompt_mode,
        "loaded_model_memory": memory,
        "summary": summary, "runs": rows,
        "conservative_summary": conservative_summary, "conservative_runs": conservative_rows,
    }


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
    parser.add_argument("--redact-recent-titles", action="store_true")
    parser.add_argument("--prompt-mode", choices=("production", "research"), default="production")
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
            redact_recent_titles=args.redact_recent_titles,
            prompt_mode=args.prompt_mode,
        )
        reports.append(report)
        print(f"{model}: {json.dumps(report['summary'], sort_keys=True)}", flush=True)
    encoded = json.dumps(reports, indent=2, ensure_ascii=False, default=str)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
