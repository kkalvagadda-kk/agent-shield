import { ShieldAlert } from "lucide-react";
import { useState } from "react";
import { decidePlaygroundApproval } from "../../api/playgroundApi";
import { toast } from "sonner";
import ApprovalCard from "../approvals/ApprovalCard";

export interface HitlRequest {
  approval_id: string;
  tool_name: string;
  risk_level: string;
  args_redacted: Record<string, unknown>;
  reasoning?: string | null;
  requested_by?: string | null;
  requested_by_team?: string | null;
}

interface Props {
  request: HitlRequest | null;
  onDecided: (decision: "approved" | "denied", threadId: string) => void;
}

export default function HitlPanel({ request, onDecided }: Props) {
  const [deciding, setDeciding] = useState(false);

  if (!request) return null;

  const decide = async (decision: "approved" | "denied") => {
    setDeciding(true);
    try {
      const result = await decidePlaygroundApproval(request.approval_id, decision);
      toast.success(`Playground approval ${decision}.`);
      onDecided(decision, result.thread_id);
    } catch {
      toast.error("Could not submit decision.");
    } finally {
      setDeciding(false);
    }
  };

  return (
    <div className="fixed bottom-0 left-0 right-0 z-50 bg-white border-t-2 border-amber-400 shadow-2xl p-4">
      <div className="max-w-3xl mx-auto">
        <p className="flex items-center gap-1.5 text-sm font-semibold text-slate-800 mb-2">
          <ShieldAlert size={16} className="text-amber-500 shrink-0" />
          Approval Required
        </p>
        <ApprovalCard
          data={{
            toolName: request.tool_name,
            riskLevel: request.risk_level,
            args: request.args_redacted,
            reasoning: request.reasoning,
            requestedBy: request.requested_by,
            requestedByTeam: request.requested_by_team,
          }}
          deciding={deciding}
          onApprove={() => decide("approved")}
          onDeny={() => decide("denied")}
        />
      </div>
    </div>
  );
}
