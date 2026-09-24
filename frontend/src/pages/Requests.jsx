import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Check, Cpu, Filter, Inbox, Loader2, RefreshCw, Search, Star } from "lucide-react";
import {
  APPROVE_ICON,
  ActionIconButton,
  POSTER_GRID,
  REJECT_ICON,
  SendToLibraryButton,
  StatusStat,
  handleApproveError,
  sendToMediaManager,
} from "@/components/ApproveRejectOverlay";
import ModelPicker from "@/components/ModelPicker";
import TitleDetailModal from "@/components/TitleDetailModal";
import ReleaseTypeFilters from "@/components/ReleaseTypeFilters";
import { DEFAULT_MODEL } from "@/lib/models";
import { todayKey, toggleInSet } from "@/lib/mediaFilters";

const PENDING = new Set(["pending_approval", "pending", "requested"]);
/** Approved titles move to their own tab, so the queue only shows what still needs a decision. */
const DONE = new Set(["rejected", "approved", "available", "completed"]);
const SORTS = [
  { value: "added_desc", label: "Newest results first" },
  { value: "added_asc", label: "Oldest results first" },
  { value: "release_desc", label: "Newest release first" },
  { value: "release_asc", label: "Oldest release first" },
  { value: "match_desc", label: "Best match first" },
  { value: "title_asc", label: "Title A–Z" },
];

/** Select circle, taste match, rating and the two action buttons share this
 *  exact footprint, so the bottom row reads as one set of equal controls. */
const POSTER_SLOT = "w-11 h-11 sm:w-12 sm:h-12 shrink-0";
const POSTER_PILL = `${POSTER_SLOT} z-30 rounded-full glass grid place-items-center`;

const FIELD =
  "glass rounded-full box-border h-11 w-full px-4 text-sm inline-flex items-center gap-2 bg-transparent outline-none focus-within:border-[rgba(216,178,106,0.5)] transition-colors";
const SELECT = "bg-transparent outline-none w-full text-sm text-[#F6EFE4] [&>option]:bg-[#17130F]";

/** One server page. Filtering, sorting and counting happen in GET /requests/page. */
const PAGE = 40;
/** A background refresh reloads what is on screen, up to the server's page cap. */
const MAX_REFRESH = 200;
/** POST /requests/bulk takes at most this many ids per call. */
const BULK_BATCH = 50;
const EMPTY_FACETS = { genres: [], years: [], type_counts: {}, release_counts: {} };
/** How often the queue asks whether anything changed. The check is a tiny
 *  count + timestamp, so this can be short without refetching the whole list. */
const POLL_MS = 5000;

const BULK_CTRL =
  "glass rounded-full box-border h-12 w-full px-3 text-sm font-medium inline-flex items-center justify-center gap-2 whitespace-nowrap hover:brutal-shadow-rose transition-shadow disabled:opacity-40";

function isPending(item) {
  return PENDING.has(item?.status);
}

