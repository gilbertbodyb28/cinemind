/** Release and media-type filtering shared by the Requests queue and the Approved tab. */

/** Media buckets a row can land in; anime is its own lane next to tv and movie. */
export const TYPE_CHECKS = [
  { key: "movie", label: "Movies" },
  { key: "tv", label: "TV series" },
  { key: "anime", label: "Anime" },
];

/** Release buckets. Rows with neither a date nor a year stay out of both. */
export const RELEASE_CHECKS = [
  { key: "upcoming", label: "Upcoming titles" },
  { key: "released", label: "Released titles" },
];

export function typeBucket(item) {
  const kind = String(item?.type || "").toLowerCase();
  if (kind === "anime") return "anime";
  if (kind === "movie" || kind === "film") return "movie";
  return "tv";
}

/** Local YYYY-MM-DD, so "upcoming" follows the user's own calendar and not UTC. */
export function todayKey(now = new Date()) {
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

/**
 * "upcoming" while a title is not out yet, "released" once it is, and
 * "unknown" when the row carries neither a release date nor a year.
 */
export function releaseBucket(item, today = todayKey()) {
  const raw = item?.release_date ? String(item.release_date).slice(0, 10) : "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw > today ? "upcoming" : "released";
  const year = Number(raw.slice(0, 4) || item?.year);
  if (!Number.isFinite(year) || year === 0) return "unknown";
  // No day to compare, so the year decides and the current year counts as out.
  return year > Number(today.slice(0, 4)) ? "upcoming" : "released";
}

/** No box ticked means no narrowing, so an empty set lets everything through. */
export function matchesChecks(bucket, checked) {
  return !checked || checked.size === 0 || checked.has(bucket);
}

/** Tally per bucket so each box can show how much ticking it would leave. */
export function bucketCounts(rows, pick) {
  const tally = {};
  rows.forEach((row) => {
    const key = pick(row);
    tally[key] = (tally[key] || 0) + 1;
  });
  return tally;
}

/** Tick or untick one box in Set-backed checkbox state. */
export function toggleInSet(current, key) {
  const next = new Set(current);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  return next;
}
