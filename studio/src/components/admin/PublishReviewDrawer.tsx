import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle, Ban, Check, Code2, Database, FlaskConical, Globe, KeyRound,
  Loader2, ShieldAlert, X,
} from "lucide-react";
import { useState } from "react";
import { getPublishReview, type PublishReviewTool } from "../../api/registryApi";
import { scoreColor, thresholdLabel } from "../../lib/evalVerdict";

/**
 * The publish reviewer surface — Decision 47 step D, gap G-R3-11.
 *
 * WHY THIS EXISTS
 * ---------------
 * Every other gate in the authorization stack is machine-enforced and testable: OPA,
 * the HITL router, the eval gate, the cross-team 422. Approve is the ONLY place a human
 * decides, and it is the last one before an artifact becomes org-wide. Until this drawer
 * the human was shown an asset name, a submitter, a timestamp, a percentage and a colour.
 * `grep -c "tool" AdminPublishRequestsPage.tsx` was **0**.
 *
 * THE GATE — option B + C (docs/design/publish-review-surface.md §7)
 * -----------------------------------------------------------------
 * Approve lives IN HERE, not on the queue row. That makes "the reviewer was shown the
 * screen" structurally true instead of hoped-for; leaving Approve on the row would have
 * made the drawer optional, which reverts to today's behaviour for anyone in a hurry.
 * The extra typed acknowledgement fires ONLY when the cascade is non-empty — reserved for
 * the case that actually escalates scope, so it does not become a click-through people
 * learn to dismiss.
 *
 * NOTHING IS RE-DERIVED HERE. `disposition` and the cascade come from the server's
 * `plan_tool_cascade`, the same producer the submit guard and the approve action use. A
 * client that computed "will this publish?" itself would eventually disagree with what
 * approve actually does, and the reviewer would be the last to find out.
 */

const RISK_CHIP: Record<string, string> = {
  critical: "bg-red-200 text-red-900",
  high: "bg-red-100 text-red-700",
  medium: "bg-amber-50 text-amber-700",
  low: "bg-blue-50 text-blue-600",
};

const DISPOSITION_CHIP: Record<PublishReviewTool["disposition"], string> = {
  will_publish: "bg-green-100 text-green-800",
  blocked: "bg-red-100 text-red-700",
  already_published: "bg-slate-100 text-slate-500",
};

const DISPOSITION_LABEL: Record<PublishReviewTool["disposition"], string> = {
  will_publish: "WILL PUBLISH",
  blocked: "blocked — other team",
  already_published: "already published",
};

interface Props {
  requestId: string;
  onClose: () => void;
  onApprove: () => void;
  approving: boolean;
}

