import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Clock, Loader2, ShieldAlert } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { getTraceDetail } from "../api/observabilityApi";

interface Observation {
  id?: string;
  name: string;
  type?: string;
  start_time?: string;
  end_time?: string;
  startTime?: string;
  endTime?: string;
  input?: unknown;
  output?: unknown;
  metadata?: Record<string, unknown>;
  model?: string;
  usage?: Record<string, unknown>;
}

function getDuration(obs: Observation): number | null {
  const start = obs.start_time || obs.startTime;
  const end = obs.end_time || obs.endTime;
  if (!start || !end) return null;
  return Math.round(new Date(end).getTime() - new Date(start).getTime());
}

function isSafety(name: string) {
  return name.startsWith("safety_scan") || name.startsWith("safety-scan");
}

interface LangfuseScore {
  name?: string;
  value?: number;
}

/** Pull the judge score off a fetched Langfuse trace (scores[] with name ~ judge). */
function judgeScore(detail: { langfuse?: unknown } | undefined): number | null {
  const scores = (detail?.langfuse as { scores?: LangfuseScore[] })?.scores;
  if (!Array.isArray(scores)) return null;
  const s = scores.find(
    (x) => typeof x.name === "string" && x.name.toLowerCase().includes("judge")
  );
  return s && typeof s.value === "number" ? s.value : null;
}

type DiffStatus = "same" | "added" | "removed" | "changed";

interface DiffRow {
  name: string;
  status: DiffStatus;
  durationA: number | null;
  durationB: number | null;
  obsA?: Observation;
  obsB?: Observation;
}

function buildDiff(obsA: Observation[], obsB: Observation[]): DiffRow[] {
  const mapA = new Map<string, Observation>();
  const mapB = new Map<string, Observation>();
  obsA.forEach((o) => mapA.set(o.name, o));
  obsB.forEach((o) => mapB.set(o.name, o));

  const rows: DiffRow[] = [];
  const seen = new Set<string>();

  for (const obs of obsA) {
    seen.add(obs.name);
    const match = mapB.get(obs.name);
    const durA = getDuration(obs);
    const durB = match ? getDuration(match) : null;
    let status: DiffStatus = "removed";
    if (match) {
      const changed = durA && durB && Math.abs(durA - durB) / Math.max(durA, 1) > 0.2;
      status = changed ? "changed" : "same";
    }
    rows.push({ name: obs.name, status, durationA: durA, durationB: durB, obsA: obs, obsB: match });
  }

  for (const obs of obsB) {
    if (!seen.has(obs.name)) {
      rows.push({ name: obs.name, status: "added", durationA: null, durationB: getDuration(obs), obsB: obs });
    }
  }

  return rows;
}

const STATUS_STYLE: Record<DiffStatus, string> = {
  same: "border-slate-100",
  added: "border-green-200 bg-green-50/30",
  removed: "border-red-200 bg-red-50/30",
  changed: "border-yellow-200 bg-yellow-50/30",
};

const STATUS_LABEL: Record<DiffStatus, string> = {
  same: "",
  added: "NEW",
  removed: "REMOVED",
  changed: "CHANGED",
};

