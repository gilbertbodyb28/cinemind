import { useEffect } from "react";

/** Opera Neon often ignores Cmd+Shift+R. Handle it in-app when the page has focus. */
export function useForceReloadShortcut() {
  useEffect(() => {
    const onKeyDown = (event) => {
      const key = event.key?.toLowerCase();
      if (!(event.metaKey || event.ctrlKey) || !event.shiftKey || key !== "r") return;
      // If the browser already hard-refreshes, this never runs. In Neon it usually does nothing,
      // so we take over and force a cache-busting reload.
      event.preventDefault();
      event.stopPropagation();
      const reload = () => {
        const url = new URL(window.location.href);
        url.searchParams.set("_reload", String(Date.now()));
        window.location.replace(url.toString());
      };
      if (!("caches" in window)) {
        reload();
        return;
      }
      caches
        .keys()
        .then((keys) => Promise.all(keys.map((keyName) => caches.delete(keyName))))
        .finally(reload);
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, []);
}
