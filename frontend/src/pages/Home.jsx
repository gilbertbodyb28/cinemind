import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import { setAmbient } from "@/lib/ambient";
import { toast } from "sonner";
import { Play, Flame, ChevronLeft, ChevronRight, Sparkles, Loader2, Bookmark } from "lucide-react";
import RecModal from "@/components/RecModal";
import WatchlistButton from "@/components/WatchlistButton";
import ApproveButton from "@/components/ApproveButton";
import { RecPosterActions, POSTER_GRID, RejectButton, rejectRecommendation, MatchStat, RatingStat } from "@/components/ApproveRejectOverlay";
import { useSearch } from "@/context/UiContext";
import { notifyUsage, providerLabel } from "@/lib/models";

const TRAILER_SORTS = [
  { key: "match", label: "Best match" },
  { key: "year", label: "Newest" },
  { key: "rating", label: "Top rated" },
];

// "Season 4 · Oct 12, 2026": what premieres, and the verified day (GET /upcoming).
function premiereLabel(r) {
  if (!r.premiere_date) return "";
  const day = new Date(`${r.premiere_date}T00:00:00Z`).toLocaleDateString(undefined, {
    day: "numeric", month: "short", year: "numeric", timeZone: "UTC",
  });
  const what = r.premiere_kind === "season_premiere" && r.premiere_season
    ? `Season ${r.premiere_season}`
    : r.premiere_kind === "series_premiere" ? "Series premiere" : "Release";
  return `${what} · ${day}`;
}

