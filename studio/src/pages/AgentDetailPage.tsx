import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Bot, Loader2, Rocket, Send } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { getAgent, publishAgent } from "../api/registryApi";

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

export default function AgentDetailPage() {
  const { name } = useParams<{ name: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const { data: agent, isLoading, error } = useQuery({
    queryKey: ["agent", name],
    queryFn: () => getAgent(name!),
    enabled: !!name,
  });

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
    <div className="max-w-3xl mx-auto px-6 py-8">
      {/* Back */}
      <button
        onClick={() => navigate("/")}
        className="inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-800 mb-6 transition-colors"
      >
        <ArrowLeft size={14} />
        All Agents
      </button>

      {/* Header */}
      <div className="flex items-start justify-between mb-8">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-blue-100 flex items-center justify-center shrink-0">
            <Bot size={18} className="text-blue-600" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-slate-900 font-mono">{agent.name}</h1>
            {agent.description && (
              <p className="text-sm text-slate-500 mt-0.5">{agent.description}</p>
            )}
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
          <button
            onClick={handlePublish}
            disabled={
              publishMutation.isPending ||
              agent.publish_status === "pending_review" ||
              agent.publish_status === "published"
            }
            className="btn-primary text-xs py-1.5 disabled:opacity-50"
          >
            {publishMutation.isPending ? (
              <><Loader2 size={12} className="animate-spin" /> Submitting…</>
            ) : (
              <><Send size={12} /> Publish</>
            )}
          </button>
        </div>
      </div>

      {/* Details card */}
      <div className="card divide-y divide-slate-100">
        <div className="grid grid-cols-2 gap-0">
          <DetailRow label="Team" value={agent.team} />
          <DetailRow label="Type" value={agent.agent_type} mono />
        </div>
        <div className="grid grid-cols-2 gap-0">
          <DetailRow
            label="Operational Status"
            value={<span className={`badge ${os.cls}`}>{os.label}</span>}
          />
          <DetailRow
            label="Publish Status"
            value={<span className={`badge ${ps.cls}`}>{ps.label}</span>}
          />
        </div>
        <div className="grid grid-cols-2 gap-0">
          <DetailRow
            label="Created"
            value={new Date(agent.created_at).toLocaleString()}
          />
          <DetailRow
            label="Last Updated"
            value={new Date(agent.updated_at).toLocaleString()}
          />
        </div>
        {agent.created_by && (
          <DetailRow label="Created By" value={agent.created_by} mono />
        )}
      </div>

      {/* Publish flow hint */}
      {agent.publish_status === "private" && (
        <div className="mt-4 rounded-lg bg-blue-50 border border-blue-200 p-4 text-sm text-blue-700">
          This agent is private. Click <strong>Publish</strong> to submit it for admin review.
          Once approved, it will be visible to other teams with an active grant.
        </div>
      )}
      {agent.publish_status === "pending_review" && (
        <div className="mt-4 rounded-lg bg-amber-50 border border-amber-200 p-4 text-sm text-amber-700">
          A publish request is pending admin review. You'll be notified when it's approved or rejected.
        </div>
      )}
      {agent.publish_status === "published" && (
        <div className="mt-4 rounded-lg bg-green-50 border border-green-200 p-4 text-sm text-green-700">
          This agent is published and visible to teams with an active grant.
        </div>
      )}
    </div>
  );
}

function DetailRow({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="px-5 py-4">
      <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">{label}</p>
      <p className={`text-sm text-slate-800 ${mono ? "font-mono" : ""}`}>{value}</p>
    </div>
  );
}
