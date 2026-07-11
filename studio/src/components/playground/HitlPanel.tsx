import { Loader2, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { decidePlaygroundApproval } from "../../api/playgroundApi";
import { toast } from "sonner";

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
        <div className="flex items-start gap-3">
          <ShieldAlert size={20} className="text-amber-500 mt-0.5 shrink-0" />
          <div className="flex-1">
            <p className="text-sm font-semibold text-slate-800">
              Approval Required — {request.tool_name}
            </p>
            <p className="text-xs text-slate-500 mt-0.5">
              Risk level:{" "}
              <span className="font-medium text-amber-700">{request.risk_level}</span>
              {request.requested_by && (
                <>
                  {" · requested by "}
                  <span className="font-medium text-slate-600">{request.requested_by}</span>
                  {request.requested_by_team && ` (${request.requested_by_team})`}
                </>
              )}
            </p>
            {request.reasoning && (
              <p className="mt-1.5 text-xs italic text-slate-600 border-l-2 border-amber-300 pl-2">
                {request.reasoning}
              </p>
            )}
            {Object.keys(request.args_redacted).length > 0 && (
              <pre className="mt-2 text-xs bg-slate-50 rounded p-2 overflow-auto max-h-24 text-slate-600">
                {JSON.stringify(request.args_redacted, null, 2)}
              </pre>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() => decide("approved")}
              disabled={deciding}
              className="btn-primary text-xs py-1.5 px-3 bg-green-600 hover:bg-green-700"
            >
              {deciding ? <Loader2 size={12} className="animate-spin" /> : "Approve"}
            </button>
            <button
              onClick={() => decide("denied")}
              disabled={deciding}
              className="btn-primary text-xs py-1.5 px-3 bg-red-600 hover:bg-red-700"
            >
              {deciding ? <Loader2 size={12} className="animate-spin" /> : "Deny"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
