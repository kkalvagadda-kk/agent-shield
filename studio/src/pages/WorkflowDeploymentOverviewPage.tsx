import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ExternalLink, GitBranch, Loader2, MessageCircle, Pause, Play, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  getCompositeWorkflow,
  getWorkflowDeploymentStats,
  listWorkflowDeployments,
  listWorkflowDeploymentRuns,
  listWorkflowVersions,
  updateWorkflowDeployment,
  type AgentRunItem,
  type AgentStats,
  type DeploymentAction,
  type WorkflowDeployment,
} from "../api/registryApi";
import WorkflowMiniGraph from "../components/WorkflowMiniGraph";
import WorkflowConversationsTab from "../components/agent-detail/WorkflowConversationsTab";
import WorkflowMemoryTab from "../components/agent-detail/WorkflowMemoryTab";
import { toast } from "sonner";

const STATUS: Record<string, { label: string; cls: string }> = {
  pending: { label: "Pending", cls: "bg-amber-100 text-amber-700" },
  deploying: { label: "Deploying", cls: "bg-blue-100 text-blue-700" },
  running: { label: "Running", cls: "bg-green-100 text-green-700" },
  failed: { label: "Failed", cls: "bg-red-100 text-red-700" },
  terminated: { label: "Terminated", cls: "bg-slate-100 text-slate-600" },
  suspended: { label: "Suspended", cls: "bg-amber-100 text-amber-700" },
  suspending: { label: "Suspending…", cls: "bg-amber-50 text-amber-600" },
  terminating: { label: "Terminating…", cls: "bg-slate-50 text-slate-500" },
};

type Tab = "overview" | "runs" | "memory" | "conversations";

