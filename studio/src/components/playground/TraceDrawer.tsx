import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Clock, ExternalLink, Loader2, ShieldAlert, X } from "lucide-react";
import { useState } from "react";
import { getTraceById } from "../../api/playgroundApi";
import { getTraceDetail } from "../../api/observabilityApi";
import type { TraceDetail, NormalizedSpan } from "../../api/observabilityApi";

type TraceFetchFn = (id: string) => Promise<TraceDetail>;

interface SpanNode extends NormalizedSpan {
  children: SpanNode[];
}

/** Nest flat spans into a tree by parent_id. Spans whose parent is missing
 * (or null) become roots — so an incomplete trace still renders. */
function buildSpanTree(spans: NormalizedSpan[]): SpanNode[] {
  const byId = new Map<string, SpanNode>();
  spans.forEach((s) => byId.set(s.id, { ...s, children: [] }));
  const roots: SpanNode[] = [];
  byId.forEach((node) => {
    const parent = node.parent_id ? byId.get(node.parent_id) : undefined;
    if (parent) parent.children.push(node);
    else roots.push(node);
  });
  const startMs = (s: NormalizedSpan) => (s.start_time ? new Date(s.start_time).getTime() : 0);
  const sortRec = (nodes: SpanNode[]) => {
    nodes.sort((a, b) => startMs(a) - startMs(b));
    nodes.forEach((n) => sortRec(n.children));
  };
  sortRec(roots);
  return roots;
}

/** The [min start, max end] window across all spans, for waterfall scaling. */
function traceWindow(spans: NormalizedSpan[]): { start: number; span: number } {
  const times: number[] = [];
  spans.forEach((s) => {
    if (s.start_time) times.push(new Date(s.start_time).getTime());
    if (s.end_time) times.push(new Date(s.end_time).getTime());
  });
  if (times.length === 0) return { start: 0, span: 1 };
  const start = Math.min(...times);
  const end = Math.max(...times);
  return { start, span: Math.max(1, end - start) };
}

function fmtUsd(n: number): string {
  return `$${n.toFixed(n < 0.01 ? 5 : 4)}`;
}

