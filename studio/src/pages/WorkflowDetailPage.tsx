import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle, ExternalLink, GitBranch, Loader2, Pause, Play, Rocket, Trash2, XCircle } from "lucide-react";
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
  updateWorkflowDeployment,
  type DeploymentAction,
  type WorkflowDeployment,
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
        <DeploymentsTab workflowId={id!} deployments={deployments} versions={versions} />
      )}
      {activeTab === "versions" && (
        <VersionsTab workflowId={id!} versions={versions} />
      )}
      {activeTab === "settings" && (
        <SettingsTab workflow={workflow} />
      )}
    </div>
  );
}

function DeploymentsTab({
  workflowId,
  deployments,
  versions,
}: {
  workflowId: string;
  deployments: WorkflowDeployment[];
  versions: WorkflowVersion[];
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

function SettingsTab({ workflow }: { workflow: { orchestration: string; execution_shape: string; memory_enabled: boolean; team: string; created_at: string; created_by: string | null; description: string | null } }) {
  return (
    <div className="card p-5 space-y-4">
      <h3 className="text-sm font-semibold text-slate-700 mb-3">Workflow Configuration</h3>
      <div className="grid grid-cols-2 gap-4 text-sm">
        <div>
          <p className="text-xs text-slate-400 uppercase mb-0.5">Orchestration</p>
          <p className="font-mono text-slate-700">{workflow.orchestration}</p>
        </div>
        <div>
          <p className="text-xs text-slate-400 uppercase mb-0.5">Execution Shape</p>
          <p className="font-mono text-slate-700">{workflow.execution_shape}</p>
        </div>
        <div>
          <p className="text-xs text-slate-400 uppercase mb-0.5">Team</p>
          <p className="text-slate-700">{workflow.team}</p>
        </div>
        <div>
          <p className="text-xs text-slate-400 uppercase mb-0.5">Memory</p>
          <p className="text-slate-700">{workflow.memory_enabled ? "Enabled" : "Disabled"}</p>
        </div>
        <div>
          <p className="text-xs text-slate-400 uppercase mb-0.5">Created</p>
          <p className="text-slate-700">{new Date(workflow.created_at).toLocaleString()}</p>
        </div>
        {workflow.created_by && (
          <div>
            <p className="text-xs text-slate-400 uppercase mb-0.5">Created By</p>
            <p className="font-mono text-slate-700">{workflow.created_by}</p>
          </div>
        )}
        {workflow.description && (
          <div className="col-span-2">
            <p className="text-xs text-slate-400 uppercase mb-0.5">Description</p>
            <p className="text-slate-700">{workflow.description}</p>
          </div>
        )}
      </div>
    </div>
  );
}
