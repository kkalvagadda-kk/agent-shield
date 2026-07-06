import { CheckCircle2, Circle, Loader2, AlertTriangle, XCircle, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import type { StepUpdateEvent } from "../../api/playgroundApi";

interface StepTrackerProps {
  runId: string;
  streamUrl: string;
}

const STATUS_ICON: Record<string, React.ReactNode> = {
  pending:            <Circle size={14} className="text-slate-400" />,
  running:            <Loader2 size={14} className="animate-spin text-blue-500" />,
  completed:          <CheckCircle2 size={14} className="text-green-600" />,
  failed:             <XCircle size={14} className="text-red-500" />,
  awaiting_approval:  <ShieldCheck size={14} className="text-amber-500" />,
  cancelled:          <AlertTriangle size={14} className="text-slate-400" />,
};

const STATUS_LABEL: Record<string, string> = {
  pending: "Pending",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
  awaiting_approval: "Awaiting Approval",
  cancelled: "Cancelled",
};

export default function StepTracker({ runId, streamUrl }: StepTrackerProps) {
  const [steps, setSteps] = useState<StepUpdateEvent[]>([]);
  const [selectedStep, setSelectedStep] = useState<number | null>(null);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const url = streamUrl;
    const es = new EventSource(url);

    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.event === "done") {
          setDone(true);
          es.close();
          return;
        }
        if (data.event === "error") {
          setError(data.message || "Stream error");
          es.close();
          return;
        }
        if (data.event === "step_update") {
          setSteps((prev) => {
            const existing = prev.findIndex((s) => s.step_number === data.step_number);
            if (existing >= 0) {
              const updated = [...prev];
              updated[existing] = data;
              return updated;
            }
            return [...prev, data];
          });
        }
      } catch {
        // ignore parse errors
      }
    };

    es.onerror = () => {
      if (!done) setError("Connection lost");
      es.close();
    };

    return () => es.close();
  }, [runId, streamUrl]);

  const selected = selectedStep !== null
    ? steps.find((s) => s.step_number === selectedStep)
    : null;

  return (
    <div className="space-y-3">
      <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
        Steps
      </h3>

      {steps.length === 0 && !done && !error && (
        <div className="flex items-center gap-2 text-sm text-slate-400">
          <Loader2 size={14} className="animate-spin" />
          Waiting for steps…
        </div>
      )}

      <div className="space-y-1">
        {steps.map((step) => (
          <button
            key={step.step_number}
            onClick={() => setSelectedStep(step.step_number)}
            className={`w-full flex items-center gap-2 px-3 py-2 rounded text-sm text-left transition-colors ${
              selectedStep === step.step_number
                ? "bg-blue-50 border border-blue-200"
                : "hover:bg-slate-50 border border-transparent"
            }`}
          >
            {STATUS_ICON[step.status] || <Circle size={14} />}
            <span className="flex-1 font-medium text-slate-700">{step.step_name}</span>
            <span className="text-xs text-slate-400">
              {STATUS_LABEL[step.status] || step.status}
            </span>
          </button>
        ))}
      </div>

      {selected && (
        <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
          <p className="text-xs font-semibold text-slate-500 mb-1">
            Step {selected.step_number}: {selected.step_name}
          </p>
          {selected.output != null && (
            <pre className="text-xs text-slate-600 whitespace-pre-wrap overflow-x-auto">
              {typeof selected.output === "string"
                ? selected.output
                : JSON.stringify(selected.output as Record<string, unknown>, null, 2)}
            </pre>
          )}
          {selected.status === "awaiting_approval" && selected.approval_id && (
            <p className="mt-2 text-xs text-amber-600">
              Approval required (ID: {selected.approval_id})
            </p>
          )}
        </div>
      )}

      {error && (
        <p className="text-xs text-red-500">{error}</p>
      )}

      {done && (
        <p className="text-xs text-green-600 font-medium">Run complete</p>
      )}
    </div>
  );
}
