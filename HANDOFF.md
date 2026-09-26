# HANDOFF — rekommendationsmotorn

Djupgranskning, rotorsaksanalys, åtgärd och mätning av hela
rekommendationskedjan. Utfört 2026-09-22 på gren `phase-0-local-runtime`.

Allt nedan är uppmätt mot Gilberts riktiga data (`user_c30bd548254a`,
10 210 historikrader, 669 `media_history`, 97 egna betyg ≥ 8), inte härlett.

> **Omgång 2 (samma kväll) ligger i avsnitt 11–16 längst ned.** Den utgick från
> faktiskt dåliga rekommendationer i den körande appen och hittade sex orsaker
> till som omgång 1 inte nådde. Läs 11–16 innan du ändrar något här.

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
(bygg frontend först med `cd frontend && npm run build` — Node ligger i
`.runtime/node/bin`, det finns ingen system-Node på maskinen, så lägg den på
PATH först eller kör `scripts/dev_frontend.sh`).

---

# OMGÅNG 2 — från faktiskt dåliga rekommendationer i den körande appen

Omgång 1 mätte mot en fryst kandidatpool hämtad från sida 1. Den körande appen
gjorde något annat. Utgångspunkten här var tre schemalagda körningar samma kväll
och vad de faktiskt producerade.

## 11. Vad appen faktiskt levererade

| körning | jobb | kandidater → valda | topplista |
|---|---|---|---|
| 16:00 | Upcoming Tv Shows | 478 → 12 | Avatar Aang, Spider-Man: Brand New Day, The Mandalorian and Grogu |
| 16:06 | Upcoming US + Anime 2026–2029 | 1 074 → 200 | **Wall Wizards, The Ferry Man, Seyon, Clay, Oh! My Guard, Teslina pošiljka** |
| 16:12 | Tv | 1 091 → 250 | **Boundless, The Iron Heart, Truth Seekers, Hanno ucciso l'Uomo Ragno** |

De sparade poängkomponenterna för de dåliga träffarna:

| komponent | Boundless (es) | The Iron Heart (tl) |
|---|---|---|
| `taste_similarity` | 1,573 | **1,573** |
| `liked_title_similarity` | 0,8274 | **0,8274** |
| `similar_to` | Daredevil 0,2758 · Winter King 0,2728 · Arrow 0,2659 | **identisk** |
| `language_fit` | **0,0** | **0,0** |
| `keyword_affinity` | 0,0 | 0,0 |

En spansk thriller och en tagalogspråkig såpa fick **bitidentiska** likhetstal
och samma jämförelsetitlar. Det är inte en olycklig slump — det är beviset.

## 12. Rotorsakerna

### RO-12 — Innehållssignalen var strukturellt tom

Uppmätt på den riktiga historiken:

- **0 av 633** titlar hade synopsis → `keyword_affinity` var alltid 0,0 och
  textöverlappet i `pair_similarity` alltid 0.
- **Språkprofilen hade 1 post**: `{'ja': 1.0}`. Den byggde på de 44
  AniList-raderna; de 576 TMDb-länkade raderna saknade språk. Engelska fanns
  alltså inte i profilen alls.
- `studios` och `tags` fanns bara på samma 44 rader.

Kvar som skiljetecken mellan två titlar: genrenamnen. Därför blev allt med
{Action, Drama} och betyg ≥ 7 likvärdigt, och svansen avgjorde ordningen.

### RO-13 — Discover-markören vandrade ut ur katalogen

`tmdb_page_cursor` stod på **191** för två av jobben. Markören flyttas
`span` sidor per körning (10 vid `candidate_limit` 300), var 30:e minut, med
enda tak `TMDB_MAX_PAGE = 500`. Sida 191 av en popularitetssorterad fråga är
ungefär den 3 800:e mest populära titeln. `already_recommended: true` gör det
till en återkopplingsslinga: varje körning måste hitta *nya* titlar, alltså
ännu djupare ner.

### RO-14 — Språk utan affinitet kostade ingenting

`language_fit` returnerade 0,0 både för ett språk användaren älskar och ett han
aldrig sett. Tagalog och engelska var omöjliga att skilja åt.

### RO-15 — Anime klassades som vanlig TV

`media_bucket` krävde genren "Animation". Trakt och AniList rapporterar
anime-genrer som "Action, Adventure, Comedy" — ingen "Animation" bland dem.
Följd: **113 av 204** japanska/koreanska/kinesiska titlar (Boruto, My Hero
Academia, That Time I Got Reincarnated as a Slime, Digimon) räknades som
dramaserier, och facket `anime_movie` var **helt tomt** trots Demon
Slayer-filmerna, Suzume och The Boy and the Heron i historiken.

Dessutom: `normalize_media_type(None)` returnerar `"movie"`, så varje anime-rad
*utan* separat `type`-fält klassades som animefilm.

### RO-16 — Identitetsnyckeln var scopad på mediatyp, och anime kolliderade

Trakt-historiken för *That Time I Got Reincarnated as a Slime* ger nyckeln
`("tmdb", 82684, "tv")`. TMDb-kandidaten för **samma serie** ger
`("tmdb", 82684, "anime")`, eftersom `_normalize_tmdb_result` sätter
`media_type: anime` för tv + japanska + animation.

De två matchar inte. Sedd-filtret missade alltså titeln, och serien kom tillbaka
som färskt förslag till någon som redan sett den. Detta var **den enskilt
största felkällan** i mätningen — och den blev värre av RO-15, eftersom fler
kandidater då hamnade i "anime"-scopet.

### RO-17 — Två tysta trunkeringar i `load_pipeline_inputs`

- `db.history … to_list(10000)` mot 10 210 rader → de **210 senaste**
  tittarhändelserna föll bort ur varje körning. Det är precis de rader
  `recent_interest` byggs av.
- `db.requests … to_list(5000)` mot 9 041 rader → cirka **4 000 redan begärda
  titlar** uteslöts inte och kunde rekommenderas igen.

### RO-18 — Mångfaldspassets andra svep struntade i golvet

`apply_diversity` har ett relevansgolv, men påfyllnadsloopen som lägger tillbaka
det franchise- och formatkvoterna hoppat över kontrollerade det inte. Ett jobb
som bad om 250 titlar tömde därför hela kandidatpoolen i listan.

## 13. Åtgärder

| fil | åtgärd |
|---|---|
| `providers/tmdb_enrich.py` | **ny** — hämtar TMDb-detaljer (nyckelord, rollista, regissörer/skapare, bolag, nätverk, samling, språk, synopsis) och cachar dem permanent i `media_enrichment` |
| `recommendation/taste_engine.py` | nya profilfack `tmdb_keywords`, `people`, `companies`, `collections`; `stated_opinion`-gate; anime känns igen på format-nyckelord; `_finalize(per_item=True)` för format |
| `recommendation/similarity.py` | nyckelords-, person-, bolags- och samlingstermer i `pair_similarity`; `people_affinity`, `franchise_affinity`; `keyword_affinity` läser riktiga nyckelord |
| `recommendation/ranking_engine.py` | nya komponenter och vikter; `language_fit` går negativt för språk utan evidens; golvet gäller även påfyllnadsloopen; motiveringar namnger tema/regissör/skådespelare |
| `recommendation/media_identity.py` | `identity_scope` — anime hör till serie-namnrymden, animefilm till film |
| `providers/tmdb.py` | `TMDB_CURSOR_PAGES = 25`, markören cyklar inom kvalitetsfönstret |
| `jobs/engine.py` | berikar historik och kandidater; `HISTORY_CAP`; höjda tak för requests/library/recommendations; teman i omrankningsprompten |
| `recommendation/llm_context.py` | profiltexten fick teman, personer och bolag |
| `evaluation/snapshot.py` | fryser berikade rader så offline mäter samma sak som appen |

### Vad berikningen gav

| | före | efter |
|---|---|---|
| titlar med synopsis | 0 / 633 | 555 / 633 |
| språk i profilen | 1 (`ja`) | 12 (`en` 1,0 · `ja` 0,69) |
| nyckelord | 0 | 606 (efter gate; 2 441 ogatade) |
| personer | 0 | 748 |
| bolag / samlingar | 0 / 0 | 261 / 13 |
| anime / animefilm i historiken | 94 / **0** | 205 / 16 |

## 14. Mätning

Etiketter, kontrollarmar och holdout-regler är oförändrade från omgång 1.

### Före → efter, samma kandidatpool, 8 veck

| mått | FÖRE | EFTER | Δ |
|---|---|---|---|
| holdout P@5 | 0,550 | **0,825** | +0,275 |
| holdout NDCG@5 | 0,623 | **0,882** | +0,259 |
| holdout NDCG@10 | 0,449 | **0,642** | +0,193 |
| kronologisk P@5 | 0,200 | **1,000** | +0,800 |
| kronologisk NDCG@5 | 0,131 | **1,000** | +0,869 |
| `watched_leakage_rate` | 0,000 | 0,000 | — |

FÖRE = koden som omgång 1 lämnade. EFTER = allt i avsnitt 13.

**Var vinsten kommer ifrån — och en siffra som inte förbättrades.** Samma
slutkod, samma vikter, enda skillnaden är om historiken är berikad:

| | P@5 | NDCG@5 | NDCG@10 | parvis |
|---|---|---|---|---|
| omgång 1:s kod, oberikad profil | 0,550 | 0,623 | 0,449 | 0,793 |
| omgång 2:s kod, **oberikad** profil | **0,900** | **0,934** | **0,716** | 0,825 |
| omgång 2:s kod, berikad profil | 0,825 | 0,882 | 0,642 | 0,766 |

Merparten av vinsten kommer alltså från kodfixarna, framför allt identitetsfixen
(RO-16) — sedda titlar slutade läcka in i listan. **Berikningen mäter lägre än
ingen berikning alls på den här holdouten.** Det påståendet står oemotsagt tills
någon förklarar det.

Vad som *har* prövats:

- **Hypotes: animefacket splittras.** Motbevisad. Med anime-regeln avstängd men
  allt annat berikat blir siffrorna identiska (P@5 0,8250, NDCG@5 0,8815).
- **Inom den berikade profilen bär varje innehållskomponent sin vikt** (tabellen
  nedan): utan nyckelordstermen −0,045 NDCG@5, utan persontermen −0,023, utan
  alla tre −0,088. Signalen är alltså inte värdelös — den oberikade armen har
  något *annat* som gynnar den.
- **Hypotes: `language_fit` är en etikettproxy i den oberikade armen.** Tanken
  var att språkfacket där bara innehåller `{'ja': 1.0}`, så termen i praktiken
  frågar "är detta japanskt?" — och de undanhållna positiva är animetunga.
  **Också motbevisad.** Med termen avstängd i båda armarna:

  | arm | P@5 | NDCG@5 |
  |---|---|---|
  | oberikad | 0,900 → 0,800 | 0,934 → 0,860 |
  | berikad | 0,825 → 0,750 | 0,882 → 0,824 |

  Termen är värd ungefär lika mycket på båda hållen och gapet består.

**Öppen fråga.** Efter två motbevisade hypoteser står ungefär 0,05–0,075 P@5
oförklarat. Vad som *inte* är orsaken: animefacket, språktermen, etikettläckage
(kontrollarmarna kollapsar), sedd-läckage eller dubbletter (0,000 i båda).

Berikningen behölls trots detta, av tre skäl som står sig oavsett hur
holdouten faller ut: de dåliga förslagen användaren klagade på orsakades bevisat
av den saknade innehållssignalen (avsnitt 11), motiveringarna blir sanna i
stället för påhittade, och `anime_movie`-lanen existerar över huvud taget bara
med den. Om språkhypotesen faller bör valet omprövas.

### Kontrollarmar (måste kollapsa)

| arm | P@5 | NDCG@5 | parvis |
|---|---|---|---|
| riktig profil | **0,750** | **0,824** | 0,751 |
| tom profil | 0,000 | 0,000 | 0,571 |
| medvetet fel profil | 0,075 | 0,086 | 0,659 |

### Vad varje innehållskomponent är värd (8 veck, holdout)

| arm | P@5 | NDCG@5 | NDCG@10 |
|---|---|---|---|
| full innehållssignal | **0,750** | **0,824** | 0,597 |
| utan nyckelordstermen | 0,700 | 0,779 | 0,572 |
| utan persontermen | 0,725 | 0,801 | 0,594 |
| utan alla tre rankningstermerna | 0,650 | 0,736 | 0,513 |

Varje komponent bär sin vikt.

### Varför `media_type_fit` stannar på 2,2 och inte högre

Svepet fortsätter uppåt: 2,2 → 0,8815 · 3,0 → 0,8997 · 4,0 → 0,9161 (NDCG@5).
Det är inte ett skäl att gå högre. De undanhållna positiva titlarna är
animetunga, så en rankare som mest väljer *format* vinner på pappret utan att bli
bättre. Och seedvariansen är lika stor som steget: samma vikt 2,2 ger 0,8815 med
seed 0 och 0,8942 med seed 11. Samma lärdom som modellvalet i avsnitt 15 — en
vinst som inte överlever ett annat urval är ingen vinst. En formatdominerad
rankare skulle dessutom begrava spelfilm och animefilm, som uttryckligen ska
fungera.

### Ärlig anmärkning om `pairwise_accuracy`

Den sjönk, 0,808 → 0,751. Hårda negativa exempel är titlar Gilbert **sett men
inte betygsatt högt** — de delar hans nyckelord, personer och studior, så en
rikare innehållssignal lyfter dem också. Tre försök att motverka det mättes:
evidensgate (0,7395), betygsgate (0,7399) och borttagen språkterm (0,7524).
Ingen återställde nivån, och betygsgaten behölls eftersom den är rätt i sak
("sedd ≠ gillad"), inte för att den räddade siffran.

## 15. OLLAMA — omgång 2

Samma harness, men mot den **berikade** profilen (prompten är nu 3 200 tecken
med teman, personer och bolag i stället för bara genrenamn).

| modell | NDCG@5 | NDCG@10 | P@5 | MRR | täckning | stabilitet | latens | hallucinationer |
|---|---|---|---|---|---|---|---|---|
| enbart deterministisk | 0,310 | 0,503 | 0,22 | 0,738 | – | – | 0 s | 0,00 |
| slumpordning | 0,337 | 0,568 | 0,37 | 0,438 | 1,00 | 0,10 | 0 s | 0,00 |
| **qwen-suggestarr** | 0,481 | 0,593 | 0,42 | 0,833 | 0,55 | 0,967 | **1,4 s** | 0,00 |
| qwen3:14b | 0,485 | 0,607 | 0,47 | 0,692 | 0,78 | 0,850 | 4,3 s | 0,00 |
| qwen3:8b | 0,397 | 0,551 | 0,40 | 0,578 | 0,84 | 0,970 | 3,1 s | 0,00 |
| llama3.1:8b | 0,500 | 0,633 | 0,43 | 0,858 | 0,90 | 0,814 | 5,9 s | 0,00 |
| gemma3:12b *(ny)* | 0,493 | 0,652 | 0,44 | 0,789 | 0,99 | 1,000 | 8,1 s | 0,00 |
| qwen2.5:14b-instruct-q4_K_M *(ny)* | 0,620 | 0,717 | 0,54 | 0,933 | 0,59 | 1,000 | 3,5 s | 0,00 |

`qwen2.5:14b` såg ut att vinna stort — **men det höll inte.** En bekräftande
körning med 14 veck i stället för 9 vände resultatet:

| modell | NDCG@5 | NDCG@10 | P@5 | MRR | stabilitet | latens |
|---|---|---|---|---|---|---|
| **qwen-suggestarr** | **0,578** | **0,695** | 0,49 | **0,933** | **0,947** | **1,3 s** |
| qwen2.5:14b-instruct-q4_K_M | 0,530 | 0,675 | 0,49 | 0,802 | 0,871 | 4,5 s |

Skillnaden i den första körningen var alltså veckurval, inte modellkvalitet.

**Beslut: modellen behålls.** `qwen-suggestarr` är minst lika bra, mer stabil
och 3,5× snabbare. `gemma3:12b` och `qwen2.5:14b-instruct-q4_K_M` ligger kvar
installerade på Ollama-maskinen; ta bort dem med `ollama rm` om utrymmet behövs.

> **Lärdom för nästa agent:** kör alltid en bekräftande omgång med annat
> veckantal innan du byter modell. Nio veck över 97 betyg räcker inte för att
> skilja 0,05 NDCG från brus.

## 16. Verifierat i den riktiga appen