export default function ObservabilityComparePage() {
  const [params] = useSearchParams();
  const traceA = params.get("a") || "";
  const traceB = params.get("b") || "";

  const { data: dataA, isLoading: loadingA } = useQuery({
    queryKey: ["trace-compare", traceA],
    queryFn: () => getTraceDetail(traceA),
    enabled: !!traceA,
  });

  const { data: dataB, isLoading: loadingB } = useQuery({
    queryKey: ["trace-compare", traceB],
    queryFn: () => getTraceDetail(traceB),
    enabled: !!traceB,
  });

  if (!traceA || !traceB) {
    return (
      <div className="p-6 max-w-4xl mx-auto">
        <p className="text-sm text-slate-400">Select two traces to compare from the Traces page.</p>
        <Link to="/observability/traces" className="text-sm text-blue-500 hover:underline mt-2 inline-block">
          Go to Traces
        </Link>
      </div>
    );
  }

  const isLoading = loadingA || loadingB;

  const obsA: Observation[] = (dataA?.langfuse as { observations?: Observation[] })?.observations ?? [];
  const obsB: Observation[] = (dataB?.langfuse as { observations?: Observation[] })?.observations ?? [];
  const diffRows = !isLoading ? buildDiff(obsA, obsB) : [];

  // Summary metrics
  const totalDurA = obsA.reduce((s, o) => s + (getDuration(o) ?? 0), 0);
  const totalDurB = obsB.reduce((s, o) => s + (getDuration(o) ?? 0), 0);
  const durDelta = totalDurA > 0 ? ((totalDurB - totalDurA) / totalDurA) * 100 : 0;

  const scoreA = judgeScore(dataA);
  const scoreB = judgeScore(dataB);
  const scoreDelta = scoreA != null && scoreB != null ? scoreB - scoreA : null;

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Link to="/observability/traces" className="text-slate-400 hover:text-slate-600">
          <ArrowLeft size={16} />
        </Link>
        <h1 className="text-lg font-semibold text-slate-800">
          Compare Traces
        </h1>
      </div>

      {/* Trace IDs */}
      <div className="grid grid-cols-2 gap-4 text-xs">
        <div className="bg-slate-50 rounded px-3 py-2 border border-slate-200">
          <span className="text-slate-500">Trace A: </span>
          <span className="font-mono text-slate-700">{traceA.slice(0, 16)}…</span>
        </div>
        <div className="bg-slate-50 rounded px-3 py-2 border border-slate-200">
          <span className="text-slate-500">Trace B: </span>
          <span className="font-mono text-slate-700">{traceB.slice(0, 16)}…</span>
        </div>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-16 text-slate-400">
          <Loader2 size={18} className="animate-spin mr-2" /> Loading traces…
        </div>
      ) : (
        <>
          {/* Summary bar */}
          <div className="flex gap-4 bg-white border border-slate-200 rounded-lg p-4">
            <div>
              <p className="text-xs text-slate-500">Total Duration A</p>
              <p className="text-sm font-medium text-slate-800">{(totalDurA / 1000).toFixed(2)}s</p>
            </div>
            <div>
              <p className="text-xs text-slate-500">Total Duration B</p>
              <p className="text-sm font-medium text-slate-800">{(totalDurB / 1000).toFixed(2)}s</p>
            </div>
            <div>
              <p className="text-xs text-slate-500">Delta</p>
              <p className={`text-sm font-medium ${durDelta < 0 ? "text-green-600" : durDelta > 0 ? "text-red-600" : "text-slate-600"}`}>
                {durDelta > 0 ? "+" : ""}{durDelta.toFixed(1)}%
              </p>
            </div>
            <div>
              <p className="text-xs text-slate-500">Judge Score</p>
              <p className="text-sm font-medium text-slate-800">
                {scoreA != null ? scoreA.toFixed(2) : "—"} → {scoreB != null ? scoreB.toFixed(2) : "—"}
              </p>
            </div>
            <div>
              <p className="text-xs text-slate-500">Score Delta</p>
              <p className={`text-sm font-medium ${
                scoreDelta == null ? "text-slate-400" :
                scoreDelta > 0 ? "text-green-600" :
                scoreDelta < 0 ? "text-red-600" : "text-slate-600"
              }`}>
                {scoreDelta == null ? "—" : `${scoreDelta > 0 ? "+" : ""}${scoreDelta.toFixed(2)}`}
              </p>
            </div>
            <div>
              <p className="text-xs text-slate-500">Spans</p>
              <p className="text-sm font-medium text-slate-800">{obsA.length} → {obsB.length}</p>
            </div>
          </div>

          {/* Diff table */}
          <div className="border border-slate-200 rounded-lg overflow-hidden">
            <div className="grid grid-cols-[1fr_80px_80px_80px] gap-2 text-xs font-medium text-slate-500 px-4 py-2 bg-slate-50 border-b border-slate-200">
              <span>Span</span>
              <span>Duration A</span>
              <span>Duration B</span>
              <span>Status</span>
            </div>
            {diffRows.map((row, i) => (
              <div
                key={i}
                className={`grid grid-cols-[1fr_80px_80px_80px] gap-2 items-center text-sm px-4 py-2 border-b last:border-0 ${STATUS_STYLE[row.status]}`}
              >
                <div className="flex items-center gap-1.5 min-w-0">
                  {isSafety(row.name) && <ShieldAlert size={12} className="text-orange-400 shrink-0" />}
                  <span className="text-xs font-medium text-slate-700 truncate">{row.name}</span>
                </div>
                <span className="text-xs text-slate-500">
                  {row.durationA != null ? `${row.durationA}ms` : "—"}
                </span>
                <span className="text-xs text-slate-500">
                  {row.durationB != null ? `${row.durationB}ms` : "—"}
                </span>
                <span className={`text-[10px] font-medium ${
                  row.status === "added" ? "text-green-600" :
                  row.status === "removed" ? "text-red-600" :
                  row.status === "changed" ? "text-yellow-700" :
                  "text-slate-400"
                }`}>
                  {STATUS_LABEL[row.status]}
                </span>
              </div>
            ))}
            {diffRows.length === 0 && (
              <p className="text-sm text-slate-400 text-center py-8">No observations to compare.</p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
