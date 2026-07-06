import { useQuery } from "@tanstack/react-query";
import { BookOpen, Loader2, MessageSquare, RefreshCw, Rocket, ShieldOff } from "lucide-react";
import { Link } from "react-router-dom";
import { listAgents, listTools, listSkills, listAgentGraphs } from "../api/registryApi";

// ── Types ────────────────────────────────────────────────────────────────────

interface TeamSummary {
  id: string;
  name: string;
  namespace: string;
  members: { user_sub: string; role: string }[];
  grants: {
    id: string;
    asset_type: string;
    asset_name: string;
    granted_at: string | null;
  }[];
}

interface CatalogEntry {
  id: string;
  name: string;
  type: "agent" | "tool" | "skill" | "workflow";
  description?: string | null;
  team?: string;
  grantedTo: string[];
  publish_status?: string;
}

// ── API ──────────────────────────────────────────────────────────────────────

async function fetchTeamsSummary(): Promise<TeamSummary[]> {
  const r = await fetch("/api/v1/admin/teams-summary");
  if (!r.ok) return [];
  return r.json();
}

// ── Constants ────────────────────────────────────────────────────────────────

const TYPE_COLORS: Record<string, string> = {
  agent:    "bg-blue-50 text-blue-700 border border-blue-200",
  tool:     "bg-purple-50 text-purple-700 border border-purple-200",
  skill:    "bg-teal-50 text-teal-700 border border-teal-200",
  workflow: "bg-indigo-50 text-indigo-700 border border-indigo-200",
};

const ASSET_TYPES = ["agent", "tool", "skill", "workflow"] as const;
type AssetType = typeof ASSET_TYPES[number];

// ── Page ─────────────────────────────────────────────────────────────────────

export default function CatalogPage() {
  const [typeFilter, setTypeFilter] = [
    typeof window !== "undefined"
      ? (new URLSearchParams(window.location.search).get("type") as AssetType | null)
      : null,
    () => {},
  ];
  void typeFilter; // accessed below via local state

  const { data: agentsPage, isLoading: loadingAgents } = useQuery({
    queryKey: ["catalog-agents"],
    queryFn: () => listAgents(100, 0, "active"),
  });
  const { data: toolsPage, isLoading: loadingTools } = useQuery({
    queryKey: ["catalog-tools"],
    queryFn: () => listTools(),
  });
  const { data: skillsPage, isLoading: loadingSkills } = useQuery({
    queryKey: ["catalog-skills"],
    queryFn: () => listSkills(),
  });
  const { data: workflows = [], isLoading: loadingWorkflows } = useQuery({
    queryKey: ["catalog-agent-graphs"],
    queryFn: () => listAgentGraphs(),
  });
  const { data: teams = [], isLoading: loadingTeams, refetch, isFetching } = useQuery({
    queryKey: ["admin-teams-summary"],
    queryFn: fetchTeamsSummary,
  });

  const isLoading = loadingAgents || loadingTools || loadingSkills || loadingWorkflows || loadingTeams;

  // Build asset → granted teams map from teams-summary
  const grantMap: Record<string, string[]> = {};
  for (const team of teams) {
    for (const g of team.grants) {
      if (!grantMap[g.asset_name]) grantMap[g.asset_name] = [];
      if (!grantMap[g.asset_name].includes(team.name)) {
        grantMap[g.asset_name].push(team.name);
      }
    }
  }

  // Build unified catalog entries
  const entries: CatalogEntry[] = [
    ...(agentsPage?.items ?? []).map((a) => ({
      id: a.id ?? a.name,
      name: a.name,
      type: "agent" as const,
      description: a.description,
      team: a.team,
      grantedTo: grantMap[a.name] ?? [],
      publish_status: a.publish_status,
    })),
    ...(toolsPage?.items ?? []).map((t) => ({
      id: t.id,
      name: t.name,
      type: "tool" as const,
      description: t.description,
      team: t.owner_team ?? undefined,
      grantedTo: grantMap[t.name] ?? [],
    })),
    ...(skillsPage?.items ?? []).map((s) => ({
      id: s.id,
      name: s.name,
      type: "skill" as const,
      description: s.description,
      team: s.team ?? undefined,
      grantedTo: grantMap[s.name] ?? [],
    })),
    ...workflows.map((w) => ({
      id: w.id,
      name: w.name,
      type: "workflow" as const,
      description: w.description,
      team: w.team ?? undefined,
      grantedTo: grantMap[w.name] ?? [],
    })),
  ];

  // Only show entries that have at least one active grant (shared with a team)
  // For agents, also require publish_status === "published"
  const shared = entries.filter(
    (e) => e.grantedTo.length > 0 &&
           (e.type !== "agent" || e.publish_status === "published")
  );
  const unshared = entries.filter((e) => e.grantedTo.length === 0);

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Catalog</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Artifacts shared with teams in your organization
          </p>
        </div>
        <button onClick={() => refetch()} disabled={isFetching} className="btn-secondary">
          <RefreshCw size={13} className={isFetching ? "animate-spin" : ""} /> Refresh
        </button>
      </div>

      {isLoading && (
        <div className="flex items-center justify-center py-20 text-slate-400">
          <Loader2 size={20} className="animate-spin mr-2" /> Loading catalog…
        </div>
      )}

      {!isLoading && (
        <>
          {/* Shared section */}
          {shared.length === 0 ? (
            <div className="card flex flex-col items-center py-16 text-center">
              <ShieldOff size={36} className="text-slate-300 mb-3" />
              <p className="text-slate-500 font-medium">Nothing shared yet</p>
              <p className="text-slate-400 text-sm mt-1">
                Grant teams access to agents, tools, skills, or workflows via{" "}
                <span className="font-medium">Administration → Access Control</span>.
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              {/* Type filter chips */}
              <TypeFilters entries={shared} />
              <CatalogGrid entries={shared} />
            </div>
          )}

          {/* Unshared section — collapsed, just a count */}
          {unshared.length > 0 && (
            <details className="mt-8 group">
              <summary className="cursor-pointer text-sm text-slate-400 hover:text-slate-600 flex items-center gap-2 list-none select-none">
                <BookOpen size={14} />
                {unshared.length} artifact{unshared.length !== 1 ? "s" : ""} not yet shared with any team
                <span className="text-xs">(click to expand)</span>
              </summary>
              <div className="mt-4">
                <CatalogGrid entries={unshared} dimmed />
              </div>
            </details>
          )}
        </>
      )}
    </div>
  );
}

