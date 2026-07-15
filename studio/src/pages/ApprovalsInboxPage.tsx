import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle, Loader2, Shield } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { listPendingApprovals, decideApproval, ApprovalInboxItem } from "../api/registryApi";
import ApprovalCard from "../components/approvals/ApprovalCard";

export default function ApprovalsInboxPage() {
  const qc = useQueryClient();
  const [teamFilter, setTeamFilter] = useState<string>("");
  // WS-2 T013 — narrow the inbox to a chosen reviewer scope ("" = all scopes).
  const [scopeFilter, setScopeFilter] = useState<string>("");

  // The list is already scoped server-side to what this reviewer may decide
  // (ApprovalAuthority + admin roles in approvals.list_approvals) — so every row
  // shown here is one the caller has authority over. The inbox reflects that
  // authority; it does not re-check it client-side (no rebuild — WS-1 T7).
  // `scopeFilter` narrows further to one reviewer role (client-side; the list
  // endpoint has no reviewer_scope query param — see listPendingApprovals).
  const { data: approvals, isLoading } = useQuery({
    queryKey: ["pending-approvals", teamFilter, scopeFilter],
    queryFn: () => listPendingApprovals(teamFilter || undefined, undefined, scopeFilter || undefined),
    refetchInterval: 10000,
  });

  // Accumulate the reviewer scopes we've seen so the filter dropdown keeps every
  // option available even after a filter narrows the returned rows to one scope.
  const [knownScopes, setKnownScopes] = useState<string[]>([]);
  useEffect(() => {
    if (!approvals) return;
    setKnownScopes((prev) => {
      const next = new Set(prev);
      for (const a of approvals) if (a.reviewer_scope) next.add(a.reviewer_scope);
      return next.size === prev.length ? prev : Array.from(next).sort();
    });
  }, [approvals]);

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
        <div className="flex items-center gap-2">
          <select
            aria-label="Filter by reviewer role"
            value={scopeFilter}
            onChange={(e) => setScopeFilter(e.target.value)}
            className="text-xs border border-slate-200 rounded px-2 py-1"
          >
            <option value="">All reviewer roles</option>
            {knownScopes.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          <select
            aria-label="Filter by team"
            value={teamFilter}
            onChange={(e) => setTeamFilter(e.target.value)}
            className="text-xs border border-slate-200 rounded px-2 py-1"
          >
            <option value="">All teams</option>
            <option value="platform">platform</option>
            <option value="operations">operations</option>
          </select>
        </div>
      </div>

      {!approvals || approvals.length === 0 ? (
        <div className="text-center py-16 text-slate-400">
          <CheckCircle size={40} className="mx-auto mb-3 text-green-300" />
          <p className="text-sm">No pending approvals. All clear.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {approvals.map((item: ApprovalInboxItem) => (
            <div key={item.id} className="card p-4">
              <ApprovalCard
                data={{
                  toolName: item.tool_name,
                  riskLevel: item.risk_level,
                  args: item.tool_args,
                  agentName: item.agent_name,
                  stepName: item.step_name,
                  team: item.team,
                  slaLabel: formatSla(item.sla_remaining_seconds),
                  createdAt: item.created_at,
                  principalDisplay: item.principal_display,
                }}
                deciding={decideMutation.isPending}
                onApprove={() => decideMutation.mutate({ id: item.id, decision: "approved", version: item.version })}
                onDeny={() => decideMutation.mutate({ id: item.id, decision: "rejected", version: item.version })}
              />
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
