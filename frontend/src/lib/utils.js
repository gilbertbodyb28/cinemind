import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs) {
  return twMerge(clsx(inputs));
}

export function formatDate(value, fallback = "Not yet") {
  if (!value) return fallback;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return fallback;
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(date);
}

export function formatReleaseDate(value) {
  if (!value) return null;
  const raw = String(value);
  const match = raw.match(/^(\d{4})-(\d{2})(?:-(\d{2}))?/);
  if (!match) return null;
  const [, y, m, d] = match;
  return `${d || "01"}/${m}/${y}`;
}

export function titleCase(value = "") {
  return String(value)
    .trim()
    .toLowerCase()
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function modelLabel(model) {
  if (!model) return "Ollama";
  return model
    .replace(/:latest$/, "")
    .replace(/[-_]/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}
