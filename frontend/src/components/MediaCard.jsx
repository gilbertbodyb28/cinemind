import { Bookmark, Info, X } from "lucide-react";
import WatchlistButton from "@/components/WatchlistButton";
import { RecPosterActions, MatchStat, RatingStat } from "@/components/ApproveRejectOverlay";

export default function MediaCard({
  recommendation,
  onOpen,
  onSave,
  onDismiss,
  onUpdated,
}) {
  const rec = recommendation;

  return (
    <article data-testid={`movie-card-${rec.id}`} className="group relative">
      <div className="poster-frame relative rounded-2xl">
        <img src={rec.poster} alt={rec.title} className="aspect-[2/3] w-full object-cover poster-hover" />
        <div className="absolute top-3 left-3 z-10 flex flex-col items-start gap-1.5">
          <MatchStat score={rec.match_score || 0} />
          <RatingStat rating={rec.tmdb_rating} />
        </div>
        <RecPosterActions
          rec={rec}
          onApproved={() => onUpdated?.({ ...rec, in_library: true })}
          onRejected={onDismiss ? () => onDismiss(rec) : undefined}
        />
        <div className="absolute top-[9.75rem] inset-x-3 opacity-0 group-hover:opacity-100 transition-opacity duration-300 flex items-center gap-2 z-20">
          {onSave ? (
            <button
              data-testid={`save-recommendation-button-${rec.id}`}
              onClick={(e) => { e.stopPropagation(); onSave(rec); }}
              className={`flex-1 rounded-lg px-3 py-2 text-xs font-medium flex items-center justify-center gap-1.5 transition-colors ${rec.saved ? "bg-rose-500 text-white" : "bg-white/10 backdrop-blur hover:bg-white/20"}`}
            >
              <Bookmark className={`w-3.5 h-3.5 ${rec.saved ? "fill-current" : ""}`} /> {rec.saved ? "Saved" : "Save"}
            </button>
          ) : null}
          <button data-testid={`info-recommendation-button-${rec.id}`} onClick={() => onOpen?.(rec)} className="rounded-lg px-3 py-2 bg-white/10 backdrop-blur hover:bg-white/20 transition-colors">
            <Info className="w-3.5 h-3.5" />
          </button>
          <WatchlistButton rec={rec} onDone={() => onUpdated?.({ ...rec, in_watchlist: true })} />
          {onDismiss ? (
            <button data-testid={`dismiss-recommendation-button-${rec.id}`} onClick={(e) => { e.stopPropagation(); onDismiss(rec); }} className="rounded-lg px-3 py-2 bg-white/10 backdrop-blur hover:bg-rose-500 transition-colors">
              <X className="w-3.5 h-3.5" />
            </button>
          ) : null}
        </div>
      </div>
      <div className="mt-3">
        <div className="flex items-baseline justify-between gap-2">
          <h3 className="font-display font-bold text-base line-clamp-1">{rec.title}</h3>
          <span className="font-mono text-xs text-slate-500 shrink-0">{rec.year}</span>
        </div>
      </div>
    </article>
  );
}
