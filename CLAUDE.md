# Claude Code — CineMind

HARD STOP. Vision UI is locked permanently.

Do not edit `frontend/src/index.css`, `frontend/src/App.css`,
`frontend/tailwind.config.js`, or theme colors in `frontend/public/index.html`.

Copy existing glass / chip / pill / gold-rail controls for new UI.
Do not invent colors. Do not replace the look with Emergent liquid-glass.

After any frontend change run: `python3 scripts/check_vision_ui_lock.py`

Only Gilbert can unlock this, in an explicit message that names the visual
change.

See `AGENTS.md`.

---

# Recommendation engine

Rebuilt and measured 2026-09-22. Full root-cause report, before/after metrics
and the Ollama benchmark live in `HANDOFF.md`. Read it before changing
anything below.

## Do not reintroduce the bugs that were just removed

- **Never let a raw provider score into the ranking total.** Every component in
  `ranking_engine.DEFAULT_WEIGHTS` is normalised to −1..1 *before* weighting.
  The old `seed_similarity` fed uncapped TMDb popularity straight in, which put
  seven talk shows and a German news bulletin above everything the user had
  rated. `popularity` is deliberately the smallest weight (0.35).
- **Never normalise a preference by how many rows its provider contributed.**
  The old `1/provider_count × len(providers)` factor made the taste profile
  emptier the larger the history: a 10/10 title scored 0.0285 against a 1.5
  threshold. Aggregate first, normalise once at the end (`_finalize`).
- **Watched is not liked.** `evidence_score` weighs personal ratings, rewatches
  and episode depth. A single sampled episode is weak evidence, not equal
  evidence.
- **Personal ratings live in `media_history`, watch events in `history`.**
  Load both — `load_pipeline_inputs` passes `personal_history` for this reason.
  Reading one or the other is the bug that hid all 97 of Gilbert's ratings.
- **Reasons must come from the score.** `_explain` reads the actual
  contributions. Never fall back to "top genres in the profile" — that is how a
  documentary came to be explained as "matches Action, Adventure".
- **Read recommendations back by `rank`, not `created_at`.** Rows are written
  best-first with increasing timestamps; sorting by time reverses them and the
  worst pick becomes the hero card. `server.by_rank` guards this, and
  `recommendation_tests/test_content_to_watch.py` regression-tests it.

## Changing weights, prompts or the model

Measure it. Do not reason about it.

```bash
cd backend
PYTHONPATH=../.runtime/python:. python3 -m evaluation.snapshot \
  --user <user_id> --output /tmp/snap.json
PYTHONPATH=../.runtime/python:. python3 -m evaluation.offline \
  --snapshot /tmp/snap.json --folds 8
```

Then run both control arms. `--control empty_taste` and
`--control shuffled_taste` **must** collapse to near zero. If they do not, the
labels are leaking and the headline numbers are worthless.

Model changes go through `evaluation/model_bench.py`, which includes a
`__deterministic__` and a `__shuffled__` arm. A model only replaces the current
one on measured recommendation quality — not on being newer or larger.

Labels are personal Trakt/AniList ratings ≥ 8 or explicit likes, never
"watched". Do not widen that rule to make a number look better.

## Current configuration

- Ollama model: `qwen-suggestarr` (= `qwen2.5:7b-instruct-q6_K`). Benchmarked
  best of seven; `qwen3:14b` lost clearly and is retired in
  `LEGACY_OLLAMA_MODELS`.
- Rerank pool: 12 candidates, short opaque handles (`r01`…), JSON-schema
  constrained. Long slug IDs made the model give up after the first one.
- Inference: greedy, fixed seed, `num_ctx 8192`. Ranking wants the same answer
  twice.
- The LLM re-ranks validated catalogue candidates. It is never the title
  database — current and upcoming titles come from TMDb and AniList.

## Vision UI still applies

`ProvenanceChips.jsx` is the only frontend file this work touched, and it
reuses the existing `.chip` classes. Run
`python3 scripts/check_vision_ui_lock.py` after any frontend change.
