import { useEffect, useState } from "react";
import { Gauge } from "lucide-react";
import { api } from "@/lib/api";
import { modelLabel } from "@/lib/models";

const fmt = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));

export default function UsageMeter() {
  const [usage, setUsage] = useState(null);

  const load = () => api.get("/usage").then((r) => setUsage(r.data)).catch(() => {});

  useEffect(() => {
    load();
    window.addEventListener("cinemind:usage", load);
    return () => window.removeEventListener("cinemind:usage", load);
  }, []);

  if (!usage) return null;
  const top = usage.by_model?.[0];

  return (
    <div data-testid="usage-meter" className="glass rounded-xl p-3 mb-3">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-widest text-slate-500">
          <Gauge className="w-3 h-3 text-cyan-300" /> AI usage
        </div>
        <span className="text-[10px] font-mono text-slate-600">{usage.month ? usage.month.split(" ")[0] : ""}</span>
      </div>
      <div className="flex items-baseline gap-3">
        <div>
          <div data-testid="usage-calls" className="font-display text-xl font-extrabold leading-none">{usage.calls}</div>
          <div className="text-[10px] text-slate-500 mt-0.5">calls</div>
        </div>
        <div>
          <div data-testid="usage-tokens" className="font-display text-xl font-extrabold leading-none">~{fmt(usage.est_tokens)}</div>
          <div className="text-[10px] text-slate-500 mt-0.5">est. tokens</div>
        </div>
      </div>
      {top && <div className="text-[10px] text-slate-500 mt-2 truncate">Mostly {modelLabel(top.model)}</div>}
      <div className="text-[10px] text-slate-600 mt-2 leading-snug">Local Ollama usage on this machine. Jobs and AI Picks use qwen3:14b.</div>
    </div>
  );
}
