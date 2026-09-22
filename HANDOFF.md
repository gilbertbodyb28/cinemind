# HANDOFF — rekommendationsmotorn

Djupgranskning, rotorsaksanalys, åtgärd och mätning av hela
rekommendationskedjan. Utfört 2026-09-22 på gren `phase-0-local-runtime`.

Allt nedan är uppmätt mot Gilberts riktiga data (`user_c30bd548254a`,
10 210 historikrader, 669 `media_history`, 97 egna betyg ≥ 8), inte härlett.

---

## 1. Rotorsaker

### RO-1 — Smakprofilen var tom, och blev tommare ju mer historik som fanns

`build_taste_snapshot` viktade varje titel med
`råpoäng × leverantörsvikt × (1/antal_rader_från_leverantören) × antal_leverantörer`.

Högsta uppnåeliga poäng mot tröskeln `1.5` för "hög tillit":

| leverantör | rader | max poäng |
|---|---|---|
| trakt | 421 | **0,0285** |
| simkl | 150 | 0,0800 |
| anilist | 43 | 0,4186 |
| plex | 11 | 1,0909 |

Tröskeln gick bara att nå om en leverantör hade **färre än åtta rader totalt**.
Följd: `high_confidence_positive_titles`, `negative_titles`,
`frequently_rewatched_titles`, `liked_genres` och `disliked_genres` var alla
tomma. Därmed var `positive_title_similarity`, `negative_title_similarity` och
`feedback_overlap` konstant noll i rankningen.

### RO-2 — De egna betygen lästes aldrig in

`load_pipeline_inputs` läste `db.history` och föll tillbaka på
`db.media_history` **bara om history var tom**. De 97 personliga
Trakt/AniList-betygen ligger i `media_history`.

### RO-3 — `seed_similarity` var okapad TMDb-popularitet, inte likhet

`candidate_score = popularity/10 + vote_average`, ×0,35 rakt in i totalen utan
tak. Komponenten `popularity` var kapad till 2,5 — men samma popularitet
återinfördes okapad och tio gånger större via `seed_similarity`.

Uppmätt topp-12 före åtgärd: The Late Show with Stephen Colbert (`seed=26,95`),
Watch What Happens Live, The Tonight Show, Mushoku Tensei, James Corden,
Kamen Rider, **Tagesschau**, Seth Meyers, The Daily Show, Craig Ferguson,
David Letterman, Jujutsu Kaisen.

### RO-4 — Engagemangsdjup kastades bort

`unique_taste_docs` slog ihop 10 210 rader → 625 genom att behålla *första*
raden per titel. 729 Simpsons-avsnitt vägde lika mycket som ett provtittat
avsnitt. `watch_count` fanns på 60 av 10 210 rader.

### RO-5 — Motiveringarna var påhittade i efterhand

Utan genreträff föll `why` tillbaka på profilens två vanligaste genrer. En
dokumentär fick texten *"Take That matches Action, Adventure in your normalized
taste profile"*.

### RO-6 — Nollröstad sörja i "upcoming"-jobben

`tmdb_discover` släpper `vote_count.gte` så snart `min_year ≥ 2025`. Straffet
för framtida titlar var −3, som erabonusen +1,2 nästan åt upp. Faktiska
träffar: "Buldok z Poděbrad" (1 röst, betyg 3,0), "Árru" (0 röster),
"Get Lite" (0 röster), "The 50", "Putin's secret weapons".

### RO-7 — Ingen likhet mot omtyckta titlar

Enbart genrenamn mot de åtta mest generiska genrerna.

### RO-8 — Ollama-omrankningen var i praktiken död

`ai_reranked: false` på **varje** körning i `job_runs`. Tre orsaker:
profiltexten var 183 tecken generiska genrenamn utan en enda titel; `num_ctx`
sattes aldrig; kandidat-id:n var långa slugar
(`pokémon horizons the series:2023`) som modellen gav upp på efter det första.

### RO-9 — Fel modell var konfigurerad, och osynligt

`connections.ollama_model` stod på `qwen3:14b`. Frontendens modellväljare
listar bara `qwen-suggestarr`, så värdet gick varken att se eller ändra i
appen — men det åsidosatte serverstandarden på varje anrop.

### RO-10 — Listan visades baklänges

Raderna skrivs i rankordning med stigande `created_at`;
`/api/recommendations` sorterade `created_at` **fallande**. Det sämsta
förslaget blev hjältekort på Home.

### RO-11 — Animefilmer fanns inte

Ingen kandidatkälla producerade en enda. Facket `anime_movie` var tomt.

### Vad som INTE var fel

