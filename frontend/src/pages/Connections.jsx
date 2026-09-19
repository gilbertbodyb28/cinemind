import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { CheckCircle2, XCircle, Loader2, Server, Cpu, Film, Tv, Inbox, Library, AppWindow, Clapperboard, Droplets, Image, Check, Maximize2 } from "lucide-react";
import { DEFAULT_MODEL } from "@/lib/models";
import DeviceConnect from "@/components/DeviceConnect";
import AnilistConnect from "@/components/AnilistConnect";
import { useTheme, WALLPAPERS, MIN_ICON_SIZE, MAX_ICON_SIZE } from "@/context/ThemeContext";

const THEME_SWATCH = {
  vision: "https://image.tmdb.org/t/p/w500/o8H6HmQNt2qx5bIfmvuI6VJn13A.jpg",
  apple: "https://image.tmdb.org/t/p/w500/cO7J0XSVKPlAjUCMWC7DVBn1Py2.jpg",
};

const initial = {
  trakt_client_id: "", trakt_access_token: "", trakt_username: "",
  simkl_client_id: "", simkl_access_token: "",
  plex_url: "", plex_token: "",
  ollama_url: "http://localhost:11434", ollama_model: DEFAULT_MODEL,
  mediamanager_url: "", mediamanager_email: "", mediamanager_password: "",
  anilist_connected: false, anilist_username: "", anilist_client_configured: false,
  mediamanager_configured: false,
  ui_theme: "vision",
};

const SECRETS = ["plex_token", "mediamanager_password", "trakt_access_token", "simkl_access_token"];

const WALLPAPER_LABELS = {
  poster: "Poster glow",
  midnight: "Midnight",
  ember: "Ember",
  dusk: "Dusk",
  mocha: "Mocha",
  aurora: "Aurora",
  graphite: "Graphite",
};

const WALLPAPER_HINTS = {
  poster: "The original — the current poster lights the background. Unchanged.",
  midnight: "Deep blue-black, like a dark dashboard at night.",
  ember: "Black falling into a low orange burn.",
  dusk: "Blue hour: indigo overhead, rose along the horizon.",
  mocha: "Warm mocha haze fading to ink.",
  aurora: "Violet field with a magenta ridge through it.",
  graphite: "Near-black with a soft steel glow.",
};

function savePayload(form, glassIntensity) {
  const n = Number(glassIntensity);
  const data = {
    trakt_client_id: form.trakt_client_id,
    trakt_username: form.trakt_username,
    simkl_client_id: form.simkl_client_id,
    plex_url: form.plex_url,
    ollama_url: form.ollama_url || "http://localhost:11434",
    ollama_model: form.ollama_model || DEFAULT_MODEL,
    mediamanager_url: form.mediamanager_url,
    mediamanager_email: form.mediamanager_email,
    ui_theme: form.ui_theme === "apple" ? "apple" : "vision",
    glass_intensity: Number.isFinite(n) ? Math.max(0, Math.min(100, Math.round(n))) : 78,
  };
  for (const key of SECRETS) {
    if (form[key]) data[key] = form[key];
  }
  return data;
}