`POST /api/recommendations/generate`
→ `{"count":8,"provider":"ollama","model":"qwen-suggestarr","warnings":[]}`,
alla rader `ai_reranked: true`, rank 1–8, noll sedda, noll dubbletter.

Samma jobb som kl 16:06 producerade *Wall Wizards, Seyon, Clay, Oh! My Guard,
Teslina pošiljka* ger nu:

| # | titel | år | format |
|---|---|---|---|
| 1 | Monsters 103 Mercies Dragon Damnation | 2024 | anime |
| 2 | My Hero Academia: You're Next | 2024 | anime |
| 3 | JUJUTSU KAISEN: Hidden Inventory / Premature Death | 2025 | anime |
| 4 | Baki Hanma VS Kengan Ashura | 2024 | anime |
| 5 | Attack on Titan: THE LAST ATTACK | 2024 | anime |

Motiveringarna läses ur de komponenter som avgjorde placeringen:

> *"Beheneko plays like That Time I Got Reincarnated as a Slime, which you rated
> highly; is the anime format you watch most; matches Animation, Action,
> Comedy."*
> *"Black Lightning is the tv format you watch most; plays like Arrow, which you
> rated highly."*

## 17. Kvarstående efter omgång 2

1. **`ProvenanceChips.jsx` importeras ingenstans** — komponenten renderas aldrig,
   så poänguppdelningen syns inte i gränssnittet. Motiveringstexten visas
   däremot via `StreamingWhy` i `RecModal`. Att koppla in chipsen är en
   UI-ändring och kräver Gilberts godkännande.
2. **Content to Watch blir animetungt** (6 av 8). Det är ärligt — hans
   formataffinitet är anime 0,857 / tv 1,0 / film 0,309 per titel — men
   spelfilm syns sällan i topp 8. `bucket_share` i `apply_diversity` kapar vid
   60 %, men påfyllnadsloopen respekterar inte kvoten (bara golvet).
3. **9 446 obehandlade `pending_approval`-förfrågningar** utesluter sina titlar.
4. **Fem negativa exempel** i datat. Negativhanteringen är tunt underbyggd.
5. **Inga embeddings.** Lexikal likhet mättes och förkastades i omgång 1.
6. **AniList bidrar inte med animefilmer** — den lanen kommer bara från TMDb.

---

# OMGÅNG 3 — vanliga TV-jobb gav anime, kids och animation (2026-09-24)

Utgångspunkt: jobbet **TV + Fantasy / Sci-Fi / Action / Adventure** (inget språk,
ingen anime) gav i topp 20 **7** engelska live-action-serier; resten var japansk
anime, donghua och tecknat. Det sparade jobbet "Tv" levererade kl 16:05 69 titlar
vars topp 14 samtliga var animefilmer (Goblin Slayer, Slime, My Hero Academia,
Naruto, Bleach, One Piece …). Mätt med `evaluation/job_trace.py --spec`.

## 18. Rotorsaker

| # | var | vad |
|---|---|---|
| RO-19 | `filter_engine.apply_filters` | Mediatyp `tv` släpper igenom allt i anime-facket. Ingen del av kedjan efter formuläret visste vad jobbet bett om. |
| RO-20 | `tmdb_discover` | Live-action-lanen frågade efter världens populäraste TV i alla språk och format: 40 anime + 29 tecknat av 200. |
| RO-21 | `related_seeds` | Similar/recommendations seedades från profilens toppbetyg oavsett lane. De är anime, så 144 av 240 rader blev anime, donghua eller tecknat. |
| RO-22 | `fetch_linked_provider_candidates` | AniList (bara anime) frågades för TV-jobb: 40 av 520 råa kandidater. |
| RO-23 | `taste_seeded_discover` | Genrepar som "Action|Animation" gav anime även för jobb utan animation. |
| RO-24 | `apply_lane_balance` | Gav anime, donghua och animation en garanterad andel var, bara för att de fanns i poolen. Topp 20 efter poäng hade 10 engelska; efter balans 7. |
| RO-25 | `ranking_engine` | Inget i poängen gynnade det jobbet bad om. Profilen säger anime 0,857 / ja 0,69, så anime vann på smak. |
| RO-26 | `providers/trakt.py` | `/recommendations` svarar med nakna objekt; parsern läste bara `{"movie": …}`. Varje rad blev "Unknown" och kastades — **Trakt bidrog med noll** till alla jobb. |

Vad som *inte* var fel: `include_genres` är OR (pipe) i TMDb och i filtret;
TV-genre-id:n 10759/10765 är rätt; Animation läggs bara som krav i anime-lanerna.

## 19. Åtgärd — jobbets avsikt (`recommendation/job_intent.py`)

Sparade jobb får `job_intent: True` (`jobs.engine.with_job_intent`). Content to
Watch sätter `job_intent: False`; offline-harnessen och AI Search har den aldrig.
Deras beteende är därför oförändrat (offline-sammanfattningen är byte-identisk).

Avsikten läses ur jobbet: vilka **lanes** (live action / anime / donghua /
animation), vilka **språk** (jobbets, annars engelska), om **kids** är önskat.

- **Källa:** live-action-discover frågar `with_original_language=en` först och
  fyller bara på med andra språk om det inte räcker; `without_genres=16,10762`
  (Animation, Kids) när jobbet inte bett om dem. Rena anime/donghua-jobb hoppar
  över live-action-lanen. Similar/recommendations seedas från `lane_seed_docs`
  (samma evidensordning, 240 djupt) filtrerat på jobbets lane, mediatyp och
  språk. AniList hoppas över när ingen anime/donghua-lane önskas. Trakt och
  Simkl frågas bara efter de mediatyper jobbet kan använda. Trakt-parsern lagad.
- **Poäng:** två nya komponenter, båda −1..1, bara för jobb med avsikt:
  `job_fit` (vikt 2,5: +1 det jobbet bad om, −0,6 annat språk, −1 ja/zh/ko
  live action och lanes jobbet aldrig nämnt, −0,4 Family) och `job_genre_fit`
  (0,8). De står utanför `match_score` och utanför "why"-texten — de säger
  något om jobbet, inte om tittaren.
- **Urval:** `select_final` fyller i nivåer: PRIMARY (lane-balanserat, men bara
  mellan lanes jobbet nämnt) → andra språk → högst 10 % från lanes jobbet aldrig
  bad om. Ett blandat jobb (live action + anime/animation) ger live action minst
  60 % av platserna; alla lanes finns kvar.
- **LLM:** omrankningspoolen (topp 12) är nu jobbets PRIMARY-nivå, så modellen kan
  inte längre lyfta in anime i ett TV-jobb. Prompt och modell oförändrade.

## 20. Mätning (riktiga data, `job_trace --spec`, topp 20, efter LLM)

| jobb | före | efter |
|---|---|---|
| TV + F/SF/A/A — rå: engelsk live-action TV | 210 av 520 | **387 av 530** |
| — rå: anime + animation + donghua | 255 | **75** |
| — engelsk TV efter filter | 19 | **39–52** |
| — **topp 20 engelsk live-action TV** | **7** | **20** |
| — topp 20 anime / animation / donghua / kids | 7 / 3 / 3 / 2 | **0 / 0 / 0 / 0** |
| TV + F/SF/A/A, språk = en | 15 (5 tecknat) | **20** |
| TV utan genrer | 6 | **20** |
| TV Fantasy/Sci-Fi | 6 | **20** |
| TV Action/Adventure | 7 | **20** |
| Anime-jobb | 10 anime + 10 donghua | 10 + 10 |
| Animation-jobb | 8 anime / 6 donghua / 6 animation | 7 / 7 / 6 |
| Donghua-jobb | 20 donghua | 20 donghua |
| Filmer (F/SF/A/A) | 16 titlar, 12 engelska | **20 engelska live-action** |
| Upcoming TV+film 2026–29 | 1 titel | **20 engelska** |
| Sparat "Upcoming Tv Shows" (blandat) | 3 live action av 12 | **8 av 12**, alla lanes kvar |
| Sparat "Tv" | 2 (anime) | **78**, 70 engelsk live action |
| Content to Watch | — | **identisk topp 8** |

I den körande appen, schemalagd körning av jobbet "Tv" med den nya koden
(16:42 UTC): 53 titlar, 47 engelsk live action, topp 20 = 13 serier + 7 filmer,
0 kids. Samma jobb med gamla koden kl 16:05: 69 titlar, topp 14 animefilmer.

Offline (fryst ögonblicksbild 2026-09-24, 8 veck): sammanfattningen före och efter
är **identisk**. Kontrollarmar: `empty_taste` P@5 0,000, `shuffled_taste` 0,150.

## 21. Kvarstående

1. **Upcoming-lanen saknar kvalitetsgolv.** Med de populära kommande titlarna redan
   i kön (143 avvisade som redan begärda) fylls ett upcoming-jobb av nollröstade
   TMDb-poster ("6th Grade: 2026-2027", "Raw Weekly Recap") med match 90–98 %.
   Fanns före denna ändring (då gav jobbet 1 titel); nu syns det.
2. **Kön innehåller fortfarande ~1 700 anime-, donghua- och animationsförfrågningar** från jobbet
   "Tv":s tidigare körningar. Inget raderat — det är Gilberts beslut.
3. `job_genre_fit` skiljer lite på TMDb-TV, eftersom 10759/10765 alltid ger två
   genrer var.

---

# OMGÅNG 4 — "rekommendationerna följer inte min smak" (2026-09-24 kväll)

Utgångspunkt: Tv-jobbet levererade Three's Company (1977), The Lucy Show (1962),
RuPaul's Drag Race, Gold Rush och Who's the Boss? — alla med match 96–99 %. Allt
nedan är mätt mot Gilberts riktiga data (`user_c30bd548254a`) och hans riktiga
Trakt-, Simkl-, AniList- och Plex-konton (läsande anrop, inget skrivet).

## 22. Källorna — ansluten ≠ synkad ≠ använd

`python3 -m evaluation.taste_report --user <id> sources --live` (nytt verktyg).

| källa | token | senast synkad | i CineMind | hos källan (live) | används av motorn |
|---|---|---|---|---|---|
| Plex | lagrad, **avvisad (401)** | 2026-09-08 | 16 visningar / 11 serier, 0 egna betyg (3 = demo) | kan inte läsas | 11 titlar, 0 positiva |
| Trakt | OK | 2026-09-08 | **10 000** visningar, **97** betyg (+2 demo) | **13 490** visningar, **176** betyg | 453 titlar / 95 betyg; med live-överlagring **603 / 169**, 234 positiva |
| Simkl | lagrad, **client_id avvisad (412)** | 2026-09-08 | 150 rader = exakt 50 per lista, 53 plantowatch, 0 egna betyg (3 = demo) | kan inte läsas | 97 titlar, 30 positiva, 3 negativa (dropped) |
| AniList | OK | 2026-09-08 | 44 (38 CURRENT, 6 COMPLETED), 2 betyg | 557 (513 PLANNING), scoreFormat **POINT_3** | 44 titlar, 22 positiva, 0 negativa (var 2) |
| Requests | — | — | 55 godkända / 234 avvisade (259 senare) | — | **48 positiva / 259 negativa** (användes inte alls förut) |

## 23. Rotorsaker

| # | var | vad |
|---|---|---|
| RO-27 | synken 2026-09-08 | Trakt kapades vid 100×100 visningar; betyg kopplades bara till titlar i det fönstret. **74 av 176 betyg och 117 filmer + 57 serier saknas**: The Witcher, Titans, Legion, Merlin, LOTR, Harry Potter, MCU, Star Wars — hela den live-action-smak Tv-jobbet behöver. Sedd-filtret släppte därför in Charmed (1998) och Harry Potter-filmerna som "nya". |
| RO-28 | `server.py /history/sync` | Regredierad till demo-versionen: 50 Trakt-visningar med *community*-betyg som egna, Simkl `[:50]`, Plex `/library/all` som historik med audienceRating som eget betyg, delete-före-insert. Ett klick på Sync hade raderat 10 000 Trakt-visningar. |
| RO-29 | demo-hyllan | 8 demo-rader i `media_history` som "egna betyg" (Chernobyl 9,4 "på Simkl", Severance 8,7 "på Trakt", Parasite 8,5 "på Plex"). Två av dem var offline-etiketter. |
| RO-30 | AniList | POINT_3 (smileys): 3 = 😊 = 8,5/10 lagrades som 3/10. Profilens enda lågt betygsatta titlar var två gillade anime. |
| RO-31 | `merge_taste_docs` | En Simkl-rad "plantowatch" vann statusen: Family Guy (389 avsnitt, 10/10), Naruto Shippuden (501), Supernatural (328), Friends (229), Avatar TLA (62), RHOA → evidens 0. |
| RO-32 | `build_taste_snapshot` | taste_sources filtrerades *efter* sammanslagning, på alfabetiskt första leverantör: titlar på anilist+trakt försvann ur Tv-jobbet, trakt-titlar med en simkl-rad smög in i ett jobb utan Trakt. |
| RO-33 | taste | 234 avvisningar + 55 godkännanden i Requests användes bara som uteslutning. Nästa CSI kom direkt efter att förra avvisats. |
| RO-34 | taste | Genrer lagrades som varje källa stavade dem: Trakt "Science-Fiction" 0,645 mot TMDb "Sci-Fi" 0,026; TMDb TV 10765 blev "Sci-Fi" utan Fantasy (0,905). |
| RO-35 | `match_score` | Konstanta format- och språktermer gav +3,25 till varje engelsk serie → 96–99 % för allt. |
| RO-36 | `select_final` | Inget golv: ett jobb som bad om 250 tog varje överlevare. |
| RO-37 | kandidater | 10 fasta frön → samma 240 titlar varje körning, alla redan i kön (445 av 767 avvisade som redan begärda); rotation genom 1-visnings-titlar; TMDb `/similar` (metadata) gav Scarface (1932), Bullitt (1968), Tabu (1931). |
| RO-38 | LLM | Se §26: rå modellordning skadar sparade jobb och hjälper Content to Watch; den begränsade omrankningen (okänd agent 19:04) neutraliserade båda. |

## 24. Åtgärder

| fil | åtgärd |
|---|---|
| `recommendation/demo_seed.py` | **ny** — demo-hyllan + fingeravtryck; `is_demo_seed` |
| `recommendation/taste_engine.py` | per-rad `source_allowed`; demo/plantowatch hoppas över; DROPPED vinner statusen; `decision_docs` (Requests, sedd-avvisning ≠ ogillande); kanoniska genrer (`taste_genres`, utan expansion av TMDb-id:n); `liked_titles` (alla positiva, ≤400); `provider_usage`, `ignored_rows` |
| `recommendation/similarity.py` | mätta vikter oförändrade; `specific_link`/`best_specific` (konkret koppling), `explain_match`, självjämförelse bort; förkastade varianter dokumenterade |
| `recommendation/ranking_engine.py` | `personal_score` (PERSONAL_COMPONENTS), `specific_score`, `match_score` bara på personligt + tak utan konkret koppling; `seed_support` (vikt 0); epokstraff; `/similar` 0,95 → 0,6; begränsad omrankning parameteriserad (`RERANK_MAX_BOOST`); "why" namnger konkret koppling |
| `recommendation/pipeline.py` | smakgolv (`TASTE_FLOOR` 2,0, `SPECIFIC_FLOOR` 0,3, okänd epok 0,6) beslutas i `run_pipeline`; betygsatta titlar räknas som sedda |
| `recommendation/candidate_engine.py` | dubbletter behåller alla frön och källor |
| `recommendation/llm_context.py` | lane-först, Requests-beslut, `candidate_evidence` |
| `providers/live_history.py` | **ny** — läsande Trakt-/AniList-överlagring (cache 6 h), kopplad till kanoniska id |
| `providers/history_sync.py` | **ny** — komplett, icke-destruktiv synk med `--dry-run` |
| `providers/tmdb.py` | 24 frön per körning (6 ankare + rotation bland gillade), 20 rader per frö, `/similar` bara för ankarna, `taste_keyword_discover` |
| `jobs/engine.py` | överlagring, berikade beslut, varning `taste_source_excluded`, omrankningspool över golvet, `rerank_lines` |
| `server.py` | `/history/sync` via history_sync; demo aldrig för konto med ansluten källa |
| `evaluation/offline.py` | demo aldrig etikett; undanhållna titlar ur Requests per veck; profil per veck |
| `evaluation/job_offline.py` | **ny** — `lane_holdout` (jobbets egen pool) och `request_decisions` (AUC) |
| `evaluation/taste_report.py` | **ny** — sources / profile / explain / compare |
| `recommendation_tests/test_taste_signals.py` | **ny** — 8 regressionstester |

