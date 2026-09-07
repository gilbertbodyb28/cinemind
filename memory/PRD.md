# CineMind AI — Product Requirements Document

## Problem Statement (verbatim)
> and very modern nice ui app that makes you able to get recommendation on shows and movies you willl like by reading my watch history on simkl trakt.tv sand my plex media sever and then know your taste exactly via ai recommendations via ollama llm privder

## User Choices (locked)
- Sources: **Simkl + Trakt.tv + Plex** (all three)
- LLM: **User-configurable Ollama URL + model**
- Feature set: **Full** — aggregated history, taste profile, recommendations with reasoning, save/dismiss
- Auth: **Google (Emergent-managed)**
- Design: Cinematic dark + Apple liquid glass + Neo-brutalist accents

## Architecture
- Backend: FastAPI + Motor (MongoDB), Emergent Google Auth, httpx for Ollama/Trakt/Simkl/Plex calls
- Frontend: React 19 + Tailwind + shadcn/ui, sonner toasts, lucide-react icons, Outfit/Inter/JetBrains Mono fonts
- Storage: MongoDB collections — users, user_sessions, connections, history, taste_profiles, recommendations
- Fallback: Every real integration has a demo-mode fallback so the app is fully usable without credentials

## Personas
- **Media enthusiast**: uses Plex + Trakt.tv daily, wants smarter picks than TMDB "similar"
- **Privacy-first tinkerer**: runs local Ollama, wants recommendations without their data leaving their network
- **Casual viewer**: signs in with Google, uses demo mode to explore

## Implemented (2026-02)
- Emergent Google Auth login/logout, session cookies + Bearer token support
- Connections page with 4 sections (Trakt / Simkl / Plex / Ollama) + test-connection buttons
- Watch history sync (real Trakt/Simkl/Plex API calls with graceful demo fallback)
- Dashboard: total watched, movies vs shows, genre bars, source distribution, avg rating, estimated hours, recent posters
- Taste Profile via Ollama with hand-crafted demo profile fallback (cinematic DNA, tropes, mood, complexity)
- AI Recommendations grid with match score, TMDB rating, "why this pick" reasoning
- Save / dismiss / detail modal / dedicated Saved library page
- Liquid-glass + neo-brutalist UI, cinematic dark theme with ambient gradients + grain

## Implemented (2026-06) — Claude fallback + power features
- Claude fallback via emergentintegrations when Ollama unset/unreachable (Taste Profile + Recommendations)
- **TMDB enrichment**: real posters/backdrops/ratings for LLM-generated picks (TMDB_API_KEY in backend/.env); lazy backfill of placeholder posters on list/saved
- **Model toggle**: Claude Sonnet 5 / Opus 5 / Haiku 4.5 — global default on Connections (`connections.llm_model`) + inline picker on Taste & Recommendations (per-action `{model}` body)
- **Streaming reasoning**: `GET /api/recommendations/{id}/reason/stream` SSE — Claude deep-dive "why this pick" streamed token-by-token in the detail modal, cached to `deep_why`
- **Usage meter**: `llm_usage` collection + `GET /api/usage`; sidebar widget shows monthly calls / est. tokens (LOCAL ESTIMATE — Emergent exposes no balance API)

- **Saved filters**: search, movie/show, case-normalized genre chips, "picked by" model chips, sort (recently saved / match / rating); `saved_at` stamped on save
- **Trakt device-code OAuth**: app-level `TRAKT_CLIENT_ID/SECRET` in backend/.env; `/api/trakt/device/start|poll`, `/api/trakt/disconnect`; single-use refresh-token rotation in `trakt_token()`; history sync uses `extended=full` (genres + ratings)
- **Trailer preview**: `GET /api/recommendations/{id}/trailer` (TMDB videos → YouTube key, cached); click-to-play `youtube-nocookie` embed in detail modal

- **History posters**: TMDB posters for synced Trakt/Simkl/Plex history (by tmdb_id or title), lazy backfill on `GET /api/history` (`poster_checked` flag)
- **Simkl PIN sign-in**: app-level `SIMKL_CLIENT_ID/SECRET`; `/api/simkl/pin/start|poll`, `/api/simkl/disconnect`; sync pulls movies + shows + anime via `/sync/all-items?extended=full`; shared `DeviceConnect` component for Trakt + Simkl
- **Trakt watchlist push**: `POST /api/recommendations/{id}/watchlist` (409 when Trakt not connected); `WatchlistButton` on Recommendations cards, Saved cards, and modal pill
- Fixed dead TMDB demo poster URLs (Landing hero + DEMO_HISTORY/DEMO_RECS)

## Backlog
- P2: "Chat with your taste" conversational mode
- P2: Weekly digest email (Resend)
- P2: Shareable taste profile cards / Taste Twins
- P2: Ollama streaming responses for reasoning
- P2: Simkl watchlist push (mirror of Trakt)
- P3: Split server.py (~1200 lines) into routers

## Test Credentials
See /app/memory/test_credentials.md — Emergent Google Auth uses no app-managed passwords; testing agent seeds sessions directly in MongoDB.
