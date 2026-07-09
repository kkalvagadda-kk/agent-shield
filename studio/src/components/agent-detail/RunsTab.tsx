import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, ExternalLink, Loader2 } from "lucide-react";
import { useState } from "react";
import { AgentRunItem, DeploymentContext, listDeploymentRuns } from "../../api/registryApi";

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

function truncate(text: string | null | undefined, max: number): string {
  if (!text) return "—";
  return text.length > max ? text.slice(0, max) + "…" : text;
}

export default function RunsTab({
  deploymentId,
  context,
}: {
  deploymentId: string;
  context: DeploymentContext;
}) {
  const [triggerFilter, setTriggerFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const { data: runs, isLoading } = useQuery({
    queryKey: ["deployment-runs", deploymentId, context, triggerFilter, statusFilter],
    queryFn: () =>
      listDeploymentRuns(deploymentId, {
        context,
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
                <th className="px-4 py-2 w-8"></th>
                <th className="px-4 py-2">Trigger</th>
                <th className="px-4 py-2">Status</th>
                <th className="px-4 py-2">Input</th>
                <th className="px-4 py-2">Duration</th>
                <th className="px-4 py-2">Started</th>
                <th className="px-4 py-2">Trace</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {runs.map((run: AgentRunItem) => {
                const isExpanded = expandedId === run.id;
                return (
                  <tr key={run.id} className="group">
                    <td colSpan={7} className="p-0">
                      <div
                        className="grid hover:bg-slate-50 cursor-pointer"
                        style={{ gridTemplateColumns: "2rem 1fr 1fr 2fr 1fr 1fr 1fr" }}
                        onClick={() => setExpandedId(isExpanded ? null : run.id)}
                      >
                        <div className="px-4 py-2 flex items-center">
                          {isExpanded
                            ? <ChevronDown size={12} className="text-slate-400" />
                            : <ChevronRight size={12} className="text-slate-300" />}
                        </div>
                        <div className="px-4 py-2">
                          {TRIGGER_ICONS[run.trigger_type || ""] || "?"}{" "}
                          <span className="text-xs text-slate-500">{run.trigger_type || "—"}</span>
                        </div>
                        <div className="px-4 py-2">
                          <span className={`badge text-xs ${STATUS_BADGE[run.status] || "bg-slate-100 text-slate-600"}`}>
                            {run.status}
                          </span>
                        </div>
                        <div className="px-4 py-2 text-xs text-slate-600 truncate">
                          {truncate(run.input, 60)}
                        </div>
                        <div className="px-4 py-2 font-mono text-xs">
                          {run.latency_ms != null ? `${run.latency_ms}ms` : "—"}
                        </div>
                        <div className="px-4 py-2 text-xs text-slate-500">
                          {new Date(run.started_at).toLocaleString()}
                        </div>
                        <div className="px-4 py-2">
                          {run.trace_url ? (
                            <a
                              href={run.trace_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              onClick={(e) => e.stopPropagation()}
                              className="inline-flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 font-medium"
                            >
                              <ExternalLink size={12} />
                              Trace
                            </a>
                          ) : run.langfuse_trace_id ? (
                            <span className="text-xs text-slate-400">{run.langfuse_trace_id.slice(0, 8)}…</span>
                          ) : null}
                        </div>
                      </div>
                      {isExpanded && (
                        <div className="px-6 pb-3 pt-1 bg-slate-50/50 border-t border-slate-100">
                          <div className="grid grid-cols-2 gap-4 text-xs">
                            <div>
                              <p className="text-slate-400 uppercase text-[10px] font-semibold mb-1">Input</p>
                              <p className="text-slate-700 whitespace-pre-wrap font-mono bg-white rounded p-2 border border-slate-100 max-h-32 overflow-auto">
                                {run.input || "—"}
                              </p>
                            </div>
                            <div>
                              <p className="text-slate-400 uppercase text-[10px] font-semibold mb-1">Output</p>
                              <p className="text-slate-700 whitespace-pre-wrap font-mono bg-white rounded p-2 border border-slate-100 max-h-32 overflow-auto">
                                {run.output || "—"}
                              </p>
                            </div>
                            {run.error_message && (
                              <div className="col-span-2">
                                <p className="text-red-400 uppercase text-[10px] font-semibold mb-1">Error</p>
                                <p className="text-red-700 whitespace-pre-wrap font-mono bg-red-50 rounded p-2 border border-red-100">
                                  {run.error_message}
                                </p>
                              </div>
                            )}
                            <div className="col-span-2 flex gap-6 text-slate-500">
                              {run.run_by && <span>Run by: <span className="text-slate-700">{run.run_by}</span></span>}
                              {run.cost_usd != null && run.cost_usd > 0 && (
                                <span>Cost: <span className="text-slate-700">${run.cost_usd.toFixed(4)}</span></span>
                              )}
                              {run.team && <span>Team: <span className="text-slate-700">{run.team}</span></span>}
                            </div>
                          </div>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

    </div>
  );
}
