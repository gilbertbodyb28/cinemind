import { MatchStat } from "@/components/ApproveRejectOverlay";

const pct = (value) => `${Math.round(value * 100)}%`;

export default function ProvenanceChips({ rec, showMatch = false }) {
  const parts = rec?.score_components || {};
  // Names follow recommendation/ranking_engine.py. The old genre_affinity and
  // feedback_overlap chips silently disappeared when the scorer was rebuilt.
  const taste = parts.taste_similarity;
  const liked = parts.liked_title_similarity;
  const negative = parts.negative_affinity;
  const thin = parts.thin_evidence;
  const similar = (rec?.similar_to || [])[0];
  return (
    <div className="flex flex-wrap gap-1.5 items-center">
      {showMatch && rec?.match_score != null && <MatchStat score={rec.match_score} />}
      {rec?.source && <span className="chip">{rec.source}</span>}
      {rec?.deterministic_score != null && <span className="chip chip-amber">Score {rec.deterministic_score}</span>}
      {taste != null && taste !== 0 && <span className="chip">Taste {pct(taste)}</span>}
      {liked != null && liked !== 0 && (
        <span className="chip" title={similar?.title ? `Closest match: ${similar.title}` : undefined}>
          Like {similar?.title || pct(liked)}
        </span>
      )}
      {negative != null && negative < 0 && <span className="chip chip-rose">Avoid {pct(negative)}</span>}
      {thin != null && thin < 0 && <span className="chip chip-rose">Few ratings</span>}
      {rec?.ai_reranked && <span className="chip chip-rose">AI reranked</span>}
      {rec?.job_id && <span className="chip">Job</span>}
    </div>
  );
}
