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

# LEVERANS 2026-09-25 — experimentloopen avslutad

Gjort i en molnsession utan åtkomst till Macen, databasen, Ollama på NAS:en eller
Plex. Simkl, Trakt, TMDb och AniList nekas av miljöns nätverkspolicy (HTTP 403).
Allt nedan skiljer därför på **ändrad kod**, **driftsatt kod** och **verifierad
funktion**, och säger var beviset kommer ifrån.

## 18. Status och bevis

### 18.1 Experimentloopen

- Inga fler ablationer, viktoptimeringar, seed-svep, signifikanstester eller
  LLM-jämförelser har startats. Konfigurationen nedan väljs ur mätningar som
  redan fanns.
- Molnsessionen når ingen lokal Claude-session, och kontots enda schemalagda
  rutin (MediaManager-deploy till NAS:en, misslyckad 2026-09-20) rör inte
  CineMind. **Kör en loop fortfarande i en terminal på Macen måste den stoppas
  där.**
- `server.py` sätter `"job_intent": False` och hänvisar till
  `recommendation.job_intent`, som inte finns i repot — spår av ocommittad
  lokal kod. `scripts/sync_runtime.sh` kopierar arbetskatalogen som den är, så
  kör `git status` och rensa eller committa innan du driftsätter. Nyckeln är
  ofarlig här (ingen kod läser den) och står kvar så att en lokal modul som
  läser `spec.get("job_intent", True)` inte slås på för Content to Watch.

### 18.2 Konfiguration, vald ur redan gjorda mätningar

Modell `gemma4:12b-it-qat` (Gilberts val 2026-09-24), mätt 2026-09-23 med
32 veck × 3 upprepningar (n = 99 per arm) mot Gilberts riktiga ögonblicksbild:

| jämförelse | mått | Δ ± SE | |
|---|---|---|---|
| mot enbart deterministisk | nDCG@5 | +0,117 ± 0,030 | signifikant |
| mot enbart deterministisk | MRR | +0,176 ± 0,039 | signifikant |
| mot `qwen-suggestarr` | MRR | +0,087 ± 0,026 | signifikant |
| mot `qwen-suggestarr` | nDCG@5 | +0,045 ± 0,025 | inte signifikant |
| mot `qwen-suggestarr` | nDCG@10 | +0,001 | inte signifikant |
| latens per omrankning | | 5,0 s mot 1,2 s | ~4× långsammare |

Svansen: Gemma behåller plats 1–5 och den deterministiska ordningen plats 6–10
mätte **+0,022 ± 0,008 nDCG@10 (t = 2,64)**. Införd nu som
`RERANK_LLM_KEEP = 5` i `jobs/engine.py`. Modellen måste fortfarande svara med
alla 12 handtag för att svaret ska räknas; plats 1–5, och därmed hjältekortet,
är modellens.

Deterministisk offline (d698cdc, 2026-09-24): nDCG@5 **0,8931**, nDCG@10
**0,6503**. Kontrollarmarna kollapsar: `empty_taste` 0,0000, `shuffled_taste`
0,1366.

### 18.3 Kvarvarande osäkerhet i rankingmätningen

Dokumenterad, inte ett skäl att starta fler experiment.

1. **Absolutvärdena per arm från 2026-09-23 committades aldrig**, bara
   skillnaderna ovan (commit 2472c53). CLAUDE.md sade "full numbers in
   HANDOFF.md"; före detta avsnitt fanns inga Gemma-siffror här alls.
2. **Litet underlag:** 97 egna betyg ≥ 8, 5 negativa exempel. Modellordningen
   har vänt två gånger när urvalet växte — `qwen2.5:14b` vann på 9 veck och
   förlorade på 14 (§15), `qwen-suggestarr` vann på 8 veck och förlorade på 32.
3. **Svansvinsten är mätt en gång**, inte bekräftad med ett annat veckantal som
   §15 kräver för modellbyten. Den är införd eftersom plats 1–5 inte kan
   påverkas och effekten är 2,6 standardfel från noll; visar en senare mätning
   motsatsen sätts `RERANK_LLM_KEEP` till 12.
4. Gemma slår `qwen-suggestarr` **bara på MRR** (topp-1). På nDCG@5 och
   nDCG@10 går de inte att skilja åt, till fyra gånger latensen.
5. Berikad profil mäter fortfarande 0,05–0,075 P@5 under oberikad på
   holdouten, oförklarat (§14). Parvis träffsäkerhet 0,751 (från 0,808).
6. Allt är offline mot en fryst ögonblicksbild. **Ingen live-lista med
   Gemma 4 och topp-5-svansen har genererats** — det kräver Ollama på NAS:en.
   Senast verifierade live-lista är §16 (2026-09-22, `qwen-suggestarr`).

### 18.4 Ändringar i denna leverans

| fil | vad | bevis |
|---|---|---|
| `frontend/src/pages/Requests.jsx` | "Ladda fler" räknade godkända rader i offset och sentinel, så lika många köade titlar hoppades över (fel infört i 5c53e42) | Chromium, 100 köade. 5 godkända: **före** 90 av 95 synliga, `req_040`–`044` saknades, offset 0/40/80; **efter** 95 av 95, offset 0/35/75. 30 godkända: **före** 40 av 70, **efter** 70 av 70. Inga dubbletter. |
| `backend/server.py` `POST /history/sync` | Ett svar som slog i 50-taket ersätter aldrig en större lagrad historik; "inget svarade" byter aldrig lagrad historik mot demorader | 6 nya tester, 4 röda utan ändringen; `verify_live --sync` lokalt: 60 lagrade rader kvar |
| `backend/jobs/engine.py` | `RERANK_LLM_KEEP = 5` (18.2) | 2 nya tester, det ena rött utan ändringen |
| `backend/evaluation/verify_live.py` | ny: kör alla fem punkterna mot den körande appen på Macen; skrivskyddad utan `--sync`/`--generate` | provkörd lokalt mot syntetiska data |

