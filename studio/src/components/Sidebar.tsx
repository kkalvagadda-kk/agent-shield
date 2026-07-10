import {
  Activity,
  Bot,
  ChevronDown,
  ChevronRight,
  ClipboardCheck,
  Cpu,
  Database,
  FlaskConical,
  HandMetal,
  KeyRound,
  LayoutDashboard,
  ListChecks,
  LogOut,
  Rocket,
  ShoppingBag,
  Sparkles,
  Store,
  UserCheck,
  Users,
  Workflow,
  Wrench,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../contexts/AuthContext";
import { listAgents } from "../api/registryApi";
import type { LucideIcon } from "lucide-react";

// ── Nav item groups ──────────────────────────────────────────────────────────

interface NavItem {
  label: string;
  to: string;
  end?: boolean;
  icon: LucideIcon;
}

const BUILD_ITEMS: NavItem[] = [
  { label: "Agents",    to: "/",          end: true,  icon: Bot },
  { label: "Skills",    to: "/skills",    end: false, icon: Sparkles },
  { label: "Tools",     to: "/tools",     end: false, icon: Wrench },
  { label: "Workflows", to: "/workflows", end: false, icon: Workflow },
];

const EVALUATE_ITEMS: NavItem[] = [
  { label: "Eval Runs", to: "/playground",          end: true,  icon: FlaskConical },
  { label: "Datasets",  to: "/playground/datasets", end: false, icon: Database },
];

const CATALOG_ITEMS: NavItem[] = [
  { label: "Marketplace",  to: "/catalog",     icon: Store },
  { label: "Approvals",    to: "/approvals",   icon: ClipboardCheck },
  { label: "Deployments",  to: "/deployments", icon: Rocket },
];

const OBSERVE_ITEMS: NavItem[] = [
  { label: "Traces",    to: "/observability/traces",    icon: Activity },
  { label: "Dashboard", to: "/observability/dashboard", icon: LayoutDashboard },
];

const SETTINGS_ITEMS: NavItem[] = [
  { label: "Models", to: "/providers", icon: Cpu },
  { label: "Credentials", to: "/credentials", icon: KeyRound },
];

const ADMIN_ITEMS: NavItem[] = [
  { label: "All Artifacts",  to: "/admin/artifacts",          icon: ShoppingBag },
  { label: "Publish Queue",  to: "/admin/publish-requests",   icon: ListChecks },
  { label: "Access Control", to: "/admin/access",             icon: Users },
  { label: "HITL Queue",     to: "/hitl",                     icon: HandMetal },
  { label: "Approvers",      to: "/admin/approval-authority", icon: UserCheck },
];

// ── Helpers ──────────────────────────────────────────────────────────────────

type SectionKey = "build" | "evaluate" | "catalog" | "observe" | "settings" | "admin";

function detectSections(pathname: string): SectionKey[] {
  const active: SectionKey[] = [];
  if (
    pathname === "/" ||
    pathname.startsWith("/agents") ||
    pathname.startsWith("/skills") ||
    pathname.startsWith("/tools") ||
    pathname.startsWith("/workflows") ||
    pathname.startsWith("/agent-graphs") ||
    pathname.startsWith("/my-agents")
  ) active.push("build");
  if (pathname.startsWith("/playground")) active.push("evaluate");
  if (
    pathname.startsWith("/catalog") ||
    pathname.startsWith("/approvals") ||
    pathname.startsWith("/deployments")
  ) active.push("catalog");
  if (pathname.startsWith("/observability")) active.push("observe");
  if (pathname.startsWith("/providers") || pathname.startsWith("/credentials")) active.push("settings");
  if (pathname.startsWith("/admin") || pathname.startsWith("/hitl")) active.push("admin");
  return active;
}

// ── Sub-item link ────────────────────────────────────────────────────────────

function SideLink({
  to,
  label,
  end = false,
  icon: Icon,
}: {
  to: string;
  label: string;
  end?: boolean;
  icon?: LucideIcon;
}) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        `flex items-center gap-3 px-3 py-2 rounded-md text-[14px] transition-colors ${
          isActive
            ? "bg-blue-500/10 text-blue-400 font-medium"
            : "text-slate-300 hover:text-white hover:bg-slate-800/60"
        }`
      }
    >
      {Icon && <Icon size={18} strokeWidth={1.8} className="shrink-0" />}
      {label}
    </NavLink>
  );
}