export default function Connections() {
  const [form, setForm] = useState(initial);
  const [saving, setSaving] = useState(false);
  const [tests, setTests] = useState({});
  const [savingIconSize, setSavingIconSize] = useState(false);
  const {
    theme,
    setTheme,
    glassIntensity,
    setGlassIntensity,
    wallpaper,
    setWallpaper,
    sidebarIconSize,
    setSidebarIconSize,
    saveSidebarIconSize,
  } = useTheme();

  const reload = () =>
    api.get("/connections")
      .then(r => setForm(f => {
        const next = { ...initial, ...f, ...r.data, mediamanager_password: "" };
        const model = String(next.ollama_model || "");
        if (!model || /claude|sonnet|opus|haiku|^llama3\.2/i.test(model)) {
          next.ollama_model = DEFAULT_MODEL;
        }
        if (!next.ollama_url) next.ollama_url = "http://localhost:11434";
        next.ui_theme = next.ui_theme === "apple" ? "apple" : "vision";
        return next;
      }))
      .catch(() => {});

  useEffect(() => { reload(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

  const save = async ({ silent } = {}) => {
    if (!silent) setSaving(true);
    try {
      await api.put("/connections", savePayload(form, glassIntensity));
      if (!silent) toast.success("Connections saved");
      await reload();
    } catch { if (!silent) toast.error("Failed to save"); }
    finally { if (!silent) setSaving(false); }
  };

  const test = async (name) => {
    setTests(t => ({ ...t, [name]: { loading: true } }));
    try {
      if (name !== "anilist") await api.put("/connections", savePayload(form, glassIntensity));
      const r = await api.post(`/connections/test/${name}`);
      setTests(t => ({ ...t, [name]: r.data }));
      r.data.ok ? toast.success(`${name}: ${r.data.message}`) : toast.error(`${name}: ${r.data.message}`);
    } catch (e) {
      setTests(t => ({ ...t, [name]: { ok: false, message: e?.message || "Test failed" } }));
    }
  };

  const saveIconSize = async () => {
    setSavingIconSize(true);
    try {
      await saveSidebarIconSize(sidebarIconSize);
      toast.success(`Icon size saved at ${sidebarIconSize}px`);
    } catch {
      toast.error("Could not save the icon size");
    } finally {
      setSavingIconSize(false);
    }
  };

  const chooseWallpaper = async (next) => {
    const resolved = await setWallpaper(next);
    toast.success(`${WALLPAPER_LABELS[resolved] || resolved} wallpaper on`);
  };

  const chooseTheme = async (next) => {
    const resolved = next === "apple" ? "apple" : "vision";
    set("ui_theme", resolved);
    await setTheme(resolved);
    toast.success(resolved === "apple" ? "Apple theme on" : "Vision UI on");
  };

  return (
    <div className="px-1 sm:px-2 pb-4 max-w-5xl float-in">
      <span className="chip chip-cyan mb-4">Connections</span>
      <h1 className="font-display text-4xl sm:text-5xl font-extrabold tracking-tight">Wire up your sources</h1>
      <p className="text-slate-400 mt-2 max-w-2xl">Add credentials for the services you use. All tokens are stored per-user and only used to fetch <em>your</em> history. Leave any empty to skip — CineMind falls back to demo mode.</p>

      <div data-testid="section-appearance" className="mt-10 glass rounded-2xl p-6 lg:p-8">
        <div className="flex items-center gap-3 mb-1">
          <div className="w-10 h-10 rounded-lg chip-rose grid place-items-center"><AppWindow className="w-5 h-5" /></div>
          <div>
            <h3 className="font-display text-xl font-bold">Appearance</h3>
            <p className="text-xs text-slate-500 mt-0.5">Two full themes. Every card uses the same glass as the request select circle. The slider sets how see-through that glass is.</p>
          </div>
        </div>
        <div className="mt-5 grid sm:grid-cols-2 gap-3">
          <button
            type="button"
            data-testid="theme-vision-button"
            onClick={() => chooseTheme("vision")}
            className={`text-left rounded-2xl overflow-hidden border transition-colors ${
              theme === "vision"
                ? "border-[rgba(216,178,106,0.55)]"
                : "border-white/10 hover:border-white/20"
            }`}
          >
            <div className="theme-swatch theme-swatch-vision">
              <img src={THEME_SWATCH.vision} alt="" />
              <span className="preview-circle" aria-hidden="true">
                <span className="preview-circle-dot" />
              </span>
            </div>
            <div className="px-4 py-4">
              <div className="flex items-center gap-2">
                <Clapperboard className="w-4 h-4" />
                <span className="font-display font-bold">Vision UI</span>
              </div>
              <p className="text-xs text-slate-500 mt-2">Warm gold glass — the select circle on Death of the Pastor's Wife, on every component.</p>
            </div>
          </button>
          <button
            type="button"
            data-testid="theme-apple-button"
            onClick={() => chooseTheme("apple")}
            className={`text-left rounded-2xl overflow-hidden border transition-colors ${
              theme === "apple"
                ? "border-[rgba(10,132,255,0.55)]"
                : "border-white/10 hover:border-white/20"
            }`}
          >
            <div className="theme-swatch theme-swatch-apple">
              <img src={THEME_SWATCH.apple} alt="" />
              <span className="preview-circle" aria-hidden="true">
                <span className="preview-circle-dot" />
              </span>
            </div>
            <div className="px-4 py-4">
              <div className="flex items-center gap-2">
                <AppWindow className="w-4 h-4" />
                <span className="font-display font-bold">Apple</span>
              </div>
              <p className="text-xs text-slate-500 mt-2">Clear crystal glass — the select circle on Zip Wire, on every component.</p>
            </div>
          </button>
        </div>
        <div className="mt-6 liquid-glass-track rounded-2xl px-4 py-4">
          <div className="flex items-center justify-between gap-3 mb-3">
            <div className="flex items-center gap-3 min-w-0">
              <div className="w-10 h-10 rounded-lg chip-rose grid place-items-center shrink-0"><Droplets className="w-5 h-5" /></div>
              <div className="min-w-0">
                <h4 className="font-display font-bold">Transparency</h4>
                <p className="text-xs text-slate-500 mt-0.5">Same glass for Vision and Apple. Circles, cards, chips, and panels move together.</p>
              </div>
            </div>
            <span className="chip shrink-0" data-testid="glass-intensity-value">{glassIntensity}%</span>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-[10px] font-mono uppercase tracking-widest text-slate-500">Solid</span>
            <input
              type="range"
              min="0"
              max="100"
              value={glassIntensity}
              onChange={(e) => setGlassIntensity(Number(e.target.value))}
              className="liquid-glass-slider"
              data-testid="glass-intensity-slider"
              aria-label="Transparency"
            />
            <span className="text-[10px] font-mono uppercase tracking-widest text-slate-500">Dramatic</span>
          </div>
        </div>

        <div data-testid="wallpaper-picker" className="mt-6 glass rounded-2xl p-5">
          <div className="flex items-center gap-3 mb-1">
            <div className="w-10 h-10 rounded-lg chip-rose grid place-items-center shrink-0"><Image className="w-5 h-5" /></div>
            <div className="min-w-0">
              <h4 className="font-display font-bold">Wallpaper</h4>
              <p className="text-xs text-slate-500 mt-0.5">The background behind the glass. Poster glow is the original and stays exactly as it was.</p>
            </div>
          </div>
          <div className="mt-4 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
            {WALLPAPERS.map((name) => (
              <button
                key={name}
                type="button"
                data-testid={`wallpaper-${name}-button`}
                onClick={() => chooseWallpaper(name)}
                aria-pressed={wallpaper === name}
                className={`text-left rounded-2xl overflow-hidden border transition-colors ${
                  wallpaper === name ? "border-[rgba(216,178,106,0.55)]" : "border-white/10 hover:border-white/20"
                }`}
              >
                <span className={`wallpaper-swatch wallpaper-swatch-${name}`} />
                <span className="block px-3 py-2.5">
                  <span className="flex items-center gap-1.5">
                    <span className="font-display font-bold text-sm">{WALLPAPER_LABELS[name]}</span>
                    {wallpaper === name && <Check className="w-3.5 h-3.5 text-[#D8B26A]" />}
                  </span>
                  <span className="block text-[11px] text-slate-500 mt-1 leading-snug">{WALLPAPER_HINTS[name]}</span>
                </span>
              </button>
            ))}
          </div>
        </div>

        <div data-testid="icon-size-picker" className="mt-6 glass rounded-2xl p-5">
          <div className="flex items-center justify-between gap-3 mb-1">
            <div className="flex items-center gap-3 min-w-0">
              <div className="w-10 h-10 rounded-lg chip-rose grid place-items-center shrink-0"><Maximize2 className="w-5 h-5" /></div>
              <div className="min-w-0">
                <h4 className="font-display font-bold">Icon size</h4>
                <p className="text-xs text-slate-500 mt-0.5">How large the buttons on the icon rail are. Dragging previews it here; Save keeps it on your account.</p>
              </div>
            </div>
            <span className="chip shrink-0" data-testid="icon-size-value">{sidebarIconSize}px</span>
          </div>
          <div className="mt-4 flex items-center gap-3">
            <span className="text-[10px] font-mono uppercase tracking-widest text-slate-500">{MIN_ICON_SIZE}</span>
            <input
              type="range"
              min={MIN_ICON_SIZE}
              max={MAX_ICON_SIZE}
              value={sidebarIconSize}
              onChange={(e) => setSidebarIconSize(Number(e.target.value))}
              className="liquid-glass-slider"
              data-testid="icon-size-slider"
              aria-label="Icon size"
            />
            <span className="text-[10px] font-mono uppercase tracking-widest text-slate-500">{MAX_ICON_SIZE}</span>
          </div>
          <button
            type="button"
            data-testid="icon-size-save-button"
            onClick={saveIconSize}
            disabled={savingIconSize}
            className="chip hover:chip-rose transition-colors flex items-center gap-1.5 disabled:opacity-60 mt-4"
          >
            {savingIconSize ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />} Save
          </button>
        </div>
      </div>

      <div className="mt-6 grid gap-6">
        <Section title="Trakt.tv" icon={Film} tone="rose" onTest={()=>test("trakt")} status={tests.trakt} testid="section-trakt" hint="One-click sign-in via Trakt device code. Manual credentials below are optional.">
          <div className="sm:col-span-2">
            <DeviceConnect id="trakt" name="Trakt" startPath="/trakt/device/start" pollPath="/trakt/device/poll" codeField="device_code" disconnectPath="/trakt/disconnect"
              connected={form.trakt_connected} username={form.trakt_username ? `@${form.trakt_username}` : null} onChange={reload} />
          </div>
          <Field label="Client ID (manual, optional)" testid="trakt-client-id-input" value={form.trakt_client_id} onChange={v=>set("trakt_client_id", v)} />
          <Field label="Access token (manual, optional)" testid="trakt-access-token-input" value={form.trakt_access_token} onChange={v=>set("trakt_access_token", v)} type="password" />
        </Section>

        <Section title="Simkl" icon={Tv} tone="cyan" onTest={()=>test("simkl")} status={tests.simkl} testid="section-simkl" hint="One-click sign-in with a Simkl PIN. Manual credentials below are optional.">
          <div className="sm:col-span-2">
            <DeviceConnect id="simkl" name="Simkl" startPath="/simkl/pin/start" pollPath="/simkl/pin/poll" codeField="user_code" disconnectPath="/simkl/disconnect"
              connected={form.simkl_connected} username={form.simkl_username} onChange={reload} />
          </div>
          <Field label="Client ID (manual, optional)" testid="simkl-client-id-input" value={form.simkl_client_id} onChange={v=>set("simkl_client_id", v)} />
          <Field label="Access token (manual, optional)" testid="simkl-access-token-input" value={form.simkl_access_token} onChange={v=>set("simkl_access_token", v)} type="password" />
        </Section>

        <Section title="AniList" icon={Film} tone="rose" onTest={()=>test("anilist")} status={tests.anilist} testid="section-anilist" hint="Authorize AniList in the browser. Tokens stay on the server.">
          <div className="sm:col-span-2">
            <AnilistConnect
              connected={form.anilist_connected}
              username={form.anilist_username}
              clientConfigured={form.anilist_client_configured}
              onChange={reload}
            />
          </div>
        </Section>

        <Section title="Plex Media Server" icon={Server} tone="amber" onTest={()=>test("plex")} status={tests.plex} testid="section-plex" hint="Server URL like http://192.168.1.10:32400 and your X-Plex-Token.">
          <Field label="Server URL" testid="plex-url-input" value={form.plex_url} onChange={v=>set("plex_url", v)} placeholder="http://plex.local:32400" />
          <Field label="Plex token" testid="plex-token-input" value={form.plex_token} onChange={v=>set("plex_token", v)} type="password" />
        </Section>

        <Section title="MediaManager" icon={Library} tone="amber" onTest={()=>test("mediamanager")} status={tests.mediamanager} testid="section-mediamanager" hint="API port 8000 — not the Vite UI. Approvals go straight into Movies / TV.">
          <Field label="API URL" testid="mediamanager-url-input" value={form.mediamanager_url} onChange={v=>set("mediamanager_url", v)} placeholder="http://127.0.0.1:8000" />
          <Field label="Admin email" testid="mediamanager-email-input" value={form.mediamanager_email} onChange={v=>set("mediamanager_email", v)} placeholder="admin@example.com" />
          <Field
            label="Admin password"
            testid="mediamanager-password-input"
            value={form.mediamanager_password}
            onChange={v=>set("mediamanager_password", v)}
            type="password"
            placeholder={form.mediamanager_configured ? "Stored — enter a new password to replace it" : "MediaManager admin password"}
          />
        </Section>

        <Section title="Ollama (Local LLM)" icon={Cpu} tone="emerald" onTest={()=>test("ollama")} status={tests.ollama} testid="section-ollama" hint="Jobs and AI Picks call this machine’s Ollama. Default model is qwen3:14b.">
          <Field label="Ollama URL" testid="ollama-url-input" value={form.ollama_url} onChange={v=>set("ollama_url", v)} placeholder="http://localhost:11434" />
          <Field label="Model" testid="ollama-model-select" value={form.ollama_model} onChange={v=>set("ollama_model", v)} placeholder="qwen3:14b" />
        </Section>

        <div data-testid="section-local-requests" className="glass rounded-2xl p-6 lg:p-8">
          <div className="flex items-center gap-3 mb-1">
            <div className="w-10 h-10 rounded-lg chip-amber grid place-items-center"><Inbox className="w-5 h-5" /></div>
            <div>
              <h3 className="font-display text-xl font-bold">Local request queue</h3>
              <p className="text-xs text-slate-500 mt-0.5">Seer is not connected. Jobs can still require approval or mark titles as requested in the local queue.</p>
            </div>
          </div>
          <div className="mt-5 grid sm:grid-cols-3 gap-3">
            {[
              ["Recommendations only", "Picks stay in AI Picks"],
              ["Require approval", "Land in the Requests tab"],
              ["Automatically request", "Marked requested on this machine"],
            ].map(([title, hint]) => (
              <div key={title} className="text-left rounded-xl px-4 py-3 border border-white/10 bg-white/[0.03]">
                <div className="font-display font-bold text-sm">{title}</div>
                <div className="text-[11px] font-mono uppercase tracking-wider text-slate-500 mt-0.5">{hint}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="mt-10 flex items-center justify-end gap-3">
        <span className="text-sm text-slate-500">Changes autosave when you test — click Save to persist all fields.</span>
        <button data-testid="save-connections-button" onClick={() => save()} disabled={saving} className="glass-strong px-6 py-2.5 rounded-full text-sm font-medium hover:brutal-shadow-rose transition-shadow">
          {saving ? "Saving…" : "Save all"}
        </button>
      </div>
    </div>
  );
}

function Field({ label, value, onChange, type="text", testid, placeholder }) {
  return (
    <label className="block">
      <div className="text-xs font-mono uppercase tracking-widest text-slate-500 mb-1.5">{label}</div>
      <input
        data-testid={testid}
        type={type}
        value={value || ""}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full bg-white/[0.03] border border-white/10 rounded-lg px-4 py-2.5 text-sm outline-none focus:border-rose-500/50 focus:bg-white/[0.06] transition-colors"
      />
    </label>
  );
}

function Section({ title, icon: Icon, tone, children, onTest, status, testid, hint }) {
  return (
    <div data-testid={testid} className="glass rounded-2xl p-6 lg:p-8">
      <div className="flex items-start justify-between mb-1">
        <div className="flex items-center gap-3">
          <div className={`w-10 h-10 rounded-lg chip-${tone} grid place-items-center`}><Icon className="w-5 h-5" /></div>
          <div>
            <h3 className="font-display text-xl font-bold">{title}</h3>
            <p className="text-xs text-slate-500 mt-0.5">{hint}</p>
          </div>
        </div>
        <button
          data-testid={`test-${testid.replace("section-", "")}-button`}
          onClick={onTest}
          className="chip hover:chip-rose transition-colors flex items-center gap-2"
        >
          {status?.loading ? <Loader2 className="w-3 h-3 animate-spin"/> : status?.ok === true ? <CheckCircle2 className="w-3 h-3 text-emerald-400"/> : status?.ok === false ? <XCircle className="w-3 h-3 text-rose-400"/> : null}
          Test
        </button>
      </div>
      <div className="mt-5 grid sm:grid-cols-2 gap-4">{children}</div>
      {status?.message && (
        <div className={`mt-4 text-xs font-mono ${!status.ok ? "text-rose-400" : /not authorized/i.test(status.message) ? "text-amber-300" : "text-emerald-400"}`}>{status.message}</div>
      )}
    </div>
  );
}
