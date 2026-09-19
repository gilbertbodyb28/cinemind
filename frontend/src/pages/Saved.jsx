import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { Bookmark, Search, ArrowUpDown } from "lucide-react";
import RecModal from "@/components/RecModal";
import WatchlistButton from "@/components/WatchlistButton";
import { toast } from "sonner";
import { MODELS, modelLabel } from "@/lib/models";
import { POSTER_GRID, SendToLibraryButton, MatchStat, RatingStat } from "@/components/ApproveRejectOverlay";

const TYPES = [{ key: "all", label: "All" }, { key: "movie", label: "Movies" }, { key: "show", label: "Shows" }];
const SORTS = [{ key: "saved", label: "Recently saved" }, { key: "match", label: "Match score" }, { key: "rating", label: "TMDB rating" }];

export default function Saved() {
  const [items, setItems] = useState([]);
  const [active, setActive] = useState(null);
  const [q, setQ] = useState("");
  const [type, setType] = useState("all");
  const [genre, setGenre] = useState(null);
  const [model, setModel] = useState(null);
  const [sort, setSort] = useState("saved");

  const load = async () => {
    const r = await api.get("/recommendations/saved");
    setItems(r.data);
  };

  useEffect(() => { load(); }, []);

  const unsave = async (id) => {
    await api.post(`/recommendations/${id}/unsave`);
    setItems(list => list.filter(r => r.id !== id));
    toast("Removed from library");
  };

  const markWatchlisted = (id) => {
    setItems(list => list.map(r => r.id === id ? { ...r, in_watchlist: true } : r));
    setActive(a => a && a.id === id ? { ...a, in_watchlist: true } : a);
  };

  const markApproved = (id) => {
    setItems(list => list.map(r => r.id === id ? { ...r, in_library: true } : r));
    setActive(a => a && a.id === id ? { ...a, in_library: true } : a);
  };

  const norm = (g) => g.trim().toLowerCase();
  const genres = useMemo(() => {
    const c = {};
    items.forEach(r => (r.genres || []).forEach(g => { const k = norm(g); c[k] = c[k] || { label: g.trim(), n: 0 }; c[k].n += 1; }));
    return Object.entries(c).sort((a, b) => b[1].n - a[1].n).map(([key, v]) => ({ key, label: v.label }));
  }, [items]);

  const models = useMemo(() => {
    const present = new Set(items.map(r => r.model || "demo"));
    return [...MODELS.filter(m => present.has(m.key)).map(m => ({ key: m.key, label: m.label })), ...(present.has("demo") ? [{ key: "demo", label: "Demo picks" }] : [])];
  }, [items]);

  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const list = items.filter(r =>
      (type === "all" || r.type === type) &&
      (!genre || (r.genres || []).some(g => norm(g) === genre)) &&
      (!model || (r.model || "demo") === model) &&
      (!needle || r.title.toLowerCase().includes(needle))
    );
    const by = {
      saved: (a, b) => (b.saved_at || "").localeCompare(a.saved_at || ""),
      match: (a, b) => (b.match_score ?? 0) - (a.match_score ?? 0),
      rating: (a, b) => (b.tmdb_rating ?? 0) - (a.tmdb_rating ?? 0),
    }[sort];
    return [...list].sort(by);
  }, [items, q, type, genre, model, sort]);

  return (
    <div className="px-1 sm:px-2 pb-4 float-in">
      <span className="chip chip-emerald mb-4">Library</span>
      <h1 className="font-display text-4xl sm:text-5xl font-extrabold tracking-tight">Saved picks</h1>
      <p className="text-slate-400 mt-2 max-w-xl">Your bookmarked recommendations — the queue you're actually going to watch.</p>

      {items.length > 0 && (
        <div data-testid="saved-filters" className="mt-8 glass rounded-2xl p-4 lg:p-5 space-y-4">
          <div className="flex flex-col lg:flex-row gap-3 lg:items-center">
            <label className="flex items-center gap-2 flex-1 bg-white/[0.03] border border-white/10 rounded-full px-4 py-2 focus-within:border-rose-500/50 transition-colors">
              <Search className="w-4 h-4 text-slate-500" />
              <input data-testid="saved-search-input" value={q} onChange={e => setQ(e.target.value)} placeholder="Search your library" className="bg-transparent outline-none text-sm w-full" />
            </label>
            <div className="flex items-center gap-1.5" data-testid="saved-type-filter">
              {TYPES.map(t => <Chip key={t.key} on={type === t.key} onClick={() => setType(t.key)} testid={`saved-type-${t.key}`}>{t.label}</Chip>)}
            </div>
            <label className="flex items-center gap-2 chip cursor-pointer">
              <ArrowUpDown className="w-3 h-3" />
              <select data-testid="saved-sort-select" value={sort} onChange={e => setSort(e.target.value)} className="bg-transparent outline-none text-xs font-medium cursor-pointer">
                {SORTS.map(s => { const t = s.label; return <option key={s.key} value={s.key} className="bg-[#17130F]" label={t}>{t}</option>; })}
              </select>
            </label>
          </div>

          <div className="flex flex-wrap gap-1.5 items-center" data-testid="saved-genre-filter">
            <span className="text-[10px] font-mono uppercase tracking-widest text-slate-500 mr-1">Genre</span>
            <Chip on={!genre} onClick={() => setGenre(null)} testid="saved-genre-all">Any</Chip>
            {genres.map(g => <Chip key={g.key} on={genre === g.key} onClick={() => setGenre(genre === g.key ? null : g.key)} testid={`saved-genre-${g.key.replace(/[^a-z0-9]+/g, "-")}`} tone="chip-cyan">{g.label}</Chip>)}
          </div>

          {models.length > 1 && (
            <div className="flex flex-wrap gap-1.5 items-center" data-testid="saved-model-filter">
              <span className="text-[10px] font-mono uppercase tracking-widest text-slate-500 mr-1">Picked by</span>
              <Chip on={!model} onClick={() => setModel(null)} testid="saved-model-all">Any</Chip>
              {models.map(m => <Chip key={m.key} on={model === m.key} onClick={() => setModel(model === m.key ? null : m.key)} testid={`saved-model-${m.key}`} tone="chip-amber">{m.label}</Chip>)}
            </div>
          )}

          <div data-testid="saved-count" className="text-xs font-mono text-slate-500">{visible.length} of {items.length} saved</div>
        </div>
      )}

      <div className={`mt-8 ${POSTER_GRID}`}>
        {visible.map(r => (
          <div key={r.id} data-testid={`saved-item-${r.id}`} className="group cursor-pointer" onClick={() => setActive(r)}>
            <div className="poster-frame relative rounded-2xl">
              <img src={r.poster} alt={r.title} className="aspect-[2/3] w-full object-cover poster-hover" />
              <div className="absolute top-3 left-3 z-10 flex flex-col items-start gap-1.5">
                <MatchStat score={r.match_score} />
                <RatingStat rating={r.tmdb_rating} />
              </div>
              <div className="absolute top-3 right-3 z-30" onClick={(e) => e.stopPropagation()}>
                <SendToLibraryButton rec={r} onDone={markApproved} />
              </div>
              <div className="absolute bottom-3 inset-x-3 flex items-center justify-between opacity-0 group-hover:opacity-100 transition-opacity">
                <WatchlistButton rec={r} onDone={markWatchlisted} className="!rounded-full !px-2.5 !py-2.5" />
                <button data-testid={`unsave-button-${r.id}`} onClick={(e)=>{e.stopPropagation(); unsave(r.id);}} className="w-9 h-9 rounded-full bg-rose-500 grid place-items-center">
                  <Bookmark className="w-4 h-4 fill-current text-white"/>
                </button>
              </div>
            </div>
            <div className="mt-3">
              <h3 className="font-display font-bold text-base line-clamp-1">{r.title}</h3>
              <div className="text-xs text-slate-500 font-mono mt-0.5 flex items-center gap-1.5">
                <span>{r.year} · {r.type}</span>
                {r.model && <span className="text-slate-600">· {modelLabel(r.model)}</span>}
              </div>
            </div>
          </div>
        ))}
      </div>

      {!items.length && (
        <div className="glass rounded-2xl p-16 text-center mt-10">
          <Bookmark className="w-10 h-10 mx-auto text-slate-600 mb-4" />
          <p className="text-slate-400">No saved picks yet — save a few from <span className="text-white">Recommendations</span>.</p>
        </div>
      )}
      {items.length > 0 && !visible.length && (
        <div data-testid="saved-empty-filtered" className="glass rounded-2xl p-12 text-center mt-4">
          <p className="text-slate-400">Nothing matches these filters.</p>
        </div>
      )}

      {active && <RecModal rec={active} onClose={() => setActive(null)} onSave={() => {}} onWatchlist={markWatchlisted} onApproved={markApproved} />}
    </div>
  );
}

function Chip({ on, onClick, children, testid, tone = "chip-rose" }) {
  return (
    <button data-testid={testid} onClick={onClick} className={`chip transition-colors ${on ? tone : "hover:bg-white/10"}`}>{children}</button>
  );
}
