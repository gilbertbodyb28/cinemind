import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { Check, Loader2, Plus, Tv, X } from "lucide-react";

/**
 * Mirrors MediaManager's own "Add series / Add movie" dialog: identity,
 * monitoring, monitor scope, quality & release rules and search-now.
 * Values below are MediaManager's API enums, so what is picked here is what
 * gets posted to /api/v1/tv/shows or /api/v1/movies.
 */
export const RESOLUTIONS = [
  { value: "any", label: "Any" },
  { value: "sd", label: "SD" },
  { value: "360p", label: "360p" },
  { value: "480p", label: "480p" },
  { value: "576p", label: "576p" },
  { value: "720p", label: "720p" },
  { value: "1080p", label: "1080p" },
  { value: "2160p", label: "2160p (4K)" },
  { value: "4320p", label: "4320p (8K)" },
];

const VIDEO_CODECS = [
  { value: "h264", label: "H.264 / AVC / x264" },
  { value: "h265", label: "H.265 / HEVC / x265" },
  { value: "av1", label: "AV1" },
];

const DYNAMIC_RANGES = [
  { value: "sdr", label: "SDR" },
  { value: "hdr10", label: "HDR10" },
  { value: "hdr10_plus", label: "HDR10+" },
  { value: "dolby_vision", label: "Dolby Vision" },
  { value: "hlg", label: "HLG" },
];

const AUDIO_FORMATS = [
  { value: "dolby_truehd", label: "Dolby TrueHD" },
  { value: "dolby_truehd_atmos", label: "Dolby TrueHD + Atmos" },
  { value: "ac3", label: "Dolby Digital / AC-3" },
  { value: "eac3", label: "Dolby Digital Plus / E-AC-3" },
  { value: "eac3_atmos", label: "Dolby Digital Plus + Atmos" },
  { value: "dts", label: "DTS" },
  { value: "dts_hd", label: "DTS-HD" },
  { value: "dts_hd_ma", label: "DTS-HD MA" },
  { value: "dts_x", label: "DTS:X" },
  { value: "aac", label: "AAC" },
  { value: "flac", label: "FLAC" },
];

const UNITS = { MB: 1024 ** 2, GB: 1024 ** 3, TB: 1024 ** 4 };

const CARD =
  "glass rounded-2xl px-4 py-3 text-left transition-colors w-full flex items-start gap-3 hover:border-[rgba(216,178,106,0.45)]";
const CARD_ON = "border-[rgba(216,178,106,0.55)] bg-[rgba(216,178,106,0.10)]";
const SELECT =
  "glass rounded-full h-11 w-full px-4 text-sm bg-transparent outline-none text-[#F6EFE4] [&>option]:bg-[#17130F] focus:border-[rgba(216,178,106,0.5)]";
const BOX =
  "glass rounded-xl px-3 py-2 text-xs flex items-center gap-2 cursor-pointer hover:border-[rgba(216,178,106,0.45)] transition-colors";

function isShow(item) {
  return ["show", "tv", "series", "anime"].includes(String(item?.type || "").toLowerCase());
}