export default function Home() {
  const [recs, setRecs] = useState([]);
  const [coming, setComing] = useState([]);
  const [tab, setTab] = useState("all");
  const [comingKind, setComingKind] = useState(() => {
    try { return localStorage.getItem("cm-upcoming-kind") || "all"; } catch { return "all"; }
  });
  const chooseComingKind = (value) => {
    setComingKind(value);
    try { localStorage.setItem("cm-upcoming-kind", value); } catch { /* per-viewer convenience only */ }
  };
  const [heroIdx, setHeroIdx] = useState(0);
  const [active, setActive] = useState(null);
  const [sort, setSort] = useState("match");
  const [generating, setGenerating] = useState(false);
  const { q } = useSearch();

  const load = async () => {
    const r = await api.get("/recommendations");
    setRecs(r.data);
    // Up Coming is its own list: picks with a verified premiere after today, soonest first.
    try {
      const u = await api.get("/upcoming", { params: { limit: 50 } });
      setComing(u.data || []);
    } catch {
      setComing([]);
    }
  };

  useEffect(() => { load(); }, []);

  const topGenres = useMemo(() => {
    const c = {};
    recs.forEach((r) => (r.genres || []).forEach((g) => {
      const k = g.trim().toLowerCase();
      c[k] = c[k] || { label: g.trim(), n: 0 };
      c[k].n += 1;
    }));
    return Object.entries(c).sort((a, b) => b[1].n - a[1].n).slice(0, 4).map(([key, v]) => ({ key, label: v.label }));
  }, [recs]);

  const tabs = useMemo(() => [
    { key: "all", label: "Movies & Shows" },
    { key: "movie", label: "Movies" },
    { key: "show", label: "TV Series" },
    ...topGenres.map((g) => ({ key: `g:${g.key}`, label: g.label.replace(/\b\w/g, (c) => c.toUpperCase()) })),
  ], [topGenres]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return recs.filter((r) => {
      if (tab === "movie" || tab === "show") { if (r.type !== tab) return false; }
      else if (tab.startsWith("g:")) {
        const g = tab.slice(2);
        if (!(r.genres || []).some((x) => x.trim().toLowerCase() === g)) return false;
      }
      if (needle && !r.title.toLowerCase().includes(needle)) return false;
      return true;
    });
  }, [recs, tab, q]);

  useEffect(() => { setHeroIdx(0); }, [tab, q]);

  const hero = filtered[heroIdx] || null;

  // The ambient wash stays on when you leave Home, so every tab shares the look.
  useEffect(() => {
    const img = hero?.backdrop || hero?.poster;
    if (img) setAmbient(img);
  }, [hero]);

  const trailerList = useMemo(() => {
    const by = {
      match: (a, b) => (b.match_score ?? 0) - (a.match_score ?? 0),
      year: (a, b) => (b.year ?? 0) - (a.year ?? 0),
      rating: (a, b) => (b.tmdb_rating ?? 0) - (a.tmdb_rating ?? 0),
    }[sort];
    return [...filtered].sort(by).slice(0, 5);
  }, [filtered, sort]);

  const upcoming = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return coming.filter((r) => {
      if (comingKind !== "all" && r.upcoming_kind !== comingKind) return false;
      if (tab === "movie" || tab === "show") { if (r.type !== tab) return false; }
      else if (tab.startsWith("g:")) {
        const g = tab.slice(2);
        if (!(r.genres || []).some((x) => x.trim().toLowerCase() === g)) return false;
      }
      if (needle && !r.title.toLowerCase().includes(needle)) return false;
      return true;
    }).slice(0, 4);
  }, [coming, tab, q, comingKind]);

  const save = async (id) => {
    await api.post(`/recommendations/${id}/save`);
    setRecs((list) => list.map((r) => (r.id === id ? { ...r, saved: !r.saved } : r)));
    setActive((a) => (a && a.id === id ? { ...a, saved: !a.saved } : a));
    toast.success("Saved to library");
  };

  const markWatchlisted = (id) => {
    setRecs((list) => list.map((r) => (r.id === id ? { ...r, in_watchlist: true } : r)));
    setActive((a) => (a && a.id === id ? { ...a, in_watchlist: true } : a));
  };

  const markApproved = (id) => {
    setRecs((list) => list.map((r) => (r.id === id ? { ...r, in_library: true } : r)));
    setActive((a) => (a && a.id === id ? { ...a, in_library: true } : a));
  };

  const dismiss = async (id) => {
    setRecs((list) => list.filter((r) => r.id !== id));
    setComing((list) => list.filter((r) => r.id !== id));
    setActive((a) => (a && a.id === id ? null : a));
  };

  const rejectHero = async (event) => {
    event?.stopPropagation();
    if (!hero) return;
    try {
      await rejectRecommendation(hero);
      dismiss(hero.id);
    } catch (error) {
      toast.error(error?.message || "Could not reject");
    }
  };

  const generate = async () => {
    setGenerating(true);
    try {
      const r = await api.post("/recommendations/generate", {});
      toast.success(`${r.data.count} picks via ${providerLabel(r.data.provider, r.data.model)}`);
      notifyUsage();
      await load();
    } catch { toast.error("Generation failed"); }
    finally { setGenerating(false); }
  };

  const cycle = (dir) => {
    if (!filtered.length) return;
    setHeroIdx((i) => (i + dir + filtered.length) % filtered.length);
  };

  return (
    <div data-testid="home-page" className="float-in">
      {/* Category tabs */}
      <div data-testid="category-tabs" className="flex items-center gap-1.5 overflow-x-auto scroll-thin pb-1 mb-5">
        {tabs.map((t) => (
          <button
            key={t.key}
            data-testid={`category-tab-${t.key.replace(/[^a-z0-9]+/gi, "-")}`}
            onClick={() => setTab(t.key)}
            className={`shrink-0 rounded-full px-4 py-2 text-sm font-medium transition-colors ${
              tab === t.key
                ? "bg-[rgba(216,178,106,0.18)] text-[#EBD3A3] border border-[rgba(216,178,106,0.4)]"
                : "text-[#A5987F] border border-transparent hover:text-[#F6EFE4] hover:bg-white/[0.05]"
            }`}
          >
            {t.label}
          </button>
        ))}
        <Link data-testid="category-tab-more" to="/recommendations" className="shrink-0 rounded-full px-4 py-2 text-sm font-medium text-[#A5987F] hover:text-[#F6EFE4] hover:bg-white/[0.05] transition-colors">More</Link>
      </div>

      {!recs.length ? (
        <div data-testid="home-empty" className="glass rounded-3xl p-14 text-center">
          <Sparkles className="w-10 h-10 mx-auto text-[#6E6355] mb-4" />
          <h2 className="font-display text-2xl font-bold">Your screen is empty</h2>
          <p className="text-slate-400 mt-2 text-sm max-w-md mx-auto">Generate your first batch of AI picks — CineMind reads your Trakt, Simkl and Plex history to find what you'll actually love.</p>
          <button data-testid="home-generate-button" onClick={generate} disabled={generating} className="mt-7 glass-strong px-6 py-3 rounded-full text-sm font-medium inline-flex items-center gap-2 hover:brutal-shadow-rose transition-shadow">
            {generating ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
            {generating ? "Thinking…" : "Generate picks"}
          </button>
        </div>
      ) : (
        <div className="grid lg:grid-cols-[300px_1fr] gap-4 lg:gap-5">
          {/* ---- New Trailer ---- */}
          {/* Spans both grid rows so the trailer list runs to the bottom of the page. */}
          <section data-testid="new-trailer-panel" className="glass rounded-3xl p-4 flex flex-col lg:row-span-2 lg:self-stretch">
            <div className="flex items-center justify-between gap-2 mb-3">
              <div className="flex items-center gap-2">
                <Flame className="w-4 h-4 text-[#D8B26A]" />
                <h2 className="font-display text-base font-semibold">New Trailer</h2>
              </div>
              <label className="flex items-center gap-1 text-[11px] text-[#8C7F6D]">
                sort by:
                <select
                  data-testid="trailer-sort-select"
                  value={sort}
                  onChange={(e) => setSort(e.target.value)}
                  className="bg-transparent outline-none text-[#DCCFBC] cursor-pointer"
                >
                  {TRAILER_SORTS.map((s) => (
                    <option key={s.key} value={s.key} className="bg-[#17130F]" label={s.label}>{s.label}</option>
                  ))}
                </select>
              </label>
            </div>

            <div className="space-y-3 overflow-y-auto scroll-thin flex-1 min-h-[460px] pr-1">
              {trailerList.map((r, i) => (
                <button
                  key={r.id}
                  data-testid={`trailer-item-${r.id}`}
                  onClick={() => setActive(r)}
                  className="group relative block w-full rounded-2xl overflow-hidden border border-white/10 text-left"
                >
                  <img src={r.backdrop || r.poster} alt={r.title} className="w-full h-[112px] object-cover opacity-85 group-hover:opacity-100 transition-opacity" />
                  <div className="absolute inset-0 bg-gradient-to-t from-black/85 via-black/25 to-transparent" />
                  <div className="absolute inset-x-3 bottom-2.5 flex items-end justify-between gap-2">
                    <div className="min-w-0">
                      <div className="font-medium text-sm truncate">{r.title}</div>
                      <div className="text-[11px] text-[#A5987F]">{i + 1}{["st", "nd", "rd"][i] || "th"} Trailer · {r.year}</div>
                    </div>
                    <span className="w-8 h-8 rounded-full glass-strong grid place-items-center shrink-0 group-hover:brutal-shadow-rose transition-shadow">
                      <Play className="w-3.5 h-3.5 fill-current ml-0.5" />
                    </span>
                  </div>
                </button>
              ))}
              {!trailerList.length && <p className="text-xs text-[#8C7F6D] py-6 text-center">Nothing matches this filter.</p>}
            </div>
          </section>

          {/* ---- Hero ---- */}
          <section data-testid="hero-panel" className="relative rounded-3xl overflow-hidden border border-white/10 min-h-[420px]">
            {hero ? (
              <>
                <img key={hero.id} src={hero.backdrop || hero.poster} alt={hero.title} className="absolute inset-0 w-full h-full object-cover" />
                <div className="absolute inset-0 bg-gradient-to-r from-[#0B0907]/95 via-[#0B0907]/60 to-transparent" />
                <div className="absolute inset-0 bg-gradient-to-t from-[#0B0907]/85 via-transparent to-transparent" />

                <div className="absolute top-4 left-4">
                  <span className="chip glass-strong !border-white/15"><Flame className="w-3 h-3 text-[#D8B26A]" /> Trends</span>
                </div>

                <div className="relative z-10 p-6 sm:p-8 lg:p-10 max-w-2xl h-full flex flex-col justify-end min-h-[420px]">
                  <div className="flex flex-wrap gap-1.5 mb-4">
                    {(hero.genres || []).slice(0, 4).map((g, i) => (
                      <span key={i} className="rounded-full px-3 py-1 text-[11px] border border-white/15 bg-white/[0.07] backdrop-blur-md text-[#DCCFBC]">{g}</span>
                    ))}
                  </div>
                  <h1 data-testid="hero-title" className="font-display text-4xl sm:text-5xl font-extrabold tracking-tight leading-none">{hero.title}</h1>
                  <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-[#A5987F]">
                    <span>{hero.type === "show" ? "TV Series" : "Movie"} {hero.year}</span>
                    <MatchStat score={hero.match_score} />
                    <RatingStat rating={hero.tmdb_rating} />
                  </div>
                  <p className="mt-4 text-sm sm:text-base text-slate-300 leading-relaxed line-clamp-3 max-w-xl">{hero.why || hero.synopsis}</p>

                  <div className="mt-7 flex flex-wrap items-center gap-3">
                    <button data-testid="hero-watch-button" onClick={() => setActive(hero)} className="px-6 py-3 rounded-full bg-[rgba(255,246,232,0.12)] border border-white/20 backdrop-blur-xl text-sm font-medium flex items-center gap-2 hover:bg-[rgba(255,246,232,0.2)] transition-colors">
                      <Play className="w-4 h-4 fill-current" /> Watch trailer
                    </button>
                    <WatchlistButton rec={hero} variant="pill" onDone={markWatchlisted} />
                    <ApproveButton rec={hero} variant="pill" onDone={markApproved} />
                    {!hero.in_library && (
                      <RejectButton testid={`reject-request-${hero.id}`} onClick={rejectHero} />
                    )}
                    <button data-testid="hero-save-button" onClick={() => save(hero.id)} title={hero.saved ? "Saved" : "Save to library"} className={`w-11 h-11 rounded-full grid place-items-center border transition-colors ${hero.saved ? "bg-[rgba(216,178,106,0.25)] border-[rgba(216,178,106,0.5)] text-[#EBD3A3]" : "border-white/20 bg-white/[0.07] hover:bg-white/[0.14]"}`}>
                      <Bookmark className={`w-4 h-4 ${hero.saved ? "fill-current" : ""}`} />
                    </button>
                  </div>
                </div>

                <div className="absolute bottom-6 right-6 flex items-center gap-2 z-10">
                  <button data-testid="hero-prev-button" onClick={() => cycle(-1)} className="w-11 h-11 rounded-full glass grid place-items-center hover:bg-white/[0.12] transition-colors"><ChevronLeft className="w-4 h-4" /></button>
                  <button data-testid="hero-next-button" onClick={() => cycle(1)} className="w-11 h-11 rounded-full glass grid place-items-center hover:bg-white/[0.12] transition-colors"><ChevronRight className="w-4 h-4" /></button>
                </div>
              </>
            ) : (
              <div className="absolute inset-0 grid place-items-center text-sm text-[#8C7F6D]">Nothing matches this filter.</div>
            )}
          </section>

          {/* ---- Up Coming ---- */}
          <section data-testid="upcoming-panel">
            <div className="flex items-center justify-between gap-3 mb-4 px-1">
              <h2 className="font-display text-2xl font-bold">Up Coming</h2>
              <label className="relative inline-flex items-center glass rounded-full pl-3 pr-3 py-1.5 text-sm cursor-pointer hover:border-[rgba(216,178,106,0.4)] transition-colors ml-auto">
                <select
                  data-testid="upcoming-kind-select"
                  aria-label="Filter Up Coming"
                  value={comingKind}
                  onChange={(e) => chooseComingKind(e.target.value)}
                  className="appearance-none bg-transparent outline-none font-medium text-[#F6EFE4] cursor-pointer"
                >
                  <option value="all" className="bg-[#17130F] text-[#F6EFE4]">All</option>
                  <option value="anime" className="bg-[#17130F] text-[#F6EFE4]">Anime</option>
                  <option value="tv" className="bg-[#17130F] text-[#F6EFE4]">TV series</option>
                  <option value="movie" className="bg-[#17130F] text-[#F6EFE4]">Movies</option>
                </select>
              </label>
              <Link data-testid="upcoming-see-all" to="/recommendations" className="chip hover:chip-rose transition-colors">See all</Link>
            </div>
            <div className={POSTER_GRID}>
              {upcoming.map((r) => (
                <div key={r.id} data-testid={`upcoming-card-${r.id}`} className="group relative text-left">
                  <div className="poster-frame relative rounded-3xl">
                    <button type="button" onClick={() => setActive(r)} className="block w-full text-left">
                      <img src={r.poster} alt={r.title} className="w-full aspect-[2/3] object-cover" />
                      <div className="absolute top-3 left-3 z-10 flex flex-col items-start gap-1.5">
                        <MatchStat score={r.match_score} />
                        <RatingStat rating={r.tmdb_rating} />
                      </div>
                    </button>
                    <RecPosterActions rec={r} onApproved={markApproved} onRejected={dismiss} />
                  </div>
                  <h3 className="font-display font-bold text-xl truncate mt-3">{r.title}</h3>
                  <p data-testid={`upcoming-date-${r.id}`} className="text-[11px] text-[#EBD3A3] mt-1">{premiereLabel(r)}</p>
                  <p className="text-[11px] text-[#A5987F] mt-1 line-clamp-2 leading-snug">{r.synopsis}</p>
                </div>
              ))}
            </div>
            {!upcoming.length && (
              <div data-testid="upcoming-empty" className="glass rounded-3xl p-6 text-sm text-[#8C7F6D]">
                {comingKind === "all"
                  ? "No verified premieres coming up among your picks yet."
                  : `No verified ${{ anime: "anime", tv: "TV series", movie: "movie" }[comingKind]} premieres coming up among your picks yet.`}
              </div>
            )}
          </section>
        </div>
      )}

      {active && <RecModal rec={active} onClose={() => setActive(null)} onSave={save} onWatchlist={markWatchlisted} onApproved={markApproved} onDismiss={dismiss} />}
    </div>
  );
}
