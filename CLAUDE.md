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
anything below. **Open work starts at `HANDOFF.md` omgång 8 (2026-09-25 evening):**
CineMind runs on the NAS (omgång 7, `scripts/deploy_nas.sh`); everything in the
workspace is deployed there; Trakt, Simkl and Plex need Gilbert to sign in again
(the Tv job fails every run until Trakt and Simkl work), and the queue backlog
is a clean-up decision for Gilbert (`evaluation/queue_cleanup.py`, manifest
`arch_20260925_152824`; `arch_20260925_152446` predates a fix and must not be applied).

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
- **A saved job is served by what it asks for, not by the profile's habits.**
  `recommendation/job_intent.py` reads lanes, languages (the job's, else
  English) and kids from the job; `jobs.engine.with_job_intent` opts saved jobs
  in, Content to Watch sets `job_intent: False`, offline/AI Search never carry
  it. Live-action discover asks English first and leaves out Animation/Kids
  unless asked; similarity lanes are seeded from `lane_seed_docs` in the job's
  own lane; AniList is skipped when no anime/donghua lane is wanted;
  `select_final` fills PRIMARY → other languages → at most 10 % from lanes the
  job never named, and live action keeps ≥ 60 % of a mixed job. Lane balancing
  only shares slots between lanes the job named. A TV job in Fantasy / Sci-Fi /
  Action / Adventure went from 7 to 20 English live-action series in its top 20.
- **Trakt `/recommendations` returns bare movie/show objects**, not
  `{"movie": …}`. Parsed the old way every row was "Unknown" and dropped, so
  Trakt contributed nothing to any job until 2026-09-24.
- **Trace a job before changing discovery:** `python3 -m evaluation.job_trace
  --user <id> --job <id> [--include a,b] [--llm]` shows candidates per stage and
  lane (before/after filtering, scoring, LLM) without writing anything.
  `--spec '<job json>'` traces an unsaved job; every stage is also counted as
  English live action / other languages / anime / donghua / animation / kids.
- **A failed `GET /jobs` is not an empty list.** Jobs.jsx retries and says so;
  it used to show "no jobs" whenever the backend was restarting.

- **The profile must see all of the history** (omgång 4, `HANDOFF.md` §22–28).
  The 2026-09-08 sync kept 10,000 of 13,490 Trakt plays and 97 of 176 ratings.
  `providers/live_history.py` overlays Trakt's ratings/watched sets and the
  AniList list (POINT_10) in memory, read-only; `providers/history_sync.py`
  is the complete sync `/history/sync` now uses. Dry-run it before anything else.
- **The history sync merges; it never deletes** (omgång 5, `HANDOFF.md` §30).
  A play is recognised by `provider_play_id` (unique index
  `history_provider_play_unique`); older rows are matched once by title id +
  watch time as a multiset. Empty values never overwrite, a personal rating is
  never removed and a changed one keeps `previous_rating`. `watch_count` is
  times watched *through*, not episode plays. Back up before a real run
  (`.runtime/backups/`), then re-run it: the second run must import 0.
- **A job never changes a decision in the queue** (`request_providers.submit`):
  approved, rejected, dismissed, archived or `rejected_at` rows are final for
  job-written statuses; a title is found by canonical / AniList / TMDb id in its
  own namespace before a new row is made; re-suggesting a pending title
  refreshes it without moving it. 79 approvals had been pushed back to pending.
  Archiving is only ever `evaluation/queue_cleanup.py plan → apply → revert`.
- **An anime film is a film.** `filter_engine.media_type_allowed`: only Movies or
  Anime admits it; the anime-film discover lane runs only for such jobs
  (`tmdb.anime_films_wanted`); queue rows store `format`/`media_type`/
  `canonical_media_id`, and `exclusion_engine.stored_keys` matches an anime row
  stored without format as film and series. "Upcoming Tv Shows" was 5 anime films.
- **A Simkl token belongs to the app that issued it**
  (`providers.simkl.simkl_token_client_id`). The manual Client ID in Sources was
  dead (412) and outranked the server's app everywhere.
