import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus, RefreshCw, ShieldCheck, ShieldOff, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import {
  createApprovalAuthority,
  listApprovalAuthority,
  revokeApprovalAuthority,
  type ApprovalAuthority,
} from "../api/registryApi";

export default function AdminApprovalAuthorityPage() {
  const qc = useQueryClient();
  const [typeFilter, setTypeFilter] = useState<string>("");
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({
    resource_type: "agent",
    resource_id: "",
    approver_user_id: "",
    approver_role: "",
  });

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["approval-authority", typeFilter],
    queryFn: () => listApprovalAuthority({ limit: 200 }),
  });

  const revokeMutation = useMutation({
    mutationFn: (id: string) => revokeApprovalAuthority(id),
    onSuccess: () => {
      toast.success("Approval authority revoked.");
      qc.invalidateQueries({ queryKey: ["approval-authority"] });
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      toast.error(msg ?? "Failed to revoke approval authority.");
    },
  });

  const createMutation = useMutation({
    mutationFn: () =>
      createApprovalAuthority({
        resource_type: form.resource_type,
        resource_id: form.resource_id,
        approver_user_id: form.approver_user_id || undefined,
        approver_role: form.approver_role || undefined,
      }),
    onSuccess: () => {
      toast.success("Approval authority created.");
      setShowCreate(false);
      setForm({ resource_type: "agent", resource_id: "", approver_user_id: "", approver_role: "" });
      qc.invalidateQueries({ queryKey: ["approval-authority"] });
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      toast.error(msg ?? "Failed to create approval authority.");
    },
  });

  const filtered = data?.items.filter(
    (a) => !typeFilter || a.resource_type === typeFilter
  ) ?? [];

  const isCreateValid =
    form.resource_id.trim() && (form.approver_user_id.trim() || form.approver_role.trim());

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Approval Authorities</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Configure who can approve HITL requests for each resource
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            className="input text-sm w-36"
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
          >
            <option value="">All types</option>
            <option value="agent">Agent</option>
            <option value="tool">Tool</option>
            <option value="skill">Skill</option>
            <option value="workflow">Workflow</option>
          </select>
          <button onClick={() => refetch()} disabled={isFetching} className="btn-secondary">
            <RefreshCw size={14} className={isFetching ? "animate-spin" : ""} />
            Refresh
          </button>
          <button onClick={() => setShowCreate((v) => !v)} className="btn-primary">
            {showCreate ? <X size={14} /> : <Plus size={14} />}
            {showCreate ? "Cancel" : "Add Authority"}
          </button>
        </div>
      </div>

      {showCreate && (
        <div className="card mb-6">
          <h2 className="text-base font-semibold text-slate-900 mb-4">Add Approval Authority</h2>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1">
              <label className="label">Resource Type</label>
              <select
                className="input"
                value={form.resource_type}
                onChange={(e) => setForm({ ...form, resource_type: e.target.value })}
              >
                <option value="agent">Agent</option>
                <option value="tool">Tool</option>
                <option value="skill">Skill</option>
                <option value="workflow">Workflow</option>
              </select>
            </div>
            <div className="space-y-1">
              <label className="label">Resource ID</label>
              <input
                className="input font-mono text-sm"
                placeholder="agent name or UUID"
                value={form.resource_id}
                onChange={(e) => setForm({ ...form, resource_id: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <label className="label">Approver User ID</label>
              <input
                className="input"
                placeholder="user@example.com or sub claim"
                value={form.approver_user_id}
                onChange={(e) => setForm({ ...form, approver_user_id: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <label className="label">Approver Role</label>
              <input
                className="input"
                placeholder="safety-reviewer (optional)"
                value={form.approver_role}
                onChange={(e) => setForm({ ...form, approver_role: e.target.value })}
              />
            </div>
          </div>
          <p className="text-xs text-slate-400 mt-2">At least one of Approver User ID or Approver Role is required.</p>
          <div className="flex justify-end gap-2 mt-4 pt-4 border-t border-slate-100">
            <button onClick={() => setShowCreate(false)} className="btn-secondary">Cancel</button>
            <button
              onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending || !isCreateValid}
              className="btn-primary disabled:opacity-50"
            >
              {createMutation.isPending ? (
                <><Loader2 size={14} className="animate-spin" /> Creating…</>
              ) : (
                "Add Authority"
              )}
            </button>
          </div>
        </div>
      )}

      {isLoading && (
        <div className="flex items-center justify-center py-20 text-slate-400">
          <Loader2 size={20} className="animate-spin mr-2" />
          Loading authorities…
        </div>
      )}

      {error && (
        <div className="rounded-lg bg-red-50 border border-red-200 p-4 text-sm text-red-700">
          Failed to load approval authorities: {String(error)}
        </div>
      )}

      {data && (
        <div className="card p-0 overflow-hidden">
          {filtered.length === 0 ? (
            <div className="flex flex-col items-center py-16 text-center">
              <ShieldCheck size={36} className="text-slate-300 mb-3" />
              <p className="text-slate-500 font-medium">No approval authorities configured</p>
              <p className="text-slate-400 text-sm mt-1">
                Add an authority to require specific approvers for HITL decisions.
              </p>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50">
                  {[
                    "Resource Type",
                    "Resource ID",
                    "Approver User",
                    "Approver Role",
                    "Granted By",
                    "Granted At",
                    "Status",
                    "Actions",
                  ].map((h) => (
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
                {filtered.map((aa) => (
                  <AuthorityRow
                    key={aa.id}
                    authority={aa}
                    onRevoke={() => {
                      if (confirm(`Revoke this approval authority for "${aa.resource_id}"?`)) {
                        revokeMutation.mutate(aa.id);
                      }
                    }}
                    revoking={
                      revokeMutation.isPending && revokeMutation.variables === aa.id
                    }
                  />
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {data && (
        <p className="text-xs text-slate-400 mt-2 text-right">
          {filtered.length} of {data.total} authorit{data.total !== 1 ? "ies" : "y"}
        </p>
      )}
    </div>
  );
}

function AuthorityRow({
  authority,
  onRevoke,
  revoking,
}: {
  authority: ApprovalAuthority;
  onRevoke: () => void;
  revoking: boolean;
}) {
  const isRevoked = !!authority.revoked_at;
  return (
    <tr className={`hover:bg-slate-50 transition-colors ${isRevoked ? "opacity-50" : ""}`}>
      <td className="px-4 py-3">
        <span className="badge bg-blue-50 text-blue-700">{authority.resource_type}</span>
      </td>
      <td className="px-4 py-3 font-mono text-xs text-slate-600">{authority.resource_id}</td>
      <td className="px-4 py-3 text-slate-600 text-xs">{authority.approver_user_id ?? "—"}</td>
      <td className="px-4 py-3 text-slate-600 text-xs">{authority.approver_role ?? "—"}</td>
      <td className="px-4 py-3 text-slate-400 text-xs">{authority.granted_by}</td>
      <td className="px-4 py-3 text-slate-400 text-xs">
        {new Date(authority.granted_at).toLocaleDateString()}
      </td>
      <td className="px-4 py-3">
        {isRevoked ? (
          <span className="badge bg-red-50 text-red-600">Revoked</span>
        ) : (
          <span className="badge bg-green-50 text-green-700">Active</span>
        )}
      </td>
      <td className="px-4 py-3">
        {!isRevoked && (
          <button
            onClick={onRevoke}
            disabled={revoking}
            className="inline-flex items-center gap-1 text-xs text-red-600 hover:text-red-800 disabled:opacity-50 transition-colors"
          >
            {revoking ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              <ShieldOff size={12} />
            )}
            Revoke
          </button>
        )}
      </td>
    </tr>
  );
}
