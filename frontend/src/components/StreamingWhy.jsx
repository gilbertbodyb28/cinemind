import { useEffect, useRef, useState } from "react";
import { Sparkles, Loader2, RefreshCw } from "lucide-react";
import { apiUrl } from "@/lib/api";
import { modelLabel, notifyUsage } from "@/lib/models";

export default function StreamingWhy({ rec, model }) {
  const [text, setText] = useState("");
  const [status, setStatus] = useState("idle"); // idle | streaming | done | cached | error
  const [usedModel, setUsedModel] = useState(null);
  const abortRef = useRef(null);

  const start = async () => {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setText("");
    setStatus("streaming");
    try {
      const res = await fetch(apiUrl(`/recommendations/${rec.id}/reason/stream?model=${encodeURIComponent(model || "")}`), {
        credentials: "include",
        signal: ctrl.signal,
        headers: { Accept: "text/event-stream" },
      });
      if (!res.ok || !res.body) throw new Error("stream failed");
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const frames = buf.split("\n\n");
        buf = frames.pop();
        for (const f of frames) {
          const line = f.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          const ev = JSON.parse(line.slice(6));
          if (ev.error) { setStatus("error"); return; }
          if (ev.t) setText((t) => t + ev.t);
          if (ev.done) { setUsedModel(ev.model); setStatus(ev.cached ? "cached" : "done"); if (!ev.cached) notifyUsage(); }
        }
      }
    } catch (e) {
      if (e.name !== "AbortError") setStatus("error");
    }
  };

  useEffect(() => { start(); return () => abortRef.current?.abort(); }, [rec.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const streaming = status === "streaming";

  return (
    <div data-testid="streaming-reasoning" className="mt-6 glass rounded-2xl p-5 relative overflow-hidden">
      <div className="flex items-center justify-between gap-2 mb-3">
        <div className="flex items-center gap-2">
          <Sparkles className={`w-4 h-4 text-rose-400 ${streaming ? "animate-pulse" : ""}`} />
          <span className="font-mono text-xs uppercase tracking-widest text-slate-400">Why this pick</span>
          {streaming && <span data-testid="reasoning-status" className="chip chip-cyan text-[10px] py-0.5"><Loader2 className="w-3 h-3 animate-spin" /> {modelLabel(model)} is thinking</span>}
          {(status === "done" || status === "cached") && <span data-testid="reasoning-status" className="chip text-[10px] py-0.5">{modelLabel(usedModel)}{status === "cached" ? " · cached" : ""}</span>}
          {status === "error" && <span data-testid="reasoning-status" className="chip chip-amber text-[10px] py-0.5">Live analysis unavailable</span>}
        </div>
        {status === "error" && (
          <button data-testid="reasoning-retry-button" onClick={start} className="text-slate-400 hover:text-white transition-colors"><RefreshCw className="w-3.5 h-3.5" /></button>
        )}
      </div>

      {status === "error" || (!text && !streaming) ? (
        <p data-testid="reasoning-text" className="text-slate-200 leading-relaxed">{rec.why}</p>
      ) : (
        <p data-testid="reasoning-text" className="text-slate-200 leading-relaxed whitespace-pre-line">
          {text}
          {streaming && <span className="inline-block w-[2px] h-[1.1em] bg-rose-400 ml-0.5 align-[-0.15em] animate-pulse" />}
        </p>
      )}
    </div>
  );
}