- **Taste sources are decided per row, before the merge**
  (`taste_engine.source_allowed`). Plan-to-watch rows and demo-shelf rows
  (`recommendation/demo_seed.py`) never enter the profile; a Simkl
  "plantowatch" once zeroed Family Guy's 389 episodes.
- **Requests decisions are taste evidence** (`taste_engine.decision_docs`):
  approved = positive, rejected = negative, but a rejected title the user has
  watched is not a dislike (Logan, 10/10).
- **Genres are canonical in the profile** (`taste_genres`: aliases and "A & B"
  names), TMDb's combined TV ids are not expanded for taste. Measured trade-off
  in `taste_engine.CANONICAL_GENRES`.
- **A match is personal or it is not a match.** `match_score` and the taste
  floor read `personal_score` and `specific_score` (a concrete link: shared
  distinctive themes, creator, cast, franchise, studio, or a recommendation from
  a liked title). Format, language, quality, popularity and job terms order the
  list but never make a title a match. A job returns fewer picks rather than
  titles below `pipeline.TASTE_FLOOR`.
- **The floor moves with the personal weights.** `pipeline.TASTE_FLOOR` and
  `ranking_engine.MATCH_CENTER` are 2.5 since the 2026-09-25 weights
  (`liked_title_similarity` 5.0, `people_affinity` 1.6,
  `NEGATIVE_EVIDENCE_SCALE = "liked"`). Change a personal weight and you must
  re-check that the floor still keeps ~97 % of held-out favourites and ~37 % of
  the rest of the Tv pool, and ~93 % of approved Requests.
- **A keyword that restates a genre is not a concrete link**
  (`similarity.SPECIFIC_SKIPS_GENRE_WORDS`): "themes fantasy, comedy" let
  zero-vote titles through the floor. It never changes the ranking itself.
- **The broad holdout rewards provider spelling.** Its labels are Trakt/AniList
  rows; most other rows are TMDb/Simkl ("Sci-Fi & Fantasy"). An engine that
  treats spellings as different genres scores higher for the wrong reason
  (old engine 0.395 → 0.355 P@5 once spellings are one). When comparing
  engines, also run them on a one-spelling copy of the snapshot (`HANDOFF.md`
  §33) — as a diagnostic next to the unchanged benchmark, never instead of it.
- **Saved jobs re-rank bounded, Content to Watch freely inside the floor**
  (`ranking_engine.RERANK_MAX_BOOST`): measured, the raw model order cut a TV
  job's MRR 0.969 -> 0.721 and lifted Content to Watch's 0.483 -> 0.802.
