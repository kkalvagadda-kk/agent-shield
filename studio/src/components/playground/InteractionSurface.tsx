import { useState } from "react";
import ChatPane from "./ChatPane";
import RunLauncher from "./RunLauncher";
import RunNowPanel from "./RunNowPanel";
import StepTracker from "./StepTracker";
import TestTriggerPanel from "./TestTriggerPanel";
import WorkflowRunTree from "./WorkflowRunTree";

type TriggerMode = "none" | "schedule" | "webhook";

interface InteractionSurfaceProps {
  agentName: string | null;
  executionShape: "reactive" | "durable";
  triggerMode?: TriggerMode;
  versionId?: string;
  workflowId?: string;
}

export default function InteractionSurface({
  agentName,
  executionShape,
  triggerMode = "none",
  versionId,
  workflowId,
}: InteractionSurfaceProps) {
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [streamUrl, setStreamUrl] = useState<string | null>(null);

  const handleRunStarted = (runId: string) => {
    setActiveRunId(runId);
    setStreamUrl(`/api/v1/playground/runs/${runId}/stream`);
  };

  // Workflow mode: show run launcher targeting the workflow endpoint
  if (workflowId) {
    return (
      <div className="space-y-6 p-4">
        {agentName ? (
          <RunLauncher
            agentName={agentName}
            versionId={versionId}
            workflowId={workflowId}
            onRunStarted={(runId) => setActiveRunId(runId)}
          />
        ) : (
          <p className="text-sm text-slate-400 text-center py-8">Select a workflow to run.</p>
        )}
        {activeRunId && (
          <WorkflowRunTree workflowId={workflowId} runId={activeRunId} />
        )}
      </div>
    );
  }

  if (!agentName) {
    return (
      <div className="flex items-center justify-center h-full text-slate-400">
        <div className="text-center">
          <p className="font-medium">No agent selected</p>
          <p className="text-sm mt-1">Pick an agent from the left panel to start chatting.</p>
        </div>
      </div>
    );
  }

  // Scheduled agents: show RunNowPanel
  if (triggerMode === "schedule") {
    return (
      <div className="space-y-6">
        <RunNowPanel agentName={agentName} onRunStarted={handleRunStarted} />
        {activeRunId && streamUrl && (
          <StepTracker runId={activeRunId} streamUrl={streamUrl} />
        )}
      </div>
    );
  }

  // Event-driven agents: show TestTriggerPanel
  if (triggerMode === "webhook") {
    return (
      <div className="space-y-6">
        <TestTriggerPanel agentName={agentName} onRunStarted={handleRunStarted} />
        {activeRunId && streamUrl && (
          <StepTracker runId={activeRunId} streamUrl={streamUrl} />
        )}
      </div>
    );
  }

  // Reactive agents: chat pane
  if (executionShape === "reactive") {
    return <ChatPane agentName={agentName} resumeStreamUrl={null} onApprovalRequested={() => {}} onResumeComplete={() => {}} onTraceEvent={() => {}} />;
  }

  // Durable agents: run launcher + step tracker
  return (
    <div className="space-y-6">
      <RunLauncher
        agentName={agentName}
        versionId={versionId}
        onRunStarted={handleRunStarted}
      />
      {activeRunId && streamUrl && (
        <StepTracker runId={activeRunId} streamUrl={streamUrl} />
      )}
    </div>
  );
}