`evaluation/model_bench.py` är **orörd** (Gilbert: "ändra inte model benchmark").
Dess prompt-spegel visar därför den gamla kandidatraden; uppdatera den innan nästa
modellbyte mäts.

## 25. Mätning (5 seeds × 8 veck, medel ± SE, samma frysta data för båda motorerna)

`snap` = databasen som den är. `snap_live` = samma + live-överlagringen (komplett
Trakt, AniList på rätt skala). "Tv-pool" = `job_offline lane_holdout`.

| arm | bred holdout P@5 / NDCG@10 | Tv-pool P@5 / NDCG@5 / NDCG@10 | Requests AUC |
|---|---|---|---|
| gammal motor, snap | 0,235 / 0,262 | 0,095 / 0,095 / 0,124 | 0,747 |
| **ny motor, snap** | 0,220 / 0,235 | **0,220 / 0,269 / 0,296** | **0,873** |
| gammal motor, snap_live | 0,395 / 0,356 | 0,415 / 0,500 / 0,367 | 0,745 |
| **ny motor, snap_live** | 0,335 / 0,317 | **0,565 / 0,649 / 0,455** | **0,885** |
| kontroll tom profil | 0,050 / 0,053 | 0,025 / 0,018 / 0,030 | 0,509 |
| kontroll fel profil | 0,095 / 0,085 | 0,035 / 0,036 / 0,043 | – |

**Ärligt:** bred holdout (Content to Watch-poolen, animetung) är 0,06 P@5 sämre
deterministiskt på komplett data. Orsaken är isolerad: kanoniska genrer (utan dem
0,383 / 0,357 men Tv-poolen 0,425 / 0,380 och AUC 0,800). Behållet medvetet.

Golvet (2,0 + koppling 0,3) behåller 97 % av undanhållna live-action-favoriter i
Tv-poolen, 88 % över alla format, **93 % av godkända Requests och 46 % av avvisade**.

Förkastat efter mätning (siffror i `similarity.py`): IDF i parvis likhet, lane-
begränsad jämförelse, kNN-blandning, nya likhetsvikter, synopsis-borttagning,
progress som visningsdjup, alla avvisningar som referenser, seed_support > 0.

## 26. LLM (gemma4:12b-it-qat, 16 veck, snap_live)

| pool / ordning | NDCG@5 | MRR |
|---|---|---|
| Tv: ny motor deterministisk | **0,655** | **0,969** |
| Tv: ny + begränsad LLM (sparade jobb) | 0,658 | 0,969 |
| Tv: ny + rå LLM | 0,498 | 0,721 |
| Tv: gammal motor + rå LLM | 0,592 | 0,927 |
| Bred: ny deterministisk | 0,328 | 0,483 |
| Bred: **ny + rå LLM (Content to Watch)** | 0,475 | **0,802** |
| Bred: gammal + rå LLM | **0,561** | 0,776 |
| Bred: ny + begränsad LLM | 0,342 | 0,470 |

Slutsats: sparade jobb omrankar begränsat, Content to Watch fritt inom golvet.

## 27. Verklig körning

Tv-jobbet med ny kod (förhandsvisning, riktiga data): 22 förslag i stället för 78
utfyllnad; 0 sedda (kontrollerat mot hela Trakt live), 0 i kön. Utan kö-uteslutning
blir topp 20 Legends of Tomorrow (Berlanti/Kreisberg/Guggenheim som Arrow 10/10),
Legion (som X-Men '97), Ahsoka (Dave Filoni som The Bad Batch), The Hobbit (Peter
Jackson som LOTR 10/10), Moon Knight, The Defenders, Krypton (David S. Goyer som
Foundation) … — **18 av 20 ligger redan obehandlade i kön**, 1 avvisad.

Nya jobbspecifikationer mot riktiga data (`taste_report explain --spec`, samma
kod som körs, inget skrivet), topp 20 kontrollerade mot hela Trakt-historiken
(live), CineMind-historiken och hela Requests-kön:

| jobb | osedda | inte i kön | konkret koppling | exempel |
|---|---|---|---|---|
| engelsk TV, alla fyra källor | 20/20 | 20/20 | 20/20 | The Witcher: Blood Origin (skapare som The Witcher 10/10), Colony (Ryan J. Condal som House of the Dragon 10/10), Black Lightning (Berlanti som Arrow) |
| film F/SF/A/A | 20/20 | 20/20 | 20/20 | Man of Steel (Snyder/Cavill som BvS 10/10), Ant-Man and the Wasp (Reed/Rudd som Ant-Man 10/10), Kong: Skull Island (som Monarch 10/10) |
| anime | 20/20 | 20/20 | 20/20 | Dead Mount Death Play (röster som Tower of God 10/10), One Piece Film Red, Castle in the Sky |

Vad som avgör ordningen inom jobbets egen lane (korrelation med slutpoängen /
spridning): personlig smak +0,88 / 0,93 (TV), +0,95 / 1,33 (film), +0,93 / 1,10
(anime); popularitet 0,04 i spridning i alla tre — bara skiljeregel.

Det sparade "Tv"-jobbets egen konfiguration: min_rating 7 stoppar 175–526 kandidater
per körning, och det ber om 250 förslag var 30:e minut.

### Första schemalagda körningen med ny kod

"Tv" 21:42 UTC i den körande appen: 1 028 kandidater → **25 förslag** (gamla koden
samma kväll: 1, 1 och 0; kl 16:42: 53 utfyllnadstitlar à 96–99 %). 0 sedda (mot hela
Trakt live + CineMind), 0 som redan låg i kön, 20 engelsk live action + 5 tillåtna
utfyllnader, match 46–97 %, personlig poäng ≥ 2,06, konkret koppling ≥ 0,30.

### Incident efter första driftsättningen (rättad)

Första schemalagda körningen med ny kod, "Upcoming Tv Shows" 21:30 UTC, **misslyckades**:
`cannot encode object: {...} of type set`. `candidate_evidence` (omrankningsprompten)
cachade `similarity._features` – mängder – på de rankade raderna, som sedan sparades.
Drabbade varje jobb med LLM påslagen, även Content to Watch. Rättat 21:32 UTC:
`explain_match` cachar inte, `strip_private` före sparning och i `apply_rerank`,
regressionstest `test_rows_stay_storable_after_the_rerank_prompt_is_built`.
Förhandsvisningarna (`taste_report explain`) sparar aldrig och fångade det därför inte.

Integrationssviten (`tests/`, mot den körande appen): 3 fel som fanns redan mot
gamla koden före driftsättningen (trailer 404, postercache, LLM-användning); inga nya.

## 28. Kvarstående — Gilberts beslut

1. **Återanslut Simkl och Plex** (tokens avvisas).
2. **Kör en synk** med nya koden: +3 490 Trakt-visningar och +74 betyg in i databasen
   (överlagringen täcker det i minnet så länge). Torrkör först: `python3 -m providers.history_sync --user <id> --dry-run`.
3. **"Upcoming Tv Shows" har Trakt avslaget** som smakkälla (ger nu 6 animefilmer och
   varningen `taste_source_excluded`). "Tv" har AniList avslaget.
4. 8 demo-rader i `media_history` ignoreras men ligger kvar.
5. Tv-jobbet: 250 förslag var 30:e minut mot 5 328 obehandlade i kön — poolen är uttömd.
6. Bred holdout −0,06 P@5 (§25).

---

# OMGÅNG 5 — synk, Upcoming, kön och den breda holdouten (2026-09-25)

Gilberts fem punkter (synk och Trakt i Upcoming uttryckligen godkända). Allt
mätt mot `user_c30bd548254a`. Säkerhetskopia före första skrivning:
`.runtime/backups/2026-09-25-pre-sync/` (history, media_history, media_library,
feedback, recommendation_feedback, recommendations, requests, media_identities,
jobbets gamla dokument; `manifest.json` har antal och sha256).

## 29. Simkl och Plex — varför de avvisas

Läsande anrop, inga inloggningsuppgifter ändrade:

| källa | anrop | svar | slutsats |
|---|---|---|---|
| Simkl, sparat manuellt Client ID `1825536c…` | `/users/settings`, `/sync/activities` | **412 client_id_failed** "Your client_id is wrong" — exakt samma svar som ett påhittat id | appen finns inte längre hos Simkl |
| Simkl, serverns app `54101790…` (.env) | samma | **401 user_token_failed** | appen är giltig, token tillhör den döda appen |
| Simkl, publika `/movies/trending` | alla tre id | 200 | publika anrop godtar vilket id som helst — bevisar inget |
| Simkl `/oauth2/device` | påhittat id | 401 invalid_client | PIN-flödet kontrollerar id:t |
| Plex `/identity` | – | 200 | kräver ingen token |
| Plex `/`, `/library/sections`, `/status/sessions/history/all` | sparad token | **401** | |
| plex.tv `/api/v2/user`, `/api/v2/resources` | sparad token | **401** | token återkallad/utgången hos Plex själv |

Rättat i koden: `simkl_token_client_id` (samma app-id som utfärdade token används
överallt; lagras som `simkl_token_client_id` vid anslutning), PIN-start faller
tillbaka på serverns app när det manuella id:t avvisas (annars gick det inte ens att
återansluta), exakta felmeddelanden i synk och "Test" för både Simkl och Plex.

**Gilbert måste göra (kan inte göras åt honom — inloggning):**
- Simkl: Sources → Simkl → töm "Client ID (manual, optional)" → Save → Disconnect →
  Connect → skriv koden på simkl.com/pin.
- Plex: logga in på app.plex.tv som serverägare → öppna valfri film → ⋯ → Get Info →
  View XML → kopiera värdet efter `X-Plex-Token=` i adressen → Sources → Plex Media
  Server → "Plex token" → klistra in → Save → Test.
Kör sedan Sync (eller `providers.history_sync`) — båda slås samman utan dubbletter.

## 30. Komplett synk — körd 2026-09-25 06:45 UTC

`providers/history_sync.py` slår nu samman i stället för att radera och ersätta:
en lagrad visning känns igen på leverantörens eget id (`provider_play_id`: Trakts
history-id, AniList-media-id, Simkl-id, Plex ratingKey+viewedAt); rader från före
id:t matchas en gång på titel-id + tidpunkt som multimängd (Trakt stämplar en hel
säsong markerad som sedd med en och samma tid — upp till 728 rader per nyckel).
Unikt index `history_provider_play_unique` hindrar dubbletter i databasen själv.
Tomma värden skriver aldrig över, ett personligt betyg tas aldrig bort, ett ändrat
betyg behåller det gamla i `previous_rating`. Rader leverantören inte längre
returnerar behålls och räknas. `watch_count` = gånger sedd *igenom* (film: olika
dagar; serie: visningar per unikt avsnitt), inte antal avsnitt — annars hade varje
serie blivit en "omtittning".

| | hämtat | importerat | uppdaterat | oförändrat | överhoppat | misslyckat | behållet (ej hos källan) |
|---|---|---|---|---|---|---|---|
| Trakt-visningar | 13 483 | **3 483** | 10 000 (play-id + avsnitt) | 0 | 0 | 0 | 0 |
| Trakt-titlar (`media_history`) | 649 | 196 | 269 | 184 | – | 0 | 2 |
| Trakt-betyg | 167 | **74** | 0 | 93 | – | 0 | 2 |
| AniList-poster | 44 | 0 | 44 (tidsstämpel fylld) | 0 | 0 | 0 | 0 |
| AniList-betyg | 2 | 0 | **2** (3 → 8: POINT_3-smiley lagrad som 3/10; 3 sparat i `previous_rating`) | 0 | – | 0 | 0 |
| Simkl | – | – | – | – | – | **ej körd** (412, se §29) | 150 kvar |
| Plex | – | – | – | – | – | **ej körd** (401) | 16 kvar |

Omkörning direkt efter: 0 importerade, 0 uppdaterade, 13 483 + 44 oförändrade.
Kontroll mot säkerhetskopian: alla 10 210 gamla historikrader finns kvar (0 borttagna),
105 av 105 tidigare betyg finns kvar (103 identiska, 2 AniList-rättningar ovan),
`recommendation_feedback` 6/6 identiska, feedback-raderna i `media_history` orörda.
Trakt-historiken börjar nu 2020-01-29 i stället för 2022-02-18. AniList "557" i §22
var fel läst: kontot har 44 sedda + 447 PLANNING (planerat är inte historik).

Följdrättelser: `apply_user_ratings` matchar på typ (en film och en serie kan dela
TMDb-nummer), identitetssammanslagning ärver betyg i stället för att radera raden
med betyget (`media_identity.merge_personal_fields`).

## 31. "Upcoming Tv Shows" släppte igenom filmer — rotorsaker och rättning

Trakt avslaget förklarade *vilka* titlar som rankades högst, inte att filmer kom med.
Spårat med `evaluation.job_trace` (före → efter):

| # | var | vad |
|---|---|---|
| RO-39 | `filter_engine.apply_filters` | Allt med medietyp "anime" räknades som TV: ett TV-jobb släppte igenom varje animefilm, och japanska animerade filmer med typ "movie" slapp igenom som "anime-bucket". |
| RO-40 | `providers/tmdb.fetch_job_candidates` | Animefilmslanen (`/discover/movie`, märkt anime + MOVIE) kördes för varje jobb som bad om anime-genrer — även TV-jobb. |
| RO-41 | jobbets inställning | `media_types` var `["tv", "movie"]` (Movies-chipet förvalt när jobbet skapades). 293 av jobbets 636 köposter är filmer, 472 från 2024–2025. |
| RO-42 | kön ↔ uteslutning | Köraden sparade bara `type: "anime"` (scope serie) medan kandidaten för samma animefilm har format MOVIE (scope film): "redan begärd" missade, och samma 7 animefilmer kom tillbaka i 92 av jobbets 96 senaste förslag. |
| RO-43 | `tmdb_lookup` | Anime söktes alltid under `/search/tv`: animefilmer fick en series TMDb-id. |

Rättat: `filter_engine.media_type_allowed` (animefilm = film: bara Movies eller Anime
släpper in den); animefilmslanen bara när jobbet tar filmer (`tmdb.anime_films_wanted`);
AniList-kandidater behåller `format`; `tmdb.tmdb_kind` (animefilm söks under /movie);
köraderna sparar `canonical_media_id`, `media_type`, `format`, `anilist_id`;
`exclusion_engine.stored_keys` matchar en lagrad anime-rad utan format som både film
och serie. Jobbet: Trakt tillagt som smakkälla (godkänt), **Movies avmarkerat** (jobbet
heter TV och du beskrev det som TV-jobb; klicka Movies-chipet igen om du vill ha filmer).
Före-bilden av jobbet ligger i säkerhetskopian.

| steg (job_trace, samma kandidatsidor) | före | efter |
|---|---|---|
| kandidater före filter | 955 (bl.a. 224 live-action-filmer, 62 animerade filmer och animefilmslanens filmer, märkta "anime") | 900 (animefilmslanen hämtas inte) |
| efter filter + uteslutning | 36: 20 animation-TV, 11 anime (mest animefilmer), 3 donghua, 2 animerade filmer | 20: 20 animation-TV, **0 filmer** |
| med Movies tillfälligt påslaget igen | – | kövarande animefilmer nu uteslutna (anime "redan begärd" 81 → 89) |
| slutlista | 5 animefilmer | 1 serie |

**Datumfiltret:** med jobbets inställningar (2026–2029) avvisar dagens kod varje titel
utanför fönstret, från alla källor (`job_trace`: `rejected_year` 187 live action, 90
anime, 56 animation; titel utan år avvisas också). Men jobbets körningar 2026-09-24
10:00–21:00 UTC godkände 2024–2025-titlar (t.ex. The Storm 2024, Nobody 2025,
My Oni Girl 2024 — premiärdatum i köraden 2024–2025). Ingen kodväg som kringgår
årsfiltret har hittats; troligast hade jobbet ett annat fönster då, men det går inte
att bevisa: varje körning skriver över jobbets `updated_at`, och körningarna sparade
inte inställningarna. **Nu gör de det** (`job_runs.settings`: medietyper,
smakkällor, filter; och år/typ/format per godkänd titel). Jobbets 472 köposter från
2024–2025 (och dess filmer) uppfyller inte dagens inställningar och ingår i
rensningsförslaget (§32, regeln "uppfyller inte längre jobbets inställningar").

Första schemalagda körningen med ny kod, 07:30 UTC: **11 förslag, 11 serier, 0 filmer**
(10 engelska/animerade serier 2026–2028 + 1 donghua-serie). Varför så få: allt som
matchar jobbet 2026–2029 ligger redan i kön — 61 live-action-serier, 65 anime, 48
donghua, 26 animerade avvisas som "redan begärda" före rankningen.

