import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Plus, Trash2 } from "lucide-react";
import {
  listArtifactGrants,
  revokeArtifactGrant,
  createArtifactGrant,
  listUsers,
  listTeams,
  type ArtifactType,
} from "../../api/registryApi";

// Full grants list (Decision 25/30) for one artifact — every active grant across all
// three roles (agent-admin / approver / invoker) and all grantee types (user / team /
// application). Artifact-type-agnostic: rendered on both the agent (SettingsTab) and
// workflow (WorkflowTriggersPanel) surfaces. This is where a human agent-admin/approver
// grant is CREATED and managed (design doc §9.2) — the `invoker`/application path lives
// in InvokeAccessPanel, so this form intentionally offers only the human roles
// (agent-admin/approver) and human grantee types (user/team).
//
// Shares the ["artifact-grants", type, id] query key with InvokeAccessPanel, so a grant
// or revoke here refetches both in sync.
export default function ArtifactGrantsList({
  artifactType,
  artifactId,
}: {
  artifactType: ArtifactType;
  artifactId: string;
}) {
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [role, setRole] = useState<"agent-admin" | "approver">("agent-admin");
  const [granteeType, setGranteeType] = useState<"user" | "team">("user");
  const [granteeId, setGranteeId] = useState("");

  const {
    data: grants = [],
    isLoading,
    isError,
  } = useQuery({
    queryKey: ["artifact-grants", artifactType, artifactId],
    queryFn: () => listArtifactGrants(artifactType, artifactId),
  });

  // Picker sources — fetched only while the form is open. The queryFns are wrapped in
  // arrows (not passed by reference) so the imported binding is touched only when the
  // query actually runs — a bare `queryFn: listUsers` would be evaluated at render and
  // blow up any test whose registryApi mock doesn't stub these lazy-only calls.
  const { data: users = [] } = useQuery({
    queryKey: ["admin-users"],
    queryFn: () => listUsers(),
    enabled: adding && granteeType === "user",
  });
  const { data: teamsPage } = useQuery({
    queryKey: ["teams"],
    queryFn: () => listTeams(),
    enabled: adding && granteeType === "team",
  });
  const teams = teamsPage?.items ?? [];

  const invalidate = () =>
    qc.invalidateQueries({ queryKey: ["artifact-grants", artifactType, artifactId] });

  const closeForm = () => {
    setAdding(false);
    setGranteeId("");
    setRole("agent-admin");
    setGranteeType("user");
  };

  const grant = useMutation({
    mutationFn: () =>
      createArtifactGrant(artifactType, artifactId, {
        grantee_type: granteeType,
        grantee_id: granteeId,
        role,
      }),
    onSuccess: () => {
      toast.success(`Granted ${role} to this ${granteeType}.`);
      closeForm();
      invalidate();
    },
    onError: (e) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data
        ?.detail;
      toast.error(detail ?? "Failed to create grant");
    },
  });

  const revoke = useMutation({
    mutationFn: (id: string) => revokeArtifactGrant(artifactType, artifactId, id),
    onSuccess: () => {
      toast.success("Grant revoked");
      invalidate();
    },
    onError: () => toast.error("Failed to revoke grant"),
  });

  const userLabel = (kcId: string) => {
    const u = users.find((x) => x.kc_id === kcId);
    if (!u) return kcId;
    const name = `${u.first_name} ${u.last_name}`.trim() || u.username;
    return u.email ? `${name} (${u.email})` : name;
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs text-slate-500 uppercase">Access grants</span>
        {!adding && (
          <button onClick={() => setAdding(true)} className="btn-secondary text-xs py-1">
            <Plus size={12} /> Grant
          </button>
        )}
      </div>

      {/* Human-grantee grant creation (agent-admin / approver to a user or team). The
          invoker/application path is InvokeAccessPanel's — deliberately kept separate. */}
      {adding && (
        <div className="border border-slate-200 rounded-lg p-4 space-y-3 bg-slate-50/50">
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="text-xs text-slate-500 uppercase">Role</span>
              <select
                value={role}
                onChange={(e) => setRole(e.target.value as "agent-admin" | "approver")}
                className="mt-1 w-full text-sm border border-slate-300 rounded px-2 py-1.5 bg-white"
                aria-label="Role to grant"
              >
                <option value="agent-admin">agent-admin</option>
                <option value="approver">approver</option>
              </select>
            </label>
            <label className="block">
              <span className="text-xs text-slate-500 uppercase">Grantee type</span>
              <select
                value={granteeType}
                onChange={(e) => {
                  setGranteeType(e.target.value as "user" | "team");
                  setGranteeId("");
                }}
                className="mt-1 w-full text-sm border border-slate-300 rounded px-2 py-1.5 bg-white"
                aria-label="Grantee type"
              >
                <option value="user">User</option>
                <option value="team">Team</option>
              </select>
            </label>
          </div>

          <label className="block">
            <span className="text-xs text-slate-500 uppercase">
              {granteeType === "user" ? "User" : "Team"}
            </span>
            <select
              value={granteeId}
              onChange={(e) => setGranteeId(e.target.value)}
              className="mt-1 w-full text-sm border border-slate-300 rounded px-2 py-1.5 bg-white"
              aria-label={granteeType === "user" ? "User to grant" : "Team to grant"}
            >
              <option value="">
                {granteeType === "user" ? "Select a user…" : "Select a team…"}
              </option>
              {granteeType === "user"
                ? users.map((u) => (
                    <option key={u.kc_id} value={u.kc_id}>
                      {userLabel(u.kc_id)}
                    </option>
                  ))
                : teams.map((t) => (
                    <option key={t.name} value={t.name}>
                      {t.name}
                    </option>
                  ))}
            </select>
          </label>

          <div className="flex justify-end gap-2">
            <button onClick={closeForm} className="btn-secondary text-xs py-1.5">
              Cancel
            </button>
            <button
              onClick={() => grant.mutate()}
              disabled={grant.isPending || !granteeId}
              className="btn-primary text-xs py-1.5 disabled:opacity-50"
            >
              {grant.isPending ? "Granting…" : "Grant"}
            </button>
          </div>
        </div>
      )}

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
