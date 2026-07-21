import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Trash2 } from "lucide-react";
import {
  listArtifactGrants,
  revokeArtifactGrant,
  type ArtifactType,
} from "../../api/registryApi";

// Full grants list (Decision 25/30) for one artifact — every active grant across all
// three roles (agent-admin / approver / invoker) and all grantee types (user / team /
// application). Artifact-type-agnostic: rendered on both the agent (SettingsTab) and
// workflow (WorkflowTriggersPanel) surfaces. This is now the place a human
// agent-admin/approver grant is managed too, not invoker-only.
//
// Shares the ["artifact-grants", type, id] query key with InvokeAccessPanel, so a
// revoke here (or an invoker grant there) refetches both in sync.
export default function ArtifactGrantsList({
  artifactType,
  artifactId,
}: {
  artifactType: ArtifactType;
  artifactId: string;
}) {
  const qc = useQueryClient();
  const {
    data: grants = [],
    isLoading,
    isError,
  } = useQuery({
    queryKey: ["artifact-grants", artifactType, artifactId],
    queryFn: () => listArtifactGrants(artifactType, artifactId),
  });

  const revoke = useMutation({
    mutationFn: (id: string) => revokeArtifactGrant(artifactType, artifactId, id),
    onSuccess: () => {
      toast.success("Grant revoked");
      qc.invalidateQueries({ queryKey: ["artifact-grants", artifactType, artifactId] });
    },
    onError: () => toast.error("Failed to revoke grant"),
  });

  return (
    <div className="space-y-2">
      <span className="text-xs text-slate-500 uppercase">Access grants</span>
      {isLoading ? (
        <p className="text-xs text-slate-400">Loading grants…</p>
      ) : isError ? (
        <p className="text-xs text-red-600">Failed to load grants.</p>
      ) : grants.length === 0 ? (
        <p className="text-xs text-slate-400">No access grants on this {artifactType}.</p>
      ) : (
        <div className="space-y-2">
          {grants.map((g) => (
            <div
              key={g.id}
              data-testid={`grant-row-${g.id}`}
              className="flex items-center justify-between gap-3 border border-slate-200 rounded px-3 py-2"
            >
              <div className="min-w-0 flex items-center gap-2 flex-wrap">
                <span className="text-[10px] uppercase tracking-wide bg-indigo-100 text-indigo-700 rounded px-1.5 py-0.5">
                  {g.role}
                </span>
                <span className="text-[10px] uppercase tracking-wide bg-slate-100 text-slate-600 rounded px-1.5 py-0.5">
                  {g.grantee_type}
                </span>
                <code className="text-xs text-slate-700">
                  {g.grantee_label ?? g.grantee_id}
                </code>
              </div>
              <button
                onClick={() => revoke.mutate(g.id)}
                disabled={revoke.isPending}
                className="text-slate-400 hover:text-red-500 shrink-0"
                aria-label={`Revoke ${g.role} for ${g.grantee_label ?? g.grantee_id}`}
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
