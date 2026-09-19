import { useState } from "react";
import { ListPlus, ListChecks, Loader2 } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { api } from "@/lib/api";

export default function WatchlistButton({ rec, onDone, variant = "icon", className = "" }) {
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const done = !!rec.in_watchlist;
  const testid = `watchlist-button-${rec.id}${variant === "pill" ? "-modal" : ""}`;

  const push = async (e) => {
    e?.stopPropagation();
    if (done || busy) return;
    setBusy(true);
    try {
      const r = await api.post(`/recommendations/${rec.id}/watchlist`);
      toast.success(r.data.existing ? `${rec.title} was already on your Trakt watchlist` : `${rec.title} added to Trakt watchlist`);
      onDone?.(rec.id);
    } catch (err) {
      const status = err?.status ?? err?.response?.status;
      const detail = err?.message || err?.response?.data?.detail;
      if (status === 409) {
        toast.error("Connect Trakt to push to your watchlist", { action: { label: "Connect", onClick: () => navigate("/connections") } });
      } else {
        toast.error(detail || "Could not add to watchlist");
      }
    } finally { setBusy(false); }
  };

  const Icon = busy ? Loader2 : done ? ListChecks : ListPlus;
  const iconCls = `w-3.5 h-3.5 ${busy ? "animate-spin" : ""}`;

  if (variant === "pill") {
    return (
      <button data-testid={testid} onClick={push} disabled={busy || done} title="Add to Trakt watchlist"
        className={`px-5 py-2.5 rounded-full text-sm font-medium flex items-center gap-2 transition-all ${done ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/40" : "glass-strong hover:brutal-shadow-rose"} ${className}`}>
        <Icon className={`w-4 h-4 ${busy ? "animate-spin" : ""}`} />
        {done ? "On Trakt watchlist" : "Add to Trakt watchlist"}
      </button>
    );
  }

  return (
    <button data-testid={testid} onClick={push} disabled={busy || done} title={done ? "On Trakt watchlist" : "Add to Trakt watchlist"}
      className={`rounded-lg px-3 py-2 transition-colors ${done ? "bg-emerald-500/30 text-emerald-200" : "bg-white/10 backdrop-blur hover:bg-emerald-500/40"} ${className}`}>
      <Icon className={iconCls} />
    </button>
  );
}
