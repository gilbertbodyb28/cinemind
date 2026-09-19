import { Ban, CheckCircle2, EyeOff, ThumbsDown, ThumbsUp } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";

const ACTIONS = [
  { action: "like", label: "Like", Icon: ThumbsUp },
  { action: "dislike", label: "Dislike", Icon: ThumbsDown },
  { action: "hide", label: "Hide", Icon: EyeOff },
  { action: "watched", label: "Already watched", Icon: CheckCircle2 },
  { action: "blacklist", label: "Blacklist", Icon: Ban },
];

export default function FeedbackChips({ rec, onFeedback, testidPrefix = "recommendation" }) {
  const sendFeedback = async (action, event) => {
    event?.stopPropagation();
    if (!rec?.id) return;
    try {
      await api.post(`/recommendations/${rec.id}/feedback`, { action });
      const labels = { like: "Liked", dislike: "Noted", hide: "Hidden", watched: "Marked watched", blacklist: "Blacklisted" };
      toast.success(labels[action] || "Saved");
      onFeedback?.(rec.id, action);
    } catch (error) {
      toast.error(error.message || "Could not save feedback");
    }
  };

  return (
    <div className="flex flex-wrap gap-1.5" onClick={(event) => event.stopPropagation()}>
      {ACTIONS.map(({ action, label, Icon }) => (
        <button
          key={action}
          data-testid={`${action}-${testidPrefix}-button-${rec.id}`}
          type="button"
          onClick={(event) => sendFeedback(action, event)}
          className="chip hover:chip-rose transition-colors flex items-center gap-1"
        >
          <Icon className="w-3 h-3" /> {label}
        </button>
      ))}
    </div>
  );
}
