import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { House, Heart, BarChart3, User, SlidersHorizontal, Sparkles, LogOut, Search, Bell, ChevronDown, ListTodo, Inbox, CheckCheck, ScrollText } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { SearchContext } from "@/context/UiContext";
import UsageMeter from "@/components/UsageMeter";
import { ensureAmbient } from "@/lib/ambient";

const RAIL = [
  { to: "/dashboard", label: "Home", icon: House, testid: "nav-dashboard-link" },
  { to: "/recommendations", label: "Recommendations", icon: Sparkles, testid: "nav-recommendations-link" },
  { to: "/saved", label: "Saved", icon: Heart, testid: "nav-saved-link" },
  { to: "/stats", label: "Stats", icon: BarChart3, testid: "nav-stats-link" },
  { to: "/taste", label: "Taste Profile", icon: User, testid: "nav-taste-profile-link" },
  { to: "/connections", label: "Connections", icon: SlidersHorizontal, testid: "nav-connections-link" },
  { to: "/jobs", label: "Jobs", icon: ListTodo, testid: "nav-jobs-link" },
  { to: "/requests", label: "Requests", icon: Inbox, testid: "nav-requests-link" },
  { to: "/approved", label: "Approved", icon: CheckCheck, testid: "nav-approved-link" },
  { to: "/logs", label: "Runtime logs", icon: ScrollText, testid: "nav-logs-link" },
];

const TABS = [
  { to: "/dashboard", label: "Home" },
  { to: "/recommendations", label: "AI Picks" },
  { to: "/saved", label: "Library" },
  { to: "/taste", label: "Taste" },
  { to: "/stats", label: "Stats" },
  { to: "/connections", label: "Sources" },
  { to: "/jobs", label: "Jobs" },
  { to: "/requests", label: "Requests" },
  { to: "/approved", label: "Approved" },
  { to: "/logs", label: "Logs" },
];

const TITLES = {
  "/recommendations": "AI Picks",
  "/saved": "Your Library",
  "/taste": "Cinematic DNA",
  "/stats": "Watch Stats",
  "/connections": "Sources",
  "/jobs": "Jobs",
  "/requests": "Requests",
  "/approved": "Approved",
  "/logs": "Runtime logs",
  "/profile": "Profile",
  "/search": "Search",
};

