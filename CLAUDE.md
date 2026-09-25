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
anything below. The open work and Gilbert's decisions of 2026-09-25 (full
sync, verified upcoming premieres, the queue) are in `HANDOFF.md` §19.

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
- **Genres are matched canonically and with OR.** `filter_engine.candidate_genres`
  resolves "Science-Fiction", "Sci-Fi & Fantasy", TMDb ids 10759/10765 and adds
  the lane (`media_identity.content_lane`: anime / donghua / animation /
  live_action). Anime = Japanese, donghua = Chinese animation; origin alone never
  makes live action donghua. Donghua needs its own TMDb vote floor (only 19 zh
  animated series have 50 votes).
- **Saved jobs balance their final slots across lanes** (`apply_lane_balance`);
  Content to Watch sets `lane_balance: False` and keeps the measured selection.
  Before this, an anime-heavy profile turned every job into anime films.
- **Trace a job before changing discovery:** `python3 -m evaluation.job_trace
  --user <id> --job <id> [--include a,b] [--llm]` shows candidates per stage and
  lane (before/after filtering, scoring, LLM) without writing anything.
- **A failed `GET /jobs` is not an empty list.** Jobs.jsx retries and says so;
  it used to show "no jobs" whenever the backend was restarting.
- **One page of a provider is not its history.** `/history/sync` reads Trakt,
  Simkl and Plex one page deep (`SYNC_PAGE`); an answer that hit the cap never
  replaces a larger stored history. One press of Sync used to cut 10,210 Trakt
  rows to 50. `recommendation_tests/test_history_sync_guard.py` covers it. The
  full, paginated sync Gilbert approved must keep that rule for partial fetches.
- **The model reorders the strong pool, never lifts a weak match.** Gemma's
  top-five picks below `ranking_engine.relevance_cut` keep their deterministic
  place, and lanes are measured from their best row. The floor used to run
  only as a `break` over a list the model had reordered, so one weak pick
  emptied Content to Watch instead of being left out.
- **A job never overturns a rejection.** `LocalRequestProvider.submit` keeps
  rejected rows (`KEPT_STATUSES`) and `apply_job_action_mode` skips rejected
  titles, so a title the exclusions miss is neither queued nor auto-sent.
- **Page the Requests queue by queued rows.** Approved rows stay in `items`
  until the next refresh, so `loadMore`'s offset and the sentinel count only
  queued ones; counting all of them skipped as many titles as were approved.
- **Verify on the Mac, not by assumption:** `python3 -m evaluation.verify_live
  --user <id>` runs the source connection tests, stored sync counts, upcoming
  jobs, the Content to Watch ranking and the queue paging in one read-only pass.

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

- Ollama model: `gemma4:12b-it-qat`, selected by Gilbert 2026-09-24. Measured
  against `qwen-suggestarr` on 2026-09-23 with 32 folds × 3 repeats (n=99 per
  arm) on Gilbert's real snapshot: it beats the deterministic baseline on every
  metric (nDCG@5 +0.117 ± 0.030, MRR +0.176 ± 0.039) but beats
  `qwen-suggestarr` only on MRR (+0.087 ± 0.026); nDCG@5 (+0.045 ± 0.025) and
  nDCG@10 (+0.001) are not significant. It is chosen for top-1 quality — MRR is
  what decides the hero card — at ~4× the latency (5.0 s vs 1.2 s).
  The QAT build is the quantisation-aware one and the only 12B Gemma 4 on the
  box; plain `gemma4:12b` is not installed. The recorded deltas are in
  `HANDOFF.md` §18; the per-arm absolute values were never committed.
- Gemma 4 re-ranks too aggressively in the tail. Restricting it to its top 5
  and letting the deterministic order keep positions 6–10 measured
  +0.022 ± 0.008 nDCG@10 (t=2.64). Implemented 2026-09-25 as
  `RERANK_LLM_KEEP = 5` in `jobs/engine.py`, limited to picks inside the
  relevance floor; measured once, not yet confirmed with a second fold count.
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