// ── Type filter chips ────────────────────────────────────────────────────────

function TypeFilters({ entries }: { entries: CatalogEntry[] }) {
  const counts = entries.reduce<Record<string, number>>((acc, e) => {
    acc[e.type] = (acc[e.type] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="flex gap-2 flex-wrap mb-2">
      {ASSET_TYPES.filter((t) => counts[t]).map((t) => (
        <span key={t} className={`badge ${TYPE_COLORS[t]}`}>
          {t}s <span className="ml-1 font-normal opacity-70">{counts[t]}</span>
        </span>
      ))}
    </div>
  );
}

// ── Catalog grid ─────────────────────────────────────────────────────────────

function CatalogGrid({ entries, dimmed }: { entries: CatalogEntry[]; dimmed?: boolean }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
      {entries.map((e) => (
        <CatalogCard key={`${e.type}:${e.id}`} entry={e} dimmed={dimmed} />
      ))}
    </div>
  );
}

function CatalogCard({ entry, dimmed }: { entry: CatalogEntry; dimmed?: boolean }) {
  return (
    <div className={`card flex flex-col gap-3 ${dimmed ? "opacity-50" : ""}`}>
      <div className="flex items-start justify-between gap-2">
        <h3 className="font-semibold text-slate-900 text-sm leading-snug">{entry.name}</h3>
        <span className={`badge shrink-0 ${TYPE_COLORS[entry.type]}`}>{entry.type}</span>
      </div>

      {entry.description && (
        <p className="text-xs text-slate-500 line-clamp-2">{entry.description}</p>
      )}

      <div className="mt-auto pt-2 border-t border-slate-100">
        {entry.team && (
          <p className="text-xs text-slate-400 mb-2">
            Owner: <span className="font-medium text-slate-600">{entry.team}</span>
          </p>
        )}
        {entry.grantedTo.length > 0 ? (
          <div>
            <p className="text-xs text-slate-400 mb-1.5">Accessible to:</p>
            <div className="flex flex-wrap gap-1">
              {entry.grantedTo.map((team) => (
                <span key={team} className="badge bg-green-50 text-green-700 border border-green-200 text-xs">
                  {team}
                </span>
              ))}
            </div>
          </div>
        ) : (
          <p className="text-xs text-slate-400 italic">Not granted to any team</p>
        )}
      </div>

      {entry.type === "agent" && entry.grantedTo.length > 0 && (
        <div className="flex gap-2 mt-2 pt-2 border-t border-slate-100">
          <Link
            to={`/agents/${entry.name}/chat`}
            className="flex items-center gap-1 text-xs font-medium text-blue-600 hover:text-blue-800 bg-blue-50 hover:bg-blue-100 px-3 py-1.5 rounded transition-colors"
          >
            <MessageSquare size={11} /> Chat
          </Link>
          <Link
            to={`/agents/${entry.name}/deploy`}
            className="flex items-center gap-1 text-xs font-medium text-slate-600 hover:text-slate-800 bg-slate-100 hover:bg-slate-200 px-3 py-1.5 rounded transition-colors"
          >
            <Rocket size={11} /> Deploy
          </Link>
        </div>
      )}
    </div>
  );
}
