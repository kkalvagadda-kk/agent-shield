import { Loader2, Send, Webhook } from "lucide-react";
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { listTriggers, type AgentTrigger } from "../../api/registryApi";
import { testEvent } from "../../api/playgroundApi";

interface TestTriggerPanelProps {
  agentName: string;
  onRunStarted: (runId: string) => void;
}

interface EventLogEntry {
  matched: boolean;
  reason: string;
  runId?: string;
  timestamp: string;
}

export default function TestTriggerPanel({ agentName, onRunStarted }: TestTriggerPanelProps) {
  const [payload, setPayload] = useState('{\n  "event": "push",\n  "repository": "my-repo"\n}');
  const [parseError, setParseError] = useState<string | null>(null);
  const [eventLog, setEventLog] = useState<EventLogEntry[]>([]);

  const { data: triggers } = useQuery({
    queryKey: ["triggers", agentName],
    queryFn: () => listTriggers(agentName),
  });

  const webhookTriggers = (triggers || []).filter(
    (t: AgentTrigger) => t.trigger_type === "webhook"
  );

  const mutation = useMutation({
    mutationFn: (parsedPayload: Record<string, unknown>) =>
      testEvent(agentName, parsedPayload),
    onSuccess: (data) => {
      setEventLog((prev) => [
        {
          matched: data.matched,
          reason: data.reason,
          runId: data.run_id,
          timestamp: new Date().toISOString(),
        },
        ...prev,
      ]);
      if (data.matched && data.run_id) {
        onRunStarted(data.run_id);
      }
    },
  });

  const handleSend = () => {
    try {
      const parsed = JSON.parse(payload);
      setParseError(null);
      mutation.mutate(parsed);
    } catch {
      setParseError("Invalid JSON payload");
    }
  };

  return (
    <div className="space-y-4">
      <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider flex items-center gap-1.5">
        <Webhook size={12} />
        Event-Driven Agent
      </h3>

      {webhookTriggers.length > 0 && (
        <div className="space-y-2">
          {webhookTriggers.map((trigger: AgentTrigger) => (
            <div
              key={trigger.id}
              className="rounded-lg border border-slate-200 bg-slate-50 p-3"
            >
              <p className="text-xs text-slate-500">
                Filter: {trigger.filter_conditions
                  ? JSON.stringify(trigger.filter_conditions)
                  : "none"}
              </p>
            </div>
          ))}
        </div>
      )}

      <div className="space-y-2">
        <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wider">
          Test Payload (JSON)
        </label>
        <textarea
          value={payload}
          onChange={(e) => {
            setPayload(e.target.value);
            setParseError(null);
          }}
          className="input font-mono text-sm resize-none"
          rows={5}
          placeholder='{"event": "..."}'
        />
        {parseError && <p className="text-xs text-red-500">{parseError}</p>}
      </div>

      <button
        onClick={handleSend}
        disabled={mutation.isPending}
        className="btn-primary text-sm"
      >
        {mutation.isPending ? (
          <><Loader2 size={14} className="animate-spin" /> Sending…</>
        ) : (
          <><Send size={14} /> Send Test Event</>
        )}
      </button>

      {eventLog.length > 0 && (
        <div className="space-y-1 mt-3">
          <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
            Event Log
          </h4>
          {eventLog.map((entry, i) => (
            <div
              key={i}
              className={`text-xs px-2 py-1.5 rounded ${
                entry.matched
                  ? "bg-green-50 text-green-700"
                  : "bg-slate-50 text-slate-500"
              }`}
            >
              <span className="font-medium">
                {entry.matched ? "Matched" : "Filtered"}
              </span>
              {" — "}
              {entry.reason}
              {entry.runId && (
                <span className="ml-1 text-blue-600 font-mono">
                  (run: {entry.runId.slice(0, 8)}…)
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
