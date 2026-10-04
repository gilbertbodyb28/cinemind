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
anything below. **Open work starts at `HANDOFF.md` omgång 14 (2026-09-29):**
every job asks for Action, Adventure, Gay Romance, Sci-Fi, Fantasy, Animation and
Kids with a minimum rating of 8.0 for released titles (none for coming ones), and
the Requests / Approved filter chips are 20 px (Gilbert's unlock); deployed
15:58 and 16:07 UTC, jobs changed 16:08 UTC. Omgång 13 before it: every saved job
run must send at least 100 results to Requests (Gilbert's rule, see below).
Upcoming is CineMind's first priority (Gilbert); nothing is committed.
Omgång 9 state (superseded where omgång 13 says so):
CineMind runs on the NAS (omgång 7, `scripts/deploy_nas.sh`); everything in the
workspace is deployed there; all four sources were signed in again 15:58 UTC.
Gilbert's three jobs keep at most 650 titles waiting each (his choice); the
clean-up `arch_20260925_165033` is applied (3,910 archived, `queue_cleanup revert`
restores them). Tv (2,156 waiting) and Upcoming US (3,982) stay full until
Gilbert decides on titles; what to do with that surplus is his decision.

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
- **Interval jobs run every 15 minutes, 2 minutes apart** (Gilbert, 2026-09-25;
  it was 30 / 6). `jobs.engine.INTERVAL_SCHEDULE = "every_15m"`, 7 slots
  (0, 2 … 12); the old `every_30m` key is normalised and migrated at startup.
  The scheduler runs due jobs one after another, so a job that takes longer
  than 2 minutes pushes the next one back rather than overlapping it.
- **A failed `GET /jobs` is not an empty list.** Jobs.jsx retries and says so;
  it used to show "no jobs" whenever the backend was restarting. **A 401 is a
  lost session, not an empty list either** (2026-09-26): `lib/api.js` raises
  `SESSION_ENDED_EVENT`, `AuthContext` re-checks `/auth/me` once and sends the
  tab to sign-in with a notice and back to the same page. A tab that kept the
  signed-in layout after its session ended made all three jobs look deleted.

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
  list but never make a title a match. Below `pipeline.TASTE_FLOOR` a title is
  never presented as a match: since 2026-09-29 a saved job fills its results
  with the closest such titles (Gilbert's rule, omgång 13), marked `weak_match`
  and shown as "weaker match"; Content to Watch and AI Search never take them.
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
- **No cap holds a result back from Requests** (Gilbert, 2026-09-29, omgång 13;
  it was "a job keeps at most its limit waiting" from 2026-09-25, which held
  every new title of Tv and Upcoming US back). Waiting ones are refreshed,
  nothing queued is touched, `held_back` is always 0; old runs' `queue_full`
  still reads as an "okey" notice. `run.requests` says what actually reached
  Requests (queued / refreshed / waiting); the toast used to call held-back
  picks "sent to Requests".
- **A run that finds nothing new is saturated, not broken.** `no_picks` is a
  warning only when nothing got past the job's own settings, and a filter's hint
  ("No candidate fell inside the year window") only when that filter removed
  every candidate. Otherwise the run gives the notice `no_new_picks` with the
  whole breakdown: already in Requests, watched, outside the settings, below the
  taste floor. Tv at 16:34 UTC blamed its year window while 436 candidates were
  already in Requests and 18 fell below the floor.
- **The taste floor decides what is a match; the limit decides how many
  results.** Measured 2026-09-25 (`job_trace`): Tv 1,105 candidates → 70 past
  settings and exclusions → 2 clear the floor. Since omgång 13 a saved job's
  results fill up to its limit (see the omgång 13 rules below).
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

Added 2026-09-25, omgång 10 (`HANDOFF.md` omgång 10) — Upcoming first:

- **An upcoming job never stops at too few picks** (`jobs/upcoming.py`). A job
  with `upcoming_only` puts the coming continuations of liked titles first, and
  with fewer than `UPCOMING_TARGET` new picks widens step by step
  (`taste_window` → `deeper_pages` → `other_sources`: Trakt anticipated and
  premiere calendar, AniList further down, TMDb /movie/upcoming), re-running the
  pipeline after each step, until the target is met or every step was tried;
  `run.upcoming_search` records each step. The bar never moves: filters,
  exclusions, the taste floor and the queue rules apply to every step's titles.
- **A coming continuation of a liked title is a franchise link**
  (`providers/continuations.py`, `ranking_engine.continuation_link`): a TMDb
  season ≥ 2 of a liked series, a coming film in a liked film's collection,
  AniList sequel / spin-off / side-story chains (matched by name without year
  and season tags). Only a liked title in *this* profile counts, like
  `seed_support`; it feeds `franchise_affinity` and `specific_score`, and the
  reason says "is season 3 of X, which you rated 10/10". One row per season:
  TMDb's wins over AniList's, which lends it its id.
- **A coming season is not "already watched" or "in the library"**
  (`exclusion_engine.is_coming_continuation`); a "no" in the queue still stands,
  and a coming season of a series in the library is shown, never queued.
- **An upcoming job's open picks stay until their premiere**
  (`jobs.upcoming.carry_over`); each run used to replace the list, and Up Coming
  shrank to the newest run's picks.
- **Up Coming is everything coming that is the user's** (Gilbert): picks of
  enabled jobs, waiting and approved queue rows with a verified premiere (premiere
  fields on request rows by `refresh_request_premieres` — never status or
  `updated_at`), coming seasons even in the library. Only blacklist, a dismissal
  or a rejection hides one (`shown_picks.upcoming_hidden_reason`, read-only).
  Requests lists coming premieres first (`upcoming_first`, the default).
- **Upcoming taste lanes ask inside the premiere window, without a vote floor**
  (`tmdb.window_params`); every other job's lanes ask exactly as before.
- **An unreleased title's metadata is fetched again after 7 days**
  (`tmdb_enrich.unreleased_when_fetched`); a released title's never expires.

Added 2026-09-29, omgång 13 (`HANDOFF.md` omgång 13) — Gilbert's rule: **every
saved job run sends at least 100 results to Requests, preferably well over 1,000**:

- **A result is a title that fits the job, new or still open.** With
  `open_results` (`jobs.engine.with_result_rules`, every saved job) a title
  waiting in Requests (queued by any job) or on the job's own list is scored and
  selected like a new one (`exclusion_engine` `open_result: waiting|listed`) and
  refreshed, never queued twice. Approved / rejected / archived rows still keep a
  title out, also when a copy waits. Content to Watch sets `open_results: False,
  min_results: 0` and keeps its measured top eight.
- **The head of the list is chosen exactly as before, then the rest follows**
  (`pipeline._complete_results`): every other title over the floor in the job's
  tier order, then the closest below the floor up to `result_target` (the job's
  limit, max 1,500), marked `weak_match`. Off-intent lanes keep their 10 % and
  pass it only to reach `MIN_RESULTS` (100). Never "fix" the fill-up as a
  regression; never drop a run below 100 when the job's settings let 100 through.
- **Every saved job widens before it fills** (`jobs.upcoming.search_more` /
  `broaden`, stop rule `enough`: 100 matches over the floor, a full list, and 20
  new picks for upcoming jobs). Stages: `taste_window` → `deeper_pages` →
  `other_sources` (Trakt trending / popular / anticipated inside the job's
  genres and years for ordinary jobs, `providers.trakt.fetch_list_titles`) →
  `full_budget` (the job's lanes with 30,000 candidates). `run.search` records it.
- **The run says what its results are**: `run.result_counts` (results, matches,
  weak, new, waiting, listed), summary lines `results_filled` / `results_short`
  (never warnings), Runtime logs "N results from M candidates; X new in Requests,
  Y already waiting", Requests shows the chip "weaker match".
- **A poster is looked up by the row's own TMDb id** (`tmdb._poster_by_id`,
  cached, 8 at a time); the name search wrote another title's id over the row's.

Added 2026-09-29, omgång 14 (`HANDOFF.md` omgång 14) — Gilbert: every job asks for
**Action, Adventure, Gay Romance, Sci-Fi, Fantasy, Animation, Kids**, and a
minimum rating of **8.0 for released titles, 0.0 for coming ones**:

- **Gay romance is one genre: romance with an LGBTQ theme, never ordinary
  romance** (his answer when asked). No provider has it; `filter_engine.
  is_gay_romance` reads TMDb keywords and AniList tags: a same-sex romance
  keyword (`gay romance`, `boys' love (bl)`, `girls' love (gl)`, AniList
  `Boys' Love` / `Yuri` …) or romance plus **two** LGBTQ keywords. TMDb puts
  `lgbt` / `gay theme` on straight romcoms with a gay friend, so one is not
  enough. `candidate_genres` adds `gay romance`; its aliases live in
  `THEME_GENRE_ALIASES`, never in `GENRE_ALIASES` (the taste profile reads that).
  TMDb is asked by keyword (`tmdb.theme_lanes`, ids measured 2026-09-29): it has
  no genre id, no Romance genre for series, and reads `A|B,C` as `A,C`.
- **Kids takes family films** (`filter_engine.FILM_GENRE_EQUIVALENTS`,
  `matched_genres`): TMDb has no Kids genre for films and `_genre_ids` already
  asked for Family. Series keep Kids (TMDb 10762, Trakt `children`; Trakt films
  `family`, `trakt_genre_filter(names, kind)`).
- **The minimum rating is for titles that are out** (`filter_engine.
  not_released_yet`, `candidate_rating`): a verified premiere after today, a
  future date, a not-out status, a later year, or this year's title nobody has
  rated is never held to it. AniList rows are held to AniList's own score
  (`candidate_score`), which is not copied onto the row (the ranking reads rating
  fields). TMDb discover asks a window reaching past today in two parts
  (`tmdb.rating_windows`): released with `vote_average.gte`, coming without a
  rating or vote floor. The form says "Minimum rating (released titles)".
- **Animation in a job means every animated lane** — anime, donghua and Western
  animation (`job_intent.animation_lane_languages`). Tv is no longer a
  live-action-only job since it names Animation and Kids; that was his ask.

Added 2026-09-26, omgång 12 (`HANDOFF.md` omgång 12):

- **An announced month or year is a premiere** (Gilbert, 2026-09-26). AniList gave
  a day for 81 of its 400 top NOT_YET_RELEASED anime (13 of 57 films).
  `premieres.announced_date`: `premiere_date` is the last day of the period and
  `premiere_precision` says `month` / `year`; a title with no year is still not
  upcoming. A TMDb row TMDb has no date for is asked on AniList too.
- **Along an AniList chain the franchise before a subtitle names the liked title**
  (`continuations.chain_keys`), and a linked AniList title takes the liked title's
  missing metadata (`_root_fields`): announced films have no genres and fell
  under the floor while continuing a 10/10 series.

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
- 2026-09-25: Gilbert's account (`user_c30bd548254a`) runs
  `qwen-suggestarr:latest` by his choice; the default stays Gemma. Retired
  defaults (`config.LEGACY_OLLAMA_MODELS`) are listed in the picker again and
  honoured once saved (`ollama_model_chosen`) or passed per run; an old stored
  value without the mark still falls back to the default.
- Gemma 4 re-ranks too aggressively in the tail. Restricting it to its top 5
  and letting the deterministic order keep positions 6–10 measured
  +0.022 ± 0.008 nDCG@10 (t=2.64). Implemented 2026-09-25 as
  `jobs.engine.RERANK_LLM_KEEP = 5` for the model's own order only (Content to
  Watch), limited to picks inside the relevance floor; measured once on the
  older engine and not confirmed with a second fold count — 12 undoes it.
  Not deployed yet (`HANDOFF.md` §41).
- Rerank pool: 12 candidates, short opaque handles (`r01`…), JSON-schema
  constrained. Long slug IDs made the model give up after the first one.
- Inference: greedy, fixed seed, `num_ctx 32768`. Ranking wants the same answer
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
Omgång 10: Gilbert unlocked one layout change on 2026-09-25 — Up Coming before
Content to Watch on Home (top right beside New Trailer, first on a phone, and
shown when Content to Watch is empty). Only the order changed; `Requests.jsx`
got the "Upcoming premieres first" sort option. Omgång 13 (2026-09-29) added
text and existing `chip` / `chip-cyan` only: results counts in `Jobs.jsx` and a
"weaker match" chip in `Jobs.jsx` and `Requests.jsx`; the lock passed. Omgång 14
changed one label's text in `Jobs.jsx` ("Minimum rating (released titles)"), and
**Gilbert unlocked one visual change on 2026-09-29**, with screenshots of the
Requests filter card: "sedan ska dessa förstoras en hel del … det gäller
filtrena", "gör det till 20px", "gör siffrorna till 20 px också". The filter chips
of Requests and Approved (Release, Media type, No date, the "N of M" count, Clear
filters) are 20 px text and numbers via `ReleaseTypeFilters.BIG_CHIP`
(`!text-xl …`: `.chip` sits after the utilities in `index.css`, so size
utilities need `!`), the card headings `text-2xl`; same `.chip` look, no new
colour, `index.css` untouched; the lock passed. Nothing else is unlocked.
Run `python3 scripts/check_vision_ui_lock.py` after any frontend change.
