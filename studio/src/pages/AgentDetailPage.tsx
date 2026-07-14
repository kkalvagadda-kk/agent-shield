import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Bot, CheckCircle, Loader2, MessageCircle, Rocket, Send, Trash2, XCircle } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { deleteAgentVersion, getAgent, getDeployments, listProviders, listTools, listVersions, publishAgent, updateAgent, type Agent, type AgentVersion, type Deployment } from "../api/registryApi";
import DeployModal from "../components/DeployModal";
import DeploymentActions from "../components/agent-detail/DeploymentActions";
import SettingsTab from "../components/agent-detail/SettingsTab";
import { shapeLabel } from "../lib/utils";

const PUBLISH_STATUS: Record<string, { label: string; cls: string }> = {
  private:        { label: "Private",        cls: "bg-slate-100 text-slate-600" },
  pending_review: { label: "Pending Review", cls: "bg-amber-100 text-amber-700" },
  published:      { label: "Published",      cls: "bg-green-100 text-green-700" },
};

const OP_STATUS: Record<string, { label: string; cls: string }> = {
  active:      { label: "Active",      cls: "bg-green-100 text-green-700" },
  archived:    { label: "Archived",    cls: "bg-slate-100 text-slate-600" },
  deprecated:  { label: "Deprecated", cls: "bg-amber-100 text-amber-700" },
  quarantined: { label: "Quarantined", cls: "bg-red-100 text-red-700" },
};

type Tab = "deployments" | "versions" | "settings";

