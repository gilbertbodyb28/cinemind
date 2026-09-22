import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Brain, Sparkles, Loader2 } from "lucide-react";
import { toast } from "sonner";
import ModelPicker from "@/components/ModelPicker";
import { DEFAULT_MODEL, providerLabel, notifyUsage } from "@/lib/models";

const MOOD_COLORS = {
  cerebral: "chip-cyan", cathartic: "chip-rose", playful: "chip-amber",
  dark: "chip-rose", dreamy: "chip-cyan", adrenaline: "chip-rose",
};

export default function TasteProfile() {
  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [model, setModel] = useState(DEFAULT_MODEL);

  useEffect(() => {
    api.get("/taste-profile").then(r => setProfile(r.data));
    api.get("/connections").then(r => r.data.ollama_model && setModel(r.data.ollama_model)).catch(() => {});
  }, []);

  const generate = async () => {
    setLoading(true);
    try {
      const r = await api.post("/taste-profile/generate", { model });
      setProfile(r.data);
      const provider = r.data.provider;
      const msg = provider === "demo"
        ? "Demo profile ready — connect Ollama with qwen-suggestarr for a personalized analysis"
        : `Profile generated via ${providerLabel(provider, r.data.model)}`;
      toast.success(msg);
      notifyUsage();
    } catch { toast.error("Failed"); }
    finally { setLoading(false); }
  };

  return (
    <div className="px-1 sm:px-2 pb-4 float-in">
      <div className="flex items-start justify-between gap-4 mb-10">
        <div>
          <span className="chip chip-rose mb-4">Cinematic DNA</span>
          <h1 className="font-display text-4xl sm:text-5xl font-extrabold tracking-tight">Your taste, decoded</h1>
          <p className="text-slate-400 mt-2 max-w-xl">A qualitative profile distilled from every show and movie you've watched — the tropes you love, the moods you chase, the complexity you crave.</p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <ModelPicker value={model} onChange={setModel} disabled={loading} testid="taste-model-picker" />
          <button data-testid="generate-taste-button" onClick={generate} disabled={loading} className="glass-strong px-5 py-2.5 rounded-full text-sm font-medium flex items-center gap-2 hover:brutal-shadow-rose transition-shadow">
            {loading ? <Loader2 className="w-4 h-4 animate-spin"/> : <Sparkles className="w-4 h-4"/>}
            {loading ? "Analyzing…" : profile ? "Regenerate" : "Generate"}
          </button>
        </div>
      </div>

      {profile ? (
        <div className="grid lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 glass rounded-2xl p-8 relative overflow-hidden grain">
            <div className="flex items-center gap-3 mb-6">
              <div className="w-11 h-11 rounded-xl chip-rose grid place-items-center"><Brain className="w-5 h-5"/></div>
              <h2 className="font-display text-2xl font-bold">Your Cinematic DNA</h2>
            </div>
            <p data-testid="cinematic-dna-text" className="text-lg leading-relaxed text-slate-200 font-display">
              {profile.cinematic_dna}
            </p>

            <div className="divider my-8" />

            <div className="text-xs font-mono uppercase tracking-widest text-slate-500 mb-4">Signature tropes</div>
            <div className="flex flex-wrap gap-2">
              {(profile.key_tropes || []).map((t, i) => (
                <span key={i} data-testid={`trope-${i}`} className="chip chip-cyan">{t}</span>
              ))}
            </div>
          </div>

          <div className="space-y-6">
            <div className="glass rounded-2xl p-6">
              <div className="text-xs font-mono uppercase tracking-widest text-slate-500 mb-2">Dominant mood</div>
              <div className={`chip ${MOOD_COLORS[profile.mood] || "chip-rose"} text-base py-2 px-4`} data-testid="mood-badge">{profile.mood}</div>
            </div>

            <div className="glass rounded-2xl p-6">
              <div className="text-xs font-mono uppercase tracking-widest text-slate-500 mb-3">Narrative complexity</div>
              <div className="flex items-baseline gap-2">
                <span className="font-display text-5xl font-extrabold">{profile.narrative_complexity}</span>
                <span className="text-slate-500 font-mono">/ 10</span>
              </div>
              <div className="h-1.5 rounded-full bg-white/5 mt-4 overflow-hidden">
                <div className="h-full bg-gradient-to-r from-rose-500 to-cyan-400" style={{ width: `${(profile.narrative_complexity || 0) * 10}%` }} />
              </div>
            </div>

            <div className="glass rounded-2xl p-6">
              <div className="text-xs font-mono uppercase tracking-widest text-slate-500 mb-3">Top genres</div>
              <div className="flex flex-wrap gap-2">
                {(profile.top_genres || []).map((g, i) => <span key={i} className="chip">{g}</span>)}
                {!(profile.top_genres || []).length && <span className="text-xs text-slate-500">Sync history to populate.</span>}
              </div>
            </div>

            {profile.used_demo && (
              <div className="glass rounded-2xl p-4 border-amber-500/30">
                <div className="text-xs text-amber-300">Demo profile · Connect Ollama with qwen-suggestarr for a personalized analysis.</div>
              </div>
            )}
            {profile.provider && profile.provider !== "demo" && (
              <div className="glass rounded-2xl p-4">
                <div className="text-[10px] font-mono uppercase tracking-widest text-slate-500 mb-1">Generated by</div>
                <div className="chip chip-emerald" data-testid="profile-provider-badge">
                  {providerLabel(profile.provider, profile.model)}
                </div>
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="glass rounded-2xl p-16 text-center">
          <Brain className="w-10 h-10 mx-auto text-slate-600 mb-4" />
          <p className="text-slate-400">Click <span className="text-white font-medium">Generate</span> to distill your taste from your watch history.</p>
        </div>
      )}
    </div>
  );
}
