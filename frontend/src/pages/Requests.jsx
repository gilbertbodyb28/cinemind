import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Check, Filter, Inbox, Loader2, RefreshCw, Search, Star } from "lucide-react";
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
import AddToLibraryDialog from "@/components/AddToLibraryDialog";
import TitleDetailModal from "@/components/TitleDetailModal";
import ReleaseTypeFilters from "@/components/ReleaseTypeFilters";
import {
  bucketCounts,
  matchesChecks,
  releaseBucket,
  toggleInSet,
  typeBucket,
} from "@/lib/mediaFilters";

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

/** When the job put the title in the queue. Blank rows sort last, never first. */
function addedKey(item) {
  return String(item?.updated_at || item?.created_at || "");
}

/** Release day, falling back to the year so undated rows still sort sensibly. */
function releaseKey(item) {
  if (item?.release_date) return String(item.release_date).slice(0, 10);
  if (item?.year != null) return `${item.year}-00-00`;
  return "";
}

const PAGE = 40;
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
  // One dialog drives both single and bulk sends, so the options are identical.
  const [dialogItems, setDialogItems] = useState([]);
  const [dialogBusy, setDialogBusy] = useState(false);
  const [detailItem, setDetailItem] = useState(null);
  // Render the queue in chunks: 400+ poster cards at once is what made scrolling jank.
  const [limit, setLimit] = useState(PAGE);
  // GET /requests hides rejected rows, so the tally comes from its own endpoint.
  const [stats, setStats] = useState({ total: 0, pending: 0, approved: 0, rejected: 0, blacklisted: 0 });
  const sentinel = useRef(null);
  // The poll reads these instead of the state, which its empty dep list would freeze.
  const workingRef = useRef(false);
  // Last count+timestamp seen from the server; null until the first check lands.
  const versionRef = useRef(null);

  const loadStats = async () => {
    try {
      const r = await api.get("/requests/stats");
      if (r.data) setStats(r.data);
    } catch {
      // The tally is informational; it must never break the queue.
    }
  };

  // quiet = background poll: a failed refresh must not spam toasts while the tab sits open.
  const load = async ({ quiet = false } = {}) => {
    try {
      const r = await api.get("/requests");
      setItems((r.data || []).filter((row) => !DONE.has(row.status)));
    } catch (error) {
      if (!quiet) toast.error(error.message || "Could not load requests");
    }
  };

  const refresh = async (options) => { await Promise.all([load(options), loadStats()]); };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { refresh(); }, []);

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
        if (!first) await refresh({ quiet: true });
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

  const queued = useMemo(
    () => items.filter((row) => !DONE.has(row.status)),
    [items],
  );

  const genreOptions = useMemo(() => {
    const found = new Set();
    queued.forEach((row) => (row.genres || []).forEach((g) => found.add(g)));
    return [...found].sort((a, b) => a.localeCompare(b));
  }, [queued]);

  // Whole steps. Setting both ends to the same number asks for that rating and
  // nothing else: 7 to 7 keeps 7.0 and drops 6.9 and 7.1 alike.
  const RATING_OPTIONS = [10, 9, 8, 7, 6, 5, 4, 3, 2, 1];

  const yearOptions = useMemo(() => {
    const found = new Set();
    queued.forEach((row) => { if (row.year != null) found.add(Number(row.year)); });
    return [...found].sort((a, b) => b - a);
  }, [queued]);

  // Counted on the queue itself, so each box keeps showing what it would leave.
  const releaseCounts = useMemo(() => bucketCounts(queued, (row) => releaseBucket(row)), [queued]);
  const typeCounts = useMemo(() => bucketCounts(queued, typeBucket), [queued]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const from = fromYear === "any" ? null : Number(fromYear);
    const to = toYear === "any" ? null : Number(toYear);
    const ratingFrom = fromRating === "any" ? null : Number(fromRating);
    const ratingTo = toRating === "any" ? null : Number(toRating);
    const rows = queued.filter((row) => {
      if (needle && !String(row.title || "").toLowerCase().includes(needle)) return false;
      if (!matchesChecks(typeBucket(row), types)) return false;
      if (!matchesChecks(releaseBucket(row), release)) return false;
      if (genre !== "all" && !(row.genres || []).some((g) => g === genre)) return false;
      if (from != null && (row.year == null || Number(row.year) < from)) return false;
      if (to != null && (row.year == null || Number(row.year) > to)) return false;
      if (ratingFrom != null && (row.rating == null || Number(row.rating) < ratingFrom)) return false;
      if (ratingTo != null && (row.rating == null || Number(row.rating) > ratingTo)) return false;
      return true;
    });
    const sorted = [...rows];
    if (sort === "added_desc") sorted.sort((a, b) => addedKey(b).localeCompare(addedKey(a)));
    else if (sort === "added_asc") sorted.sort((a, b) => {
      const left = addedKey(a);
      const right = addedKey(b);
      if (!left) return 1;
      if (!right) return -1;
      return left.localeCompare(right);
    });
    else if (sort === "release_desc") sorted.sort((a, b) => releaseKey(b).localeCompare(releaseKey(a)));
    else if (sort === "release_asc") sorted.sort((a, b) => releaseKey(a).localeCompare(releaseKey(b)));
    else if (sort === "match_desc") sorted.sort((a, b) => (b.match_score || 0) - (a.match_score || 0));
    else sorted.sort((a, b) => String(a.title || "").localeCompare(String(b.title || "")));
    return sorted;
  }, [queued, query, types, release, genre, fromYear, toYear, fromRating, toRating, sort]);

  useEffect(() => { setLimit(PAGE); }, [query, types, release, genre, fromYear, toYear, fromRating, toRating, sort]);

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

  const shown = useMemo(() => visible.slice(0, limit), [visible, limit]);
  const pendingItems = useMemo(() => visible.filter(isPending), [visible]);
  const selectedItems = pendingItems.filter((row) => selected.has(row.id));

  const removeItems = (ids) => {
    const gone = new Set(ids);
    setItems((rows) => rows.filter((row) => !gone.has(row.id)));
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

  const selectAllPending = () => {
    setSelected(new Set(pendingItems.map((row) => row.id)));
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

  const bulkReject = async () => {
    const targets = selectedItems;
    if (!targets.length || bulkBusy) return;
    const ids = targets.map((row) => row.id);
    const snapshot = targets;
    setBulkBusy(true);
    removeItems(ids);
    try {
      await api.post("/requests/bulk", { ids, action: "reject" });
      setStats((n) => ({ ...n, rejected: n.rejected + ids.length, pending: Math.max(0, n.pending - ids.length) }));
      toast.success(`Removed ${ids.length} title${ids.length === 1 ? "" : "s"}`);
    } catch (error) {
      setItems((rows) => [...snapshot, ...rows]);
      toast.error(error.message || "Could not reject");
    } finally {
      setBulkBusy(false);
    }
  };

  const bulkApprove = async (options) => {
    const targets = selectedItems;
    if (!targets.length || bulkBusy) return;
    const ids = targets.map((row) => row.id);
    setBulkBusy(true);
    try {
      const r = await api.post("/requests/bulk", { ids, action: "approve", options });
      const results = r.data?.results || [];
      const failed = new Set(results.filter((row) => row.ok === false).map((row) => row.id));
      const okIds = ids.filter((id) => !failed.has(id));
      setItems((rows) => rows.map((row) => (
        okIds.includes(row.id) ? { ...row, status: "approved" } : row
      )));
      setSelected(failed);
      setStats((n) => ({ ...n, approved: n.approved + okIds.length, pending: Math.max(0, n.pending - okIds.length) }));
      if (okIds.length) toast.success(`Sent ${okIds.length} to MediaManager`);
      if (failed.size) toast.error(`${failed.size} could not be added`);
      setDialogItems([]);
    } catch (error) {
      handleApproveError(error, navigate);
    } finally {
      setBulkBusy(false);
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
  }, [visible.length]);

  const runDialog = async (options) => {
    const targets = dialogItems;
    if (!targets.length) return;
    setDialogBusy(true);
    try {
      if (targets.length === 1) {
        await approve(targets[0], options);
        setDialogItems([]);
      } else {
        await bulkApprove(options);
      }
    } finally {
      setDialogBusy(false);
    }
  };

  return (
    <div data-testid="requests-page" className="px-1 sm:px-2 pb-4 w-full float-in">
      <TitleDetailModal item={detailItem} onClose={() => setDetailItem(null)} />
      <AddToLibraryDialog
        open={dialogItems.length > 0}
        items={dialogItems}
        busy={dialogBusy}
        onCancel={() => setDialogItems([])}
        onConfirm={runDialog}
      />
      <span className="chip chip-cyan mb-4">Requests</span>
      <h1 className="font-display text-4xl sm:text-5xl font-extrabold tracking-tight">Approval queue</h1>
      <p className="text-slate-400 mt-2 max-w-2xl">Jobs set to require approval land here. Approve or Send to MediaManager both add the title to Movies or TV immediately. Rejected titles leave the list at once.</p>

      <div data-testid="request-tally" className="mt-6 flex flex-wrap items-center gap-2">
        <span data-testid="tally-results" className="chip chip-cyan">{visible.length} results</span>
        <span data-testid="tally-queued" className="chip">{queued.length} awaiting decision</span>
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

      <div data-testid="request-filters" className="mt-6 glass-strong rounded-2xl px-4 py-4 lg:px-5">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <h3 className="font-display text-lg font-bold flex items-center gap-2">
              <Filter className="w-4 h-4 text-[#D8B26A]" /> Filter the queue
            </h3>
            <p className="text-xs text-slate-500 mt-0.5">Tick what is out and what is still coming, keep only the media types you want, then search, narrow by genre and year, and choose the order.</p>
          </div>
          <span data-testid="request-visible-count" className="chip shrink-0">{visible.length} of {queued.length}</span>
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

      {pendingItems.length > 0 && (
        <div data-testid="bulk-actions" className="mt-6 glass-strong rounded-2xl px-4 py-4 lg:px-5">
          <div className="mb-4">
            <h3 className="font-display text-lg font-bold">Bulk approve or reject</h3>
            <p className="text-xs text-slate-500 mt-0.5">Tick posters, then send them to MediaManager or remove them together.</p>
          </div>
          <div className="grid grid-cols-5 gap-2">
            <button type="button" data-testid="bulk-select-all-button" onClick={selectAllPending} className={BULK_CTRL}>Select all</button>
            <button type="button" data-testid="bulk-clear-button" onClick={clearSelected} className={BULK_CTRL}>Clear</button>
            <span data-testid="bulk-selected-count" className={BULK_CTRL}>{selectedItems.length} selected</span>
            <button
              type="button"
              data-testid="bulk-approve-button"
              disabled={!selectedItems.length || bulkBusy}
              onClick={() => setDialogItems(selectedItems)}
              className={BULK_CTRL}
            >
              {bulkBusy ? <Loader2 className="w-4 h-4 animate-spin" /> : <img src={APPROVE_ICON} alt="" className="w-5 h-5 object-contain" />}
              Approve selected
            </button>
            <button
              type="button"
              data-testid="bulk-reject-button"
              disabled={!selectedItems.length || bulkBusy}
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
        {shown.map((item, index) => (
          <RequestPoster
            key={item.id}
            item={item}
            index={index}
            busy={busyId === item.id || bulkBusy}
            selected={selected.has(item.id)}
            onToggleSelect={() => toggleSelected(item.id)}
            onOpenDetails={() => setDetailItem(item)}
            onApprove={() => setDialogItems([item])}
            onReject={() => reject(item)}
            onRetry={() => retry(item)}
          />
        ))}
      </div>

      {shown.length < visible.length && (
        <div ref={sentinel} data-testid="requests-sentinel" className="py-8 text-center text-xs text-[#8C7F6D]">
          Showing {shown.length} of {visible.length} — keep scrolling
        </div>
      )}

      {!visible.length && (
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
              onClick={onApprove}
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