// ── Section header ───────────────────────────────────────────────────────────

function SectionHeader({ label }: { label: string }) {
  return (
    <p className="px-3 pt-5 pb-1 text-[11px] font-semibold uppercase tracking-widest text-slate-500">
      {label}
    </p>
  );
}

// ── Collapsible section (Admin only) ────────────────────────────────────────

function CollapsibleSection({
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
        className="w-full flex items-center justify-between px-3 pt-5 pb-1 text-[11px] font-semibold uppercase tracking-widest text-slate-500 hover:text-slate-300 transition-colors"
      >
        {label}
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
      </button>
      {open && <div className="mt-0.5 space-y-0.5">{children}</div>}
    </div>
  );
}

// ── Sidebar ──────────────────────────────────────────────────────────────────

export default function Sidebar() {
  const { pathname } = useLocation();
  const { user, logout, isAtLeast } = useAuth();

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

  const [adminOpen, setAdminOpen] = useState(() => detectSections(pathname).includes("admin"));

  useEffect(() => {
    if (detectSections(pathname).includes("admin")) setAdminOpen(true);
  }, [pathname]);

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
      <nav className="flex-1 px-2 py-2 overflow-y-auto">
        {/* Build */}
        <SectionHeader label="Build" />
        <div className="space-y-0.5">
          {BUILD_ITEMS.map((i) => (
            <SideLink key={i.to} to={i.to} label={i.label} end={i.end} icon={i.icon} />
          ))}
        </div>

        {/* Evaluate */}
        <SectionHeader label="Evaluate" />
        <div className="space-y-0.5">
          {EVALUATE_ITEMS.map((i) => (
            <SideLink key={i.to} to={i.to} label={i.label} end={i.end} icon={i.icon} />
          ))}
        </div>

        {/* Shared With Me */}
        <SectionHeader label="Shared With Me" />
        <div className="space-y-0.5">
          {myTeamGrants.length === 0 ? (
            <p className="px-3 py-2 text-[13px] text-slate-500 italic">Nothing shared yet</p>
          ) : (
            <>
              {myTeamGrants.map((a) => (
                <SideLink key={a.name} to={`/agents/${a.name}/chat`} label={a.name} end={false} icon={Bot} />
              ))}
              <NavLink
                to="/my-agents"
                className={({ isActive }) =>
                  `block px-3 py-1.5 text-[13px] transition-colors ${
                    isActive ? "text-blue-400" : "text-slate-500 hover:text-slate-300"
                  }`
                }
              >
                See all →
              </NavLink>
            </>
          )}
        </div>

        {/* Catalog */}
        <SectionHeader label="Catalog" />
        <div className="space-y-0.5">
          {CATALOG_ITEMS.map((i) => (
            <SideLink key={i.to} to={i.to} label={i.label} end={false} icon={i.icon} />
          ))}
        </div>

        {/* Observe */}
        <SectionHeader label="Observe" />
        <div className="space-y-0.5">
          {OBSERVE_ITEMS.map((i) => (
            <SideLink key={i.to} to={i.to} label={i.label} end={false} icon={i.icon} />
          ))}
        </div>

        {/* Settings */}
        <SectionHeader label="Settings" />
        <div className="space-y-0.5">
          {SETTINGS_ITEMS.map((i) => (
            <SideLink key={i.to} to={i.to} label={i.label} end={false} icon={i.icon} />
          ))}
        </div>

        {/* Admin — visible to platform-admin only */}
        {isAtLeast("platform-admin") && (
          <CollapsibleSection label="Admin" open={adminOpen} onToggle={() => setAdminOpen((o) => !o)}>
            {ADMIN_ITEMS.map((i) => (
              <SideLink key={i.to} to={i.to} label={i.label} end={false} icon={i.icon} />
            ))}
          </CollapsibleSection>
        )}
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