export default function PublishReviewDrawer({ requestId, onClose, onApprove, approving }: Props) {
  const [ack, setAck] = useState(false);
  const [showCode, setShowCode] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["publish-review", requestId],
    queryFn: () => getPublishReview(requestId),
  });

  const cascadeCount = data?.cascade?.will_publish.length ?? 0;
  // Option C: the acknowledgement is required ONLY when approving widens scope beyond
  // the agent itself. A blanket confirm on every approval is the click-through.
  const needsAck = cascadeCount > 0;
  const canApprove = data?.status === "pending_review" && (!needsAck || ack);

  return (
    <div className="fixed inset-0 z-50 flex justify-end" data-testid="publish-review-drawer">
      <div className="absolute inset-0 bg-black/20" onClick={onClose} />
      <div className="relative w-[720px] max-w-full bg-white shadow-xl border-l border-slate-200 flex flex-col h-full overflow-hidden">
        {/* Header */}
        <div className="flex items-start justify-between px-5 py-3 border-b border-slate-100 shrink-0">
          <div>
            <h3 className="text-sm font-semibold text-slate-900">
              Publish review
              {data?.agent?.name ? ` — ${data.agent.name}` : ""}
              {data?.version?.version_number != null ? ` v${data.version.version_number}` : ""}
            </h3>
            {data?.agent && (
              <p className="text-xs text-slate-500 mt-0.5">
                {data.agent.team} · submitted by {data.submitted_by} ·{" "}
                {/* `daemon` skips OPA's identity floor. Different risk decision entirely. */}
                <span
                  className={
                    data.agent.agent_class === "daemon"
                      ? "font-semibold text-amber-700"
                      : "text-slate-500"
                  }
                  data-testid="review-agent-class"
                  title={
                    data.agent.agent_class === "daemon"
                      ? "A daemon agent is EXEMPT from OPA's identity floor — it can act with no human attached to the run."
                      : undefined
                  }
                >
                  {data.agent.agent_class}
                </span>{" "}
                · {data.agent.execution_shape} · memory{" "}
                {data.agent.memory_enabled ? "ON" : "off"}
              </p>
            )}
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600" aria-label="Close review">
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
          {isLoading && (
            <div className="flex items-center justify-center py-20 text-slate-400">
              <Loader2 size={18} className="animate-spin mr-2" />
              Loading review…
            </div>
          )}

          {error && (
            <div className="rounded-lg bg-red-50 border border-red-200 p-3 text-sm text-red-700">
              Failed to load the review payload: {String(error)}
            </div>
          )}

          {/* D-3 — a workflow says so instead of rendering an empty tool list, which
              would read as "this workflow has no tools". */}
          {data && !data.review_supported && (
            <div
              className="rounded-lg bg-amber-50 border border-amber-200 p-3 text-sm text-amber-800"
              data-testid="review-unsupported"
            >
              <AlertTriangle size={14} className="inline mr-1.5 -mt-0.5" />
              {data.unsupported_reason}
            </div>
          )}

          {data?.review_supported && (
            <>
              {/* Eval */}
              <section>
                <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
                  Evaluation
                </h4>
                {data.eval?.score != null ? (
                  <div className="flex items-center gap-2">
                    <span
                      className={`badge text-xs ${scoreColor(data.eval.score, data.eval.pass_threshold)}`}
                      data-testid="review-eval-score"
                    >
                      <FlaskConical size={10} className="mr-0.5 inline" />
                      {Math.round(data.eval.score * 100)}%
                    </span>
                    <span className="text-xs text-slate-500">
                      {thresholdLabel(data.eval.score, data.eval.pass_threshold)}
                    </span>
                    {data.eval.source === "agent_latest" && (
                      <span className="badge bg-amber-100 text-amber-700 text-[10px]">
                        from a different version
                      </span>
                    )}
                  </div>
                ) : (
                  <span className="badge bg-amber-50 text-amber-600 text-xs">No eval</span>
                )}
              </section>

              {/* Version — for an sdk agent the image is USER-BUILT. */}
              {data.version && (
                <section>
                  <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
                    Version
                  </h4>
                  <dl className="text-xs text-slate-600 space-y-1">
                    <div className="flex gap-2">
                      <dt className="w-24 text-slate-400">image</dt>
                      <dd className="font-mono break-all" data-testid="review-image-tag">
                        {data.version.image_tag ?? "—"}
                      </dd>
                    </div>
                    {data.version.git_sha && (
                      <div className="flex gap-2">
                        <dt className="w-24 text-slate-400">git</dt>
                        <dd className="font-mono">
                          {data.version.git_sha.slice(0, 12)}
                          {data.version.git_branch ? ` (${data.version.git_branch})` : ""}
                        </dd>
                      </div>
                    )}
                    <div className="flex gap-2">
                      <dt className="w-24 text-slate-400">gates</dt>
                      <dd>
                        eval {data.version.eval_passed ? "✓" : "✗"} · adversarial{" "}
                        {data.version.adversarial_eval_passed ? "✓" : "✗"}
                      </dd>
                    </div>
                  </dl>
                </section>
              )}

              {/* Instructions — the prompt is what the agent will actually do. */}
              {data.agent?.instructions && (
                <section>
                  <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
                    Instructions
                  </h4>
                  <pre
                    className="text-xs bg-slate-50 border border-slate-200 rounded p-3 whitespace-pre-wrap max-h-56 overflow-y-auto text-slate-700"
                    data-testid="review-instructions"
                  >
                    {data.agent.instructions}
                  </pre>
                </section>
              )}

              {/* Knowledge bases — what corpus it can quote from. */}
              {(data.knowledge_bases?.length ?? 0) > 0 && (
                <section>
                  <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
                    Knowledge
                  </h4>
                  <ul className="text-xs text-slate-600 space-y-1">
                    {data.knowledge_bases!.map((kb) => (
                      <li key={kb.id}>
                        <Database size={11} className="inline mr-1 -mt-0.5 text-slate-400" />
                        {kb.name} <span className="text-slate-400">({kb.team})</span>
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              {/* Tools */}
              <section>
                <div className="flex items-baseline justify-between mb-2">
                  <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                    Tools ({data.tools?.length ?? 0})
                  </h4>
                  {cascadeCount > 0 && (
                    <span
                      className="text-xs font-semibold text-green-700"
                      data-testid="review-cascade-count"
                    >
                      {cascadeCount} will be PUBLISHED by this approval
                    </span>
                  )}
                </div>

                {(data.tools?.length ?? 0) === 0 ? (
                  <p className="text-xs text-slate-400">This agent binds no tools.</p>
                ) : (
                  <ul className="space-y-2" data-testid="review-tool-list">
                    {data.tools!.map((t) => (
                      <li
                        key={t.id}
                        className="border border-slate-200 rounded-lg p-3"
                        data-testid={`review-tool-${t.name}`}
                      >
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-sm font-medium text-slate-800">{t.name}</span>
                          <span className={`badge text-[10px] ${RISK_CHIP[(t.risk_level ?? "").toLowerCase()] ?? "bg-slate-100 text-slate-600"}`}>
                            {t.risk_level ?? "—"}
                          </span>
                          <span className="text-xs text-slate-400">{t.owner_team ?? "—"}</span>
                          <span
                            className={`badge text-[10px] ml-auto ${DISPOSITION_CHIP[t.disposition]}`}
                            data-testid={`review-disposition-${t.name}`}
                          >
                            {DISPOSITION_LABEL[t.disposition]}
                          </span>
                        </div>

                        {/* WHERE THE DATA GOES. An external host here is the single
                            highest-signal field on this whole screen. */}
                        {t.http_url && (
                          <p className="text-xs font-mono text-slate-600 mt-1.5 break-all">
                            <Globe size={11} className="inline mr-1 -mt-0.5 text-slate-400" />
                            {t.http_method ?? "GET"} {t.http_url}
                          </p>
                        )}
                        {t.mcp_server_name && (
                          <p className="text-xs text-slate-600 mt-1.5">
                            via MCP server <span className="font-medium">{t.mcp_server_name}</span>
                            {t.mcp_tool_name ? ` → ${t.mcp_tool_name}` : ""}
                            <span className="text-slate-400"> · schema can drift upstream</span>
                          </p>
                        )}

                        <div className="flex items-center gap-3 mt-1.5 text-xs text-slate-500 flex-wrap">
                          {t.auth_config_name && (
                            <span data-testid={`review-cred-${t.name}`}>
                              <KeyRound size={11} className="inline mr-1 -mt-0.5 text-amber-600" />
                              cred: <span className="font-mono">{t.auth_config_name}</span>
                            </span>
                          )}
                          {t.side_effecting && (
                            <span className="text-amber-700">
                              <ShieldAlert size={11} className="inline mr-1 -mt-0.5" />
                              side-effecting
                            </span>
                          )}
                          <span>
                            PII de-anonymize: {t.pii_deanonymize_allowed ? "YES" : "no"}
                          </span>
                          {t.python_code && (
                            <button
                              onClick={() => setShowCode(showCode === t.id ? null : t.id)}
                              className="text-blue-600 hover:text-blue-800"
                              data-testid={`review-code-toggle-${t.name}`}
                            >
                              <Code2 size={11} className="inline mr-1 -mt-0.5" />
                              {showCode === t.id ? "hide code" : "show code"}
                            </button>
                          )}
                        </div>

                        {/* D-1 — in full. It is what is being approved. */}
                        {showCode === t.id && t.python_code && (
                          <pre
                            className="text-[11px] bg-slate-900 text-slate-100 rounded p-3 mt-2 overflow-x-auto max-h-64 overflow-y-auto"
                            data-testid={`review-code-${t.name}`}
                          >
                            {t.python_code}
                          </pre>
                        )}
                      </li>
                    ))}
                  </ul>
                )}

                {(data.cascade?.blocked.length ?? 0) > 0 && (
                  <div className="mt-3 rounded-lg bg-red-50 border border-red-200 p-3 text-xs text-red-700">
                    <Ban size={12} className="inline mr-1 -mt-0.5" />
                    These tools will NOT cascade — they belong to another team and stay
                    private:{" "}
                    {data.cascade!.blocked.map((b) => `${b.name} (${b.owner_team ?? "no owner"})`).join(", ")}
                  </div>
                )}
              </section>
            </>
          )}
        </div>

        {/* Footer — Approve lives HERE, not on the queue row (option B). */}
        <div className="border-t border-slate-100 px-5 py-3 shrink-0 space-y-2">
          {needsAck && (
            <label className="flex items-start gap-2 text-xs text-slate-700" data-testid="review-cascade-ack">
              <input
                type="checkbox"
                checked={ack}
                onChange={(e) => setAck(e.target.checked)}
                className="mt-0.5"
              />
              <span>
                I understand this also publishes {cascadeCount} tool
                {cascadeCount === 1 ? "" : "s"} org-wide:{" "}
                <span className="font-medium">{data!.cascade!.will_publish.join(", ")}</span>
              </span>
            </label>
          )}
          <div className="flex items-center justify-end gap-2">
            <button onClick={onClose} className="btn-secondary text-xs py-2">
              Close
            </button>
            <button
              onClick={onApprove}
              disabled={!canApprove || approving}
              className="btn-primary text-xs py-2 disabled:opacity-40 disabled:cursor-not-allowed"
              data-testid="review-approve"
            >
              {approving ? (
                <Loader2 size={12} className="animate-spin" />
              ) : (
                <>
                  <Check size={12} className="inline mr-1 -mt-0.5" />
                  Promote to Catalog
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