- **Inga embeddings/vektorsökning fanns** i kodbasen — inget att granska.
- **`provider_cache`** (6 h) cachar råa leverantörssvar, inte rankningar.
  Var alltså inte en orsak till inaktuella rekommendationer.
- **Dataintegriteten** var i stort ren: noll föräldralösa kanoniska id:n, noll
  historikrader utan `canonical_media_id`, noll trasiga betygsskalor.
  En (1) dubblerad identitet hittades och slogs ihop.

---

## 2. Pipeline före → efter

**Före**
```
historik → leverantörsnormaliserad profil (tom)
        → TMDb discover på ren popularitet
          + tmdb_related seedad av de 8 FÖRSTA raderna i databasen
        → filter → poäng dominerad av popularitet
        → (LLM-omrankning som alltid föll igenom)
        → visning i OMVÄND ordning
```

**Efter**
```
historik + personliga betyg
  → sammanslagna per titel MED avsnittsdjup
  → strukturerad profil (affinitet, evidensantal, tillit) per
    genre / genrekombination / språk / era / studio / tagg / format
  → kandidatgenerering seedad från de högst värderade titlarna
    + genrekombinations-lanes + animefilm-lane
    + format användaren aldrig tittar på bortfiltrerade VID KÄLLAN
  → hårda uteslutningar på stabila id:n
  → deterministisk poäng, alla komponenter normaliserade till −1..1
  → topp-12
  → Ollama omrankar (korta handtag, schemastyrd JSON)
  → mångfaldspass inom relevansgolvet
  → visning BÄST FÖRST
```

---

## 3. Mätning

Etiketter: endast normaliserade Trakt/AniList-betyg ≥ 8 eller uttryckliga
gilla-markeringar. **"Sedd" räknas aldrig som "gillad."** Bortvalda titlar
utesluts ur träningsdata. Samma frysta ögonblicksbild i alla armar.

### Slumpmässig holdout, 8 veck

| arm | P@5 | P@10 | R@20 | NDCG@5 | NDCG@10 | parvis |
|---|---|---|---|---|---|---|
| **FÖRE (v1)** | 0,000 | 0,000 | 0,000 | 0,000 | 0,000 | 0,748 |
| **EFTER (v2)** | **0,425** | **0,288** | **0,330** | **0,494** | **0,374** | **0,795** |
| kontroll: ingen profil | 0,000 | 0,000 | 0,010 | 0,000 | 0,000 | 0,644 |
| kontroll: fel profil | 0,050 | 0,038 | 0,052 | 0,069 | 0,053 | 0,747 |

### Kronologiskt test (profil t.o.m. datum X, utvärderad på vad som gillades efter X)

| arm | P@5 | P@10 | NDCG@5 | NDCG@10 | parvis |
|---|---|---|---|---|---|
| FÖRE | 0,000 | 0,000 | 0,000 | 0,000 | 0,779 |
| **EFTER** | **0,800** | **0,500** | **0,869** | **0,642** | **0,804** |
| kontroll: ingen profil | 0,000 | 0,000 | 0,000 | 0,000 | 0,587 |
| kontroll: fel profil | 0,000 | 0,100 | 0,000 | 0,064 | 0,757 |

### Varför siffrorna inte är riggade

- **Ingen profil alls**, allt annat identiskt → 0,000. Vinsten är alltså inte
  en artefakt av hur kandidatpoolen är formad.
- **Medvetet fel profil** (byggd av titlar Gilbert sett men *inte* betygsatt
  högt) → 0,050.
- **Parvis träffsäkerhet 0,795**: ställd mellan en undanhållen 9–10/10-titel
  och en titel han sett men aldrig betygsatt högt — samma användare, samma
  genrer, samma era — sätter rankaren rätt titel först i 80 % av fallen.
  Slump = 0,50, tom profil = 0,64.
- Bortvalda positiva titlar injiceras med `source_confidence` pinnad till
  **golvet** (0,5) så en vinst aldrig kan komma av etikettens ursprung.

### Läckage och hygien

`watched_leakage_rate`, `duplicate_rate`, `invalid_candidate_rate` = **0,000**
i samtliga armar.

---

## 4. OLLAMA-MODELLRAPPORT

### Den obligatoriska frågan: är modellen en orsak till de dåliga resultaten?

**Delvis ja — men inte den modell man först tror, och inte av det skäl man
först antar.** Modellen som *kördes* (`qwen3:14b`) var den sämsta av sju
testade. Modellen som var *avsedd* (`qwen-suggestarr`) var den bästa
realistiska. Arkitekturen var flaskhalsen, inte modellvalet i sig.