## 32. Kön — varför den växer, och vad som stoppar ansamlingen

Kön hade 10 129 väntande (inte 5 328 — det var Tv-jobbets andel igår): Tv 5 457,
det avstängda "Upcoming US+Anime" 4 122, Upcoming Tv Shows 550. 8 416 kom in en
enda dag (2026-09-22: Tv-jobbet tog 250 per körning i 19 körningar).

| # | orsak |
|---|---|
| RO-44 | Ingen utgång, inget tak: en post lämnar "väntande" bara när du agerar. |
| RO-45 | `submit()` matchade bara exakt titel + år: ett omdöpt/romaniserat namn blev en ny rad (13 verkliga dubbletter). |
| RO-46 | **Manuella beslut skyddades inte:** ett jobb som föreslog en avvisad titel igen vände den till väntande; 79 väntande rader bär `approved_at` — godkännanden som jobbkörningar tryckt tillbaka (senast 2026-09-24 16:42). Avvisningar lämnade inget spår. |
| RO-47 | Ett nytt förslag på en väntande titel skrev om `updated_at`, så gamla förslag hoppade upp överst som "nya". |
| RO-48 | Animefilmer känndes inte igen som redan köade (RO-42). |

Rättat (`request_providers.py`, `server.py`, `jobs/engine.py`, `exclusion_engine.py`):
en jobbkörning ändrar aldrig en rad som är godkänd, avvisad, avfärdad, arkiverad eller
har `rejected_at` (din egen "Request" får fortfarande överpröva en gammal avvisning);
titel hittas på id (canonical, AniList, TMDb i rätt namnrymd) innan en ny rad skapas;
ett nytt förslag på en väntande titel uppdaterar match/`last_suggested_at` men behåller
platsen och första jobbet; avvisningar sparar `rejected_at`; nya rader får `created_at`.

Köposter hanteras *före* sluturvalet (uteslutning före rankning i `run_pipeline`) —
kontrollerat på Tv-jobbet: utan kö-uteslutning är **20 av 20** bästa redan i kön;
med den (så som jobbet körs) **0 av 4** nya förslag. Tv-jobbet är mättat: 07:12-körningen
gav 4 nya titlar i stället för 250.

**Rensningsförslag — inte utfört, reversibelt** (`evaluation/queue_cleanup.py`):
`plan` skriver bara en manifestfil, `apply --batch` sätter `status: archived` +
`archived_from_status/_at/_reason/archive_batch` på exakt de raderna (bara orörda
väntande förslag; allt du rört är utanför), `revert --batch` återställer.
Arkiverade rader döljs i kön men ligger kvar som "redan begärda" (kommer inte tillbaka).

| regel | rader (dry-run 07:05 UTC) |
|---|---|
| dubblett av väntande (samma TMDb-id/namnrymd, år ±1; bästa behålls) | 13 |
| dubblett av godkänd/avvisad titel | 1 |
| redan sedd (hela synkade historiken) | 156 |
| uppfyller inte längre jobbets inställningar (t.ex. filmer i TV-jobbet) | 470 |
| **standardförslag totalt** | **640** → 9 491 kvar |
| + från avstängt jobb (valfritt) | + 4 078 → 5 413 kvar |
| + inte föreslagen på 14 dagar (valfritt) | + 271 |

Manifest: `.runtime/backups/queue-archive/arch_20260925_070533.json` (standard) och
`arch_20260925_070522.json` (alla regler). 30 av de 79 tillbakatryckta godkännandena
saknar leveransstatus och listas separat (`approvals_to_restore`) — att återställa
dem till godkända är ditt beslut (det skickar inget till MediaManager av sig självt,
men de hamnar på Approved-fliken).

## 33. Den breda holdouten: P@5 0,395 → 0,335 utredd

Samma låsta data (`snap_live.json`, 2026-09-24 19:28), samma kandidatpool, samma
mätkod för båda motorerna (`backend_before/evaluation/offline_v2.py` är en byte-kopia
av dagens `evaluation/offline.py`), 5 seeds × 8 veck. Benchmark, etiketter och
testurval är oförändrade.

**Var tappet sitter.** Per veck: 18 favoriter föll ur topp 5, 6 kom in (netto −12
av 200 platser). Tre titlar tar topp 5-platser i nästan varje veck i *båda* motorerna
(KonoSuba 40/40, Overlord 37/40, Chainsaw Man 36/40) — alla ligger obehandlade i din
kö, ingen är sedd eller betygsatt. Den nya motorn lägger dessutom två andra köade,
osedda titlar där: JoJo's Bizarre Adventure (11 veck) och Star Wars Rebels (7 mot 2).
JoJo steg från plats 12 till 4 av ett enda skäl: TMDb kallar genren "Sci-Fi", Trakt
"Science-Fiction". Den gamla motorn såg två olika genrer.

**Varför det var en genväg och inte smak.** Etiketterna är bara Trakt-/AniList-betyg
≥ 8, alltså rader med Trakts/AniLists stavning. Övriga kandidater och de "sedda men
inte högt betygsatta" titlarna (parjämförelsen) kommer till stor del från TMDb och
Simkl med stavningen "Action & Adventure", "Sci-Fi & Fantasy". Med rå stavning
betydde "stavad som Trakt" nästan "är en etikett". De sedda titlar som nu hamnar
över favoriterna är nästan alla Simkl-rader: Teen Titans, Fate/Zero, Bleach,
Smallville, Justice League (2001), X-Men TAS, Heroes …

**Kontrollexperiment:** samma låsta data med genrenamnen skrivna på *ett* sätt
(`snap_live_canon.json`, bara stavningen ändrad) genom båda motorerna:

| | bred P@5 | NDCG@10 | parjämförelse | Tv-pool P@5 / NDCG@5 | Requests AUC |
|---|---|---|---|---|---|
| gammal motor, originaldata | 0,395 ± 0,018 | 0,356 | 0,709 | 0,415 / 0,500 | 0,745 |
| **gammal motor, en stavning** | **0,355 ± 0,025** | 0,329 | **0,666** | 0,565 / 0,658 | 0,872 |
| ny motor (samma med båda) | 0,335 ± 0,027 | 0,317 | 0,665 | 0,565 / 0,649 | 0,885 |

Två tredjedelar av P@5-tappet (0,040 av 0,060) och hela parjämförelsetappet
(0,043 av 0,044) var stavningen. Resten (0,020) ligger inom felmarginalen.
Den gamla motorns Tv-pool-siffra hoppar till den nyas när stavningen är en —
samma fel dolde live-action-favoriter där.

## 34. Rankingen förbättrad — mätt, inte resonerat

Tre saker som stämmer med din prioritetsordning (personlig smak och betyg före
genre, format och popularitet) och som mättes var för sig (3 seeds) och sedan
tillsammans (5 seeds × 8 veck, samma låsta data, kontrollarmar):

1. **Avvisningar vägs på samma skala som dina betyg** (`ranking_engine.NEGATIVE_EVIDENCE_SCALE = "liked"`).
   Styrkan hos jämförelsetitlarna normerades inom varje lista: bland idel
   avvisningar (−1,5) blev varje avvisning "full styrka" — lika tung som ett 2/10 —
   medan gillade titlar vägs mot en 10/10-favorit. Favoriter du gett 8–10 fick i
   snitt −0,13 i straff för att de liknade något du avvisat i kön.
2. **Skapare och skådespelare** (`people_affinity`) 1,1 → 1,6.
3. **Likhet med titlar du gillat** (`liked_title_similarity`) 4,2 → 5,0.

Förkastat efter mätning: lägre vikt på genresmak (2,6 → 1,8: bred P@5 0,358 → 0,325),
fler jämförelsetitlar (24 → 48: kronologisk P@5 0,4 → 0,2), nyckelord 1,8,
franchise 1,0, people 2,0, avvisningar borttagna helt (bred P@5 0,358 → 0,333),
beslut borttagna helt (AUC 0,889 → 0,875).

Plus, utanför rankningspoängen: **ett nyckelord som bara upprepar en genre är ingen
konkret koppling** (`similarity.SPECIFIC_SKIPS_GENRE_WORDS`). "Sodor516 plays like
Amphibia (themes fantasy, comedy)" räckte för att passera smakgolvet. Golvet behåller
fortfarande 97 % av undanhållna favoriter i Tv-poolen, 86 % i alla format och 93 %
av godkända Requests — exakt som före.

Smakgolvet och match-% omkalibrerade till de nya vikterna (alla personliga poäng
steg): `pipeline.TASTE_FLOOR` och `ranking_engine.MATCH_CENTER` 2,0 → 2,5. Vid 2,5
behåller golvet 97 % av undanhållna favoriter i Tv-poolen och 37 % av resten (förut
97 / 38 %), 86 % i alla format (86 %), 93 % av godkända Requests mot 44 % av avvisade
(93 / 46 %). Match-%: undanhållna 9–10/10 median 96 %, pooltitlar utan konkret
koppling median 35 %.

### Mätning (5 seeds × 8 veck, `snap_live`, medel ± SE)

| arm | bred P@5 | NDCG@10 | par | Tv-pool P@5 / NDCG@5 / NDCG@10 | Requests AUC |
|---|---|---|---|---|---|
| gammal motor | 0,395 ± 0,018 | 0,356 | 0,709 | 0,415 / 0,500 / 0,367 | 0,745 |
| gammal motor, en stavning | 0,355 ± 0,025 | 0,329 | 0,666 | 0,565 / 0,658 / 0,455 | 0,872 |
| ny motor i morse | 0,335 ± 0,027 | 0,317 | 0,665 | 0,565 / 0,649 / 0,455 | 0,885 |
| **ny motor nu** | **0,370 ± 0,024** | **0,351** | 0,665 | **0,585 / 0,664 / 0,465** | 0,881 |
| kontroll tom profil | 0,050 | 0,053 | 0,573 | 0,025 / 0,018 / 0,030 | 0,509 |
| kontroll fel profil | 0,100 | 0,088 | 0,586 | 0,035 / 0,036 / 0,037 | – |

Parvis per veck (samma veck, samma process), nu − i morse: bred P@5 **+0,035 ± 0,012**
(t = 2,9), NDCG@5 +0,037 ± 0,009 (t = 4,0), NDCG@10 **+0,035 ± 0,006** (t = 5,8),
R@20 +0,006 (t = 1,4), par ±0; Tv-pool P@5 +0,020 ± 0,012 (t = 1,7), NDCG@10 +0,010 ±
0,004 (t = 2,3), R@20 +0,009 (t = 2,1); **Requests AUC −0,004 ± 0,001** (t = −3,2).

Nu − gammal motor, originaldata, samma 40 veck: P@5 **−0,025 ± 0,018 (t = −1,4)**,
P@10 −0,020 ± 0,016 (t = −1,2) — inte statistiskt skiljbart. Med en stavning är den
nya motorn bättre (0,370 mot 0,355). Resten av skillnaden på originaldatan är den
genväg §33 visar; en motor som ser "Sci-Fi" och "Science-Fiction" som samma genre kan
inte ta den.

## 35. LLM (gemma4:12b-it-qat, 16 veck, samma pooler)

| motor, data | Tv-pool NDCG@5 / MRR: det · begränsad · rå | bred NDCG@5 / MRR: det · begränsad · rå |
|---|---|---|
| gammal + gammal prompt, original (omg. 4) | 0,518/0,875 · 0,525/0,875 · 0,592/0,927 | 0,363/0,460 · 0,358/0,495 · 0,561/0,776 |
| gammal + gammal prompt, en stavning | 0,651/0,969 · 0,660/0,969 · 0,518/0,599 | 0,346/0,481 · 0,353/0,481 · 0,495/0,750 |
| ny i morse | 0,655/0,969 · 0,657/0,969 · 0,506/0,698 | 0,328/0,483 · 0,342/0,470 · 0,469/0,729 |
| **ny nu** | 0,655/0,969 · **0,664**/0,969 · 0,563/0,781 | **0,366/0,523 · 0,383/0,528 · 0,519**/0,736 |
| ny nu, en stavning | samma | 0,366/0,523 · 0,386/0,528 · 0,526/0,697 |

Sparade jobb använder "begränsad" (bäst i Tv-poolen), Content to Watch den fria
("rå") inom golvet. Den gamla motorns fria LLM-siffra 0,561 faller till 0,495 med en
stavning — samma genväg. Modellen och `model_bench.py` är orörda.

## 36. Verkliga körningar efter driftsättning (07:06 och 08:54 UTC)

