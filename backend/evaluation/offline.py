"""Offline holdout evaluation from an explicit JSON snapshot, never a live DB.

Raw history ratings may be catalogue scores. Labels come only from normalized
Trakt/AniList personal scores or explicit recommendation likes.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import re
from typing import Any, Dict, List, Optional, Sequence

DEFAULT_KS = (5, 10, 20)
BEHAVIOR_FIELDS = {
    "rating", "rating_scale", "favorite", "completed", "status", "watched_at",
    "last_watched_at", "rated_at", "watch_count", "rewatched", "match_score",
    "candidate_score", "rank_score", "deterministic_score", "score_components",
    "ai_rank", "ai_score", "why", "deep_why",
}


def _kind(row: Dict[str, Any]) -> str:
    kind = str(row.get("media_type") or row.get("type") or "").lower()
    fmt = str(row.get("format") or row.get("media_format") or "").upper()
    if kind in {"anime_movie", "anime-film"} or (kind == "anime" and fmt in {"MOVIE", "FILM"}):
        return "anime_movie"
    if kind in {"show", "series", "tv"}:
        return "tv"
    return kind if kind in {"movie", "anime"} else "unknown"


def _tokens(row: Dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    canonical = row.get("canonical_media_id") or row.get("canonical_id")
    if canonical:
        tokens.add(f"canonical:{canonical}")
    if row.get("tmdb_id") is not None:
        tokens.add(f"tmdb:{_kind(row)}:{row['tmdb_id']}")
    if row.get("anilist_id") is not None:
        tokens.add(f"anilist:{row['anilist_id']}")
    if row.get("imdb_id"):
        tokens.add(f"imdb:{row['imdb_id']}")
    title = re.sub(r"\W+", " ", str(row.get("title") or "").casefold()).strip()
    if title and row.get("year") and _kind(row) != "unknown":
        tokens.add(f"title:{_kind(row)}:{row['year']}:{title}")
    return tokens


def _same(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    return bool(_tokens(a) & _tokens(b))


def _stamp(row: Dict[str, Any]) -> Optional[datetime]:
    for field in ("rated_at", "watched_at", "last_watched_at", "updated_at"):
        if row.get(field):
            try:
                value = datetime.fromisoformat(str(row[field]).replace("Z", "+00:00"))
                return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
            except ValueError:
                pass
    return None


def _feedback(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    return snapshot.get("feedback", snapshot.get("recommendation_feedback", [])) or []


def _source_candidates(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = snapshot.get("candidates", snapshot.get("recommendations"))
    if not isinstance(rows, list):
        raise ValueError("snapshot needs a candidates or recommendations array")
    return rows


def explicit_positive_rows(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Deduplicate canonical titles; watched/completed and legacy favorites are not labels."""
    rows: List[Dict[str, Any]] = []
    for row in snapshot.get("media_history") or []:
        if str(row.get("provider") or row.get("source") or "").lower() not in {"trakt", "anilist"}:
            continue
        try:
            rating, scale = float(row["rating"]), float(row.get("rating_scale") or 10)
        except (KeyError, TypeError, ValueError):
            continue
        if 0 < scale and 0 <= rating <= scale and rating * 10 / scale >= 8 and row.get("title") and _tokens(row):
            rows.append(row)
    rows.extend(row for row in _feedback(snapshot) if row.get("action") == "like" and row.get("title") and _tokens(row))
    negatives = [row for row in _feedback(snapshot) if row.get("action") in {"dislike", "blacklist"}]
    unique: List[Dict[str, Any]] = []
    for row in rows:
        if not any(_same(row, other) for other in unique) and not any(_same(row, negative) for negative in negatives):
            unique.append(dict(row))
    return sorted(unique, key=lambda row: (_kind(row), str(row.get("title") or ""), str(row.get("year") or "")))


def _candidate(row: Dict[str, Any]) -> Dict[str, Any]:
    item = {key: value for key, value in row.items() if key not in BEHAVIOR_FIELDS and key not in {"_id", "user_id"}}
    item["type"] = item.get("type") or ("show" if item.get("media_type") == "tv" else item.get("media_type"))
    item["synopsis"] = item.get("synopsis") or item.get("overview") or ""
    item["source"] = item.get("source") or "snapshot"
    return item


