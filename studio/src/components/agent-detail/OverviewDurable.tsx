import { useQuery } from "@tanstack/react-query";
import { Activity, AlertTriangle, Clock, Loader2, Play } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { AgentRunItem, getAgentStats, listAgentRuns } from "../../api/registryApi";

export default function OverviewDurable({ agentName }: { agentName: string }) {
  const navigate = useNavigate();

  const { data: stats, isLoading: statsLoading } = useQuery({
    queryKey: ["agent-stats", agentName],
    queryFn: () => getAgentStats(agentName),
  });

  const { data: runs } = useQuery({
    queryKey: ["agent-runs-active", agentName],
    queryFn: () => listAgentRuns({ agent_name: agentName, limit: 10 }),
  });

  const activeRuns = runs?.filter((r: AgentRunItem) => r.status === "running" || r.status === "awaiting_approval") || [];
  const failedCount = runs?.filter((r: AgentRunItem) => r.status === "failed").length || 0;
  const awaitingCount = runs?.filter((r: AgentRunItem) => r.status === "awaiting_approval").length || 0;

  return (
    <div className="space-y-6">
      {/* Stats row */}
      {statsLoading ? (
        <div className="flex items-center justify-center py-6 text-slate-400">
          <Loader2 size={16} className="animate-spin mr-2" />
          Loading stats…
        </div>
      ) : stats ? (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard
            icon={<Activity size={16} className="text-blue-500" />}
            label="Runs (24h)"
            value={String(stats.run_count)}
          />
          <StatCard
            icon={<Clock size={16} className="text-purple-500" />}
            label="Avg Duration"
            value={stats.p50_latency_ms != null ? `${(stats.p50_latency_ms / 1000).toFixed(1)}s` : "—"}
          />
          <StatCard
            icon={<AlertTriangle size={16} className="text-red-500" />}
            label="Failed"
            value={String(failedCount)}
            highlight={failedCount > 0}
          />
          <StatCard
            icon={<Play size={16} className="text-amber-500" />}
            label="Awaiting Approval"
            value={String(awaitingCount)}
            highlight={awaitingCount > 0}
          />
        </div>
      ) : null}

      {/* New run button */}
      <div className="flex items-center gap-3">
        <button
          onClick={() => navigate(`/playground?agent=${agentName}`)}
          className="btn-primary text-xs py-1.5"
        >
          <Play size={12} />
          New Run
        </button>
      </div>

      {/* Active runs */}
      <div>
        <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">
          Active Runs
        </h3>
        {activeRuns.length === 0 ? (
          <p className="text-sm text-slate-400">No active runs.</p>
        ) : (
          <div className="space-y-2">
            {activeRuns.map((run: AgentRunItem) => (
              <div key={run.id} className="card p-3 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <span
                    className={`w-2 h-2 rounded-full ${
                      run.status === "awaiting_approval" ? "bg-amber-500 animate-pulse" : "bg-blue-500 animate-pulse"
                    }`}
                  />
                  <div>
                    <p className="text-xs font-mono text-slate-700">{run.id.slice(0, 8)}…</p>
                    <p className="text-xs text-slate-400">
                      {run.status === "awaiting_approval" ? "Waiting for approval" : "Running"}
                      {run.started_at && ` • Started ${new Date(run.started_at).toLocaleTimeString()}`}
                    </p>
                  </div>
                </div>
                <span className={`badge text-xs ${
                  run.status === "awaiting_approval" ? "bg-amber-100 text-amber-700" : "bg-blue-100 text-blue-700"
                }`}>
                  {run.status}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function StatCard({
  icon,
  label,
  value,
  highlight = false,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  highlight?: boolean;
}) {
  return (
    <div className={`card p-4 flex flex-col gap-1 ${highlight ? "border-amber-200" : ""}`}>
      <div className="flex items-center gap-1.5 text-xs text-slate-500">
        {icon}
        {label}
      </div>
      <p className={`text-lg font-semibold ${highlight ? "text-amber-600" : "text-slate-800"}`}>{value}</p>
    </div>
  );
}