function Choice({ active, title, subtitle, onClick, testid }) {
  return (
    <button type="button" data-testid={testid} onClick={onClick} className={`${CARD} ${active ? CARD_ON : ""}`}>
      <span className={`mt-0.5 w-5 h-5 rounded-full grid place-items-center shrink-0 border ${active ? "bg-[#D8B26A] border-[#D8B26A]" : "border-[rgba(255,240,220,0.35)]"}`}>
        {active && <Check className="w-3 h-3 text-[#17130F]" />}
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-medium text-[#F6EFE4]">{title}</span>
        {subtitle && <span className="block text-[11px] text-[#8C7F6D] mt-0.5 leading-snug">{subtitle}</span>}
      </span>
    </button>
  );
}

function Toggle({ checked, label, onChange, testid }) {
  return (
    <label className={`${BOX} ${checked ? CARD_ON : ""}`} data-testid={testid}>
      <input type="checkbox" checked={checked} onChange={onChange} className="accent-[#D8B26A] w-3.5 h-3.5" />
      <span className="text-[#F6EFE4]">{label}</span>
    </label>
  );
}

function SizeRow({ label, hint, value, onChange, testid }) {
  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs font-medium text-[#F6EFE4]">{label}</span>
        <span className="text-[10px] font-mono uppercase tracking-wider text-[#8C7F6D]">{hint}</span>
      </div>
      <div className="flex gap-2 mt-1.5">
        <input
          data-testid={`${testid}-amount`}
          value={value.amount}
          onChange={(event) => onChange({ ...value, amount: event.target.value.replace(/[^\d.]/g, "") })}
          placeholder="None"
          inputMode="decimal"
          className="glass rounded-full h-10 px-4 text-sm bg-transparent outline-none w-full placeholder:text-[#8C7F6D] focus:border-[rgba(216,178,106,0.5)]"
        />
        <select
          data-testid={`${testid}-unit`}
          value={value.unit}
          onChange={(event) => onChange({ ...value, unit: event.target.value })}
          className={`${SELECT} !h-10 !w-24`}
        >
          {Object.keys(UNITS).map((unit) => (
            <option key={unit} value={unit}>{unit}</option>
          ))}
        </select>
      </div>
    </div>
  );
}

const emptySize = () => ({ amount: "", unit: "GB" });

function toBytes(size) {
  const amount = Number.parseFloat(size?.amount);
  if (!Number.isFinite(amount) || amount <= 0) return null;
  return Math.round(amount * (UNITS[size.unit] || UNITS.GB));
}

export default function AddToLibraryDialog({ open, items = [], busy = false, onCancel, onConfirm }) {
  const list = useMemo(() => (Array.isArray(items) ? items : [items]).filter(Boolean), [items]);
  const single = list.length === 1 ? list[0] : null;
  const hasShow = list.some(isShow);
  const hasMovie = list.some((row) => !isShow(row));
  const looksAnime = list.some((row) => String(row?.type || "").toLowerCase() === "anime");

  const [seriesType, setSeriesType] = useState("standard");
  const [provider, setProvider] = useState("tmdb");
  const [monitoring, setMonitoring] = useState("monitored");
  const [scope, setScope] = useState("entire");
  const [minRes, setMinRes] = useState("any");
  const [maxRes, setMaxRes] = useState("any");
  const [codecs, setCodecs] = useState([]);
  const [ranges, setRanges] = useState([]);
  const [audio, setAudio] = useState([]);
  const [minEpisode, setMinEpisode] = useState(emptySize);
  const [maxEpisode, setMaxEpisode] = useState(emptySize);
  const [minSeason, setMinSeason] = useState(emptySize);
  const [maxSeason, setMaxSeason] = useState(emptySize);
  const [minMovie, setMinMovie] = useState(emptySize);
  const [maxMovie, setMaxMovie] = useState(emptySize);
  const [searchNow, setSearchNow] = useState(false);

  useEffect(() => {
    if (!open) return;
    setSeriesType(looksAnime ? "anime" : "standard");
    setProvider("tmdb");
    setMonitoring("monitored");
    setScope("entire");
    setMinRes("any");
    setMaxRes("any");
    setCodecs([]);
    setRanges([]);
    setAudio([]);
    setMinEpisode(emptySize());
    setMaxEpisode(emptySize());
    setMinSeason(emptySize());
    setMaxSeason(emptySize());
    setMinMovie(emptySize());
    setMaxMovie(emptySize());
    setSearchNow(false);
  }, [open, looksAnime]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event) => { if (event.key === "Escape" && !busy) onCancel?.(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onCancel]);

  if (!open) return null;

  const toggle = (values, setValues) => (value) => {
    setValues(values.includes(value) ? values.filter((item) => item !== value) : [...values, value]);
  };

  const confirm = () => {
    onConfirm?.({
      series_type: seriesType,
      metadata_provider: provider,
      monitoring,
      monitor_scope: scope,
      search_now: searchNow,
      release_rules: {
        minimum_resolution: minRes,
        maximum_resolution: maxRes,
        allowed_video_codecs: codecs,
        allowed_dynamic_ranges: ranges,
        allowed_audio_formats: audio,
        minimum_episode_size_bytes: toBytes(minEpisode),
        maximum_episode_size_bytes: toBytes(maxEpisode),
        minimum_season_pack_size_bytes: toBytes(minSeason),
        maximum_season_pack_size_bytes: toBytes(maxSeason),
        minimum_movie_size_bytes: toBytes(minMovie),
        maximum_movie_size_bytes: toBytes(maxMovie),
      },
    });
  };

  const heading = single ? `Add ${single.title}` : `Add ${list.length} titles`;
  const subheading = single
    ? `${single.year ?? "—"} · Confirm identity and monitoring before adding.`
    : "These settings apply to every selected title.";
  const action = single ? (isShow(single) ? "Add Series" : "Add Movie") : `Add ${list.length} titles`;

  // Portalled to body: the app shell uses filters/transforms, which would turn
  // position: fixed into a containing-block-relative offset.
  return createPortal(
    <div
      data-testid="add-to-library-dialog"
      className="fixed inset-0 z-[60] grid place-items-center px-3 py-6 bg-black/70 backdrop-blur-sm"
      onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onCancel?.(); }}
    >
      <div className="glass-shell rounded-[26px] w-full max-w-2xl max-h-[88vh] overflow-y-auto scroll-thin grain">
        <div className="sticky top-0 z-10 flex items-start gap-3 px-5 py-4 bg-[#17130F]/85 backdrop-blur-md border-b border-white/10">
          {single?.poster ? (
            <img src={single.poster} alt="" className="w-12 h-16 rounded-xl object-cover shrink-0" />
          ) : (
            <span className="w-12 h-16 rounded-xl glass grid place-items-center shrink-0">
              <Tv className="w-5 h-5 text-[#D8B26A]" />
            </span>
          )}
          <div className="min-w-0 flex-1">
            <h2 className="font-display text-xl font-bold leading-tight line-clamp-2">{heading}</h2>
            <p className="text-xs text-[#8C7F6D] mt-1">{subheading}</p>
          </div>
          <button
            type="button"
            data-testid="add-dialog-close"
            onClick={() => !busy && onCancel?.()}
            className="w-8 h-8 rounded-full glass grid place-items-center text-[#8C7F6D] hover:text-[#F6EFE4] transition-colors shrink-0"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="px-5 py-5 space-y-5">
          {hasShow && (
            <section className="glass rounded-2xl p-4">
              <div className="flex items-baseline justify-between gap-3">
                <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-[#8C7F6D]">Series identity</span>
                <span className="text-[11px] text-[#A8BE92] flex items-center gap-1"><Check className="w-3 h-3" /> Preview ready</span>
              </div>
              <h3 className="font-display text-lg font-bold mt-1">Classification and metadata</h3>
              <p className="text-[11px] text-[#8C7F6D] mt-0.5">
                Search source: {provider.toUpperCase()}{single?.tmdb_id ? ` · ID ${single.tmdb_id}` : ""}
              </p>

              <div className="flex items-baseline justify-between gap-3 mt-4">
                <span className="text-sm font-medium">Series type</span>
                <span className="text-[11px] text-[#8C7F6D]">Recommended: {looksAnime ? "Anime" : "Standard TV"}</span>
              </div>
              <div className="grid sm:grid-cols-2 gap-2 mt-2">
                <Choice
                  testid="add-dialog-series-anime"
                  active={seriesType === "anime"}
                  title="Anime"
                  subtitle="Allows verified absolute episode numbering."
                  onClick={() => setSeriesType("anime")}
                />
                <Choice
                  testid="add-dialog-series-standard"
                  active={seriesType === "standard"}
                  title="Standard TV"
                  subtitle="Uses season and episode numbering only."
                  onClick={() => setSeriesType("standard")}
                />
              </div>

              <div className="flex items-baseline justify-between gap-3 mt-4">
                <span className="text-sm font-medium">Metadata provider</span>
                <span className="text-[11px] text-[#8C7F6D]">Used for seasons, episodes and artwork.</span>
              </div>
              <div className="grid sm:grid-cols-2 gap-2 mt-2">
                <Choice
                  testid="add-dialog-provider-tmdb"
                  active={provider === "tmdb"}
                  title="TMDB"
                  subtitle="Available · Direct"
                  onClick={() => setProvider("tmdb")}
                />
                <Choice
                  testid="add-dialog-provider-tvdb"
                  active={provider === "tvdb"}
                  title="TVDB"
                  subtitle="Available · Direct"
                  onClick={() => setProvider("tvdb")}
                />
              </div>
            </section>
          )}

          {!hasShow && hasMovie && (
            <section className="glass rounded-2xl p-4">
              <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-[#8C7F6D]">Movie identity</span>
              <h3 className="font-display text-lg font-bold mt-1">Metadata provider</h3>
              <div className="grid sm:grid-cols-2 gap-2 mt-2">
                <Choice
                  testid="add-dialog-provider-tmdb"
                  active={provider === "tmdb"}
                  title="TMDB"
                  subtitle="Available · Direct"
                  onClick={() => setProvider("tmdb")}
                />
                <Choice
                  testid="add-dialog-provider-tvdb"
                  active={provider === "tvdb"}
                  title="TVDB"
                  subtitle="Available · Direct"
                  onClick={() => setProvider("tvdb")}
                />
              </div>
            </section>
          )}

          <section className="glass rounded-2xl p-4">
            <span className="text-sm font-medium">Monitoring</span>
            <div className="grid sm:grid-cols-2 gap-2 mt-2">
              <Choice
                testid="add-dialog-monitored"
                active={monitoring === "monitored"}
                title="Monitored"
                subtitle="Automatically manage selected releases."
                onClick={() => setMonitoring("monitored")}
              />
              <Choice
                testid="add-dialog-unmonitored"
                active={monitoring === "unmonitored"}
                title="Unmonitored"
                subtitle="Add it without automatic downloads."
                onClick={() => setMonitoring("unmonitored")}
              />
            </div>

            {hasShow && (
              <>
                <span className="text-sm font-medium block mt-4">Monitor scope</span>
                <div className="grid sm:grid-cols-2 gap-2 mt-2">
                  <Choice
                    testid="add-dialog-scope-entire"
                    active={scope === "entire"}
                    title="Entire Series"
                    subtitle="All current and future seasons and episodes."
                    onClick={() => setScope("entire")}
                  />
                  <Choice
                    testid="add-dialog-scope-missing"
                    active={scope === "missing"}
                    title="Missing Episodes"
                    subtitle="Missing aired episodes, plus new episodes when they air."
                    onClick={() => setScope("missing")}
                  />
                  <Choice
                    testid="add-dialog-scope-future"
                    active={scope === "future"}
                    title="Future Episodes"
                    subtitle="Only unreleased and newly discovered future episodes."
                    onClick={() => setScope("future")}
                  />
                  <Choice
                    testid="add-dialog-scope-specific"
                    active={scope === "specific"}
                    title="Specific Seasons or Episodes"
                    subtitle="Pick the seasons to monitor in MediaManager after adding."
                    onClick={() => setScope("specific")}
                  />
                </div>
              </>
            )}
          </section>

          <section className="glass rounded-2xl p-4">
            <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-[#8C7F6D]">Quality &amp; release rules</span>
            <h3 className="font-display text-lg font-bold mt-1">Quality &amp; release rules</h3>
            <p className="text-[11px] text-[#8C7F6D] mt-0.5">
              Only releases matching these rules will be automatically downloaded. Any means today&apos;s unrestricted behavior.
            </p>

            <div className="grid sm:grid-cols-2 gap-3 mt-4">
              <label className="min-w-0">
                <span className="text-xs font-medium block mb-1.5">Minimum Resolution</span>
                <select data-testid="add-dialog-min-resolution" value={minRes} onChange={(e) => setMinRes(e.target.value)} className={SELECT}>
                  {RESOLUTIONS.map((row) => <option key={row.value} value={row.value}>{row.label}</option>)}
                </select>
              </label>
              <label className="min-w-0">
                <span className="text-xs font-medium block mb-1.5">Maximum Resolution <span className="text-[#8C7F6D]">(optional)</span></span>
                <select data-testid="add-dialog-max-resolution" value={maxRes} onChange={(e) => setMaxRes(e.target.value)} className={SELECT}>
                  {RESOLUTIONS.map((row) => <option key={row.value} value={row.value}>{row.label}</option>)}
                </select>
              </label>
            </div>

            <Group
              title="Allowed Video Codecs"
              options={VIDEO_CODECS}
              values={codecs}
              onToggle={toggle(codecs, setCodecs)}
              onAny={() => setCodecs([])}
              testid="codec"
            />
            <Group
              title="Allowed Dynamic Range / HDR"
              options={DYNAMIC_RANGES}
              values={ranges}
              onToggle={toggle(ranges, setRanges)}
              onAny={() => setRanges([])}
              testid="hdr"
            />
            <Group
              title="Allowed Audio Formats"
              options={AUDIO_FORMATS}
              values={audio}
              onToggle={toggle(audio, setAudio)}
              onAny={() => setAudio([])}
              testid="audio"
            />

            {hasShow && (
              <div className="grid sm:grid-cols-2 gap-4 mt-5">
                <div className="space-y-3">
                  <div className="flex items-baseline justify-between">
                    <span className="text-sm font-medium">Episode Size</span>
                    <span className="text-[10px] font-mono uppercase tracking-wider text-[#8C7F6D]">Empty = no bound</span>
                  </div>
                  <SizeRow label="Minimum" hint="" value={minEpisode} onChange={setMinEpisode} testid="add-dialog-episode-min" />
                  <SizeRow label="Maximum" hint="" value={maxEpisode} onChange={setMaxEpisode} testid="add-dialog-episode-max" />
                </div>
                <div className="space-y-3">
                  <div className="flex items-baseline justify-between">
                    <span className="text-sm font-medium">Season Pack Size</span>
                    <span className="text-[10px] font-mono uppercase tracking-wider text-[#8C7F6D]">Empty = no bound</span>
                  </div>
                  <SizeRow label="Minimum" hint="" value={minSeason} onChange={setMinSeason} testid="add-dialog-season-min" />
                  <SizeRow label="Maximum" hint="" value={maxSeason} onChange={setMaxSeason} testid="add-dialog-season-max" />
                </div>
              </div>
            )}

            {hasMovie && (
              <div className="mt-5 space-y-3">
                <div className="flex items-baseline justify-between">
                  <span className="text-sm font-medium">Movie Size</span>
                  <span className="text-[10px] font-mono uppercase tracking-wider text-[#8C7F6D]">Empty = no bound</span>
                </div>
                <div className="grid sm:grid-cols-2 gap-3">
                  <SizeRow label="Minimum" hint="" value={minMovie} onChange={setMinMovie} testid="add-dialog-movie-min" />
                  <SizeRow label="Maximum" hint="" value={maxMovie} onChange={setMaxMovie} testid="add-dialog-movie-max" />
                </div>
              </div>
            )}
          </section>

          <label className="glass rounded-2xl p-4 flex items-start gap-3 cursor-pointer hover:border-[rgba(216,178,106,0.45)] transition-colors">
            <input
              type="checkbox"
              data-testid="add-dialog-search-now"
              checked={searchNow}
              onChange={(event) => setSearchNow(event.target.checked)}
              className="accent-[#D8B26A] w-4 h-4 mt-0.5"
            />
            <span className="min-w-0">
              <span className="block text-sm font-medium">Search and download now</span>
              <span className="block text-[11px] text-[#8C7F6D] mt-0.5">
                Wake the worker immediately. If no approved release exists, the persisted job follows the retry schedule.
              </span>
            </span>
          </label>
        </div>

        <div className="sticky bottom-0 flex items-center justify-end gap-2 px-5 py-4 bg-[#17130F]/85 backdrop-blur-md border-t border-white/10">
          <button
            type="button"
            data-testid="add-dialog-cancel"
            onClick={() => !busy && onCancel?.()}
            className="chip hover:chip-rose transition-colors px-5 py-2.5"
          >
            Cancel
          </button>
          <button
            type="button"
            data-testid="add-dialog-confirm"
            disabled={busy}
            onClick={confirm}
            className="glass rounded-full px-5 py-2.5 text-sm font-medium inline-flex items-center gap-2 hover:border-[rgba(216,178,106,0.55)] transition-colors disabled:opacity-50"
          >
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4 text-[#D8B26A]" />}
            {action}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function Group({ title, options, values, onToggle, onAny, testid }) {
  const any = values.length === 0;
  return (
    <div className="mt-5">
      <div className="flex items-baseline justify-between">
        <span className="text-sm font-medium">{title}</span>
        <span className="text-[11px] text-[#8C7F6D]">{any ? "Any" : `${values.length} selected`}</span>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mt-2">
        <Toggle checked={any} label="Any" onChange={onAny} testid={`add-dialog-${testid}-any`} />
        {options.map((option) => (
          <Toggle
            key={option.value}
            checked={values.includes(option.value)}
            label={option.label}
            onChange={() => onToggle(option.value)}
            testid={`add-dialog-${testid}-${option.value}`}
          />
        ))}
      </div>
    </div>
  );
}