def _make_case(snapshot: Dict[str, Any], name: str, heldout: List[Dict[str, Any]], cutoff: Optional[datetime] = None) -> Dict[str, Any]:
    def eligible(row: Dict[str, Any]) -> bool:
        if any(_same(row, positive) for positive in heldout):
            return False
        if cutoff is None:
            return True
        stamp = _stamp(row)
        return stamp is not None and stamp < cutoff

    originals = _source_candidates(snapshot)
    pool = [_candidate(row) for row in originals]
    identities = snapshot.get("media_identities") or []
    for positive in heldout:
        if not any(_same(positive, candidate) for candidate in pool):
            metadata = next((row for row in identities if _same(positive, row)), {})
            item = _candidate({**metadata, **positive})
            item["source"] = "heldout_challenge"
            pool.append(item)
    return {
        "name": name,
        "cutoff": cutoff.isoformat() if cutoff else None,
        "heldout": heldout,
        "train_history": [row for row in snapshot.get("history") or [] if eligible(row)],
        "train_media_history": [row for row in snapshot.get("media_history") or [] if eligible(row)],
        "train_feedback": [row for row in _feedback(snapshot) if eligible(row)],
        "candidate_pool": pool,
        "source_candidate_coverage": sum(any(_same(row, source) for source in originals) for row in heldout) / len(heldout),
    }


def build_cases(snapshot: Dict[str, Any], *, folds: int = 3, seed: int = 0, chronological: bool = True) -> List[Dict[str, Any]]:
    _source_candidates(snapshot)
    positives = explicit_positive_rows(snapshot)
    if len(positives) < 2:
        raise ValueError("at least two distinct explicit positive titles required")
    shuffled = list(positives)
    random.Random(seed).shuffle(shuffled)
    count = min(max(2, folds), len(shuffled))
    cases = [_make_case(snapshot, f"holdout_{index + 1}", shuffled[index::count]) for index in range(count)]
    if chronological:
        dated = sorted(((stamp, row) for row in positives if (stamp := _stamp(row))), key=lambda pair: pair[0])
        if len(dated) >= 2:
            boundary = min(len(dated) - 1, max(1, math.ceil(len(dated) * 0.8)))
            cutoff = dated[boundary][0]
            earlier = [row for stamp, row in dated if stamp < cutoff]
            future = [row for stamp, row in dated if stamp >= cutoff]
            if earlier and future:
                cases.append(_make_case(snapshot, "chronological", future, cutoff))
    return cases


def _at_k(ranked: Sequence[Dict[str, Any]], positives: Sequence[Dict[str, Any]], ks: Sequence[int]) -> Dict[str, float]:
    output: Dict[str, float] = {}
    for k in ks:
        hits: List[Dict[str, Any]] = []
        gain = 0.0
        for position, row in enumerate(ranked[:k], start=1):
            if any(_same(row, positive) for positive in positives) and not any(_same(row, prior) for prior in hits):
                hits.append(row)
                gain += 1 / math.log2(position + 1)
        ideal = sum(1 / math.log2(position + 1) for position in range(1, min(k, len(positives)) + 1))
        output[f"precision_at_{k}"] = len(hits) / k
        output[f"recall_at_{k}"] = len(hits) / len(positives) if positives else 0.0
        output[f"hit_rate_at_{k}"] = float(bool(hits))
        output[f"ndcg_at_{k}"] = gain / ideal if ideal else 0.0
    return output


