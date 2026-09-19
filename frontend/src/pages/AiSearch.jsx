import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Search, Loader2 } from "lucide-react";
import RecModal from "@/components/RecModal";
import FeedbackChips from "@/components/FeedbackChips";
import { POSTER_GRID, MatchStat } from "@/components/ApproveRejectOverlay";

export default function AiSearch() {
  const [params] = useSearchParams();
  const q = params.get("q") || "";
  const [items, setItems] = useState([]);
  const [intent, setIntent] = useState(null);
  const [provider, setProvider] = useState(null);
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState(null);

  useEffect(() => {
    if (!q.trim()) return;
    setLoading(true);
    api.post("/search/ai", { query: q })
      .then((r) => {
        setItems(r.data.results || []);
        setIntent(r.data.intent);
        setProvider(r.data.provider);
      })
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  }, [q]);

  const handleFeedback = (id, action) => {
    if (action === "hide" || action === "blacklist" || action === "watched") {
      setItems((list) => list.filter((r) => r.id !== id));
      setActive(null);
    }
  };

  const save = async (id) => {
    await api.post(`/recommendations/${id}/save`);
    toast.success("Saved to library");
  };

  return (
    <div className="px-1 sm:px-2 pb-4 float-in">
      <span className="chip chip-rose mb-4">AI Search</span>
      <h1 className="font-display text-4xl sm:text-5xl font-extrabold tracking-tight">{q || "Search"}</h1>
      <p className="text-slate-400 mt-2 max-w-xl">The same candidate pipeline as jobs, filtered from your phrasing. Local titles stay on this machine.</p>

      {intent && (
        <div className="flex flex-wrap gap-2 mt-6">
          {provider && <span className="chip chip-rose">{provider}</span>}
          {(intent.media_types || []).map((item) => <span key={item} className="chip">{item}</span>)}
          {(intent.include_genres || []).map((item) => <span key={item} className="chip chip-cyan">{item}</span>)}
          {intent.search_text ? <span className="chip">{intent.search_text}</span> : null}
          {intent.min_year && intent.max_year ? (
            <span className="chip chip-amber">{intent.min_year}–{intent.max_year}</span>
          ) : intent.min_year ? (
            <span className="chip chip-amber">after {intent.min_year}</span>
          ) : null}
        </div>
      )}

      {loading && (
        <div className="glass rounded-2xl p-16 text-center mt-10">
          <Loader2 className="w-8 h-8 mx-auto animate-spin text-slate-500" />
        </div>
      )}

      <div className={`${POSTER_GRID} mt-10`}>
        {items.map((rec, index) => (
          <div key={rec.id || `${rec.title}-${rec.year}`} className="text-left group float-in" style={{ animationDelay: `${index * 60}ms` }}>
            <button type="button" onClick={() => setActive(rec)} className="text-left w-full">
              <div className="poster-frame relative rounded-2xl">
                {rec.poster ? (
                  <img src={rec.poster} alt={rec.title} className="aspect-[2/3] w-full object-cover poster-hover" />
                ) : (
                  <div className="aspect-[2/3] w-full glass grid place-items-center"><Search className="w-8 h-8 text-slate-600" /></div>
                )}
                {rec.match_score ? (
                  <div className="absolute top-3 left-3">
                    <MatchStat score={rec.match_score} />
                  </div>
                ) : null}
              </div>
            </button>
            <div className="mt-3 flex items-baseline justify-between gap-2">
              <h3 className="font-display font-bold text-base line-clamp-1">{rec.title}</h3>
              <span className="font-mono text-xs text-slate-500 shrink-0">{rec.year}</span>
            </div>
            <div className="mt-2">
              <FeedbackChips rec={rec} onFeedback={handleFeedback} testidPrefix="search" />
            </div>
          </div>
        ))}
      </div>

      {!loading && !items.length && q && (
        <div className="glass rounded-2xl p-16 text-center mt-10">
          <Search className="w-10 h-10 mx-auto text-slate-600 mb-4" />
          <p className="text-slate-400">No pipeline matches for that phrasing yet.</p>
        </div>
      )}

      {active && (
        <RecModal
          rec={{ ...active, id: active.id || `${active.title}-${active.year}`, poster: active.poster || "", genres: active.genres || [], synopsis: active.synopsis || "", match_score: active.match_score || 0, tmdb_rating: active.tmdb_rating || 0 }}
          onClose={() => setActive(null)}
          onSave={save}
          onFeedback={handleFeedback}
        />
      )}
    </div>
  );
}
