import { useQuery } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, CheckCircle, Loader2, XCircle } from "lucide-react";
import { getEvalRun, getEvalRunResults } from "../api/playgroundApi";

const STATUS_CHIP: Record<string, string> = {
  pending:   "bg-amber-100 text-amber-700",
  running:   "bg-blue-100 text-blue-700",
  completed: "bg-green-100 text-green-700",
  failed:    "bg-red-100 text-red-700",
};

export default function EvalResultsPage() {
  const { evalRunId } = useParams<{ evalRunId: string }>();
  const navigate = useNavigate();

  const { data: run, isLoading: runLoading } = useQuery({
    queryKey: ["eval-run", evalRunId],
    queryFn: () => getEvalRun(evalRunId!),
    enabled: !!evalRunId,
    refetchInterval: (query) =>
      query.state.data?.status === "completed" || query.state.data?.status === "failed"
        ? false
        : 5_000,
  });

  const { data: results, isLoading: resultsLoading } = useQuery({
    queryKey: ["eval-run-results", evalRunId],
    queryFn: () => getEvalRunResults(evalRunId!),
    enabled: !!evalRunId && run?.status === "completed",
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
            <h1 className="text-xl font-bold text-slate-900">{run.agent_name}</h1>
            <p className="text-xs text-slate-400 mt-0.5 font-mono">
              Run ID: {run.id.slice(0, 8)}…
            </p>
          </div>
          <span className={`badge ${STATUS_CHIP[run.status] ?? "bg-slate-100 text-slate-600"}`}>
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
          <div className="grid grid-cols-3 gap-4 mt-4">
            <div className="text-center">
              <p className="text-3xl font-bold text-slate-900">
                {scorePercent != null ? `${scorePercent}%` : "—"}
              </p>
              <p className="text-xs text-slate-400 mt-0.5">Overall Score</p>
            </div>
            <div className="text-center">
              <p className="text-3xl font-bold text-green-600">{run.passed_count ?? 0}</p>
              <p className="text-xs text-slate-400 mt-0.5">Passed</p>
            </div>
            <div className="text-center">
              <p className="text-3xl font-bold text-red-500">{run.failed_count ?? 0}</p>
              <p className="text-xs text-slate-400 mt-0.5">Failed</p>
            </div>
          </div>
        )}
      </div>

      {/* Results table */}
      {results && results.length > 0 && (
        <div className="card p-0 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-100 bg-slate-50">
                {["#", "Input", "Response", "Passed", "Score", "Reasoning"].map((h) => (
                  <th
                    key={h}
                    className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {results.map((r) => (
                <tr key={r.id} className="hover:bg-slate-50 transition-colors">
                  <td className="px-4 py-3 text-slate-500 text-xs">{r.dataset_item_idx + 1}</td>
                  <td className="px-4 py-3 text-slate-700 max-w-[200px] truncate">
                    {r.input_message ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-slate-500 max-w-[200px] truncate text-xs">
                    {r.response ? r.response.slice(0, 100) + (r.response.length > 100 ? "…" : "") : "—"}
                  </td>
                  <td className="px-4 py-3">
                    {r.passed === true ? (
                      <CheckCircle size={16} className="text-green-500" />
                    ) : r.passed === false ? (
                      <XCircle size={16} className="text-red-500" />
                    ) : (
                      <span className="text-slate-300">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-slate-600">
                    {r.judge_score != null ? r.judge_score.toFixed(2) : "—"}
                  </td>
                  <td className="px-4 py-3 text-slate-400 text-xs max-w-[180px] truncate">
                    {r.judge_reasoning ?? "—"}
                  </td>
                </tr>
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
    </div>
  );
}
