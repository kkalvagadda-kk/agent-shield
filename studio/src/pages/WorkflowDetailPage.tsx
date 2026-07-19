import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle, ExternalLink, GitBranch, Loader2, MessageCircle, Pause, Play, Rocket, Trash2, XCircle } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import {
  createWorkflowVersion,
  deleteWorkflowVersion,
  deployWorkflow,
  getCompositeWorkflow,
  listWorkflowDeployments,
  listWorkflowVersions,
  updateCompositeWorkflow,
  updateWorkflowDeployment,
  type CompositeWorkflowWithMembers,
  type DeploymentAction,
  type WorkflowDeployment,
  type WorkflowOrchestration,
  type WorkflowVersion,
} from "../api/registryApi";
import WorkflowMiniGraph from "../components/WorkflowMiniGraph";

const STATUS: Record<string, { label: string; cls: string }> = {
  pending: { label: "Pending", cls: "bg-amber-100 text-amber-700" },
  deploying: { label: "Deploying", cls: "bg-blue-100 text-blue-700" },
  running: { label: "Running", cls: "bg-green-100 text-green-700" },
  failed: { label: "Failed", cls: "bg-red-100 text-red-700" },
  terminated: { label: "Terminated", cls: "bg-slate-100 text-slate-600" },
  suspended: { label: "Suspended", cls: "bg-amber-100 text-amber-700" },
};

type Tab = "deployments" | "versions" | "settings";

export default function WorkflowDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [activeTab, setActiveTab] = useState<Tab>("deployments");

  const { data: workflow, isLoading, error } = useQuery({
    queryKey: ["workflow", id],
    queryFn: () => getCompositeWorkflow(id!),
    enabled: !!id,
  });

  const { data: deployments = [] } = useQuery({
    queryKey: ["workflow-deployments", id],
    queryFn: () => listWorkflowDeployments(id!),
    enabled: !!id,
    refetchInterval: 5_000,
  });

  const { data: versions = [] } = useQuery({
    queryKey: ["workflow-versions", id],
    queryFn: () => listWorkflowVersions(id!),
    enabled: !!id,
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20 text-slate-400">
        <Loader2 size={20} className="animate-spin mr-2" /> Loading workflow…
      </div>
    );
  }

  if (error || !workflow) {
    return (
      <div className="max-w-3xl mx-auto px-6 py-8">
        <div className="rounded-lg bg-red-50 border border-red-200 p-4 text-sm text-red-700">
          {error ? `Failed to load workflow: ${String(error)}` : "Workflow not found."}
        </div>
      </div>
    );
  }

  const orch = workflow.orchestration;

  return (
    <div className="max-w-4xl mx-auto px-6 py-8">
      <button
        onClick={() => navigate("/workflows")}
        className="inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-800 mb-6 transition-colors"
      >
        <ArrowLeft size={14} /> All Workflows
      </button>

      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-purple-100 flex items-center justify-center shrink-0">
            <GitBranch size={18} className="text-purple-600" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-slate-900 font-mono">{workflow.name}</h1>
            <div className="flex items-center gap-2 mt-1">
              <span className="badge text-xs bg-purple-100 text-purple-700">{orch}</span>
              <span className="badge text-xs bg-slate-100 text-slate-600">{workflow.member_count} members</span>
            </div>
          </div>
        </div>
        <Link
          to={`/workflows/${id}/builder`}
          className="btn-secondary text-xs py-1.5 inline-flex items-center gap-1"
        >
          <ExternalLink size={12} /> Open Builder
        </Link>
      </div>

      {/* Tabs */}
      <div className="border-b border-slate-200 mb-6">
        <nav className="flex gap-6">
          {(["deployments", "versions", "settings"] as Tab[]).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`pb-2 text-sm font-medium capitalize transition-colors ${
                activeTab === tab
                  ? "border-b-2 border-purple-500 text-purple-600"
                  : "text-slate-500 hover:text-slate-700"
              }`}
            >
              {tab}
            </button>
          ))}
        </nav>
      </div>

      {activeTab === "deployments" && (
        <DeploymentsTab
          workflowId={id!}
          deployments={deployments}
          versions={versions}
          isReactive={workflow?.execution_shape === "reactive"}
        />
      )}
      {activeTab === "versions" && (
        <VersionsTab workflowId={id!} versions={versions} />
      )}
      {activeTab === "settings" && (
        <SettingsTab id={id!} workflow={workflow} />
      )}
    </div>
  );
}

