/**
 * The blurred backdrop wash behind the app shell.
 *
 * Home picks it from the current hero; every other tab reuses the same image so
 * the whole app shares one look. The last image is remembered per browser, so a
 * deep link straight into Jobs or Requests still opens on a warm gradient.
 */
import { api } from "@/lib/api";

const STORAGE_KEY = "cinemind-ambient-img";

export function setAmbient(url) {
  if (!url) return;
  document.documentElement.style.setProperty("--ambient-img", `url("${url}")`);
  try {
    localStorage.setItem(STORAGE_KEY, url);
  } catch {
    /* private mode: the wash simply falls back to the recommendation fetch */
  }
}

export function currentAmbient() {
  const value = document.documentElement.style.getPropertyValue("--ambient-img").trim();
  return value && value !== "none" ? value : "";
}

export function restoreAmbient() {
  if (currentAmbient()) return true;
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      setAmbient(stored);
      return true;
    }
  } catch {
    /* ignore */
  }
  return false;
}

let pending = null;

/** Fetch one backdrop to seed the wash when nothing is stored yet. */
export function ensureAmbient() {
  if (restoreAmbient()) return Promise.resolve();
  if (pending) return pending;
  pending = api
    .get("/recommendations")
    .then((r) => {
      const row = (r.data || []).find((item) => item.backdrop || item.poster);
      if (row) setAmbient(row.backdrop || row.poster);
    })
    .catch(() => {})
    .finally(() => { pending = null; });
  return pending;
}