def score_ranking(ranked: Sequence[Dict[str, Any]], positives: Sequence[Dict[str, Any]], train_history: Sequence[Dict[str, Any]], candidate_pool: Sequence[Dict[str, Any]], *, ks: Sequence[int] = DEFAULT_KS) -> Dict[str, Any]:
    if not ks or any(k < 1 for k in ks):
        raise ValueError("ks must contain positive cutoffs")
    shown = list(ranked[:max(ks)])
    metrics: Dict[str, Any] = _at_k(shown, positives, ks)
    duplicates = invalid = watched = 0
    prior: List[Dict[str, Any]] = []
    for row in shown:
        duplicates += any(_same(row, seen) for seen in prior)
        invalid += not row.get("title") or not any(_same(row, allowed) for allowed in candidate_pool)
        watched += any(_same(row, seen) for seen in train_history)
        prior.append(row)
    denominator = len(shown) or 1
    metrics.update({
        "ranked_count": len(shown),
        "positive_count": len(positives),
        "candidate_coverage": sum(any(_same(row, candidate) for candidate in candidate_pool) for row in positives) / len(positives) if positives else 0.0,
        "watched_leakage_rate": watched / denominator,
        "duplicate_rate": duplicates / denominator,
        "invalid_candidate_rate": invalid / denominator,
    })
    metrics["by_media_type"] = {
        kind: {"positive_count": len(type_positives), **_at_k([row for row in shown if _kind(row) == kind], type_positives, ks)}
        for kind in ("movie", "tv", "anime", "anime_movie")
        if (type_positives := [row for row in positives if _kind(row) == kind])
    }
    return metrics


def watched_but_unlabelled(snapshot: Dict[str, Any], positives: Sequence[Dict[str, Any]], limit: int = 60, seed: int = 0) -> List[Dict[str, Any]]:
    """Titles the user watched but never rated highly.

    These are the hard case: same user, same catalogue, same era, same genres -
    the only thing separating them from a positive is that the user did not
    actually rate them well. A ranker that merely learned "anime from the 2010s"
    scores the same on both; one that learned taste does not.
    """
    rows: List[Dict[str, Any]] = []
    for row in snapshot.get("media_history") or []:
        if any(_same(row, positive) for positive in positives):
            continue
        rating = row.get("rating")
        try:
            normalized = float(rating) * 10 / float(row.get("rating_scale") or 10) if rating is not None else None
        except (TypeError, ValueError):
            normalized = None
        if normalized is not None and normalized >= 8:
            continue
        if not row.get("title") or not _tokens(row):
            continue
        if any(_same(row, other) for other in rows):
            continue
        rows.append(row)
    random.Random(seed + 7).shuffle(rows)
    return rows[:limit]


def discrimination_ranking(run_pipeline, spec, history, personal, feedback, snapshot, positives, hard):
    """Score positives and hard negatives side by side with the watched filter off.

    Hard negatives are by definition already watched, so the production pipeline
    drops them before they can be compared. This pass exists only to measure
    whether the ranker can tell them apart from a held-out positive.
    """
    identities = snapshot.get("media_identities") or []

    def dress(row: Dict[str, Any]) -> Dict[str, Any]:
        metadata = next((item for item in identities if _same(row, item)), {})
        item = _candidate({**metadata, **row})
        item["source"] = "heldout_challenge"
        return item

    pool = [dress(row) for row in list(positives) + list(hard)]
    result = run_pipeline(
        {**spec, "exclusions": {
            "already_watched": False, "already_in_library": False,
            "already_requested": False, "already_recommended": False, "blacklisted": False,
        }, "final_recommendation_limit": len(pool) or 1, "diversity": False},
        history=history, feedback=feedback, personal_history=personal,
        catalog=[], extra_candidates=pool,
    )
    return result.get("ranked") or []


