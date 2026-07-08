import { useQuery } from "@tanstack/react-query";
import { Activity, AlertTriangle, Clock, DollarSign, Loader2 } from "lucide-react";
import { AgentRunItem, getAgentStats, listAgentRuns } from "../../api/registryApi";

export default function OverviewReactive({ agentName }: { agentName: string }) {
  const { data: stats, isLoading: statsLoading } = useQuery({
    queryKey: ["agent-stats", agentName],
    queryFn: () => getAgentStats(agentName),
  });

  const { data: recentRuns } = useQuery({
    queryKey: ["agent-runs-recent", agentName],
    queryFn: () => listAgentRuns({ agent_name: agentName, limit: 5 }),
  });

  return (
    <div className="space-y-6">
      {/* Stats cards */}
      {statsLoading ? (
        <div className="flex items-center justify-center py-8 text-slate-400">
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
            label="P50 Latency"
            value={stats.p50_latency_ms != null ? `${stats.p50_latency_ms}ms` : "—"}
          />
          <StatCard
            icon={<AlertTriangle size={16} className="text-amber-500" />}
            label="Error Rate"
            value={`${(stats.error_rate * 100).toFixed(1)}%`}
          />
          <StatCard
            icon={<DollarSign size={16} className="text-green-500" />}
            label="Cost (24h)"
            value={stats.total_cost_usd > 0 ? `$${stats.total_cost_usd.toFixed(4)}` : "—"}
          />
        </div>
      ) : null}

      {/* Endpoint card */}
      <div className="card p-4">
        <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">
          API Endpoint
        </h3>
        <code className="text-sm font-mono text-slate-700 bg-slate-50 rounded px-2 py-1 block">
          POST /api/v1/agents/{agentName}/chat
        </code>
      </div>

      {/* Recent runs mini-table */}
      <div>
        <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">
          Recent Runs
        </h3>
        {!recentRuns || recentRuns.length === 0 ? (
          <p className="text-sm text-slate-400">No production runs yet.</p>
        ) : (
          <div className="border border-slate-200 rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs text-slate-500">
                <tr>
                  <th className="px-3 py-2">Status</th>
                  <th className="px-3 py-2">Input</th>
                  <th className="px-3 py-2">Duration</th>
                  <th className="px-3 py-2">Started</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {recentRuns.map((run: AgentRunItem) => (
                  <tr key={run.id}>
                    <td className="px-3 py-2">
                      <span
                        className={`inline-block w-2 h-2 rounded-full mr-1.5 ${
                          run.status === "completed"
                            ? "bg-green-500"
                            : run.status === "failed"
                            ? "bg-red-500"
                            : "bg-blue-500"
                        }`}
                      />
                      <span className="text-xs">{run.status}</span>
                    </td>
                    <td className="px-3 py-2 text-xs text-slate-600 max-w-[200px] truncate">
                      {run.input ? (run.input.length > 50 ? run.input.slice(0, 50) + "…" : run.input) : "—"}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs">
                      {run.latency_ms != null ? `${run.latency_ms}ms` : "—"}
                    </td>
                    <td className="px-3 py-2 text-xs text-slate-500">
                      {new Date(run.started_at).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
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
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="card p-4 flex flex-col gap-1">
      <div className="flex items-center gap-1.5 text-xs text-slate-500">
        {icon}
        {label}
      </div>
      <p className="text-lg font-semibold text-slate-800">{value}</p>
    </div>
  );
}
