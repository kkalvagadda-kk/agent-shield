import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus, RefreshCw, ShieldOff, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import {
  createGrant,
  listGrants,
  revokeGrant,
  type AssetGrant,
} from "../api/registryApi";

const TYPE_CHIP: Record<string, string> = {
  agent:    "bg-blue-50 text-blue-700",
  tool:     "bg-purple-50 text-purple-700",
  skill:    "bg-teal-50 text-teal-700",
  workflow: "bg-indigo-50 text-indigo-700",
};

export default function AdminGrantsPage() {
  const qc = useQueryClient();
  const [typeFilter, setTypeFilter] = useState<string>("");
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({
    asset_id: "",
    asset_type: "agent",
    grantee_team: "",
    expires_at: "",
  });

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["grants", typeFilter],
    queryFn: () => listGrants({ limit: 200 }),
  });

  const revokeMutation = useMutation({
    mutationFn: (id: string) => revokeGrant(id),
    onSuccess: () => {
      toast.success("Grant revoked.");
      qc.invalidateQueries({ queryKey: ["grants"] });
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      toast.error(msg ?? "Failed to revoke grant.");
    },
  });

  const createMutation = useMutation({
    mutationFn: () =>
      createGrant({
        asset_id: form.asset_id,
        asset_type: form.asset_type,
        grantee_team: form.grantee_team,
        expires_at: form.expires_at || undefined,
      }),
    onSuccess: () => {
      toast.success("Grant created.");
      setShowCreate(false);
      setForm({ asset_id: "", asset_type: "agent", grantee_team: "", expires_at: "" });
      qc.invalidateQueries({ queryKey: ["grants"] });
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      toast.error(msg ?? "Failed to create grant.");
    },
  });

  const filtered = data?.items.filter(
    (g) => !typeFilter || g.asset_type === typeFilter
  ) ?? [];

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Asset Grants</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Manage which teams have access to which assets
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
            {showCreate ? "Cancel" : "New Grant"}
          </button>
        </div>
      </div>

      {/* Create form */}
      {showCreate && (
        <div className="card mb-6">
          <h2 className="text-base font-semibold text-slate-900 mb-4">Create Asset Grant</h2>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1">
              <label className="label">Asset ID (UUID)</label>
              <input
                className="input font-mono text-sm"
                placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                value={form.asset_id}
                onChange={(e) => setForm({ ...form, asset_id: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <label className="label">Asset Type</label>
              <select
                className="input"
                value={form.asset_type}
                onChange={(e) => setForm({ ...form, asset_type: e.target.value })}
              >
                <option value="agent">Agent</option>
                <option value="tool">Tool</option>
                <option value="skill">Skill</option>
                <option value="workflow">Workflow</option>
              </select>
            </div>
            <div className="space-y-1">
              <label className="label">Grantee Team</label>
              <input
                className="input"
                placeholder="platform"
                value={form.grantee_team}
                onChange={(e) => setForm({ ...form, grantee_team: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <label className="label">Expires At (optional)</label>
              <input
                type="datetime-local"
                className="input"
                value={form.expires_at}
                onChange={(e) => setForm({ ...form, expires_at: e.target.value })}
              />
            </div>
          </div>
          <div className="flex justify-end gap-2 mt-4 pt-4 border-t border-slate-100">
            <button onClick={() => setShowCreate(false)} className="btn-secondary">Cancel</button>
            <button
              onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending || !form.asset_id || !form.grantee_team}
              className="btn-primary disabled:opacity-50"
            >
              {createMutation.isPending ? (
                <><Loader2 size={14} className="animate-spin" /> Creating…</>
              ) : (
                "Create Grant"
              )}
            </button>
          </div>
        </div>
      )}

      {isLoading && (
        <div className="flex items-center justify-center py-20 text-slate-400">
          <Loader2 size={20} className="animate-spin mr-2" />
          Loading grants…
        </div>
      )}

      {error && (
        <div className="rounded-lg bg-red-50 border border-red-200 p-4 text-sm text-red-700">
          Failed to load grants: {String(error)}
        </div>
      )}

      {data && (
        <div className="card p-0 overflow-hidden">
          {filtered.length === 0 ? (
            <div className="flex flex-col items-center py-16 text-center">
              <ShieldOff size={36} className="text-slate-300 mb-3" />
              <p className="text-slate-500 font-medium">No active grants</p>
              <p className="text-slate-400 text-sm mt-1">Create a grant to give a team access to an asset.</p>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50">
                  {[
                    "Asset Type",
                    "Asset ID",
                    "Grantee Team",
                    "Granted By",
                    "Granted At",
                    "Expires At",
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
                {filtered.map((grant) => (
                  <GrantRow
                    key={grant.id}
                    grant={grant}
                    onRevoke={() => {
                      if (confirm(`Revoke grant for team "${grant.grantee_team}"?`)) {
                        revokeMutation.mutate(grant.id);
                      }
                    }}
                    revoking={
                      revokeMutation.isPending && revokeMutation.variables === grant.id
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
          {filtered.length} of {data.total} grant{data.total !== 1 ? "s" : ""}
        </p>
      )}
    </div>
  );
}

function GrantRow({
  grant,
  onRevoke,
  revoking,
}: {
  grant: AssetGrant;
  onRevoke: () => void;
  revoking: boolean;
}) {
  const typeChip = TYPE_CHIP[grant.asset_type] ?? "bg-slate-100 text-slate-600";
  return (
    <tr className="hover:bg-slate-50 transition-colors">
      <td className="px-4 py-3">
        <span className={`badge ${typeChip}`}>{grant.asset_type}</span>
      </td>
      <td className="px-4 py-3 font-mono text-xs text-slate-500">
        {grant.asset_id.slice(0, 8)}…
      </td>
      <td className="px-4 py-3 font-medium text-slate-800">{grant.grantee_team}</td>
      <td className="px-4 py-3 text-slate-500 text-xs">{grant.granted_by}</td>
      <td className="px-4 py-3 text-slate-400 text-xs">
        {new Date(grant.granted_at).toLocaleDateString()}
      </td>
      <td className="px-4 py-3 text-slate-400 text-xs">
        {grant.expires_at ? new Date(grant.expires_at).toLocaleDateString() : "—"}
      </td>
      <td className="px-4 py-3">
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
      </td>
    </tr>
  );
}
