import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Clock, ExternalLink, Loader2, ShieldAlert, X } from "lucide-react";
import { useState } from "react";
import { getTraceById } from "../../api/playgroundApi";
import { getTraceDetail } from "../../api/observabilityApi";

type TraceFetchFn = (id: string) => Promise<{ trace_id: string; trace_url: string | null; langfuse: Record<string, unknown> }>;

interface Observation {
  id: string;
  name: string;
  type: string;
  startTime: string;
  endTime?: string;
  input?: unknown;
  output?: unknown;
  metadata?: Record<string, unknown>;
  statusMessage?: string;
  level?: string;
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

  const observations: Observation[] =
    (data?.langfuse as { observations?: Observation[] })?.observations ?? [];

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/20" onClick={onClose} />
      <div className="relative w-[520px] max-w-full bg-white shadow-xl border-l border-slate-200 flex flex-col h-full overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100 shrink-0">
          <div>
            <h3 className="text-sm font-semibold text-slate-900">
              Execution Trace
            </h3>
            <div className="flex items-center gap-2 mt-0.5">
              <p className="text-xs text-slate-400 font-mono">
                {traceId.slice(0, 12)}…
              </p>
              {(data as { trace_url?: string })?.trace_url && (
                <a
                  href={(data as { trace_url?: string }).trace_url!}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-0.5 text-xs text-blue-600 hover:text-blue-800 font-medium"
                >
                  <ExternalLink size={10} />
                  Langfuse
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

          {error && (
            <p className="text-sm text-red-600">Failed to load trace data.</p>
          )}

          {data && !isLoading && (
            <>
              {observations.length === 0 && (
                <p className="text-sm text-slate-400 text-center py-8">
                  {(data.langfuse as { warning?: string })?.warning ??
                    "No observations in this trace."}
                </p>
              )}

              {observations.length > 0 && (
                <div className="space-y-1">
                  {observations.map((obs) => (
                    <SpanRow key={obs.id} observation={obs} />
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

function SpanRow({ observation: obs }: { observation: Observation }) {
  const [expanded, setExpanded] = useState(false);

  const duration =
    obs.startTime && obs.endTime
      ? Math.round(
          new Date(obs.endTime).getTime() - new Date(obs.startTime).getTime()
        )
      : null;

  const isSafetySpan = obs.name.startsWith("safety_scan") || obs.name.startsWith("safety-scan");
  const isBlocked = isSafetySpan && (obs.metadata as Record<string, unknown>)?.blocked === true;

  const typeColor: Record<string, string> = {
    GENERATION: "bg-purple-100 text-purple-700",
    SPAN: "bg-blue-100 text-blue-700",
    EVENT: "bg-amber-100 text-amber-700",
  };

  return (
    <div className={`border rounded-md ${isBlocked ? "border-red-200 bg-red-50/30" : "border-slate-100"}`}>
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-slate-50 transition-colors"
      >
        {expanded ? (
          <ChevronDown size={12} className="text-slate-400 shrink-0" />
        ) : (
          <ChevronRight size={12} className="text-slate-400 shrink-0" />
        )}
        {isSafetySpan && (
          <ShieldAlert size={12} className={`shrink-0 ${isBlocked ? "text-red-500" : "text-orange-400"}`} />
        )}
        <span
          className={`badge text-[10px] px-1.5 py-0.5 ${typeColor[obs.type] ?? "bg-slate-100 text-slate-600"}`}
        >
          {obs.type}
        </span>
        <span className="text-xs font-medium text-slate-700 truncate flex-1">
          {obs.name}
        </span>
        {duration != null && (
          <span className="inline-flex items-center gap-0.5 text-[10px] text-slate-400 shrink-0">
            <Clock size={10} />
            {duration}ms
          </span>
        )}
      </button>
      {expanded && (
        <div className="px-3 pb-3 space-y-2 text-xs">
          {obs.input != null && (
            <div>
              <p className="font-semibold text-slate-500 mb-0.5">Input</p>
              <pre className="bg-slate-50 rounded p-2 overflow-x-auto text-slate-600 max-h-40 overflow-y-auto">
                {typeof obs.input === "string" ? obs.input : JSON.stringify(obs.input, null, 2)}
              </pre>
            </div>
          )}
          {obs.output != null && (
            <div>
              <p className="font-semibold text-slate-500 mb-0.5">Output</p>
              <pre className="bg-slate-50 rounded p-2 overflow-x-auto text-slate-600 max-h-40 overflow-y-auto">
                {typeof obs.output === "string" ? obs.output : JSON.stringify(obs.output, null, 2)}
              </pre>
            </div>
          )}
          {obs.statusMessage && (
            <p className="text-red-500">{obs.statusMessage}</p>
          )}
        </div>
      )}
    </div>
  );
}
