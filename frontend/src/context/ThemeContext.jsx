import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

export const THEME_STORAGE_KEY = "cinemind-ui-theme";
export const GLASS_STORAGE_KEY = "cinemind-glass-intensity";
export const THEMES = ["vision", "apple"];
export const WALLPAPER_STORAGE_KEY = "cinemind-wallpaper";
/** "poster" keeps the original poster-lit background; the rest are gradients. */
export const WALLPAPERS = ["poster", "midnight", "ember", "dusk", "mocha", "aurora", "graphite"];
export const DEFAULT_GLASS_INTENSITY = 78;
export const ICON_SIZE_STORAGE_KEY = "cinemind-sidebar-icon-size";
/** Header icon button size in px. 40 is the original rail; 16 to 98 is the range. */
export const DEFAULT_ICON_SIZE = 40;
export const MIN_ICON_SIZE = 16;
export const MAX_ICON_SIZE = 98;

export function normalizeTheme(value) {
  return String(value || "").trim().toLowerCase() === "apple" ? "apple" : "vision";
}

export function normalizeGlassIntensity(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return DEFAULT_GLASS_INTENSITY;
  return Math.max(0, Math.min(100, Math.round(n)));
}

export function applyTheme(theme) {
  const next = normalizeTheme(theme);
  document.documentElement.dataset.theme = next;
  try {
    localStorage.setItem(THEME_STORAGE_KEY, next);
  } catch {
    /* ignore */
  }
  return next;
}

export function normalizeWallpaper(value) {
  const name = String(value || "").trim().toLowerCase();
  return WALLPAPERS.includes(name) ? name : "poster";
}

export function applyWallpaper(value) {
  const next = normalizeWallpaper(value);
  document.documentElement.dataset.wallpaper = next;
  try {
    localStorage.setItem(WALLPAPER_STORAGE_KEY, next);
  } catch {
    /* ignore */
  }
  return next;
}

export function readStoredWallpaper() {
  try {
    return normalizeWallpaper(localStorage.getItem(WALLPAPER_STORAGE_KEY));
  } catch {
    return "poster";
  }
}

export function applyGlassIntensity(value) {
  const n = normalizeGlassIntensity(value);
  const t = n / 100;
  const fill = (0.11 * (1 - t) + 0.016 * t).toFixed(3);
  const root = document.documentElement;
  root.dataset.glass = String(n);
  root.style.setProperty("--lg-t", t.toFixed(3));
  root.style.setProperty("--lg-blur", `${Math.round(16 + t * 56)}px`);
  root.style.setProperty("--lg-sat", `${Math.round(140 + t * 80)}%`);
  root.style.setProperty("--lg-fill", fill);
  root.style.setProperty("--lg-fill-strong", fill);
  root.style.setProperty("--lg-fill-shell", fill);
  root.style.setProperty("--lg-stroke", (0.06 + t * 0.1).toFixed(3));
  root.style.setProperty("--lg-shine", (0.06 + t * 0.16).toFixed(3));
  root.style.setProperty("--lg-rail", fill);
  root.style.setProperty("--lg-chip", fill);
  try {
    localStorage.setItem(GLASS_STORAGE_KEY, String(n));
  } catch {
    /* ignore */
  }
  return n;
}

export function readStoredTheme() {
  try {
    return normalizeTheme(localStorage.getItem(THEME_STORAGE_KEY));
  } catch {
    return "vision";
  }
}

export function normalizeIconSize(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return DEFAULT_ICON_SIZE;
  return Math.max(MIN_ICON_SIZE, Math.min(MAX_ICON_SIZE, Math.round(n)));
}

export function applyIconSize(value) {
  const n = normalizeIconSize(value);
  // The rail reads these; keeping them on the root means the size survives a
  // route change without every page threading the number through.
  const root = document.documentElement;
  root.style.setProperty("--rail-icon", `${n}px`);
  // The glyph kept the original 18/40 proportion at every size.
  root.style.setProperty("--rail-glyph", `${Math.round(n * 0.45)}px`);
  try {
    localStorage.setItem(ICON_SIZE_STORAGE_KEY, String(n));
  } catch {
    /* ignore */
  }
  return n;
}

