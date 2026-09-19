import { useEffect, useRef, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { Film, Sparkles, Server, Zap, LogIn, X, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

const GOOGLE_ERRORS = {
  google_not_configured: "Google sign-in is not configured on this server yet.",
  google_denied: "Google sign-in was cancelled.",
  google_state: "Google sign-in expired. Try again.",
  google_failed: "Google sign-in failed.",
};

export default function Landing() {
  const { user, loading, login, refresh } = useAuth();
  const navigate = useNavigate();
  const [authOpen, setAuthOpen] = useState(false);
  const [mode, setMode] = useState("login");
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ name: "", email: "", password: "" });
  const googlePollRef = useRef(null);
  const googleFocusRef = useRef(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const err = params.get("auth_error");
    if (err) {
      toast.error(GOOGLE_ERRORS[err] || "Could not sign in");
      params.delete("auth_error");
      const query = params.toString();
      window.history.replaceState({}, "", `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`);
    }
  }, []);

  useEffect(() => () => {
    if (googlePollRef.current) clearInterval(googlePollRef.current);
    if (googleFocusRef.current) window.removeEventListener("focus", googleFocusRef.current);
  }, []);

  if (!loading && user) return <Navigate to="/dashboard" replace />;

  const stopGooglePoll = () => {
    if (googlePollRef.current) {
      clearInterval(googlePollRef.current);
      googlePollRef.current = null;
    }
    if (googleFocusRef.current) {
      window.removeEventListener("focus", googleFocusRef.current);
      googleFocusRef.current = null;
    }
  };

  const startGooglePoll = () => {
    stopGooglePoll();
    const deadline = Date.now() + 5 * 60 * 1000;
    const tick = async () => {
      if (Date.now() > deadline) {
        stopGooglePoll();
        return;
      }
      const current = await refresh();
      if (current) {
        stopGooglePoll();
        navigate("/dashboard");
      }
    };
    googleFocusRef.current = tick;
    window.addEventListener("focus", tick);
    googlePollRef.current = setInterval(tick, 1500);
  };

  const startGoogle = () => {
    const port = window.location.port || "3005";
    const returnTo = encodeURIComponent(`http://localhost:${port}`);
    window.location.assign(`http://localhost:8001/api/auth/google/start?return_to=${returnTo}`);
  };

  const submit = async (event) => {
    event.preventDefault();
    if (mode === "register") {
      startGoogle();
      return;
    }
    setBusy(true);
    try {
      await login(form.email, form.password);
      navigate("/dashboard");
    } catch (error) {
      toast.error(error.message || "Could not sign in");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative min-h-screen overflow-hidden">
      <div className="absolute inset-0 -z-10">
        <div className="absolute top-[-10%] left-[-10%] w-[600px] h-[600px] rounded-full blur-[120px] bg-rose-600/20" />
        <div className="absolute bottom-[-10%] right-[-10%] w-[700px] h-[700px] rounded-full blur-[140px] bg-cyan-500/15" />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 w-[900px] h-[500px] rounded-full blur-[160px] bg-purple-600/10" />
      </div>

      <nav className="flex items-center justify-between px-8 lg:px-16 py-6">
        <div className="flex items-center gap-2">
          <div className="w-9 h-9 rounded-lg glass grid place-items-center">
            <Film className="w-5 h-5 text-rose-400" />
          </div>
          <span className="font-display text-xl font-bold tracking-tight">CineMind<span className="text-rose-500">.</span>ai</span>
        </div>
        <span className="chip chip-cyan">v1 · Beta</span>
      </nav>

      <section className="px-6 lg:px-16 pt-10 lg:pt-20 pb-24 max-w-7xl mx-auto">
        <div className="grid lg:grid-cols-12 gap-12 items-center">
          <div className="lg:col-span-7 float-in">
            <div className="chip chip-rose mb-8">
              <Sparkles className="w-3 h-3" /> AI-powered · Ollama · Private
            </div>
            <h1 className="font-display text-5xl sm:text-6xl lg:text-7xl font-extrabold leading-[1.02] tracking-tight">
              Your taste,<br />
              <span className="bg-gradient-to-r from-rose-400 via-fuchsia-400 to-cyan-300 bg-clip-text text-transparent">decoded by AI.</span>
            </h1>
            <p className="mt-8 text-lg text-slate-400 max-w-xl leading-relaxed">
              CineMind reads your watch history from <span className="text-slate-200 font-medium">Simkl</span>, <span className="text-slate-200 font-medium">Trakt.tv</span>, <span className="text-slate-200 font-medium">Plex</span>, and <span className="text-slate-200 font-medium">AniList</span>, then uses your own local <span className="text-slate-200 font-medium">Ollama</span> model to recommend the next thing you'll obsess over.
            </p>
            <div className="mt-10 flex flex-wrap items-center gap-4">
              <button
                data-testid="google-login-button"
                onClick={startGoogle}
                className="group relative px-8 py-4 rounded-full glass-strong font-medium text-white overflow-hidden hover:brutal-shadow-rose transition-all duration-300"
              >
                <span className="relative z-10 flex items-center gap-3">
                  <LogIn className="w-5 h-5" />
                  Continue with Google
                </span>
              </button>
              <button type="button" onClick={() => { setMode("login"); setAuthOpen(true); }} className="chip chip-cyan hover:bg-cyan-500/20 transition-colors cursor-pointer">
                Use email
              </button>
              <a href="#how" className="chip chip-cyan hover:bg-cyan-500/20 transition-colors cursor-pointer">How it works ↓</a>
            </div>

            <div className="mt-14 flex flex-wrap items-center gap-x-8 gap-y-3 text-sm text-slate-500">
              <div className="flex items-center gap-2"><span className="w-1.5 h-1.5 rounded-full bg-rose-500"/> Simkl</div>
              <div className="flex items-center gap-2"><span className="w-1.5 h-1.5 rounded-full bg-cyan-500"/> Trakt.tv</div>
              <div className="flex items-center gap-2"><span className="w-1.5 h-1.5 rounded-full bg-amber-500"/> Plex Media Server</div>
              <div className="flex items-center gap-2"><span className="w-1.5 h-1.5 rounded-full bg-violet-500"/> AniList</div>
              <div className="flex items-center gap-2"><span className="w-1.5 h-1.5 rounded-full bg-emerald-500"/> Ollama (local LLM)</div>
            </div>
          </div>

          <div className="lg:col-span-5 float-in" style={{animationDelay: '150ms'}}>
            <div className="relative">
              <div className="absolute -inset-4 bg-gradient-to-br from-rose-500/30 via-fuchsia-500/20 to-cyan-500/20 blur-3xl rounded-3xl" />
              <div className="relative glass rounded-3xl p-6 grain overflow-hidden">
                <div className="flex items-center justify-between mb-6">
                  <span className="chip">Weekly pick · 96% match</span>
                  <span className="font-mono text-xs text-slate-500">llama3.2</span>
                </div>
                <img src="https://image.tmdb.org/t/p/w780/tg9I5pOY4M9CKj8U0cxVBTsm5eh.jpg" className="w-full aspect-[2/3] object-cover rounded-2xl border border-white/10" alt="Foundation" />
                <div className="mt-5">
                  <h3 className="font-display text-2xl font-bold">Foundation</h3>
                  <p className="text-sm text-slate-400 mt-1 leading-relaxed">Because you loved Andor, Blade Runner 2049 & Arrival — dense worldbuilding and quiet menace.</p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section id="how" className="px-6 lg:px-16 pb-32 max-w-7xl mx-auto">
        <div className="grid md:grid-cols-3 gap-6">
          {[
            { icon: Server, title: "Connect your sources", desc: "Plug in Trakt.tv, Simkl, Plex, and AniList. We aggregate every episode, film, and anime you've watched." , tone: "rose"},
            { icon: Sparkles, title: "Distill your taste", desc: "Your history is turned into a Cinematic DNA profile — tropes, moods, complexity, and hidden patterns." , tone: "cyan"},
            { icon: Zap, title: "Recommend privately", desc: "Your local Ollama model produces personalized picks with real reasoning — never touches the cloud." , tone: "amber"},
          ].map((f,i)=>(
            <div key={i} className="glass rounded-2xl p-8 relative overflow-hidden group hover:border-rose-500/40 transition-colors" style={{animationDelay: `${i*100}ms`}}>
              <div className={`w-11 h-11 rounded-xl grid place-items-center mb-5 chip-${f.tone}`}>
                <f.icon className="w-5 h-5" />
              </div>
              <h3 className="font-display text-xl font-bold mb-2">{f.title}</h3>
              <p className="text-sm text-slate-400 leading-relaxed">{f.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {authOpen && (
        <div className="fixed inset-0 z-50 grid place-items-center p-4">
          <button className="absolute inset-0 bg-black/75 backdrop-blur-lg" onClick={() => setAuthOpen(false)} aria-label="Close sign in" />
          <div className="relative glass-strong rounded-3xl p-7 sm:p-8 w-full max-w-md float-in">
            <button onClick={() => setAuthOpen(false)} className="absolute right-4 top-4 w-9 h-9 rounded-full glass grid place-items-center" aria-label="Close">
              <X className="w-4 h-4" />
            </button>
            <span className="chip chip-rose mb-4">{mode === "register" ? "Google account" : "Private local account"}</span>
            <h2 className="font-display text-3xl font-extrabold">{mode === "login" ? "Welcome back" : "Create your CineMind"}</h2>
            <div className="grid grid-cols-2 gap-1 glass rounded-full p-1 mt-6">
              <button onClick={() => setMode("login")} className={`rounded-full py-2 text-sm transition-colors ${mode === "login" ? "bg-[rgba(216,178,106,0.18)] text-[#EBD3A3]" : "text-[#8C7F6D]"}`}>Sign in</button>
              <button data-testid="create-account-tab" onClick={() => setMode("register")} className={`rounded-full py-2 text-sm transition-colors ${mode === "register" ? "bg-[rgba(216,178,106,0.18)] text-[#EBD3A3]" : "text-[#8C7F6D]"}`}>Create account</button>
            </div>
            {mode === "register" ? (
              <div className="mt-5 space-y-4">
                <p className="text-sm text-slate-400">Sign in with Google. CineMind creates your local account from that profile.</p>
                <button type="button" data-testid="google-create-account-button" onClick={startGoogle} className="w-full glass-strong px-6 py-3 rounded-full text-sm font-medium flex items-center justify-center gap-2 hover:brutal-shadow-rose transition-shadow">
                  <LogIn className="w-4 h-4" />
                  Continue with Google
                </button>
              </div>
            ) : (
              <form onSubmit={submit} className="mt-5 space-y-4">
                <AuthField label="Email" type="email" value={form.email} onChange={(email) => setForm((f) => ({...f, email}))} autoComplete="email" />
                <AuthField label="Password" type="password" value={form.password} onChange={(password) => setForm((f) => ({...f, password}))} autoComplete="current-password" minLength={1} />
                <button type="button" onClick={startGoogle} className="w-full chip hover:chip-rose transition-colors py-3">Continue with Google</button>
                <button type="submit" disabled={busy} className="w-full glass-strong px-6 py-3 rounded-full text-sm font-medium flex items-center justify-center gap-2 hover:brutal-shadow-rose transition-shadow">
                  {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <LogIn className="w-4 h-4" />}
                  {busy ? "Opening…" : "Enter CineMind"}
                </button>
              </form>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function AuthField({ label, value, onChange, type = "text", ...props }) {
  return (
    <label className="block">
      <span className="text-xs font-mono uppercase tracking-widest text-slate-500 block mb-1.5">{label}</span>
      <input required type={type} value={value} onChange={(event) => onChange(event.target.value)} className="w-full bg-white/[0.03] border border-white/10 rounded-xl px-4 py-3 text-sm outline-none focus:border-rose-500/50 transition-colors" {...props} />
    </label>
  );
}
