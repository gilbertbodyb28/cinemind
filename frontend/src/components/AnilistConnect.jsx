import { useState } from "react";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Link2, Loader2, CheckCircle2, Unplug } from "lucide-react";

export default function AnilistConnect({ connected, checking, notice, username, clientConfigured = true, onChange }) {
  const [code, setCode] = useState("");
  const [importJson, setImportJson] = useState("");
  const [busy, setBusy] = useState(false);
  const [importing, setImporting] = useState(false);
  const [redirectUri, setRedirectUri] = useState(
    "http://localhost:8001/api/anilist/auth/callback",
  );

  const start = async () => {
    try {
      const r = await api.get("/anilist/oauth/start", { params: { mode: "code" } });
      const url = r.data?.authorization_url;
      if (r.data?.redirect_uri) setRedirectUri(r.data.redirect_uri);
      if (!url) {
        toast.error("AniList authorize URL missing — check server ANILIST_CLIENT_ID");
        return;
      }
      window.location.href = url;
    } catch (e) {
      toast.error(e?.message || "AniList app credentials are not configured on the server");
    }
  };

  const exchange = async () => {
    if (!code.trim()) {
      toast.error("Paste the AniList access token or authorization code first");
      return;
    }
    setBusy(true);
    try {
      const r = await api.post("/anilist/oauth/exchange", { code: code.trim() });
      if (r.data.status === "authorized") {
        toast.success(`AniList connected${r.data.username ? ` as ${r.data.username}` : ""}`);
      } else {
        toast.success(r.data.detail || "Stored on the server");
      }
      setCode("");
      onChange?.();
    } catch (e) {
      toast.error(e?.message || "Could not connect AniList");
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    try {
      await api.post("/anilist/disconnect");
      setCode("");
      toast("AniList disconnected");
      onChange?.();
    } catch (e) {
      toast.error(e?.message || "Could not disconnect AniList");
    }
  };

  const importList = async () => {
    if (!importJson.trim()) {
      toast.error("Paste AniList MediaListCollection JSON first");
      return;
    }
    let parsed;
    try {
      parsed = JSON.parse(importJson);
    } catch {
      toast.error("Invalid JSON");
      return;
    }
    setImporting(true);
    try {
      const body = parsed?.data ? { data: parsed.data } : { data: parsed };
      const r = await api.post("/anilist/import", body);
      toast.success(`Imported ${r.data.count} AniList titles`);
      setImportJson("");
      onChange?.();
    } catch (e) {
      toast.error(e?.message || "Import failed");
    } finally {
      setImporting(false);
    }
  };

  return (
    <div className="space-y-4">
      {checking ? (
        <div data-testid="anilist-checking" className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 text-xs text-slate-400 font-mono">
          <Loader2 className="w-3.5 h-3.5 animate-spin" /> Checking the saved sign-in with AniList…
        </div>
      ) : connected ? (
        <div data-testid="anilist-connected" className="flex items-center justify-between gap-3 rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-3">
          <div className="flex items-center gap-2 text-sm">
            <CheckCircle2 className="w-4 h-4 text-emerald-400" />
            <span>
              Connected
              {username ? (
                <> as <span className="font-mono text-emerald-300">{username.startsWith("@") ? username : `@${username}`}</span></>
              ) : null}
            </span>
          </div>
          <button data-testid="anilist-disconnect-button" onClick={disconnect} className="chip hover:chip-rose transition-colors flex items-center gap-1.5">
            <Unplug className="w-3 h-3" /> Disconnect
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center gap-3 flex-wrap">
            <button
              data-testid="anilist-connect-button"
              onClick={start}
              disabled={!clientConfigured}
              className="glass-strong px-5 py-2.5 rounded-full text-sm font-medium flex items-center gap-2 hover:brutal-shadow-rose transition-shadow"
            >
              <Link2 className="w-4 h-4" /> Connect with AniList
            </button>
            {/* Why a stored sign-in stopped counting (AniList refused it). */}
            {notice && (
              <span data-testid="anilist-auth-notice" className="text-xs text-rose-300 font-mono">{notice}</span>
            )}
          </div>
          <p className="text-xs text-slate-500 leading-relaxed">
            AniList Redirect URL must be exactly{" "}
            <span className="font-mono text-slate-300">{redirectUri}</span>.
          </p>
          <label className="block">
            <div className="text-xs font-mono uppercase tracking-widest text-slate-500 mb-1.5">Authorization code</div>
            <div className="flex gap-2">
              <input
                data-testid="anilist-code-input"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder="Paste the code from AniList"
                autoComplete="off"
                className="flex-1 bg-white/[0.03] border border-white/10 rounded-lg px-4 py-2.5 text-sm outline-none focus:border-rose-500/50 focus:bg-white/[0.06] transition-colors"
              />
              <button data-testid="anilist-exchange-button" onClick={exchange} disabled={busy} className="chip hover:chip-rose transition-colors flex items-center gap-1.5">
                {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
                Save code
              </button>
            </div>
          </label>
        </div>
      )}

      <label className="block sm:col-span-2">
        <div className="text-xs font-mono uppercase tracking-widest text-slate-500 mb-1.5">Import list JSON</div>
        <textarea
          data-testid="anilist-import-json"
          value={importJson}
          onChange={(e) => setImportJson(e.target.value)}
          placeholder='{"MediaListCollection":{"lists":[{"entries":[...]}]}}'
          rows={4}
          className="w-full bg-white/[0.03] border border-white/10 rounded-lg px-4 py-2.5 text-xs font-mono outline-none focus:border-rose-500/50 focus:bg-white/[0.06] transition-colors"
        />
        <button
          data-testid="anilist-import-button"
          type="button"
          onClick={importList}
          disabled={importing}
          className="mt-2 chip hover:chip-rose transition-colors flex items-center gap-1.5"
        >
          {importing ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
          Import into history
        </button>
      </label>
    </div>
  );
}