export function readStoredIconSize() {
  try {
    return normalizeIconSize(localStorage.getItem(ICON_SIZE_STORAGE_KEY));
  } catch {
    return DEFAULT_ICON_SIZE;
  }
}

export function readStoredGlass() {
  try {
    return normalizeGlassIntensity(localStorage.getItem(GLASS_STORAGE_KEY));
  } catch {
    return DEFAULT_GLASS_INTENSITY;
  }
}

const ThemeContext = createContext(null);

export function ThemeProvider({ children }) {
  const { user } = useAuth();
  const [theme, setThemeState] = useState(readStoredTheme);
  const [glassIntensity, setGlassState] = useState(readStoredGlass);
  const [wallpaper, setWallpaperState] = useState(readStoredWallpaper);
  // The rail size previews live but only reaches the server on Save.
  const [sidebarIconSize, setSidebarIconSizeState] = useState(readStoredIconSize);
  const persistGlass = useRef(null);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    applyGlassIntensity(glassIntensity);
  }, [glassIntensity]);

  useEffect(() => {
    applyWallpaper(wallpaper);
  }, [wallpaper]);

  useEffect(() => {
    applyIconSize(sidebarIconSize);
  }, [sidebarIconSize]);

  useEffect(() => {
    if (!user) return undefined;
    let cancelled = false;
    api.get("/connections")
      .then((r) => {
        if (cancelled) return;
        setThemeState(normalizeTheme(r.data?.ui_theme));
        if (r.data?.glass_intensity != null) {
          setGlassState(normalizeGlassIntensity(r.data.glass_intensity));
        }
        if (r.data?.wallpaper) {
          setWallpaperState(normalizeWallpaper(r.data.wallpaper));
        }
        if (r.data?.sidebar_icon_size != null) {
          setSidebarIconSizeState(normalizeIconSize(r.data.sidebar_icon_size));
        }
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [user]);

  useEffect(() => () => {
    if (persistGlass.current) window.clearTimeout(persistGlass.current);
  }, []);

  const setTheme = useCallback(async (next) => {
    const resolved = applyTheme(next);
    setThemeState(resolved);
    if (!user) return resolved;
    try {
      await api.put("/connections", { ui_theme: resolved });
    } catch {
      /* keep local choice */
    }
    return resolved;
  }, [user]);

  const setWallpaper = useCallback(async (next) => {
    const resolved = applyWallpaper(next);
    setWallpaperState(resolved);
    if (!user) return resolved;
    try {
      await api.put("/connections", { wallpaper: resolved });
    } catch {
      /* keep local choice */
    }
    return resolved;
  }, [user]);

  const setGlassIntensity = useCallback((next) => {
    const resolved = applyGlassIntensity(next);
    setGlassState(resolved);
    if (!user) return resolved;
    if (persistGlass.current) window.clearTimeout(persistGlass.current);
    persistGlass.current = window.setTimeout(() => {
      api.put("/connections", { glass_intensity: resolved }).catch(() => {});
    }, 350);
    return resolved;
  }, [user]);

  // Dragging the slider only previews. Save is what writes it to the account,
  // so a size tried out and abandoned does not follow you to the next device.
  const setSidebarIconSize = useCallback((next) => {
    const resolved = normalizeIconSize(next);
    setSidebarIconSizeState(resolved);
    return resolved;
  }, []);

  const saveSidebarIconSize = useCallback(async (next) => {
    const resolved = normalizeIconSize(next ?? sidebarIconSize);
    setSidebarIconSizeState(resolved);
    if (!user) return resolved;
    await api.put("/connections", { sidebar_icon_size: resolved });
    return resolved;
  }, [user, sidebarIconSize]);

  const value = useMemo(
    () => ({
      theme,
      setTheme,
      isApple: theme === "apple",
      glassIntensity,
      setGlassIntensity,
      wallpaper,
      setWallpaper,
      sidebarIconSize,
      setSidebarIconSize,
      saveSidebarIconSize,
    }),
    [
      theme,
      setTheme,
      glassIntensity,
      setGlassIntensity,
      wallpaper,
      setWallpaper,
      sidebarIconSize,
      setSidebarIconSize,
      saveSidebarIconSize,
    ],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error("useTheme must be used inside ThemeProvider");
  }
  return context;
}
