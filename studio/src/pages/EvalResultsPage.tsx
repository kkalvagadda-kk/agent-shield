import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  CheckCircle,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Eye,
  Filter,
  Loader2,
  Play,
  RotateCcw,
  Send,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import {
  createEvalRun,
  getEvalRun,
  getEvalRunResults,
  EvalRunResult,
} from "../api/playgroundApi";
import {
  patchVersion,
  patchWorkflowVersion,
  publishAgent,
  publishWorkflow,
} from "../api/registryApi";
import TraceDrawer from "../components/playground/TraceDrawer";

const STATUS_CHIP: Record<string, string> = {
  pending: "bg-amber-100 text-amber-700",
  running: "bg-blue-100 text-blue-700",
  completed: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
};

function scoreColor(score: number | null): string {
  if (score == null) return "";
  if (score < 0.4) return "bg-red-50 text-red-700";
  if (score <= 0.7) return "bg-amber-50 text-amber-700";
  return "bg-green-50 text-green-700";
}

export default function EvalResultsPage() {
  const { evalRunId } = useParams<{ evalRunId: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const [expandedRow, setExpandedRow] = useState<string | null>(null);
  const [showFailedOnly, setShowFailedOnly] = useState(false);
  const [traceId, setTraceId] = useState<string | null>(null);

  const { data: run, isLoading: runLoading } = useQuery({
    queryKey: ["eval-run", evalRunId],
    queryFn: () => getEvalRun(evalRunId!),
    enabled: !!evalRunId,
    refetchInterval: (query) =>
      query.state.data?.status === "completed" ||
      query.state.data?.status === "failed"
        ? false
        : 5_000,
  });

  const { data: results, isLoading: resultsLoading } = useQuery({
    queryKey: ["eval-run-results", evalRunId],
    queryFn: () => getEvalRunResults(evalRunId!),
    enabled: !!evalRunId && run?.status === "completed",
  });

  const isWorkflowEval = run?.workflow_id != null;

  const rerunMutation = useMutation({
    mutationFn: () =>
      createEvalRun({
        dataset_id: run!.dataset_id,
        ...(run!.sandbox_deployment_id
          ? { sandbox_deployment_id: run!.sandbox_deployment_id }
          : run!.workflow_deployment_id
            ? { workflow_deployment_id: run!.workflow_deployment_id }
            : { agent_name: run!.agent_name, agent_version_id: run!.agent_version_id ?? undefined }),
      }),
    onSuccess: (newRun) => {
      toast.success("Eval re-run started");
      navigate(`/playground/eval-runs/${newRun.id}`);
    },
    onError: () => toast.error("Failed to start eval re-run"),
  });

  const markPassedMutation = useMutation<unknown, Error, void>({
    mutationFn: () =>
      isWorkflowEval
        ? patchWorkflowVersion(run!.workflow_id!, run!.workflow_version_id!, { eval_passed: true })
        : patchVersion(run!.agent_name, run!.agent_version_id!, { eval_passed: true }),
    onSuccess: () => {
      toast.success("Version marked as eval passed");
      qc.invalidateQueries({ queryKey: ["eval-run", evalRunId] });
    },
    onError: () => toast.error("Failed to mark version passed"),
  });

  const publishMutation = useMutation({
    mutationFn: async () => {
      if (isWorkflowEval) {
        if (run!.workflow_version_id) {
          await patchWorkflowVersion(run!.workflow_id!, run!.workflow_version_id, { eval_passed: true });
        }
        return publishWorkflow(run!.workflow_id!, run!.workflow_version_id ?? undefined);
      }
      if (run!.agent_version_id) {
        await patchVersion(run!.agent_name, run!.agent_version_id, { eval_passed: true });
      }
      return publishAgent(run!.agent_name, { version_id: run!.agent_version_id ?? undefined });
    },
    onSuccess: () => {
      toast.success("Publish request submitted");
      qc.invalidateQueries({ queryKey: ["eval-run", evalRunId] });
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      toast.error(detail || "Failed to submit publish request");
    },
  });

  const isLoading = runLoading || resultsLoading;

  if (isLoading && !run) {
    return (
      <div className="flex items-center justify-center py-20 text-slate-400">
        <Loader2 size={20} className="animate-spin mr-2" />
        Loading eval run…
      </div>
    );
  }

  if (!run) {
    return (
      <div className="max-w-5xl mx-auto px-6 py-8 text-slate-500 text-sm">
        Eval run not found.
      </div>
    );
  }

  const scorePercent =
    run.overall_score != null ? Math.round(run.overall_score * 100) : null;
  const canMarkPassed =
    run.status === "completed" &&
    run.overall_score != null &&
    run.overall_score >= 0.7 &&
    (isWorkflowEval ? run.workflow_version_id != null : run.agent_version_id != null);

  const filteredResults = showFailedOnly
    ? (results ?? []).filter((r) => r.passed === false)
    : results ?? [];

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      {/* Back link */}
      <button
        onClick={() => navigate("/playground/datasets")}
        className="flex items-center gap-1 text-sm text-slate-400 hover:text-slate-600 mb-5 transition-colors"
      >
        <ArrowLeft size={14} />
        Back to Datasets
      </button>

      {/* Summary header */}
      <div className="card p-5 mb-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-bold text-slate-900">
              {run.agent_name}
            </h1>
            <p className="text-xs text-slate-400 mt-0.5 font-mono">
              Run ID: {run.id.slice(0, 8)}…
            </p>
          </div>
          <span
            className={`badge ${STATUS_CHIP[run.status] ?? "bg-slate-100 text-slate-600"}`}
          >
            {run.status}
          </span>
        </div>

        {run.status !== "completed" && run.status !== "failed" && (
          <div className="flex items-center gap-2 mt-3 text-sm text-blue-600">
            <Loader2 size={14} className="animate-spin" />
            Eval running — auto-refreshing every 5s…
          </div>
        )}

        {run.status === "completed" && (
          <>
            <div className="grid grid-cols-3 gap-4 mt-4">
              <div className="text-center">
                <p className="text-3xl font-bold text-slate-900">
                  {scorePercent != null ? `${scorePercent}%` : "—"}
                </p>
                <p className="text-xs text-slate-400 mt-0.5">Overall Score</p>
              </div>
              <div className="text-center">
                <p className="text-3xl font-bold text-green-600">
                  {run.passed_count ?? 0}
                </p>
                <p className="text-xs text-slate-400 mt-0.5">Passed</p>
              </div>
              <div className="text-center">
                <p className="text-3xl font-bold text-red-500">
                  {run.failed_count ?? 0}
                </p>
                <p className="text-xs text-slate-400 mt-0.5">Failed</p>
              </div>
            </div>

            {/* Action CTAs */}
            <div className="flex items-center gap-2 mt-5 pt-4 border-t border-slate-100">
              {canMarkPassed && (
                <button
                  onClick={() => markPassedMutation.mutate()}
                  disabled={markPassedMutation.isPending}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-green-600 text-white hover:bg-green-700 disabled:opacity-50"
                >
                  <CheckCircle size={12} />
                  Mark Version Passed
                </button>
              )}
              <button
                onClick={() => rerunMutation.mutate()}
                disabled={rerunMutation.isPending}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-slate-200 text-slate-600 hover:bg-slate-50 disabled:opacity-50"
              >
                <RotateCcw size={12} />
                Re-run Eval
              </button>
              <button
                onClick={() =>
                  navigate(
                    isWorkflowEval
                      ? `/workflows/${run.workflow_id}`
                      : `/agents/${run.agent_name}`
                  )
                }
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-slate-200 text-slate-600 hover:bg-slate-50"
              >
                {isWorkflowEval ? "Back to Workflow" : "Back to Agent"}
              </button>
              {canMarkPassed && (
                <button
                  onClick={() => publishMutation.mutate()}
                  disabled={publishMutation.isPending}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
                >
                  {publishMutation.isPending ? (
                    <Loader2 size={12} className="animate-spin" />
                  ) : (
                    <Send size={12} />
                  )}
                  {isWorkflowEval ? "Publish Workflow" : "Publish Agent"}
                </button>
              )}
            </div>
          </>
        )}
      </div>

      {/* Filter bar */}
      {results && results.length > 0 && (
        <div className="flex items-center gap-2 mb-3">
          <button
            onClick={() => setShowFailedOnly(!showFailedOnly)}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-full border transition-colors ${
              showFailedOnly
                ? "bg-red-50 border-red-200 text-red-700"
                : "border-slate-200 text-slate-500 hover:bg-slate-50"
            }`}
          >
            <Filter size={12} />
            Show failed only
          </button>
          <span className="text-xs text-slate-400">
            {filteredResults.length} of {results.length} results
          </span>
        </div>
      )}

      {/* Results table */}
      {filteredResults.length > 0 && (
        <div className="card p-0 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-100 bg-slate-50">
                {[
                  "",
                  "#",
                  "Input",
                  "Expected",
                  "Response",
                  "Passed",
                  "Score",
                  "Trace",
                ].map((h) => (
                  <th
                    key={h}
                    className="px-3 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {filteredResults.map((r) => (
                <ResultRow
                  key={r.id}
                  result={r}
                  expanded={expandedRow === r.id}
                  onToggle={() =>
                    setExpandedRow(expandedRow === r.id ? null : r.id)
                  }
                  onViewTrace={() => setTraceId(r.langfuse_trace_id)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {results && results.length === 0 && run.status === "completed" && (
        <p className="text-sm text-slate-400 text-center py-8">
          No item results recorded for this eval run.
        </p>
      )}

      {/* Trace drawer */}
      {traceId && (
        <TraceDrawer traceId={traceId} onClose={() => setTraceId(null)} />
      )}
    </div>
  );
}

function ResultRow({
  result: r,
  expanded,
  onToggle,
  onViewTrace,
}: {
  result: EvalRunResult;
  expanded: boolean;
  onToggle: () => void;
  onViewTrace: () => void;
}) {
  return (
    <>
      <tr
        onClick={onToggle}
        className="hover:bg-slate-50 transition-colors cursor-pointer"
      >
        <td className="px-3 py-3 text-slate-400">
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </td>
        <td className="px-3 py-3 text-slate-500 text-xs">
          {r.dataset_item_idx + 1}
        </td>
        <td className="px-3 py-3 text-slate-700 max-w-[160px] truncate">
          {r.input_message ?? "—"}
        </td>
        <td className="px-3 py-3 text-slate-500 max-w-[140px] truncate text-xs">
          {r.expected_output ?? "—"}
        </td>
        <td className="px-3 py-3 text-slate-500 max-w-[160px] truncate text-xs">
          {r.response
            ? r.response.slice(0, 80) + (r.response.length > 80 ? "…" : "")
            : "—"}
        </td>
        <td className="px-3 py-3">
          {r.passed === true ? (
            <CheckCircle size={16} className="text-green-500" />
          ) : r.passed === false ? (
            <XCircle size={16} className="text-red-500" />
          ) : (
            <span className="text-slate-300">—</span>
          )}
        </td>
        <td className="px-3 py-3">
          <span
            className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${scoreColor(r.judge_score)}`}
          >
            {r.judge_score != null ? r.judge_score.toFixed(2) : "—"}
          </span>
        </td>
        <td className="px-3 py-3">
          {r.trace_url ? (
            <a
              href={r.trace_url}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
              className="inline-flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 font-medium"
            >
              <ExternalLink size={12} />
              Trace
            </a>
          ) : r.langfuse_trace_id ? (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onViewTrace();
              }}
              className="inline-flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 font-medium"
            >
              <Eye size={12} />
              Trace
            </button>
          ) : null}
        </td>
      </tr>
      {expanded && (
        <tr className="bg-slate-25">
          <td colSpan={8} className="px-6 py-4">
            <div className="grid grid-cols-1 gap-3 text-xs">
              <div>
                <p className="font-semibold text-slate-600 mb-1">Input</p>
                <p className="text-slate-700 whitespace-pre-wrap bg-white rounded p-2 border border-slate-100">
                  {r.input_message ?? "—"}
                </p>
              </div>
              {r.expected_output && (
                <div>
                  <p className="font-semibold text-slate-600 mb-1">
                    Expected Output
                  </p>
                  <p className="text-slate-700 whitespace-pre-wrap bg-white rounded p-2 border border-slate-100">
                    {r.expected_output}
                  </p>
                </div>
              )}
              <div>
                <p className="font-semibold text-slate-600 mb-1">Response</p>
                <p className="text-slate-700 whitespace-pre-wrap bg-white rounded p-2 border border-slate-100">
                  {r.response ?? "—"}
                </p>
              </div>
              <div>
                <p className="font-semibold text-slate-600 mb-1">Reasoning</p>
                <p className="text-slate-500 whitespace-pre-wrap">
                  {r.judge_reasoning ?? "—"}
                </p>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
