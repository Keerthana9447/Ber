import { useEffect, useMemo, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Bell, ChevronLeft, ChevronRight, ChevronDown, LogOut, Menu, Search, User as UserIcon, X } from "lucide-react";
import { useAuth } from "../lib/auth";
import { api, isEndpointMissing } from "../lib/api";
import { GROUP_ORDER, MODULES, moduleByPath } from "../lib/modules";
import { cn } from "../lib/cn";
import { ConfirmDialog, Modal, useToast } from "./ui";

function Sidebar({ collapsed, mobileOpen, onClose }: { collapsed: boolean; mobileOpen: boolean; onClose: () => void }) {
  const { can } = useAuth();
  const visible = useMemo(() => MODULES.filter((m) => can(m.permission)), [can]);
  return (
    <>
      {mobileOpen && <div className="fixed inset-0 z-30 bg-forest-950/50 lg:hidden" onClick={onClose} aria-hidden />}
      <aside
        aria-label="Main navigation"
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-64 flex-col bg-forest-900 text-forest-100 transition-transform lg:translate-x-0 lg:transition-[width]",
          mobileOpen ? "translate-x-0" : "-translate-x-full",
          collapsed ? "lg:w-16" : "lg:w-64",
        )}
      >
        <div className="flex h-16 items-center justify-between border-b border-forest-800 px-4">
          <Link to="/" className="flex min-w-0 items-center" aria-label="BEREV home">
            <img
              src="/branding/berev-logo.png"
              alt="BEREV — Boundless EV Revolution"
              className={cn("h-auto w-36 max-h-12 rounded bg-white object-contain", collapsed && "lg:w-10")}
            />
          </Link>
          <button className="lg:hidden" onClick={onClose} aria-label="Close menu">
            <X className="h-5 w-5" />
          </button>
        </div>
        <nav className="flex-1 overflow-y-auto px-2 py-3">
          {GROUP_ORDER.map((group) => {
            const items = visible.filter((m) => m.group === group);
            if (!items.length) return null;
            return (
              <div key={group} className="mb-3">
                <p className={cn("px-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-forest-400", collapsed && "lg:hidden")}>
                  {group}
                </p>
                {items.map((m) => (
                  <NavLink
                    key={m.key}
                    to={m.path}
                    end={m.path === "/"}
                    title={collapsed ? m.label : undefined}
                    className={({ isActive }) =>
                      cn(
                        "flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
                        isActive ? "bg-forest-700 text-white" : "text-forest-100 hover:bg-forest-800",
                        collapsed && "lg:justify-center",
                      )
                    }
                  >
                    <m.icon className="h-4 w-4 shrink-0" aria-hidden />
                    <span className={cn(collapsed && "lg:hidden")}>{m.label}</span>
                  </NavLink>
                ))}
              </div>
            );
          })}
        </nav>
      </aside>
    </>
  );
}

function Breadcrumbs() {
  const { pathname } = useLocation();
  const segments = pathname.split("/").filter(Boolean);
  const first = moduleByPath(`/${segments[0] ?? ""}`);
  const label = segments[0] === "profile" ? "Profile" : first?.label;
  return (
    <nav aria-label="Breadcrumb" className="hidden min-w-0 items-center gap-1 text-sm text-slate-500 sm:flex">
      <Link to="/" className="hover:text-forest-700">
        Home
      </Link>
      {label && segments.length > 0 && (
        <>
          <ChevronRight className="h-3 w-3" aria-hidden />
          <span className="truncate font-medium text-slate-800">{label}</span>
        </>
      )}
    </nav>
  );
}

function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { can } = useAuth();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    return MODULES.filter((m) => can(m.permission) && (!q || `${m.label} ${m.description}`.toLowerCase().includes(q)));
  }, [query, can]);
  useEffect(() => {
    if (open) {
      setQuery("");
      setIndex(0);
    }
  }, [open]);
  const go = (path: string) => {
    onClose();
    navigate(path);
  };
  return (
    <Modal open={open} onClose={onClose} title="Search">
      <div className="border-b border-ivory-300 p-3">
        <input
          autoFocus
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setIndex(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") setIndex((i) => Math.min(i + 1, results.length - 1));
            if (e.key === "ArrowUp") setIndex((i) => Math.max(i - 1, 0));
            if (e.key === "Enter" && results[index]) go(results[index].path);
          }}
          placeholder="Go to a module…"
          aria-label="Search modules"
          className="w-full rounded-lg border border-ivory-300 px-3 py-2 text-sm"
        />
      </div>
      <ul className="max-h-72 overflow-y-auto p-2">
        {results.length === 0 && <li className="px-3 py-6 text-center text-sm text-slate-500">No matching module</li>}
        {results.map((m, i) => (
          <li key={m.key}>
            <button
              onClick={() => go(m.path)}
              onMouseEnter={() => setIndex(i)}
              className={cn("flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm", i === index && "bg-forest-50")}
            >
              <m.icon className="h-4 w-4 text-forest-600" aria-hidden />
              <span className="font-medium text-slate-800">{m.label}</span>
              <span className="truncate text-xs text-slate-500">{m.description}</span>
            </button>
          </li>
        ))}
      </ul>
    </Modal>
  );
}

type NotificationItem = { id: number | string; title: string; body?: string | null; is_read?: boolean };

function useDismiss(open: boolean, onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && onClose();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);
  return ref;
}

