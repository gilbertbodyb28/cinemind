import { MatchStat } from "@/components/ApproveRejectOverlay";

export default function ProvenanceChips({ rec, showMatch = false }) {
  const affinity = rec?.score_components?.genre_affinity;
  const feedback = rec?.score_components?.feedback_overlap;
  return (
    <div className="flex flex-wrap gap-1.5 items-center">
      {showMatch && rec?.match_score != null && <MatchStat score={rec.match_score} />}
      {rec?.source && <span className="chip">{rec.source}</span>}
      {rec?.deterministic_score != null && <span className="chip chip-amber">Score {rec.deterministic_score}</span>}
      {affinity != null && <span className="chip">Taste {affinity}</span>}
      {feedback != null && feedback !== 0 && <span className="chip chip-rose">Feedback {feedback}</span>}
      {rec?.ai_reranked && <span className="chip chip-rose">AI reranked</span>}
      {rec?.job_id && <span className="chip">Job</span>}
    </div>
  );
}