### Metod

Alla modeller fick identisk smakprofil, identisk kandidatlista, identiska
instruktioner och identiska inferensparametrar. Sanningen är undanhållna
positiva titlar. Två kontrollarmar. 9 veck × 2 upprepningar.
Harness: `backend/evaluation/model_bench.py`.

| modell | NDCG@10 | NDCG@5 | P@5 | MRR | täckning | stabilitet | latens |
|---|---|---|---|---|---|---|---|
| enbart deterministisk | 0,396 | 0,140 | 0,120 | 0,321 | – | – | 0 s |
| slumpordning | 0,511 | 0,257 | 0,270 | 0,396 | 1,00 | 0,10 | 0 s |
| **qwen-suggestarr (VALD)** | **0,645** | **0,585** | **0,500** | **0,950** | 0,55 | 0,93 | 0,8 s |
| qwen2.5:7b-instruct-q6_K | 0,645 | 0,585 | 0,500 | 0,950 | 0,53 | 1,00 | 0,7 s |
| qwen3:14b *(var konfigurerad)* | 0,539 | 0,377 | 0,350 | 0,614 | 0,81 | 0,95 | 3,9 s |
| qwen3:8b | 0,369 | 0,311 | 0,200 | 0,861 | 0,78 | – | 2,7 s |
| llama3.1:8b | 0,310 | 0,255 | 0,200 | 0,607 | 0,74 | – | 20,3 s |
| qwen3.5:0.8b | 0,355 | 0,171 | 0,167 | 0,413 | 0,97 | – | 16,6 s |

### Beslut

- **Vald: `qwen-suggestarr`** (= `qwen2.5:7b-instruct-q6_K`, Q6_K, 7,6 B,
  6,9 GB VRAM, kontext 32 768, Modelfile sätter `num_ctx 16384`).
  Vann, var redan serverstandard, krävde ingen ny installation.
- **`qwen3:14b` avfördes**: 5× långsammare och NDCG@5 0,377 mot 0,585.
  Kontots värde bytt till vinnaren; gamla värdet bevarat i
  `connections.ollama_model_previous`. `qwen3:14b` tillagd i
  `LEGACY_OLLAMA_MODELS` så ett konto inte kan låsas fast vid det igen.
- **Basmodellen `qwen2.5:7b-instruct-q6_K` valdes bort**: mäter identiskt med
  `qwen-suggestarr`. Ett byte utan uppmätt vinst vore just det omotiverade
  modellbytet uppdraget förbjöd.
- **LLM-lagret behålls påslaget**: NDCG@5 0,585 med omrankning mot 0,140 utan.

### Inferensinställningar (nya, i `providers/ollama.py`)

```
temperature 0, top_p 1, top_k 1, seed 11, repeat_penalty 1.0,
num_ctx 8192, num_predict 1024
+ JSON-schema med minItems/maxItems (format-fältet)
```
Tidigare sattes **bara** `temperature`. `num_ctx` var osatt.

### Kandidatpool till modellen: 12 (uppmätt)

| poolstorlek | NDCG@5 | P@5 | hallucinerade id:n |
|---|---|---|---|
| **12** | **0,572** | **0,520** | **0,00** |
| 16 | 0,332 | 0,280 | 0,00 |
| 20 | 0,484 | 0,400 | 0,00 |
| 24 | 0,514 | 0,440 | 0,10 |

### Stabilitet

0,93–1,00 — identisk indata ger identisk ordning. Hela pipelinen kördes två
gånger i rad mot riktiga data och gav exakt samma åtta titlar.

---

## 5. Poängkomponenter och vikter

Alla komponenter normaliseras till −1..1 **före** viktning
(`backend/recommendation/ranking_engine.py`, `DEFAULT_WEIGHTS`).

| komponent | vikt | vad den mäter |
|---|---|---|
| `liked_title_similarity` | 3,0 | bästa likhet mot en faktiskt högt betygsatt titel |
| `taste_similarity` | 2,6 | genrer + genrekombinationer, tillitsviktat |
| `negative_affinity` | 2,6 | marginal över positiv likhet mot avvisat material |
| `quality` | 1,3 | bayesiansk betygsutjämning (prior 200 röster @ 6,4) |
| `thin_evidence` | 1,2 | släppt titel som nästan ingen röstat på |
| `recent_interest` | 1,0 | senaste 240 dagarnas genrer, separat från livstidssmak |
| `keyword_affinity` | 0,8 | synopsisvokabulär delad med favoriterna |
| `media_type_fit` | 0,8 | film / serie / anime / animefilm som egna fack |
| `language_fit` | 0,6 | språkaffinitet |
| `metadata_confidence` | 0,5 | hur komplett metadatan är |
| `source_confidence` | 0,4 | hur tillförlitlig kandidatkällan är |
| `era_fit` | 0,4 | decennieaffinitet |
| `popularity` | **0,35** | log-skalad, medvetet minst av alla |