export default function AgentDetailPage() {
  const { name } = useParams<{ name: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [activeTab, setActiveTab] = useState<Tab>("deployments");
  const [showDeployModal, setShowDeployModal] = useState(false);

  const { data: agent, isLoading, error } = useQuery({
    queryKey: ["agent", name],
    queryFn: () => getAgent(name!),
    enabled: !!name,
  });

  const { data: versions = [] } = useQuery({
    queryKey: ["versions", name],
    queryFn: () => listVersions(name!),
    enabled: !!name,
  });
  const latestVersion = versions.length > 0 ? versions[0] : null;
  const evalGatePassed = latestVersion?.eval_passed === true;

  const publishMutation = useMutation({
    mutationFn: () => publishAgent(name!),
    onSuccess: (result) => {
      toast.success(`Publish request submitted (id: ${result.publish_request_id.slice(0, 8)}…)`);
      qc.invalidateQueries({ queryKey: ["agent", name] });
      qc.invalidateQueries({ queryKey: ["agents"] });
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: unknown } } })
        ?.response?.data?.detail;
      if (detail && typeof detail === "object" && "error" in detail) {
        const errCode = (detail as { error: string }).error;
        if (errCode === "critical_risk_not_publishable") {
          toast.error("Cannot publish: agent has a critical-risk tool assigned.");
          return;
        }
      }
      toast.error(typeof detail === "string" ? detail : "Failed to submit publish request.");
    },
  });

  const handlePublish = () => {
    if (agent?.publish_status === "pending_review") {
      toast.info("A publish request is already pending review.");
      return;
    }
    if (agent?.publish_status === "published") {
      toast.info("This agent is already published.");
      return;
    }
    if (confirm(`Submit agent "${name}" for publish review?`)) {
      publishMutation.mutate();
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20 text-slate-400">
        <Loader2 size={20} className="animate-spin mr-2" />
        Loading agent…
      </div>
    );
  }

  if (error || !agent) {
    return (
      <div className="max-w-3xl mx-auto px-6 py-8">
        <div className="rounded-lg bg-red-50 border border-red-200 p-4 text-sm text-red-700">
          {error ? `Failed to load agent: ${String(error)}` : "Agent not found."}
        </div>
      </div>
    );
  }

  const ps = PUBLISH_STATUS[agent.publish_status ?? "private"] ??
    { label: agent.publish_status, cls: "bg-slate-100 text-slate-600" };
  const os = OP_STATUS[agent.status] ??
    { label: agent.status, cls: "bg-slate-100 text-slate-600" };

  return (
    <div className="max-w-4xl mx-auto px-6 py-8">
      {/* Back */}
      <button
        onClick={() => navigate("/")}
        className="inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-800 mb-6 transition-colors"
      >
        <ArrowLeft size={14} />
        All Agents
      </button>

      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-blue-100 flex items-center justify-center shrink-0">
            <Bot size={18} className="text-blue-600" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-slate-900 font-mono">{agent.name}</h1>
            <div className="flex items-center gap-2 mt-1">
              <span className={`badge text-xs ${os.cls}`}>{os.label}</span>
              <span className={`badge text-xs ${ps.cls}`}>{ps.label}</span>
              <span className={`badge text-xs ${agent.execution_shape === "durable" ? "bg-purple-100 text-purple-700" : "bg-sky-100 text-sky-700"}`}>
                {shapeLabel(agent.execution_shape)}
              </span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowDeployModal(true)}
            className="btn-secondary text-xs py-1.5"
          >
            <Rocket size={12} />
            Deploy
          </button>
          <div className="relative group">
            <button
              onClick={handlePublish}
              disabled={
                publishMutation.isPending ||
                agent.publish_status === "pending_review" ||
                agent.publish_status === "published" ||
                !evalGatePassed
              }
              className="btn-primary text-xs py-1.5 disabled:opacity-50"
            >
              {publishMutation.isPending ? (
                <><Loader2 size={12} className="animate-spin" /> Submitting…</>
              ) : (
                <><Send size={12} /> Publish</>
              )}
            </button>
            {!evalGatePassed && agent.publish_status !== "published" && (
              <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 hidden group-hover:block z-10">
                <div className="bg-slate-800 text-white text-xs rounded px-2 py-1 whitespace-nowrap">
                  Run an eval that passes before publishing
                </div>
              </div>
            )}
          </div>
        </div>
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
                  ? "border-b-2 border-blue-500 text-blue-600"
                  : "text-slate-500 hover:text-slate-700"
              }`}
            >
              {tab}
            </button>
          ))}
        </nav>
      </div>

      {/* Tab content */}
      {activeTab === "deployments" && (
        <SandboxDeploymentsTab agentName={agent.name} />
      )}
      {activeTab === "versions" && (
        <VersionsContent agentName={agent.name} />
      )}
      {activeTab === "settings" && (
        <div className="space-y-4">
          <SettingsContent agent={agent} />
          <SettingsTab agentName={agent.name} memoryEnabled={agent.memory_enabled} />
        </div>
      )}

      {showDeployModal && (
        <DeployModal
          agentName={agent.name}
          onClose={() => setShowDeployModal(false)}
          onDeployed={() => {
            qc.invalidateQueries({ queryKey: ["deployments", name] });
            qc.invalidateQueries({ queryKey: ["versions", name] });
          }}
        />
      )}
    </div>
  );
}

const DEP_STATUS: Record<string, { label: string; cls: string }> = {
  pending: { label: "Pending", cls: "bg-amber-100 text-amber-700" },
  deploying: { label: "Deploying", cls: "bg-blue-100 text-blue-700" },
  running: { label: "Running", cls: "bg-green-100 text-green-700" },
  failed: { label: "Failed", cls: "bg-red-100 text-red-700" },
  rolled_back: { label: "Rolled back", cls: "bg-slate-100 text-slate-600" },
  terminated: { label: "Terminated", cls: "bg-slate-100 text-slate-600" },
  gate_failed: { label: "Gate failed", cls: "bg-red-100 text-red-700" },
};

// Sandbox deployments list — clicking a deployment name opens its overview.
// Metrics/runs/memory live on the deployment, not here.
function SandboxDeploymentsTab({ agentName }: { agentName: string }) {
  const { data: deployments = [], isLoading } = useQuery({
    queryKey: ["deployments", agentName],
    queryFn: () => getDeployments(agentName),
    refetchInterval: 5_000,
  });
  const { data: versions = [] } = useQuery({
    queryKey: ["versions", agentName],
    queryFn: () => listVersions(agentName),
  });
  const verMap = new Map(versions.map((v) => [v.id, v.version_number]));
  const sandbox = deployments.filter((d: Deployment) => d.environment === "sandbox");

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12 text-slate-400">
        <Loader2 size={16} className="animate-spin mr-2" /> Loading deployments…
      </div>
    );
  }

  if (sandbox.length === 0) {
    return (
      <div className="card p-6 text-center">
        <p className="text-sm text-slate-500 mb-1">No sandbox deployments yet.</p>
        <p className="text-xs text-slate-400">
          Deploy this agent to a sandbox to evaluate it before publishing.
        </p>
      </div>
    );
  }

  return (
    <div className="border border-slate-200 rounded-lg overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-slate-50 text-left text-xs text-slate-500 uppercase tracking-wider">
          <tr>
            <th className="px-4 py-2">Deployment</th>
            <th className="px-4 py-2">Version</th>
            <th className="px-4 py-2">Status</th>
            <th className="px-4 py-2">Created</th>
            <th className="px-4 py-2 text-right">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {sandbox.map((d: Deployment) => {
            const s = DEP_STATUS[d.status] ?? { label: d.status, cls: "bg-slate-100 text-slate-600" };
            const vn = verMap.get(d.version_id);
            return (
              <tr key={d.id} className="hover:bg-slate-50">
                <td className="px-4 py-2.5">
                  <Link
                    to={`/agents/${agentName}/d/${d.id}`}
                    className="font-mono text-blue-600 hover:text-blue-800 hover:underline"
                  >
                    {d.name ?? `${agentName}-${d.id.slice(0, 4)}`}
                  </Link>
                </td>
                <td className="px-4 py-2.5 font-mono text-xs text-slate-500">
                  {vn != null ? `v${vn}` : "—"}
                </td>
                <td className="px-4 py-2.5">
                  <span className={`badge text-xs ${s.cls}`}>{s.label}</span>
                </td>
                <td className="px-4 py-2.5 text-xs text-slate-500">
                  {new Date(d.deployed_at).toLocaleString()}
                </td>
                <td className="px-4 py-2.5">
                  <div className="flex items-center justify-end gap-2">
                    {d.status === "running" && (
                      <Link
                        to={`/agents/${agentName}/chat`}
                        className="text-xs font-medium text-blue-600 hover:text-blue-800 inline-flex items-center gap-1"
                      >
                        <MessageCircle size={12} /> Chat
                      </Link>
                    )}
                    <DeploymentActions agentName={agentName} deployment={d} />
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

function VersionsContent({ agentName }: { agentName: string }) {
  const qc = useQueryClient();
  const [deployVer, setDeployVer] = useState<AgentVersion | null>(null);

  const { data: versions = [], isLoading } = useQuery({
    queryKey: ["versions", agentName],
    queryFn: () => listVersions(agentName),
  });

  const deleteMut = useMutation({
    mutationFn: (versionId: string) => deleteAgentVersion(agentName, versionId),
    onSuccess: (res) => {
      const msg = res.terminated_deployments > 0
        ? `Deleted version. ${res.terminated_deployments} sandbox deployment(s) terminated.`
        : "Version deleted.";
      toast.success(msg);
      qc.invalidateQueries({ queryKey: ["versions", agentName] });
      qc.invalidateQueries({ queryKey: ["deployments", agentName] });
    },
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail ?? "Failed to delete version.");
    },
  });

  const handleDelete = (v: AgentVersion) => {
    if (!confirm(`Delete version v${v.version_number}? Any sandbox deployments using this version will be terminated.`)) return;
    deleteMut.mutate(v.id);
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12 text-slate-400">
        <Loader2 size={16} className="animate-spin mr-2" /> Loading versions…
      </div>
    );
  }

  if (versions.length === 0) {
    return (
      <div className="card p-6 text-center">
        <p className="text-slate-500 text-sm">No versions yet. Create one from the Deploy flow.</p>
      </div>
    );
  }

  return (
    <>
      <div className="card overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-slate-50 text-left text-xs text-slate-500 uppercase">
              <th className="px-4 py-2">Version</th>
              <th className="px-4 py-2">Eval</th>
              <th className="px-4 py-2">Created</th>
              <th className="px-4 py-2">Notes</th>
              <th className="px-4 py-2 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {versions.map((v) => (
              <tr key={v.id} className="hover:bg-slate-50/50">
                <td className="px-4 py-2.5 font-mono font-medium text-slate-800">v{v.version_number}</td>
                <td className="px-4 py-2.5">
                  {v.eval_passed ? (
                    <span className="inline-flex items-center gap-1 text-green-600"><CheckCircle size={13} /> Passed</span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-slate-400"><XCircle size={13} /> Not passed</span>
                  )}
                </td>
                <td className="px-4 py-2.5 text-slate-500">{new Date(v.created_at).toLocaleDateString()}</td>
                <td className="px-4 py-2.5 text-slate-500 truncate max-w-[180px]">{v.notes || "—"}</td>
                <td className="px-4 py-2.5 text-right">
                  <div className="inline-flex items-center gap-2">
                    <button
                      onClick={() => setDeployVer(v)}
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

      {deployVer && (
        <DeployModal
          agentName={agentName}
          versionId={deployVer.id}
          versionLabel={`v${deployVer.version_number}`}
          onClose={() => setDeployVer(null)}
        />
      )}
    </>
  );
}

function SettingsContent({ agent }: { agent: Agent }) {
  const qc = useQueryClient();
  const meta = agent.metadata ?? {};
  const [description, setDescription] = useState(agent.description ?? "");
  const [agentStatus, setAgentStatus] = useState(agent.status);
  const [execShape, setExecShape] = useState<"reactive" | "durable">(agent.execution_shape);
  const currentClass: "user_delegated" | "daemon" = agent.agent_class === "daemon" ? "daemon" : "user_delegated";
  const [agentClass, setAgentClass] = useState<"user_delegated" | "daemon">(currentClass);
  const [instructions, setInstructions] = useState((meta.instructions as string) ?? "");
  const [selectedProvider, setSelectedProvider] = useState((meta.llm_provider_id as string) ?? "");
  const [selectedTools, setSelectedTools] = useState<string[]>((meta.tools as string[]) ?? []);
  const [dirty, setDirty] = useState(false);

  const { data: providers } = useQuery({
    queryKey: ["providers"],
    queryFn: () => listProviders(),
  });
  const { data: tools } = useQuery({
    queryKey: ["tools"],
    queryFn: () => listTools(200),
  });

  const save = useMutation({
    mutationFn: () => {
      const newMeta = { ...meta, instructions, tools: selectedTools, llm_provider_id: selectedProvider || undefined };
      return updateAgent(agent.name, {
        description: description.trim() || undefined,
        status: agentStatus !== agent.status ? agentStatus : undefined,
        execution_shape: execShape !== agent.execution_shape ? execShape : undefined,
        agent_class: agentClass !== currentClass ? agentClass : undefined,
        metadata: newMeta,
      });
    },
    onSuccess: () => {
      toast.success("Agent updated");
      setDirty(false);
      qc.invalidateQueries({ queryKey: ["agent", agent.name] });
      qc.invalidateQueries({ queryKey: ["agents"] });
    },
    onError: () => toast.error("Failed to update agent"),
  });

  const markDirty = () => setDirty(true);

  const toggleTool = (toolName: string) => {
    setSelectedTools((prev) =>
      prev.includes(toolName) ? prev.filter((t) => t !== toolName) : [...prev, toolName]
    );
    markDirty();
  };

  return (
    <div className="card p-5 space-y-5">
      <h3 className="text-sm font-semibold text-slate-700">Agent Configuration</h3>

      <label className="block">
        <span className="text-xs text-slate-500 uppercase">Description</span>
        <textarea
          value={description}
          onChange={(e) => { setDescription(e.target.value); markDirty(); }}
          rows={2}
          className="mt-1 w-full text-sm border border-slate-300 rounded px-3 py-2 resize-none"
          placeholder="What does this agent do?"
        />
      </label>

      <label className="block">
        <span className="text-xs text-slate-500 uppercase">Instructions (System Prompt)</span>
        <textarea
          value={instructions}
          onChange={(e) => { setInstructions(e.target.value); markDirty(); }}
          rows={8}
          className="mt-1 w-full text-sm font-mono border border-slate-300 rounded px-3 py-2"
          placeholder="You are a helpful agent that..."
        />
      </label>

      <div className="grid grid-cols-2 gap-4">
        <label className="block">
          <span className="text-xs text-slate-500 uppercase">Model (LLM Provider)</span>
          <select
            value={selectedProvider}
            onChange={(e) => { setSelectedProvider(e.target.value); markDirty(); }}
            className="mt-1 w-full text-sm border border-slate-300 rounded px-2 py-1.5"
          >
            <option value="">— None —</option>
            {providers?.items.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} ({p.default_model})
              </option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className="text-xs text-slate-500 uppercase">Execution Shape</span>
          <select
            value={execShape}
            onChange={(e) => { setExecShape(e.target.value as "reactive" | "durable"); markDirty(); }}
            className="mt-1 w-full text-sm border border-slate-300 rounded px-2 py-1.5"
          >
            <option value="reactive">Ephemeral</option>
            <option value="durable">Durable</option>
          </select>
        </label>

        <label className="block">
          <span className="text-xs text-slate-500 uppercase">Authority (class)</span>
          <select
            value={agentClass}
            onChange={(e) => { setAgentClass(e.target.value as "user_delegated" | "daemon"); markDirty(); }}
            className="mt-1 w-full text-sm border border-slate-300 rounded px-2 py-1.5"
          >
            <option value="user_delegated">User-delegated</option>
            <option value="daemon">Daemon</option>
          </select>
        </label>
      </div>

      <div>
        <span className="text-xs text-slate-500 uppercase">Tools</span>
        <div className="mt-1 border border-slate-200 rounded p-3 max-h-48 overflow-y-auto space-y-1">
          {tools?.items.length === 0 && (
            <p className="text-xs text-slate-400">No tools registered.</p>
          )}
          {tools?.items.map((t) => (
            <label key={t.name} className="flex items-center gap-2 text-sm cursor-pointer hover:bg-slate-50 px-1 py-0.5 rounded">
              <input
                type="checkbox"
                checked={selectedTools.includes(t.name)}
                onChange={() => toggleTool(t.name)}
                className="rounded"
              />
              <span className="font-mono text-xs">{t.name}</span>
              {t.description && <span className="text-xs text-slate-400 truncate">— {t.description}</span>}
            </label>
          ))}
        </div>
      </div>

      <label className="block">
        <span className="text-xs text-slate-500 uppercase">Status</span>
        <select
          value={agentStatus}
          onChange={(e) => { setAgentStatus(e.target.value); markDirty(); }}
          className="mt-1 w-full text-sm border border-slate-300 rounded px-2 py-1.5"
        >
          <option value="active">Active</option>
          <option value="archived">Archived</option>
          <option value="deprecated">Deprecated</option>
        </select>
      </label>

      <div className="grid grid-cols-2 gap-4 text-sm text-slate-500">
        <div>
          <span className="text-xs uppercase">Name</span>
          <p className="font-mono text-slate-700">{agent.name}</p>
        </div>
        <div>
          <span className="text-xs uppercase">Team</span>
          <p className="text-slate-700">{agent.team}</p>
        </div>
      </div>

      {dirty && (
        <div className="flex justify-end">
          <button
            onClick={() => save.mutate()}
            disabled={save.isPending}
            className="btn-primary text-xs py-1.5"
          >
            {save.isPending ? "Saving…" : "Save Changes"}
          </button>
        </div>
      )}

    </div>
  );
}