export default function TraceDrawer({
  traceId,
  onClose,
  fetchFn,
}: {
  traceId: string;
  onClose: () => void;
  fetchFn?: TraceFetchFn;
}) {
  const fetcher = fetchFn ?? getTraceById;
  const { data, isLoading, error } = useQuery({
    queryKey: ["trace", traceId, fetchFn ? "obs" : "pg"],
    queryFn: () => fetcher(traceId),
    enabled: !!traceId,
  });

  const trace = data?.trace ?? null;
  const spans: NormalizedSpan[] = trace?.spans ?? [];
  const roots = buildSpanTree(spans);
  const window = traceWindow(spans);
  const scores = trace?.scores ?? [];

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/20" onClick={onClose} />
      <div className="relative w-[560px] max-w-full bg-white shadow-xl border-l border-slate-200 flex flex-col h-full overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100 shrink-0">
          <div>
            <h3 className="text-sm font-semibold text-slate-900">Execution Trace</h3>
            <div className="flex items-center gap-2 mt-0.5">
              <p className="text-xs text-slate-400 font-mono">{traceId.slice(0, 12)}…</p>
              {data?.trace_url && (
                <a
                  href={data.trace_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-0.5 text-xs text-blue-600 hover:text-blue-800 font-medium"
                >
                  <ExternalLink size={10} />
                  Trace
                </a>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-slate-100 text-slate-400 hover:text-slate-600"
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-4">
          {isLoading && (
            <div className="flex items-center justify-center py-12 text-slate-400">
              <Loader2 size={18} className="animate-spin mr-2" />
              Loading trace…
            </div>
          )}

          {error && <p className="text-sm text-red-600">Failed to load trace data.</p>}

          {data && !isLoading && (
            <>
              {/* Trace-level metadata */}
              {trace && !trace.warning && (
                <div className="mb-3 space-y-2 text-xs border border-slate-100 rounded-md p-3 bg-slate-50">
                  {trace.name && (
                    <div className="flex justify-between">
                      <span className="text-slate-500">Name</span>
                      <span className="font-medium text-slate-700">{trace.name}</span>
                    </div>
                  )}
                  {trace.user && (
                    <div className="flex justify-between">
                      <span className="text-slate-500">User</span>
                      <span className="font-medium text-slate-700">{trace.user}</span>
                    </div>
                  )}
                  {trace.started_at && (
                    <div className="flex justify-between">
                      <span className="text-slate-500">Started</span>
                      <span className="text-slate-700">{new Date(trace.started_at).toLocaleString()}</span>
                    </div>
                  )}
                  {trace.total_cost != null && (
                    <div className="flex justify-between">
                      <span className="text-slate-500">Total Cost</span>
                      <span className="font-medium text-emerald-700">{fmtUsd(trace.total_cost)}</span>
                    </div>
                  )}
                  {trace.tags?.length ? (
                    <div className="flex justify-between">
                      <span className="text-slate-500">Tags</span>
                      <span className="text-slate-700">{trace.tags.join(", ")}</span>
                    </div>
                  ) : null}
                </div>
              )}

              {/* Scores */}
              {scores.length > 0 && (
                <div className="mb-3">
                  <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide mb-1">Scores</p>
                  <div className="flex flex-wrap gap-1.5">
                    {scores.map((s, i) => (
                      <span
                        key={`${s.name}-${i}`}
                        title={s.comment ?? undefined}
                        className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded bg-blue-50 text-blue-700 border border-blue-100"
                      >
                        <span className="font-medium">{s.name}</span>
                        <span className="font-mono">{s.value != null ? s.value : "—"}</span>
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {spans.length === 0 && (
                <p className="text-sm text-slate-400 text-center py-8">
                  {trace?.warning ?? "No span-level observations recorded for this trace."}
                </p>
              )}

              {spans.length > 0 && (
                <div className="space-y-1">
                  {roots.map((node) => (
                    <SpanNodeRow
                      key={node.id}
                      node={node}
                      depth={0}
                      winStart={window.start}
                      winSpan={window.span}
                    />
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

const TYPE_BADGE: Record<string, string> = {
  GENERATION: "bg-purple-100 text-purple-700",
  TOOL: "bg-indigo-100 text-indigo-700",
  CHAIN: "bg-slate-100 text-slate-600",
  AGENT: "bg-teal-100 text-teal-700",
  SPAN: "bg-blue-100 text-blue-700",
  EVENT: "bg-amber-100 text-amber-700",
};
const TYPE_BAR: Record<string, string> = {
  GENERATION: "bg-purple-400",
  TOOL: "bg-indigo-400",
  CHAIN: "bg-slate-300",
  AGENT: "bg-teal-400",
  SPAN: "bg-blue-300",
  EVENT: "bg-amber-300",
};

function SpanNodeRow({
  node,
  depth,
  winStart,
  winSpan,
}: {
  node: SpanNode;
  depth: number;
  winStart: number;
  winSpan: number;
}) {
  const [expanded, setExpanded] = useState(false);

  const startMs = node.start_time ? new Date(node.start_time).getTime() : null;
  const endMs = node.end_time ? new Date(node.end_time).getTime() : null;
  const duration = startMs != null && endMs != null ? Math.round(endMs - startMs) : null;

  // Waterfall bar geometry (percent of the trace window).
  const leftPct = startMs != null ? ((startMs - winStart) / winSpan) * 100 : 0;
  const widthPct =
    startMs != null && endMs != null ? Math.max(1.5, ((endMs - startMs) / winSpan) * 100) : 1.5;

  const isSafetySpan = node.name.startsWith("safety_scan") || node.name.startsWith("safety-scan");
  const isBlocked = isSafetySpan && (node.metadata as Record<string, unknown>)?.blocked === true;
  const isGen = node.type === "GENERATION";
  const hasEconomics = node.cost_usd != null || node.prompt_tokens != null || node.model != null;

  return (
    <div>
      <div className={`border rounded-md ${isBlocked ? "border-red-200 bg-red-50/30" : "border-slate-100"}`}>
        <button
          onClick={() => setExpanded(!expanded)}
          className="w-full flex items-center gap-2 px-2 py-2 text-left hover:bg-slate-50 transition-colors"
          style={{ paddingLeft: `${8 + depth * 14}px` }}
        >
          {expanded ? (
            <ChevronDown size={12} className="text-slate-400 shrink-0" />
          ) : (
            <ChevronRight size={12} className="text-slate-400 shrink-0" />
          )}
          {isSafetySpan && (
            <ShieldAlert size={12} className={`shrink-0 ${isBlocked ? "text-red-500" : "text-orange-400"}`} />
          )}
          <span className={`text-[10px] px-1.5 py-0.5 rounded shrink-0 ${TYPE_BADGE[node.type] ?? "bg-slate-100 text-slate-600"}`}>
            {node.type}
          </span>
          <span className="text-xs font-medium text-slate-700 truncate w-28 shrink-0">{node.name}</span>
          {/* Waterfall bar */}
          <div className="relative flex-1 h-2 bg-slate-50 rounded overflow-hidden min-w-[40px]">
            <div
              className={`absolute h-full rounded ${TYPE_BAR[node.type] ?? "bg-slate-300"}`}
              style={{ left: `${Math.min(99, Math.max(0, leftPct))}%`, width: `${Math.min(100, widthPct)}%` }}
            />
          </div>
          {isGen && node.cost_usd != null && (
            <span className="text-[10px] text-emerald-600 shrink-0 font-mono">{fmtUsd(node.cost_usd)}</span>
          )}
          {duration != null && (
            <span className="inline-flex items-center gap-0.5 text-[10px] text-slate-400 shrink-0 w-14 justify-end">
              <Clock size={10} />
              {duration}ms
            </span>
          )}
        </button>
        {expanded && (
          <div className="px-3 pb-3 space-y-2 text-xs" style={{ paddingLeft: `${20 + depth * 14}px` }}>
            {hasEconomics && (
              <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-[11px] text-slate-500 pt-1">
                {node.model && <span>model <span className="font-mono text-slate-700">{node.model}</span></span>}
                {node.cost_usd != null && <span>cost <span className="font-mono text-emerald-700">{fmtUsd(node.cost_usd)}</span></span>}
                {node.prompt_tokens != null && <span>in <span className="font-mono text-slate-700">{node.prompt_tokens}</span></span>}
                {node.completion_tokens != null && <span>out <span className="font-mono text-slate-700">{node.completion_tokens}</span></span>}
              </div>
            )}
            {node.input != null && (
              <div>
                <p className="font-semibold text-slate-500 mb-0.5">Input</p>
                <pre className="bg-slate-50 rounded p-2 overflow-x-auto text-slate-600 max-h-40 overflow-y-auto">
                  {typeof node.input === "string" ? node.input : JSON.stringify(node.input, null, 2)}
                </pre>
              </div>
            )}
            {node.output != null && (
              <div>
                <p className="font-semibold text-slate-500 mb-0.5">Output</p>
                <pre className="bg-slate-50 rounded p-2 overflow-x-auto text-slate-600 max-h-40 overflow-y-auto">
                  {typeof node.output === "string" ? node.output : JSON.stringify(node.output, null, 2)}
                </pre>
              </div>
            )}
            {node.status_message && <p className="text-red-500">{node.status_message}</p>}
          </div>
        )}
      </div>
      {/* Children (nested) */}
      {node.children.length > 0 && (
        <div className="mt-1 space-y-1">
          {node.children.map((child) => (
            <SpanNodeRow key={child.id} node={child} depth={depth + 1} winStart={winStart} winSpan={winSpan} />
          ))}
        </div>
      )}
    </div>
  );
}
