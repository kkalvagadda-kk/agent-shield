import { useState } from "react";
import { useNavigate } from "react-router-dom";
import ChatPane from "../components/playground/ChatPane";
import HitlPanel, { type HitlRequest } from "../components/playground/HitlPanel";
import TracePanel, { type TraceEvent } from "../components/playground/TracePanel";
import VersionSelector from "../components/playground/VersionSelector";
import { Database } from "lucide-react";

export default function PlaygroundPage() {
  const navigate = useNavigate();
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const [hitlRequest, setHitlRequest] = useState<HitlRequest | null>(null);
  const [traceEvents, setTraceEvents] = useState<TraceEvent[]>([]);
  const [tracePanelCollapsed, setTracePanelCollapsed] = useState(false);

  const handleApprovalRequested = (
    approvalId: string,
    toolName: string,
    riskLevel: string,
    args: Record<string, unknown>
  ) => {
    setHitlRequest({
      approval_id: approvalId,
      tool_name: toolName,
      risk_level: riskLevel,
      args_redacted: args,
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

  const handleHitlDecided = (_decision: "approved" | "denied") => {
    setHitlRequest(null);
  };

  const handleTraceEvent = (ev: TraceEvent) => {
    setTraceEvents((prev) => [...prev, ev]);
  };

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Left panel — agent selector */}
      <div className="w-60 shrink-0 border-r border-slate-200 bg-white p-4 flex flex-col gap-4 overflow-y-auto">
        <div>
          <h2 className="text-sm font-semibold text-slate-800 mb-3">Playground</h2>
          <VersionSelector
            selectedAgent={selectedAgent ?? ""}
            onSelect={(name) => {
              setSelectedAgent(name || null);
              setTraceEvents([]);
            }}
          />
        </div>

        <div className="pt-2 border-t border-slate-100">
          <button
            onClick={() => navigate("/playground/datasets")}
            className="flex items-center gap-2 text-xs text-slate-500 hover:text-slate-700 w-full text-left py-1"
          >
            <Database size={12} />
            Manage Datasets / Eval
          </button>
        </div>

        {selectedAgent && (
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

      {/* Center panel — chat */}
      <div className="flex-1 flex flex-col min-w-0 bg-white">
        {selectedAgent && (
          <div className="px-4 py-2 border-b border-slate-100 flex items-center gap-2">
            <span className="text-sm font-medium text-slate-700">{selectedAgent}</span>
            <span className="badge bg-purple-100 text-purple-700 text-xs">sandbox</span>
          </div>
        )}
        <ChatPane
          agentName={selectedAgent}
          onApprovalRequested={handleApprovalRequested}
          onTraceEvent={handleTraceEvent}
        />
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
