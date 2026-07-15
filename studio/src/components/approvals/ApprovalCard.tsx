import { Loader2, ShieldAlert } from "lucide-react";

/**
 * The ONE presentational approval body (WS-1 M1). Every approval surface — the
 * playground HITL bar (`HitlPanel`), the sandbox chat side panel
 * (`ConversationApprovalPanel`), and the global Approvals Inbox
 * (`ApprovalsInboxPage`) — mounts this same card for the per-approval fields
 * (WHO / WHY / WHAT) and the Approve / Deny actions. A new approval field is
 * therefore added in exactly one place. Each surface keeps its own shell
 * (fixed bar / aside / page) and its own decide-endpoint wiring; the card is
 * purely presentational and decision-agnostic (it just calls onApprove/onDeny).
 */
export interface ApprovalCardData {
  /** The tool the paused call would run. */
  toolName: string;
  /** critical | high | medium | low — rendered as a badge. */
  riskLevel: string;
  /** WHAT — the (redacted) arguments the tool will run with. */
  args?: Record<string, unknown> | null;
  /** WHY — the LLM's stated reason (hidden when empty). */
  reasoning?: string | null;
  /** WHO — the requester + their team. */
  requestedBy?: string | null;
  requestedByTeam?: string | null;
  /**
   * WHO (daemon) — the derived principal label for a service-identity run, e.g.
   * "service:X on behalf of Y" (WS-2 T013). Shown in place of/alongside the
   * requester when present; null on interactive/user-delegated approvals.
   */
  principalDisplay?: string | null;
  /** Inbox context — the agent that raised the gate. */
  agentName?: string | null;
  /** Inbox context — the durable step the gate parked at. */
  stepName?: string | null;
  /** Inbox context — owning team. */
  team?: string | null;
  /** Inbox context — pre-formatted SLA-remaining label. */
  slaLabel?: string | null;
  /** Inbox context — ISO timestamp; shown localized. */
  createdAt?: string | null;
}

interface Props {
  data: ApprovalCardData;
  onApprove: () => void;
  onDeny: () => void;
  /** Disables both buttons + shows a spinner while a decision is in flight. */
  deciding?: boolean;
  approveLabel?: string;
  denyLabel?: string;
  /** Optional testid on the reasoning block (back-compat for existing specs). */
  reasoningTestId?: string;
  className?: string;
}

export default function ApprovalCard({
  data,
  onApprove,
  onDeny,
  deciding = false,
  approveLabel = "Approve",
  denyLabel = "Deny",
  reasoningTestId,
  className = "",
}: Props) {
  const hasArgs = !!data.args && Object.keys(data.args).length > 0;
  const hasMeta = !!(data.team || data.slaLabel || data.createdAt);

  return (
    <div data-testid="approval-card" className={className}>
      {/* Title: tool + risk (+ agent / step context on the inbox). */}
      <div className="flex flex-wrap items-center gap-2">
        <ShieldAlert size={16} className="text-amber-500 shrink-0" />
        {data.agentName && (
          <span className="font-mono text-sm font-semibold text-slate-800">
            {data.agentName}
          </span>
        )}
        <span className="font-mono text-sm font-medium text-slate-800">
          {data.toolName}
        </span>
        <span
          data-testid="approval-card-risk"
          className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-100 text-amber-700 uppercase"
        >
          {data.riskLevel}
        </span>
        {data.stepName && (
          <span className="text-xs text-slate-400">Step: {data.stepName}</span>
        )}
      </div>

      {/* WHO (daemon) — the service-identity principal for a daemon trigger-run. */}
      {data.principalDisplay && (
        <p
          data-testid="approval-card-principal"
          className="mt-1 text-[11px] text-slate-500"
        >
          Acting as{" "}
          <span className="font-medium text-slate-600">{data.principalDisplay}</span>
        </p>
      )}

      {/* WHO — who requested the call (interactive/user-delegated). */}
      {data.requestedBy && (
        <p className="mt-1 text-[11px] text-slate-500">
          Requested by{" "}
          <span className="font-medium text-slate-600">{data.requestedBy}</span>
          {data.requestedByTeam && ` · ${data.requestedByTeam}`}
        </p>
      )}

      {/* WHY — the LLM's stated reason (hidden when empty). */}
      {data.reasoning && (
        <p
          data-testid={reasoningTestId}
          className="mt-2 text-xs italic text-slate-600 border-l-2 border-amber-300 pl-2"
        >
          {data.reasoning}
        </p>
      )}

      {/* WHAT — the exact arguments the tool will run with. */}
      {hasArgs && (
        <pre className="mt-2 p-2 rounded bg-slate-50 text-xs text-slate-700 overflow-x-auto max-w-full max-h-24">
          {JSON.stringify(data.args, null, 2)}
        </pre>
      )}

      {/* Inbox meta — team / SLA / timestamp. */}
      {hasMeta && (
        <div className="flex flex-wrap items-center gap-3 mt-2 text-xs text-slate-400">
          {data.team && <span>Team: {data.team}</span>}
          {data.slaLabel && <span>SLA: {data.slaLabel}</span>}
          {data.createdAt && <span>{new Date(data.createdAt).toLocaleString()}</span>}
        </div>
      )}

      <div className="flex gap-2 mt-3">
        <button
          onClick={onApprove}
          disabled={deciding}
          className="flex-1 px-3 py-1.5 bg-green-600 text-white text-xs font-medium rounded-md hover:bg-green-700 disabled:opacity-50 transition-colors inline-flex items-center justify-center gap-1"
        >
          {deciding ? <Loader2 size={12} className="animate-spin" /> : approveLabel}
        </button>
        <button
          onClick={onDeny}
          disabled={deciding}
          className="flex-1 px-3 py-1.5 bg-red-600 text-white text-xs font-medium rounded-md hover:bg-red-700 disabled:opacity-50 transition-colors inline-flex items-center justify-center gap-1"
        >
          {deciding ? <Loader2 size={12} className="animate-spin" /> : denyLabel}
        </button>
      </div>
    </div>
  );
}