function NotificationMenu() {
  const [open, setOpen] = useState(false);
  const ref = useDismiss(open, () => setOpen(false));
  const query = useQuery({
    queryKey: ["notifications"],
    queryFn: ({ signal }) => api<{ items?: NotificationItem[] } | NotificationItem[]>("/notifications", { query: { page_size: 10 }, signal }),
    retry: false,
    refetchOnWindowFocus: false,
  });
  const items = Array.isArray(query.data) ? query.data : (query.data?.items ?? []);
  const unread = items.filter((n) => n.is_read === false).length;
  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen((o) => !o)} aria-label="Notifications" aria-expanded={open} className="relative rounded-lg p-2 text-slate-600 hover:bg-forest-50">
        <Bell className="h-5 w-5" />
        {unread > 0 && <span className="absolute right-1 top-1 h-2 w-2 rounded-full bg-gold-500" />}
      </button>
      {open && (
        <div className="absolute right-0 z-50 mt-2 w-80 max-w-[90vw] rounded-xl border border-ivory-300 bg-white shadow-lg">
          <p className="border-b border-ivory-300 px-4 py-2 text-sm font-semibold text-slate-800">Notifications</p>
          <div className="max-h-80 overflow-y-auto p-2 text-sm">
            {query.isLoading && <p className="p-4 text-center text-slate-500">Loading…</p>}
            {query.isError && (
              <p className="p-4 text-center text-slate-500">
                {isEndpointMissing(query.error) ? "Notifications are not available yet." : "Could not load notifications."}
              </p>
            )}
            {query.isSuccess && items.length === 0 && <p className="p-4 text-center text-slate-500">You're all caught up.</p>}
            {items.map((n) => (
              <div key={n.id} className="rounded-lg px-3 py-2 hover:bg-ivory-100">
                <p className="font-medium text-slate-800">{n.title}</p>
                {n.body && <p className="text-xs text-slate-500">{n.body}</p>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function UserMenu({ onSignOut }: { onSignOut: () => void }) {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const ref = useDismiss(open, () => setOpen(false));
  if (!user) return null;
  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-label="User menu" className="flex items-center gap-2 rounded-lg p-1.5 hover:bg-forest-50">
        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-forest-700 text-sm font-semibold text-white">
          {user.full_name.trim().charAt(0).toUpperCase() || "U"}
        </span>
        <span className="hidden text-left leading-tight md:block">
          <span className="block text-sm font-medium text-slate-800">{user.full_name}</span>
          <span className="block text-xs text-slate-500">{user.role}</span>
        </span>
        <ChevronDown className="hidden h-4 w-4 text-slate-400 md:block" />
      </button>
      {open && (
        <div className="absolute right-0 z-50 mt-2 w-56 rounded-xl border border-ivory-300 bg-white p-1 shadow-lg">
          <p className="truncate px-3 py-2 text-xs text-slate-500">{user.email}</p>
          <Link to="/profile" onClick={() => setOpen(false)} className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm hover:bg-forest-50">
            <UserIcon className="h-4 w-4" /> My profile
          </Link>
          <button
            onClick={() => {
              setOpen(false);
              onSignOut();
            }}
            className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-red-700 hover:bg-red-50"
          >
            <LogOut className="h-4 w-4" /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}

export function Layout() {
  const { logout } = useAuth();
  const toast = useToast();
  const { pathname } = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [palette, setPalette] = useState(false);
  const [confirmOut, setConfirmOut] = useState(false);

  useEffect(() => setMobileOpen(false), [pathname]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette(true);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="min-h-full">
      <Sidebar collapsed={collapsed} mobileOpen={mobileOpen} onClose={() => setMobileOpen(false)} />
      <div className={cn("flex min-h-full flex-col transition-[padding]", collapsed ? "lg:pl-16" : "lg:pl-64")}>
        <header className="sticky top-0 z-20 flex h-16 items-center gap-2 border-b border-ivory-300 bg-white/95 px-3 backdrop-blur sm:px-6">
          <button className="rounded-lg p-2 text-slate-600 hover:bg-forest-50 lg:hidden" onClick={() => setMobileOpen(true)} aria-label="Open menu">
            <Menu className="h-5 w-5" />
          </button>
          <button className="hidden rounded-lg p-2 text-slate-600 hover:bg-forest-50 lg:block" onClick={() => setCollapsed((c) => !c)} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
            {collapsed ? <ChevronRight className="h-5 w-5" /> : <ChevronLeft className="h-5 w-5" />}
          </button>
          <Breadcrumbs />
          <div className="ml-auto flex items-center gap-1">
            <button
              onClick={() => setPalette(true)}
              className="flex items-center gap-2 rounded-lg border border-ivory-300 px-3 py-1.5 text-sm text-slate-500 hover:bg-ivory-100"
              aria-label="Search"
            >
              <Search className="h-4 w-4" />
              <span className="hidden sm:inline">Search</span>
              <kbd className="hidden rounded bg-ivory-200 px-1.5 text-[10px] sm:inline">Ctrl K</kbd>
            </button>
            <NotificationMenu />
            <UserMenu onSignOut={() => setConfirmOut(true)} />
          </div>
        </header>
        <main className="flex-1 p-4 sm:p-6">
          <Outlet />
        </main>
      </div>
      <CommandPalette open={palette} onClose={() => setPalette(false)} />
      <ConfirmDialog
        open={confirmOut}
        title="Sign out?"
        message="You will need to sign in again to continue."
        confirmLabel="Sign out"
        onCancel={() => setConfirmOut(false)}
        onConfirm={async () => {
          await logout();
          setConfirmOut(false);
          toast("success", "Signed out.");
        }}
      />
    </div>
  );
}