export default function WorkflowDeploymentOverviewPage() {
  const { id, depId } = useParams<{ id: string; depId: string }>();
  const [activeTab, setActiveTab] = useState<Tab>("overview");

  const { data: workflow } = useQuery({
    queryKey: ["workflow", id],
    queryFn: () => getCompositeWorkflow(id!),
    enabled: !!id,
  });

  const { data: deployments, isLoading, refetch } = useQuery({
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

  const deployment = deployments?.find((d) => d.id === depId);

  const { data: stats } = useQuery({
    queryKey: ["wf-dep-stats", id, depId],
    queryFn: () => getWorkflowDeploymentStats(id!, depId!),
    enabled: !!id && !!depId && !!deployment,
    refetchInterval: 30_000,
  });

  const { data: runs = [] } = useQuery({
    queryKey: ["wf-dep-runs", id, depId],
    queryFn: () => listWorkflowDeploymentRuns(id!, depId!),
    enabled: !!id && !!depId && !!deployment && activeTab === "runs",
  });

  const handleAction = async (action: DeploymentAction, versionId?: string) => {
    try {
      await updateWorkflowDeployment(id!, depId!, action, versionId);
      toast.success(`Action '${action}' applied`);
      refetch();
    } catch (e: unknown) {
      toast.error(`Failed: ${e instanceof Error ? e.message : "unknown error"}`);
    }
  };

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
          to={`/workflows/${id}`}
          className="inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-800 mb-6"
        >
          <ArrowLeft size={14} /> Back to workflow
        </Link>
        <div className="rounded-lg bg-red-50 border border-red-200 p-4 text-sm text-red-700">
          Workflow deployment not found.
        </div>
      </div>
    );
  }

  const st = STATUS[deployment.status] ?? { label: deployment.status, cls: "bg-slate-100 text-slate-600" };
  const version = versions.find((v) => v.id === deployment.version_id);
  const deploymentName = deployment.name ?? workflow?.name ?? id!;

  const isTransitional = ["suspending", "terminating", "pending", "deploying"].includes(deployment.status);

  return (
    <div className="max-w-4xl mx-auto px-6 py-8">
      {/* Breadcrumb */}
      <Link
        to={`/workflows/${id}`}
        className="inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-800 mb-6 transition-colors"
      >
        <ArrowLeft size={14} /> {workflow?.name ?? "Workflow"}
      </Link>

      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-purple-100 flex items-center justify-center shrink-0">
            <GitBranch size={18} className="text-purple-600" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-slate-900 font-mono">{deploymentName}</h1>
            <div className="flex items-center gap-2 mt-1 text-xs text-slate-400">
              <span className={`badge ${st.cls}`}>{st.label}</span>
              <span>workflow: <span className="font-medium text-slate-600">{workflow?.name ?? id}</span></span>
              {version && <span>· v{version.version_number}</span>}
            </div>
          </div>
        </div>
        {/* Actions */}
        <div className="flex items-center gap-1.5">
          {/* Reactive workflows get an "Open Chat" entry point — parity with a
              reactive agent's deployment (its chat streams POST /workflows/{id}/runs/stream). */}
          {workflow?.execution_shape === "reactive" && deployment.status === "running" && (
            <Link
              to={`/workflows/${id}/d/${depId}/chat`}
              className="btn-primary text-xs py-1.5 mr-1 inline-flex items-center gap-1.5"
              data-testid="workflow-open-chat"
            >
              <MessageCircle size={12} /> Open Chat
            </Link>
          )}
          {!isTransitional && deployment.status === "running" && (
            <button onClick={() => handleAction("suspend")} className="p-1.5 rounded-md hover:bg-slate-100 text-amber-600" title="Suspend">
              <Pause size={16} />
            </button>
          )}
          {deployment.status === "suspended" && (
            <button onClick={() => handleAction("resume")} className="p-1.5 rounded-md hover:bg-slate-100 text-green-600" title="Resume">
              <Play size={16} />
            </button>
          )}
          {(deployment.status === "running" || deployment.status === "suspended") && !isTransitional && (
            <button onClick={() => handleAction("terminate")} className="p-1.5 rounded-md hover:bg-red-50 text-red-500" title="Terminate">
              <Trash2 size={16} />
            </button>
          )}
        </div>
      </div>

      {/* Tabs */}
      <div className="border-b border-slate-200 mb-6">
        <nav className="flex gap-6">
          {(["overview", "runs", "memory", "conversations"] as Tab[]).map((tab) => (
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

      {/* Content */}
      {activeTab === "overview" && (
        <OverviewStats stats={stats} deployment={deployment} version={version} workflowId={id!} workflow={workflow} />
      )}
      {activeTab === "runs" && <RunsList runs={runs} />}
      {/* Conversations + Memory parity with an agent's deployment overview. A
          workflow's transcript is authored by its MEMBERS (member agent_name,
          NULL user_id), so the Conversations tab must resolve the list through
          the workflow's parent runs (workflow id), NOT the workflow name — see
          WorkflowConversationsTab / GET /workflows/{id}/conversations. */}
      {activeTab === "memory" && <WorkflowMemoryTab workflowId={id!} deploymentId={depId!} />}
      {activeTab === "conversations" && (
        <WorkflowConversationsTab workflowId={id!} deploymentId={depId!} />
      )}
    </div>
  );
}

function OverviewStats({
  stats,
  deployment,
  version,
  workflowId,
  workflow,
}: {
  stats?: AgentStats;
  deployment: WorkflowDeployment;
  version?: { version_number: number; orchestration: string; members: unknown[]; edges?: unknown[] };
  workflowId: string;
  workflow?: {
    execution_shape?: string;
    members?: { agent_id: string; agent_name: string | null }[];
    edges?: { source_agent_id: string; target_agent_id: string }[];
  };
}) {
  const members = (version?.members ?? workflow?.members ?? []) as { agent_id: string; agent_name: string | null }[];
  const edges = (version?.edges ?? workflow?.edges ?? []) as { source_agent_id: string; target_agent_id: string }[];

  return (
    <div className="space-y-6">
      {/* Stats cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Runs (24h)" value={stats?.run_count ?? 0} />
        <StatCard label="Error Rate" value={`${((stats?.error_rate ?? 0) * 100).toFixed(1)}%`} />
        <StatCard label="P50 Latency" value={stats?.p50_latency_ms ? `${stats.p50_latency_ms}ms` : "—"} />
        <StatCard label="Cost (24h)" value={stats?.total_cost_usd ? `$${stats.total_cost_usd.toFixed(4)}` : "—"} />
      </div>

      {/* Mini-graph: read-only topology */}
      {members.length > 0 && (
        <div>
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-sm font-semibold text-slate-700">Member Topology</h3>
            <Link
              to={`/workflows/${workflowId}/builder`}
              className="inline-flex items-center gap-1 text-xs text-purple-600 hover:text-purple-800"
            >
              <ExternalLink size={11} /> Open Builder
            </Link>
          </div>
          <WorkflowMiniGraph members={members} edges={edges} />
        </div>
      )}

      {/* Deployment info */}
      <div className="rounded-lg border border-slate-200 p-4 space-y-2 text-sm">
        <div className="flex justify-between">
          <span className="text-slate-500">Environment</span>
          <span className="font-medium">{deployment.environment}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500">Version</span>
          <span className="font-medium">v{version?.version_number ?? "?"}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500">Orchestration</span>
          <span className="font-medium">{version?.orchestration ?? "—"}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-slate-500">Members</span>
          <span className="font-medium">{members.length}</span>
        </div>
        {workflow?.execution_shape === "reactive" && (
          <div className="flex justify-between items-center gap-3">
            <span className="text-slate-500 shrink-0">Chat Endpoint</span>
            <code className="font-mono text-xs text-slate-700 truncate" title={`POST /api/v1/workflows/${workflowId}/runs/stream`}>
              POST /api/v1/workflows/{workflowId}/runs/stream
            </code>
          </div>
        )}
        {deployment.ttl_hours && (
          <div className="flex justify-between">
            <span className="text-slate-500">TTL</span>
            <span className="font-medium">{deployment.ttl_hours}h</span>
          </div>
        )}
        <div className="flex justify-between">
          <span className="text-slate-500">Deployed</span>
          <span className="font-medium">{new Date(deployment.deployed_at).toLocaleString()}</span>
        </div>
      </div>
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-slate-200 p-3 text-center">
      <div className="text-lg font-bold text-slate-900">{value}</div>
      <div className="text-xs text-slate-500">{label}</div>
    </div>
  );
}

function RunsList({ runs }: { runs: AgentRunItem[] }) {
  if (runs.length === 0) {
    return <p className="text-sm text-slate-400 py-8 text-center">No runs yet for this deployment.</p>;
  }
  return (
    <div className="border border-slate-200 rounded-lg overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-slate-50 text-slate-500 text-xs">
          <tr>
            <th className="text-left px-4 py-2">Run ID</th>
            <th className="text-left px-4 py-2">Status</th>
            <th className="text-left px-4 py-2">Trigger</th>
            <th className="text-left px-4 py-2">Started</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id} className="border-t border-slate-100 hover:bg-slate-50">
              <td className="px-4 py-2 font-mono text-xs">{run.id.slice(0, 8)}</td>
              <td className="px-4 py-2">{run.status}</td>
              <td className="px-4 py-2">{run.trigger_type ?? "manual"}</td>
              <td className="px-4 py-2 text-slate-500">{new Date(run.started_at).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