De två likhetstermerna kan tillsammans bidra 5,6; popularitet högst 0,35.

**Källtillit** (`SOURCE_CONFIDENCE`): `tmdb_recommendations` 1,0 ·
`tmdb_similar` 0,95 · `anilist` 0,9 · `trakt` 0,85 · `simkl` 0,8 ·
`taste_seeded_discover` 0,8 · `anilist_upcoming` 0,7 · `tmdb_discover` 0,55 ·
`seed_expand` 0,5 · `heldout_challenge` 0,5 (golvet, av mäthygien).

**`match_score`** är nu en fast logistisk kalibrering av totalen, inte en
min/max-skalning över körningens egen spridning — därför visade åtta lika
svaga förslag tidigare alla 77 %.

---

## 6. Vad som testades och FÖRKASTADES

**Centrerad synopsisöverlappning** (låta uteblivet ordöverlapp räknas *emot*
en titel i stället för att bara inte räknas för den). Tanken var att skilja
sketch-/talkshow från skriven komedi, eftersom TMDb taggar båda enbart
"Comedy".

Uppmätt: holdout P@5 **1,000 → 0,800**, NDCG@10 **0,929 → 0,693**.
Orsak: titlar med kort eller saknad synopsis är inte dåliga matchningar utan
*okända*, och `metadata_confidence` prissätter redan det.

**Borttagen igen.** Kommentar och siffror ligger kvar i
`similarity.py::keyword_affinity` så nästa agent inte återupptäcker den.

---

## 7. Riktiga Content to Watch — genom appens HTTP-API, efter omstart

`POST /api/recommendations/generate`
→ `{"count":8,"provider":"ollama","model":"qwen-suggestarr","warnings":[]}`

| # | titel | år | format | match | källa |
|---|---|---|---|---|---|
| 1 | Hunter x Hunter (2011) | 2011 | anime | 89 % | anilist |
| 2 | Demon Slayer — the Movie | 2020 | **animefilm** | 85 % | tmdb_discover |
| 3 | Jujutsu Kaisen 0 | 2021 | **animefilm** | 85 % | tmdb_discover |
| 4 | Spider-Man: Brand New Day | **2026** | film | 84 % | tmdb_discover |
| 5 | So I'm a Spider, So What? | 2021 | anime | 84 % | anilist |
| 6 | The Death of Robin Hood | **2026** | film | 89 % | tmdb_discover |
| 7 | Hawaii Five-0 | 2010 | tv | 85 % | tmdb_similar |
| 8 | Reacher | 2022 | tv | 85 % | tmdb_discover |

Alla fyra format täckta, två kommande 2026-titlar, inga dubbletter, inget
redan sett, inga påhittade titlar. Verifierat visuellt: hjältekortet på Home
visar rank 1 och Vision UI är orört (`check_vision_ui_lock.py` → OK).

Exempel på verklig motivering:
> *"Hunter x Hunter (2011) matches Action, Adventure, Fantasy, a combination
> you keep going back to; plays like The Beginning After the End, which you
> rated highly; lines up with what you have been watching lately."*

Texten läses ur de komponenter som faktiskt avgjorde placeringen. Fältet
`similar_to` visar jämförelsetitlarna med poäng.

---

## 8. Ändrade filer

| fil | vad |
|---|---|
| `backend/recommendation/taste_engine.py` | omskriven — strukturerad profil, avsnittsdjup, genrekombinationer, `anime_movie`, seeds |
| `backend/recommendation/similarity.py` | **ny** — likhet mot faktiskt omtyckta titlar |
| `backend/recommendation/ranking_engine.py` | omskriven — normaliserade komponenter, bayesiansk kvalitet, ärliga motiveringar, mångfaldspass |
| `backend/recommendation/llm_context.py` | profiltext med riktiga titlar och evidens |
| `backend/recommendation/pipeline.py` | tar emot personliga betyg och färdig profil |
| `backend/recommendation/candidate_engine.py` | seed-expansion på affinitet i stället för genreräkning |
| `backend/providers/tmdb.py` | seeds från toppbetyg, genrekombinations-lanes, animefilm-lane, formatfilter |
| `backend/providers/ollama.py` | inferensparametrar + JSON-schema |
| `backend/llm.py` | skickar vidare options och schema |
| `backend/jobs/engine.py` | läser `media_history`, profil före kandidater, korta handtag i omrankning, `rank` |
| `backend/server.py` | `by_rank` — bäst först |
| `backend/config.py` | `qwen3:14b` pensionerad |
| `backend/evaluation/` | **ny** — `snapshot.py`, `offline.py`, `model_bench.py` |
| `backend/evaluation_tests/`, `backend/recommendation_tests/` | **nya** regressionstester |
| `frontend/src/components/ProvenanceChips.jsx` | chips mot nya komponentnamn (samma `.chip`-klasser) |

