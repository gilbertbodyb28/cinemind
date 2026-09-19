import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { AlertTriangle, CheckCheck, Filter, Loader2, RefreshCw, Star } from "lucide-react";
import {
  POSTER_GRID,
  StatusStat,
  handleApproveError,
  mediaManagerLibraryLabel,
} from "@/components/ApproveRejectOverlay";
import { useNavigate } from "react-router-dom";
import TitleDetailModal from "@/components/TitleDetailModal";
import ReleaseTypeFilters from "@/components/ReleaseTypeFilters";
import {
  bucketCounts,
  matchesChecks,
  releaseBucket,
  toggleInSet,
  typeBucket,
} from "@/lib/mediaFilters";

const APPROVED = new Set(["approved", "available", "completed"]);
/** Same footprint as the queue's select circle. */
const POSTER_PILL =
  "absolute bottom-3 z-30 w-[3.75rem] h-[3.75rem] sm:w-16 sm:h-16 rounded-full glass grid place-items-center flex-col !gap-0 pointer-events-none";
const PAGE = 40;

function isApproved(item) {
  return APPROVED.has(item?.status);
}

export default function Approved() {
  const navigate = useNavigate();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  // Same chunked rendering as the queue, so a long list still scrolls at 60fps.
  const [limit, setLimit] = useState(PAGE);
  const [detailItem, setDetailItem] = useState(null);
  // Same tick boxes as the queue, so both tabs filter release and type alike.
  const [types, setTypes] = useState(() => new Set());
  const [release, setRelease] = useState(() => new Set());
  const sentinel = useRef(null);

  const load = async () => {
    try {
      const r = await api.get("/requests");
      setItems((r.data || []).filter(isApproved));
    } catch (error) {
      toast.error(error.message || "Could not load approved titles");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const counts = useMemo(() => {
    const tally = { movie: 0, show: 0, anime: 0, undelivered: 0 };
    items.forEach((row) => {
      const kind = String(row.type || "movie").toLowerCase();
      if (kind in tally) tally[kind] += 1;
      else tally.show += 1;
      if (row.delivery_status === "not_delivered") tally.undelivered += 1;
    });
    return tally;
  }, [items]);

  // Counted on everything approved, so each box shows what ticking it would leave.
  const releaseCounts = useMemo(() => bucketCounts(items, (row) => releaseBucket(row)), [items]);
  const typeCounts = useMemo(() => bucketCounts(items, typeBucket), [items]);

  const visible = useMemo(
    () => items.filter((row) => matchesChecks(typeBucket(row), types) && matchesChecks(releaseBucket(row), release)),
    [items, types, release],
  );

  const filtersActive = types.size > 0 || release.size > 0;

  const resetFilters = () => {
    setTypes(new Set());
    setRelease(new Set());
  };

  useEffect(() => { setLimit(PAGE); }, [types, release]);

  const shown = useMemo(() => visible.slice(0, limit), [visible, limit]);

  // Titles approved while MediaManager was unreachable stay here and can be sent again.
  const retryDelivery = async (item) => {
    try {
      const r = await api.post(`/requests/${item.id}/approve`, {});
      if (r.data?.delivery_error) {
        toast.error(r.data.delivery_error);
      } else {
        toast.success(`${item.title} sent to MediaManager`);
      }
      setItems((rows) => rows.map((row) => (row.id === item.id ? { ...row, ...(r.data?.request || {}) } : row)));
    } catch (error) {
      handleApproveError(error, navigate);
    }
  };

  useEffect(() => {
    const node = sentinel.current;
    if (!node) return undefined;
    const io = new IntersectionObserver(
      (entries) => { if (entries[0].isIntersecting) setLimit((n) => n + PAGE); },
      { rootMargin: "1200px 0px" },
    );
    io.observe(node);
    return () => io.disconnect();
  }, [shown.length]);

  return (
    <div data-testid="approved-page" className="px-1 sm:px-2 pb-4 w-full float-in">
      <TitleDetailModal item={detailItem} onClose={() => setDetailItem(null)} />
      <span className="chip chip-cyan mb-4">Approved</span>
      {/* Title and blurb share one row so the heading lines up with the queue's intro text. */}
      <div className="flex flex-col lg:flex-row lg:items-baseline gap-2 lg:gap-6">
        <h1 className="font-display text-4xl sm:text-5xl font-extrabold tracking-tight shrink-0">Approved titles</h1>
        <p className="text-slate-400 max-w-2xl">Everything you approved in the queue lands here and is on its way to Movies or TV in MediaManager.</p>
      </div>

      <div className="mt-6 flex flex-wrap items-center gap-2">
        <span data-testid="approved-results" className="chip chip-cyan">{visible.length} results</span>
        <span data-testid="approved-total" className="chip">{items.length} total</span>
        <span className="chip">{counts.movie} movies</span>
        <span className="chip">{counts.show} shows</span>
        <span className="chip">{counts.anime} anime</span>
        {counts.undelivered > 0 && (
          <span data-testid="approved-undelivered" className="chip chip-amber">{counts.undelivered} not sent</span>
        )}
        <button
          type="button"
          data-testid="approved-refresh-button"
          onClick={load}
          className="chip hover:chip-rose transition-colors flex items-center gap-1.5"
        >
          <RefreshCw className="w-3 h-3" /> Refresh
        </button>
      </div>

      <div data-testid="approved-filters" className="mt-6 glass-strong rounded-2xl px-4 py-4 lg:px-5">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <h3 className="font-display text-lg font-bold flex items-center gap-2">
              <Filter className="w-4 h-4 text-[#D8B26A]" /> Filter approved titles
            </h3>
            <p className="text-xs text-slate-500 mt-0.5">Tick what is out and what is still coming, and keep only the media types you want.</p>
          </div>
          <span data-testid="approved-visible-count" className="chip shrink-0">{visible.length} of {items.length}</span>
        </div>

        <ReleaseTypeFilters
          prefix="approved"
          release={release}
          onToggleRelease={(key) => setRelease((current) => toggleInSet(current, key))}
          types={types}
          onToggleType={(key) => setTypes((current) => toggleInSet(current, key))}
          releaseCounts={releaseCounts}
          typeCounts={typeCounts}
        />

        {filtersActive && (
          <button
            type="button"
            data-testid="approved-clear-filters"
            onClick={resetFilters}
            className="chip hover:chip-rose transition-colors mt-3"
          >
            Clear filters
          </button>
        )}
      </div>

      <div className={`mt-10 ${POSTER_GRID}`}>
        {shown.map((item, index) => (
          <ApprovedPoster key={item.id} item={item} index={index} onRetry={retryDelivery} onOpenDetails={() => setDetailItem(item)} />
        ))}
      </div>

      {shown.length < visible.length && (
        <div ref={sentinel} data-testid="approved-sentinel" className="py-8 text-center text-xs text-[#8C7F6D]">
          Showing {shown.length} of {visible.length} — keep scrolling
        </div>
      )}

      {!loading && !visible.length && (
        <div className="glass rounded-2xl p-16 text-center mt-10">
          <CheckCheck className="w-10 h-10 mx-auto text-slate-600 mb-4" />
          {filtersActive ? (
            <p data-testid="approved-empty-filtered" className="text-slate-400">Nothing matches these filters. <button type="button" onClick={resetFilters} className="text-white font-medium underline underline-offset-4">Clear them</button> to see everything approved.</p>
          ) : (
            <p className="text-slate-400">Nothing approved yet. Approve a title in <span className="text-white font-medium">Requests</span> and it shows up here.</p>
          )}
        </div>
      )}
    </div>
  );
}

function ApprovedPoster({ item, index, onRetry, onOpenDetails }) {
  const undelivered = item.delivery_status === "not_delivered";
  const [busy, setBusy] = useState(false);

  const retry = async () => {
    if (busy) return;
    setBusy(true);
    try { await onRetry(item); } finally { setBusy(false); }
  };

  return (
    <div
      data-testid={`approved-card-${item.id}`}
      className="group relative float-in cursor-pointer"
      style={{ animationDelay: `${index * 60}ms` }}
      onDoubleClick={(event) => {
        if (event.target.closest("button, a")) return;
        onOpenDetails();
      }}
      title="Double-click for trailer, cast and description"
    >
      <div className="poster-frame relative">
        {item.poster ? (
          <img
            src={item.poster}
            alt={item.title}
            loading="lazy"
            decoding="async"
            className="aspect-[2/3] w-full object-cover poster-hover"
          />
        ) : (
          <div className="aspect-[2/3] w-full grid place-items-center p-6 text-center text-slate-500 text-lg font-display font-bold">
            {item.title}
          </div>
        )}

        <div className="absolute top-3 left-3 right-3 z-30 grid grid-cols-2 gap-2 items-start">
          <div className="min-w-0 flex flex-col items-start gap-1.5">
            <StatusStat status={item.status} className="!w-full" />
            {item.type && (
              <span className="chip backdrop-blur-md !bg-[#17130F]/75 shadow-md">{item.type}</span>
            )}
          </div>
          <div className="min-w-0 flex justify-end">
            {undelivered ? (
              <button
                type="button"
                data-testid={`approved-retry-${item.id}`}
                onClick={retry}
                disabled={busy}
                title={item.delivery_error || "Not in MediaManager yet — send again"}
                className="chip chip-amber shadow-md flex items-center gap-1.5 disabled:opacity-60"
              >
                {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : <AlertTriangle className="w-3 h-3" />} Send again
              </button>
            ) : (
              <span className="chip !bg-[#17130F]/75 shadow-md flex items-center gap-1.5">
                <CheckCheck className="w-3 h-3 text-[#A8BE92]" /> {mediaManagerLibraryLabel(item)}
              </span>
            )}
          </div>
        </div>

        {item.match_score != null && (
          <span
            data-testid={`approved-match-${item.id}`}
            title={`${Math.round(item.match_score)}% match to your taste`}
            className={`${POSTER_PILL} left-1/2 -translate-x-1/2`}
          >
            <span className="font-mono text-sm font-bold leading-none text-[#D8B26A]">{Math.round(item.match_score)}%</span>
            <span className="font-mono text-[8px] uppercase tracking-wider text-[#8C7F6D] mt-0.5">match</span>
          </span>
        )}

        {item.rating != null && (
          <span
            data-testid={`approved-rating-${item.id}`}
            title={Number(item.rating) > 0 ? `TMDb rating ${Number(item.rating).toFixed(1)} of 10` : "Not rated yet"}
            className={`${POSTER_PILL} right-3`}
          >
            <Star className="w-3.5 h-3.5 fill-current text-[#D8B26A]" />
            <span className="font-mono text-sm font-bold leading-none text-[#F6EFE4] mt-0.5">
              {Number(item.rating) > 0 ? Number(item.rating).toFixed(1) : "–"}
            </span>
          </span>
        )}
      </div>

      <div className="mt-3.5">
        <div className="flex items-baseline justify-between gap-2">
          <h3 className="font-display font-bold text-xl line-clamp-2 leading-snug">{item.title}</h3>
          {item.year != null && <span className="font-mono text-xs text-slate-500 shrink-0">{item.year}</span>}
        </div>
        <div className="flex flex-wrap gap-1.5 mt-2">
          {item.provider && <span className="chip chip-cyan">{item.provider}</span>}
          {item.approved_at && (
            <span className="chip">{String(item.approved_at).slice(0, 10)}</span>
          )}
        </div>
      </div>
    </div>
  );
}
