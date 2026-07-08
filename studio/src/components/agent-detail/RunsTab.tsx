import { useQuery } from "@tanstack/react-query";
import { Eye, Loader2 } from "lucide-react";
import { useState } from "react";
import { AgentRunItem, listAgentRuns } from "../../api/registryApi";
import TraceDrawer from "../playground/TraceDrawer";

const STATUS_BADGE: Record<string, string> = {
  completed: "bg-green-100 text-green-700",
  running: "bg-blue-100 text-blue-700",
  failed: "bg-red-100 text-red-700",
  awaiting_approval: "bg-amber-100 text-amber-700",
  cancelled: "bg-slate-100 text-slate-600",
};

const TRIGGER_ICONS: Record<string, string> = {
  api: "🔌",
  manual: "👤",
  schedule: "⏰",
  webhook: "🔗",
};

export default function RunsTab({ agentName }: { agentName: string }) {
  const [triggerFilter, setTriggerFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [traceId, setTraceId] = useState<string | null>(null);

  const { data: runs, isLoading } = useQuery({
    queryKey: ["agent-runs", agentName, triggerFilter, statusFilter],
    queryFn: () =>
      listAgentRuns({
        agent_name: agentName,
        trigger_type: triggerFilter || undefined,
        status: statusFilter || undefined,
        limit: 50,
      }),
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12 text-slate-400">
        <Loader2 size={16} className="animate-spin mr-2" />
        Loading runs…
      </div>
    );
  }

  return (
    <div>
      <div className="flex gap-3 mb-4">
        <select
          value={triggerFilter}
          onChange={(e) => setTriggerFilter(e.target.value)}
          className="text-xs border border-slate-200 rounded px-2 py-1"
        >
          <option value="">All triggers</option>
          <option value="api">API</option>
          <option value="manual">Manual</option>
          <option value="schedule">Schedule</option>
          <option value="webhook">Webhook</option>
        </select>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="text-xs border border-slate-200 rounded px-2 py-1"
        >
          <option value="">All statuses</option>
          <option value="completed">Completed</option>
          <option value="running">Running</option>
          <option value="awaiting_approval">Awaiting approval</option>
          <option value="failed">Failed</option>
          <option value="cancelled">Cancelled</option>
        </select>
      </div>

      {!runs || runs.length === 0 ? (
        <p className="text-sm text-slate-400 py-8 text-center">No runs yet.</p>
      ) : (
        <div className="border border-slate-200 rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs text-slate-500 uppercase tracking-wider">
              <tr>
                <th className="px-4 py-2">Trigger</th>
                <th className="px-4 py-2">Status</th>
                <th className="px-4 py-2">Duration</th>
                <th className="px-4 py-2">Cost</th>
                <th className="px-4 py-2">Run By</th>
                <th className="px-4 py-2">Started</th>
                <th className="px-4 py-2">Trace</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {runs.map((run: AgentRunItem) => (
                <tr key={run.id} className="hover:bg-slate-50">
                  <td className="px-4 py-2">
                    {TRIGGER_ICONS[run.trigger_type || ""] || "?"}{" "}
                    <span className="text-xs text-slate-500">{run.trigger_type || "—"}</span>
                  </td>
                  <td className="px-4 py-2">
                    <span className={`badge text-xs ${STATUS_BADGE[run.status] || "bg-slate-100 text-slate-600"}`}>
                      {run.status}
                    </span>
                  </td>
                  <td className="px-4 py-2 font-mono text-xs">
                    {run.latency_ms != null ? `${run.latency_ms}ms` : "—"}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs">
                    {run.cost_usd != null ? `$${run.cost_usd.toFixed(4)}` : "—"}
                  </td>
                  <td className="px-4 py-2 text-xs text-slate-500">
                    {run.run_by || "—"}
                  </td>
                  <td className="px-4 py-2 text-xs text-slate-500">
                    {new Date(run.started_at).toLocaleString()}
                  </td>
                  <td className="px-4 py-2">
                    {run.langfuse_trace_id && (
                      <button
                        onClick={() => setTraceId(run.langfuse_trace_id)}
                        className="inline-flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 font-medium"
                      >
                        <Eye size={12} />
                        View
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {traceId && (
        <TraceDrawer traceId={traceId} onClose={() => setTraceId(null)} />
      )}
    </div>
  );
}
