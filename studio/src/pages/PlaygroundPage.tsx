import { useState, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import ChatPane from "../components/playground/ChatPane";
import InteractionSurface from "../components/playground/InteractionSurface";
import HitlPanel, { type HitlRequest } from "../components/playground/HitlPanel";
import TracePanel, { type TraceEvent } from "../components/playground/TracePanel";
import VersionSelector from "../components/playground/VersionSelector";
import type { AgentDeploymentSelection } from "../components/playground/VersionSelector";
import WorkflowSelector from "../components/playground/WorkflowSelector";
import type { WorkflowDeploymentSelection } from "../components/playground/WorkflowSelector";
import { CheckCircle, Database, Loader2, Send, ShieldCheck } from "lucide-react";
import {
  getAgent,
  listTriggers,
  patchVersion,
  patchWorkflowVersion,
  publishAgent,
  publishWorkflow,
} from "../api/registryApi";

// Publish endpoints can return a 422 whose `detail` is an OBJECT
// (e.g. {error: "adversarial_eval_not_passed", version_number: 4}), not a string.
// Passing an object to toast.error() renders it as a React child → "Objects are
// not valid as a React child" → the whole page blanks. Extract a safe string.
function publishErrorMessage(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const code = (detail as { error?: unknown }).error;
    if (code === "adversarial_eval_not_passed") {
      return "This version hasn't passed adversarial evaluation yet — run an eval and mark it passed before publishing.";
    }
    if (typeof code === "string") return code;
  }
  return fallback;
}