export default function Requests() {
  const navigate = useNavigate();
  const [items, setItems] = useState([]);
  const [busyId, setBusyId] = useState(null);
  const [selected, setSelected] = useState(() => new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [query, setQuery] = useState("");
  // Tick boxes, so several kinds of media can be kept at once; empty means all.
  const [types, setTypes] = useState(() => new Set());
  const [release, setRelease] = useState(() => new Set());
  const [genre, setGenre] = useState("all");
  const [fromYear, setFromYear] = useState("any");
  const [toYear, setToYear] = useState("any");
  const [fromRating, setFromRating] = useState("any");
  const [toRating, setToRating] = useState("any");
  const [sort, setSort] = useState("added_desc");
  const [detailItem, setDetailItem] = useState(null);
  // The account's Ollama model. Seeded from the saved value so the picker opens
  // on what this account actually uses, not on the build-time fallback.
  const [model, setModel] = useState(DEFAULT_MODEL);
  // The server holds the queue; this is only what has been paged in so far.
  // Downloading all of it was 4.7 MB for 9,936 rows, capped at 5,000 at that.
  const [total, setTotal] = useState(0);
  const [viewTotal, setViewTotal] = useState(0);
  const [pendingCount, setPendingCount] = useState(0);
  const [facets, setFacets] = useState(EMPTY_FACETS);
  const [loaded, setLoaded] = useState(false);
  const [debouncedQuery, setDebouncedQuery] = useState("");
  // GET /requests hides rejected rows, so the tally comes from its own endpoint.
  const [stats, setStats] = useState({ total: 0, pending: 0, approved: 0, rejected: 0, blacklisted: 0 });
  const sentinel = useRef(null);
  // The poll reads these instead of the state, which its empty dep list would freeze.
  const workingRef = useRef(false);
  // Last count+timestamp seen from the server; null until the first check lands.
  const versionRef = useRef(null);
  // Answers to an older filter must not land on top of a newer one.
  const requestSeq = useRef(0);
  const pagingRef = useRef(false);
  const itemsRef = useRef([]);
  const paramsRef = useRef(null);

  const loadStats = async () => {
    try {
      const r = await api.get("/requests/stats");
      if (r.data) setStats(r.data);
    } catch {
      // The tally is informational; it must never break the queue.
    }
  };

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query.trim()), 250);
    return () => clearTimeout(timer);
  }, [query]);

  const pageParams = useMemo(() => ({
    view: "queue",
    q: debouncedQuery,
    types: [...types].join(","),
    release: [...release].join(","),
    genre,
    from_year: fromYear === "any" ? "" : fromYear,
    to_year: toYear === "any" ? "" : toYear,
    from_rating: fromRating === "any" ? "" : fromRating,
    to_rating: toRating === "any" ? "" : toRating,
    sort,
    // "Upcoming" follows the viewer's own calendar, as it did in the browser.
    today: todayKey(),
  }), [debouncedQuery, types, release, genre, fromYear, toYear, fromRating, toRating, sort]);
  paramsRef.current = pageParams;
  itemsRef.current = items;

  const applyPage = (data, replace) => {
    const rows = data?.items || [];
    setItems((current) => {
      if (replace) return rows;
      const have = new Set(current.map((row) => row.id));
      return [...current, ...rows.filter((row) => !have.has(row.id))];
    });
    setTotal(data?.total || 0);
    setViewTotal(data?.view_total || 0);
    setPendingCount(data?.pending_count || 0);
    setFacets({ ...EMPTY_FACETS, ...(data?.facets || {}) });
    setLoaded(true);
  };

  // quiet = background poll: a failed refresh must not spam toasts while the tab sits open.
  // keep = reload as many rows as are already on screen, so a refresh does not
  // throw the viewer back to the top.
  const load = async ({ quiet = false, keep = false } = {}) => {
    const seq = ++requestSeq.current;
    const size = keep ? Math.min(Math.max(itemsRef.current.length, PAGE), MAX_REFRESH) : PAGE;
    try {
      const r = await api.get("/requests/page", { params: { ...paramsRef.current, offset: 0, limit: size } });
      if (seq === requestSeq.current) applyPage(r.data, true);
    } catch (error) {
      if (!quiet) toast.error(error.message || "Could not load requests");
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
      toast.error(error.message || "Could not load more requests");
    } finally {
      pagingRef.current = false;
    }
  };

  const refresh = async (options) => { await Promise.all([load(options), loadStats()]); };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { loadStats(); }, []);

  // A new filter is a new list: first page, and a selection that only means what is in it.
  useEffect(() => {
    setSelected(new Set());
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pageParams]);

  useEffect(() => {
    api.get("/connections")
      .then((r) => r.data.ollama_model && setModel(r.data.ollama_model))
      .catch(() => {});
  }, []);

  // Job runs write straight into the queue, so the open tab has to go look for
  // them. Asking for the full list every few seconds would mean refetching a
  // megabyte of posters, so the tick reads a count + newest timestamp and only
  // pulls the list when a job has actually written something.
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      // Never overwrite the list mid-approve; the action refreshes it when it lands.
      if (document.hidden || workingRef.current) return;
      try {
        const r = await api.get("/requests/version");
        const token = `${r.data?.count ?? ""}:${r.data?.latest ?? ""}`;
        if (!alive || token === versionRef.current) return;
        // First tick after mount only records the token; the initial load is fresh.
        const first = versionRef.current === null;
        versionRef.current = token;
        if (!first) await refresh({ quiet: true, keep: true });
      } catch {
        // A dropped check is harmless; the next tick tries again.
      }
    };
    tick();
    const timer = setInterval(tick, POLL_MS);
    window.addEventListener("focus", tick);
    document.addEventListener("visibilitychange", tick);
    return () => {
      alive = false;
      clearInterval(timer);
      window.removeEventListener("focus", tick);
      document.removeEventListener("visibilitychange", tick);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Approving flips a row to approved before the next refresh; it leaves the queue at once.
  const queued = useMemo(
    () => items.filter((row) => !DONE.has(row.status)),
    [items],
  );

  const genreOptions = facets.genres || [];

  // Tenths, the precision TMDb actually scores in. Whole steps could only ask
  // for a band; 7.0 to 7.9 now says that outright, and 7.4 to 7.4 asks for 7.4.
  const RATING_OPTIONS = Array.from({ length: 101 }, (_, i) => ((100 - i) / 10).toFixed(1));

  const yearOptions = facets.years || [];

  // Counted by the server on the whole queue, so each box keeps showing what it would leave.
  const releaseCounts = facets.release_counts || {};
  const typeCounts = facets.type_counts || {};

  const resetFilters = () => {
    setQuery("");
    setTypes(new Set());
    setRelease(new Set());
    setGenre("all");
    setFromYear("any");
    setToYear("any");
    setFromRating("any");
    setToRating("any");
    setSort("added_desc");
  };

  const filtersActive =
    query.trim() !== "" || types.size > 0 || release.size > 0 || genre !== "all" || fromYear !== "any" ||
    toYear !== "any" || fromRating !== "any" || toRating !== "any";

  useEffect(() => { workingRef.current = Boolean(busyId) || bulkBusy; }, [busyId, bulkBusy]);

  const selectedIds = [...selected];

  // Rows that left the queue (rejected or approved) leave the server's counts too.
  const dropCounts = (count) => {
    setTotal((n) => Math.max(0, n - count));
    setViewTotal((n) => Math.max(0, n - count));
    setPendingCount((n) => Math.max(0, n - count));
  };

  const removeItems = (ids) => {
    const gone = new Set(ids);
    setItems((rows) => rows.filter((row) => !gone.has(row.id)));
    dropCounts(ids.length);
    setSelected((current) => {
      const next = new Set(current);
      ids.forEach((id) => next.delete(id));
      return next;
    });
  };

  const patchItem = (id, next) => {
    setItems((rows) => rows.map((row) => (row.id === id ? { ...row, ...next } : row)));
  };

  const toggleSelected = (id) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // Every pending title in the filter, not only the ones scrolled into view.
  const selectAllPending = async () => {
    try {
      const r = await api.get("/requests/page", { params: { ...paramsRef.current, offset: 0, limit: 1, with_ids: true } });
      setSelected(new Set(r.data?.pending_ids || []));
    } catch (error) {
      toast.error(error.message || "Could not select the queue");
    }
  };

  const clearSelected = () => setSelected(new Set());

  const approve = async (item, options) => {
    if (busyId || bulkBusy) return;
    const previous = item;
    setBusyId(item.id);
    if (isPending(item)) patchItem(item.id, { status: "approved" });
    try {
      const data = await sendToMediaManager(item, { requestId: item.id, options });
      patchItem(item.id, data?.request || { status: "approved", tmdb_id: data?.tmdb_id });
      dropCounts(1);
      setStats((n) => ({ ...n, approved: n.approved + 1, pending: Math.max(0, n.pending - 1) }));
      setSelected((current) => {
        const next = new Set(current);
        next.delete(item.id);
        return next;
      });
    } catch (error) {
      patchItem(item.id, previous);
      handleApproveError(error, navigate);
    } finally {
      setBusyId(null);
    }
  };

  const reject = async (item) => {
    if (busyId || bulkBusy) return;
    const previous = item;
    setBusyId(item.id);
    removeItems([item.id]);
    try {
      await api.post(`/requests/${item.id}/reject`);
      setStats((n) => ({ ...n, rejected: n.rejected + 1, pending: Math.max(0, n.pending - 1) }));
      toast.success(`${item.title} removed`);
    } catch (error) {
      setItems((rows) => [previous, ...rows.filter((row) => row.id !== previous.id)]);
      load({ quiet: true, keep: true });
      toast.error(error.message || "Could not reject");
    } finally {
      setBusyId(null);
    }
  };

  const retry = async (item) => {
    if (busyId || bulkBusy) return;
    setBusyId(item.id);
    try {
      const data = await api.post(`/requests/${item.id}/retry`);
      toast.success("Sent to MediaManager");
      patchItem(item.id, data?.request || { status: "approved" });
    } catch (error) {
      handleApproveError(error, navigate);
    } finally {
      setBusyId(null);
    }
  };

  // POST /requests/bulk handles 50 ids per call and silently ignored the rest,
  // while the page removed every selected title. Send the selection in batches
  // and count only what the server confirmed.
  const inBatches = async (ids, send) => {
    for (let start = 0; start < ids.length; start += BULK_BATCH) {
      // eslint-disable-next-line no-await-in-loop
      await send(ids.slice(start, start + BULK_BATCH));
    }
  };

  const bulkReject = async () => {
    const ids = selectedIds;
    if (!ids.length || bulkBusy) return;
    setBulkBusy(true);
    const done = [];
    try {
      await inBatches(ids, async (batch) => {
        const r = await api.post("/requests/bulk", { ids: batch, action: "reject" });
        const confirmed = r.data?.ids || [];
        done.push(...confirmed);
        removeItems(confirmed);
      });
      toast.success(`Removed ${done.length} title${done.length === 1 ? "" : "s"}`);
    } catch (error) {
      load({ quiet: true, keep: true });
      toast.error(error.message || "Could not reject");
    } finally {
      if (done.length) {
        setStats((n) => ({ ...n, rejected: n.rejected + done.length, pending: Math.max(0, n.pending - done.length) }));
      }
      setBulkBusy(false);
    }
  };

  const bulkApprove = async (options) => {
    const ids = selectedIds;
    if (!ids.length || bulkBusy) return;
    setBulkBusy(true);
    const okIds = [];
    const failed = new Set();
    try {
      await inBatches(ids, async (batch) => {
        const r = await api.post("/requests/bulk", { ids: batch, action: "approve", options });
        (r.data?.results || []).forEach((row) => (row.ok === false ? failed.add(row.id) : okIds.push(row.id)));
        const ok = new Set(okIds);
        setItems((rows) => rows.map((row) => (ok.has(row.id) ? { ...row, status: "approved" } : row)));
      });
    } catch (error) {
      handleApproveError(error, navigate);
    } finally {
      setSelected(failed);
      if (okIds.length) {
        dropCounts(okIds.length);
        setStats((n) => ({ ...n, approved: n.approved + okIds.length, pending: Math.max(0, n.pending - okIds.length) }));
        toast.success(`Sent ${okIds.length} to MediaManager`);
      }
      if (failed.size) toast.error(`${failed.size} could not be added`);
      setBulkBusy(false);
    }
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
    <div data-testid="requests-page" className="px-1 sm:px-2 pb-4 w-full float-in">
      <TitleDetailModal item={detailItem} onClose={() => setDetailItem(null)} />
      <span className="chip chip-cyan mb-4">Requests</span>
      <h1 className="font-display text-4xl sm:text-5xl font-extrabold tracking-tight">Approval queue</h1>
      <p className="text-slate-400 mt-2 max-w-2xl">Jobs set to require approval land here. Approve or Send to MediaManager both add the title to Movies or TV immediately. Rejected titles leave the list at once.</p>

      <div data-testid="request-tally" className="mt-6 flex flex-wrap items-center gap-2">
        <span data-testid="tally-results" className="chip chip-cyan">{total} results</span>
        <span data-testid="tally-queued" className="chip">{viewTotal} awaiting decision</span>
        <span data-testid="tally-approved" className="chip chip-emerald">{stats.approved} approved</span>
        <span data-testid="tally-rejected" className="chip chip-rose">{stats.rejected} rejected</span>
        <span data-testid="tally-blacklisted" className="chip chip-amber">{stats.blacklisted} blacklisted</span>
        <button
          type="button"
          data-testid="request-refresh-button"
          onClick={() => refresh()}
          className="chip hover:chip-rose transition-colors flex items-center gap-1.5"
        >
          <RefreshCw className="w-3 h-3" /> Refresh
        </button>
      </div>

      <div data-testid="request-model" className="mt-6 glass-strong rounded-2xl px-4 py-4 lg:px-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="font-display text-lg font-bold flex items-center gap-2">
              <Cpu className="w-4 h-4 text-[#D8B26A]" /> AI model
            </h3>
            <p className="text-xs text-slate-500 mt-0.5">Every model your Ollama host has pulled. Pick one and press Save to use it for this account.</p>
          </div>
          <ModelPicker value={model} onChange={setModel} testid="requests-model-picker" />
        </div>
      </div>

      <div data-testid="request-filters" className="mt-6 glass-strong rounded-2xl px-4 py-4 lg:px-5">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <h3 className="font-display text-lg font-bold flex items-center gap-2">
              <Filter className="w-4 h-4 text-[#D8B26A]" /> Filter the queue
            </h3>
            <p className="text-xs text-slate-500 mt-0.5">Tick what is out and what is still coming, keep only the media types you want, then search, narrow by genre and year, and choose the order.</p>
          </div>
          <span data-testid="request-visible-count" className="chip shrink-0">{total} of {viewTotal}</span>
        </div>

        <ReleaseTypeFilters
          prefix="request"
          release={release}
          onToggleRelease={(key) => setRelease((current) => toggleInSet(current, key))}
          types={types}
          onToggleType={(key) => setTypes((current) => toggleInSet(current, key))}
          releaseCounts={releaseCounts}
          typeCounts={typeCounts}
          className="mb-4"
        />

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
          <label className={FIELD}>
            <Search className="w-4 h-4 text-[#8C7F6D] shrink-0" />
            <input
              data-testid="request-search-input"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search titles"
              className="bg-transparent outline-none text-sm w-full placeholder:text-[#8C7F6D]"
            />
          </label>

          <label className={FIELD}>
            <select
              data-testid="request-genre-filter"
              value={genre}
              onChange={(event) => setGenre(event.target.value)}
              className={SELECT}
            >
              <option value="all">All genres</option>
              {genreOptions.map((name) => (
                <option key={name} value={name}>{name}</option>
              ))}
            </select>
          </label>

          <label className={FIELD}>
            <select
              data-testid="request-from-year"
              value={fromYear}
              onChange={(event) => setFromYear(event.target.value)}
              className={SELECT}
            >
              <option value="any">From any year</option>
              {yearOptions.map((year) => (
                <option key={year} value={year}>From {year}</option>
              ))}
            </select>
          </label>

          <label className={FIELD}>
            <select
              data-testid="request-to-year"
              value={toYear}
              onChange={(event) => setToYear(event.target.value)}
              className={SELECT}
            >
              <option value="any">To any year</option>
              {yearOptions.map((year) => (
                <option key={year} value={year}>To {year}</option>
              ))}
            </select>
          </label>

          <label className={FIELD}>
            <select
              data-testid="request-from-rating"
              value={fromRating}
              onChange={(event) => setFromRating(event.target.value)}
              className={SELECT}
            >
              <option value="any">From any rating</option>
              {RATING_OPTIONS.map((value) => (
                <option key={value} value={value}>From {value}</option>
              ))}
            </select>
          </label>

          <label className={FIELD}>
            <select
              data-testid="request-to-rating"
              value={toRating}
              onChange={(event) => setToRating(event.target.value)}
              className={SELECT}
            >
              <option value="any">To any rating</option>
              {RATING_OPTIONS.map((value) => (
                <option key={value} value={value}>To {value}</option>
              ))}
            </select>
          </label>

          <label className={FIELD}>
            <select
              data-testid="request-sort"
              value={sort}
              onChange={(event) => setSort(event.target.value)}
              className={SELECT}
            >
              {SORTS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
        </div>

        {filtersActive && (
          <button
            type="button"
            data-testid="request-clear-filters"
            onClick={resetFilters}
            className="chip hover:chip-rose transition-colors mt-3"
          >
            Clear filters
          </button>
        )}
      </div>

      {pendingCount > 0 && (
        <div data-testid="bulk-actions" className="mt-6 glass-strong rounded-2xl px-4 py-4 lg:px-5">
          <div className="mb-4">
            <h3 className="font-display text-lg font-bold">Bulk approve or reject</h3>
            <p className="text-xs text-slate-500 mt-0.5">Tick posters, then send them to MediaManager or remove them together.</p>
          </div>
          <div className="grid grid-cols-5 gap-2">
            <button type="button" data-testid="bulk-select-all-button" onClick={selectAllPending} className={BULK_CTRL}>Select all</button>
            <button type="button" data-testid="bulk-clear-button" onClick={clearSelected} className={BULK_CTRL}>Clear</button>
            <span data-testid="bulk-selected-count" className={BULK_CTRL}>{selected.size} selected</span>
            <button
              type="button"
              data-testid="bulk-approve-button"
              disabled={!selected.size || bulkBusy}
              onClick={() => bulkApprove()}
              className={BULK_CTRL}
            >
              {bulkBusy ? <Loader2 className="w-4 h-4 animate-spin" /> : <img src={APPROVE_ICON} alt="" className="w-5 h-5 object-contain" />}
              Approve selected
            </button>
            <button
              type="button"
              data-testid="bulk-reject-button"
              disabled={!selected.size || bulkBusy}
              onClick={bulkReject}
              className={BULK_CTRL}
            >
              <img src={REJECT_ICON} alt="" className="w-5 h-5 object-contain" />
              Reject selected
            </button>
          </div>
        </div>
      )}

      <div className={`mt-10 ${POSTER_GRID}`}>
        {queued.map((item, index) => (
          <RequestPoster
            key={item.id}
            item={item}
            index={index}
            busy={busyId === item.id || bulkBusy}
            selected={selected.has(item.id)}
            onToggleSelect={() => toggleSelected(item.id)}
            onOpenDetails={() => setDetailItem(item)}
            onApprove={(options) => approve(item, options)}
            onReject={() => reject(item)}
            onRetry={() => retry(item)}
          />
        ))}
      </div>

      {items.length < total && (
        <div ref={sentinel} data-testid="requests-sentinel" className="py-8 text-center text-xs text-[#8C7F6D]">
          Showing {queued.length} of {total} — keep scrolling
        </div>
      )}

      {loaded && !queued.length && !total && (
        <div className="glass rounded-2xl p-16 text-center mt-10">
          <Inbox className="w-10 h-10 mx-auto text-slate-600 mb-4" />
          {filtersActive ? (
            <p className="text-slate-400">Nothing matches these filters. <button type="button" onClick={resetFilters} className="text-white font-medium underline underline-offset-4">Clear them</button> to see the whole queue.</p>
          ) : (
            <p className="text-slate-400">No requests yet. Run a job with <span className="text-white font-medium">Require approval</span>.</p>
          )}
        </div>
      )}
    </div>
  );
}

function RequestPoster({ item, index, busy, selected, onToggleSelect, onOpenDetails, onApprove, onReject, onRetry }) {
  const pending = isPending(item);
  const failed = item.status === "request_failed" || item.status === "failed";

  // Anywhere on the card toggles the bulk selection; the buttons layered on top
  // (approve, reject, send, retry) keep their own jobs.
  const toggleFromCard = (event) => {
    if (!pending) return;
    if (event.target.closest("button, a, input, select, label")) return;
    onToggleSelect();
  };

  return (
    <div
      data-testid={`request-card-${item.id}`}
      className={`group relative float-in ${pending ? "cursor-pointer" : ""}`}
      style={{ animationDelay: `${index * 60}ms` }}
      onClick={toggleFromCard}
      onDoubleClick={(event) => {
        if (event.target.closest("button, a, input, select, label")) return;
        // The two clicks of a double-click already cancel each other out.
        onOpenDetails();
      }}
      title="Click to select · double-click for trailer, cast and description"
      role={pending ? "button" : undefined}
      aria-pressed={pending ? selected : undefined}
      tabIndex={pending ? 0 : undefined}
      onKeyDown={(event) => {
        if (!pending) return;
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onToggleSelect(); }
      }}
    >
      <div className={`poster-frame relative transition-[border-color,box-shadow] ${selected ? "is-selected" : ""}`}>
        {item.poster ? (
          <img
            src={item.poster}
            alt={item.title}
            loading="lazy"
            decoding="async"
            className={`aspect-[2/3] w-full object-cover poster-hover ${selected ? "brightness-[0.82]" : ""}`}
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
            <SendToLibraryButton rec={item} onSend={onApprove} className="!w-full" />
          </div>
        </div>

        {/* One row across the foot of the poster, in the order you act in:
            pick it, approve it, see the match, reject it, see the rating. */}
        <div className="absolute inset-x-0 bottom-0 z-30 px-3 pb-3 pt-16 bg-gradient-to-t from-black/80 via-black/40 to-transparent flex items-center justify-between gap-1">
          {pending ? (
            <button
              type="button"
              data-testid={`select-request-${item.id}`}
              aria-pressed={selected}
              aria-label={selected ? `Deselect ${item.title}` : `Select ${item.title}`}
              onClick={(event) => { event.preventDefault(); event.stopPropagation(); onToggleSelect(); }}
              className={`${POSTER_PILL} hover:border-[rgba(216,178,106,0.5)] transition-colors`}
            >
              {selected ? <Check className="w-6 h-6 text-[#D8B26A]" /> : <span className="w-5 h-5 rounded-full border border-[rgba(255,240,220,0.35)]" />}
            </button>
          ) : (
            <span className={POSTER_SLOT} aria-hidden />
          )}

          {pending ? (
            <ActionIconButton
              testid={`approve-request-${item.id}`}
              label="Approve"
              src={APPROVE_ICON}
              onClick={(event) => { event.preventDefault(); event.stopPropagation(); onApprove(); }}
              disabled={busy}
              className="!w-11 !h-11 sm:!w-12 sm:!h-12"
            />
          ) : (
            <span className={POSTER_SLOT} aria-hidden />
          )}

          {item.match_score != null ? (
            <span
              data-testid={`match-score-${item.id}`}
              title={`${Math.round(item.match_score)}% match to your taste — 100% is exactly your thing`}
              className={`${POSTER_PILL} flex-col !gap-0 pointer-events-none`}
            >
              <span className="font-mono text-sm font-bold leading-none text-[#D8B26A]">{Math.round(item.match_score)}%</span>
              <span className="font-mono text-[8px] uppercase tracking-wider text-[#8C7F6D] mt-0.5">match</span>
            </span>
          ) : (
            <span className={POSTER_SLOT} aria-hidden />
          )}

          {pending ? (
            <ActionIconButton
              testid={`reject-request-${item.id}`}
              label="Reject"
              src={REJECT_ICON}
              onClick={onReject}
              disabled={busy}
              className="!w-11 !h-11 sm:!w-12 sm:!h-12"
            />
          ) : (
            <span className={POSTER_SLOT} aria-hidden />
          )}

          {item.rating != null ? (
            <span
              data-testid={`rating-${item.id}`}
              title={Number(item.rating) > 0
                ? `TMDb rating ${Number(item.rating).toFixed(1)} of 10`
                : "Not rated yet — no votes on TMDb"}
              className={`${POSTER_PILL} flex-col !gap-0 pointer-events-none`}
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

        {failed && (
          <div className="absolute inset-x-0 bottom-0 p-4 bg-gradient-to-t from-black via-black/80 to-transparent flex justify-center">
            <button
              data-testid={`retry-request-${item.id}`}
              type="button"
              onClick={onRetry}
              disabled={busy}
              className="chip hover:chip-rose transition-colors flex items-center gap-1.5 backdrop-blur-md !bg-[#17130F]/80"
            >
              <RefreshCw className="w-3 h-3" /> Retry
            </button>
          </div>
        )}
      </div>

      <div className="mt-3.5">
        <div className="flex items-baseline justify-between gap-2">
          <h3 className="font-display font-bold text-xl line-clamp-2 leading-snug">{item.title}</h3>
          {item.year != null && <span className="font-mono text-xs text-slate-500 shrink-0">{item.year}</span>}
        </div>
        {(item.provider || item.external_request_id) && (
          <div className="flex flex-wrap gap-1.5 mt-2">
            {item.provider && <span className="chip chip-cyan">{item.provider}</span>}
            {item.external_request_id && <span className="chip">ext · {item.external_request_id}</span>}
          </div>
        )}
      </div>
    </div>
  );
}
