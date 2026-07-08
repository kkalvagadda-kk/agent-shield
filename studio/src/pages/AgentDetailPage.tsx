import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Bot, Loader2, Rocket, Send } from "lucide-react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { getAgent, listVersions, publishAgent } from "../api/registryApi";
import MemoryTab from "../components/agent-detail/MemoryTab";
import OverviewDurable from "../components/agent-detail/OverviewDurable";
import OverviewReactive from "../components/agent-detail/OverviewReactive";
import OverviewScheduled from "../components/agent-detail/OverviewScheduled";
import OverviewEventDriven from "../components/agent-detail/OverviewEventDriven";
import RunsTab from "../components/agent-detail/RunsTab";
import SettingsTab from "../components/agent-detail/SettingsTab";
import { listTriggers } from "../api/registryApi";

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

type Tab = "overview" | "runs" | "memory" | "versions" | "settings";

export default function AgentDetailPage() {
  const { name } = useParams<{ name: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [activeTab, setActiveTab] = useState<Tab>("overview");

  const { data: agent, isLoading, error } = useQuery({
    queryKey: ["agent", name],
    queryFn: () => getAgent(name!),
    enabled: !!name,
  });

  const { data: triggers = [] } = useQuery({
    queryKey: ["triggers", name],
    queryFn: () => listTriggers(name!),
    enabled: !!name,
  });
  const hasSchedule = triggers.some((t) => t.trigger_type === "schedule");
  const hasWebhook = triggers.some((t) => t.trigger_type === "webhook");

  const { data: versions = [] } = useQuery({
    queryKey: ["versions", name],
    queryFn: () => listVersions(name!),
    enabled: !!name,
  });
  const latestVersion = versions.length > 0 ? versions[versions.length - 1] : null;
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
                {agent.execution_shape === "durable" ? "Durable" : "Reactive"}
              </span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => navigate(`/agents/${agent.name}/deploy`)}
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
          {(["overview", "runs", "memory", "versions", "settings"] as Tab[]).map((tab) => (
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
        hasWebhook
          ? <OverviewEventDriven agentName={agent.name} />
          : hasSchedule
            ? <OverviewScheduled agentName={agent.name} />
            : agent.execution_shape === "durable"
              ? <OverviewDurable agentName={agent.name} />
              : <OverviewReactive agentName={agent.name} />
      )}
      {activeTab === "runs" && (
        <RunsTab agentName={agent.name} />
      )}
      {activeTab === "memory" && (
        <MemoryTab agentName={agent.name} />
      )}
      {activeTab === "versions" && (
        <VersionsContent agent={agent} />
      )}
      {activeTab === "settings" && (
        <div className="space-y-4">
          <SettingsContent agent={agent} />
          <SettingsTab agentName={agent.name} memoryEnabled={agent.memory_enabled} />
        </div>
      )}
    </div>
  );
}

function VersionsContent({ agent }: { agent: { name: string; agent_type: string; team: string; created_at: string; updated_at: string; created_by: string; description?: string | null } }) {
  return (
    <div className="card p-5">
      <h3 className="text-sm font-semibold text-slate-700 mb-3">Agent Details</h3>
      <div className="grid grid-cols-2 gap-4 text-sm">
        <div>
          <p className="text-xs text-slate-400 uppercase mb-0.5">Type</p>
          <p className="font-mono text-slate-700">{agent.agent_type}</p>
        </div>
        <div>
          <p className="text-xs text-slate-400 uppercase mb-0.5">Team</p>
          <p className="text-slate-700">{agent.team}</p>
        </div>
        <div>
          <p className="text-xs text-slate-400 uppercase mb-0.5">Created</p>
          <p className="text-slate-700">{new Date(agent.created_at).toLocaleString()}</p>
        </div>
        <div>
          <p className="text-xs text-slate-400 uppercase mb-0.5">Updated</p>
          <p className="text-slate-700">{new Date(agent.updated_at).toLocaleString()}</p>
        </div>
        {agent.created_by !== "system" && (
          <div>
            <p className="text-xs text-slate-400 uppercase mb-0.5">Created By</p>
            <p className="font-mono text-slate-700">{agent.created_by}</p>
          </div>
        )}
        {agent.description && (
          <div className="col-span-2">
            <p className="text-xs text-slate-400 uppercase mb-0.5">Description</p>
            <p className="text-slate-700">{agent.description}</p>
          </div>
        )}
      </div>
    </div>
  );
}

function SettingsContent({ agent }: { agent: { memory_enabled?: boolean; execution_shape?: string } }) {
  return (
    <div className="card p-5 space-y-4">
      <h3 className="text-sm font-semibold text-slate-700 mb-3">Configuration</h3>
      <div className="grid grid-cols-2 gap-4 text-sm">
        <div>
          <p className="text-xs text-slate-400 uppercase mb-0.5">Execution Shape</p>
          <p className="font-mono text-slate-700">{agent.execution_shape || "reactive"}</p>
        </div>
        <div>
          <p className="text-xs text-slate-400 uppercase mb-0.5">Memory</p>
          <p className="text-slate-700">{agent.memory_enabled ? "Enabled" : "Disabled"}</p>
        </div>
      </div>
    </div>
  );
}
