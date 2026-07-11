import { useState } from "react";
import { Loader2, ShieldAlert, X } from "lucide-react";
import { toast } from "sonner";
import { SessionApproval, decideSandboxApproval } from "../../api/registryApi";

interface Props {
  /** Pending approvals for the current conversation (session). */
  approvals: SessionApproval[];
  /** Called after a decision is submitted, with the run_id to resume. */
  onDecided: (runId: string, decision: "approved" | "denied") => void;
  onClose: () => void;
}

/**
 * Right-side panel for SANDBOX deployment chats. A developer testing their own
 * agent self-approves (or denies) the paused high-risk tool call right here —
 * no separate reviewer, no production console. The chat auto-resumes on decision.
 *
 * Scoped to the current conversation via session_id. Today the graph interrupts
 * at the first high-risk tool so there's usually one pending row; the list shape
 * is forward-proof for conversation approval history once conversations persist.
 */
export default function ConversationApprovalPanel({
  approvals,
  onDecided,
  onClose,
}: Props) {
  const [deciding, setDeciding] = useState<string | null>(null);

  const pending = approvals.filter((a) => a.status === "pending");

  const decide = async (a: SessionApproval, decision: "approved" | "denied") => {
    setDeciding(a.approval_id);
    try {
      await decideSandboxApproval(a.approval_id, decision);
      onDecided(a.run_id, decision);
    } catch {
      toast.error("Could not submit the decision.");
    } finally {
      setDeciding(null);
    }
  };

  return (
    <aside
      data-testid="sandbox-approval-panel"
      className="w-[360px] shrink-0 border-l border-slate-200 bg-slate-50 flex flex-col h-full"
    >
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-200 shrink-0">
        <div className="flex items-center gap-2">
          <ShieldAlert size={16} className="text-amber-500" />
          <h3 className="text-sm font-semibold text-slate-900">
            Approvals{pending.length > 0 && ` (${pending.length})`}
          </h3>
          <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-slate-200 text-slate-600 uppercase">
            sandbox
          </span>
        </div>
        <button
          onClick={onClose}
          className="p-1 rounded hover:bg-slate-200 text-slate-400 hover:text-slate-600"
          aria-label="Close approvals"
        >
          <X size={16} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        <p className="text-xs text-slate-500">
          You're testing in sandbox — approve or deny this tool call to continue
          the conversation.
        </p>
        {pending.length === 0 && (
          <p className="text-xs text-slate-400">No pending approvals.</p>
        )}
        {pending.map((a) => (
          <div
            key={a.approval_id}
            data-testid="sandbox-approval-row"
            className="rounded-lg border border-amber-200 bg-white p-3"
          >
            <div className="flex items-center gap-2">
              <span className="font-mono text-sm font-medium text-slate-800">
                {a.tool}
              </span>
              <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-red-100 text-red-700 uppercase">
                {a.risk} risk
              </span>
            </div>
            {/* WHO — who requested the call */}
            {a.requested_by && (
              <p className="mt-1 text-[11px] text-slate-500">
                Requested by <span className="font-medium text-slate-600">{a.requested_by}</span>
                {a.requested_by_team && ` · ${a.requested_by_team}`}
              </p>
            )}
            {/* WHY — the LLM's stated reason (hidden when empty) */}
            {a.reasoning && (
              <p
                className="mt-2 text-xs italic text-slate-600 border-l-2 border-amber-300 pl-2"
                data-testid="sandbox-approval-reasoning"
              >
                {a.reasoning}
              </p>
            )}
            {/* WHAT — the exact arguments the tool will run with */}
            {a.args && Object.keys(a.args).length > 0 && (
              <pre className="mt-2 p-2 rounded bg-slate-50 text-xs text-slate-700 overflow-x-auto max-w-full">
                {JSON.stringify(a.args, null, 2)}
              </pre>
            )}
            <div className="flex gap-2 mt-3">
              <button
                onClick={() => decide(a, "approved")}
                disabled={deciding === a.approval_id}
                className="flex-1 px-3 py-1.5 bg-green-600 text-white text-xs font-medium rounded-md hover:bg-green-700 disabled:opacity-50 transition-colors inline-flex items-center justify-center gap-1"
              >
                {deciding === a.approval_id ? (
                  <Loader2 size={12} className="animate-spin" />
                ) : (
                  "Approve"
                )}
              </button>
              <button
                onClick={() => decide(a, "denied")}
                disabled={deciding === a.approval_id}
                className="flex-1 px-3 py-1.5 bg-red-600 text-white text-xs font-medium rounded-md hover:bg-red-700 disabled:opacity-50 transition-colors inline-flex items-center justify-center gap-1"
              >
                {deciding === a.approval_id ? (
                  <Loader2 size={12} className="animate-spin" />
                ) : (
                  "Deny"
                )}
              </button>
            </div>
          </div>
        ))}
      </div>
    </aside>
  );
}
