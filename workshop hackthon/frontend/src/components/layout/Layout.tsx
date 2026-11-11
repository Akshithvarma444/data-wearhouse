import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import {
  BarChart3, Boxes, Database, FileText, Gauge, History, Menu, Settings, ShieldCheck, Sparkles, Workflow, X,
  type LucideIcon,
} from "lucide-react";
import { api } from "../../api/endpoints";
import { usePolling } from "../../hooks/usePolling";
import { ErrorBoundary } from "../ui/ErrorBoundary";
import type { Health } from "../../types/api";

interface NavItem { to: string; label: string; icon: LucideIcon }

const NAV_GROUPS: { title: string; items: NavItem[] }[] = [
  { title: "", items: [{ to: "/", label: "Dashboard", icon: Gauge }] },
  { title: "Data", items: [
    { to: "/data-sources", label: "Data sources", icon: Database },
    { to: "/generator", label: "Generate data", icon: Sparkles },
  ] },
  { title: "Pipeline", items: [
    { to: "/pipeline", label: "Run pipeline", icon: Workflow },
    { to: "/quality", label: "Data quality", icon: ShieldCheck },
    { to: "/history", label: "Run history", icon: History },
  ] },
  { title: "Warehouse", items: [
    { to: "/warehouse", label: "Tables", icon: Boxes },
    { to: "/analytics", label: "Analytics", icon: BarChart3 },
    { to: "/reports", label: "Reports", icon: FileText },
  ] },
];

function useHealth() {
  const [health, setHealth] = useState<Health | null>(null);
  const [down, setDown] = useState(false);
  usePolling(async () => {
    try {
      setHealth(await api.health());
      setDown(false);
    } catch {
      setDown(true);
    }
  }, 30000, true);
  if (down) return { ok: false as const, text: "Backend offline" };
  if (!health) return { ok: null, text: "Connecting…" };
  return health.database?.connected
    ? { ok: true as const, text: "Database connected" }
    : { ok: false as const, text: "Database offline" };
}

function NavItemLink({ item, onNavigate }: { item: NavItem; onNavigate?: () => void }) {
  const Icon = item.icon;
  return (
    <NavLink to={item.to} end={item.to === "/"} onClick={onNavigate}
      className={({ isActive }) =>
        `flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${isActive
          ? "bg-brand-50 font-medium text-brand-700"
          : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"}`}>
      {({ isActive }) => (<><Icon className={`h-[18px] w-[18px] shrink-0 ${isActive ? "text-brand-600" : "text-slate-400"}`} aria-hidden />{item.label}</>)}
    </NavLink>
  );
}

function Sidebar({ onNavigate, status }: { onNavigate?: () => void; status: ReturnType<typeof useHealth> }) {
  return (
    <>
      <Link to="/" className="flex items-center gap-2.5 px-5 py-5" onClick={onNavigate}>
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-sm font-bold text-white">S</span>
        <span className="leading-tight">
          <span className="block text-[15px] font-semibold text-slate-900">Sales DW</span>
          <span className="block text-xs text-slate-500">E-commerce warehouse</span>
        </span>
      </Link>
      <nav className="flex-1 space-y-5 overflow-y-auto px-3 pb-4" aria-label="Main">
        {NAV_GROUPS.map((group, gi) => (
          <div key={gi}>
            {group.title && <p className="px-3 pb-1.5 text-xs font-medium text-slate-400">{group.title}</p>}
            <div className="space-y-0.5">{group.items.map((item) => <NavItemLink key={item.to} item={item} onNavigate={onNavigate} />)}</div>
          </div>
        ))}
      </nav>
      <div className="space-y-0.5 border-t border-slate-200 px-3 py-3">
        <NavItemLink item={{ to: "/settings", label: "Settings", icon: Settings }} onNavigate={onNavigate} />
        <p className="flex items-center gap-2 px-3 pt-1 text-xs text-slate-500">
          <span className={`h-2 w-2 rounded-full ${status.ok === null ? "bg-slate-300" : status.ok ? "bg-emerald-500" : "bg-rose-500"}`} aria-hidden />
          {status.text}
        </p>
      </div>
    </>
  );
}

export function Layout() {
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const status = useHealth();
  useEffect(() => setOpen(false), [location.pathname]);

  return (
    <div className="flex h-full">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-slate-200 bg-white lg:flex">
        <Sidebar status={status} />
      </aside>

      {open && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <button type="button" aria-label="Close menu" className="absolute inset-0 bg-slate-900/30" onClick={() => setOpen(false)} />
          <aside className="relative flex h-full w-64 flex-col bg-white shadow-xl">
            <button type="button" onClick={() => setOpen(false)} className="absolute right-3 top-5 rounded-lg p-1 text-slate-500 hover:bg-slate-100" aria-label="Close menu">
              <X className="h-5 w-5" />
            </button>
            <Sidebar status={status} onNavigate={() => setOpen(false)} />
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b border-slate-200 bg-white px-4 py-3 lg:hidden">
          <button type="button" onClick={() => setOpen(true)} className="rounded-lg p-1 text-slate-600 hover:bg-slate-100" aria-label="Open menu">
            <Menu className="h-5 w-5" />
          </button>
          <span className="text-[15px] font-semibold text-slate-900">Sales DW</span>
        </header>
        <main className="min-w-0 flex-1 overflow-y-auto px-4 py-6 sm:px-8 sm:py-8">
          <div className="mx-auto max-w-7xl">
            <ErrorBoundary resetKey={location.pathname}>
              <Outlet />
            </ErrorBoundary>
          </div>
        </main>
      </div>
    </div>
  );
}
