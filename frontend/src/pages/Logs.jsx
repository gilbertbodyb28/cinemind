import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "sonner";
import {
  AlertCircle,
  AlertTriangle,
  Bug,
  CheckCheck,
  CheckCircle2,
  Loader2,
  RefreshCw,
  ScrollText,
  XCircle,
} from "lucide-react";

/** Six buckets, in the order you scan them: worst first, healthy last. */
const LEVELS = [
  { key: "failed", label: "Failed", chip: "chip-rose", icon: XCircle, blurb: "The run stopped before it finished" },
  { key: "error", label: "Error", chip: "chip-rose", icon: AlertTriangle, blurb: "A sync or a delivery did not go through" },
  { key: "bug", label: "Bugs", chip: "chip-amber", icon: Bug, blurb: "A provider broke — worth looking into" },
  { key: "warning", label: "Warning", chip: "chip-amber", icon: AlertCircle, blurb: "The run finished, but something was missing" },
  { key: "approved", label: "Approved", chip: "chip-cyan", icon: CheckCheck, blurb: "Titles that went to MediaManager" },
  { key: "ok", label: "Okey", chip: "chip-emerald", icon: CheckCircle2, blurb: "Everything went through cleanly" },
];

const LEVEL_BY_KEY = Object.fromEntries(LEVELS.map((row) => [row.key, row]));
const PAGE = 60;

function stamp(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 19).replace("T", " ");
  return date.toLocaleString(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export default function Logs() {
  const [rows, setRows] = useState([]);
  const [counts, setCounts] = useState({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  // Nothing selected means "show me everything", which is how you open the page.
  const [active, setActive] = useState(() => new Set());
  const [limit, setLimit] = useState(PAGE);

  const load = async (manual = false) => {
    if (manual) setBusy(true);
    try {
      const r = await api.get("/runtime/logs");
      setRows(r.data?.rows || []);
      setCounts(r.data?.counts || {});
      if (manual) toast.success("Logs refreshed");
    } catch (error) {
      toast.error(error.message || "Could not load runtime logs");
    } finally {
      setLoading(false);
      setBusy(false);
    }
  };

  useEffect(() => { load(); }, []);

  const toggle = (key) => {
    setLimit(PAGE);
    setActive((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const filtered = useMemo(
    () => (active.size ? rows.filter((row) => active.has(row.level)) : rows),
    [rows, active],
  );
  const shown = useMemo(() => filtered.slice(0, limit), [filtered, limit]);

  return (
    <div data-testid="logs-page" className="px-1 sm:px-2 pb-4 w-full float-in">
      <span className="chip chip-cyan mb-4">Runtime logs</span>
      <div className="flex flex-col lg:flex-row lg:items-baseline gap-2 lg:gap-6">
        <h1 className="font-display text-4xl sm:text-5xl font-extrabold tracking-tight shrink-0">Runtime logs</h1>
        <p className="text-slate-400 max-w-2xl">
          Every job run, history sync and approval in one list. Click a chip to keep only that kind of row.
        </p>
      </div>

      <div className="mt-6 flex flex-wrap items-center gap-2">
        {LEVELS.map(({ key, label, chip, icon: Icon, blurb }) => {
          const on = active.has(key);
          return (
            <button
              key={key}
              type="button"
              data-testid={`logs-filter-${key}`}
              onClick={() => toggle(key)}
              title={blurb}
              className={`chip ${on ? chip : ""} transition-colors flex items-center gap-1.5 ${
                on ? "" : "hover:chip-rose"
              }`}
            >
              <Icon className="w-3 h-3" /> {label}
              <span className="font-mono opacity-70">{counts[key] ?? 0}</span>
            </button>
          );
        })}
        <button
          type="button"
          data-testid="logs-refresh-button"
          onClick={() => load(true)}
          disabled={busy}
          className="chip hover:chip-rose transition-colors flex items-center gap-1.5 disabled:opacity-60"
        >
          {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />} Refresh
        </button>
        {active.size > 0 && (
          <button
            type="button"
            data-testid="logs-clear-filter"
            onClick={() => { setActive(new Set()); setLimit(PAGE); }}
            className="chip hover:chip-rose transition-colors"
          >
            Show all
          </button>
        )}
      </div>

      <div data-testid="logs-table" className="mt-8 glass rounded-2xl p-2 sm:p-4 overflow-x-auto scroll-thin">
        <table className="w-full min-w-[54rem] border-collapse text-sm">
          <thead>
            <tr className="text-left">
              {["Time", "Level", "Source", "Where", "Code", "Detail"].map((head) => (
                <th
                  key={head}
                  className="font-mono text-[10px] uppercase tracking-widest text-slate-500 font-bold px-3 pb-3 pt-2 whitespace-nowrap"
                >
                  {head}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((row, index) => {
              const level = LEVEL_BY_KEY[row.level] || LEVEL_BY_KEY.ok;
              const Icon = level.icon;
              return (
                <tr
                  key={`${row.run_id || row.code}-${row.time}-${index}`}
                  data-testid={`logs-row-${row.level}`}
                  className="border-t border-white/[0.06] align-top hover:bg-white/[0.03] transition-colors"
                >
                  <td className="px-3 py-3 font-mono text-[11px] text-slate-500 whitespace-nowrap">{stamp(row.time)}</td>
                  <td className="px-3 py-3">
                    <span className={`chip ${level.chip} flex w-fit items-center gap-1.5`}>
                      <Icon className="w-3 h-3" /> {level.label}
                    </span>
                  </td>
                  <td className="px-3 py-3">
                    <span className="chip">{row.source || "—"}</span>
                  </td>
                  <td className="px-3 py-3 text-slate-300 max-w-[16rem]">
                    <span className="line-clamp-2">{row.where || "—"}</span>
                    {row.trigger && (
                      <span className="block font-mono text-[10px] uppercase tracking-widest text-slate-500 mt-1">
                        {row.trigger}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-3 font-mono text-[11px] text-slate-400 whitespace-nowrap">{row.code || "—"}</td>
                  <td className="px-3 py-3 text-slate-400 max-w-[28rem]">{row.detail || "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {!loading && !filtered.length && (
          <div className="p-14 text-center">
            <ScrollText className="w-10 h-10 mx-auto text-slate-600 mb-4" />
            <p className="text-slate-400">
              {rows.length
                ? "No rows of that kind. Pick another chip, or Show all."
                : "Nothing logged yet. Run a job and it shows up here."}
            </p>
          </div>
        )}
      </div>

      {shown.length < filtered.length && (
        <div className="mt-5 text-center">
          <button
            type="button"
            data-testid="logs-more-button"
            onClick={() => setLimit((n) => n + PAGE)}
            className="chip hover:chip-rose transition-colors"
          >
            Showing {shown.length} of {filtered.length} — show more
          </button>
        </div>
      )}
    </div>
  );
}