function DeploymentsTab({
  workflowId,
  deployments,
  versions,
  isReactive,
}: {
  workflowId: string;
  deployments: WorkflowDeployment[];
  versions: WorkflowVersion[];
  isReactive?: boolean;
}) {
  const qc = useQueryClient();
  const verMap = new Map(versions.map((v) => [v.id, v.version_number]));
  const sandbox = deployments.filter((d) => d.environment === "sandbox");

  const handleAction = async (depId: string, action: DeploymentAction) => {
    try {
      await updateWorkflowDeployment(workflowId, depId, action);
      toast.success(`${action} applied`);
      qc.invalidateQueries({ queryKey: ["workflow-deployments", workflowId] });
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "Action failed");
    }
  };

  if (sandbox.length === 0) {
    return (
      <div className="card p-6 text-center">
        <p className="text-slate-500 text-sm">No sandbox deployments yet.</p>
        <p className="text-xs text-slate-400 mt-1">Create a version first, then deploy it from the Versions tab.</p>
      </div>
    );
  }

  return (
    <div className="card overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-slate-50 text-left text-xs text-slate-500 uppercase">
            <th className="px-4 py-2">Name</th>
            <th className="px-4 py-2">Version</th>
            <th className="px-4 py-2">Status</th>
            <th className="px-4 py-2">Deployed</th>
            <th className="px-4 py-2 text-right">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {sandbox.map((d) => {
            const st = STATUS[d.status] ?? { label: d.status, cls: "bg-slate-100 text-slate-600" };
            return (
              <tr key={d.id} className="hover:bg-slate-50/50">
                <td className="px-4 py-2.5">
                  <Link
                    to={`/workflows/${workflowId}/d/${d.id}`}
                    className="font-medium text-purple-600 hover:text-purple-800 font-mono text-xs"
                  >
                    {d.name ?? d.id.slice(0, 8)}
                  </Link>
                </td>
                <td className="px-4 py-2.5 font-mono text-xs">v{verMap.get(d.version_id) ?? "?"}</td>
                <td className="px-4 py-2.5"><span className={`badge text-xs ${st.cls}`}>{st.label}</span></td>
                <td className="px-4 py-2.5 text-slate-500 text-xs">{new Date(d.deployed_at).toLocaleDateString()}</td>
                <td className="px-4 py-2.5 text-right">
                  <div className="inline-flex items-center gap-1">
                    {isReactive && d.status === "running" && (
                      <Link
                        to={`/workflows/${workflowId}/d/${d.id}/chat`}
                        className="p-1.5 rounded-md hover:bg-purple-50 text-purple-600"
                        title="Open Chat"
                        data-testid="workflow-row-open-chat"
                      >
                        <MessageCircle size={14} />
                      </Link>
                    )}
                    {d.status === "running" && (
                      <button onClick={() => handleAction(d.id, "suspend")} className="p-1.5 rounded-md hover:bg-slate-100 text-amber-600" title="Suspend">
                        <Pause size={14} />
                      </button>
                    )}
                    {d.status === "suspended" && (
                      <button onClick={() => handleAction(d.id, "resume")} className="p-1.5 rounded-md hover:bg-slate-100 text-green-600" title="Resume">
                        <Play size={14} />
                      </button>
                    )}
                    {(d.status === "running" || d.status === "suspended") && (
                      <button onClick={() => handleAction(d.id, "terminate")} className="p-1.5 rounded-md hover:bg-red-50 text-red-500" title="Terminate">
                        <Trash2 size={14} />
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function VersionsTab({ workflowId, versions }: { workflowId: string; versions: WorkflowVersion[] }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [deployingVer, setDeployingVer] = useState<WorkflowVersion | null>(null);
  const [ttlHours, setTtlHours] = useState("");

  const snapshotMut = useMutation({
    mutationFn: () => createWorkflowVersion(workflowId, {}),
    onSuccess: (v) => {
      toast.success(`Version v${v.version_number} created.`);
      qc.invalidateQueries({ queryKey: ["workflow-versions", workflowId] });
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : "Failed to create version."),
  });

  const deleteMut = useMutation({
    mutationFn: (versionId: string) => deleteWorkflowVersion(workflowId, versionId),
    onSuccess: (res) => {
      const msg = res.terminated_deployments > 0
        ? `Deleted version. ${res.terminated_deployments} deployment(s) terminated.`
        : "Version deleted.";
      toast.success(msg);
      qc.invalidateQueries({ queryKey: ["workflow-versions", workflowId] });
      qc.invalidateQueries({ queryKey: ["workflow-deployments", workflowId] });
    },
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail ?? "Failed to delete version.");
    },
  });

  const deployMut = useMutation({
    mutationFn: (ver: WorkflowVersion) =>
      deployWorkflow(workflowId, {
        version_id: ver.id,
        environment: "sandbox",
        ttl_hours: ttlHours ? Number(ttlHours) : undefined,
      }),
    onSuccess: (dep) => {
      toast.success(`Deploying workflow…`);
      setDeployingVer(null);
      qc.invalidateQueries({ queryKey: ["workflow-deployments", workflowId] });
      navigate(`/workflows/${workflowId}/d/${dep.id}`);
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : "Deploy failed."),
  });

  const handleDelete = (v: WorkflowVersion) => {
    if (!confirm(`Delete version v${v.version_number}? Sandbox deployments using this version will be terminated.`)) return;
    deleteMut.mutate(v.id);
  };

  if (versions.length === 0) {
    return (
      <div className="card p-6 text-center">
        <p className="text-slate-500 text-sm mb-4">No versions yet. Configure members in the Builder, then snapshot.</p>
        <button
          onClick={() => snapshotMut.mutate()}
          disabled={snapshotMut.isPending}
          className="btn-primary text-sm"
        >
          {snapshotMut.isPending ? (
            <><Loader2 size={13} className="animate-spin" /> Creating…</>
          ) : (
            <><GitBranch size={13} /> Snapshot Version</>
          )}
        </button>
      </div>
    );
  }

  return (
    <>
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs text-slate-500">{versions.length} version(s)</span>
        <button
          onClick={() => snapshotMut.mutate()}
          disabled={snapshotMut.isPending}
          className="btn-secondary text-xs"
        >
          {snapshotMut.isPending ? (
            <><Loader2 size={11} className="animate-spin" /> Creating…</>
          ) : (
            <><GitBranch size={11} /> Snapshot Version</>
          )}
        </button>
      </div>
      <div className="card overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-slate-50 text-left text-xs text-slate-500 uppercase">
              <th className="px-4 py-2">Version</th>
              <th className="px-4 py-2">Orchestration</th>
              <th className="px-4 py-2">Members</th>
              <th className="px-4 py-2">Created</th>
              <th className="px-4 py-2 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {versions.map((v) => (
              <tr key={v.id} className="hover:bg-slate-50/50">
                <td className="px-4 py-2.5 font-mono font-medium text-slate-800">v{v.version_number}</td>
                <td className="px-4 py-2.5 text-slate-600">{v.orchestration}</td>
                <td className="px-4 py-2.5 text-slate-600">{(v.members as unknown[]).length}</td>
                <td className="px-4 py-2.5 text-slate-500">{new Date(v.created_at).toLocaleDateString()}</td>
                <td className="px-4 py-2.5 text-right">
                  <div className="inline-flex items-center gap-2">
                    <button
                      onClick={() => { setDeployingVer(v); setTtlHours(""); }}
                      className="btn-secondary text-xs py-1 px-2"
                    >
                      <Rocket size={11} /> Deploy
                    </button>
                    <button
                      onClick={() => handleDelete(v)}
                      disabled={deleteMut.isPending}
                      className="text-red-400 hover:text-red-600 disabled:opacity-40 p-1"
                      title="Delete version"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {deployingVer && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setDeployingVer(null)}>
          <div className="card w-full max-w-md p-6 bg-white" onClick={(e) => e.stopPropagation()}>
            <h2 className="text-lg font-semibold text-slate-900 mb-4">
              Deploy workflow v{deployingVer.version_number}
            </h2>
            <label className="block text-sm text-slate-600 mb-1">TTL (hours, optional)</label>
            <input
              type="number"
              min={1}
              value={ttlHours}
              onChange={(e) => setTtlHours(e.target.value)}
              placeholder="No auto-terminate"
              className="input mb-4 w-full"
            />
            <div className="flex justify-end gap-2">
              <button onClick={() => setDeployingVer(null)} className="btn-secondary text-sm">Cancel</button>
              <button
                onClick={() => deployMut.mutate(deployingVer)}
                disabled={deployMut.isPending}
                className="btn-primary text-sm"
              >
                {deployMut.isPending ? <Loader2 size={14} className="animate-spin" /> : <Rocket size={14} />} Deploy
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// Editable workflow configuration. The Save Workflow dialog sets these at
// creation but they were read-only afterward — this closes that gap (backend
// PATCH /workflows/{id} already accepts execution_shape / agent_class /
// orchestration / memory_enabled / description). Option labels mirror the
// create dialog (WorkflowBuilderPage) so the two surfaces stay consistent.
function SettingsTab({ id, workflow }: { id: string; workflow: CompositeWorkflowWithMembers }) {
  const qc = useQueryClient();
  const [executionShape, setExecutionShape] = useState<"reactive" | "durable">(workflow.execution_shape);
  const [agentClass, setAgentClass] = useState<"user_delegated" | "daemon">(workflow.agent_class);
  const [orchestration, setOrchestration] = useState<WorkflowOrchestration>(workflow.orchestration);
  const [memoryEnabled, setMemoryEnabled] = useState(workflow.memory_enabled);
  const [description, setDescription] = useState(workflow.description ?? "");

  const dirty =
    executionShape !== workflow.execution_shape ||
    agentClass !== workflow.agent_class ||
    orchestration !== workflow.orchestration ||
    memoryEnabled !== workflow.memory_enabled ||
    description !== (workflow.description ?? "");

  const save = useMutation({
    mutationFn: () =>
      updateCompositeWorkflow(id, {
        execution_shape: executionShape,
        agent_class: agentClass,
        orchestration,
        memory_enabled: memoryEnabled,
        description: description || undefined,
      }),
    onSuccess: () => {
      toast.success("Workflow settings saved");
      qc.invalidateQueries({ queryKey: ["workflow", id] });
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : "Save failed"),
  });

  return (
    <div className="card p-5 space-y-5" data-testid="workflow-settings">
      <h3 className="text-sm font-semibold text-slate-700">Workflow Configuration</h3>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label className="label" htmlFor="wf-shape">Execution Shape</label>
          <select
            id="wf-shape"
            className="input"
            value={executionShape}
            onChange={(e) => setExecutionShape(e.target.value as "reactive" | "durable")}
          >
            <option value="reactive">Ephemeral (fast, stateless request/response)</option>
            <option value="durable">Durable (long-running, resumable, HITL)</option>
          </select>
        </div>

        <div>
          <label className="label" htmlFor="wf-class">Authority (class)</label>
          <select
            id="wf-class"
            className="input"
            value={agentClass}
            onChange={(e) => setAgentClass(e.target.value as "user_delegated" | "daemon")}
          >
            <option value="user_delegated">User-delegated (runs under the invoking user)</option>
            <option value="daemon">Daemon (service identity, no live user)</option>
          </select>
        </div>

        <div>
          <label className="label" htmlFor="wf-orch">Orchestration Mode</label>
          <select
            id="wf-orch"
            className="input"
            value={orchestration}
            onChange={(e) => setOrchestration(e.target.value as WorkflowOrchestration)}
          >
            <option value="sequential">Sequential</option>
            <option value="conditional">Conditional (edge conditions route)</option>
            <option value="supervisor">Supervisor (a coordinator routes)</option>
            <option value="handoff">Handoff (agents pass control)</option>
          </select>
        </div>

        <div className="flex items-end">
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              className="mt-1"
              checked={memoryEnabled}
              onChange={(e) => setMemoryEnabled(e.target.checked)}
            />
            <span>
              <span className="label mb-0">Share context between agents</span>
              <span className="block text-xs text-slate-400">Members see each other&apos;s turns in a shared thread.</span>
            </span>
          </label>
        </div>
      </div>

      <div>
        <label className="label" htmlFor="wf-desc">Description <span className="text-slate-400 font-normal">(optional)</span></label>
        <textarea
          id="wf-desc"
          className="input resize-none"
          rows={2}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="What does this workflow do?"
        />
      </div>

      {/* Read-only provenance */}
      <div className="grid grid-cols-2 gap-4 text-sm border-t border-slate-100 pt-4">
        <div>
          <p className="text-xs text-slate-400 uppercase mb-0.5">Team</p>
          <p className="text-slate-700">{workflow.team}</p>
        </div>
        <div>
          <p className="text-xs text-slate-400 uppercase mb-0.5">Created</p>
          <p className="text-slate-700">{new Date(workflow.created_at).toLocaleString()}</p>
        </div>
      </div>

      <div className="flex justify-end pt-1">
        <button
          onClick={() => save.mutate()}
          disabled={!dirty || save.isPending}
          className="btn-primary text-sm disabled:opacity-50"
          data-testid="workflow-settings-save"
        >
          {save.isPending ? <Loader2 size={14} className="animate-spin" /> : null}
          {save.isPending ? "Saving…" : "Save Changes"}
        </button>
      </div>
    </div>
  );
}
