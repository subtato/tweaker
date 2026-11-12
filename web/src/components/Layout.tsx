import {
  Activity,
  ChevronDown,
  Command,
  LayoutDashboard,
  Menu,
  Search,
  Settings,
  Sparkles,
  TriangleAlert,
  X,
  Zap,
} from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api";
import { useOrg } from "../OrgContext";
import type { AppConfig, Stats } from "../types";
import { formatNumber } from "../lib";
import { Logo } from "./Logo";

const NAV = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/issues", label: "Sentry mirror", icon: TriangleAlert },
  { to: "/runs", label: "Runs", icon: Activity },
  { to: "/ask", label: "Agent test", icon: Sparkles },
  { to: "/settings", label: "Settings", icon: Settings },
];

const TITLES: Record<string, string> = {
  "/": "Overview",
  "/issues": "Sentry mirror",
  "/runs": "Runs",
  "/ask": "Agent test",
  "/settings": "Settings",
};

export function Layout({ config }: { config: AppConfig }) {
  const { org, orgs, setOrgId } = useOrg();
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [stats, setStats] = useState<Stats | null>(null);
  const location = useLocation();
  const navigate = useNavigate();
  const title = TITLES[location.pathname] ?? (location.pathname.startsWith("/issues/") ? "Issue" : "Seer run");

  useEffect(() => {
    setOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!org?.id) return;
    api.stats(org.id).then(setStats).catch(() => undefined);
  }, [org?.id, location.pathname]);

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    if (!search.trim()) return;
    navigate(`/issues?search=${encodeURIComponent(search.trim())}`);
    setSearch("");
  }

  return (
    <div className="shell">
      <aside className={`sidebar ${open ? "sidebar--open" : ""}`}>
        <div className="sidebar__top">
          <Logo />
          <button className="icon-btn sidebar__close" onClick={() => setOpen(false)} aria-label="Close menu">
            <X size={18} />
          </button>
        </div>

        <div className="org-switcher">
          <div className="org-switcher__avatar">{(org?.name ?? "O").slice(0, 1).toUpperCase()}</div>
          <div className="org-switcher__body">
            <span className="org-switcher__label">Organization</span>
            <select
              value={org?.id ?? ""}
              onChange={(event) => setOrgId(event.target.value)}
              aria-label="Switch organization"
            >
              {orgs.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </div>
          <ChevronDown size={14} className="org-switcher__chevron" />
        </div>

        <nav className="nav" aria-label="Main navigation">
          <span className="nav__section">Workspace</span>
          {NAV.slice(0, 4).map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => `nav__item ${isActive ? "nav__item--active" : ""}`}
            >
              <item.icon size={17} />
              <span>{item.label}</span>
              {item.to === "/issues" && stats ? <span className="nav__count">{formatNumber(stats.open_issues)}</span> : null}
              {item.to === "/ask" ? <span className="nav__spark">AI</span> : null}
            </NavLink>
          ))}
          <span className="nav__section nav__section--spaced">Configure</span>
          {NAV.slice(4).map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => `nav__item ${isActive ? "nav__item--active" : ""}`}
            >
              <item.icon size={17} />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="sidebar__footer">
          <div className="ai-card">
            <div className="ai-card__top">
              <span className="pulse-dot" />
              <span>{config.ai_provider === "demo" ? "Local analyzer" : "AI connected"}</span>
              <Zap size={13} />
            </div>
            <div className="ai-card__model">{config.ai_model}</div>
            <div className="ai-card__note">
              {config.ai_provider === "demo"
                ? "No model credentials required. Add a provider to unlock full analysis."
                : "Telemetry is redacted before inference. Org controls apply."}
            </div>
          </div>
          <div className="sidebar__meta">
            <span>Tweaker {config.version}</span>
            <a href="https://github.com/tweaker/tweaker" target="_blank" rel="noreferrer">
              Docs
            </a>
          </div>
        </div>
      </aside>

      {open && <button className="sidebar-scrim" onClick={() => setOpen(false)} aria-label="Close menu" />}

      <div className="main">
        <header className="topbar">
          <div className="topbar__left">
            <button className="icon-btn topbar__menu" onClick={() => setOpen(true)} aria-label="Open menu">
              <Menu size={19} />
            </button>
            <div className="topbar__crumb">
              <span>Workspace</span>
              <span className="topbar__slash">/</span>
              <strong>{title}</strong>
            </div>
          </div>
          <div className="topbar__right">
            <form className="search" onSubmit={submitSearch}>
              <Search size={15} />
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search issues, culprits, short IDs…"
                aria-label="Search issues"
              />
              <span className="search__key">
                <Command size={11} /> K
              </span>
            </form>
            <NavLink to="/ask" className="btn btn--ghost topbar__ask">
              <Sparkles size={15} />
              Agent test
            </NavLink>
            <div className="avatar" title="Signed in">
              OP
            </div>
          </div>
        </header>
        <main className="content">
          <Outlet context={{ stats, config }} />
        </main>
      </div>
    </div>
  );
}
