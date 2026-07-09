import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Loader2, MessageSquare, Rocket, Eye } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { listAgents, listAllDeployments } from "../api/registryApi";
import DeployModal from "../components/DeployModal";
import { useAuth } from "../contexts/AuthContext";

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

// ── API ──────────────────────────────────────────────────────────────────────

async function fetchTeamsSummary(): Promise<TeamSummary[]> {
  const r = await fetch("/api/v1/admin/teams-summary");
  if (!r.ok) return [];
  return r.json();
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function MyAgentsPage() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [deployingAgent, setDeployingAgent] = useState<string | null>(null);

  const { data: teams = [], isLoading: loadingTeams } = useQuery({
    queryKey: ["my-agents-teams"],
    queryFn: fetchTeamsSummary,
    staleTime: 60_000,
  });

  const { data: agentsPage, isLoading: loadingAgents } = useQuery({
    queryKey: ["my-agents-list"],
    queryFn: () => listAgents(200, 0, "active"),
    staleTime: 60_000,
  });

  const { data: deploymentsPage, isLoading: loadingDeployments } = useQuery({
    queryKey: ["my-agents-deployments"],
    queryFn: () => listAllDeployments("running", 100),
    staleTime: 30_000,
  });

  const isLoading = loadingTeams || loadingAgents || loadingDeployments;

  // Find the team this user belongs to
  const myTeam = teams.find((t: TeamSummary) =>
    t.members?.some((m) => m.user_sub === user?.sub)
  );

  // Derive granted agent names for this team
  const grantedNames = new Set(
    (myTeam?.grants ?? [])
      .filter((g) => g.asset_type === "agent")
      .map((g) => g.asset_name)
  );

  const runningNames = new Set(
    (deploymentsPage?.items ?? []).map((d) => d.agent_name)
  );

  const myAgents = (agentsPage?.items ?? [])
    .filter((a) => grantedNames.has(a.name))
    .map((a) => ({ ...a, isRunning: runningNames.has(a.name) }));

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-slate-900">My Agents</h1>
        <p className="text-sm text-slate-500 mt-0.5">
          Agents your team ({myTeam?.name ?? "—"}) has been granted access to.
        </p>
      </div>

      {/* Loading */}
      {isLoading && (
        <div className="flex items-center justify-center py-20 text-slate-400">
          <Loader2 size={20} className="animate-spin mr-2" /> Loading…
        </div>
      )}

      {/* Empty state */}
      {!isLoading && myAgents.length === 0 && (
        <div className="card flex flex-col items-center py-16 text-center">
          <Bot size={36} className="text-slate-300 mb-3" />
          <p className="text-slate-500 font-medium">No agents yet</p>
          <p className="text-slate-400 text-sm mt-1 max-w-sm">
            No agents have been granted to your team yet. Ask an admin to grant
            access via Administration → Access Control.
          </p>
        </div>
      )}

      {/* Agent grid */}
      {!isLoading && myAgents.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {myAgents.map((a) => (
            <div key={a.id} className="card flex flex-col gap-3">
              {/* Name + description */}
              <div>
                <h3 className="font-semibold text-slate-900 text-sm leading-snug">
                  {a.name}
                </h3>
                {a.description && (
                  <p className="text-xs text-slate-500 line-clamp-2 mt-1">
                    {a.description}
                  </p>
                )}
              </div>

              {/* Status badge */}
              <div>
                {a.isRunning ? (
                  <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700">
                    <span className="w-1.5 h-1.5 rounded-full bg-green-500 shrink-0" />
                    Running
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium bg-slate-100 text-slate-500">
                    Not deployed
                  </span>
                )}
              </div>

              {/* Owner team */}
              {a.team && (
                <p className="text-xs text-slate-400">
                  Owner: <span className="font-medium text-slate-500">{a.team}</span>
                </p>
              )}

              {/* Actions */}
              <div className="mt-auto pt-3 border-t border-slate-100 flex gap-2">
                {a.isRunning ? (
                  <>
                    <Link
                      to={`/agents/${a.name}/chat`}
                      className="flex items-center gap-1 text-xs font-medium text-white bg-blue-600 hover:bg-blue-700 px-3 py-1.5 rounded transition-colors"
                    >
                      <MessageSquare size={11} /> Chat
                    </Link>
                    <Link
                      to={`/agents/${a.name}`}
                      className="flex items-center gap-1 text-xs font-medium text-slate-600 hover:text-slate-800 bg-slate-100 hover:bg-slate-200 px-3 py-1.5 rounded transition-colors"
                    >
                      <Eye size={11} /> View
                    </Link>
                  </>
                ) : (
                  <>
                    <button
                      onClick={() => setDeployingAgent(a.name)}
                      className="flex items-center gap-1 text-xs font-medium text-slate-600 hover:text-slate-800 bg-slate-100 hover:bg-slate-200 px-3 py-1.5 rounded transition-colors"
                    >
                      <Rocket size={11} /> Deploy
                    </button>
                    <Link
                      to={`/agents/${a.name}`}
                      className="flex items-center gap-1 text-xs font-medium text-slate-600 hover:text-slate-800 bg-slate-100 hover:bg-slate-200 px-3 py-1.5 rounded transition-colors"
                    >
                      <Eye size={11} /> View
                    </Link>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {deployingAgent && (
        <DeployModal
          agentName={deployingAgent}
          onClose={() => setDeployingAgent(null)}
          onDeployed={() => {
            qc.invalidateQueries({ queryKey: ["deployments"] });
            qc.invalidateQueries({ queryKey: ["agents"] });
          }}
        />
      )}
    </div>
  );
}
