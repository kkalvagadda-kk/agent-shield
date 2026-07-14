import { useState } from "react";
import { ShieldAlert, X } from "lucide-react";
import { toast } from "sonner";
import { SessionApproval, decideSandboxApproval } from "../../api/registryApi";
import ApprovalCard from "../approvals/ApprovalCard";

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
            <ApprovalCard
              data={{
                toolName: a.tool,
                riskLevel: a.risk,
                args: a.args,
                reasoning: a.reasoning,
                requestedBy: a.requested_by,
                requestedByTeam: a.requested_by_team,
              }}
              deciding={deciding === a.approval_id}
              reasoningTestId="sandbox-approval-reasoning"
              onApprove={() => decide(a, "approved")}
              onDeny={() => decide(a, "denied")}
            />
          </div>
        ))}
      </div>
    </aside>
  );
}
