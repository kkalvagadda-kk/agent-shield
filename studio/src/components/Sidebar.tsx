import { ChevronDown, ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";

// ── Nav item groups ──────────────────────────────────────────────────────────

const PLAYGROUND_BUILD = [
  { label: "Agents",    to: "/",          end: true },
  { label: "Skills",    to: "/skills",    end: false },
  { label: "Tools",     to: "/tools",     end: false },
  { label: "Workflows", to: "/workflows", end: false },
];

const PLAYGROUND_TEST = [
  { label: "Test",     to: "/playground",         end: true },
  { label: "Datasets", to: "/playground/datasets", end: false },
];

const ADMIN_ITEMS = [
  { label: "Access Control", to: "/admin/access" },
  { label: "Publish Queue",  to: "/admin/publish-requests" },
  { label: "HITL Queue",     to: "/hitl" },
  { label: "Approvers",      to: "/admin/approval-authority" },
];

// ── Helpers ──────────────────────────────────────────────────────────────────

function isPlaygroundRoute(pathname: string) {
  return (
    pathname === "/" ||
    pathname.startsWith("/agents") ||
    pathname.startsWith("/skills") ||
    pathname.startsWith("/tools") ||
    pathname.startsWith("/workflows") ||
    pathname.startsWith("/playground")
  );
}

function isAdminRoute(pathname: string) {
  return pathname.startsWith("/admin") || pathname.startsWith("/hitl");
}

// ── Sub-item link ────────────────────────────────────────────────────────────

function SideLink({ to, label, end = false }: { to: string; label: string; end?: boolean }) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        `block px-3 py-1.5 rounded text-sm transition-colors ${
          isActive
            ? "bg-slate-700 text-white font-medium"
            : "text-slate-400 hover:text-slate-200 hover:bg-slate-800"
        }`
      }
    >
      {label}
    </NavLink>
  );
}

// ── Collapsible section ──────────────────────────────────────────────────────

function Section({
  label,
  open,
  onToggle,
  children,
}: {
  label: string;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <div>
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between px-3 py-2 text-xs font-semibold uppercase tracking-wider text-slate-500 hover:text-slate-300 transition-colors"
      >
        {label}
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
      </button>
      {open && <div className="mt-0.5 space-y-0.5">{children}</div>}
    </div>
  );
}

// ── Sidebar ──────────────────────────────────────────────────────────────────

export default function Sidebar() {
  const { pathname } = useLocation();

  const [open, setOpen] = useState({
    playground: isPlaygroundRoute(pathname),
    admin: isAdminRoute(pathname),
  });

  // auto-expand the right section when navigating directly to a deep route
  useEffect(() => {
    if (isPlaygroundRoute(pathname)) setOpen((o) => ({ ...o, playground: true }));
    if (isAdminRoute(pathname)) setOpen((o) => ({ ...o, admin: true }));
  }, [pathname]);

  const toggle = (key: "playground" | "admin") =>
    setOpen((o) => ({ ...o, [key]: !o[key] }));

  return (
    <aside className="w-56 shrink-0 bg-slate-900 flex flex-col min-h-screen border-r border-slate-800">
      {/* Logo */}
      <div className="flex items-center gap-2 px-4 py-4 border-b border-slate-800">
        <div className="w-7 h-7 rounded-md bg-blue-500 flex items-center justify-center text-white text-xs font-bold shrink-0">
          AS
        </div>
        <div className="leading-tight">
          <p className="text-white font-semibold text-sm tracking-tight">AgentShield</p>
          <p className="text-slate-400 text-xs">Studio</p>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-2 py-4 space-y-4 overflow-y-auto">
        {/* Playground section */}
        <Section
          label="Playground"
          open={open.playground}
          onToggle={() => toggle("playground")}
        >
          {PLAYGROUND_BUILD.map((item) => (
            <SideLink key={item.to} to={item.to} label={item.label} end={item.end} />
          ))}

          <div className="my-2 border-t border-slate-800" />

          {PLAYGROUND_TEST.map((item) => (
            <SideLink key={item.to} to={item.to} label={item.label} end={item.end} />
          ))}
        </Section>

        {/* Catalog — shared artifacts */}
        <div>
          <p className="px-3 mb-0.5 text-xs font-semibold uppercase tracking-wider text-slate-500">
            Org
          </p>
          <SideLink to="/catalog" label="Catalog" />
        </div>

        {/* Providers — standalone */}
        <div>
          <p className="px-3 mb-0.5 text-xs font-semibold uppercase tracking-wider text-slate-500">
            Config
          </p>
          <SideLink to="/providers" label="Providers" />
        </div>

        {/* Administration section */}
        <Section
          label="Administration"
          open={open.admin}
          onToggle={() => toggle("admin")}
        >
          {ADMIN_ITEMS.map((item) => (
            <SideLink key={item.to} to={item.to} label={item.label} />
          ))}
        </Section>
      </nav>
    </aside>
  );
}
