import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Link2, Loader2, CheckCircle2, Copy, Unplug } from "lucide-react";

// A poll that fails for a moment (the service restarting, a network blip) is not
// a failed sign-in: the code stays valid, so polling carries on a few times.
const POLL_RETRIES = 6;

export default function DeviceConnect({ id, name, startPath, pollPath, codeField, disconnectPath, connected, checking, username, notice, onChange }) {
  const [device, setDevice] = useState(null);
  const [status, setStatus] = useState("idle"); // idle | pending | authorized | unverified | expired | denied | invalid | error
  const [detail, setDetail] = useState("");
  const timer = useRef(null);

  useEffect(() => () => clearTimeout(timer.current), []);

  const start = async () => {
    setStatus("pending");
    setDetail("");
    try {
      const r = await api.post(startPath);
      setDevice(r.data);
      window.open(r.data.verification_url, "_blank", "noopener");
      const deadline = Date.now() + r.data.expires_in * 1000;
      let failures = 0;
      const poll = async () => {
        if (Date.now() > deadline) { setStatus("expired"); return; }
        try {
          const p = await api.post(pollPath, { [codeField]: r.data[codeField] });
          failures = 0;
          const s = p.data.status;
          if (s === "authorized") { setStatus("authorized"); toast.success(`${name} connected${p.data.username ? ` as ${p.data.username}` : ""}`); onChange?.(); return; }
          // Signed in, but the provider did not confirm it yet: not shown as connected.
          if (s === "unverified") { setStatus("unverified"); setDetail(p.data.detail || ""); onChange?.(); return; }
          if (s === "expired" || s === "denied" || s === "invalid") { setStatus(s); setDetail(p.data.detail || ""); return; }
          timer.current = setTimeout(poll, Math.max(r.data.interval || 5, 5) * 1000 * (s === "slow_down" ? 2 : 1));
        } catch {
          failures += 1;
          if (failures > POLL_RETRIES) { setStatus("error"); return; }
          timer.current = setTimeout(poll, Math.max(r.data.interval || 5, 5) * 1000);
        }
      };
      timer.current = setTimeout(poll, (r.data.interval || 5) * 1000);
    } catch (e) {
      setStatus("error");
      toast.error(e?.message || e?.response?.data?.detail || `Could not start ${name} sign-in`);
    }
  };

  const disconnect = async () => {
    await api.post(disconnectPath);
    setDevice(null); setStatus("idle");
    toast(`${name} disconnected`);
    onChange?.();
  };

  // Sources asks the provider before it says "Connected" (POST /connections/verify).
  if (checking && status === "idle") {
    return (
      <div data-testid={`${id}-checking`} className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3 text-xs text-slate-400 font-mono">
        <Loader2 className="w-3.5 h-3.5 animate-spin" /> Checking the saved sign-in with {name}…
      </div>
    );
  }

  if (connected || status === "authorized") {
    return (
      <div data-testid={`${id}-connected`} className="flex items-center justify-between gap-3 rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-3">
        <div className="flex items-center gap-2 text-sm">
          <CheckCircle2 className="w-4 h-4 text-emerald-400" />
          <span>Connected{username ? <> as <span className="font-mono text-emerald-300">{username}</span></> : ""}</span>
        </div>
        <button data-testid={`${id}-disconnect-button`} onClick={disconnect} className="chip hover:chip-rose transition-colors flex items-center gap-1.5"><Unplug className="w-3 h-3" /> Disconnect</button>
      </div>
    );
  }

  if (status === "pending" && device) {
    return (
      <div data-testid={`${id}-device-pending`} className="rounded-xl border border-white/10 bg-white/[0.03] px-5 py-4">
        <div className="text-xs font-mono uppercase tracking-widest text-slate-500 mb-2">Enter this code at <a className="text-rose-300 underline" href={device.verification_url} target="_blank" rel="noreferrer">{device.verification_url.replace("https://", "")}</a></div>
        <div className="flex items-center gap-3">
          <span data-testid={`${id}-user-code`} className="font-mono text-3xl font-extrabold tracking-[0.3em] text-white">{device.user_code}</span>
          <button data-testid={`${id}-copy-code-button`} onClick={() => { navigator.clipboard?.writeText(device.user_code); toast("Code copied"); }} className="p-2 rounded-md hover:bg-white/5 text-slate-400"><Copy className="w-4 h-4" /></button>
          <span className="ml-auto flex items-center gap-2 text-xs text-slate-400"><Loader2 className="w-3.5 h-3.5 animate-spin" /> Waiting for approval…</span>
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-3 flex-wrap">
      <button data-testid={`${id}-connect-button`} onClick={start} disabled={status === "pending"} className="glass-strong px-5 py-2.5 rounded-full text-sm font-medium flex items-center gap-2 hover:brutal-shadow-rose transition-shadow">
        {status === "pending" ? <Loader2 className="w-4 h-4 animate-spin" /> : <Link2 className="w-4 h-4" />} Connect with {name}
      </button>
      {["expired", "denied", "invalid", "error", "unverified"].includes(status) && (
        <span data-testid={`${id}-device-status`} className="text-xs text-rose-300 font-mono">
          {status === "expired" ? "Code expired — try again"
            : status === "denied" ? "Authorization denied"
            : detail || "Sign-in failed — try again"}
        </span>
      )}
      {/* Why a stored sign-in stopped counting (the provider refused it), so Connect is offered again. */}
      {notice && status === "idle" && (
        <span data-testid={`${id}-auth-notice`} className="text-xs text-rose-300 font-mono">{notice}</span>
      )}
    </div>
  );
}
