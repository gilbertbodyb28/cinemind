import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Ban, Film, Loader2, RefreshCw, Server, SlidersHorizontal, Tv, User } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

export default function Profile() {
  const { user } = useAuth();
  const [connections, setConnections] = useState(null);
  const [blacklist, setBlacklist] = useState([]);
  const [recent, setRecent] = useState([]);
  const [syncing, setSyncing] = useState(false);

  const reload = async () => {
    const [conn, blocked, overview] = await Promise.all([
      api.get("/connections"),
      api.get("/blacklist").catch(() => ({ data: [] })),
      api.get("/overview").catch(() => ({ data: {} })),
    ]);
    setConnections(conn.data);
    setBlacklist(blocked.data || []);
    setRecent(overview.data?.recent_feedback || []);
  };

  useEffect(() => { reload(); }, []);

  const syncNow = async () => {
    setSyncing(true);
    try {
      const r = await api.post("/history/sync");
      toast.success(`Synced ${r.data.count} items${r.data.demo ? " (demo mode)" : ""}`);
    } catch (error) {
      toast.error(error.message || "Sync failed");
    } finally {
      setSyncing(false);
    }
  };

  const removeBlacklist = async (canonicalId) => {
    try {
      await api.delete(`/blacklist/${encodeURIComponent(canonicalId)}`);
      toast.success("Removed from blacklist");
      await reload();
    } catch (error) {
      toast.error(error.message || "Could not update blacklist");
    }
  };

  const accounts = [
    ["Plex", connections?.plex_connected, connections?.plex_url || "Local server"],
    ["Trakt", connections?.trakt_connected, connections?.trakt_username ? `@${connections.trakt_username}` : "Device login"],
    ["Simkl", connections?.simkl_connected, connections?.simkl_username || "PIN login"],
    ["AniList", connections?.anilist_connected, connections?.anilist_username || "OAuth"],
    ["TMDb", connections?.tmdb_configured, "Posters & discover"],
    ["TVDb", connections?.tvdb_configured, "Poster fallback"],
    ["Seer", connections?.seer_connected, connections?.seer_url || "Request provider"],
    ["Ollama", Boolean(connections?.ollama_url), connections?.ollama_model || "local model"],
  ];

  return (
    <div data-testid="profile-page" className="px-1 sm:px-2 pb-4 max-w-5xl float-in">
      <span className="chip chip-rose mb-4">Profile</span>
      <h1 className="font-display text-4xl sm:text-5xl font-extrabold tracking-tight">Your account</h1>
      <p className="text-slate-400 mt-2 max-w-2xl">Your CineMind identity plus the external services that feed taste and history. Tokens stay on the server.</p>

      <div className="mt-10 grid gap-6">
        <div data-testid="section-local-account" className="glass rounded-2xl p-6 lg:p-8">
          <div className="flex items-center gap-3 mb-5">
            <div className="w-10 h-10 rounded-lg chip-rose grid place-items-center"><User className="w-5 h-5" /></div>
            <div>
              <h3 className="font-display text-xl font-bold">
                {user?.auth_provider === "google" ? "Google account" : "Local account"}
              </h3>
              <p className="text-xs text-slate-500 mt-0.5">
                {user?.auth_provider === "google"
                  ? "Signed in with Google. Your CineMind profile stays in this MongoDB."
                  : "Email and password live in this MongoDB."}
              </p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <span className="chip">{user?.name || "Guest"}</span>
            <span className="chip">{user?.email}</span>
            {user?.auth_provider === "google" ? (
              <span data-testid="auth-provider-chip" className="chip chip-cyan">Google</span>
            ) : (
              <span data-testid="auth-provider-chip" className="chip chip-amber">Email &amp; password</span>
            )}
            {user?.has_password && user?.auth_provider === "google" && (
              <span className="chip">Password also set</span>
            )}
          </div>
        </div>

        <div data-testid="section-external-accounts" className="glass rounded-2xl p-6 lg:p-8">
          <div className="flex items-start justify-between gap-4 flex-wrap mb-5">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg chip-cyan grid place-items-center"><SlidersHorizontal className="w-5 h-5" /></div>
              <div>
                <h3 className="font-display text-xl font-bold">External accounts</h3>
                <p className="text-xs text-slate-500 mt-0.5">Connection state only. Open Sources to add or remove tokens.</p>
              </div>
            </div>
            <Link data-testid="profile-open-sources" to="/connections" className="chip hover:chip-rose transition-colors">Open Sources</Link>
          </div>
          <div className="grid sm:grid-cols-2 gap-3">
            {accounts.map(([name, connected, hint]) => (
              <div key={name} data-testid={`external-account-${name.toLowerCase()}`} className="text-left rounded-xl px-4 py-3 border border-white/10 bg-white/[0.03]">
                <div className="flex items-center gap-2 mb-2">
                  <span className={`chip ${connected ? "chip-emerald" : ""}`}>{connected ? "Connected" : "Not connected"}</span>
                  <span className="chip">{name}</span>
                </div>
                <div className="text-[11px] font-mono uppercase tracking-wider text-slate-500 truncate">{hint}</div>
              </div>
            ))}
          </div>
        </div>

        <div data-testid="section-recent-feedback" className="glass rounded-2xl p-6 lg:p-8">
          <div className="flex items-center gap-3 mb-5">
            <div className="w-10 h-10 rounded-lg chip-amber grid place-items-center"><Film className="w-5 h-5" /></div>
            <div>
              <h3 className="font-display text-xl font-bold">Recent feedback</h3>
              <p className="text-xs text-slate-500 mt-0.5">Likes and dislikes that reshape the next job and generate run.</p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {recent.map((row) => (
              <span key={`${row.recommendation_id}-${row.action}`} className="chip">{row.title} · {row.action}</span>
            ))}
            {!recent.length && <span className="text-xs text-slate-500">No feedback yet — use the chips on Home or AI Picks.</span>}
          </div>
        </div>

        <div data-testid="section-blacklist" className="glass rounded-2xl p-6 lg:p-8">
          <div className="flex items-center gap-3 mb-5">
            <div className="w-10 h-10 rounded-lg chip-rose grid place-items-center"><Ban className="w-5 h-5" /></div>
            <div>
              <h3 className="font-display text-xl font-bold">Blacklist</h3>
              <p className="text-xs text-slate-500 mt-0.5">These titles stay out of future jobs until you remove them.</p>
            </div>
          </div>
          <div className="space-y-2">
            {blacklist.map((row) => (
              <div key={row.canonical_media_id} className="flex items-center justify-between gap-3 rounded-xl px-4 py-3 border border-white/10 bg-white/[0.03]">
                <div>
                  <div className="text-sm font-medium">{row.title}</div>
                  <div className="text-[11px] font-mono uppercase tracking-wider text-slate-500">{row.year}</div>
                </div>
                <button
                  data-testid={`remove-blacklist-${row.canonical_media_id}`}
                  type="button"
                  onClick={() => removeBlacklist(row.canonical_media_id)}
                  className="chip hover:chip-rose transition-colors"
                >
                  Allow again
                </button>
              </div>
            ))}
            {!blacklist.length && <p className="text-xs text-slate-500">Nothing blacklisted.</p>}
          </div>
        </div>

        <div className="glass rounded-2xl p-6 lg:p-8">
          <div className="flex items-start justify-between gap-4 flex-wrap">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg chip-emerald grid place-items-center"><Server className="w-5 h-5" /></div>
              <div>
                <h3 className="font-display text-xl font-bold">Watch history</h3>
                <p className="text-xs text-slate-500 mt-0.5">Plex, Trakt, Simkl and AniList rows collapse to one canonical title before they shape taste.</p>
              </div>
            </div>
            <button
              data-testid="profile-sync-now"
              type="button"
              onClick={syncNow}
              disabled={syncing}
              className="chip hover:chip-rose transition-colors flex items-center gap-2"
            >
              {syncing ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
              {syncing ? "Syncing" : "Sync now"}
            </button>
          </div>
          <div className="mt-5 flex flex-wrap gap-2">
            <span className="chip"><Tv className="w-3 h-3" /> Canonical identity</span>
            <Link to="/taste" className="chip hover:chip-rose transition-colors">Open Taste</Link>
          </div>
        </div>
      </div>
    </div>
  );
}
