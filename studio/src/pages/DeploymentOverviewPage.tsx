import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Bot, Loader2, MessageCircle } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  getAgent,
  getDeployments,
  listTriggers,
  listVersions,
  type DeploymentContext,
} from "../api/registryApi";
import DeploymentActions from "../components/agent-detail/DeploymentActions";
import MemoryTab from "../components/agent-detail/MemoryTab";
import OverviewForShape, {
  resolveOverviewShape,
} from "../components/agent-detail/OverviewForShape";
import RunsTab from "../components/agent-detail/RunsTab";

const STATUS: Record<string, { label: string; cls: string }> = {
  pending: { label: "Pending", cls: "bg-amber-100 text-amber-700" },
  deploying: { label: "Deploying", cls: "bg-blue-100 text-blue-700" },
  running: { label: "Running", cls: "bg-green-100 text-green-700" },
  failed: { label: "Failed", cls: "bg-red-100 text-red-700" },
  rolled_back: { label: "Rolled back", cls: "bg-slate-100 text-slate-600" },
  terminated: { label: "Terminated", cls: "bg-slate-100 text-slate-600" },
  gate_failed: { label: "Gate failed", cls: "bg-red-100 text-red-700" },
};

type Tab = "overview" | "runs" | "memory";

/**
 * Level-3 Deployment Overview (playground context). Metrics, runs and memory
 * belong to a running deployment — not to the artifact. Reached from the
 * artifact page's Sandbox Deployments list.
 */
export default function DeploymentOverviewPage() {
  const { name, depId } = useParams<{ name: string; depId: string }>();
  const context: DeploymentContext = "playground";
  const [activeTab, setActiveTab] = useState<Tab>("overview");

  const { data: agent } = useQuery({
    queryKey: ["agent", name],
    queryFn: () => getAgent(name!),
    enabled: !!name,
  });

  const { data: triggers = [] } = useQuery({
    queryKey: ["triggers", name],
    queryFn: () => listTriggers(name!),
    enabled: !!name,
  });

  const { data: versions = [] } = useQuery({
    queryKey: ["versions", name],
    queryFn: () => listVersions(name!),
    enabled: !!name,
  });

  const { data: deployments, isLoading } = useQuery({
    queryKey: ["deployments", name],
    queryFn: () => getDeployments(name!),
    enabled: !!name,
    refetchInterval: 5_000,
  });

  const deployment = deployments?.find((d) => d.id === depId);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20 text-slate-400">
        <Loader2 size={20} className="animate-spin mr-2" /> Loading deployment…
      </div>
    );
  }

  if (!deployment) {
    return (
      <div className="max-w-3xl mx-auto px-6 py-8">
        <Link
          to={`/agents/${name}`}
          className="inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-800 mb-6"
        >
          <ArrowLeft size={14} /> Back to agent
        </Link>
        <div className="rounded-lg bg-red-50 border border-red-200 p-4 text-sm text-red-700">
          Deployment not found for this agent.
        </div>
      </div>
    );
  }

  const st = STATUS[deployment.status] ?? { label: deployment.status, cls: "bg-slate-100 text-slate-600" };
  const version = versions.find((v) => v.id === deployment.version_id);
  const deploymentName = deployment.name ?? name!;
  // WS-6 — one resolver, one dispatcher. The shape decides BOTH which overview mounts
  // and whether the Chat action is offered, so it must be derived exactly once.
  const overviewShape = resolveOverviewShape({
    hasWebhook: triggers.some((t) => t.trigger_type === "webhook"),
    hasSchedule: triggers.some((t) => t.trigger_type === "schedule"),
    executionShape: agent?.execution_shape,
  });
  const isReactive = overviewShape === "reactive";

  return (
    <div className="max-w-4xl mx-auto px-6 py-8">
      {/* Breadcrumb */}
      <Link
        to={`/agents/${name}`}
        className="inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-800 mb-6 transition-colors"
      >
        <ArrowLeft size={14} /> {name}
      </Link>

      {/* Header — deployment name is the primary identifier */}
      <div className="flex items-start justify-between mb-6">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-blue-100 flex items-center justify-center shrink-0">
            <Bot size={18} className="text-blue-600" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-slate-900 font-mono">{deploymentName}</h1>
            <div className="flex items-center gap-2 mt-1 text-xs text-slate-400">
              <span className={`badge ${st.cls}`}>{st.label}</span>
              <span>agent: <span className="font-medium text-slate-600">{name}</span></span>
              {version && <span>· {`v${version.version_number}`}</span>}
              <span>· ns: <span className="font-mono">{deployment.k8s_namespace}</span></span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {isReactive && deployment.status === "running" && (
            <Link to={`/agents/${name}/d/${depId}/chat`} className="btn-primary text-xs py-1.5">
              <MessageCircle size={12} /> Open Chat
            </Link>
          )}
          <DeploymentActions agentName={name!} deployment={deployment} />
        </div>
      </div>

      {/* Tabs */}
      <div className="border-b border-slate-200 mb-6">
        <nav className="flex gap-6">
          {(["overview", "runs", "memory"] as Tab[]).map((tab) => (
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
      {activeTab === "overview" && (
        <OverviewForShape
          shape={overviewShape}
          agentName={name!}
          deploymentId={deployment.id}
          context={context}
        />
      )}
      {activeTab === "runs" && <RunsTab deploymentId={deployment.id} context={context} />}
      {activeTab === "memory" && <MemoryTab agentName={name!} deploymentId={depId} />}
    </div>
  );
}