- **Explain a job before touching it:** `python3 -m evaluation.taste_report
  --user <id> sources --live | profile | explain --job <id> [--llm] | compare
  --job <id>`. Measure a saved job's own pool with `evaluation.job_offline
  --benchmark lane_holdout` and Requests with `--benchmark request_decisions`.

Added 2026-09-25, omgång 6 (`HANDOFF.md` §38–46):

- **The model reorders the strong pool, never lifts a weak match** (cloud
  `9949f1d`). A pick below `ranking_engine.relevance_cut(pool)` keeps its
  deterministic place; lanes are measured from their best row, not their first.
  One weak pick first used to end `apply_diversity`'s walk and empty Content to
  Watch. `RERANK_LLM_KEEP = 5` (cloud `2930177`) applies only where the model's
  own order is used (Content to Watch); saved jobs and AI Search re-rank bounded
  over the whole floor-checked order, as measured (`jobs.engine.rerank_keep`,
  `apply_model_order`). Evaluation tools must apply the order the same way.
- **A decision stands on the job's own path too.** `apply_job_action_mode` looks
  every title up with `request_providers.find_existing_request`: rejected or
  dismissed → not queued, not sent to MediaManager, recommendation hidden;
  approved → not sent again (cloud `bb8e0dc`, adapted).
- **A job keeps at most its `final_recommendation_limit` titles waiting.** New
  titles only take the room left; waiting ones are refreshed; nothing queued is
  touched; the run warns `queue_full`. 10,180 titles were waiting on 2026-09-25.
- **A dismissal is remembered.** A new run never deletes dismissed
  recommendation rows, and `exclusion_engine` rejects their titles
  (`rejected_dismissed`). Deleting them with the old list let a pick rejected on
  Home come straight back.
- **A refused sign-in is not "Connected"** (`providers/auth_state.py`). A 401
  (Simkl 401/412) from a test, sync or job records `<provider>_auth_error`;
  `connections_public` then reports the provider as not connected, so Sources
  offers Connect with the reason. A new sign-in or a successful call clears it.
  Plex signs in with a plex.tv/link code (`/api/plex/pin/start|poll`).
- **Upcoming means a verified premiere after today** (`providers/premieres.py`):
  a film's release day, a series premiere, or episode 1 of a coming season of an
  older series — never a year, a month or the next weekly episode. Jobs opt in
  with `filters.upcoming_only` (Jobs: "Only upcoming premieres"): the window is
  measured on the premiere (`rejected_not_upcoming`) and a returning-series lane
  (`tmdb.upcoming_lanes`) finds new seasons. Home's "Up Coming" reads
  `GET /api/upcoming`; it used to show Content to Watch picks 2–5.
- **Page the Requests queue by queued rows** (cloud `56cedeb`): `loadMore`'s
  offset and the sentinel count only queued rows.
- **One page of a provider is not its history.** `/history/sync` goes through
  `providers/history_sync` (merge, never delete); the cloud's one-page guard
  (`7b92a58`) targets the old endpoint and is superseded, not merged.
- **Verify on the Mac:** `python3 -m evaluation.verify_live --user <id>` runs the
  connection tests, stored sync counts, upcoming jobs (verified premieres), the
  Content to Watch ranking and the queue paging read-only; `--sync` and
  `--generate` write. It signs in by writing a short session for the user:
  ask Gilbert first.

Added 2026-09-25, omgång 8 (`HANDOFF.md` omgång 8):

- **A shown pick is re-checked when it is shown** (`recommendation/shown_picks.py`):
  `GET /recommendations` and `GET /upcoming` retire picks settled since their
  run (watched, rated, in the library, queued or decided under another row,
  rejected on another list). **A decision is about the title**
  (`request_providers.settle_duplicates`): approving or rejecting archives the
  title's waiting copies (batch `dup:<id>`, revertible with `queue_cleanup revert`).
- **Sources asks the providers** (`POST /connections/verify` on open,
  `auth_state.verify_connections`); a stored token proves nothing.
- **A queue row has no premiere date.** Anything judging queue rows of an
  `upcoming_only` job verifies premieres first (`queue_cleanup._verified_premieres`,
  `verify_premieres(store=False, unanswered=...)`); otherwise every waiting series
  reads as "not upcoming".

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
  box; plain `gemma4:12b` is not installed. Full numbers in `HANDOFF.md`.
- Gemma 4 re-ranks too aggressively in the tail. Restricting it to its top 5
  and letting the deterministic order keep positions 6–10 measured
  +0.022 ± 0.008 nDCG@10 (t=2.64). Implemented 2026-09-25 as
  `jobs.engine.RERANK_LLM_KEEP = 5` for the model's own order only (Content to
  Watch), limited to picks inside the relevance floor; measured once on the
  older engine and not confirmed with a second fold count — 12 undoes it.
  Not deployed yet (`HANDOFF.md` §41).
- Rerank pool: 12 candidates, short opaque handles (`r01`…), JSON-schema
  constrained. Long slug IDs made the model give up after the first one.
- Inference: greedy, fixed seed, `num_ctx 8192`. Ranking wants the same answer
  twice.
- The LLM re-ranks validated catalogue candidates. It is never the title
  database — current and upcoming titles come from TMDb and AniList.

## Vision UI still applies

`ProvenanceChips.jsx` is the only frontend file the ranking work touched, and it
reuses the existing `.chip` classes. Omgång 6 touched `DeviceConnect.jsx`
(a `notice` line), `Connections.jsx` (Connect with Plex), `Home.jsx` (Up Coming
from `/api/upcoming`, premiere date, empty state), `Jobs.jsx` (the "Only
upcoming premieres" chip) and `Requests.jsx` (cloud paging fix) — existing
`glass` / `chip` / `chip-rose` classes and listed tokens only; the lock passed.
Run `python3 scripts/check_vision_ui_lock.py` after any frontend change.
