import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { ListTodo, Loader2, Play, Eye, Copy, Trash2, Plus } from "lucide-react";
import { MatchStat } from "@/components/ApproveRejectOverlay";

const JOB_TYPES = [
  ["personalized", "Personalized"],
  ["discover", "Discover"],
  ["trakt", "Trakt"],
  ["simkl", "Simkl"],
  ["anilist", "AniList"],
];

const TYPE_PRESETS = {
  personalized: { candidate_sources: ["seed_expand"], media_types: ["movie", "tv"] },
  discover: { candidate_sources: ["seed_expand", "tmdb_discover"], media_types: ["movie", "tv"] },
  trakt: { candidate_sources: ["seed_expand", "trakt"] },
  simkl: { candidate_sources: ["seed_expand", "simkl"] },
  anilist: { candidate_sources: ["seed_expand", "anilist"], media_types: ["anime"], taste_sources: ["anilist"] },
};

const SOURCE_OPTIONS = ["seed_expand", "tmdb_discover", "tmdb_similar", "tmdb_recommendations", "trakt", "simkl", "anilist"];

const empty = () => ({
  name: "",
  description: "",
  enabled: true,
  job_type: "personalized",
  media_types: ["movie", "tv"],
  taste_sources: ["plex", "trakt", "simkl", "anilist"],
  candidate_sources: ["seed_expand", "tmdb_discover"],
  required_sources: [],
  include_genres: "",
  exclude_genres: "",
  min_year: "",
  max_year: "",
  upcoming_only: false,
  min_rating: "",
  min_vote_count: "",
  min_runtime: "",
  max_runtime: "",
  language: "",
  country: "",
  anime_format: "",
  anime_season: "",
  anime_status: "",
  studio: "",
  include_tags: "",
  min_episodes: "",
  max_episodes: "",
  timezone: "UTC",
  already_watched: true,
  already_in_library: true,
  already_requested: true,
  already_recommended: false,
  recommend_again_after_days: "",
  allow_if_feedback_changed: false,
  blacklisted: true,
  ai_enabled: false,
  candidate_limit: 40,
  final_recommendation_limit: 8,
  action_mode: "require_approval",
  schedule: "every_30m",
});

function fromJob(job) {
  const filters = job.filters || {};
  const exclusions = job.exclusions || {};
  return {
    ...empty(),
    ...job,
    include_genres: (filters.include_genres || []).join(", "),
    exclude_genres: (filters.exclude_genres || []).join(", "),
    min_year: filters.min_year ?? "",
    max_year: filters.max_year ?? "",
    upcoming_only: Boolean(filters.upcoming_only),
    min_rating: filters.min_rating ?? "",
    min_vote_count: filters.min_vote_count ?? "",
    min_runtime: filters.min_runtime ?? "",
    max_runtime: filters.max_runtime ?? "",
    language: filters.language ?? "",
    country: filters.country ?? "",
    anime_format: filters.anime_format ?? "",
    anime_season: filters.anime_season ?? "",
    anime_status: filters.anime_status ?? "",
    studio: filters.studio ?? "",
    include_tags: (filters.include_tags || []).join(", "),
    min_episodes: filters.min_episodes ?? "",
    max_episodes: filters.max_episodes ?? "",
    timezone: job.timezone || "UTC",
    required_sources: job.required_sources || [],
    already_watched: exclusions.already_watched !== false,
    already_in_library: exclusions.already_in_library !== false,
    already_requested: exclusions.already_requested !== false,
    already_recommended: Boolean(exclusions.already_recommended),
    recommend_again_after_days: exclusions.recommend_again_after_days ?? "",
    allow_if_feedback_changed: Boolean(exclusions.allow_if_feedback_changed),
    blacklisted: exclusions.blacklisted !== false,
  };
}

