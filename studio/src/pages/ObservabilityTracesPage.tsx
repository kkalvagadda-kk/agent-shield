import { useQuery } from "@tanstack/react-query";
import { Activity, ChevronLeft, ChevronRight, Eye, Filter, GitCompare } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { listTraces, getTraceDetail, TracesFilter, TraceSummary } from "../api/observabilityApi";
import { listAgents } from "../api/registryApi";
import TraceDrawer from "../components/playground/TraceDrawer";

const STATUS_COLORS: Record<string, string> = {
  running: "bg-blue-100 text-blue-700",
  completed: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
  blocked: "bg-orange-100 text-orange-700",
  cancelled: "bg-slate-100 text-slate-600",
};

const CONTEXT_COLORS: Record<string, string> = {
  playground: "bg-purple-100 text-purple-700",
  production: "bg-emerald-100 text-emerald-700",
};

function scoreColor(score: number | null): string {
  if (score === null) return "text-slate-400";
  if (score >= 0.8) return "text-green-600";
  if (score >= 0.5) return "text-yellow-600";
  return "text-red-600";
}

export default function ObservabilityTracesPage() {
  const [filters, setFilters] = useState<TracesFilter>({ limit: 20, offset: 0 });
  const [traceId, setTraceId] = useState<string | null>(null);
  const [showFilters, setShowFilters] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const navigate = useNavigate();

  const { data, isLoading } = useQuery({
    queryKey: ["observability-traces", filters],
    queryFn: () => listTraces(filters),
    staleTime: 15_000,
  });

  const { data: agentsData } = useQuery({
    queryKey: ["agents-for-filter"],
    queryFn: () => listAgents(200, 0, "active"),
    staleTime: 60_000,
  });

  const agents = agentsData?.items ?? [];
  const items = data?.items ?? [];
  const hasMore = data?.has_more ?? false;
  const offset = filters.offset ?? 0;

  function updateFilter(key: keyof TracesFilter, value: string | undefined) {
    setFilters((f) => ({ ...f, [key]: value || undefined, offset: 0 }));
  }

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Activity size={20} className="text-blue-500" />
          <h1 className="text-xl font-semibold text-slate-800">Traces</h1>
        </div>
        <div className="flex items-center gap-2">
          {selected.size === 2 && (
            <button
              onClick={() => {
                const ids = Array.from(selected);
                navigate(`/observability/compare?a=${ids[0]}&b=${ids[1]}`);
              }}
              className="flex items-center gap-1.5 text-sm px-3 py-1.5 rounded border border-blue-300 bg-blue-50 text-blue-700"
            >
              <GitCompare size={14} /> Compare
            </button>
          )}
          {selected.size > 0 && selected.size !== 2 && (
            <span className="text-xs text-slate-400">{selected.size}/2 selected</span>
          )}
          <button
            onClick={() => setShowFilters(!showFilters)}
            className={`flex items-center gap-1.5 text-sm px-3 py-1.5 rounded border ${
              showFilters ? "border-blue-300 bg-blue-50 text-blue-700" : "border-slate-200 text-slate-600 hover:bg-slate-50"
            }`}
          >
            <Filter size={14} /> Filters
          </button>
        </div>
      </div>

      {/* Filters */}
      {showFilters && (
        <div className="flex flex-wrap gap-3 p-3 bg-slate-50 rounded-lg border border-slate-200">
          <select
            value={filters.agent_name || ""}
            onChange={(e) => updateFilter("agent_name", e.target.value)}
            className="text-sm border border-slate-200 rounded px-2 py-1.5"
          >
            <option value="">All agents</option>
            {agents.map((a) => (
              <option key={a.name} value={a.name}>{a.name}</option>
            ))}
          </select>

          <select
            value={filters.status || ""}
            onChange={(e) => updateFilter("status", e.target.value)}
            className="text-sm border border-slate-200 rounded px-2 py-1.5"
          >
            <option value="">All statuses</option>
            <option value="running">Running</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
            <option value="blocked">Blocked</option>
          </select>

          <select
            value={filters.context || ""}
            onChange={(e) => updateFilter("context", e.target.value)}
            className="text-sm border border-slate-200 rounded px-2 py-1.5"
          >
            <option value="">All contexts</option>
            <option value="playground">Playground</option>
            <option value="production">Production</option>
          </select>

          <select
            value={filters.trigger_type || ""}
            onChange={(e) => updateFilter("trigger_type", e.target.value)}
            className="text-sm border border-slate-200 rounded px-2 py-1.5"
          >
            <option value="">All triggers</option>
            <option value="manual">Manual</option>
            <option value="api">API</option>
            <option value="schedule">Schedule</option>
            <option value="webhook">Webhook</option>
            <option value="workflow">Workflow</option>
          </select>
        </div>
      )}

      {/* Table */}
      {isLoading ? (
        <div className="flex items-center justify-center py-16 text-slate-400 text-sm">
          Loading traces…
        </div>
      ) : items.length === 0 ? (
        <div className="text-center py-16 text-slate-400 text-sm">
          No traces found. Run an agent to see traces here.
        </div>
      ) : (
        <div className="border border-slate-200 rounded-lg overflow-hidden">
          <div className="grid grid-cols-[30px_1fr_90px_90px_90px_80px_70px_70px_50px] gap-2 text-xs font-medium text-slate-500 px-4 py-2.5 bg-slate-50 border-b border-slate-200">
            <span></span>
            <span>Agent</span>
            <span>Status</span>
            <span>Context</span>
            <span>Trigger</span>
            <span>Latency</span>
            <span>Cost</span>
            <span>Score</span>
            <span>Trace</span>
          </div>
          {items.map((r) => (
            <TraceRow
              key={r.id}
              trace={r}
              onViewTrace={() => setTraceId(r.trace_id)}
              isSelected={selected.has(r.trace_id ?? "")}
              onToggleSelect={() => {
                if (!r.trace_id) return;
                setSelected((prev) => {
                  const next = new Set(prev);
                  if (next.has(r.trace_id!)) {
                    next.delete(r.trace_id!);
                  } else if (next.size < 2) {
                    next.add(r.trace_id!);
                  }
                  return next;
                });
              }}
            />
          ))}
        </div>
      )}

      {/* Pagination */}
      {items.length > 0 && (
        <div className="flex items-center justify-between text-sm text-slate-500">
          <span>
            Showing {offset + 1}–{offset + items.length}
          </span>
          <div className="flex gap-2">
            <button
              disabled={offset === 0}
              onClick={() => setFilters((f) => ({ ...f, offset: Math.max(0, (f.offset ?? 0) - (f.limit ?? 20)) }))}
              className="flex items-center gap-1 px-2 py-1 rounded border border-slate-200 disabled:opacity-40 hover:bg-slate-50"
            >
              <ChevronLeft size={14} /> Prev
            </button>
            <button
              disabled={!hasMore}
              onClick={() => setFilters((f) => ({ ...f, offset: (f.offset ?? 0) + (f.limit ?? 20) }))}
              className="flex items-center gap-1 px-2 py-1 rounded border border-slate-200 disabled:opacity-40 hover:bg-slate-50"
            >
              Next <ChevronRight size={14} />
            </button>
          </div>
        </div>
      )}

      {/* Trace drawer */}
      {traceId && <TraceDrawer traceId={traceId} onClose={() => setTraceId(null)} fetchFn={getTraceDetail} />}
    </div>
  );
}

