import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { listAgents, listTools, listSkills, listAgentGraphs } from "../api/registryApi";

interface ArtifactRow {
  id: string;
  name: string;
  type: "agent" | "tool" | "skill" | "workflow";
  team: string;
  status: string;
  publish_status?: string;
  risk_level?: string;
  created_at: string;
  description?: string | null;
}

const TYPE_CHIP: Record<string, string> = {
  agent:    "bg-blue-100 text-blue-700",
  tool:     "bg-purple-100 text-purple-700",
  skill:    "bg-green-100 text-green-700",
  workflow: "bg-amber-100 text-amber-700",
};

const STATUS_CHIP: Record<string, string> = {
  active:      "bg-green-100 text-green-700",
  deprecated:  "bg-amber-100 text-amber-700",
  quarantined: "bg-red-100 text-red-700",
};

const PUBLISH_CHIP: Record<string, string> = {
  published:      "bg-green-100 text-green-700",
  pending_review: "bg-amber-100 text-amber-700",
};

export default function AdminArtifactsPage() {
  const navigate = useNavigate();
  const [typeFilter, setTypeFilter] = useState<"" | "agent" | "tool" | "skill" | "workflow">("");

  const { data: agentsPage, isLoading: loadingAgents } = useQuery({
    queryKey: ["artifacts-agents"],
    queryFn: () => listAgents(200, 0, undefined),
  });

  const { data: toolsPage, isLoading: loadingTools } = useQuery({
    queryKey: ["artifacts-tools"],
    queryFn: () => listTools(200, 0),
  });

  const { data: skillsPage, isLoading: loadingSkills } = useQuery({
    queryKey: ["artifacts-skills"],
    queryFn: () => listSkills(200, 0),
  });

  const { data: workflowsList, isLoading: loadingWorkflows } = useQuery({
    queryKey: ["artifacts-workflows"],
    queryFn: () => listAgentGraphs(),
  });

  const isLoading = loadingAgents || loadingTools || loadingSkills || loadingWorkflows;

  const rows = useMemo<ArtifactRow[]>(() => [
    ...(agentsPage?.items ?? []).map(a => ({
      id: a.id,
      name: a.name,
      type: "agent" as const,
      team: a.team,
      status: a.status,
      publish_status: a.publish_status,
      created_at: a.created_at,
      description: a.description,
    })),
    ...(toolsPage?.items ?? []).map(t => ({
      id: t.id,
      name: t.name,
      type: "tool" as const,
      team: t.owner_team ?? "",
      status: t.status ?? "active",
      risk_level: t.risk_level,
      created_at: "",
      description: t.description,
    })),
    ...(skillsPage?.items ?? []).map(s => ({
      id: s.id,
      name: s.name,
      type: "skill" as const,
      team: s.team,
      status: s.status,
      created_at: "",
      description: s.description,
    })),
    ...(workflowsList ?? []).map(w => ({
      id: w.id,
      name: w.name,
      type: "workflow" as const,
      team: w.team,
      status: w.status,
      created_at: w.created_at,
      description: w.description,
    })),
  ], [agentsPage, toolsPage, skillsPage, workflowsList]);

  const filtered = typeFilter ? rows.filter(r => r.type === typeFilter) : rows;

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-slate-900">All Artifacts</h1>
        <p className="text-sm text-slate-500 mt-0.5">
          Read-only view of all registry artifacts across all teams.
        </p>
      </div>

      {/* Filter chips */}
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        {(["", "agent", "tool", "skill", "workflow"] as const).map(t => (
          <button
            key={t}
            onClick={() => setTypeFilter(t)}
            className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
              typeFilter === t
                ? "bg-slate-800 text-white"
                : "bg-slate-100 text-slate-600 hover:bg-slate-200"
            }`}
          >
            {t === ""
              ? `All (${rows.length})`
              : `${t.charAt(0).toUpperCase() + t.slice(1)}s (${rows.filter(r => r.type === t).length})`}
          </button>
        ))}
      </div>

      {/* Loading */}
      {isLoading && (
        <div className="flex items-center justify-center py-20 text-slate-400">
          <Loader2 size={20} className="animate-spin mr-2" />
          Loading…
        </div>
      )}

      {/* Table */}
      {!isLoading && (
        <div className="card p-0 overflow-hidden">
          {filtered.length === 0 ? (
            <div className="flex flex-col items-center py-16 text-center">
              <p className="text-slate-500 font-medium">No artifacts found</p>
              <p className="text-slate-400 text-sm mt-1">
                {typeFilter ? `No ${typeFilter}s exist yet.` : "No artifacts in the registry yet."}
              </p>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50">
                  {["Name", "Type", "Team", "Status", "Publish Status", "Created At"].map(h => (
                    <th
                      key={h}
                      className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {filtered.map(row => (
                  <tr key={`${row.type}-${row.id}`} className="hover:bg-slate-50 transition-colors">
                    <td className="px-4 py-3">
                      {row.type === "agent" ? (
                        <button
                          onClick={() => navigate(`/agents/${row.name}`)}
                          className="text-sm font-medium text-blue-600 hover:text-blue-800 hover:underline text-left"
                        >
                          {row.name}
                        </button>
                      ) : (
                        <span className="text-sm font-medium text-slate-800">{row.name}</span>
                      )}
                      {row.description && (
                        <p className="text-xs text-slate-400 line-clamp-1 mt-0.5">
                          {row.description}
                        </p>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`badge ${TYPE_CHIP[row.type] ?? "bg-slate-100 text-slate-600"}`}>
                        {row.type}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-700">{row.team || "—"}</td>
                    <td className="px-4 py-3">
                      <span className={`badge ${STATUS_CHIP[row.status] ?? "bg-slate-100 text-slate-600"}`}>
                        {row.status}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      {row.publish_status ? (
                        <span className={`badge ${PUBLISH_CHIP[row.publish_status] ?? "bg-slate-100 text-slate-600"}`}>
                          {row.publish_status.replace("_", " ")}
                        </span>
                      ) : (
                        <span className="text-slate-300 text-xs">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-slate-400 text-xs">
                      {row.created_at ? new Date(row.created_at).toLocaleString() : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {!isLoading && (
        <p className="text-xs text-slate-400 mt-2 text-right">
          {filtered.length} artifact{filtered.length !== 1 ? "s" : ""}
          {typeFilter ? ` (filtered from ${rows.length} total)` : " total"}
        </p>
      )}
    </div>
  );
}
