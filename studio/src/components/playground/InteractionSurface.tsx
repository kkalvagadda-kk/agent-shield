import { useState } from "react";
import ChatPane from "./ChatPane";
import RunLauncher from "./RunLauncher";
import RunNowPanel from "./RunNowPanel";
import StepTracker from "./StepTracker";
import TestTriggerPanel from "./TestTriggerPanel";

type TriggerMode = "none" | "schedule" | "webhook";

interface InteractionSurfaceProps {
  agentName: string;
  executionShape: "reactive" | "durable";
  triggerMode?: TriggerMode;
  versionId?: string;
}

export default function InteractionSurface({
  agentName,
  executionShape,
  triggerMode = "none",
  versionId,
}: InteractionSurfaceProps) {
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [streamUrl, setStreamUrl] = useState<string | null>(null);

  const handleRunStarted = (runId: string) => {
    setActiveRunId(runId);
    setStreamUrl(`/api/v1/playground/runs/${runId}/stream`);
  };

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
    return <ChatPane agentName={agentName} onApprovalRequested={() => {}} onTraceEvent={() => {}} />;
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
