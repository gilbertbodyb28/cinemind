import { X, Bookmark } from "lucide-react";
import { useEffect } from "react";
import { toast } from "sonner";
import StreamingWhy from "@/components/StreamingWhy";
import TrailerPlayer from "@/components/TrailerPlayer";
import WatchlistButton from "@/components/WatchlistButton";
import ApproveButton from "@/components/ApproveButton";
import FeedbackChips from "@/components/FeedbackChips";
import { RejectButton, rejectRecommendation, MatchStat, RatingStat } from "@/components/ApproveRejectOverlay";

export default function RecModal({ rec, onClose, onSave, model, onWatchlist, onApproved, onFeedback, onDismiss }) {
  useEffect(() => {
    const esc = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    document.body.style.overflow = "hidden";
    return () => { window.removeEventListener("keydown", esc); document.body.style.overflow = ""; };
  }, [onClose]);

  const reject = async (event) => {
    event?.stopPropagation();
    try {
      await rejectRecommendation(rec);
      onDismiss?.(rec.id);
      onClose();
    } catch (error) {
      toast.error(error?.message || "Could not reject");
    }
  };

  return (
    <div data-testid="recommendation-detail-modal" className="fixed inset-0 z-50 grid place-items-center p-4 sm:p-8" onClick={onClose}>
      <div className="absolute inset-0 bg-black/70 backdrop-blur-lg" />
      <div className="relative max-w-3xl w-full glass-strong rounded-3xl overflow-hidden float-in" onClick={(e) => e.stopPropagation()}>
        <div className="relative h-56 sm:h-72 overflow-hidden">
          <img src={rec.backdrop || rec.poster} alt="" className="w-full h-full object-cover" />
          <div className="absolute inset-0 bg-gradient-to-t from-[#17130F] via-[#17130F]/40 to-transparent" />
          <button data-testid="close-modal-button" onClick={onClose} className="absolute top-4 right-4 w-9 h-9 rounded-full glass grid place-items-center hover:bg-rose-500/30">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-8 -mt-16 relative">
          <div className="flex gap-6">
            <img src={rec.poster} className="w-28 sm:w-36 aspect-[2/3] object-cover rounded-xl border border-white/10 shadow-2xl" alt={rec.title} />
            <div className="flex-1 pt-16">
              <div className="flex items-center gap-2 mb-2 flex-wrap">
                <MatchStat score={rec.match_score} />
                <RatingStat rating={rec.tmdb_rating} />
                <span className="chip">{rec.year} · {rec.type}</span>
              </div>
              <h2 className="font-display text-3xl sm:text-4xl font-extrabold tracking-tight">{rec.title}</h2>
              <div className="flex flex-wrap gap-1.5 mt-2">
                {rec.genres?.map((g, i) => <span key={i} className="text-xs font-mono uppercase tracking-wider text-slate-400">{g}{i < rec.genres.length - 1 ? " ·" : ""}</span>)}
              </div>
            </div>
          </div>

          <p className="text-slate-300 mt-6 leading-relaxed">{rec.synopsis}</p>

          <StreamingWhy rec={rec} model={model} />
          <TrailerPlayer rec={rec} />

          <div className="mt-6">
            <FeedbackChips rec={rec} onFeedback={onFeedback} />
          </div>

          <div className="mt-6 flex items-center gap-3 flex-wrap">
            <button data-testid="modal-save-button" onClick={() => onSave?.(rec.id)} className={`px-5 py-2.5 rounded-full text-sm font-medium flex items-center gap-2 transition-all ${rec.saved ? "bg-rose-500 text-white brutal-shadow-rose" : "glass-strong hover:brutal-shadow-rose"}`}>
              <Bookmark className={`w-4 h-4 ${rec.saved ? "fill-current" : ""}`} />
              {rec.saved ? "Saved" : "Save to library"}
            </button>
            <ApproveButton rec={rec} variant="pill" onDone={onApproved} />
            <WatchlistButton rec={rec} variant="pill" onDone={onWatchlist} />
            {!rec.in_library && (
              <RejectButton testid={`reject-request-${rec.id}-modal`} onClick={reject} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