export default function PlaygroundPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [targetType, setTargetType] = useState<"agent" | "workflow">("agent");
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const [agentSelection, setAgentSelection] = useState<AgentDeploymentSelection | null>(null);
  const [selectedWorkflow, setSelectedWorkflow] = useState<WorkflowDeploymentSelection | null>(null);
  const [hitlRequest, setHitlRequest] = useState<HitlRequest | null>(null);
  const [resumeStreamUrl, setResumeStreamUrl] = useState<string | null>(null);
  const resumeNonceRef = useRef(0);
  const [traceEvents, setTraceEvents] = useState<TraceEvent[]>([]);
  const [tracePanelCollapsed, setTracePanelCollapsed] = useState(false);

  const { data: agentData } = useQuery({
    queryKey: ["agent", selectedAgent],
    queryFn: () => getAgent(selectedAgent!),
    enabled: !!selectedAgent,
  });

  const executionShape = agentData?.execution_shape ?? "reactive";

  const { data: triggers } = useQuery({
    queryKey: ["triggers", selectedAgent],
    queryFn: () => listTriggers(selectedAgent!),
    enabled: !!selectedAgent,
  });

  const triggerMode: "none" | "schedule" | "webhook" = (() => {
    if (!triggers || triggers.length === 0) return "none";
    if (triggers.some((t) => t.trigger_type === "webhook")) return "webhook";
    if (triggers.some((t) => t.trigger_type === "schedule")) return "schedule";
    return "none";
  })();

  const handleApprovalRequested = (
    approvalId: string,
    toolName: string,
    riskLevel: string,
    args: Record<string, unknown>,
    reasoning?: string | null,
    requestedBy?: string | null,
    requestedByTeam?: string | null
  ) => {
    setHitlRequest({
      approval_id: approvalId,
      tool_name: toolName,
      risk_level: riskLevel,
      args_redacted: args,
      reasoning: reasoning ?? null,
      requested_by: requestedBy ?? null,
      requested_by_team: requestedByTeam ?? null,
    });
    setTraceEvents((prev) => [
      ...prev,
      {
        ts: new Date().toISOString(),
        event: "approval_requested",
        tool_name: toolName,
      },
    ]);
  };

  const handleHitlDecided = (_decision: "approved" | "denied", threadId: string) => {
    setHitlRequest(null);
    // Nonce forces the resume effect to re-fire even for a SECOND approval on the
    // same run (a later-turn tool call), where the base URL is unchanged. The
    // backend ignores the extra query param.
    resumeNonceRef.current += 1;
    setResumeStreamUrl(
      `/api/v1/playground/runs/${threadId}/resume-stream?_n=${resumeNonceRef.current}`
    );
    setTraceEvents((prev) => [
      ...prev,
      {
        ts: new Date().toISOString(),
        event: "approval_decided",
        content: _decision,
      },
    ]);
  };

  const handleResumeComplete = () => {
    setResumeStreamUrl(null);
  };

  const handleTraceEvent = (ev: TraceEvent) => {
    setTraceEvents((prev) => [...prev, ev]);
  };

  // Promotion mutations
  const markAgentPassedMutation = useMutation({
    mutationFn: () =>
      patchVersion(agentSelection!.agentName, agentSelection!.versionId!, { eval_passed: true }),
    onSuccess: () => toast.success("Agent version marked as eval passed"),
    onError: () => toast.error("Failed to mark version passed"),
  });

  // Adversarial-eval gate: publish rejects any agent with a high/critical-risk tool
  // unless the version has adversarial_eval_passed=true. This is a distinct, explicit
  // governance step from the ordinary eval mark (kept separate on purpose — bundling it
  // into publish would hide the red-team sign-off).
  const markAgentAdversarialPassedMutation = useMutation({
    mutationFn: () =>
      patchVersion(agentSelection!.agentName, agentSelection!.versionId!, { adversarial_eval_passed: true }),
    onSuccess: () => toast.success("Agent version marked as adversarial-eval passed"),
    onError: () => toast.error("Failed to mark adversarial eval passed"),
  });

  const publishAgentMutation = useMutation({
    mutationFn: async () => {
      if (agentSelection?.versionId) {
        await patchVersion(agentSelection.agentName, agentSelection.versionId, { eval_passed: true });
      }
      return publishAgent(agentSelection!.agentName, { version_id: agentSelection?.versionId ?? undefined });
    },
    onSuccess: () => {
      toast.success("Publish request submitted");
      qc.invalidateQueries({ queryKey: ["agent", selectedAgent] });
    },
    onError: (err: unknown) => {
      toast.error(publishErrorMessage(err, "Failed to submit publish request"));
    },
  });

  const markWorkflowPassedMutation = useMutation({
    mutationFn: () =>
      patchWorkflowVersion(selectedWorkflow!.id, selectedWorkflow!.versionId!, { eval_passed: true }),
    onSuccess: () => toast.success("Workflow version marked as eval passed"),
    onError: () => toast.error("Failed to mark version passed"),
  });

  const publishWorkflowMutation = useMutation({
    mutationFn: async () => {
      if (selectedWorkflow?.versionId) {
        await patchWorkflowVersion(selectedWorkflow.id, selectedWorkflow.versionId, { eval_passed: true });
      }
      return publishWorkflow(selectedWorkflow!.id, selectedWorkflow?.versionId ?? undefined);
    },
    onSuccess: () => {
      toast.success("Publish request submitted");
      qc.invalidateQueries({ queryKey: ["workflow", selectedWorkflow?.id] });
    },
    onError: (err: unknown) => {
      toast.error(publishErrorMessage(err, "Failed to submit publish request"));
    },
  });

  const canPromoteAgent = targetType === "agent" && agentSelection?.versionId != null;
  const canPromoteWorkflow = targetType === "workflow" && selectedWorkflow?.versionId != null;

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Left panel — agent/workflow selector */}
      <div className="w-60 shrink-0 border-r border-slate-200 bg-white p-4 flex flex-col gap-4 overflow-y-auto">
        <div>
          <h2 className="text-sm font-semibold text-slate-800 mb-3">Eval Runs</h2>

          {/* Agent / Workflow toggle */}
          <div className="flex gap-1 p-1 bg-slate-100 rounded-lg mb-3">
            <button
              onClick={() => { setTargetType("agent"); setSelectedWorkflow(null); }}
              className={`flex-1 text-xs font-medium py-1.5 rounded-md transition-colors ${
                targetType === "agent" ? "bg-white shadow text-slate-800" : "text-slate-500"
              }`}
            >
              Agent
            </button>
            <button
              onClick={() => { setTargetType("workflow"); setSelectedAgent(null); setAgentSelection(null); setTraceEvents([]); }}
              className={`flex-1 text-xs font-medium py-1.5 rounded-md transition-colors ${
                targetType === "workflow" ? "bg-white shadow text-slate-800" : "text-slate-500"
              }`}
            >
              Workflow
            </button>
          </div>

          {targetType === "agent" ? (
            <VersionSelector
              selectedAgent={selectedAgent ?? ""}
              onSelect={(name, selection) => {
                setSelectedAgent(name || null);
                setAgentSelection(selection ?? null);
                setTraceEvents([]);
              }}
            />
          ) : (
            <WorkflowSelector
              selectedWorkflowId={selectedWorkflow?.id ?? ""}
              onSelect={(wf) => setSelectedWorkflow(wf)}
            />
          )}
        </div>

        {/* Promotion controls */}
        {canPromoteAgent && (
          <div className="flex flex-col gap-2 pt-3 border-t border-slate-100">
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Promote</p>
            <button
              onClick={() => markAgentPassedMutation.mutate()}
              disabled={markAgentPassedMutation.isPending || markAgentPassedMutation.isSuccess}
              className={`inline-flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md disabled:opacity-50 w-full ${
                markAgentPassedMutation.isSuccess
                  ? "bg-green-600 text-white"
                  : "bg-slate-200 text-slate-700 hover:bg-slate-300"
              }`}
            >
              {markAgentPassedMutation.isPending ? <Loader2 size={12} className="animate-spin" /> : <CheckCircle size={12} />}
              {markAgentPassedMutation.isSuccess ? "Version Passed" : "Mark Version Passed"}
            </button>
            <button
              onClick={() => markAgentAdversarialPassedMutation.mutate()}
              disabled={markAgentAdversarialPassedMutation.isPending || markAgentAdversarialPassedMutation.isSuccess}
              title="Required to publish agents that use a high-risk tool"
              className={`inline-flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md disabled:opacity-50 w-full ${
                markAgentAdversarialPassedMutation.isSuccess
                  ? "bg-green-600 text-white"
                  : "bg-slate-200 text-slate-700 hover:bg-slate-300"
              }`}
            >
              {markAgentAdversarialPassedMutation.isPending ? <Loader2 size={12} className="animate-spin" /> : <ShieldCheck size={12} />}
              {markAgentAdversarialPassedMutation.isSuccess ? "Adversarial Passed" : "Mark Adversarial Passed"}
            </button>
            <button
              onClick={() => publishAgentMutation.mutate()}
              disabled={publishAgentMutation.isPending}
              className="inline-flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 w-full"
            >
              {publishAgentMutation.isPending ? <Loader2 size={12} className="animate-spin" /> : <Send size={12} />}
              Publish Agent
            </button>
          </div>
        )}

        {canPromoteWorkflow && (
          <div className="flex flex-col gap-2 pt-3 border-t border-slate-100">
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Promote</p>
            <button
              onClick={() => markWorkflowPassedMutation.mutate()}
              disabled={markWorkflowPassedMutation.isPending || markWorkflowPassedMutation.isSuccess}
              className={`inline-flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md disabled:opacity-50 w-full ${
                markWorkflowPassedMutation.isSuccess
                  ? "bg-green-600 text-white"
                  : "bg-slate-200 text-slate-700 hover:bg-slate-300"
              }`}
            >
              {markWorkflowPassedMutation.isPending ? <Loader2 size={12} className="animate-spin" /> : <CheckCircle size={12} />}
              {markWorkflowPassedMutation.isSuccess ? "Version Passed" : "Mark Version Passed"}
            </button>
            <button
              onClick={() => publishWorkflowMutation.mutate()}
              disabled={publishWorkflowMutation.isPending}
              className="inline-flex items-center justify-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 w-full"
            >
              {publishWorkflowMutation.isPending ? <Loader2 size={12} className="animate-spin" /> : <Send size={12} />}
              Publish Workflow
            </button>
          </div>
        )}

        <div className="pt-2 border-t border-slate-100">
          <button
            onClick={() => navigate("/playground/datasets")}
            className="flex items-center gap-2 text-xs text-slate-500 hover:text-slate-700 w-full text-left py-1"
          >
            <Database size={12} />
            Manage Datasets / Eval
          </button>
        </div>

        {(selectedAgent || selectedWorkflow) && (
          <div className="mt-auto">
            <div className="rounded-md bg-purple-50 border border-purple-200 px-3 py-2">
              <p className="text-xs text-purple-700 font-medium">Sandbox mode</p>
              <p className="text-xs text-purple-500 mt-0.5">
                Runs are isolated. No production state affected.
              </p>
            </div>
          </div>
        )}
      </div>

      {/* Center panel — chat / workflow run */}
      <div className="flex-1 flex flex-col min-w-0 bg-white">
        {selectedAgent && targetType === "agent" && (
          <div className="px-4 py-2 border-b border-slate-100 flex items-center gap-2">
            <span className="text-sm font-medium text-slate-700">{selectedAgent}</span>
            <span className="badge bg-purple-100 text-purple-700 text-xs">sandbox</span>
            <span className={`badge text-xs ${executionShape === "durable" ? "bg-purple-100 text-purple-700" : "bg-sky-100 text-sky-700"}`}>
              {executionShape}
            </span>
          </div>
        )}
        {selectedWorkflow && targetType === "workflow" && (
          <div className="px-4 py-2 border-b border-slate-100 flex items-center gap-2">
            <span className="text-sm font-medium text-slate-700">{selectedWorkflow.name}</span>
            <span className="badge bg-indigo-100 text-indigo-700 text-xs">workflow</span>
          </div>
        )}
        {targetType === "agent" ? (
          executionShape === "durable" || triggerMode !== "none" ? (
            <div className="flex-1 overflow-y-auto p-4">
              <InteractionSurface
                agentName={selectedAgent!}
                executionShape={executionShape as "reactive" | "durable"}
                triggerMode={triggerMode}
              />
            </div>
          ) : (
            <ChatPane
              agentName={selectedAgent}
              resumeStreamUrl={resumeStreamUrl}
              onApprovalRequested={handleApprovalRequested}
              onResumeComplete={handleResumeComplete}
              onTraceEvent={handleTraceEvent}
            />
          )
        ) : (
          <InteractionSurface
            agentName={selectedWorkflow?.name ?? null}
            executionShape="durable"
            triggerMode="none"
            workflowId={selectedWorkflow?.id}
          />
        )}
      </div>

      {/* Right panel — trace */}
      <TracePanel
        events={traceEvents}
        collapsed={tracePanelCollapsed}
        onToggle={() => setTracePanelCollapsed((c) => !c)}
      />

      {/* HITL overlay */}
      <HitlPanel request={hitlRequest} onDecided={handleHitlDecided} />
    </div>
  );
}
