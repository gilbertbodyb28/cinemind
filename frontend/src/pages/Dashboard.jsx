import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Film, Tv, Clock, Star, RefreshCw } from "lucide-react";
import { toast } from "sonner";

const SOURCE_COLOR = { trakt: "chip-rose", simkl: "chip-cyan", plex: "chip-amber", demo: "chip-emerald" };

export default function Dashboard() {
  const [stats, setStats] = useState(null);
  const [history, setHistory] = useState([]);
  const [syncing, setSyncing] = useState(false);

  const load = async () => {
    const [s, h] = await Promise.all([api.get("/history/stats"), api.get("/history")]);
    setStats(s.data);
    setHistory(h.data);
  };

  useEffect(() => { load(); }, []);

  const sync = async () => {
    setSyncing(true);
    try {
      const r = await api.post("/history/sync");
      toast.success(`Synced ${r.data.count} items${r.data.demo ? " (demo mode)" : ""}`);
      await load();
    } catch { toast.error("Sync failed"); }
    finally { setSyncing(false); }
  };

  return (
    <div className="px-1 sm:px-2 pb-4 float-in">
      <div className="flex items-start justify-between gap-4 mb-10">
        <div>
          <span className="chip chip-rose mb-4">Overview</span>
          <h1 className="font-display text-4xl sm:text-5xl font-extrabold tracking-tight">Your watch universe</h1>
          <p className="text-slate-400 mt-2 max-w-xl">Aggregated history from Simkl, Trakt.tv, and Plex — the raw material your AI uses to know you.</p>
        </div>
        <button data-testid="sync-history-button" onClick={sync} disabled={syncing} className="glass rounded-full px-5 py-2.5 flex items-center gap-2 text-sm font-medium hover:border-rose-500/40 transition-colors">
          <RefreshCw className={`w-4 h-4 ${syncing ? "animate-spin" : ""}`} /> {syncing ? "Syncing…" : "Sync history"}
        </button>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-10">
        <StatCard testid="stat-total" icon={Film} label="Total watched" value={stats?.total ?? "—"} tone="rose" />
        <StatCard testid="stat-movies" icon={Film} label="Movies" value={stats?.movies ?? "—"} tone="cyan" />
        <StatCard testid="stat-shows" icon={Tv} label="Shows" value={stats?.shows ?? "—"} tone="amber" />
        <StatCard testid="stat-hours" icon={Clock} label="Est. hours" value={stats?.hours_estimate ?? "—"} tone="emerald" />
      </div>

      <div className="grid lg:grid-cols-3 gap-6 mb-10">
        <div className="lg:col-span-2 glass rounded-2xl p-8">
          <div className="flex items-center justify-between mb-6">
            <h2 className="font-display text-2xl font-bold">Favorite genres</h2>
            <span className="chip">Top {stats?.genres?.length ?? 0}</span>
          </div>
          {stats?.genres?.length ? (
            <div className="space-y-3">
              {stats.genres.map((g) => {
                const max = stats.genres[0].count;
                const pct = Math.round((g.count / max) * 100);
                return (
                  <div key={g.name} data-testid={`genre-bar-${g.name}`}>
                    <div className="flex justify-between text-sm mb-1.5">
                      <span className="font-medium">{g.name}</span>
                      <span className="font-mono text-xs text-slate-500">{g.count}</span>
                    </div>
                    <div className="h-2 rounded-full bg-white/5 overflow-hidden">
                      <div className="h-full bg-gradient-to-r from-rose-500 via-fuchsia-500 to-cyan-400 rounded-full transition-all duration-700" style={{ width: `${pct}%` }} />
                    </div>
                  </div>
                );
              })}
            </div>
          ) : <p className="text-slate-500 text-sm">Sync to see your genre distribution.</p>}
        </div>

        <div className="glass rounded-2xl p-8">
          <h2 className="font-display text-2xl font-bold mb-6">Sources</h2>
          <div className="space-y-3">
            {stats?.sources?.map((s) => (
              <div key={s.name} className="flex items-center justify-between">
                <span className={`chip ${SOURCE_COLOR[s.name] || ""}`}>{s.name}</span>
                <span className="font-mono text-sm text-slate-300">{s.count}</span>
              </div>
            ))}
            {!stats?.sources?.length && <p className="text-slate-500 text-sm">No sources yet.</p>}
          </div>
          <div className="divider my-6" />
          <div className="flex items-center gap-3">
            <div className="w-11 h-11 rounded-lg chip-amber grid place-items-center"><Star className="w-5 h-5" /></div>
            <div>
              <div className="text-xs text-slate-500 font-mono tracking-widest uppercase">Avg rating</div>
              <div className="font-display text-2xl font-bold">{stats?.avg_rating || "—"}</div>
            </div>
          </div>
        </div>
      </div>

      <div className="glass rounded-2xl p-8">
        <div className="flex items-center justify-between mb-6">
          <h2 className="font-display text-2xl font-bold">Recent history</h2>
          <span className="chip chip-cyan">{history.length} items</span>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-4">
          {history.slice(0, 12).map((h) => (
            <div key={h.id} data-testid={`history-item-${h.id}`} className="group relative">
              <div className="poster-frame relative">
                {h.poster ? (
                  <img src={h.poster} alt={h.title} className="aspect-[2/3] w-full object-cover poster-hover" />
                ) : (
                  <div className="aspect-[2/3] w-full bg-gradient-to-br from-slate-800 to-slate-950 grid place-items-center text-slate-600 text-xs p-2 text-center">{h.title}</div>
                )}
              </div>
              <div className="mt-2">
                <div className="text-sm font-medium line-clamp-1">{h.title}</div>
                <div className="text-xs text-slate-500 font-mono mt-0.5">{h.year} · {h.type}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function StatCard({ icon: Icon, label, value, tone, testid }) {
  return (
    <div data-testid={testid} className="glass rounded-2xl p-6 relative overflow-hidden group hover:border-rose-500/40 transition-colors">
      <div className={`w-10 h-10 rounded-lg chip-${tone} grid place-items-center mb-4`}>
        <Icon className="w-5 h-5" />
      </div>
      <div className="text-xs text-slate-500 font-mono tracking-widest uppercase">{label}</div>
      <div className="font-display text-3xl font-extrabold mt-1">{value}</div>
    </div>
  );
}
