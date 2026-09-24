"""Validate benchmark-only interest labels before any model sees the dataset.

The labeling UI keeps these files outside production collections. An unrated
title is unknown, never a negative example.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

GROUPS = (
    "anime_movie", "upcoming_movie", "upcoming_tv", "upcoming_anime",
    "upcoming_anime_movie",
)
UPCOMING = GROUPS[1:]


def validate(candidates: Dict[str, Any], labels: Dict[str, Any],
             trusted_initial_labels: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    unsigned = {key: value for key, value in candidates.items() if key != "dataset_sha256"}
    canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    if digest != candidates.get("dataset_sha256") or digest != labels.get("dataset_sha256"):
        raise ValueError("dataset hash mismatch; candidates or labels changed after labeling")

    items = candidates.get("items")
    if not isinstance(items, list):
        raise ValueError("candidate items must be a list")
    by_id = {item.get("id"): item for item in items if isinstance(item, dict)}
    if len(by_id) != len(items) or None in by_id:
        raise ValueError("candidate IDs must be unique and nonempty")

    entries = labels.get("labels")
    if not isinstance(entries, dict):
        raise ValueError("labels must be a mapping from candidate ID to rating")
    if set(entries) - set(by_id):
        raise ValueError("labels contain an ID outside the frozen candidate pool")
    for key, entry in entries.items():
        score = entry.get("score") if isinstance(entry, dict) else None
        if type(score) is not int or score not in (0, 1, 2, 3):
            raise ValueError(f"invalid 0–3 score for {key}")
        if entry.get("source") not in {"gilbert_explicit", "personal_rating_9plus", "personal_rating_8plus"}:
            raise ValueError(f"unverified label source for {key}")
        if entry["source"] != "gilbert_explicit" and trusted_initial_labels is not None:
            original = (trusted_initial_labels.get("labels") or {}).get(key)
            if original != entry:
                raise ValueError(f"personal-rating provenance changed for {key}")

    by_group: Dict[str, Dict[str, int]] = {}
    for group in GROUPS:
        members = [item for item in items if group in item.get("groups", [])]
        rated = [item for item in members if item["id"] in entries]
        scores = Counter(entries[item["id"]]["score"] for item in rated)
        by_group[group] = {
            "candidates": len(members),
            "labeled": len(rated),
            "manual_labels": sum(entries[item["id"]]["source"] == "gilbert_explicit" for item in rated),
            "positive_2_or_3": scores[2] + scores[3],
            "negative_0_or_1": scores[0] + scores[1],
            "labeled_with_synopsis_and_genres": sum(
                bool(item.get("synopsis") and item.get("genres")) for item in rated
            ),
        }
    upcoming_ids = {item["id"] for item in items if any(group in item.get("groups", []) for group in UPCOMING)}
    upcoming_labeled = [key for key in entries if key in upcoming_ids]
    anime_movies = by_group["anime_movie"]
    ready = (
        anime_movies["labeled"] >= 30
        and anime_movies["positive_2_or_3"] >= 5
        and anime_movies["negative_0_or_1"] >= 5
        and len(upcoming_labeled) >= 30
        and all(by_group[group]["labeled"] >= 5 for group in UPCOMING)
        and sum(entries[key]["score"] >= 2 for key in upcoming_labeled) >= 5
        and sum(entries[key]["score"] <= 1 for key in upcoming_labeled) >= 5
    )
    return {
        "dataset_sha256": digest,
        "unique_candidates": len(items),
        "labeled_unique": len(entries),
        "manual_labels": sum(entry["source"] == "gilbert_explicit" for entry in entries.values()),
        "upcoming_labeled_unique": len(upcoming_labeled),
        "by_group": by_group,
        "minimum_label_gate_passed": ready,
    }


def model_row(item: Dict[str, Any], bench_id: str, *, mask_identity: bool) -> Dict[str, Any]:
    """Strict allowlist: labels, behavior and original provider IDs cannot leak."""
    groups = item.get("groups") or []
    media_type = (
        "anime" if "anime_movie" in groups or "upcoming_anime" in groups or "upcoming_anime_movie" in groups
        else "tv" if "upcoming_tv" in groups else "movie"
    )
    fmt = "MOVIE" if "anime_movie" in groups or "upcoming_anime_movie" in groups else None
    return {
        "bench_id": bench_id,
        "title": "[title withheld]" if mask_identity else item.get("title"),
        "year": item.get("year"),
        "type": media_type,
        "format": fmt,
        "synopsis": item.get("synopsis") or "",
        "genres": item.get("genres") or [],
        "keywords": item.get("keywords") or [],
        "cast": item.get("cast") or [],
        "creators": item.get("creator_director") or [],
        "studios": item.get("studio") or [],
        "franchise": item.get("franchise") or "",
        "source_material": item.get("source_material") or [],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate CineMind benchmark-only ratings")
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--labels", required=True, type=Path)
    parser.add_argument("--initial-labels", type=Path)
    args = parser.parse_args(argv)
    initial = json.loads(args.initial_labels.read_text()) if args.initial_labels else None
    report = validate(json.loads(args.candidates.read_text()), json.loads(args.labels.read_text()), initial)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["minimum_label_gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
