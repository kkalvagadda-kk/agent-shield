import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  Loader2,
  Plus,
  RefreshCw,
  Shield,
  ShieldOff,
  Trash2,
  UserCheck,
  UserPlus,
  Users,
  X,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import {
  createGrant,
  listGrants,
  listTools,
  listAgents,
  listSkills,
  listCompositeWorkflows,
  revokeGrant,
  type AssetGrant,
} from "../api/registryApi";

// ── API types ────────────────────────────────────────────────────────────────

interface User {
  kc_id: string;
  username: string;
  email: string;
  first_name: string;
  last_name: string;
  enabled: boolean;
  team: string | null;
  role: string | null;
  created_at: number | null;
}

interface TeamSummary {
  id: string;
  name: string;
  namespace: string;
  members: { user_sub: string; role: string }[];
  grants: { id: string; asset_type: string; asset_name: string; granted_at: string | null }[];
}

// ── API calls ────────────────────────────────────────────────────────────────

const API = "/api/v1";

async function fetchUsers(): Promise<User[]> {
  const r = await fetch(`${API}/admin/users`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

async function createUser(body: {
  username: string; email: string; first_name: string; last_name: string;
  temp_password: string; team: string; role: string;
}): Promise<User> {
  const r = await fetch(`${API}/admin/users`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: r.statusText }));
    throw new Error(err.detail ?? r.statusText);
  }
  return r.json();
}

async function patchUser(kc_id: string, body: Partial<{
  team: string; role: string; enabled: boolean; first_name: string; last_name: string;
}>): Promise<User> {
  const r = await fetch(`${API}/admin/users/${kc_id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: r.statusText }));
    throw new Error(err.detail ?? r.statusText);
  }
  return r.json();
}

async function deleteUser(kc_id: string): Promise<void> {
  const r = await fetch(`${API}/admin/users/${kc_id}`, { method: "DELETE" });
  if (!r.ok && r.status !== 404) throw new Error(await r.text());
}

async function resetPassword(kc_id: string, new_password: string): Promise<void> {
  const r = await fetch(`${API}/admin/users/${kc_id}/reset-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ new_password, temporary: true }),
  });
  if (!r.ok) throw new Error(await r.text());
}

