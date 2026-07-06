import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle, Loader2, Shield, XCircle } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { listPendingApprovals, decideApproval, ApprovalInboxItem } from "../api/registryApi";

const RISK_BADGE: Record<string, string> = {
  critical: "bg-red-100 text-red-700",
  high: "bg-amber-100 text-amber-700",
};

export default function ApprovalsInboxPage() {
  const qc = useQueryClient();
  const [teamFilter, setTeamFilter] = useState<string>("");

  const { data: approvals, isLoading } = useQuery({
    queryKey: ["pending-approvals", teamFilter],
    queryFn: () => listPendingApprovals(teamFilter || undefined),
    refetchInterval: 10000,
  });

  const decideMutation = useMutation({
    mutationFn: ({ id, decision, version }: { id: string; decision: "approved" | "rejected"; version: number }) =>
      decideApproval(id, decision, version),
    onSuccess: () => {
      toast.success("Decision submitted.");
      qc.invalidateQueries({ queryKey: ["pending-approvals"] });
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || "Failed to submit decision.");
    },
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20 text-slate-400">
        <Loader2 size={20} className="animate-spin mr-2" />
        Loading approvals…
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto px-6 py-8">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-2">
          <Shield size={20} className="text-amber-500" />
          <h1 className="text-xl font-bold text-slate-900">Approvals Inbox</h1>
          {approvals && approvals.length > 0 && (
            <span className="badge bg-amber-100 text-amber-700 text-xs ml-2">
              {approvals.length} pending
            </span>
          )}
        </div>
        <select
          value={teamFilter}
          onChange={(e) => setTeamFilter(e.target.value)}
          className="text-xs border border-slate-200 rounded px-2 py-1"
        >
          <option value="">All teams</option>
          <option value="platform">platform</option>
          <option value="operations">operations</option>
        </select>
      </div>

      {!approvals || approvals.length === 0 ? (
        <div className="text-center py-16 text-slate-400">
          <CheckCircle size={40} className="mx-auto mb-3 text-green-300" />
          <p className="text-sm">No pending approvals. All clear.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {approvals.map((item: ApprovalInboxItem) => (
            <div key={item.id} className="card p-4 flex items-start justify-between">
              <div className="flex-1">
                <div className="flex items-center gap-2 mb-1">
                  <span className="font-mono text-sm font-semibold text-slate-800">
                    {item.agent_name}
                  </span>
                  <span className={`badge text-xs ${RISK_BADGE[item.risk_level] || "bg-slate-100 text-slate-600"}`}>
                    {item.risk_level}
                  </span>
                  {item.step_name && (
                    <span className="text-xs text-slate-400">Step: {item.step_name}</span>
                  )}
                </div>
                <p className="text-sm text-slate-600 mb-1">
                  <span className="font-medium">Tool:</span>{" "}
                  <code className="text-xs bg-slate-50 px-1 rounded">{item.tool_name}</code>
                </p>
                {item.tool_args && Object.keys(item.tool_args).length > 0 && (
                  <pre className="text-xs text-slate-500 bg-slate-50 p-2 rounded mt-1 max-h-20 overflow-auto">
                    {JSON.stringify(item.tool_args, null, 2)}
                  </pre>
                )}
                <div className="flex items-center gap-3 mt-2 text-xs text-slate-400">
                  <span>Team: {item.team}</span>
                  <span>SLA: {formatSla(item.sla_remaining_seconds)}</span>
                  <span>{new Date(item.created_at).toLocaleString()}</span>
                </div>
              </div>
              <div className="flex gap-2 ml-4 shrink-0">
                <button
                  onClick={() => decideMutation.mutate({ id: item.id, decision: "approved", version: item.version })}
                  disabled={decideMutation.isPending}
                  className="btn-primary text-xs py-1 px-3"
                >
                  <CheckCircle size={12} />
                  Approve
                </button>
                <button
                  onClick={() => decideMutation.mutate({ id: item.id, decision: "rejected", version: item.version })}
                  disabled={decideMutation.isPending}
                  className="btn-secondary text-xs py-1 px-3 text-red-600 hover:text-red-700"
                >
                  <XCircle size={12} />
                  Deny
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function formatSla(seconds: number): string {
  if (seconds <= 0) return "Expired";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}