Massåtgärderna från 5c53e42, verifierade i Chromium: "Select all" på 120 köade →
120 markerade → `POST /requests/bulk` i satser om 50/50/20 → servern har 120
avvisade respektive 120 godkända, kön 0, Approved-fliken "120 total".

### 18.5 Tester efter senaste gröna körning

Molncontainer med MongoDB 8.3.7 och lokal testserver, samma kommando som §10.

| kod | gröna | röda | errors |
|---|---|---|---|
| 463e858 (baslinje, samma miljö) | 151 | 24 | 2 |
| 5c53e42 (HEAD före ändringarna) | 173 | 25 | 2 |
| efter ändringarna | **182** | 24 | 2 |

**Mängden röda är identisk med baslinjens.** Alla kräver Simkl-, Trakt- eller
TMDb-nycklar, Ollama eller nätverk som containern saknar. Den 25:e på HEAD
(`test_pin_poll_validation`) återanvände en anslutning efter en 500 och gick
grönt 3 av 3 gånger isolerat. Frontend bygger; enda lint-varningen
(`Jobs.jsx:177`) fanns före. `check_vision_ui_lock.py` → OK.

### 18.6 Status per ursprunglig punkt

| punkt | ändrad kod | driftsatt | verifierad funktion | status |
|---|---|---|---|---|
| Anslutningstest Simkl/Plex | Simkl-enhetsflöde + NameError-fix (463e858); inget nytt | okänt härifrån; 2472c53 och 5c53e42 redovisar mätningar mot en körande instans | **Nej.** Inget lyckat Simkl- eller Plex-test finns i repot eller commit-historiken; molnet når varken Simkl (403) eller Plex (LAN) | **BLOCKERAD** — `verify_live` på Macen |
| Faktiska synkantal | skydd mot krympning (nytt) | nej | Skyddet: 6 tester. Senast uppmätta antal (2026-09-22): 10 210 `history`, 669 `media_history`, 97 betyg ≥ 8; unika titlar per källa i smakprofilen trakt 421, simkl 150 (= taket 3 × 50), anilist 43, plex 11 | **BLOCKERAD** för färska antal; full synk kräver beslut (18.7) |
| Upcoming-lista | fönsterlogik d698cdc; inget nytt | enligt commit | **Delvis.** "Upcoming Tv Shows" fyllde 12 av 12 platser (från 6) och donghua gav 74 (från 3) med `job_trace` 2026-09-24, men titlarna och datumen sparades inte. §16 listar 2024–2025-titlar för jobbet "Upcoming US + Anime 2026–2029" — antingen namnet eller filtret stämmer inte | **BLOCKERAD** — TMDb/AniList nås inte härifrån; `verify_live` prövar varje val mot jobbets årsfönster och dagens datum |
| Rankingresultat | topp-5-svans (ny) | nej | Offline ja (18.2); live senast 2026-09-22 med `qwen-suggestarr` | **Delvis** — live kräver Ollama |
| Köhantering | offset-rättelse (ny) ovanpå 5c53e42 | 5c53e42 ja enligt commit; rättelsen nej | **Ja** i Chromium på syntetiska data (18.4) + 5 enhetstester; 5c53e42 bläddrade igenom alla 9 942 köade med samma ordning som webbläsaren | **Klar**, väntar på driftsättning |

### 18.7 Fynd som inte åtgärdats

Fanns före de senaste ändringarna eller kräver ett beslut.

1. **Synken läser en sida per källa:** Trakts 50 senaste, 50 per Simkl-lista,
   50 Plex-*biblioteksobjekt* — inte tittarhistorik. Skyddet i 18.4 stoppar
   dataförlusten men ger inte fullständiga antal. Full synk (Trakt-paginering,
   Simkl utan tak, Plex-historik) ändrar datat rankingen mättes på och kan bara
   provas på Macen → **beslut**.
2. **"Up Coming" på Home** visar rekommendation 2–5, inte kommande premiärer
   (`Home.jsx`, `filtered.filter(r => r.id !== hero?.id).slice(0, 4)`) →
   **beslut** om vad panelen ska visa.
3. Simkl-flödet anropar `GET /oauth2/device` medan kommentaren citerar Simkls
   svar "use POST /oauth2/device". Oprövat mot Simkl.
4. `/simkl/pin/poll` och `/trakt/device/poll` svarar 500 vid nätverksfel (och
   Trakt-poll när client id saknas) i stället för en status.
5. `TestHistoryPosters` läser en hårdkodad `test_database` — testfel.
6. `ProvenanceChips.jsx` renderas fortfarande ingenstans (UI-ändring kräver
   Gilberts godkännande).
7. `GET /requests` och `/requests/stats` läser högst 30 000 rader.

### 18.8 Slutför på Macen

```bash
cd ~/Documents/CineMind
git status                     # ocommittat från loopen? rensa innan driftsättning
git fetch origin && git checkout claude/great-davinci-zlc97t
cd frontend && PATH="$PWD/../.runtime/node/bin:$PATH" npm run build && cd ..
bash scripts/sync_runtime.sh
cd backend
PYTHONPATH=../.runtime/python:. python3 -m evaluation.verify_live \
  --user user_c30bd548254a --output /tmp/verify.json
# --sync trycker Sync (nu skyddat), --generate genererar ny Content to Watch
```