function TraceRow({ trace, onViewTrace, isSelected, onToggleSelect }: {
  trace: TraceSummary;
  onViewTrace: () => void;
  isSelected: boolean;
  onToggleSelect: () => void;
}) {
  return (
    <div className="grid grid-cols-[30px_1fr_90px_90px_90px_80px_70px_70px_50px] gap-2 items-center text-sm px-4 py-2.5 border-b border-slate-100 last:border-0 hover:bg-slate-50">
      <span>
        {trace.trace_id && (
          <input
            type="checkbox"
            checked={isSelected}
            onChange={onToggleSelect}
            className="w-3.5 h-3.5 rounded border-slate-300"
          />
        )}
      </span>
      <div className="flex items-center gap-2 min-w-0">
        <span className="truncate font-medium text-slate-800">{trace.agent_name}</span>
        {trace.run_by && (
          <span className="text-xs text-slate-400 truncate">{trace.run_by}</span>
        )}
      </div>
      <span className={`badge text-xs w-fit ${STATUS_COLORS[trace.status] || "bg-slate-100 text-slate-600"}`}>
        {trace.status}
      </span>
      <span className={`badge text-xs w-fit ${CONTEXT_COLORS[trace.context] || "bg-slate-100 text-slate-600"}`}>
        {trace.context}
      </span>
      <span className="text-xs text-slate-500">{trace.trigger_type || "—"}</span>
      <span className="text-xs text-slate-500">
        {trace.latency_ms != null ? `${(trace.latency_ms / 1000).toFixed(1)}s` : "—"}
      </span>
      <span className="text-xs text-slate-500">
        {trace.cost_usd != null ? `$${trace.cost_usd.toFixed(4)}` : "—"}
      </span>
      <span className={`text-xs font-medium ${scoreColor(trace.judge_score)}`}>
        {trace.judge_score != null ? trace.judge_score.toFixed(2) : "—"}
      </span>
      <span>
        {trace.trace_id ? (
          <button
            onClick={onViewTrace}
            className="text-blue-500 hover:text-blue-700"
            title="View trace"
          >
            <Eye size={14} />
          </button>
        ) : (
          <span className="text-slate-300">—</span>
        )}
      </span>
    </div>
  );
}