export default function Layout({ children }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [q, setQ] = useState("");
  const [bell, setBell] = useState(false);
  const [menu, setMenu] = useState(false);
  // Which rail button the pointer is on. Driving the label from state instead of
  // Tailwind's group-hover takes the whole CSS cascade out of the picture: no
  // theme override, no missing utility, no stale stylesheet can swallow it.
  const [railHover, setRailHover] = useState(null);
  const popRef = useRef(null);

  const isHome = location.pathname === "/dashboard";
  const showSearch = isHome || location.pathname === "/search";

  // Every tab gets Home's gradient wash, not just Home.
  useEffect(() => { ensureAmbient(); }, []);

  useEffect(() => {
    const close = (e) => { if (!popRef.current?.contains(e.target)) { setBell(false); setMenu(false); } };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, []);

  useEffect(() => {
    if (location.pathname === "/search") {
      setQ(new URLSearchParams(location.search).get("q") || "");
    } else if (!isHome) {
      setQ("");
    }
  }, [isHome, location.pathname, location.search]);

  const submitSearch = (event) => {
    if (event.key === "Enter" && q.trim()) {
      navigate(`/search?q=${encodeURIComponent(q.trim())}`);
    }
  };

  const doLogout = async () => {
    await logout();
    navigate("/", { replace: true });
  };

  const handle = user?.email ? `@${user.email.split("@")[0]}` : "";

  return (
    <SearchContext.Provider value={{ q, setQ }}>
      <div
        aria-hidden
        className="cm-ambient fixed inset-[-12%] z-0 bg-cover bg-center pointer-events-none"
        style={{ backgroundImage: "var(--ambient-img, none)", filter: "blur(90px) saturate(135%) brightness(0.9)", opacity: 0.95, transition: "opacity 800ms ease" }}
      />
      <div aria-hidden className="ambient-scrim fixed inset-0 z-0 pointer-events-none" />
      <div className="relative z-10 min-h-screen py-4 sm:py-7 lg:py-9 px-3 sm:px-5 pb-24 lg:pr-5">
        {/* Floating app shell */}
        {/* The shell always fills the viewport so short tabs look like Home instead of ending mid-screen. */}
        <div className="glass-shell rounded-[26px] sm:rounded-[32px] w-full mx-auto p-3 sm:p-5 lg:p-7 relative overflow-hidden grain min-h-[calc(100vh-8rem)] lg:min-h-[calc(100vh-4.5rem)]">
          <header className="relative z-20 flex flex-wrap items-center gap-3 lg:gap-5">
            {showSearch ? (
              <label className="flex items-center gap-2.5 glass rounded-full px-4 py-2.5 w-full sm:w-[260px] lg:w-[300px] focus-within:border-[rgba(216,178,106,0.5)] transition-colors">
                <Search className="w-4 h-4 text-[#8C7F6D] shrink-0" />
                <input
                  data-testid="home-search-input"
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  onKeyDown={submitSearch}
                  placeholder="Search Movies"
                  className="bg-transparent outline-none text-sm w-full placeholder:text-[#8C7F6D]"
                />
              </label>
            ) : (
              <div className="w-full sm:w-auto sm:min-w-[220px]">
                <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-[#8C7F6D]">CineMind</div>
                <div className="font-display text-lg font-bold leading-tight">{TITLES[location.pathname] || "CineMind"}</div>
              </div>
            )}

            <div className="flex-1 min-w-0 order-3 lg:order-none w-full lg:w-auto">
              <div className="flex items-center gap-1 overflow-x-auto scroll-thin -mx-1 px-1">
                {TABS.map((t) => (
                  <NavLink
                    key={t.to}
                    to={t.to}
                    data-testid={`tab-${t.label.toLowerCase().replace(/\s+/g, "-")}`}
                    className={({ isActive }) =>
                      `shrink-0 rounded-full px-4 py-2 text-sm font-medium transition-colors ${
                        isActive
                          ? "bg-[rgba(255,246,232,0.10)] text-[#F6EFE4] shadow-[inset_0_1px_0_rgba(255,240,220,0.12)]"
                          : "text-[#A5987F] hover:text-[#F6EFE4] hover:bg-white/[0.05]"
                      }`
                    }
                  >
                    {t.label}
                  </NavLink>
                ))}
              </div>
            </div>

            <div className="flex items-center gap-2.5 ml-auto relative" ref={popRef}>
              <button
                data-testid="usage-bell-button"
                onClick={() => { setBell((b) => !b); setMenu(false); }}
                className="w-10 h-10 rounded-full grid place-items-center bg-[rgba(168,190,146,0.16)] border border-[rgba(168,190,146,0.35)] text-[#C6D6B4] hover:bg-[rgba(168,190,146,0.26)] transition-colors"
                title="AI usage"
              >
                <Bell className="w-4 h-4" />
              </button>

              <button
                data-testid="profile-pill"
                onClick={() => { setMenu((m) => !m); setBell(false); }}
                className="flex items-center gap-2.5 glass rounded-full pl-1.5 pr-3 py-1.5 shrink-0 hover:border-[rgba(216,178,106,0.4)] transition-colors"
              >
                {user?.picture ? (
                  <img src={user.picture} alt="" onError={(e) => { e.currentTarget.style.display = "none"; }} className="w-8 h-8 rounded-full object-cover shrink-0" />
                ) : (
                  <span className="w-8 h-8 rounded-full bg-[rgba(216,178,106,0.28)] grid place-items-center font-mono text-xs shrink-0">{user?.name?.[0] || "U"}</span>
                )}
                <span className="hidden sm:block text-left leading-tight max-w-[130px]">
                  <span className="block text-xs font-medium truncate">{user?.name || "Guest"}</span>
                  <span className="block text-[10px] font-mono text-[#8C7F6D] truncate">{handle}</span>
                </span>
                <ChevronDown className="w-3.5 h-3.5 text-[#8C7F6D]" />
              </button>

              {bell && (
                <div data-testid="usage-popover" className="absolute right-0 top-12 w-72 z-50">
                  <div className="glass-strong rounded-2xl p-3">
                    <UsageMeter />
                  </div>
                </div>
              )}
              {menu && (
                <div data-testid="profile-menu" className="absolute right-0 top-12 w-56 z-50 glass-strong rounded-2xl p-2">
                  <div className="px-3 py-2">
                    <div className="text-xs font-medium truncate">{user?.name}</div>
                    <div className="text-[10px] font-mono text-[#8C7F6D] truncate">{user?.email}</div>
                  </div>
                  <div className="divider my-1" />
                  <NavLink
                    to="/profile"
                    onClick={() => setMenu(false)}
                    className="w-full flex items-center gap-2 px-3 py-2 rounded-xl text-sm text-[#BFB09A] hover:text-[#F6EFE4] hover:bg-white/[0.06] transition-colors"
                  >
                    <User className="w-4 h-4" /> Profile
                  </NavLink>
                  <NavLink
                    to="/connections"
                    onClick={() => setMenu(false)}
                    className="w-full flex items-center gap-2 px-3 py-2 rounded-xl text-sm text-[#BFB09A] hover:text-[#F6EFE4] hover:bg-white/[0.06] transition-colors"
                  >
                    <SlidersHorizontal className="w-4 h-4" /> Sources
                  </NavLink>
                  <button
                    data-testid="logout-button"
                    onClick={doLogout}
                    className="w-full flex items-center gap-2 px-3 py-2 rounded-xl text-sm text-[#BFB09A] hover:text-[#F6EFE4] hover:bg-white/[0.06] transition-colors"
                  >
                    <LogOut className="w-4 h-4" /> Sign out
                  </button>
                </div>
              )}
            </div>
          </header>

          <div className="relative z-10 mt-4 sm:mt-6">{children}</div>
        </div>

        {/* Icon rail: spans the full working width along the bottom edge, its
            side gutters matching the shell's so the two line up. */}
        {/* Button and glyph size come from Sources. The rail wraps onto more rows
            when a large size stops ten buttons fitting — it must never scroll,
            because any overflow here turns the bar into a clipping box and eats
            the hover labels that sit above it. */}
        <nav data-testid="icon-rail" className="fixed bottom-3 left-3 right-3 sm:left-5 sm:right-5 z-40 glass-strong rounded-[1.9rem] px-2 py-2 flex flex-wrap items-center justify-around gap-1">
          {RAIL.map((n) => {
            const showLabel = railHover === n.to;
            return (
              <NavLink
                key={n.to}
                to={n.to}
                data-testid={`${n.testid}-mobile`}
                onMouseEnter={() => setRailHover(n.to)}
                onMouseLeave={() => setRailHover((current) => (current === n.to ? null : current))}
                onFocus={() => setRailHover(n.to)}
                onBlur={() => setRailHover((current) => (current === n.to ? null : current))}
                style={{ width: "var(--rail-icon, 40px)", height: "var(--rail-icon, 40px)" }}
                className={({ isActive }) =>
                  `group relative shrink-0 rounded-full grid place-items-center transition-colors ${
                    isActive
                      ? "bg-[rgba(216,178,106,0.2)] text-[#EBD3A3]"
                      : "text-[#9C907E] hover:text-[#F6EFE4] hover:bg-white/[0.06]"
                  }`
                }
              >
                <n.icon style={{ width: "var(--rail-glyph, 18px)", height: "var(--rail-glyph, 18px)" }} />
                {/* Every visual property is inline. The label floats outside the bar
                    over poster art, and glass-strong is forced to a ~2% fill by the
                    theme layer, so it carries its own surface colour instead. */}
                <span
                  data-testid={`rail-label-${n.to.slice(1)}`}
                  /* Two independent paths to the same result. React state covers the
                     normal case; the group-hover class covers the one it misses, where
                     the pointer is already resting where the button re-renders and no
                     enter event ever fires. Inline opacity is only set while hovered,
                     so it never blocks the CSS path. */
                  className="opacity-0 group-hover:opacity-100 transition-opacity duration-200"
                  style={{
                    position: "absolute",
                    bottom: "calc(var(--rail-icon, 40px) + 14px)",
                    left: "50%",
                    transform: `translateX(-50%) translateY(${showLabel ? "0" : "4px"})`,
                    ...(showLabel ? { opacity: 1 } : {}),
                    pointerEvents: "none",
                    whiteSpace: "nowrap",
                    zIndex: 60,
                    background: "#17130F",
                    color: "#F6EFE4",
                    border: "1px solid rgba(255,240,220,0.18)",
                    borderRadius: "999px",
                    padding: "6px 12px",
                    fontSize: "12px",
                    lineHeight: 1.2,
                    boxShadow: "0 10px 30px rgba(0,0,0,0.55)",
                    transition: "transform 180ms ease",
                  }}
                >
                  {n.label}
                </span>
              </NavLink>
            );
          })}
        </nav>
      </div>
    </SearchContext.Provider>
  );
}
