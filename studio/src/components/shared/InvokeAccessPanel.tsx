import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Link } from "react-router-dom";
import { Plus, Trash2, AlertTriangle } from "lucide-react";
import {
  listApplications,
  listArtifactGrants,
  createArtifactGrant,
  revokeArtifactGrant,
  type ArtifactType,
} from "../../api/registryApi";

// Invoke access (Decision 30) — the artifact-scoped panel where an agent-admin grants
// an APPLICATION the `invoker` role so it can send authenticated (client_signed)
// webhooks to this agent/workflow. Artifact-type-agnostic from the start: SettingsTab
// (agent) and WorkflowTriggersPanel (workflow) both render this same component — the
// workflow surface consumes it, never re-creates it, closing the two-parallel-paths gap.
//
// Granting the FIRST invoker grant flips the trigger's auth_mode to client_signed
// server-side (registry-api create_grant); we invalidate the trigger queries so the
// existing auth_mode badge updates on refetch — no new badge code here.
export default function InvokeAccessPanel({
  artifactType,
  artifactId,
  artifactTeam,
}: {
  artifactType: ArtifactType;
  artifactId: string;
  artifactTeam: string;
}) {
  const qc = useQueryClient();
  const [granting, setGranting] = useState(false);
  const [selected, setSelected] = useState("");

  const { data: apps = [], isLoading: appsLoading } = useQuery({
    queryKey: ["applications", artifactTeam],
    queryFn: () => listApplications(artifactTeam),
  });
  const {
    data: grants = [],
    isLoading: grantsLoading,
    isError,
  } = useQuery({
    queryKey: ["artifact-grants", artifactType, artifactId],
    queryFn: () => listArtifactGrants(artifactType, artifactId),
  });
  const invalidate = () =>
    qc.invalidateQueries({ queryKey: ["artifact-grants", artifactType, artifactId] });

  const invokerGrants = grants.filter(
    (g) => g.role === "invoker" && g.grantee_type === "application"
  );
  const grantedIds = new Set(invokerGrants.map((g) => g.grantee_id));
  const pickable = apps.filter((a) => !grantedIds.has(a.id));
  const appById = new Map(apps.map((a) => [a.id, a]));
  const selectedApp = appById.get(selected);

  const grant = useMutation({
    mutationFn: () =>
      createArtifactGrant(artifactType, artifactId, {
        grantee_type: "application",
        grantee_id: selected,
        role: "invoker",
      }),
    onSuccess: () => {
      toast.success("Invoke access granted — this trigger now requires client_signed.");
      setGranting(false);
      setSelected("");
      invalidate();
      // The first invoker grant flips auth_mode server-side; refetch triggers so the
      // existing token/client_signed badge reflects it.
      qc.invalidateQueries({ queryKey: ["triggers"] });
    },
    onError: (e) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data
        ?.detail;
      toast.error(detail ?? "Failed to grant access");
    },
  });

  const revoke = useMutation({
    mutationFn: (id: string) => revokeArtifactGrant(artifactType, artifactId, id),
    onSuccess: () => {
      toast.success("Invoke access revoked");
      invalidate();
    },
    onError: () => toast.error("Failed to revoke access"),
  });

  return (
    <div className="border-t border-slate-100 pt-3 space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs text-slate-500 uppercase">Invoke access — applications</span>
        {!granting && apps.length > 0 && (
          <button onClick={() => setGranting(true)} className="btn-secondary text-xs py-1">
            <Plus size={12} /> Grant access
          </button>
        )}
      </div>

      {/* Empty state — no team applications exist yet (§9.8). Creation lives at the
          team level only, so this links out rather than offering an inline create. */}
      {!appsLoading && apps.length === 0 && (
        <p className="text-xs text-slate-400" data-testid="invoke-empty-state">
          No applications registered for your team yet.{" "}
          <Link to="/applications" className="text-indigo-600 hover:underline">
            Create one
          </Link>
          .
        </p>
      )}

      {granting && (
        <div className="border border-slate-200 rounded-lg p-4 space-y-3 bg-slate-50/50">
          <label className="block">
            <span className="text-xs text-slate-500 uppercase">Application</span>
            <select
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
              className="mt-1 w-full text-sm border border-slate-300 rounded px-2 py-1.5 bg-white"
              aria-label="Application to grant invoke access"
            >
              <option value="">Select an application…</option>
              {pickable.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                  {a.enabled ? "" : " (disabled)"}
                </option>
              ))}
            </select>
          </label>
          {selectedApp && (
            <div className="flex items-start gap-2 border border-amber-200 bg-amber-50 rounded-lg p-3">
              <AlertTriangle size={14} className="text-amber-600 mt-0.5 shrink-0" />
              <p className="text-xs text-amber-800" data-testid="invoke-ack">
                <span className="font-medium">{selectedApp.name}</span> will be able to
                trigger runs on this {artifactType} without a human present. Approval-gated
                steps may stall if nobody is watching.
              </p>
            </div>
          )}
          <div className="flex justify-end gap-2">
            <button
              onClick={() => {
                setGranting(false);
                setSelected("");
              }}
              className="btn-secondary text-xs py-1.5"
            >
              Cancel
            </button>
            <button
              onClick={() => grant.mutate()}
              disabled={grant.isPending || !selected}
              className="btn-primary text-xs py-1.5 disabled:opacity-50"
            >
              {grant.isPending ? "Granting…" : "Grant access"}
            </button>
          </div>
        </div>
      )}

      {grantsLoading ? (
        <p className="text-xs text-slate-400">Loading invoke access…</p>
      ) : isError ? (
        <p className="text-xs text-red-600">Failed to load invoke access.</p>
      ) : invokerGrants.length === 0 ? (
        apps.length > 0 ? (
          <p className="text-xs text-slate-400">
            No applications can invoke this {artifactType} yet.
          </p>
        ) : null
      ) : (
        <div className="space-y-2">
          {invokerGrants.map((g) => {
            const app = appById.get(g.grantee_id);
            const disabled = app ? !app.enabled : false;
            return (
              <div
                key={g.id}
                data-testid={`invoker-grant-${g.grantee_id}`}
                className="flex items-center justify-between gap-3 border border-slate-200 rounded px-3 py-2"
              >
                <div className="min-w-0 flex items-center gap-2">
                  <code className="text-xs text-slate-700">
                    {g.grantee_label ?? app?.name ?? g.grantee_id}
                  </code>
                  {disabled && (
                    <span className="text-[10px] uppercase tracking-wide bg-red-100 text-red-700 rounded px-1.5 py-0.5">
                      application disabled
                    </span>
                  )}
                </div>
                <button
                  onClick={() => revoke.mutate(g.id)}
                  disabled={revoke.isPending}
                  className="text-slate-400 hover:text-red-500 shrink-0"
                  aria-label={`Revoke invoke access for ${g.grantee_label ?? g.grantee_id}`}
                >
                  <Trash2 size={14} />
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
