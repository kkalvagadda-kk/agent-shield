import { ChevronDown, ChevronRight, LogOut } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../contexts/AuthContext";
import { listAgents } from "../api/registryApi";

// ── Nav item groups ──────────────────────────────────────────────────────────

const PLAYGROUND_BUILD = [
  { label: "Agents",       to: "/",             end: true  },
  { label: "Skills",       to: "/skills",       end: false },
  { label: "Tools",        to: "/tools",        end: false },
  // "Agent Graphs" hidden from nav — the composite Workflow builder now covers
  // multi-agent authoring (inline + existing agents). Routes remain for direct-URL access.
  // { label: "Agent Graphs", to: "/agent-graphs", end: false },
  { label: "Workflows",    to: "/workflows",    end: false },
];

const PLAYGROUND_EVAL = [
  { label: "Evaluate", to: "/playground",          end: true  },
  { label: "Datasets", to: "/playground/datasets", end: false },
];

const ORG_ITEMS = [
  { label: "Catalog",       to: "/catalog"                  },
  { label: "Traces",        to: "/observability/traces"     },
  { label: "Dashboard",     to: "/observability/dashboard"  },
  { label: "Approvals",     to: "/approvals"                },
  { label: "Deployments",   to: "/deployments"              },
];

const ADMIN_ITEMS = [
  { label: "All Artifacts",  to: "/admin/artifacts"          },
  { label: "Publish Queue",  to: "/admin/publish-requests"   },
  { label: "Access Control", to: "/admin/access"             },
  { label: "HITL Queue",     to: "/hitl"                     },
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
    pathname.startsWith("/agent-graphs") ||
    pathname.startsWith("/playground") ||
    pathname.startsWith("/my-agents")
  );
}

function isOrgRoute(p: string) {
  return p.startsWith("/catalog") || p.startsWith("/deployments") || p.startsWith("/observability");
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
  const { user, logout } = useAuth();

  const { data: sidebarTeams } = useQuery({
    queryKey: ["sidebar-teams"],
    queryFn: () => fetch("/api/v1/admin/teams-summary").then((r) => r.json()),
    staleTime: 60_000,
  });

  const { data: sidebarAgents } = useQuery({
    queryKey: ["sidebar-agents"],
    queryFn: () => listAgents(200, 0, "active"),
    staleTime: 60_000,
  });

  const myTeamGrants = useMemo(() => {
    const myTeam = (sidebarTeams ?? []).find((t: any) =>
      t.members?.some((m: any) => m.user_sub === user?.sub)
    );
    const grantedNames = new Set(
      (myTeam?.grants ?? [])
        .filter((g: any) => g.asset_type === "agent")
        .map((g: any) => g.asset_name as string)
    );
    return (sidebarAgents?.items ?? [])
      .filter((a) => grantedNames.has(a.name))
      .slice(0, 5);
  }, [sidebarTeams, sidebarAgents, user?.sub]);

  const [open, setOpen] = useState({
    playground: isPlaygroundRoute(pathname),
    org: isOrgRoute(pathname),
    admin: isAdminRoute(pathname),
  });

  // auto-expand the right section when navigating directly to a deep route
  useEffect(() => {
    if (isPlaygroundRoute(pathname)) setOpen((o) => ({ ...o, playground: true }));
    if (isOrgRoute(pathname))        setOpen((o) => ({ ...o, org: true }));
    if (isAdminRoute(pathname))      setOpen((o) => ({ ...o, admin: true }));
  }, [pathname]);

  const toggle = (section: "playground" | "org" | "admin") =>
    setOpen((o) => ({ ...o, [section]: !o[section] }));

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
        {/* Playground */}
        <Section label="Playground" open={open.playground} onToggle={() => toggle("playground")}>
          {PLAYGROUND_BUILD.map((i) => (
            <SideLink key={i.to} to={i.to} label={i.label} end={i.end} />
          ))}
          <div className="my-2 border-t border-slate-800" />
          {PLAYGROUND_EVAL.map((i) => (
            <SideLink key={i.to} to={i.to} label={i.label} end={i.end} />
          ))}
        </Section>

        {/* My Agents */}
        <div className="mt-2">
          <p className="px-3 mb-0.5 text-xs font-semibold uppercase tracking-wider text-slate-500">
            My Agents
          </p>
          {myTeamGrants.length === 0 ? (
            <p className="px-3 py-1 text-xs text-slate-500 italic">No agents granted yet</p>
          ) : (
            <>
              {myTeamGrants.map((a) => (
                <SideLink key={a.name} to={`/agents/${a.name}/chat`} label={a.name} end={false} />
              ))}
              <NavLink
                to="/my-agents"
                className={({ isActive }) =>
                  `block px-3 py-1 text-xs transition-colors ${
                    isActive ? "text-white" : "text-slate-500 hover:text-slate-300"
                  }`
                }
              >
                See all →
              </NavLink>
            </>
          )}
        </div>

        {/* Org */}
        <Section label="Org" open={open.org} onToggle={() => toggle("org")}>
          {ORG_ITEMS.map((i) => (
            <SideLink key={i.to} to={i.to} label={i.label} end={false} />
          ))}
        </Section>

        {/* Config */}
        <div className="mt-2">
          <p className="px-3 mb-0.5 text-xs font-semibold uppercase tracking-wider text-slate-500">Config</p>
          <SideLink to="/providers" label="Providers" end={false} />
        </div>

        {/* Administration */}
        <Section label="Administration" open={open.admin} onToggle={() => toggle("admin")}>
          {ADMIN_ITEMS.map((i) => (
            <SideLink key={i.to} to={i.to} label={i.label} end={false} />
          ))}
        </Section>
      </nav>

      {/* User footer */}
      {user && (
        <div className="border-t border-slate-800 px-3 py-3 flex items-center gap-2">
          <div className="w-7 h-7 rounded-full bg-blue-600 flex items-center justify-center text-white text-xs font-semibold shrink-0">
            {(user.given_name?.[0] ?? user.preferred_username?.[0] ?? "?").toUpperCase()}
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-xs font-medium text-slate-200 truncate">
              {user.given_name
                ? `${user.given_name} ${user.family_name ?? ""}`.trim()
                : user.preferred_username}
            </p>
            <p className="text-xs text-slate-500 truncate">{user.email ?? user.preferred_username}</p>
          </div>
          <button
            onClick={logout}
            title="Sign out"
            className="text-slate-500 hover:text-slate-300 transition-colors shrink-0"
          >
            <LogOut size={14} />
          </button>
        </div>
      )}
    </aside>
  );
}