async function fetchTeamsSummary(): Promise<TeamSummary[]> {
  const r = await fetch(`${API}/admin/teams-summary`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

// ── Constants ────────────────────────────────────────────────────────────────

const ROLES = ["admin", "operator", "viewer"] as const;
type Role = typeof ROLES[number];

const ROLE_CHIP: Record<string, string> = {
  admin:    "bg-red-50 text-red-700 border-red-200",
  operator: "bg-blue-50 text-blue-700 border-blue-200",
  viewer:   "bg-slate-100 text-slate-600 border-slate-200",
};

const TYPE_CHIP: Record<string, string> = {
  agent:    "bg-blue-50 text-blue-700",
  tool:     "bg-purple-50 text-purple-700",
  skill:    "bg-teal-50 text-teal-700",
  workflow: "bg-indigo-50 text-indigo-700",
};

function initials(u: User) {
  const f = u.first_name?.[0] ?? u.username?.[0] ?? "?";
  const l = u.last_name?.[0] ?? "";
  return (f + l).toUpperCase();
}

// ── Tab shell ────────────────────────────────────────────────────────────────

type Tab = "users" | "teams" | "grants";

export default function AdminAccessPage() {
  const [tab, setTab] = useState<Tab>("users");

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-slate-900">Access Control</h1>
        <p className="text-sm text-slate-500 mt-0.5">
          Manage users, team membership, and asset permissions
        </p>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-6 border-b border-slate-200">
        {(["users", "teams", "grants"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium capitalize border-b-2 transition-colors ${
              tab === t
                ? "border-blue-500 text-blue-600"
                : "border-transparent text-slate-500 hover:text-slate-700"
            }`}
          >
            {t === "users" && <Users size={13} className="inline mr-1.5 -mt-0.5" />}
            {t === "teams" && <Shield size={13} className="inline mr-1.5 -mt-0.5" />}
            {t === "grants" && <UserCheck size={13} className="inline mr-1.5 -mt-0.5" />}
            {t}
          </button>
        ))}
      </div>

      {tab === "users"  && <UsersTab />}
      {tab === "teams"  && <TeamsTab />}
      {tab === "grants" && <GrantsTab />}
    </div>
  );
}

// ── Users tab ────────────────────────────────────────────────────────────────

function UsersTab() {
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [resetId, setResetId] = useState<string | null>(null);

  const { data: users = [], isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["admin-users"],
    queryFn: fetchUsers,
  });

  const deleteMutation = useMutation({
    mutationFn: deleteUser,
    onSuccess: () => { toast.success("User deleted."); qc.invalidateQueries({ queryKey: ["admin-users"] }); },
    onError: (e: Error) => toast.error(e.message),
  });

  const toggleMutation = useMutation({
    mutationFn: ({ kc_id, enabled }: { kc_id: string; enabled: boolean }) =>
      patchUser(kc_id, { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-users"] }),
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <p className="text-sm text-slate-500">{users.length} user{users.length !== 1 ? "s" : ""}</p>
        <div className="flex gap-2">
          <button onClick={() => refetch()} disabled={isFetching} className="btn-secondary">
            <RefreshCw size={13} className={isFetching ? "animate-spin" : ""} />
            Refresh
          </button>
          <button onClick={() => setShowCreate(true)} className="btn-primary">
            <UserPlus size={13} />
            Create User
          </button>
        </div>
      </div>

      {showCreate && (
        <CreateUserModal
          onClose={() => setShowCreate(false)}
          onSuccess={() => { setShowCreate(false); qc.invalidateQueries({ queryKey: ["admin-users"] }); }}
        />
      )}
      {editId && (
        <EditUserModal
          user={users.find((u) => u.kc_id === editId)!}
          onClose={() => setEditId(null)}
          onSuccess={() => { setEditId(null); qc.invalidateQueries({ queryKey: ["admin-users"] }); }}
        />
      )}
      {resetId && (
        <ResetPasswordModal
          kc_id={resetId}
          onClose={() => setResetId(null)}
        />
      )}

      {isLoading && <Spinner label="Loading users…" />}
      {error && <ErrorBox msg={String(error)} />}

      {!isLoading && !error && (
        <div className="card p-0 overflow-hidden">
          {users.length === 0 ? (
            <EmptyState icon={<Users size={36} />} label="No users yet" hint="Create the first user above." />
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50">
                  {["User", "Team", "Role", "Status", "Actions"].map((h) => (
                    <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {users.map((u) => (
                  <tr key={u.kc_id} className="hover:bg-slate-50 transition-colors">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-full bg-blue-500 flex items-center justify-center text-white text-xs font-semibold shrink-0">
                          {initials(u)}
                        </div>
                        <div>
                          <p className="font-medium text-slate-900">
                            {u.first_name || u.last_name
                              ? `${u.first_name} ${u.last_name}`.trim()
                              : u.username}
                          </p>
                          <p className="text-xs text-slate-400">{u.email}</p>
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-slate-600">
                      {u.team ?? <span className="text-slate-400 italic">unassigned</span>}
                    </td>
                    <td className="px-4 py-3">
                      {u.role ? (
                        <span className={`badge border ${ROLE_CHIP[u.role] ?? "bg-slate-100 text-slate-600"}`}>
                          {u.role}
                        </span>
                      ) : (
                        <span className="text-slate-400 italic text-xs">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`badge ${u.enabled ? "bg-green-50 text-green-700" : "bg-red-50 text-red-600"}`}>
                        {u.enabled ? "active" : "disabled"}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => setEditId(u.kc_id)}
                          className="text-xs text-blue-600 hover:text-blue-800 font-medium"
                        >
                          Edit
                        </button>
                        <button
                          onClick={() => setResetId(u.kc_id)}
                          className="text-xs text-slate-500 hover:text-slate-700"
                        >
                          Reset pwd
                        </button>
                        <button
                          onClick={() => toggleMutation.mutate({ kc_id: u.kc_id, enabled: !u.enabled })}
                          className={`text-xs ${u.enabled ? "text-amber-600 hover:text-amber-800" : "text-green-600 hover:text-green-800"}`}
                        >
                          {u.enabled ? "Disable" : "Enable"}
                        </button>
                        <button
                          onClick={() => {
                            if (confirm(`Delete user "${u.username}"? This is permanent.`)) {
                              deleteMutation.mutate(u.kc_id);
                            }
                          }}
                          className="text-xs text-red-600 hover:text-red-800"
                        >
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}

// ── Create user modal ────────────────────────────────────────────────────────

function CreateUserModal({ onClose, onSuccess }: { onClose: () => void; onSuccess: () => void }) {
  const { data: teams = [] } = useQuery({ queryKey: ["admin-teams-summary"], queryFn: fetchTeamsSummary });
  const [form, setForm] = useState({
    username: "", email: "", first_name: "", last_name: "",
    temp_password: "", team: "", role: "operator" as Role,
  });

  const mutation = useMutation({
    mutationFn: () => createUser(form),
    onSuccess: () => { toast.success("User created. They'll be prompted to change their password on first login."); onSuccess(); },
    onError: (e: Error) => toast.error(e.message),
  });

  const f = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm({ ...form, [k]: e.target.value });

  const valid = form.username && form.email && form.temp_password && form.team;

  return (
    <Modal title="Create User" onClose={onClose}>
      <div className="grid grid-cols-2 gap-4">
        <Field label="Username *">
          <input className="input" placeholder="jsmith" value={form.username} onChange={f("username")} />
        </Field>
        <Field label="Email *">
          <input className="input" type="email" placeholder="j.smith@company.com" value={form.email} onChange={f("email")} />
        </Field>
        <Field label="First name">
          <input className="input" placeholder="Jane" value={form.first_name} onChange={f("first_name")} />
        </Field>
        <Field label="Last name">
          <input className="input" placeholder="Smith" value={form.last_name} onChange={f("last_name")} />
        </Field>
        <Field label="Temporary password *">
          <input className="input font-mono" type="password" placeholder="••••••••" value={form.temp_password} onChange={f("temp_password")} />
        </Field>
        <Field label="Team *">
          <select className="input" value={form.team} onChange={f("team")}>
            <option value="">— pick a team —</option>
            {teams.map((t) => <option key={t.id} value={t.name}>{t.name}</option>)}
          </select>
        </Field>
        <Field label="Role">
          <select className="input" value={form.role} onChange={f("role")}>
            {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </Field>
      </div>
      <p className="text-xs text-slate-400 mt-3">
        User will be created in Keycloak and must change their password on first login.
      </p>
      <div className="flex justify-end gap-2 mt-4 pt-4 border-t border-slate-100">
        <button onClick={onClose} className="btn-secondary">Cancel</button>
        <button
          onClick={() => mutation.mutate()}
          disabled={mutation.isPending || !valid}
          className="btn-primary disabled:opacity-50"
        >
          {mutation.isPending ? <><Loader2 size={13} className="animate-spin" /> Creating…</> : <><UserPlus size={13} /> Create User</>}
        </button>
      </div>
    </Modal>
  );
}

// ── Edit user modal ──────────────────────────────────────────────────────────

function EditUserModal({ user, onClose, onSuccess }: { user: User; onClose: () => void; onSuccess: () => void }) {
  const { data: teams = [] } = useQuery({ queryKey: ["admin-teams-summary"], queryFn: fetchTeamsSummary });
  const [form, setForm] = useState({
    first_name: user.first_name ?? "",
    last_name: user.last_name ?? "",
    team: user.team ?? "",
    role: (user.role ?? "operator") as Role,
  });

  const mutation = useMutation({
    mutationFn: () => patchUser(user.kc_id, form),
    onSuccess: () => { toast.success("User updated."); onSuccess(); },
    onError: (e: Error) => toast.error(e.message),
  });

  const f = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm({ ...form, [k]: e.target.value });

  return (
    <Modal title={`Edit — ${user.username}`} onClose={onClose}>
      <div className="grid grid-cols-2 gap-4">
        <Field label="First name">
          <input className="input" value={form.first_name} onChange={f("first_name")} />
        </Field>
        <Field label="Last name">
          <input className="input" value={form.last_name} onChange={f("last_name")} />
        </Field>
        <Field label="Team">
          <select className="input" value={form.team} onChange={f("team")}>
            <option value="">— unassigned —</option>
            {teams.map((t) => <option key={t.id} value={t.name}>{t.name}</option>)}
          </select>
        </Field>
        <Field label="Role">
          <select className="input" value={form.role} onChange={f("role")}>
            {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </Field>
      </div>
      <div className="flex justify-end gap-2 mt-4 pt-4 border-t border-slate-100">
        <button onClick={onClose} className="btn-secondary">Cancel</button>
        <button onClick={() => mutation.mutate()} disabled={mutation.isPending} className="btn-primary disabled:opacity-50">
          {mutation.isPending ? <><Loader2 size={13} className="animate-spin" /> Saving…</> : "Save Changes"}
        </button>
      </div>
    </Modal>
  );
}

// ── Reset password modal ─────────────────────────────────────────────────────

function ResetPasswordModal({ kc_id, onClose }: { kc_id: string; onClose: () => void }) {
  const [pwd, setPwd] = useState("");
  const mutation = useMutation({
    mutationFn: () => resetPassword(kc_id, pwd),
    onSuccess: () => { toast.success("Password reset. User will be forced to change it on next login."); onClose(); },
    onError: (e: Error) => toast.error(e.message),
  });
  return (
    <Modal title="Reset Password" onClose={onClose}>
      <Field label="New temporary password">
        <input className="input font-mono" type="password" placeholder="••••••••" value={pwd} onChange={(e) => setPwd(e.target.value)} />
      </Field>
      <p className="text-xs text-slate-400 mt-2">User must change this on next login.</p>
      <div className="flex justify-end gap-2 mt-4 pt-4 border-t border-slate-100">
        <button onClick={onClose} className="btn-secondary">Cancel</button>
        <button onClick={() => mutation.mutate()} disabled={mutation.isPending || !pwd} className="btn-primary disabled:opacity-50">
          {mutation.isPending ? <><Loader2 size={13} className="animate-spin" /> Resetting…</> : "Reset Password"}
        </button>
      </div>
    </Modal>
  );
}

// ── Teams tab ────────────────────────────────────────────────────────────────

function TeamsTab() {
  const { data: teams = [], isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["admin-teams-summary"],
    queryFn: fetchTeamsSummary,
  });
  const { data: users = [] } = useQuery({ queryKey: ["admin-users"], queryFn: fetchUsers });
  const userMap = Object.fromEntries(users.map((u) => [u.kc_id, u]));

  return (
    <div>
      <div className="flex justify-end mb-4">
        <button onClick={() => refetch()} disabled={isFetching} className="btn-secondary">
          <RefreshCw size={13} className={isFetching ? "animate-spin" : ""} /> Refresh
        </button>
      </div>
      {isLoading && <Spinner label="Loading teams…" />}
      {error && <ErrorBox msg={String(error)} />}
      {!isLoading && !error && (
        <div className="space-y-4">
          {teams.length === 0 && (
            <EmptyState icon={<Shield size={36} />} label="No teams" hint="Create teams via the Registry API." />
          )}
          {teams.map((t) => (
            <div key={t.id} className="card">
              <div className="flex items-start justify-between mb-3">
                <div>
                  <h3 className="font-semibold text-slate-900">{t.name}</h3>
                  <p className="text-xs text-slate-400 font-mono">{t.namespace}</p>
                </div>
                <div className="flex gap-4 text-xs text-slate-500">
                  <span>{t.members.length} member{t.members.length !== 1 ? "s" : ""}</span>
                  <span>{t.grants.length} grant{t.grants.length !== 1 ? "s" : ""}</span>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                {/* Members */}
                <div>
                  <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">Members</p>
                  {t.members.length === 0 ? (
                    <p className="text-xs text-slate-400 italic">No members assigned</p>
                  ) : (
                    <div className="space-y-1.5">
                      {t.members.map((m) => {
                        const u = userMap[m.user_sub];
                        return (
                          <div key={m.user_sub} className="flex items-center gap-2">
                            <div className="w-6 h-6 rounded-full bg-blue-100 flex items-center justify-center text-blue-700 text-xs font-semibold shrink-0">
                              {u ? initials(u) : "?"}
                            </div>
                            <div className="min-w-0">
                              <p className="text-xs font-medium text-slate-800 truncate">
                                {u ? (u.first_name ? `${u.first_name} ${u.last_name}`.trim() : u.username) : m.user_sub.slice(0, 8) + "…"}
                              </p>
                              {u && <p className="text-xs text-slate-400 truncate">{u.email}</p>}
                            </div>
                            <span className={`badge border ml-auto shrink-0 ${ROLE_CHIP[m.role] ?? "bg-slate-100 text-slate-600 border-slate-200"}`}>
                              {m.role}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>

                {/* Grants */}
                <div>
                  <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">Asset Access</p>
                  {t.grants.length === 0 ? (
                    <p className="text-xs text-slate-400 italic">No grants — team cannot access any assets</p>
                  ) : (
                    <div className="space-y-1.5">
                      {t.grants.map((g) => (
                        <div key={g.id} className="flex items-center gap-2">
                          <span className={`badge ${TYPE_CHIP[g.asset_type] ?? "bg-slate-100 text-slate-600"}`}>
                            {g.asset_type}
                          </span>
                          <span className="text-xs text-slate-700 font-medium truncate">{g.asset_name}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Grants tab ───────────────────────────────────────────────────────────────

function GrantsTab() {
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [typeFilter, setTypeFilter] = useState("");
  const [form, setForm] = useState({ asset_id: "", asset_type: "agent", grantee_team: "", expires_at: "" });

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["grants", typeFilter],
    queryFn: () => listGrants({ limit: 200 }),
  });

  const { data: agentsPage } = useQuery({ queryKey: ["agents"], queryFn: () => listAgents(100, 0, "active") });
  const { data: toolsPage } = useQuery({ queryKey: ["tools"], queryFn: () => listTools() });
  const { data: skillsPage } = useQuery({ queryKey: ["skills"], queryFn: () => listSkills() });
  const { data: workflows = [] } = useQuery({ queryKey: ["workflows-published"], queryFn: () => listCompositeWorkflows() });
  const { data: teams = [] } = useQuery({ queryKey: ["admin-teams-summary"], queryFn: fetchTeamsSummary });

  const agents = (agentsPage?.items ?? []).filter((a) => a.publish_status === "published");
  const tools = (toolsPage?.items ?? []).filter((t) => (t as { publish_status?: string }).publish_status === "published");
  const skills = (skillsPage?.items ?? []).filter((s) => (s as { publish_status?: string }).publish_status === "published");
  const publishedWorkflows = workflows.filter((w) => w.publish_status === "published");

  const assetOptions: { id: string; name: string }[] =
    form.asset_type === "agent"
      ? agents.map((a) => ({ id: a.id, name: a.name }))
      : form.asset_type === "tool"
      ? tools.map((t) => ({ id: t.id, name: t.name }))
      : form.asset_type === "skill"
      ? skills.map((s) => ({ id: s.id, name: s.name }))
      : form.asset_type === "workflow"
      ? publishedWorkflows.map((w) => ({ id: w.id, name: w.name }))
      : [];

  const revokeMutation = useMutation({
    mutationFn: (id: string) => revokeGrant(id),
    onSuccess: () => { toast.success("Grant revoked."); qc.invalidateQueries({ queryKey: ["grants"] }); },
    onError: (e: Error) => toast.error(e.message ?? "Failed to revoke grant."),
  });

  const createMutation = useMutation({
    mutationFn: () => createGrant({ asset_id: form.asset_id, asset_type: form.asset_type, grantee_team: form.grantee_team, expires_at: form.expires_at || undefined }),
    onSuccess: () => {
      toast.success("Grant created.");
      setShowCreate(false);
      setForm({ asset_id: "", asset_type: "agent", grantee_team: "", expires_at: "" });
      qc.invalidateQueries({ queryKey: ["grants"] });
    },
    onError: (e: Error) => toast.error(e.message ?? "Failed to create grant."),
  });

  const filtered = data?.items.filter((g) => !typeFilter || g.asset_type === typeFilter) ?? [];

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <select className="input text-sm w-36" value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
          <option value="">All types</option>
          <option value="agent">Agent</option>
          <option value="tool">Tool</option>
          <option value="skill">Skill</option>
          <option value="workflow">Workflow</option>
        </select>
        <div className="flex gap-2">
          <button onClick={() => refetch()} disabled={isFetching} className="btn-secondary">
            <RefreshCw size={13} className={isFetching ? "animate-spin" : ""} /> Refresh
          </button>
          <button onClick={() => setShowCreate((v) => !v)} className="btn-primary">
            {showCreate ? <X size={13} /> : <Plus size={13} />}
            {showCreate ? "Cancel" : "New Grant"}
          </button>
        </div>
      </div>

      {showCreate && (
        <div className="card mb-4">
          <h2 className="text-sm font-semibold text-slate-900 mb-4">Create Asset Grant</h2>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Asset type">
              <select className="input" value={form.asset_type} onChange={(e) => setForm({ ...form, asset_type: e.target.value, asset_id: "" })}>
                <option value="agent">Agent</option>
                <option value="tool">Tool</option>
                <option value="skill">Skill</option>
                <option value="workflow">Workflow</option>
              </select>
            </Field>
            <Field label="Asset">
              {assetOptions.length > 0 ? (
                <select className="input" value={form.asset_id} onChange={(e) => setForm({ ...form, asset_id: e.target.value })}>
                  <option value="">— pick an asset —</option>
                  {assetOptions.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
              ) : (
                <input className="input font-mono text-sm" placeholder="asset UUID" value={form.asset_id} onChange={(e) => setForm({ ...form, asset_id: e.target.value })} />
              )}
            </Field>
            <Field label="Grantee team">
              <select className="input" value={form.grantee_team} onChange={(e) => setForm({ ...form, grantee_team: e.target.value })}>
                <option value="">— pick a team —</option>
                {teams.map((t) => <option key={t.id} value={t.name}>{t.name}</option>)}
              </select>
            </Field>
            <Field label="Expires at (optional)">
              <input type="datetime-local" className="input" value={form.expires_at} onChange={(e) => setForm({ ...form, expires_at: e.target.value })} />
            </Field>
          </div>
          <div className="flex justify-end gap-2 mt-4 pt-4 border-t border-slate-100">
            <button onClick={() => setShowCreate(false)} className="btn-secondary">Cancel</button>
            <button onClick={() => createMutation.mutate()} disabled={createMutation.isPending || !form.asset_id || !form.grantee_team} className="btn-primary disabled:opacity-50">
              {createMutation.isPending ? <><Loader2 size={13} className="animate-spin" /> Creating…</> : "Create Grant"}
            </button>
          </div>
        </div>
      )}

      {isLoading && <Spinner label="Loading grants…" />}
      {error && <ErrorBox msg={String(error)} />}

      {data && (
        <div className="card p-0 overflow-hidden">
          {filtered.length === 0 ? (
            <EmptyState icon={<ShieldOff size={36} />} label="No active grants" hint="Create a grant to give a team access to an asset." />
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50">
                  {["Type", "Asset", "Grantee Team", "Granted", "Expires", ""].map((h) => (
                    <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {filtered.map((g) => (
                  <tr key={g.id} className="hover:bg-slate-50">
                    <td className="px-4 py-3"><span className={`badge ${TYPE_CHIP[g.asset_type] ?? "bg-slate-100 text-slate-600"}`}>{g.asset_type}</span></td>
                    <td className="px-4 py-3 font-mono text-xs text-slate-500">{g.asset_id.slice(0, 8)}…</td>
                    <td className="px-4 py-3 font-medium text-slate-800">{g.grantee_team}</td>
                    <td className="px-4 py-3 text-slate-400 text-xs">{new Date(g.granted_at).toLocaleDateString()}</td>
                    <td className="px-4 py-3 text-slate-400 text-xs">{g.expires_at ? new Date(g.expires_at).toLocaleDateString() : "—"}</td>
                    <td className="px-4 py-3">
                      <button onClick={() => { if (confirm(`Revoke grant for team "${g.grantee_team}"?`)) revokeMutation.mutate(g.id); }}
                        className="inline-flex items-center gap-1 text-xs text-red-600 hover:text-red-800">
                        <ShieldOff size={12} /> Revoke
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
      {data && (
        <p className="text-xs text-slate-400 mt-2 text-right">{filtered.length} of {data.total} grant{data.total !== 1 ? "s" : ""}</p>
      )}
    </div>
  );
}

// ── Shared UI helpers ────────────────────────────────────────────────────────

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg">
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100">
          <h2 className="font-semibold text-slate-900">{title}</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X size={16} /></button>
        </div>
        <div className="px-6 py-4">{children}</div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <label className="label">{label}</label>
      {children}
    </div>
  );
}

function Spinner({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center py-20 text-slate-400">
      <Loader2 size={20} className="animate-spin mr-2" />{label}
    </div>
  );
}

function ErrorBox({ msg }: { msg: string }) {
  return <div className="rounded-lg bg-red-50 border border-red-200 p-4 text-sm text-red-700">{msg}</div>;
}

function EmptyState({ icon, label, hint }: { icon: React.ReactNode; label: string; hint: string }) {
  return (
    <div className="flex flex-col items-center py-16 text-center">
      <div className="text-slate-300 mb-3">{icon}</div>
      <p className="text-slate-500 font-medium">{label}</p>
      <p className="text-slate-400 text-sm mt-1">{hint}</p>
    </div>
  );
}
