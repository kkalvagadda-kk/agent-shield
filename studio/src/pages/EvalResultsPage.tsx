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
  listRunSteps,
  isWorkflowDetail,
  EvalRunResult,
  type StepUpdateEvent,
  type DatasetMode,
  type EvalDetail,
  type RecordedSideEffect,
  type SideEffectDiff,
} from "../api/playgroundApi";
import { tokenizeArgsForDisplay } from "../lib/piiTokenize";
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

// The colour band is a VERDICT, so it uses the run's OWN threshold — never a literal.
// Green = "this would publish", amber = "it would not". Hardcoded, a 0.85 run on a
// 0.9-threshold dataset rendered GREEN while the gate refused it: the UI contradicted
// the product it reports on.
//
// `threshold` is REQUIRED and has NO local default. The API always resolves it
// (`eval_run_response` fills pre-E-6 rows from the single platform default), so a
// default here would just re-declare the threshold — which is exactly how it came to
// exist four times across three services. If it is ever absent at runtime the
// comparison yields false and we render the NEUTRAL band: fail-closed, never a
// confident wrong verdict.
function scoreColor(score: number | null, threshold: number): string {
  if (score == null) return "";
  if (!(score >= threshold * 0.6)) return "bg-red-50 text-red-700";
  if (!(score >= threshold)) return "bg-amber-50 text-amber-700";
  return "bg-green-50 text-green-700";
}

// Eval v2 dimensions rendered per result. E-0 populates `response`; E-1 adds the
// durable `trajectory` + `tool_call` scorers; E-2 `side_effect`; E-5 `member_path`;
// E-4 `filter` + `injection`. These keys MUST match the backend `dimension_scores`
// keys exactly (judge.py / routers/playground.py) — a near-miss key renders a
// permanent "—" for a dimension that IS being scored.
//
// A "—" is deliberately NOT a zero: it means the dimension was not scored for this
// item (present-dims-only). A correctly-filtered webhook event shows `filter: 1.00`
// and "—" for every action dimension, because nothing ran — which is the pass.
const EVAL_DIMENSIONS = ["response", "trajectory", "tool_call", "side_effect", "filter", "injection", "member_path"] as const;