def discrimination_score(ranked: Sequence[Dict[str, Any]], positives: Sequence[Dict[str, Any]], hard: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    """Pairwise accuracy: is a held-out positive ranked above a watched non-positive?

    Distribution shift cannot fake this - both sides come from the same user's
    own history and differ only in whether they were actually rated well.
    """
    order = {id(row): index for index, row in enumerate(ranked)}
    def position(target: Dict[str, Any]) -> Optional[int]:
        for row in ranked:
            if _same(row, target):
                return order[id(row)]
        return None

    positive_positions = [value for value in (position(row) for row in positives) if value is not None]
    hard_positions = [value for value in (position(row) for row in hard) if value is not None]
    if not positive_positions or not hard_positions:
        return {"pairwise_accuracy": 0.0, "compared_pairs": 0}
    wins = sum(1 for p in positive_positions for h in hard_positions if p < h)
    total = len(positive_positions) * len(hard_positions)
    return {
        "pairwise_accuracy": wins / total,
        "compared_pairs": total,
        "positives_ranked": len(positive_positions),
        "hard_negatives_ranked": len(hard_positions),
    }


def evaluate_snapshot(snapshot: Dict[str, Any], *, folds: int = 3, seed: int = 0, chronological: bool = True, ks: Sequence[int] = DEFAULT_KS, control: Optional[str] = None, hard_negatives: int = 60) -> Dict[str, Any]:
    """Call the production deterministic pipeline with only frozen in-memory inputs.

    `control` deliberately breaks the personalization so a real gain can be told
    apart from an artefact of how the candidate pool is shaped:
      empty_taste     - no profile at all
      shuffled_taste  - a profile built from watched titles the user did NOT rate well
    """
    cases = build_cases(snapshot, folds=folds, seed=seed, chronological=chronological)
    from recommendation.pipeline import run_pipeline  # lazy import; no DB operation

    all_positives = explicit_positive_rows(snapshot)
    reports: List[Dict[str, Any]] = []
    for case in cases:
        hard = watched_but_unlabelled(snapshot, all_positives, limit=hard_negatives, seed=seed)
        pool = list(case["candidate_pool"])
        spec = {"job_type": "trakt", "candidate_sources": ["snapshot_fixed_pool"],
                "media_types": ["movie", "tv", "anime"], "final_recommendation_limit": max(ks)}
        history, personal, train_feedback = case["train_history"], case["train_media_history"], case["train_feedback"]
        if control == "empty_taste":
            spec["taste_sources"] = []
            history, personal, train_feedback = [], [], []
        elif control == "shuffled_taste":
            keep = {token for row in hard for token in _tokens(row)}
            history = [row for row in history if _tokens(row) & keep]
            personal = [row for row in personal if _tokens(row) & keep]
            train_feedback = []
        result = run_pipeline(
            spec,
            history=history, feedback=train_feedback,
            personal_history=personal,
            catalog=[], extra_candidates=[dict(row) for row in pool],
        )
        ranked = result.get("ranked") or []
        reports.append({
            "name": case["name"], "cutoff": case["cutoff"],
            "train_history_count": len(case["train_history"]), "heldout_count": len(case["heldout"]),
            "candidate_pool_count": len(case["candidate_pool"]),
            "source_candidate_coverage": case["source_candidate_coverage"],
            "metrics": {
                **score_ranking(ranked, case["heldout"], case["train_history"], pool, ks=ks),
                **discrimination_score(
                    discrimination_ranking(run_pipeline, spec, history, personal, train_feedback, snapshot, case["heldout"], hard),
                    case["heldout"], hard,
                ),
            },
            "ranked": [{key: row.get(key) for key in ("title", "year", "type", "canonical_media_id", "source", "rank_score", "score_components")} for row in ranked[:max(ks)]],
        })
    summary: Dict[str, Dict[str, float]] = {}
    for group, selected in (("holdout", [row for row in reports if row["name"].startswith("holdout_")]), ("chronological", [row for row in reports if row["name"] == "chronological"])):
        if selected:
            numeric = [key for key, value in selected[0]["metrics"].items() if isinstance(value, (int, float))]
            summary[group] = {key: sum(row["metrics"][key] for row in selected) / len(selected) for key in numeric}
            summary[group]["source_candidate_coverage"] = sum(row["source_candidate_coverage"] for row in selected) / len(selected)
    return {
        "control": control,
        "positive_count": len(explicit_positive_rows(snapshot)),
        "original_candidate_count": len(_source_candidates(snapshot)),
        "label_rule": "normalized Trakt/AniList personal rating >=8/10 or explicit like; watched/community/favorite excluded",
        "candidate_pool_rule": "fixed snapshot candidates plus held-out positives; original source coverage separate",
        "summary": summary, "cases": reports,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate recommendations from an explicit JSON snapshot")
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--folds", type=int, default=3)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--no-chronological", action="store_true")
    parser.add_argument("--control", choices=("empty_taste", "shuffled_taste"))
    parser.add_argument("--hard-negatives", type=int, default=60)
    args = parser.parse_args(argv)
    with args.snapshot.open("r", encoding="utf-8") as stream:
        snapshot = json.load(stream)
    report = evaluate_snapshot(
        snapshot, folds=args.folds, seed=args.seed, chronological=not args.no_chronological,
        control=args.control, hard_negatives=args.hard_negatives,
    )
    encoded = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    else:
        print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