Diffstat: 1 321 tillagda, 377 borttagna rader över 13 spårade filer.

### Data

**Inget raderat.** En dubblerad identitet ("Bad Boys" 1995, tmdb 9737) slogs
ihop med appens egen `apply_identity_mapping`. Historik, betyg, jobb och kö
orörda. `connections.ollama_model` ändrad, gammalt värde bevarat i
`ollama_model_previous`.

### Tester

142 passerar. 9 fel + 5 errors — **samtliga fanns före detta arbete** (de
anropar Simkl/TMDb live). Mängden fel är en delmängd av utgångsläget:
**noll nya regressioner**, två tidigare fel åtgärdade.

---

## 9. Kvarstående begränsningar

1. **6 723 obehandlade `pending_approval`-förfrågningar** utesluter sina
   titlar ur varje ny rekommendation — cirka 19 % av kandidatpoolen.
   Kön rördes inte; det är användarens data och beslut.
2. **Inga TMDb-nyckelord, skådespelare eller regissörer hämtas.** Därför finns
   ingen regissörs-, skådespelar- eller franchiseaffinitet. Profilen bygger på
   genrer, genrekombinationer, språk, era, synopsistext samt AniList-taggar och
   studior för de 44 titlar som har dem. **Detta är den största kvarvarande
   uppsidan.**
3. **Inga embeddings.** Den lexikala varianten mättes och förkastades (se §6).
4. **Sketch-/talkshowproblemet är löst vid källan, inte i rankningen.** TMDb
   taggar Saturday Night Live enbart "Comedy". Talk/News/Soap utesluts i
   discover, men **bara** för tittare vars egen historik saknar intresse för
   dem — Gilberts *Reality*-affinitet är positiv (Real Housewives 10/10), så
   Reality behålls.
5. **AniList-kandidater saknar röstantal** och undantas från
   tunn-evidens-straffet. Kan inte kvalitetsgranskas som TMDb-titlar.
6. **Endast 5 negativa exempel finns i datat.** Negativhanteringen är
   implementerad och testad men tunt underbyggd. Fler avvisningar i appen gör
   den mätbart bättre.
7. **Offlineutvärderingen vilar på 97 betyg.** Tillräckligt för 8 veck, men
   konfidensintervallen är breda; enskilda veck varierar.
8. **AniList bidrar inte med animefilmer** — den lanen kommer bara från TMDb.

---

## 10. Så kör du om mätningen

```bash
cd backend

# 1. Frys en ögonblicksbild av riktiga data + live kandidatpool
PYTHONPATH=../.runtime/python:. python3 -m evaluation.snapshot \
  --user user_c30bd548254a --output /tmp/snap.json

# 2. Offline holdout + kronologisk utvärdering
PYTHONPATH=../.runtime/python:. python3 -m evaluation.offline \
  --snapshot /tmp/snap.json --folds 8 --output /tmp/eval.json

# 3. Kontrollarmar — MÅSTE kollapsa, annars läcker etiketter
PYTHONPATH=../.runtime/python:. python3 -m evaluation.offline \
  --snapshot /tmp/snap.json --folds 8 --control empty_taste
PYTHONPATH=../.runtime/python:. python3 -m evaluation.offline \
  --snapshot /tmp/snap.json --folds 8 --control shuffled_taste

# 4. Modelljämförelse
PYTHONPATH=../.runtime/python:. python3 -m evaluation.model_bench \
  --snapshot /tmp/snap.json \
  --models __deterministic__ __shuffled__ qwen-suggestarr \
  --folds 9 --repeats 2 --pool-size 12
```

Tester kräver `mongosh` på PATH:

```bash
export PATH="$PWD/../.runtime/mongosh/bin:$PATH"
PYTHONPATH=../.runtime/python:. python3 -m pytest \
  tests recommendation_tests evaluation_tests -q
```

Efter backend- eller frontendändring: `bash scripts/sync_runtime.sh`
(bygg frontend först med `cd frontend && npm run build`).