| körning | resultat |
|---|---|
| Tv 07:12 | 4 nya (Aftershock, Unleashed, No One Saw Us Leave, Ozanari Dungeon), 0 redan köade |
| Tv 07:42 | 6 nya (Batman Returns — som The Dark Knight; Justice League: Throne of Atlantis — DC; Rearrange — som Re:ZERO 10/10; Coroner's Diary — Ao Ruipeng som Pull Strings 9/10; Молодёжка — ishockey som Heated Rivalry 9/10; We Were Liars) |
| Tv 08:12 | 3 nya |
| Upcoming 07:30 | 11 serier, 0 filmer |
| Upcoming 08:00, 09:00 | 0 — `no_picks`: allt som matchar ligger redan i kön |
| kön | 0 beslut ändrade av jobb, 0 avvisade vända, 0 godkännanden tillbakatryckta; dina 33 avvisningar och 3 godkännanden efter 07:06 står kvar, avvisningarna med `rejected_at`; 31 nya köposter, alla med `created_at` |

Acceptans med slutmotorn mot riktiga, nu kompletta data (`taste_report explain --spec`,
inget skrivet): engelsk TV, film, anime 20/20 osedda (mot hela synkade historiken),
20/20 inte i kön, rätt lane 20/20; Content to Watch 8/8. Popularitetens bidrag i
snitt 0,14, högst 0,21 av poäng runt 10 — bara skiljeregel. Exempel: Secret Invasion
(som The Avengers 10/10), The Witcher: Blood Origin (Lauren Schmidt som The Witcher
10/10), Justice League 2017 (Zack Snyder som Rebel Moon 10/10), Kong: Skull Island
(Monarch 10/10), Dead Mount Death Play (röster som Tower of God 10/10).

Tester: `recommendation_tests` 99/99 (nya: `test_history_sync.py` 8,
`test_media_type_filter.py` 4, `test_queue_decisions.py` 5, `test_simkl_connect.py` 3,
genre-ord-testet). Integrationssviten mot den körande appen: 133 gröna, samma 3 fel
som före alla ändringar (trailer 404, postercache, användningsräknare) + ett
samtidighetsfel ("Job is already running") som går grönt ensam.

## 37. Kvarstående — Gilberts beslut

1. **Simkl och Plex: logga in igen** (§29 — exakta steg). Kör sedan Sync.
2. **Kön:** `evaluation.queue_cleanup apply --batch arch_20260925_070533` arkiverar
   standardförslagets 640 rader (kör `plan` igen först — du har redan beslutat om
   ett 40-tal sedan manifestet skrevs; `apply` hoppar ändå över allt du rört). Valfritt:
   det avstängda jobbets 4 078. Att återställa de 30 tillbakatryckta godkännandena är
   ett separat beslut.
3. **Tv-jobbet** ber fortfarande om 250 förslag var 30:e minut mot en mättad pool;
   det ger nu 3–6 nya per körning. Ett lägre tak ändrar inget i kvaliteten men
   gör körningarna billigare — din inställning.
4. **Upcoming Tv Shows** är nu TV-only (Movies avmarkerat) med Trakt som smakkälla.
5. Bred holdout mot gamla motorn: −0,025 P@5, inte signifikant (§34).
6. `model_bench.py` är orörd; dess prompt-spegel är fortfarande den gamla.

---

# OMGÅNG 6 — molnleveransen integrerad lokalt, anslutningar, kön och Upcoming (2026-09-25 eftermiddag)

Gilberts uppdrag, i ordning: 1 säkra arbetsytan och databasen · 2 kontrollera Simkl/Plex och
åtgärda anslutningarna (befintliga Trakt/AniList skulle användas) · 3 integrera relevanta
rättningar från `claude/great-davinci-zlc97t` · 4 slutför och kör den godkända fullständiga
synken · 5 färdigställ Upcoming, åtgärda återkommande kandidater och fortsatt köansamling ·
6 testa, driftsätt, verifiera mot riktiga data. Kl 11:38 UTC bad Gilbert om att avsluta efter
Upcoming-logiken och anteckna allt här och i CLAUDE.md.

**Status: steg 1, 3 och 5 är gjorda i koden och enhetstestade. Inget är driftsatt, ingen synk
är körd och inget är live-verifierat** (steg 4 och 6). Steg 4 kräver dessutom tre inloggningar
som bara Gilbert kan göra (§40). Allt ligger ocommittat i arbetsytan på `phase-0-local-runtime`
(inget committat, inget pushat); säkerhetskopian före sessionen är `refs/backup/wip-2026-09-25-pre-integration`.

## 38. Molnets §19 jämfört med den lokala arbetsytan

Molngrenen (9 commits, bas `5c53e42`) skrevs utan åtkomst till Macen och utan den lokala,
ocommittade koden från omgång 3–5. Hela texten finns kvar på grenen:
`git show origin/claude/great-davinci-zlc97t:HANDOFF.md` (dess §18–19; numreringen krockar
med den lokala, därför är den inte inklistrad här).

Gilberts beslut enligt molnets §19.1 (gäller fortfarande): 1 full synk godkänd (hela historiken
och alla egna betyg, med paginering och deduplicering; rankingunderlaget får ändras) · 2 "Up
Coming" på Home visar faktiskt kommande premiärer med verifierade datum; "Upcoming Tv Shows"
ger bara serier enligt sina inställningar; en äldre serie får vara med om en kommande säsong
har ett verifierat premiärdatum; Vision UI behålls · 3 den personliga rankingen bevaras: Gemma
får inte lyfta svaga smakmatchningar förbi tydligt starkare; inga nya benchmarkomgångar · 4 kön
är mer än paginering: obehandlade förslag, återkommande kandidater och fortsatt ansamling;
manuella beslut bevaras · 5 lokala ändringar bevaras · 6 bara inloggningssteg till Gilbert.

Skillnader mot molnets bild:

| molnets §19 | lokalt läge |
|---|---|
| full synk saknas (§19.8 B) | fanns redan: `providers/history_sync.py`, körd 06:45 UTC för Trakt + AniList (§30). Kvar: Simkl, Plex och Trakt igen efter ny inloggning. |
| `submit` matchar bara titel + år (§19.8 D.3) | redan id-medveten sedan omgång 5 (`find_existing_request`) |
| Simkl GET/POST `/oauth2/device` oprövat (§18.6.1) | båda svarar 200 med serverns app 11:2x UTC — koden fungerar |
| Upcoming och kön öppna (§19.8 C–D) | gjort nu, §42–43 |
| status ≈ 26 % (molnets egen skala) | se §45 för vad som återstår |

## 39. Säkerhetskopior (steg 1), 11:12–11:15 UTC

Allt i `.runtime/backups/2026-09-25-pre-integration/` (se `README.txt` där):

- `db/` — hela databasen `cinemind`, alla 21 samlingar som BSON med index och sha256 i
  `manifest.json`, kontrolläst efteråt (0 avvikelser), 70 MB. `backup_db.py` gjorde den.
- `workspace/` — `tracked_changes.patch` (git diff HEAD), `untracked_files.txt`,
  `source_tree.tgz` (hela arbetsträdet inkl. `.env`, utan node_modules/venv/.runtime/.git).
- `runtime/deployed_runtime.tgz` — den driftsatta koden i `~/CineMind` (backend, frontend,
  scripts): packa upp där och `launchctl kickstart -k gui/$(id -u)/com.cinemind.local` för att
  backa en driftsättning.
- git: `refs/backup/wip-2026-09-25-pre-integration` = commit `e576e8f` med hela arbetsträdet
  (förälder `5c53e42`). HEAD, index och filer rördes inte.

## 40. Anslutningarna (steg 2) — uppmätt 11:15–11:25 UTC med läsande anrop

| källa | svar | slutsats |
|---|---|---|
| **Trakt** | `/users/settings` **401 invalid_token** sedan ~10:00 UTC (sista 200 kl 09:42); förnyelse med refresh-token → **400 invalid_grant "session not found"** | Trakt har återkallat sessionen — ny inloggning krävs. Appens id är giltigt (publika anrop 200). Varje körning av Tv-jobbet sedan 10:12 UTC misslyckas ("Required source trakt failed"). |
| Simkl | manuellt Client ID `1825536c…` → 412 client_id_failed; serverns app `54101790…` → 401 user_token_failed | som §29, ingen ny inloggning gjord. Nytt: även publika detaljanrop med det döda id:t svarar nu 412. |
| Plex | `/identity` 200; `/`, `/library/sections` och plex.tv `/api/v2/user` 401 | token återkallad (§29) |
| AniList | 200 (gibbe21, POINT_3) | fungerar |
| TMDb | 200 | fungerar |
| Ollama 192.168.50.94 | 200; `gemma4:12b-it-qat` och `qwen-suggestarr` installerade | fungerar |
| MediaManager 127.0.0.1:5173 | ConnectError | körs inte — Approve och auto_request når den inte |

Koder för alla tre inloggningarna skapades 11:18 UTC (skript i sessionens scratchpad, samma
flöden som Sources) och skickades till Gilbert; alla gick ut obrukade. Inget sparades.

Rättat i koden (inte driftsatt):

- `providers/auth_state.py` (ny): en 401 (Simkl även 412) från anslutningstest, synk eller jobb
  sparar `<källa>_auth_error` på anslutningen; en ny inloggning, ett inklistrat token eller ett
  lyckat anrop tar bort den. `connections_public` rapporterar då källan som *inte* ansluten, så
  Sources visar "Connect with …" i stället för "Connected" över en återkallad inloggning, med
  orsaken i befintlig statusstil (`DeviceConnect` `notice`). Tv-jobbet varnar
  `trakt_signin_rejected` i stället för `trakt_http_error`.
- **Plex kan anslutas med kod**: `POST /api/plex/pin/start`, `/api/plex/pin/poll`,
  `/api/plex/disconnect` (plex.tv/link, samma `DeviceConnect` som Trakt/Simkl). Den nya token
  kontrolleras mot servern (`/library/sections`) innan den sparas; `plex_client_identifier` är
  stabil per konto. Att klistra in en X-Plex-Token fungerar fortfarande.
- Förhandsvisningen i §43 körde den nya koden och skrev därför `trakt_auth_error` på Gilberts
  anslutning 11:39 UTC (det stämmer: Trakt vägrar). Den driftsatta koden läser inte fältet.

## 41. Molnets sex kodcommits — hur de integrerades (steg 3)

Ingen cherry-pick: grenen saknar den lokala koden och alla berörda filer hade lokala ändringar.
Varje ändring granskades och slogs ihop för hand, testfilerna tre-vägs (`git merge-file`, rent).

| commit | integrerat | hur |
|---|---|---|
| `56cedeb` Requests "Ladda fler" hoppade över titlar efter godkännanden | ja, oförändrat | `Requests.jsx` var orörd lokalt; stämmer med lokala `QUEUE_HIDDEN` (archived) |
| `7b92a58` 50-radsskydd i `/history/sync` | **nej — ersatt** | lokala `/history/sync` går genom `providers/history_sync`, som aldrig raderar och bara slår ihop kompletta hämtningar; skyddet är starkare där. Molnets tester gäller den gamla koden. |
| `2930177` `RERANK_LLM_KEEP = 5` | ja, **bara där modellens egen ordning används** (Content to Watch) | där mättes det (+0,022 ± 0,008 nDCG@10, en mätning 2026-09-23, äldre motor). Sparade jobb och AI Search omrankar begränsat över modellens hela ordning precis som mätt i omgång 4 (`jobs.engine.rerank_keep`, `rerank_bounded`, `apply_model_order`). `RERANK_LLM_KEEP = 12` ångrar. |
| `9949f1d` Gemmas val inom relevansgolvet; banans golv från dess bästa | ja | golvet är omrankningspoolens (`relevance_cut(pool)`) — samma snitt som `select_final` gör över titlarna ovanför smakgolvet — och gäller alla anropare (även AI Search och `job_trace`/`taste_report`, som nu tillämpar ordningen exakt som `execute_job`). Samma fel fanns lokalt: ett svagt val först bröt `apply_diversity` direkt. |
| `dd7c01c` `evaluation/verify_live.py` | ja | läser verifierat premiärdatum och `upcoming_only`, kör sparade jobb med `with_job_intent` |
| `bb8e0dc` en avvisning står | ja | lokala `submit` skyddade redan mer (FINAL_STATUSES, id-uppslag). `apply_job_action_mode` slår nu upp varje titel med `find_existing_request`: avvisad eller avfärdad → ingen kö, ingen MediaManager, rekommendationen döljs; godkänd → skickas inte till MediaManager igen. Molnets fyra tester är införda med anpassade fakes. |

## 42. Kön — återkommande kandidater och fortsatt ansamling (steg 5)

Uppmätt 11:25 UTC: **10 180 väntande** (Tv 5 531 mot gränsen 250, Upcoming Tv Shows 536 mot 12,
det avstängda Upcoming US + Anime/Donghua 4 113), 293 avvisade, 59 godkända. Sedan 06:00 UTC:
85 nya köposter, **0** väntande titlar föreslagna igen (uteslutningen fångar dem), 1 väntande
dubblett av en avvisad titel (Frieren, skapad 2026-09-22, före omgång 5), 0 aktuella
rekommendationer som matchar en avvisning.

Rättat (inte driftsatt):

- **Rum i kön** (`apply_job_action_mode`): ett jobb har högst sitt `final_recommendation_limit`
  titlar väntande. Nya titlar köas bara i det rum som finns kvar; redan väntande förnyas som
  förut; inget befintligt rörs. Körningen varnar `queue_full`. **Följd med dagens kö: Tv och
  Upcoming Tv Shows köar inga nya titlar alls** förrän kön beslutats ned eller rensats (§45.6).
  Deras förslag syns fortfarande som rekommendationer.
- **En avvisning på Home glömdes.** En avfärdad rekommendation utan köpost (Content to Watch)
  raderades med resten av listan vid nästa körning, och titeln kunde komma tillbaka. Avfärdade
  rader raderas inte längre och utesluter titeln (`exclusion_engine`, `rejected_dismissed`).
- `blacklist` och `recommendation_feedback` läses utan 500-tak (`load_pipeline_inputs`).

## 43. Upcoming — verifierade premiärdatum (steg 5)

- **`providers/premieres.py`** (ny). Verifierat = en hel dag efter i dag: film `release_date`;
  serie `first_air_date`, eller avsnitt 1 i en kommande säsong (`next_episode_to_air` med avsnitt 1,
  eller säsongens `air_date`); anime utan TMDb-id: AniList `nextAiringEpisode` avsnitt 1 eller
  fullt `startDate` för NOT_YET_RELEASED. Ett år, en månad eller nästa veckoavsnitt i en pågående
  säsong räknas inte. Cache 12 h i `provider_cache`. Fält på raden: `premiere_date`,
  `premiere_kind` (film_release / series_premiere / season_premiere), `premiere_season`,
  `premiere_source`, `premiere_checked_at`.
- **Jobbinställningen `filters.upcoming_only`** (Jobs: chipet "Only upcoming premieres"):
  discover frågar från i morgon till fönstrets slut, plus en lane för serier med avsnitt i
  fönstret oavsett startår — där hittas nya säsonger av äldre serier (`tmdb.upcoming_lanes`).
  Kandidaterna verifieras i `gather_job_candidates`; filtret mäter årsfönstret på premiären och
  avvisar allt utan verifierad premiär (`rejected_not_upcoming`). AniList `fetch_upcoming` körs
  även för sådana jobb. Jobb utan inställningen beter sig som förut.
- **`GET /api/upcoming` + Home "Up Coming"**: användarens rekommendationer (inte avfärdade, inte
  i biblioteket, inte från avstängda jobb) med verifierad premiär efter i dag, en per titel,
  tidigast först, "Season 4 · 4 Mar 2027" på kortet (token `#EBD3A3`) och ett tomt läge i
  befintlig `glass`-stil. Förut visade panelen Content to Watch plats 2–5, vilket datum de än hade.
- **Provkörning mot riktiga data, läsande, 11:39 UTC** ("Upcoming Tv Shows" med `upcoming_only`
  påslaget bara i specen): 1 116 kandidater → **227 med verifierad premiär** (175 seriepremiärer,
  **52 nya säsonger av äldre serier**: The Simpsons S38 2026-09-27, NCIS S24, Silo S4 2027-07-08,
  The Rings of Power S3 2026-11-10, Percy Jackson S3, The Terminal List S2 …). Avvisade: 451 inte
  kommande, 305 genre, 99 redan i kön. **Resultat: 9 val av 12 platser, alla serier, alla med
  premiär efter i dag inom 2026–2029**, t.ex. The Detective Is Already Dead S2 (2021-serie,
  2026-10-07), Magical Explorer 2026-10-03, Sacred Jewel 2026-12-05. Inget sparat.
- **Inte gjort:** `upcoming_only` är inte satt på jobbet i databasen, och `/api/upcoming` är inte
  driftsatt (§45.3).

## 44. Tester

`recommendation_tests` + `evaluation_tests` + `tests/test_job_action_mode.py`: **144 gröna, 0 röda**.
Nya: `test_upcoming_premieres.py` (8), `test_queue_room.py` (5), molnets
`test_manual_decisions_stand.py` (4, fakes anpassade) och molnets 5 tester i
`test_content_to_watch.py` / `test_genre_lanes.py`. Frontend bygger (`npm run build`, bygget i
`frontend/build` är från 11:38 UTC), `check_vision_ui_lock.py` → OK. **Integrationssviten `tests/`
är inte körd**: den går mot den körande appen, som fortfarande kör koden från före sessionen.

Ändrade filer i sessionen: backend `api_extra.py`, `jobs/engine.py`, `server.py`,
`request_providers.py`, `providers/tmdb.py`, `providers/history_sync.py`,
`recommendation/{exclusion_engine,filter_engine,ranking_engine}.py`,
`evaluation/{job_trace,taste_report}.py`; nya `providers/auth_state.py`, `providers/premieres.py`,
`evaluation/verify_live.py`; tester ovan och `tests/test_job_action_mode.py`. Frontend
`components/DeviceConnect.jsx`, `pages/{Connections,Home,Jobs,Requests}.jsx` — bara befintliga
klasser och tokens (`glass`, `chip`, `chip-rose`, `#EBD3A3`, `#8C7F6D`).

## 45. Nästa steg, i ordning

1. **Driftsätt**: `cd frontend && PATH="$PWD/../.runtime/node/bin:$PATH" npm run build && cd .. && bash scripts/sync_runtime.sh`.
   Kör sedan integrationssviten (§10) mot appen.
2. **Gilbert loggar in** (det enda som är hans): Sources → Trakt "Connect with Trakt" (kod på
   trakt.tv/activate) · Simkl "Connect with Simkl" (simkl.com/pin) · Plex "Connect with Plex"
   (plex.tv/link, inloggad som serverägare). Efter driftsättningen visar Sources "Connect" för de
   tre, med orsaken.
3. **Slå på Upcoming**: Jobs → "Upcoming Tv Shows" → Edit → "Only upcoming premieres" → Save.
   Kontrollera Home "Up Coming".
4. **Synken** (godkänd): säkerhetskopia, `python3 -m providers.history_sync --user user_c30bd548254a --dry-run`,
   kör, kör igen (ska importera 0). Kontrollera Plex först: `/status/sessions/history/all` ger alla
   kontons visningar på servern (§46.2).
5. `python3 -m evaluation.verify_live --user user_c30bd548254a` (läsande), sedan `--sync --generate`,
   sedan en andra `--sync` (inga nya rader).
6. **Kön**: `python3 -m evaluation.queue_cleanup plan` på nytt och låt Gilbert besluta om `apply`
   (§32, §37.2). Med rum-i-kön-regeln köar Tv och Upcoming inget nytt förrän kön minskat.
7. Skriv de verifierade resultaten här.

## 46. Fynd som inte åtgärdats

1. **Content to Watch växlar mellan två uppsättningar.** `recommend_again_after_days: 90` läser
   raderna i `recommendations`, men varje generering raderar förra listan: A utesluter B, B
   utesluter A, och A kommer tillbaka tredje gången. Att göra 90-dagarsregeln verklig ändrar vad
   Home visar vid varje generering — Gilberts beslut. Inte ändrat.
2. Plex-historiken (`/status/sessions/history/all`) innehåller alla konton på servern;
   `parse_watch_history_metadata` sparar `accountID` men filtrerar inte.
3. `cinemind.error.log` innehåller TMDb-nyckeln i klartext (httpx loggar URL:er med `api_key`).
4. Integrationssvitens testanvändare ligger i den riktiga databasen (`test-user-fe-claude`,
   `test-user-claude-1`, skapade 08:55–08:57 UTC).
5. MediaManager (127.0.0.1:5173) svarar inte.
6. Publika Simkl-anrop använder det döda manuella id:t tills Simkl ansluts om (då vinner
   `simkl_token_client_id`).

## Omgång 7 (2026-09-25) — CineMind flyttad till NAS:en, bredvid MediaManager

- **CineMind kör nu på NAS:en**, i MediaManagers compose-projekt `/vol2/1000/MediaManager`:
  tjänsterna `cinemind` (container `mediamanager_cinemind`, http://192.168.50.94:8001) och
  `cinemind-mongo` (`mongo:8.0`, data i `./cinemind-data/mongo`, LAN-port 27018).
  Filer: `deploy/nas/Dockerfile`, `deploy/nas/requirements.txt` (bara det backenden importerar,
  + `email-validator` som pydantic kräver), `scripts/deploy_nas.sh` (bygger frontend, skickar
  koden med tar över SSH — aldrig SMB — och kör `docker compose up -d --build cinemind cinemind-mongo`;
  `--env` skriver om `cinemind/cinemind.env` från `backend/.env` med NAS-adresserna).
- **Datan flyttades** med `scripts/mongo_transfer.py`: alla 21 samlingar, samma antal dokument
  (t.ex. requests 10 534, history 13 717, users 5). `connections.mediamanager_url` ändrades i
  NAS-databasen från `http://127.0.0.1:5173` till `http://mediamanager:8000` (compose-nätet).
  Verifierat inifrån containern: MM `/api/v1/health` 200, Ollama `192.168.50.94:11434` 200.
- **Mac-versionen är stoppad** (`launchctl bootout gui/$UID/com.cinemind.local`). Starta den inte
  igen utan att först stoppa NAS-versionen — två appar mot två databaser glider isär.
  Mac-databasen (`~/CineMind/data/mongo`) finns kvar orörd som backup.
- **MM:s compose-fil**: den riktiga (md5 `2cb07fbe…`, backup `docker-compose.yaml.bak.20260925-cinemind`)
  + cinemind-tjänsterna = md5 `af5ccf8b…`. GitHub-versionen som låg där sparades som
  `docker-compose.yaml.replaced-by-cinemind.20260925`.
- **MM-fliken är inlagd och MM är byggt** (samma kväll, efter Gilberts godkännande). MM-källträdet
  återställdes först från `/vol2/1000/MM-restore-20260925`: backend 0 skillnader mot imagen `fbd98a54`
  (407 filer), web 0 skillnader mot BuildKit-cachen (887 filer). Snapshot före återställningen:
  `/vol2/1000/MediaManager-snapshot-20260925-before-cinemind-restore.tgz`. Tillagt:
  `web/src/routes/dashboard/cinemind/+page.svelte` (iframe mot `<samma host>:8001/dashboard`) och posten
  "CineMind" (ikon Popcorn) i `app-sidebar.svelte`; Dockerfile fick `COPY web/vendor/ ./vendor/` före
  `npm ci`. Byggt med `docker compose up -d --build --no-deps mediamanager` — db inte rörd. Verifierat:
  health 200, OpenAPI 233 paths med suggestarr/rename/recommendations, `/web/dashboard/cinemind` 200 och
  "CineMind" i två frontend-noder.
- `db` har `stop_grace_period: 2m` i compose-filen sedan 2026-09-25 (md5 `21127684…`), med Gilberts
  godkännande. Den körande Postgres-containern skapades före ändringen och har kvar docker-standarden
  10 s tills den återskapas. Stoppa den därför med `docker compose stop` före en omstart av NAS:en.
  Avsnittsscannern portas av en annan session ovanpå detta träd.
- Google-inloggning fungerar inte på 192.168.x (Google kräver loopback); logga in med e-post +
  lösenord. AniList-callbacken är kvar på `localhost:8001` (registrerad adress hos AniList).

# OMGÅNG 8 — anslutningar, Upcoming, återkommande kandidater och kön: uppmätt läge (2026-09-25 15:10–15:45 UTC)

Gilberts uppdrag: kontrollera Simkl/Plex/AniList med läsande anrop och återanslut bara det som är
trasigt; Upcoming, återkommande kandidater och kön permanent i backend; testa, driftsätt,
verifiera mot riktiga data. Ingen full synk, inga modelltester, Vision UI orört, inget raderat.

**Läge vid start:** NAS-containern körde exakt arbetsytans backend (md5 för alla .py lika).
Odokumenterat arbete från samma eftermiddag (13:53–14:19 UTC, före omgång 7) ingick och är
driftsatt: `POST /connections/verify` + `auth_state.verify_connections` (Sources frågar varje
källa när sidan öppnas), `recommendation/shown_picks.retire_settled` (Home och Up Coming
pensionerar förslag som avgjorts sedan körningen), `request_providers.settle_duplicates` (ett
beslut arkiverar titelns väntande kopior, batch `dup:<id>`), och `find_existing_request` låter
ett beslut gå före en väntande kopia.

## Anslutningar (läsande `auth_state.check_*` mot NAS-databasen, före och efter omstart)

| källa | svar | vad Sources visar |
|---|---|---|
| AniList | 200, gibbe21 | Connected — **orörd** |
| Simkl | 412 client_id_failed (manuellt id `1825536c…` dött) | Connect with Simkl + orsak |
| Plex | 401 från servern `192.168.50.223:32400` (nåbar från containern: `/identity` 200) | Connect with Plex + orsak |
| Trakt | 401 | Connect with Trakt + orsak |

- Simkls device-flöde provat direkt mot Simkl: det döda manuella id:t → 401 invalid_client,
  serverns app `54101790…` → 200 med kod. `simkl_pin_start` faller alltså tillbaka av sig själv;
  fältet behöver inte rensas. Orsakstexten sa ändå "rensa Client ID, spara, Disconnect och
  Connect" — nu "press Connect with Simkl under Sources" (`providers/simkl.simkl_failure_hint`).
- **Tv-jobbet misslyckas varje körning** ("Required source trakt failed: trakt_signin_rejected");
  det kräver även simkl. Det kommer inga nya Tv-förslag förrän Trakt och Simkl är inloggade.
- Inloggning återstår (bara Gilbert): Sources → Connect with Simkl / Plex / Trakt.

## Upcoming

`GET /api/upcoming` (läsande kopia av endpointen, ingen skrivning): 9 kort, alla från
"Upcoming Tv Shows" (`upcoming_only` påslaget), alla med verifierad premiär efter i dag:
Dive into You 2026-09-26, Magical Explorer 10-03, Good Boy 10-04, The Detective Is Already Dead
S2 10-07, Take Charge of My Heart 10-09, Dreamland 10-17, The Patrick Star Show S6 10-30,
The Grim Lover 11-28, Sacred Jewel 12-05. Inget av dem ligger i kön, är sett, i biblioteket eller avvisat.

## Återkommande kandidater och kön

- Kön: 10 532 rader, **10 085 väntande** (Tv 5 530, avstängda Upcoming US 4 021, Upcoming Tv
  Shows 534). **0 nya rader sedan 09:43 UTC**, 0 omföreslagna (`suggested_count`), 0 väntande
  som matchar en avvisning, 0 aktuella förslag som är avgjorda någon annanstans (utom 3 i det
  avstängda jobbet, som inget visar).
- Titlar valda i fler än en körning, sedan 07:30 UTC: bara Upcoming Tv Shows 9 — de hålls
  tillbaka (`queue_full`: 534 väntande mot taket 12) och är inte köade, så nästa körning väljer
  dem igen (jobbet har `already_recommended: false`). De når aldrig kön. Före morgonens
  rättningar: 262 titlar i 2–34 körningar (animefilmer i TV-jobbet m.m., RO-42/48).
- **Rotorsaker till ansamlingen** (alla rättade i omgång 5–6, nu driftsatta): inget tak (RO-44),
  bara titel+år-matchning (RO-45), beslut skyddades inte (RO-46), animefilmer kändes inte igen
  (RO-48). Det som ligger kvar är gamla rader: **3 374 av Tv:s väntande uppfyller inte Tv:s
  nuvarande inställningar** (min_year 2018 sattes ~08:15–08:40 UTC i dag; 07:12–08:12 köades
  ännu 1991–2012-titlar), 263 filmer i det TV-only Upcoming-jobbet, 156 redan sedda, 12 dubbletter.
  Med taket köar Tv och Upcoming Tv Shows inget nytt förrän deras andel minskat.
- **Fel rättat i `evaluation/queue_cleanup.py plan`:** köraderna saknar premiärdatum, så för ett
  `upcoming_only`-jobb lästes varje väntande serie som "inte kommande" och planen föreslog att
  arkivera titler med premiär nästa månad. Planen verifierar nu premiären först
  (`_verified_premieres`, `verify_premieres(store=False, unanswered=...)`: inga skrivningar, inte
  ens cachen; en rad utan svar bedöms inte på premiären). Test:
  `test_the_queue_clean_up_verifies_an_upcoming_jobs_premieres_before_judging_them`.
- **Rensningsplan (inte tillämpad, Gilberts beslut):** `arch_20260925_152824` — 3 910 att arkivera
  (fails_job_filters 3 742, already_watched 156, duplicate_row 12) → 6 175 väntande kvar;
  Tv 3 374, Upcoming Tv Shows 497, avstängda jobbet 39. `apply --batch arch_20260925_152824`,
  `revert` återställer. `arch_20260925_152446` gjordes före rättningen och raderades av Gilbert 15:5x UTC
  (den arkiverar 23 rader som nu lämnas: 5 med verifierad kommande premiär, t.ex. Marshals S2 2026-10-04 och Doctor Who 2027-01-14, och 18 donghua som ingen källa svarade för).

## Vanliga rekommendationer

Content to Watch spårad läsande (`job_trace --spec`, samma spec som `/recommendations/generate`,
utan LLM): 750 kandidater → 495 uteslutna (sedda/bibliotek/kö/…) → 8 val (5 engelsk TV, 3 anime,
t.ex. The Witcher: Blood Origin, Dead Mount Death Play); inget av dem finns i kön, historiken,
biblioteket eller bland avvisningarna.

## Tester och driftsättning

`recommendation_tests` + `evaluation_tests` 180 gröna, `tests/test_job_action_mode.py` 7 gröna
(körd isolerat: `tests/conftest.py` sår en testanvändare och session i den riktiga databasen).
Frontend bygger, `check_vision_ui_lock.py` OK (ingen frontendändring i omgången). Driftsatt
15:31 UTC med `scripts/deploy_nas.sh`; md5 på NAS = arbetsytan; `/api/` 200, inga fel i loggen.
Efter driftsättningen: Upcoming 15:30 (9 val, queue_full), Tv 15:42 (failed, Trakt) — kön oförändrad.

**Inte verifierat via inloggade HTTP-anrop:** att skriva en tillfällig session för Gilberts konto
(som `evaluation.verify_live` gör) nekades i sessionen; allt ovan är läst direkt ur databasen och
med backendens egna funktioner. Kvar: Gilbert loggar in (Simkl, Plex, Trakt), sedan
kontroll med riktiga API-anrop, omstart och ny kontroll.

## Kvar / fynd

1. Plex-biblioteket (`media_library`, 400 rader) är från 2026-09-08 och förnyas bara av synken.
2. Fyra test-/demorader utan jobb i Gilberts `recommendations` ("Shelf Drama", "Library
   Sentinel", "Owned Sci-Fi" …, 2026-09-08) visas bara om Content to Watch är tom.
3. TMDb-nyckeln syns i klartext i containerloggen (httpx INFO-rader, §46.3).
4. Content to Watch är från 2026-09-22 och genereras bara när du trycker (A/B-växlingen §46.1 kvar).

## localhost:8001 på Macen → NAS:en (2026-09-25 15:49 UTC)

"This site can't be reached / ERR_CONNECTION_REFUSED" på `http://localhost:8001`: Mac-appen är
stoppad sedan omgång 7, men AniList-återkopplingen (`ANILIST_REDIRECT_URI`), Google-callbacken,
CineMind.app och bokmärken går fortfarande dit. `scripts/nas_forward.py` vidarebefordrar Macens
loopback (127.0.0.1 och ::1, port 8001 — inte LAN) byte för byte till `192.168.50.94:8001`;
`scripts/install_nas_forward.sh` installerar LaunchAgenten `com.cinemind.nas-forward` (RunAtLoad,
KeepAlive, körs från `~/CineMind/scripts`, logg `~/CineMind/.runtime/nas-forward.log`) och
**stänger av `com.cinemind.local`** (`launchctl disable`), som annars hade startat den gamla
Mac-backenden mot Mac-databasen vid nästa inloggning. Verifierat: `/`, `/api/`, `::1` och
AniList-callbacken svarar 200 via localhost, samma sida som NAS:en (md5 lika), processen startar
om av sig själv efter kill, sidan laddar i webbläsaren. `--remove` backar (och tillåter
`com.cinemind.local` igen). Inloggningen på localhost är en egen cookie: logga in en gång där.

## Simkl-inloggningen kastades (2026-09-25 15:53 UTC) — rättat och driftsatt 15:57

Gilbert tryckte Connect with Simkl: device-koden kom från serverns app (det döda manuella id:t
401, sedan 54101790… 200) och Simkl utfärdade token 15:53:18 — men bekräftelsen
`POST /users/settings` svarade **403**, och `simkl_pin_poll` behandlar 403 som "vägrad", så inget
sparades. Orsak: Simkl AUTH V2 räknar POST som en skrivning, och en inloggning utan `scope` får
bara `media:read` (api.simkl.org/api-reference/oauth2-scopes: 403 `insufficient_scope`). Både
bekräftelsen (`server.simkl_pin_poll`) och Sources-kontrollen (`auth_state.check_simkl`) gör nu
`GET /users/settings`. CineMind skriver aldrig till Simkl, så läsbehörighet räcker. Testfakes
svarar nu 403 på POST precis som Simkl (`test_a_read_only_simkl_sign_in_is_confirmed_with_a_read`).
Plex-koden 15:53 hämtades en gång; sedan laddades Sources om och väntan på koden avbröts — ingen
kodändring, men sidan måste stå kvar tills "Connected" visas.

## Inloggningarna gjorda och verifierade (2026-09-25 15:58–16:13 UTC)

Gilbert anslöt Trakt, Plex och Simkl i Sources. Verifierat med riktiga anrop mot källorna (före och
efter `docker compose restart cinemind` 16:00): Trakt `/users/settings` 200 gibbe21, Simkl
`GET /users/settings` 200 "Gilbert Grigorian" (serverns app, `simkl_token_client_id` sparad), Plex
`/library/sections` 200 på 192.168.50.223:32400 (gibbe211), AniList 200 gibbe21 (orörd); inga
`*_auth_error` kvar, `connections_public` visar alla fyra anslutna. **Tv-jobbet 16:12 gick igenom
för första gången sedan 10:12** (1 041 kandidater, 2 val, `queue_full` 5 530/250 — inget köat).
Upcoming 16:00: samma 9 verifierade premiärer. Kön: 10 532 rader, 10 085 väntande, 0 nya sedan
09:43 UTC; inget aktuellt val är avgjort någon annanstans. Tv ger bara 2 nya val eftersom de
5 530 väntande utesluts — rensningsplanen `arch_20260925_152824` är fortfarande Gilberts beslut.

# OMGÅNG 9 — varningarna i Runtime logs, 650 per jobb och kön (2026-09-25 16:45–18:00 UTC)

Gilberts uppdrag (skärmbilder från Jobs och Runtime logs 16:32–16:40 UTC): ta reda på varför
körningarna varnar, låt jobben "requesta 650 i stället för 250 och 200", och rätta de andra
varningarna på bilderna. **Hans val** (frågat i sessionen): högst 650 väntande per jobb, en full
andel visas som info i stället för varning; arkivera de 3 910 raderna i planen `arch_20260925_165033`.

## Varför det varnade (läst ur NAS-databasen och med `job_trace`, inget skrivet)

| varning | orsak |
|---|---|
| `queue_full` på varje körning | Taket räknar jobbets väntande titlar: Tv 5 530 mot 250, Upcoming US 4 021 mot 200, Upcoming Tv Shows 534 mot 12. Ansamlingen är från före taket (RO-44) och från gamla inställningar; inget nytt hade köats sedan 09:43 UTC. Ett högre tak ensamt hade bara gett Upcoming Tv Shows plats. |
| `no_picks`, Tv 16:34 | Texten skyllde på årsfönstret ("No candidate fell inside the job's year window") fast 413 av 1 041 föll där; resten låg redan i Requests, var sedda eller under betygsgolvet, och de 18 som klarade alla inställningar låg under smakgolvet. Tipset gavs för den största orsaken även när den inte gällde alla. |
| `run_failed`, Tv 14:42–15:42 UTC | Trakt-inloggningen var avvisad; löst när Gilbert loggade in 15:52–15:58 UTC. Token gäller till 2026-10-02 och förnyas automatiskt (klient-id och hemlighet finns på NAS:en). Inga Trakt-fel sedan dess. |
| Toasten "2 sent to Requests" | Räknade valen, inte vad som köades: de två hölls tillbaka av den fulla kön. |

**650 ändrar inte hur många titlar en körning väljer; det gör smakgolvet** (`pipeline.TASTE_FLOOR`,
läsande förhandsvisning 16:45 UTC med `select_final` på 650):

| jobb | kandidater | förbi inställningar och uteslutningar | klarar smakgolvet | val med 650 |
|---|---|---|---|---|
| Tv | 1 105 | 70 | 2 | 2 |
| Upcoming US + Anime/Donghua | 1 411 | 67 | 6 | 6 |
| Upcoming Tv Shows | 919 | 54 | 9 | 9 |

Tv: 431 kandidater låg redan i kön och 411 utanför 2018–2029 (likhetsbanorna: tmdb_recommendations
209, tmdb_similar 72, trakt 52, taste_keyword_discover 49, simkl 29). De bästa som föll på golvet:
Debris (personal 3,94, specific 0,19), Jury Duty (3,28 / 0,27), Driver's Ed (3,37 / 0,24).

## Ändrat

Kod (`jobs/engine.py`, `api_extra.py`, `frontend/src/pages/Jobs.jsx`, test):
- `NOTICE_CODES` (`queue_full`, `no_new_picks`) och `split_notices`: sådana meddelanden sparas i
  `run.notices`, körningen blir `completed`, och Runtime logs visar dem som "okey" — även gamla
  körningars `queue_full`, som ligger bland deras `warnings`.
- `empty_result_warnings`: `no_picks` (varning) bara när inget kom förbi jobbets egna
  inställningar, och ett filters tips bara när det filtret tog alla. Annars notisen
  `no_new_picks` med hela uppdelningen (i Requests, sedda, utanför inställningarna, under smakgolvet).
- `apply_job_action_mode(..., outcome=)` → `run.requests` = mode, limit, waiting (efter körningen),
  queued, refreshed, held_back, sent. `run_completed` i Runtime logs säger "N new in Requests";
  toasten "N new in Requests · M already waiting · K wait for room (W waiting, limit L)".
  `queue_full`-texten säger att jobbet håller högst L väntande och hur man gör plats.
- Jobs: notiser som vanliga `chip` i körhistoriken och förhandsvisningen (befintlig klass,
  `check_vision_ui_lock.py` OK).

Data på NAS:en, efter säkerhetskopian `.runtime/backups/2026-09-25-omgang9/` (requests 10 532
rader, jobs 3, md5 i `manifest.json`):
- De tre jobben via `jobs.engine.update_job` (validerat, som Edit → Save): `final_recommendation_limit`
  650 och `candidate_limit` 650 (Tv 250/300, Upcoming US 200/220, Upcoming Tv Shows 12/120 förut).
  Discover går då 10 sidor per bana (taket i `discover_page_span`) i stället för 6–8.
- `queue_cleanup apply --batch arch_20260925_165033` 17:38 UTC: 3 910 arkiverade
  (fails_job_filters 3 742, already_watched 156, duplicate_row 12). Väntande: Tv 5 530 → 2 156,
  Upcoming Tv Shows 534 → 37, Upcoming US 4 021 → 3 982. Inget raderat (10 532 rader kvar).
  Ångra: `MONGO_URL=mongodb://192.168.50.94:27018 PYTHONPATH=../.runtime/python:. python3 -m
  evaluation.queue_cleanup --user user_c30bd548254a revert --batch arch_20260925_165033`.
- Rättelse till omgång 8: "Tv 3 374, Upcoming Tv Shows 497, avstängda jobbet 39" var planens
  `archive_by_job` (det som arkiveras), inte det som blir kvar.

## Tester och driftsättning

`recommendation_tests` + `evaluation_tests` 188 gröna (ny: `test_run_notices.py`, 7);
`tests/test_job_action_mode.py` + `tests/test_empty_job_results.py` 22 gröna med `--noconftest`
(ingen databas rörd). Frontend bygger. Driftsatt 17:37 UTC med `scripts/deploy_nas.sh`, och
17:50 och 17:53 UTC igen för textjusteringar (`empty_result_warnings`, toasten): md5 för
`jobs/engine.py` och `api_extra.py` på NAS:en = arbetsytan, samma `main.a0ee479a.js`, `/api/` 200,
inga fel i loggen.

## Verifierat efter ändringen (schemalagda körningar, läst ur NAS-databasen)

| körning (UTC) | status | kandidater | val | i Requests |
|---|---|---|---|---|
| Upcoming Tv Shows 17:45 | completed | 922 | 9 | **9 nya** (37 → 46 väntande) — de första nya köraderna sedan 09:43 |
| Upcoming US + Anime/Donghua 17:47 | completed | 1 591 | 7 | 0 nya, 7 väntar på plats (3 982 väntande, notis `queue_full`) |
| Tv 17:49 | completed | 1 180 | 1 (Sisi) | 0 nya, 1 väntar på plats (2 156 väntande, notis `queue_full`) |

De nio nya: The Detective Is Already Dead, Dreamland, The Patrick Star Show, Magical Explorer,
The Grim Lover, Good Boy, Take Charge of My Heart, Sacred Jewel, Dive into You (samma verifierade
premiärer som Up Coming visar). Runtime logs (`runtime_logs` anropad direkt i containern, läsande):
WARNING 61 → 47, och alla rader efter 17:39 UTC är "okey" (`run_completed` "… 9 new in Requests",
`queue_full`). Det som är kvar under FAILED/ERROR/BUGS/WARNING är äldre än ändringen: `run_failed`
Trakt senast 15:42, `delivery_failed` senast 12:50, `sync_error` 06:46, `no_picks` i gammal
formulering senast 16:34, `taste_source_excluded` 06:31, resten 2026-09-08–09-22.

Inte verifierat: toasten och chipsen i webbläsaren (kräver Gilberts inloggning; en Run now köar
titlar). Datat de läser (`requests`, `notices`) är kontrollerat ovan och bygget innehåller texten.

## Kvar / fynd

1. **Tv (2 156 väntande) och Upcoming US (3 982) är fulla mot 650.** De köar nytt först när ~1 510
   resp. ~3 330 titlar är beslutade eller arkiverade; notisen säger det utan varning. Ytterligare
   rensning är Gilberts beslut.
2. **Smakgolvet, inte taket, avgör antalet val per körning** (2–9). Fler val kräver fler kandidater
   med konkret koppling.
3. **Anime-uppföljare från AniList kan aldrig klara golvet:** raderna saknar tmdb_id, keywords,
   cast och studio (t.ex. Reincarnated as a Sword Season 2, anilist 159042; Shangri-La Frontier
   Season 3, 189323; Black Clover Season 2, 195604) → personal 4,4–5,2 men specific 0,0 <
   `SPECIFIC_FLOOR` 0,3; kopplingen till säsong 1 syns inte. Överlämnat till sessionen
   "Upcoming prioritering i CineMind", som arbetar med uppföljare och nya säsonger.
4. **75 godkännanden nådde aldrig MediaManager** (`delivery_status: not_delivered`, "Could not reach
   MediaManager", godkända 2026-09-22 07:07 – 09-25 12:50, när MediaManager kördes mot Macen):
   26 står som godkända, 49 har gamla körningar tryckt tillbaka till väntande (RO-46). Det är 61 av
   ERROR-raderna i Runtime logs. Att skicka dem igen startar nedladdningar i MediaManager — Gilberts
   beslut (Approved → Send to MediaManager).
5. Nya jobb börjar fortfarande på 8 (`Jobs.jsx` `empty()`, `api_extra` 8); bara de tre befintliga har 650.
6. Omgång 9 är inte committad (Gilbert har inte bett om det).

# OMGÅNG 10 — Upcoming först: uppföljare, stegvis breddad sökning, "allt kommande" (2026-09-25 17:40–18:50 UTC)

Gilberts uppdrag: Upcoming är CineMinds högsta prioritet — filmer, TV-serier, anime, nya titlar,
gamla serier med nya säsonger, fortsättningar/spin-offs och framtida säsonger; det som räknas är att
innehållet är kommande och passar hans smak, inte om serien eller franchisen är gammal. Sökningen
får inte sluta vid för få eller inga träffar utan ska breddas stegvis (andra källor, närliggande
genrer, relaterade titlar, nya säsonger). Befintliga regler ändras inte i onödan.
**Hans val** (frågat i sessionen): (1) "Upcoming US + Anime/Donghua 2026–2029" får "Only upcoming
premieres"; (2) Up Coming visar *allt kommande som är hans*: nya förslag, titlar som väntar i Requests
eller som han godkänt, och nya säsonger/uppföljare av titlar han gillar även om serien finns i
biblioteket — kvar till premiären, aldrig det han avvisat; (3) Upcoming går före generiska
rekommendationer i sökningen, i Requests och på Home (han låste upp just flytten av Up Coming före
Content to Watch).

## Uppmätt före (läsande, NAS-databasen, "Upcoming Tv Shows", 17:55 UTC)

1 166 kandidater → 229 verifierade premiärer (176 seriepremiärer, 53 nya säsonger) → 45 förbi
inställningar och uteslutningar → **1 över smakgolvet**. Varför:

| fynd | uppmätt |
|---|---|
| Nya säsonger av serier han sett stoppades som "redan sedd" | 7: The Simpsons S38 (10/10), The Rings of Power S3, Delicious in Dungeon S2 (10/10), Tougen Anki S2, Reincarnated as a Sword S2, The Iceblade Sorcerer S2, Rakshasa Street S5 (10/10) |
| AniList-uppföljare saknade koppling till säsongen han sett | personal 4,4–5,2, specific 0,0 < `SPECIFIC_FLOOR`. Av dem är bara Reincarnated as a Sword, Iceblade Sorcerer, Black Clover och Ranma1/2 hans; Shangri-La Frontier, A Returner's Magic och Appraisal Skill finns inte i historiken, A Wild Last Boss har 3 avsnitt (inte gillad) — dem håller golvet rätteligen ute |
| Filmuppföljare söktes aldrig | 13 kommande filmer i TMDb-samlingar av gillade filmer: Avengers: Doomsday, Secret Wars, Dune: Part Three, Avatar 4/5, Frozen III, Black Panther 3, Jumanji: Open World, Godzilla x Kong: Supernova … |
| Varje körning ersatte jobbets lista | de 9 valen 17:46 hamnade i Requests; nästa körning hittade 1 ny → Up Coming hade krympt till 1 kort |
| Filmer täcktes inte | bara det TV-only jobbet hade `upcoming_only`; Up Coming "Movies" var tomt |
| Smakbanorna sökte släppta titlar | 550 av 1 166 avvisade `rejected_not_upcoming` |
| Metadata för osläppta titlar uppdaterades aldrig | 68 av 229 verifierade premiärer utan ett enda nyckelord (`media_enrichment` går aldrig ut) |

## Ändrat (kod)

- **`providers/continuations.py`** (ny): kommande fortsättningar av *gillade* titlar (profilens
  positiva, aldrig en avvisad; sedd ≠ gillad). TMDb: säsong ≥ 2 av en gillad serie (verifierad
  premiär); kommande filmer i en gillad films TMDb-samling (cache 7 d / 12 h). AniList: SEQUEL /
  SPIN_OFF / SIDE_STORY framåt från gillade anime (genom redan sända säsonger, 3 steg), och bakåt
  (PREQUEL / PARENT) från kommande AniList-titlar till en gillad titel matchad på namn utan årtal och
  säsongstagg ("Ranma1/2 (2024) Season 3" → "Ranma1/2"). En rad per kommande säsong: TMDb:s vinner,
  AniList-id:t följer med (`drop_duplicate_seasons`). Raden bär `continuation_of`.
- **`recommendation/ranking_engine.py`**: `continuation_link` — en fortsättning är den starkaste
  franchise-kopplingen, rabatterad med bevisen bakom den gillade titeln som i `best_specific`; den
  går in i `franchise_affinity` och `specific_score`, bara om titeln finns i *den här* profilen
  (en utelämnad titel lånar inget, som `seed_support`). Varför-texten börjar "is season 2 of X, which
  you rated 10/10". `_liked_mark` säger "watched 3 times" för film (var "3 episodes of").
- **`recommendation/exclusion_engine.py`**: `is_coming_continuation` — en verifierad kommande
  fortsättning är varken "redan sedd" eller "i biblioteket"; ett nej i kön står kvar
  (`requested_rejected`); en fortsättning av en serie i biblioteket visas men köas aldrig
  (`continuation_in_library`, `apply_job_action_mode`).
- **`jobs/upcoming.py`** (ny) + hakar i `jobs/engine.py`: för jobb med `upcoming_only`
  (1) fortsättningarna först i poolen (`gather_job_candidates`), (2) färre än `UPCOMING_TARGET` (20)
  nya val → bredda steg för steg (`broaden`): `taste_window` (profilens genrepar och teman inom
  premiärfönstret), `deeper_pages` (jobbets egna banor ett sidspann längre), `other_sources` (Trakt
  anticipated + premiärkalendern, AniList längre ned, TMDb /movie/upcoming). Pipelinen körs om efter
  varje steg; stoppar vid målet eller när alla steg är provade; ett steg som fallerar avslutar inte
  sökningen. `run.upcoming_search` sparar vad varje steg gav (även i förhandsvisning och `job_trace`).
  (3) `carry_over`: tidigare öppna val stannar i listan till premiären (inom jobbets gräns); de som
  väntade på plats erbjuds kön igen. (4) `refresh_request_premieres`: efter varje upcoming-körning
  får upp till 300 väntande/godkända köer ett verifierat premiärdatum — bara premiärfälten, aldrig
  status eller `updated_at`. `python3 -m jobs.upcoming --user <id>` gör alla på en gång.
- **`providers/tmdb.py`**: `window_params` — smakbanorna (`taste_seeded_discover`,
  `taste_keyword_discover`) frågar inom premiärfönstret för upcoming-jobb, utan röstgolv; utan fönster
  exakt som förut. `upcoming_movies`.
- **`providers/trakt.py`**: `fetch_upcoming_titles` (anticipated-listor + `/calendars/all/shows/premieres`,
  jobbets genrer som Trakt-filter, cache 6 h, publika anrop).
- **`providers/tmdb_enrich.py`**: en post hämtad innan titeln kom ut hämtas om efter 7 dagar
  (`unreleased_when_fetched`); släppta titlar går fortfarande aldrig ut.
- **`GET /api/upcoming`** (`server.py`, `shown_picks.upcoming_hidden_reason`): alla aktiverade jobbs
  val + väntande och godkända köer med verifierad premiär + nya säsonger även när serien finns i
  biblioteket; döljs bara av svartlista, avfärdad rad eller avvisning i Requests (läsande kontroll,
  skriver inga `retired`). En per titel, högst `limit` per sort (anime / TV / film). Kort från kön
  bär `request_id` (Approve/Reject verkar på köraden; godkända visas som klara).
- **Requests**: ny standardsortering "Upcoming premieres first" (`request_queue.upcoming_first`);
  filtret "Upcoming" räknar en kommande säsong (`release_bucket`, spegeln i `mediaFilters.js`).
- **Home** (`Home.jsx`): Up Coming före Content to Watch — överst till höger bredvid New Trailer, först
  på mobil — och visas även när Content to Watch är tom. Bara ordningen ändrad, befintliga klasser;
  `check_vision_ui_lock.py` OK. Kontrollerat i en lokal attrapp med påhittade titlar (skärmbilder:
  dator, mobil 375 px utan sidoscroll, tom Content to Watch; inga konsolfel).

## Mätning

- **Offline, oförändrat** (`snap_live.json` 2026-09-24, 8 veck, HEAD mot arbetsytan): 0 skillnader —
  holdout P@5 0,400, NDCG@5 0,371, NDCG@10 0,336, parvis 0,716; kronologiskt P@5 0,400, NDCG@5 0,470.
  Ögonblicksbilden har inga fortsättningar, så inget uppmätt värde kan röra sig; kontrollarmarna är
  därmed också oförändrade.
- **Riktiga data, läsande `job_trace`-väg** (samma kod som körningen, bara cache skriven):

| jobb | före | efter | varav fortsättningar | breddning |
|---|---|---|---|---|
| Upcoming Tv Shows | 1 val | **12** | 9-1-1: Nashville S2, Delicious in Dungeon S2, Reincarnated as a Sword S2, Rakshasa Street S5, Iceblade Sorcerer II, Black Clover S2, The Simpsons S38, Ranma1/2 S3 | `other_sources` gav 4 (Black Dagger Brotherhood S2, Grimsburg S3, Cat's Eyes S2, Undercover S6) |
| Upcoming US + Anime/Donghua (nu `upcoming_only`) | — | **33** | + Avengers: Doomsday, Black Panther 3, Dune: Part Three, Jumanji: Open World, The Rookie S9 | behövdes inte (33 ≥ 20) |

  Ingen fortsättning hamnade under smakgolvet; The Rookie S9 faller på Upcoming Tv Shows genrer
  (crime/drama) och Avatar 5 (2031) utanför 2026–2029, som inställningarna säger.

## Tester

`recommendation_tests` + `evaluation_tests` **213 gröna** (nya: `test_upcoming_search.py`, 25);
`tests/test_job_action_mode.py`, `test_empty_job_results.py`, `test_requests_queue_order.py`,
`test_job_stagger.py`, `test_runtime_logs.py` 39 gröna med `--noconftest`. Frontend bygger.

## Data på NAS:en

- Säkerhetskopia `.runtime/backups/2026-09-25-omgang10/jobs.jsonl` (3 jobb, md5 i `manifest.json`).
- `job_87f31614e5a8` fick `filters.upcoming_only: true` via `jobs.engine.update_job` (Gilberts val);
  fönster 2026–2029 och film/TV/anime oförändrade. Gäller redan med den gamla koden på NAS:en.

## Inte gjort — väntar

1. **Driftsättning**: `bash scripts/deploy_nas.sh` nekades av sessionens behörighetskontroll
   ("Production Deploy"); Gilbert kör den eller godkänner. NAS:en kör fortfarande omgång 9.
2. Efter driftsättningen: säkerhetskopiera `requests`, kör `python3 -m jobs.upcoming --user
   user_c30bd548254a` (premiärdatum på köns väntande/godkända rader; annars 300 per upcoming-körning),
   och verifiera de första schemalagda körningarna (`run.upcoming_search`, antal val, `carry_over`),
   `GET /api/upcoming` per sort och Requests-ordningen.
3. Inte committat (varken omgång 9 eller 10).

## Kvar / fynd

1. Samma fortsättning kan väljas av båda upcoming-jobben; Upcoming US har `already_recommended` och
   hoppar över den om Upcoming Tv Shows redan har den, Up Coming visar ett kort per titel och kön
   känner igen titeln (förnyas, dubbleras inte).
2. AniList har en minutbudget; `other_sources` läser 8 sidor till och slutar tyst vid 429.
3. Tv (2 156) och Upcoming US (3 982) är fortsatt fulla mot 650: Upcoming US val syns i Up Coming men
   köas först när det finns plats. Rensning är Gilberts beslut (se omgång 9).

# OMGÅNG 11 — CineMind-fliken syntes inte i MediaManagers sidomeny (2026-09-25 18:25–18:45 UTC)

Gilbert: "ser inte cinemind fliken i sidebaren som du skapade på min … nas media manager app".
Bara MediaManager ändrat; CineMinds kod, databas och container orörda (omgång 10 är fortfarande
inte driftsatt).

- **Orsak:** omgång 7 lade posten bara i MM:s klassiska sidomeny (`nav/app-sidebar.svelte`). MM har
  fyra navigeringar, och vilken som visas beror på tema och sidomenyläge som sparas i webbläsaren
  (`mediamanager.appearance`, `mediamanager.sidebar-preferences`): Glass 27 har egen meny, och
  hover-railen (ChatGPT App / "chatgpt-hover") likaså. Live före: den byggda layouten (nod 2)
  länkade `dashboard/episode-scanner` 6 gånger men `dashboard/cinemind` 1 gång.
- **Ändrat på NAS:en** (`/vol2/1000/MediaManager/web/src/lib/components/`, via SSH, md5 före och
  efter): CineMind (ikon Popcorn) i `glass27/glass27-navigation.svelte`, `nav/chatgpt-app-rail.svelte`
  och `media-manager-liquid-glass/media-manager-liquid-glass-navigation.svelte` (tre listor; ingår inte
  i bygget, ändrad som scannern), sidtiteln i `glass27-header`, `vision-header`, `liquid-header`.
  Backup `backups/pre-cinemind-nav-20260925T183419Z.tgz`; md5 och detaljer i MM:s `HANDOFF.md`,
  avsnittet "CineMind i MediaManager". Hur man lägger in allt igen: `deploy/mediamanager/README.md`.
- **Test och bygge:** svelte-check 0 fel (6 285 filer). `docker compose up -d --build --no-deps
  mediamanager` → avbild `2f79aacf…` (förra taggad `mediamanager-enhanced:pre-cinemind-nav-20260925`
  för återställning), health 200 efter ~15 s, entry `app.C-7Q1vQx.js`, nod 2 länkar
  `dashboard/cinemind` 6 gånger. `/web/dashboard/cinemind` och `:8001/dashboard` svarar 200.
- **Renderat:** det byggda paketet (kopierat ur containern) serverat lokalt med en påhittad
  superanvändare för `/api/v1/users/me` och 404 för allt annat, i den inbyggda webbläsaren:
  CineMind syns och är markerad som aktiv i den klassiska menyn, i Glass 27 (System) och i
  hover-railen (ikonläge: alla 18 poster ryms i 1 357 px, CineMind som fjärde). Tillfällig
  `mm-nav-preview` i `.claude/launch.json` borttagen efteråt.
- **Inte sett:** Gilberts egen inloggade vy (sessionen loggar inte in). Han behöver ladda om sidan
  (Cmd+Shift+R) eller öppna MM-fönstret på nytt; HTML:en har `no-cache`, det gamla paketet ger 404.
- **Fynd, inte åtgärdade (fanns före, gäller Avsnittsscanner likadant):** Glass 27 markerar
  "Dashboard" som aktiv på varje undersida och rubriken säger "Dashboard", eftersom
  `isActive` och `exactRouteTrails` jämför med `/dashboard…` medan `page.url.pathname` börjar med
  basvägen `/web`; sidtitlarna som lades in syns därför inte än. Hover-railen i etikettläge är
  bredare än ett 1 357 px fönster (Dashboard, Discover och AI Recommendations hamnar utanför till
  vänster); ikonläget, standard, ryms.
- **Samtidigt på NAS:en:** en annan session körde MM:s pytest i den gamla avbilden från
  `/tmp/mm-base` och har en kopia `/tmp/mm-scanfix` (backend + tester, ingen `web/`), så dess
  scannerfix skriver inte över menyerna. MM:s logg efter omstarten 18:38 visar två fel som inte rör
  menyerna och inte utreddes: Plex 401 i MM:s egna rekommendationer (användare `e2551a48…`) och
  `OSError` vid import av Clevatess S02E07.

# OMGÅNG 12 — varför Upcoming inte hittar kommande filmer, serier och anime (2026-09-26 05:50–06:15 UTC, läsande)

Gilbert: "kolla upp varför min cinemind app inte letar och eller hittar ej släppta kommande tv serier
filmer anime filmer anime tv". Inget skrivet i NAS-databasen; bara `job_trace` (cache) och frågor.

- **NAS:en kör omgång 9.** `jobs/upcoming.py` och `providers/continuations.py` finns inte där;
  `tmdb.py`/`server.py` = HEAD, `jobs/engine.py` = omgång 9. Omgång 10 är aldrig driftsatt.
- **Live, senaste 24 h:** Upcoming Tv Shows 77 körningar, senaste 0 val (`no_new_picks`: 939
  kandidater, 594 utan verifierad premiär, 43 under smakgolvet, 7 "redan sedda" — nya säsonger).
  Upcoming US + Anime/Donghua 11 val per körning men `queue_full` (3 723 väntar mot 650), så inget
  når Requests. Up Coming på Home: 12 kort, 11 från Upcoming US (svaga, match 47–68, inga
  uppföljare) + 1 koreansk serie; **ingen anime, ingen animefilm**.
- **Omgång 10-koden mot NAS-databasen (`job_trace`, läsande):** Upcoming Tv Shows 0 → 12 val
  (5 anime, 1 donghua, 2 animation, 2 engelsk TV, 2 annat språk; `other_sources` gav 4);
  Upcoming US 11 → 39 (15 film, 13 TV, 5 anime, 1 donghua), bl.a. Avengers: Doomsday, Dune: Part
  Three, Delicious in Dungeon S2, Black Clover S2, Iceblade Sorcerer II, Rakshasa Street.
  Fortfarande 0 animefilmer: 3 i poolen, alla utan verifierat datum.
- **Datumkravet stänger ute det mesta av anime.** AniList NOT_YET_RELEASED (400, popularitet):
  bara 81 har exakt dag. TV/ONA 67 av 287 (utan dag: Frieren S3, Dandadan S3, Bocchi S2, Witch Hat
  Atelier S2, [Oshi no Ko] Final); film 13 av 57 (utan dag: Demon Slayer Infinity Castle del 2/3,
  Solo Leveling, One Piece Film: God Valley, Haikyuu!!). `premieres.premiere_from_anilist` kräver
  år+månad+dag (regeln "never a year, a month", omgång 6); `anilist.fetch_upcoming` hoppar dessutom
  över titlar utan år när jobbet har `min_year`.
- Tester på arbetsytan: 213 + 39 gröna.
- Sidofynd, inte åtgärdat: demo-rader från `api_extra.py` (Library Sentinel, Shelf Drama, Owned
  Sci-Fi m.fl., utan `job_id`) ligger bland Gilberts rekommendationer på NAS:en; de har inget
  premiärdatum och syns inte i Up Coming.

## Gilberts beslut och vad som gjordes (06:05–06:35 UTC)

Frågat i sessionen: (1) **driftsätt omgång 10 — ja**; (2) **annonserade titlar med bara år eller
månad räknas som kommande — ja** (titlar utan år gör det fortfarande inte).

- **`providers/premieres.py`**: `announced_date` / `premiere_from_anilist` godtar NOT_YET_RELEASED med
  år+månad (`premiere_date` = månadens sista dag) eller bara år (31 dec), med nytt fält
  `premiere_precision` (`day` / `month` / `year`, i `PREMIERE_FIELDS`); alla läsare som jämför
  `premiere_date > today` fungerar oförändrat. En TMDb-rad som TMDb inte har datum för men som har
  AniList-id frågas nu även hos AniList (Frieren S3). `premiere_label` ger "October 2026" / "2027".
- **`providers/continuations.py`**: `chain_keys` — längs en AniList PREQUEL/PARENT-kedja matchas
  också franchisen före undertiteln ("Sword Art Online: Alicization – War of Underworld Part 2" →
  gillade "Sword Art Online"; kräver minst 4 tecken före ": ", så "Re:Zero" matchar inte "Re").
  En kopplad AniList-titel ärver den gillade titelns saknade metadata (`_root_fields`, bara tomma
  fält): ONE PIECE FILM: GOD VALLEY hade inga genrer och fick personal 0,85 mot golvet 2,5 trots
  One Piece 10/10; nu 6,36. `_same_premiere` jämför år/månad-datum med TMDb:s dag inom perioden.
  Varför-texterna använder `premiere_label`.
- `server.upcoming_request_card` och `request_queue.LIGHT_FIELDS` bär `premiere_precision`;
  **Home** `premiereLabel` visar "Oct 2026" / "2027" för sådana (bara text, lås OK).
- Tester: `recommendation_tests` + `evaluation_tests` **217 gröna** (nya i
  `test_upcoming_premieres.py` och `test_upcoming_search.py`), `tests/…` 39 gröna. Offline
  (`snap_live.json`, 8 veck): oförändrat — holdout P@5 0,400, NDCG@5 0,371, NDCG@10 0,336,
  parvis 0,716; kronologiskt P@5 0,400, NDCG@5 0,470.
- `job_trace` mot NAS-databasen före driftsättning: Upcoming Tv Shows 12 → 18 val (anime 5 → 11:
  Frieren S3, Dandadan S3, One Punch Man S3 del 2, Sakamoto Days S2, Shield Hero S5, Slime S4 del 3);
  anime förbi filtren 44 → 145. Upcoming US 39 → 47 (anime 5 → 13, animefilmer 0 → 2). Ett tänkt
  jobb med bara animefilmer: 19 förbi filtren; SAO: Integral Domain, One Piece Film: God Valley och
  BAAD klarar golvet, övriga saknar koppling till något gillat och hålls ute som förut.
- **Säkerhetskopia** före driftsättning: `.runtime/backups/2026-09-26-omgang12/`
  (jobs 4, requests 10 544, recommendations 94 rader; `manifest.md5`).
- **Driftsatt 06:15 UTC** (`scripts/deploy_nas.sh`); md5 för premieres/continuations/upcoming i
  containern = arbetsytan; `/api/` 200, inga fel i loggen. `python3 -m jobs.upcoming --user
  user_c30bd548254a` i containern: 6 000 köer fick premiärfält, 139 med kommande premiär.
- **Verifierat live:** Upcoming Tv Shows 06:15:42 → 18 val (var 0 i varje körning i natt);
  Upcoming US 06:17:18 → 33 val (var 11), bl.a. Avengers: Doomsday, Dune: Part Three, SAO: Integral
  Domain, One Piece Film: God Valley — `queue_full` håller fortfarande tillbaka 58 från Requests
  (3 723 väntar mot 650; Gilberts beslut). `upcoming_premieres` anropad i containern: **142 kort**
  (50 TV, 50 film — taket per sort —, 42 anime), var 12 utan anime.
- Inte gjort: inget committat (omgång 9, 10 och 12 ligger i arbetsytan). Gilberts inloggade vy av
  Home inte sedd (sessionen loggar inte in).

## Omdriftsättning och kontroll live (2026-09-26 06:42–06:50 UTC)

Gilbert: "driftsätt omgång 10 nu … verifiera". Arbetsytan och NAS:en var redan identiska (md5 för
hela backend, även i containern) efter driftsättningen 06:15; `scripts/deploy_nas.sh` kördes igen
06:43. Förkontroller: 217 + 42 tester gröna, låset OK, `cinemind` beror bara på `cinemind-mongo`
(MediaManager rörs inte; avsnittsscanner-sessionen tillfrågad). Säkerhetskopia:
`.runtime/backups/2026-09-26-omgang10-redeploy/` (requests 10 558, jobs; `manifest.md5`).

- Containrar: `mediamanager_cinemind` healthy, `restart=unless-stopped`, bilden byggd från
  `/vol2/1000/MediaManager/cinemind` (överlever omstart); `/api/`, `/` och `localhost:8001` 200.
  MediaManager: `/api/v1/health` och `/web/` 200 efter min deploy och efter avsnittsscanner-
  sessionens ombyggnad 06:47. MediaManagers logg har egna fel (Plex-token 401 → Gilbert loggar in
  på Plex i MediaManager; tv-import OSError) — rapporterade till den sessionen, inte ändrade.
- `python3 -m jobs.upcoming --user user_c30bd548254a`: 0 nya (alla fick fält 06:15), 139 köer med
  kommande premiär.
- Schemalagt efter deploy: Upcoming Tv Shows 06:45 → 14 val av 1 086 (fortsättningar: 6 säsonger,
  6 anime, 7 AniList-kopplade), 19 öppna val (`carry_over`); Upcoming US 06:47 → alla fyra steg
  (`job_lanes` → `taste_window` → `deeper_pages` → `other_sources`), 3 nya mot målet 20,
  `queue_full` håller tillbaka 72 (3 723 väntar mot 650, Gilberts beslut). Modell qwen-suggestarr.
- `upcoming_premieres(limit=50)`: 143 kort (`upcoming_kind` tv 50 / movie 50 / anime 43), inget
  passerat datum, soonest-first per sort, 13 fortsättningskort ("season 38 of The Simpsons, which
  you rated 10/10"), 3 månads- och 6 årsdatum. `/requests/page?sort=upcoming_first`: kommande
  premiärer först (27/9 The Simpsons … 3/10 Black Clover S2).
- Fortfarande inte committat (omgång 9, 10, 12).
