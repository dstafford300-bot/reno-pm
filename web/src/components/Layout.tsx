import { useState, type ReactNode } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { useAuth } from "../auth";
import { useProperty } from "../property";
import { Icon, Sheet } from "./ui";
import type { ChangeRequest } from "../types";

interface Item { to: string; label: string; icon: ReactNode; show: boolean; badge?: number }

export default function Layout() {
  const { user, logout } = useAuth();
  const { properties, selected, select } = useProperty();
  const [more, setMore] = useState(false);
  const nav = useNavigate();
  const caps = user!.capabilities;

  const pending = useQuery({
    queryKey: ["change-requests", "pending"],
    queryFn: () => api.get<ChangeRequest[]>("/api/change-requests?status=pending"),
    refetchInterval: 60_000,
    enabled: caps.approve_changes,
  });
  const pendingCount = pending.data?.length ?? 0;

  const items: Item[] = [
    { to: "/", label: "Dashboard", icon: Icon.home, show: true },
    { to: "/schedule", label: "Schedule", icon: Icon.calendar, show: true },
    { to: "/budget", label: "Budget", icon: Icon.money, show: caps.view_budget },
    { to: "/journal", label: "Journal", icon: Icon.book, show: caps.view_journal },
    { to: "/materials", label: "Materials", icon: Icon.receipt, show: caps.view_materials },
    { to: "/approvals", label: caps.approve_changes ? "Approvals" : "My requests", icon: Icon.check, show: caps.approve_changes || caps.propose_changes, badge: pendingCount },
    { to: "/sow", label: "Upload SOW", icon: Icon.upload, show: caps.upload_sow },
    { to: "/users", label: "Users", icon: Icon.users, show: caps.manage_users },
    { to: "/account", label: "Account", icon: Icon.user, show: true },
  ].filter((i) => i.show);

  // Bottom bar: the four most-used screens + More.
  const primary = items.filter((i) => ["/", "/schedule", "/budget", "/journal", "/approvals"].includes(i.to)).slice(0, 4);
  const overflow = items.filter((i) => !primary.includes(i));

  const switcher = properties.length > 0 && (
    <div className="prop-select">
      <select aria-label="Property" value={selected?.id ?? ""} onChange={(e) => select(e.target.value)}>
        {properties.map((p) => (
          <option key={p.id} value={p.id}>{p.property_name}{p.archived ? " (finished)" : ""}</option>
        ))}
      </select>
    </div>
  );

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">🏠 Reno PM</div>
        {items.map((i) => (
          <NavLink key={i.to} to={i.to} end={i.to === "/"} className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}>
            {i.icon}{i.label}{!!i.badge && <span className="badge">{i.badge}</span>}
          </NavLink>
        ))}
        <div className="sidebar-spacer" />
        <div className="small muted" style={{ padding: "0 10px" }}>{user!.name} · {user!.role}</div>
        <button className="btn ghost small" onClick={() => void logout()}>Sign out</button>
      </aside>

      <div className="main">
        <header className="topbar">
          <span className="brand">Reno PM</span>
          {switcher}
        </header>
        <main className="content"><Outlet /></main>
      </div>

      <nav className="tabbar" aria-label="Main">
        {primary.map((i) => (
          <NavLink key={i.to} to={i.to} end={i.to === "/"} className={({ isActive }) => `tab ${isActive ? "active" : ""}`}>
            {i.icon}{i.label}{!!i.badge && <span className="badge">{i.badge}</span>}
          </NavLink>
        ))}
        <button className="tab" onClick={() => setMore(true)} aria-label="More">{Icon.more}More</button>
      </nav>

      {more && (
        <Sheet title="More" onClose={() => setMore(false)}>
          <div className="stack">
            {overflow.map((i) => (
              <button key={i.to} className="btn block" style={{ justifyContent: "flex-start" }}
                onClick={() => { setMore(false); nav(i.to); }}>
                <span style={{ width: 22, height: 22, display: "inline-flex" }}>{i.icon}</span>{i.label}
                {!!i.badge && <span className="tag danger" style={{ marginLeft: "auto" }}>{i.badge}</span>}
              </button>
            ))}
            <button className="btn block" onClick={() => void logout()}>Sign out</button>
          </div>
        </Sheet>
      )}
    </div>
  );
}
