# Master prompt progress

Källan till sanning för förloppsraden. Andel klart = summan av poängen
nedan delat med 31. Återstående % = `100 − andel klart`, avrundat.

Filen saknades på disk och rekonstruerades 2026-09-08 mot vad som faktiskt
finns i kedjan frontend → API → databas (master-prompt §78).

## Slutkriterier

| # | Kriterium | Poäng |
|---|---|---|
| 1 | Lokal runtime (API + frontend + Mongo) | 1,0 |
| 2 | Google / lokal inloggning med session | 1,0 |
| 3 | Vision UI / låst tema | 1,0 |
| 4 | Sources: Trakt device OAuth | 1,0 |
| 5 | Sources: Simkl PIN | 1,0 |
| 6 | Sources: Plex URL + token | 1,0 |
| 7 | Sources: AniList OAuth | 1,0 |
| 8 | Sources: Ollama | 0,9 |
| 9 | Sources: MediaManager + test | 1,0 |
| 10 | TMDb posters / trailers | 0,8 |
| 11 | Rekommendationer generate | 0,8 |
| 12 | Approve → MediaManager-bibliotek | 1,0 |
| 13 | Requests-kö approve/reject | 0,8 |
| 14 | Jobs-motor (schema + körning) | 0,5 |
| 15 | Taste-profil | 0,4 |
| 16 | History sync (Plex/Trakt/Simkl/AniList) | 0,5 |
| 17 | Watchlist (Trakt) | 0,8 |
| 18 | Saved library | 0,8 |
| 19 | Landing + Sources copy (inkl. AniList) | 1,0 |
| 20 | Användarscoping | 0,6 |
| 21 | Upcoming / kalender | 0,0 |
| 22 | AI-sök | 0,3 |
| 23 | Feedback / blacklist | 0,2 |
| 24 | Canonical title merge | 0,2 |
| 25 | Provider keys UI (TVDb/Seer) | 0,0 |
| 26 | Rec reason stream | 0,7 |
| 27 | Usage meter | 0,5 |
| 28 | Tester mot kedjan | 0,4 |
| 29 | Indexer / downloaders | 0,0 |
| 30 | Dokumentation mot master-prompt | 0,1 |
| 31 | Slutlig E2E mot alla källor | 0,2 |

**Summa: 19,4 / 31 → 63 % klart → ~37 % kvar**

Aktiv fas: 8/18 (källor + approve-bibliotek). Inte 31/31 — AniList i Sources
och MediaManager-godkännande saknade UI tills 2026-09-08. Jobs auto_request
skickar till MediaManager; require_approval ger pending Requests med
approve/reject-ikoner. Full E2E mot live-bibliotek är inte kört i inloggad UI.