function toPayload(form) {
  const split = (value) => value.split(",").map((item) => item.trim()).filter(Boolean);
  return {
    name: form.name || "Untitled job",
    description: form.description || "",
    enabled: Boolean(form.enabled),
    job_type: form.job_type,
    media_types: form.media_types,
    taste_sources: form.taste_sources,
    candidate_sources: form.candidate_sources,
    required_sources: form.required_sources,
    filters: {
      include_genres: split(form.include_genres),
      exclude_genres: split(form.exclude_genres),
      min_year: form.min_year === "" ? null : Number(form.min_year),
      max_year: form.max_year === "" ? null : Number(form.max_year),
      // Only titles whose premiere (film, series or a coming season) is verified after today.
      upcoming_only: Boolean(form.upcoming_only),
      min_rating: form.min_rating === "" ? null : Number(form.min_rating),
      min_vote_count: form.min_vote_count === "" ? null : Number(form.min_vote_count),
      min_runtime: form.min_runtime === "" ? null : Number(form.min_runtime),
      max_runtime: form.max_runtime === "" ? null : Number(form.max_runtime),
      language: form.language || null,
      country: form.country || null,
      anime_format: form.anime_format || null,
      anime_season: form.anime_season || null,
      anime_status: form.anime_status || null,
      studio: form.studio || null,
      include_tags: split(form.include_tags),
      min_episodes: form.min_episodes === "" ? null : Number(form.min_episodes),
      max_episodes: form.max_episodes === "" ? null : Number(form.max_episodes),
    },
    exclusions: {
      already_watched: form.already_watched,
      already_in_library: form.already_in_library,
      already_requested: form.already_requested,
      already_recommended: form.already_recommended,
      recommend_again_after_days: form.recommend_again_after_days === "" ? null : Number(form.recommend_again_after_days),
      allow_if_feedback_changed: form.allow_if_feedback_changed,
      blacklisted: form.blacklisted,
    },
    ai_enabled: Boolean(form.ai_enabled),
    candidate_limit: Math.max(Number(form.candidate_limit) || 40, Number(form.final_recommendation_limit) || 8),
    final_recommendation_limit: Number(form.final_recommendation_limit) || 8,
    action_mode: form.action_mode,
    schedule: form.schedule,
    timezone: form.timezone || "UTC",
  };
}

