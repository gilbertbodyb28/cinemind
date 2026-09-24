import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { AlertTriangle, CheckCheck, Filter, RefreshCw, Star } from "lucide-react";
import {
  POSTER_GRID,
  SendToLibraryButton,
  StatusStat,
  mediaManagerLibraryLabel,
} from "@/components/ApproveRejectOverlay";
import TitleDetailModal from "@/components/TitleDetailModal";
import ReleaseTypeFilters from "@/components/ReleaseTypeFilters";
import { todayKey, toggleInSet } from "@/lib/mediaFilters";

/** The queue's footprint, so both tabs show the same card. */
const POSTER_SLOT = "w-11 h-11 sm:w-12 sm:h-12 shrink-0";
const POSTER_PILL = `${POSTER_SLOT} z-30 rounded-full glass grid place-items-center flex-col !gap-0 pointer-events-none`;
const PAGE = 40;

export default function Approved() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  // Paged by the server (GET /requests/page?view=approved), like the queue.
  const [total, setTotal] = useState(0);
  const [viewTotal, setViewTotal] = useState(0);
  const [facets, setFacets] = useState({ type_counts: {}, release_counts: {}, kind_counts: {}, undelivered: 0 });
  const [detailItem, setDetailItem] = useState(null);
  // Same tick boxes as the queue, so both tabs filter release and type alike.
  const [types, setTypes] = useState(() => new Set());
  const [release, setRelease] = useState(() => new Set());
  const sentinel = useRef(null);
  const requestSeq = useRef(0);
  const pagingRef = useRef(false);
  const itemsRef = useRef([]);
  itemsRef.current = items;

  const params = useMemo(() => ({
    view: "approved",
    types: [...types].join(","),
    release: [...release].join(","),
    today: todayKey(),
  }), [types, release]);
  const paramsRef = useRef(params);
  paramsRef.current = params;

  const applyPage = (data, replace) => {
    const rows = data?.items || [];
    setItems((current) => {
      if (replace) return rows;
      const have = new Set(current.map((row) => row.id));
      return [...current, ...rows.filter((row) => !have.has(row.id))];
    });
    setTotal(data?.total || 0);
    setViewTotal(data?.view_total || 0);
    if (data?.facets) setFacets(data.facets);
  };

  const load = async () => {
    const seq = ++requestSeq.current;
    try {
      const r = await api.get("/requests/page", { params: { ...paramsRef.current, offset: 0, limit: PAGE } });
      if (seq === requestSeq.current) applyPage(r.data, true);
    } catch (error) {
      toast.error(error.message || "Could not load approved titles");
    } finally {
      setLoading(false);
    }
  };

  const loadMore = async () => {
    if (pagingRef.current) return;
    pagingRef.current = true;
    const seq = requestSeq.current;
    try {
      const r = await api.get("/requests/page", {
        params: { ...paramsRef.current, offset: itemsRef.current.length, limit: PAGE },
      });
      if (seq === requestSeq.current) applyPage(r.data, false);
    } catch (error) {
      toast.error(error.message || "Could not load more approved titles");
    } finally {
      pagingRef.current = false;
    }
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [params]);

  // Counted by the server on everything approved, so each box shows what ticking it would leave.
  const counts = { movie: 0, show: 0, anime: 0, ...(facets.kind_counts || {}), undelivered: facets.undelivered || 0 };
  const releaseCounts = facets.release_counts || {};
  const typeCounts = facets.type_counts || {};

  const filtersActive = types.size > 0 || release.size > 0;

  const resetFilters = () => {
    setTypes(new Set());
    setRelease(new Set());
  };

  // The send button owns the request itself; this only folds its answer back in.
  const applyDelivery = (id, data) => {
    setItems((rows) => rows.map((row) => (row.id === id ? { ...row, ...(data?.request || {}) } : row)));
  };

  useEffect(() => {
    const node = sentinel.current;
    if (!node) return undefined;
    const io = new IntersectionObserver(
      (entries) => { if (entries[0].isIntersecting) loadMore(); },
      { rootMargin: "1200px 0px" },
    );
    io.observe(node);
    return () => io.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items.length, total]);

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
        <span data-testid="approved-results" className="chip chip-cyan">{total} results</span>
        <span data-testid="approved-total" className="chip">{viewTotal} total</span>
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
          <span data-testid="approved-visible-count" className="chip shrink-0">{total} of {viewTotal}</span>
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
        {items.map((item, index) => (
          <ApprovedPoster key={item.id} item={item} index={index} onDelivered={applyDelivery} onOpenDetails={() => setDetailItem(item)} />
        ))}
      </div>

      {items.length < total && (
        <div ref={sentinel} data-testid="approved-sentinel" className="py-8 text-center text-xs text-[#8C7F6D]">
          Showing {items.length} of {total} — keep scrolling
        </div>
      )}

      {!loading && !total && (
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

function ApprovedPoster({ item, index, onDelivered, onOpenDetails }) {
  const undelivered = item.delivery_status === "not_delivered";

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
            {/* The warning keeps its place on the card even though the send button
                is now the same one the queue uses. */}
            {undelivered ? (
              <span
                data-testid={`approved-undelivered-${item.id}`}
                title={item.delivery_error || "Not in MediaManager yet"}
                className="chip chip-amber shadow-md flex items-center gap-1.5"
              >
                <AlertTriangle className="w-3 h-3" /> Not sent
              </span>
            ) : (
              <span className="chip !bg-[#17130F]/75 shadow-md flex items-center gap-1.5">
                <CheckCheck className="w-3 h-3 text-[#A8BE92]" /> {mediaManagerLibraryLabel(item)}
              </span>
            )}
          </div>
          <div className="min-w-0 flex justify-end">
            <SendToLibraryButton
              rec={item}
              requestId={item.id}
              testid={`approved-send-${item.id}`}
              onDone={(id, data) => onDelivered(item.id, data)}
              className="!w-full"
            />
          </div>
        </div>

        {/* Same row the queue uses. Approved titles need no select, approve or
            reject, so those slots stay empty and match and rating keep the exact
            positions they have in the queue. */}
        <div className="absolute inset-x-0 bottom-0 z-30 px-3 pb-3 pt-16 bg-gradient-to-t from-black/80 via-black/40 to-transparent flex items-center justify-between gap-1">
          <span className={POSTER_SLOT} aria-hidden />
          <span className={POSTER_SLOT} aria-hidden />

          {item.match_score != null ? (
            <span
              data-testid={`approved-match-${item.id}`}
              title={`${Math.round(item.match_score)}% match to your taste`}
              className={POSTER_PILL}
            >
              <span className="font-mono text-sm font-bold leading-none text-[#D8B26A]">{Math.round(item.match_score)}%</span>
              <span className="font-mono text-[8px] uppercase tracking-wider text-[#8C7F6D] mt-0.5">match</span>
            </span>
          ) : (
            <span className={POSTER_SLOT} aria-hidden />
          )}

          <span className={POSTER_SLOT} aria-hidden />

          {item.rating != null ? (
            <span
              data-testid={`approved-rating-${item.id}`}
              title={Number(item.rating) > 0 ? `TMDb rating ${Number(item.rating).toFixed(1)} of 10` : "Not rated yet"}
              className={POSTER_PILL}
            >
              <Star className="w-3.5 h-3.5 fill-current text-[#D8B26A]" />
              <span className="font-mono text-sm font-bold leading-none text-[#F6EFE4] mt-0.5">
                {Number(item.rating) > 0 ? Number(item.rating).toFixed(1) : "–"}
              </span>
            </span>
          ) : (
            <span className={POSTER_SLOT} aria-hidden />
          )}
        </div>
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