function DimensionScores({ scores, threshold }: { scores: Record<string, number> | null; threshold: number }) {
  return (
    <div className="flex flex-wrap gap-1">
      {EVAL_DIMENSIONS.map((dim) => {
        const v = scores?.[dim];
        const has = typeof v === "number";
        return (
          <span
            key={dim}
            data-testid={`dim-${dim}`}
            title={dim}
            className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-medium ${
              has ? scoreColor(v as number, threshold) : "text-slate-300"
            }`}
          >
            {dim.replace("_", " ")}: {has ? (v as number).toFixed(2) : "—"}
          </span>
        );
      })}
    </div>
  );
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
  // Gate the "mark passed" action on the run's OWN threshold. This offered to mark a
  // version eval-passed at >= 0.7 regardless of the run's actual gate, so on a
  // 0.9-threshold dataset the button appeared for a 0.85 run and the backend then
  // refused the publish — the UI promising something the platform would not honour.
  const canMarkPassed =
    run.status === "completed" &&
    run.overall_score != null &&
    run.overall_score >= run.pass_threshold &&
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
                  "Dimensions",
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
                  threshold={run.pass_threshold}
                  mode={run.mode}
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
  mode,
  expanded,
  onToggle,
  onViewTrace,
  threshold,
}: {
  result: EvalRunResult;
  /** The parent run's resolved publish threshold — every verdict in this row is
   *  rendered against it, never a local literal. */
  threshold: number;
  // The eval run's EXPLICIT interpretation mode. It decides which evidence blocks
  // render — never sniffed from which keys the row carries, because a scheduled
  // `job_spec` and a webhook synthetic event ride the SAME `trigger_payload` column
  // and would otherwise be labelled identically.
  mode: DatasetMode;
  expanded: boolean;
  onToggle: () => void;
  onViewTrace: () => void;
}) {
  // Composite == the overall result score. Reactive: composite == judge_score
  // (dimension_scores = {response: composite}); prefer an explicit composite
  // when the backend supplies one.
  const composite = r.composite ?? r.judge_score;
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
            className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${scoreColor(composite, threshold)}`}
          >
            {composite != null ? composite.toFixed(2) : "—"}
          </span>
        </td>
        <td className="px-3 py-3">
          <DimensionScores scores={r.dimension_scores} threshold={threshold} />
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
          <td colSpan={9} className="px-6 py-4">
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
              {/* Eval v2 E-4 — the FILTER VERDICT: the decision the real filter made
                  for this synthetic event, vs what the item expected. First, because
                  for a webhook agent it is the first-class result: a correctly-filtered
                  event scores on this alone and runs nothing. */}
              {mode === "webhook" && (
                <FilterVerdict
                  matched={r.matched ?? null}
                  detail={r.eval_detail ?? null}
                />
              )}
              {/* Eval v2 E-3/E-4 — WHAT the eval actually fired. `trigger_payload` is
                  the row's own record of what was fed to the run (written by the
                  eval-runner, present on fail-closed rows too); `detail.job_spec` is
                  the scheduled score door's echo of the authored spec, read for a row
                  recorded before that column was written. The LABEL comes from the
                  run's explicit mode — the same column carries a job spec for a
                  scheduled item and a synthetic event for a webhook one. Renders
                  nothing for the families that fire neither. */}
              <FiredPayloadEvidence
                mode={mode}
                payload={r.trigger_payload ?? r.eval_detail?.job_spec ?? null}
              />
              {/* Eval v2 E-4 — the injection probe: ASR and utility SIDE BY SIDE. */}
              {mode === "webhook" && (
                <InjectionEvidence detail={r.eval_detail ?? null} />
              )}
              {r.eval_detail && isWorkflowDetail(r.eval_detail) ? (
                <WorkflowEvidence detail={r.eval_detail} threshold={threshold} runId={r.run_id ?? null} />
              ) : r.eval_detail ? (
                <DurableEvidence detail={r.eval_detail} runId={r.run_id ?? null} />
              ) : null}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Scheduled (E-3) / webhook (E-4) evidence: WHAT the eval actually fired.
//
// Both families record it on the SAME `trigger_payload` column, because both feed it
// to the run as `input_payload` in the identical production shape — a job spec for a
// schedule (`trigger_type='schedule'`), a synthetic event for a webhook
// (`trigger_type='webhook'`). So this block answers "is this what the nightly job / a
// real delivery actually runs?" for both.
//
// The LABEL is keyed off the run's explicit `mode`, never off the payload's shape:
// the two are indistinguishable as JSON, and calling a synthetic event a "Job spec"
// (which is what happened before E-4 wired the discriminator through) is a quiet lie
// in the one panel whose whole job is to say what was fired.
//
// The side-effect evidence ("what would have been sent") is the E-2 panel below,
// reused as-is: the payload that went IN, the recorded calls that would have come OUT.
// ---------------------------------------------------------------------------
function FiredPayloadEvidence({
  mode,
  payload,
}: {
  mode: DatasetMode;
  payload: Record<string, unknown> | null;
}) {
  if (!payload || Object.keys(payload).length === 0) return null;
  if (mode !== "scheduled" && mode !== "webhook") return null;
  const isWebhook = mode === "webhook";
  return (
    <div data-testid={isWebhook ? "synthetic-event-evidence" : "job-spec-evidence"}>
      <p className="font-semibold text-slate-600 mb-1">
        {isWebhook ? "Synthetic event (trigger_payload)" : "Job spec"}
        <span className="ml-2 text-[10px] font-normal text-slate-400 uppercase tracking-wide">
          {isWebhook ? "fired at the real webhook filter" : "fed as input_payload"}
        </span>
      </p>
      <pre className="text-[11px] font-mono text-slate-700 whitespace-pre-wrap bg-white rounded p-2 border border-slate-100 overflow-x-auto">
        {JSON.stringify(payload, null, 2)}
      </pre>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Webhook (Eval v2 E-4) FILTER VERDICT: the decision the REAL filter made for this
// synthetic event, vs what the item expected.
//
// This is the first-class evidence for a webhook agent, because its first job is to
// NOT run on events it should filter. A correctly-filtered event is a PASS with no run
// at all — so this block has to make "nothing ran, and that was right" legible, or a
// human reads an empty row as a broken eval.
//
// `matched` is `eval_run_results.matched` — a column that existed since E-0 with
// NEITHER a writer nor a reader. The eval-runner's webhook branch writes it; this is
// the reader.
// ---------------------------------------------------------------------------
function FilterVerdict({
  matched,
  detail,
}: {
  matched: boolean | null;
  detail: EvalDetail | null;
}) {
  const fd = detail?.filter_detail ?? null;
  const expected = fd?.expected_match ?? null;
  const realReason = detail?.filter_reason ?? fd?.filter_reason ?? null;
  const expectedReason = fd?.expected_filter_reason ?? null;
  // A row that fail-closed BEFORE firing has no decision at all. Say so — "no decision"
  // and "filtered" are completely different outcomes and must never render alike.
  if (matched === null || matched === undefined) {
    return (
      <div data-testid="filter-verdict">
        <p className="font-semibold text-slate-600 mb-1">Filter verdict</p>
        <p className="text-[11px] text-amber-700 bg-amber-50 rounded p-2 border border-amber-100">
          No filter decision was recorded — the item failed closed before the event was
          fired, so the filter never ran. This is not a "filtered" result.
        </p>
      </div>
    );
  }
  const correct = expected === null ? null : matched === expected;
  return (
    <div data-testid="filter-verdict">
      <p className="font-semibold text-slate-600 mb-1">Filter verdict</p>
      <div className="flex flex-wrap items-center gap-2 mb-1">
        <span
          data-testid="filter-decision"
          className={`inline-block px-2 py-0.5 rounded text-[10px] font-medium ${
            matched ? "bg-blue-50 text-blue-700" : "bg-slate-100 text-slate-600"
          }`}
        >
          real filter: {matched ? "matched — the agent ran" : "filtered — nothing ran"}
        </span>
        {expected !== null && (
          <span className="text-[10px] text-slate-500">
            expected: {expected ? "match" : "filtered"}
          </span>
        )}
        {correct !== null && (
          <span
            className={`inline-block px-2 py-0.5 rounded text-[10px] font-medium ${
              correct ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700"
            }`}
          >
            {correct ? "correct decision" : "FILTER ERROR"}
          </span>
        )}
      </div>
      {realReason && (
        <p className="text-[11px] text-slate-600">
          reason: <span className="font-mono">{realReason}</span>
        </p>
      )}
      {expectedReason && (
        <p className="text-[11px] text-slate-600">
          expected reason to contain:{" "}
          <span className="font-mono">{expectedReason}</span>
          {fd?.reason_matched === false && (
            <span className="ml-2 text-red-700 font-medium">
              — not present: the event was filtered for a different reason, so the rule
              under test never ran
            </span>
          )}
        </p>
      )}
      {!matched && (
        <p className="text-[11px] text-slate-400 mt-1">
          No run exists for this item — that is the pass. The whole job of a filter is
          to not run.
        </p>
      )}
      {detail?.veto && detail.veto.length > 0 && (
        <p
          data-testid="filter-veto"
          className="text-[11px] text-red-700 bg-red-50 rounded p-2 border border-red-100 mt-1"
        >
          Composite forced to 0 — {detail.veto.join(", ")}. These are exact facts a
          weighted average may not out-vote.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Webhook (Eval v2 E-4) INJECTION evidence: ASR and utility, SIDE BY SIDE.
//
// The side-by-side is the whole point, not a layout choice. An agent that refuses
// every event drives ASR to 0 and would read as a flawless defense; only its utility
// exposes it as useless. Rendering ASR alone would let a human sign off on exactly the
// agent this probe exists to catch (the AgentDojo/InjecAgent framing, made visible).
// ---------------------------------------------------------------------------
function InjectionEvidence({ detail }: { detail: EvalDetail | null }) {
  if (!detail) return null;
  if (detail.injection_not_exercised) {
    return (
      <div data-testid="injection-evidence">
        <p className="font-semibold text-slate-600 mb-1">Injection probe</p>
        <p className="text-[11px] text-slate-500 bg-slate-50 rounded p-2 border border-slate-100">
          Not exercised — the event was filtered, so the agent never saw the payload.
          There is no injection score for this item: the filter already blocked it, and
          that is fully credited by the filter dimension.
        </p>
      </div>
    );
  }
  const id = detail.injection_detail ?? null;
  const asr = detail.asr ?? id?.asr ?? null;
  const utility = detail.utility ?? id?.utility ?? null;
  const forbidden = detail.forbidden_called ?? id?.forbidden_called ?? null;
  if (asr === null && utility === null && !id) return null;
  return (
    <div data-testid="injection-evidence">
      <p className="font-semibold text-slate-600 mb-1">Injection probe</p>
      <div className="grid grid-cols-2 gap-2 mb-1">
        <div
          data-testid="injection-asr"
          className={`rounded p-2 border ${
            asr === 0 ? "bg-green-50 border-green-100" : "bg-red-50 border-red-100"
          }`}
        >
          <p className="text-[10px] uppercase tracking-wide text-slate-500">
            ASR (attack success)
          </p>
          <p
            className={`text-sm font-semibold ${
              asr === 0 ? "text-green-700" : "text-red-700"
            }`}
          >
            {asr === null ? "—" : asr.toFixed(2)}
          </p>
          <p className="text-[10px] text-slate-500">
            {asr === 0
              ? "no forbidden tool fired"
              : "an injected instruction really fired a forbidden tool"}
          </p>
        </div>
        <div
          data-testid="injection-utility"
          className="rounded p-2 border bg-white border-slate-100"
        >
          <p className="text-[10px] uppercase tracking-wide text-slate-500">
            Utility (task still done)
          </p>
          <p className="text-sm font-semibold text-slate-700">
            {utility === null ? "—" : utility.toFixed(2)}
          </p>
          <p className="text-[10px] text-slate-500">
            a defense that refuses everything scores ASR 0 but tanks this
          </p>
        </div>
      </div>
      {forbidden && forbidden.length > 0 && (
        <p className="text-[11px] text-red-700">
          forbidden tools actually called:{" "}
          <span className="font-mono">{forbidden.join(", ")}</span>
        </p>
      )}
      {id?.must_not_call && id.must_not_call.length > 0 && (
        <p className="text-[11px] text-slate-500">
          must not call: <span className="font-mono">{id.must_not_call.join(", ")}</span>
          {id.must_refuse ? " · must refuse: yes" : ""}
          {id.must_refuse && id.refused !== null && id.refused !== undefined
            ? ` · refused: ${id.refused ? "yes" : "no"}`
            : ""}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Durable (Eval v2 E-1) per-item evidence: trajectory dimensions render above;
// this block shows the tool-diff panel, the expected-vs-actual step diff, the
// HITL approvals, and a deep-link into the real run tree (StepTracker steps).
// ---------------------------------------------------------------------------
function DurableEvidence({
  detail,
  runId,
}: {
  detail: EvalDetail;
  runId: string | null;
}) {
  const expectedSteps = detail.expected_trajectory?.steps ?? [];
  const actualSteps = detail.actual_trajectory ?? [];
  const toolDiffs = detail.tool_diffs ?? [];
  const approvals = detail.approvals ?? [];
  const recorded = detail.recorded_side_effects ?? [];
  const sideEffectDiffs = detail.side_effect_detail?.side_effect_diffs ?? [];

  return (
    <div data-testid="durable-evidence" className="space-y-3 pt-1">
      {/* Expected vs actual step diff */}
      {(expectedSteps.length > 0 || actualSteps.length > 0) && (
        <div>
          <p className="font-semibold text-slate-600 mb-1">
            Trajectory (expected vs actual)
            {detail.expected_trajectory?.match_mode && (
              <span className="ml-2 text-[10px] font-normal text-slate-400 uppercase tracking-wide">
                {detail.expected_trajectory.match_mode}
              </span>
            )}
          </p>
          <div className="grid grid-cols-2 gap-2">
            <div className="bg-white rounded p-2 border border-slate-100">
              <p className="text-[10px] font-semibold text-slate-400 uppercase mb-1">Expected</p>
              {expectedSteps.length === 0 ? (
                <p className="text-slate-300">—</p>
              ) : (
                <ol className="space-y-0.5">
                  {expectedSteps.map((s, i) => (
                    <li key={i} className="text-slate-700 flex items-center gap-1">
                      <span className="text-slate-400">{i + 1}.</span>
                      <span className="font-mono">{s.tool}</span>
                      {s.expect_approval && (
                        <span className="text-amber-500 text-[10px]">⚑ approval</span>
                      )}
                    </li>
                  ))}
                </ol>
              )}
            </div>
            <div
              data-testid="actual-trajectory"
              className="bg-white rounded p-2 border border-slate-100"
            >
              <p className="text-[10px] font-semibold text-slate-400 uppercase mb-1">Actual</p>
              {actualSteps.length === 0 ? (
                <p className="text-slate-300">—</p>
              ) : (
                <ol className="space-y-0.5">
                  {actualSteps.map((s, i) => (
                    <li key={i} className="text-slate-700 flex items-center gap-1">
                      <span className="text-slate-400">{s.step_number}.</span>
                      <span className="font-mono">{s.tool ?? s.name}</span>
                      {s.status === "awaiting_approval" && (
                        <span className="text-amber-500 text-[10px]">⚑ parked</span>
                      )}
                    </li>
                  ))}
                </ol>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Tool-diff panel */}
      {toolDiffs.length > 0 && (
        <div data-testid="tool-diff-panel">
          <p className="font-semibold text-slate-600 mb-1">Tool-call args diff</p>
          <div className="overflow-x-auto">
            <table className="w-full text-[11px]">
              <thead>
                <tr className="text-slate-400 text-left">
                  <th className="py-1 pr-3 font-medium">Step</th>
                  <th className="py-1 pr-3 font-medium">Expected args</th>
                  <th className="py-1 pr-3 font-medium">Actual args</th>
                  <th className="py-1 font-medium">Match</th>
                </tr>
              </thead>
              <tbody>
                {toolDiffs.map((d, i) => (
                  <tr key={i} className="align-top border-t border-slate-100">
                    <td className="py-1 pr-3 font-mono text-slate-700">{d.step}</td>
                    <td className="py-1 pr-3 font-mono text-slate-500 whitespace-pre-wrap">
                      {d.expected_args ? JSON.stringify(d.expected_args) : "—"}
                    </td>
                    <td className="py-1 pr-3 font-mono text-slate-500 whitespace-pre-wrap">
                      {d.actual_args ? JSON.stringify(d.actual_args) : "—"}
                    </td>
                    <td className="py-1">
                      {d.arg_match ? (
                        <CheckCircle size={12} className="text-green-500" />
                      ) : (
                        <XCircle size={12} className="text-red-500" />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* HITL approvals */}
      {approvals.length > 0 && (
        <div data-testid="approvals-panel">
          <p className="font-semibold text-slate-600 mb-1">HITL approvals</p>
          <ul className="space-y-0.5">
            {approvals.map((a, i) => (
              <li key={i} className="flex items-center gap-2 text-slate-600">
                <span className="font-mono">{a.step}</span>
                <span
                  className={`text-[10px] px-1.5 py-0.5 rounded ${
                    a.parked ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700"
                  }`}
                >
                  {a.parked ? "parked" : "did not park"}
                </span>
                <span
                  className={`text-[10px] px-1.5 py-0.5 rounded ${
                    a.args_matched ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700"
                  }`}
                >
                  args {a.args_matched ? "matched" : "mismatch"}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Side effects recorded instead of delivered (Eval v2 E-2) */}
      <SideEffectEvidence recorded={recorded} diffs={sideEffectDiffs} />

      {/* Deep-link into the real run tree (StepTracker steps) */}
      {runId && <RunStepsDeepLink runId={runId} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Side-effect (Eval v2 E-2) evidence. Under `eval_mode=record` the governed-tool
// delivery seam records a side-effecting call and answers it with a mock INSTEAD of
// invoking the real downstream — so this panel is "the email that would have been
// sent": what the agent tried to do, with what args, and what it got back instead.
//
// Args are PII-tokenized for display (E-2 gap ledger / OQ-3 policy) — the raw args
// are what `score_side_effects` asserts server-side, never what a reviewer reads.
//
// Renders only when the item asserted side effects OR the run recorded some; a
// normal `live` item has neither and this collapses away.
// ---------------------------------------------------------------------------
function SideEffectEvidence({
  recorded,
  diffs,
}: {
  recorded: RecordedSideEffect[];
  diffs: SideEffectDiff[];
}) {
  if (recorded.length === 0 && diffs.length === 0) return null;

  return (
    <div data-testid="side-effect-evidence" className="space-y-2">
      <p className="font-semibold text-slate-600 mb-1">
        Side effects recorded, not delivered
        <span className="ml-2 text-[10px] font-normal text-slate-400 uppercase tracking-wide">
          eval_mode=record
        </span>
      </p>

      {/* Per-assertion outcomes (the side_effect dimension's evidence) */}
      {diffs.length > 0 && (
        <ul data-testid="side-effect-assertions" className="space-y-0.5">
          {diffs.map((d, i) => (
            <li key={i} className="flex flex-wrap items-center gap-2 text-slate-600">
              <span className="font-mono">{d.tool}</span>
              <span className="text-[10px] text-slate-400">
                {d.occurs ?? "exactly"}
                {d.occurs !== "never" && ` ${d.count ?? 1}`} · matched {d.matched}
              </span>
              <span
                className={`text-[10px] px-1.5 py-0.5 rounded ${
                  d.satisfied ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700"
                }`}
              >
                {d.satisfied ? "satisfied" : "violated"}
              </span>
              {d.args_match && Object.keys(d.args_match).length > 0 && (
                <span className="font-mono text-[10px] text-slate-400">
                  args_match {tokenizeArgsForDisplay(d.args_match)}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      {/* The intercepted calls themselves */}
      {recorded.length === 0 ? (
        <p data-testid="no-recorded-side-effects" className="text-slate-400">
          No side effects were recorded — the run never attempted a write.
        </p>
      ) : (
        <div className="space-y-1.5">
          {recorded.map((r, i) => (
            <div
              key={i}
              data-testid={`recorded-side-effect-${i}`}
              className="bg-white rounded p-2 border border-slate-100"
            >
              <div className="flex items-center gap-2 mb-1">
                <span className="font-mono text-slate-700">{r.tool}</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-50 text-amber-700">
                  not delivered
                </span>
              </div>
              <p className="text-slate-500">
                <span className="text-slate-400">args </span>
                <span className="font-mono">{tokenizeArgsForDisplay(r.args)}</span>
              </p>
              {r.would_have_invoked && (
                <p className="text-slate-500">
                  <span className="text-slate-400">would have invoked </span>
                  <span className="font-mono">{r.would_have_invoked}</span>
                </p>
              )}
              {r.mocked_response && (
                <p className="text-slate-500">
                  <span className="text-slate-400">returned instead </span>
                  <span className="font-mono">{JSON.stringify(r.mocked_response)}</span>
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Workflow (Eval v2 E-5) per-item evidence: the member-path dimension renders in
// the row above; this block shows the expected-vs-actual member path, the
// member_diff (missing/extra/order), per-member rubric scores (each member's LLM
// rubric score + the judge's reason + a had_steps flag when the child had no
// run_steps to zoom into), and a deep-link into the workflow run tree via the
// parent run_id (reuses the durable RunStepsDeepLink).
// ---------------------------------------------------------------------------
function WorkflowEvidence({
  detail,
  threshold,
  runId,
}: {
  detail: EvalDetail;
  threshold: number;
  runId: string | null;
}) {
  const expected = detail.expected_member_path ?? [];
  const actual = detail.actual_member_path ?? [];
  const diff = detail.member_diff ?? null;
  const perMember = detail.per_member ?? [];

  return (
    <div data-testid="workflow-evidence" className="space-y-3 pt-1">
      {/* Expected vs actual member path */}
      {(expected.length > 0 || actual.length > 0) && (
        <div>
          <p className="font-semibold text-slate-600 mb-1">
            Member path (expected vs actual)
            {diff?.match_mode && (
              <span className="ml-2 text-[10px] font-normal text-slate-400 uppercase tracking-wide">
                {diff.match_mode}
              </span>
            )}
          </p>
          <div className="grid grid-cols-2 gap-2">
            <div className="bg-white rounded p-2 border border-slate-100">
              <p className="text-[10px] font-semibold text-slate-400 uppercase mb-1">Expected</p>
              {expected.length === 0 ? (
                <p className="text-slate-300">—</p>
              ) : (
                <ol className="space-y-0.5">
                  {expected.map((m, i) => (
                    <li key={i} className="text-slate-700 flex items-center gap-1">
                      <span className="text-slate-400">{i + 1}.</span>
                      <span className="font-mono">{m}</span>
                    </li>
                  ))}
                </ol>
              )}
            </div>
            <div
              data-testid="actual-member-path"
              className="bg-white rounded p-2 border border-slate-100"
            >
              <p className="text-[10px] font-semibold text-slate-400 uppercase mb-1">Actual</p>
              {actual.length === 0 ? (
                <p className="text-slate-300">—</p>
              ) : (
                <ol className="space-y-0.5">
                  {actual.map((m, i) => {
                    const isExtra = diff?.extra?.includes(m);
                    return (
                      <li key={i} className="text-slate-700 flex items-center gap-1">
                        <span className="text-slate-400">{i + 1}.</span>
                        <span className="font-mono">{m}</span>
                        {isExtra && (
                          <span className="text-amber-500 text-[10px]">+ extra</span>
                        )}
                      </li>
                    );
                  })}
                </ol>
              )}
            </div>
          </div>

          {/* member_diff summary badges */}
          {diff && (
            <div data-testid="member-diff" className="flex flex-wrap items-center gap-1.5 mt-2">
              <span
                className={`text-[10px] px-1.5 py-0.5 rounded ${
                  diff.order_ok ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700"
                }`}
              >
                order {diff.order_ok ? "ok" : "wrong"}
              </span>
              {(diff.missing ?? []).length > 0 && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-50 text-red-700">
                  missing: {(diff.missing ?? []).join(", ")}
                </span>
              )}
              {(diff.extra ?? []).length > 0 && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-50 text-amber-700">
                  extra: {(diff.extra ?? []).join(", ")}
                </span>
              )}
            </div>
          )}
        </div>
      )}

      {/* Per-member rubric scores + zoom */}
      {perMember.length > 0 && (
        <div data-testid="per-member-panel">
          <p className="font-semibold text-slate-600 mb-1">Per-member evidence</p>
          <div className="space-y-2">
            {perMember.map((pm, i) => (
              <div
                key={i}
                data-testid={`per-member-evidence-${i}`}
                className="bg-white rounded p-2 border border-slate-100"
              >
                <div className="flex items-center gap-2 mb-1">
                  <span className="font-mono text-slate-700">{pm.member}</span>
                  {typeof pm.score === "number" && (
                    <span
                      className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-medium ${scoreColor(pm.score, threshold)}`}
                    >
                      {pm.score.toFixed(2)}
                    </span>
                  )}
                </div>
                {pm.rubric && (
                  <p className="text-slate-500 italic mb-1">“{pm.rubric}”</p>
                )}
                {pm.reason && (
                  <p className="text-slate-500 whitespace-pre-wrap mb-1">{pm.reason}</p>
                )}
                {pm.had_steps === false && (
                  <p className="text-[10px] text-amber-500">
                    no run_steps to zoom into — scored on the member's response only
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Deep-link into the workflow run tree (parent run_id) */}
      {runId && <RunStepsDeepLink runId={runId} />}
    </div>
  );
}

// Resolving deep-link: on demand fetches the real `run_steps` for the durable
// run (`GET /playground/runs/{id}/steps`) — the same substrate StepTracker and
// the eval-runner read — and renders them read-only.
function RunStepsDeepLink({ runId }: { runId: string }) {
  const [open, setOpen] = useState(false);
  const { data: steps, isLoading } = useQuery({
    queryKey: ["run-steps", runId],
    queryFn: () => listRunSteps(runId),
    enabled: open,
  });

  return (
    <div>
      <button
        onClick={() => setOpen((v) => !v)}
        data-testid="run-steps-deeplink"
        className="inline-flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 font-medium"
      >
        <Eye size={12} />
        {open ? "Hide run tree" : "View run tree"}
        <span className="font-mono text-slate-400">({runId.slice(0, 8)}…)</span>
      </button>
      {open && (
        <div className="mt-2 bg-white rounded p-2 border border-slate-100">
          {isLoading && (
            <p className="text-slate-400 flex items-center gap-1">
              <Loader2 size={12} className="animate-spin" />
              Loading run steps…
            </p>
          )}
          {steps && steps.length === 0 && (
            <p className="text-slate-400">No run steps recorded.</p>
          )}
          {steps && steps.length > 0 && (
            <ol className="space-y-0.5">
              {steps.map((s: StepUpdateEvent) => (
                <li key={s.step_number} className="text-slate-700 flex items-center gap-1">
                  <span className="text-slate-400">{s.step_number}.</span>
                  <span className="font-mono flex-1">{s.step_name}</span>
                  <span className="text-[10px] text-slate-400">{s.status}</span>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </div>
  );
}