export default function Jobs() {
  const navigate = useNavigate();
  const [jobs, setJobs] = useState([]);
  const [form, setForm] = useState(empty());
  const [editing, setEditing] = useState(null);
  const [preview, setPreview] = useState(null);
  const [runs, setRuns] = useState([]);
  const [busy, setBusy] = useState(false);

  // A failed request is not an empty list. Turning every error into [] made
  // saved jobs "disappear" whenever the page loaded while the backend was
  // restarting (runtime sync, 2026-09-24 08:33), with nothing on screen to say so.
  const reload = async (attempt = 0) => {
    try {
      const r = await api.get("/jobs");
      setJobs(r.data || []);
    } catch (error) {
      if (error?.status === undefined && attempt < 5) {
        await new Promise((resolve) => setTimeout(resolve, 1500 * (attempt + 1)));
        return reload(attempt + 1);
      }
      toast.error(error?.message || "Could not load jobs", {
        description: "Your saved jobs are not deleted — the server did not answer. Reload the page to try again.",
      });
    }
  };

  // Load once on mount; reload is recreated every render and re-running it would loop.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { reload(); }, []);

  const set = (key, value) => setForm((current) => ({ ...current, [key]: value }));
  const toggleList = (key, value) => {
    setForm((current) => {
      const list = current[key].includes(value)
        ? current[key].filter((item) => item !== value)
        : [...current[key], value];
      const next = { ...current, [key]: list };
      if (key === "candidate_sources") {
        next.required_sources = (next.required_sources || []).filter((item) => list.includes(item));
      }
      return next;
    });
  };
  const setJobType = (value) => {
    const preset = TYPE_PRESETS[value] || {};
    setForm((current) => {
      const sources = [...current.candidate_sources];
      for (const source of preset.candidate_sources || []) {
        if (!sources.includes(source)) sources.push(source);
      }
      const media = [...current.media_types];
      for (const kind of preset.media_types || []) {
        if (!media.includes(kind)) media.push(kind);
      }
      const taste = [...current.taste_sources];
      for (const source of preset.taste_sources || []) {
        if (!taste.includes(source)) taste.push(source);
      }
      return {
        ...current,
        job_type: value,
        candidate_sources: sources,
        media_types: media,
        taste_sources: taste,
        required_sources: (current.required_sources || []).filter((item) => sources.includes(item)),
      };
    });
  };

  const save = async () => {
    setBusy(true);
    try {
      if (editing) await api.put(`/jobs/${editing}`, toPayload(form));
      else await api.post("/jobs", toPayload(form));
      toast.success(editing ? "Job updated" : "Job created");
      setForm(empty());
      setEditing(null);
      await reload();
    } catch (error) {
      toast.error(error.message || "Could not save job");
    } finally {
      setBusy(false);
    }
  };

  const act = async (job, action) => {
    setBusy(true);
    try {
      if (action === "preview") {
        const r = await api.post(`/jobs/${job.id}/preview`);
        setPreview(r.data);
        toast.success(`${r.data.accepted?.length || 0} preview picks`);
      } else if (action === "run") {
        const r = await api.post(`/jobs/${job.id}/run`);
        if (r.data.status === "locked") toast.error("Job is already running");
        else if (r.data.status === "failed") toast.error(r.data.detail || "Job failed");
        else {
          // Say where the picks went: "recommendations_only" writes nothing to Requests,
          // which is the usual reason that tab looks empty after a run.
          const picks = r.data.accepted?.length ?? r.data.run?.accepted_count ?? 0;
          const mode = r.data.run?.action_mode || job.action_mode || "require_approval";
          const where = mode === "require_approval"
            ? `${picks} sent to Requests`
            : mode === "auto_request"
              ? `${picks} sent to MediaManager`
              : `${picks} picks — this job is set to Recommendations only, so nothing lands in Requests`;
          if (r.data.status === "completed_with_warnings") {
            // Name the warnings in the toast; the full list lives in Runtime logs.
            const codes = (r.data.warnings || []).map((row) => `${row.source} · ${row.code}`).join(", ");
            toast.warning(`Finished with warnings · ${where}`, {
              description: codes ? `${codes} — open Runtime logs for the details` : "Open Runtime logs for the details",
              action: { label: "Logs", onClick: () => navigate("/logs") },
            });
          } else toast.success(`Job finished · ${where}`);
        }
        await reload();
      } else if (action === "clone") {
        await api.post(`/jobs/${job.id}/clone`);
        toast.success("Job cloned");
        await reload();
      } else if (action === "delete") {
        await api.delete(`/jobs/${job.id}`);
        toast("Job deleted");
        await reload();
      } else if (action === "history") {
        const r = await api.get(`/jobs/${job.id}/runs`);
        setRuns(r.data);
      } else if (action === "toggle") {
        await api.put(`/jobs/${job.id}`, { enabled: !job.enabled });
        await reload();
      }
    } catch (error) {
      toast.error(error.message || "Job action failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="px-1 sm:px-2 pb-4 max-w-5xl float-in">
      <span className="chip chip-rose mb-4">Jobs</span>
      <h1 className="font-display text-4xl sm:text-5xl font-extrabold tracking-tight">Automation</h1>
      <p className="text-slate-400 mt-2 max-w-2xl">Preview, run and schedule the same recommendation pipeline. Results stay in your library; nothing here changes the look of CineMind.</p>

      <div className="mt-10 grid gap-6">
        {jobs.map((job) => (
          <div key={job.id} data-testid={`job-card-${job.id}`} className="glass rounded-2xl p-6 lg:p-8">
            <div className="flex items-start justify-between gap-4 flex-wrap">
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <span className={`chip ${job.enabled ? "chip-emerald" : ""}`}>{job.enabled ? "Enabled" : "Disabled"}</span>
                  <span className="chip chip-amber">{job.job_type}</span>
                  <span className="chip">{job.schedule}</span>
                </div>
                <h3 className="font-display text-xl font-bold">{job.name}</h3>
                <p className="text-xs text-slate-500 mt-1">{job.description || "No description"}</p>
              </div>
              <div className="flex flex-wrap gap-2">
                <button data-testid={`job-preview-${job.id}`} onClick={() => act(job, "preview")} className="chip hover:chip-rose transition-colors flex items-center gap-1.5"><Eye className="w-3 h-3" /> Preview</button>
                <button data-testid={`job-run-${job.id}`} onClick={() => act(job, "run")} className="chip hover:chip-rose transition-colors flex items-center gap-1.5"><Play className="w-3 h-3" /> Run now</button>
                <button onClick={() => { setEditing(job.id); setForm(fromJob(job)); }} className="chip hover:chip-rose transition-colors">Edit</button>
                <button onClick={() => act(job, "clone")} className="chip hover:chip-rose transition-colors flex items-center gap-1.5"><Copy className="w-3 h-3" /> Clone</button>
                <button onClick={() => act(job, "toggle")} className="chip hover:chip-rose transition-colors">{job.enabled ? "Disable" : "Enable"}</button>
                <button onClick={() => act(job, "history")} className="chip hover:chip-rose transition-colors">History</button>
                <button onClick={() => act(job, "delete")} className="chip hover:chip-rose transition-colors flex items-center gap-1.5"><Trash2 className="w-3 h-3" /> Delete</button>
              </div>
            </div>
          </div>
        ))}

        <div className="glass rounded-2xl p-6 lg:p-8">
          <div className="flex items-center gap-3 mb-6">
            <div className="w-10 h-10 rounded-lg chip-rose grid place-items-center"><ListTodo className="w-5 h-5" /></div>
            <div>
              <h3 className="font-display text-xl font-bold">{editing ? "Edit job" : "Create job"}</h3>
              <p className="text-xs text-slate-500 mt-0.5">Same pipeline for preview, manual run and schedule.</p>
            </div>
          </div>

          <div className="grid sm:grid-cols-2 gap-4">
            <Field label="Name" testid="job-name-input" value={form.name} onChange={(v) => set("name", v)} />
            <Field label="Description" testid="job-description-input" value={form.description} onChange={(v) => set("description", v)} />
            <label className="block">
              <div className="text-xs font-mono uppercase tracking-widest text-slate-500 mb-1.5">Job type</div>
              <select data-testid="job-type-select" value={form.job_type} onChange={(e) => setJobType(e.target.value)} className="w-full bg-white/[0.03] border border-white/10 rounded-lg px-4 py-2.5 text-sm outline-none focus:border-rose-500/50">
                {JOB_TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
            <label className="block">
              <div className="text-xs font-mono uppercase tracking-widest text-slate-500 mb-1.5">Schedule</div>
              <select data-testid="job-schedule-select" value={form.schedule} onChange={(e) => set("schedule", e.target.value)} className="w-full bg-white/[0.03] border border-white/10 rounded-lg px-4 py-2.5 text-sm outline-none focus:border-rose-500/50">
                <option value="every_30m">Every 30 minutes</option>
                <option value="manual">Manual</option>
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
              </select>
            </label>
            <Field label="Timezone" value={form.timezone} onChange={(v) => set("timezone", v)} placeholder="UTC" />
            <label className="flex items-end gap-3 pb-2">
              <input data-testid="job-enabled-input" type="checkbox" checked={form.enabled} onChange={(e) => set("enabled", e.target.checked)} />
              <span className="text-sm text-slate-300">Enabled</span>
            </label>
          </div>

          <div className="text-xs font-mono uppercase tracking-widest text-slate-500 mt-6 mb-3">Media</div>
          <div className="flex flex-wrap gap-2">
            {[["movie", "Movies"], ["tv", "TV"], ["anime", "Anime"]].map(([value, label]) => (
              <button key={value} type="button" onClick={() => toggleList("media_types", value)} className={`chip ${form.media_types.includes(value) ? "chip-rose" : ""}`}>{label}</button>
            ))}
          </div>

          <div className="text-xs font-mono uppercase tracking-widest text-slate-500 mt-6 mb-3">Taste sources</div>
          <div className="flex flex-wrap gap-2">
            {["plex", "trakt", "simkl", "anilist"].map((value) => (
              <button key={value} type="button" onClick={() => toggleList("taste_sources", value)} className={`chip ${form.taste_sources.includes(value) ? "chip-cyan" : ""}`}>{value}</button>
            ))}
          </div>

          <div className="text-xs font-mono uppercase tracking-widest text-slate-500 mt-6 mb-3">Candidate sources</div>
          <div className="flex flex-wrap gap-2">
            {SOURCE_OPTIONS.map((value) => (
              <button key={value} type="button" onClick={() => toggleList("candidate_sources", value)} className={`chip ${form.candidate_sources.includes(value) ? "chip-amber" : ""}`}>{value}</button>
            ))}
          </div>

          <div className="text-xs font-mono uppercase tracking-widest text-slate-500 mt-6 mb-3">Required sources</div>
          <div className="flex flex-wrap gap-2">
            {form.candidate_sources.filter((value) => value !== "seed_expand").map((value) => (
              <button
                key={value}
                type="button"
                data-testid={`required-source-${value}`}
                onClick={() => toggleList("required_sources", value)}
                className={`chip ${form.required_sources.includes(value) ? "chip-rose" : ""}`}
              >
                {value}
              </button>
            ))}
            {!form.candidate_sources.filter((value) => value !== "seed_expand").length && (
              <span className="text-xs text-slate-500">Optional unless you mark a live source required.</span>
            )}
          </div>

          <div className="grid sm:grid-cols-2 gap-4 mt-6">
            <Field label="Include genres" value={form.include_genres} onChange={(v) => set("include_genres", v)} placeholder="Sci-Fi, Drama" />
            <Field label="Exclude genres" value={form.exclude_genres} onChange={(v) => set("exclude_genres", v)} placeholder="Horror" />
            <Field label="Minimum year" value={form.min_year} onChange={(v) => set("min_year", v)} />
            <Field label="Maximum year" value={form.max_year} onChange={(v) => set("max_year", v)} />
            <div className="sm:col-span-2 flex flex-wrap items-center gap-2">
              <button type="button" data-testid="job-upcoming-only-toggle" onClick={() => set("upcoming_only", !form.upcoming_only)} className={`chip ${form.upcoming_only ? "chip-rose" : ""}`}>Only upcoming premieres</button>
              <span className="text-xs text-slate-500">A verified premiere after today inside the years above, including a new season of an older series.</span>
            </div>
            <Field label="Minimum rating" value={form.min_rating} onChange={(v) => set("min_rating", v)} placeholder="7.0" />
            <Field label="Minimum vote count" value={form.min_vote_count} onChange={(v) => set("min_vote_count", v)} />
            <Field label="Minimum runtime" value={form.min_runtime} onChange={(v) => set("min_runtime", v)} />
            <Field label="Maximum runtime" value={form.max_runtime} onChange={(v) => set("max_runtime", v)} />
            <Field label="Language" value={form.language} onChange={(v) => set("language", v)} placeholder="en" />
            <Field label="Country" value={form.country} onChange={(v) => set("country", v)} placeholder="US" />
            <Field label="Anime format" value={form.anime_format} onChange={(v) => set("anime_format", v)} placeholder="TV, Movie, ONA" />
            <Field label="Anime season" value={form.anime_season} onChange={(v) => set("anime_season", v)} placeholder="WINTER" />
            <Field label="Anime status" value={form.anime_status} onChange={(v) => set("anime_status", v)} placeholder="FINISHED" />
            <Field label="Studio" value={form.studio} onChange={(v) => set("studio", v)} placeholder="Kyoto Animation" />
            <Field label="Include tags" value={form.include_tags} onChange={(v) => set("include_tags", v)} placeholder="time travel" />
            <Field label="Minimum episodes" value={form.min_episodes} onChange={(v) => set("min_episodes", v)} />
            <Field label="Maximum episodes" value={form.max_episodes} onChange={(v) => set("max_episodes", v)} />
          </div>

          <div className="text-xs font-mono uppercase tracking-widest text-slate-500 mt-6 mb-3">Exclusions</div>
          <div className="flex flex-wrap gap-2">
            {[
              ["already_watched", "Already watched"],
              ["already_in_library", "In Plex library"],
              ["already_requested", "Already requested"],
              ["already_recommended", "Previously recommended"],
              ["allow_if_feedback_changed", "Allow again after feedback"],
              ["blacklisted", "Blacklisted"],
            ].map(([key, label]) => (
              <button key={key} type="button" onClick={() => set(key, !form[key])} className={`chip ${form[key] ? "chip-rose" : ""}`}>{label}</button>
            ))}
          </div>
          <div className="grid sm:grid-cols-2 gap-4 mt-4">
            <Field label="Allow again after days" value={form.recommend_again_after_days} onChange={(v) => set("recommend_again_after_days", v)} placeholder="Leave empty to never repeat" />
          </div>

          <div className="grid sm:grid-cols-2 gap-4 mt-6">
            <label className="block">
              <div className="text-xs font-mono uppercase tracking-widest text-slate-500 mb-1.5">Action</div>
              <select data-testid="job-action-select" value={form.action_mode} onChange={(e) => set("action_mode", e.target.value)} className="w-full bg-white/[0.03] border border-white/10 rounded-lg px-4 py-2.5 text-sm outline-none focus:border-rose-500/50">
                <option value="recommendations_only">Recommendations only</option>
                <option value="require_approval">Require approval</option>
                <option value="auto_request">Automatically add to MediaManager</option>
              </select>
              <p className="text-[11px] text-slate-500 mt-1.5">Require approval puts posters in Requests. Automatically add sends them into MediaManager.</p>
            </label>
            <label className="flex items-end gap-3 pb-2">
              <input data-testid="job-ai-enabled-input" type="checkbox" checked={form.ai_enabled} onChange={(e) => set("ai_enabled", e.target.checked)} />
              <span className="text-sm text-slate-300">Rerank with Ollama Gemma 4 12B · konservativ</span>
            </label>
            <Field label="Candidate limit" value={form.candidate_limit} onChange={(v) => set("candidate_limit", v)} />
            <Field label="Final recommendation limit" value={form.final_recommendation_limit} onChange={(v) => set("final_recommendation_limit", v)} />
          </div>

          <div className="mt-8 flex items-center justify-end gap-3">
            {editing && (
              <button onClick={() => { setEditing(null); setForm(empty()); }} className="chip hover:chip-rose transition-colors">Cancel</button>
            )}
            <button data-testid="save-job-button" onClick={save} disabled={busy} className="glass-strong px-6 py-2.5 rounded-full text-sm font-medium flex items-center gap-2 hover:brutal-shadow-rose transition-shadow">
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
              {editing ? "Save job" : "Create job"}
            </button>
          </div>
        </div>
      </div>

      {preview?.accepted && (
        <div data-testid="job-preview-results" className="mt-8 glass rounded-2xl p-6">
          <div className="text-xs font-mono uppercase tracking-widest text-slate-500 mb-4">Preview — no requests sent</div>
          <div className="grid gap-3">
            {preview.accepted.map((row) => (
              <div key={`${row.title}-${row.year}`} className="text-left rounded-xl px-4 py-3 border border-white/10 bg-white/[0.03]">
                <div className="flex items-center justify-between gap-3">
                  <div className="font-display font-bold text-sm">{row.title}{row.year ? ` · ${row.year}` : ""}</div>
                  <MatchStat score={row.match_score ?? 0} />
                </div>
                <div className="flex flex-wrap gap-2 mt-2">
                  <span className="chip">{row.source || "seed"}</span>
                  {row.deterministic_score != null && <span className="chip">{row.deterministic_score}</span>}
                  {row.ai_reranked && <span className="chip chip-rose">AI reranked</span>}
                  <span className="chip chip-cyan">{row.filter_outcome || "accepted"}</span>
                </div>
                {row.why && <p className="text-xs text-slate-400 mt-2">{row.why}</p>}
              </div>
            ))}
          </div>
          {!!preview.warnings?.length && (
            <div className="flex flex-wrap gap-2 mt-4">
              {preview.warnings.map((row) => (
                <span key={`${row.source}-${row.code}`} className="chip chip-amber">{row.source} · {row.code}</span>
              ))}
            </div>
          )}
          {!!preview.rejected?.length && (
            <>
              <div className="text-xs font-mono uppercase tracking-widest text-slate-500 mt-6 mb-3">Excluded</div>
              <div className="flex flex-wrap gap-2">
                {preview.rejected.slice(0, 24).map((row) => (
                  <span key={`${row.title}-${row.filter_outcome}`} className="chip">{row.title} · {row.filter_outcome}</span>
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {!!runs.length && (
        <div data-testid="job-run-history" className="mt-8 glass rounded-2xl p-6">
          <div className="text-xs font-mono uppercase tracking-widest text-slate-500 mb-4">Run history</div>
          <div className="space-y-4">
            {runs.map((run) => (
              <div key={run.id} data-testid={`job-run-${run.id}`} className="text-sm">
                <div className="flex items-center justify-between gap-3 flex-wrap">
                  <div className="flex flex-wrap gap-2">
                    <span className="chip">{run.trigger_type}</span>
                    <span className={`chip ${run.status === "failed" ? "chip-rose" : run.status?.includes("warning") ? "chip-amber" : "chip-emerald"}`}>{run.status}</span>
                    {run.ai_reranked && <span className="chip chip-rose">AI reranked</span>}
                  </div>
                  <span className="text-slate-400">{run.accepted_count ?? 0} picks · {run.candidate_count ?? "—"} candidates</span>
                  <span className="font-mono text-[11px] text-slate-500">{run.started_at}</span>
                </div>
                {!!run.results?.length && (
                  <div className="flex flex-wrap gap-2 mt-2">
                    {run.results.slice(0, 12).map((row) => (
                      <span key={`${run.id}-${row.title || row.id}`} className="chip chip-cyan">{row.title || row.id}</span>
                    ))}
                  </div>
                )}
                {!!run.warnings?.length && (
                  <div className="flex flex-wrap gap-2 mt-2">
                    {run.warnings.map((row) => (
                      <span key={`${run.id}-${row.source}-${row.code}`} className="chip chip-amber">{row.source} · {row.code}</span>
                    ))}
                  </div>
                )}
                {run.error && <div className="text-xs text-rose-400 mt-2 font-mono">{run.error}</div>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Field({ label, value, onChange, testid, placeholder }) {
  return (
    <label className="block">
      <div className="text-xs font-mono uppercase tracking-widest text-slate-500 mb-1.5">{label}</div>
      <input
        data-testid={testid}
        value={value || ""}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full bg-white/[0.03] border border-white/10 rounded-lg px-4 py-2.5 text-sm outline-none focus:border-rose-500/50 focus:bg-white/[0.06] transition-colors"
      />
    </label>
  );
}
