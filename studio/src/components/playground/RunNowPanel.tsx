import { Loader2, Play, Clock } from "lucide-react";
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { listTriggers, type AgentTrigger } from "../../api/registryApi";
import { createPlaygroundRun } from "../../api/playgroundApi";

interface RunNowPanelProps {
  agentName: string;
  onRunStarted: (runId: string) => void;
}

function parseCronHuman(cron: string): string {
  const parts = cron.split(/\s+/);
  if (parts.length < 5) return cron;
  const [min, hour, dom, mon, dow] = parts;
  if (min === "0" && hour === "*") return "Every hour";
  if (min === "0" && hour.startsWith("*/")) return `Every ${hour.slice(2)} hours`;
  if (hour.startsWith("*/")) return `Every ${hour.slice(2)} hours at minute ${min}`;
  if (dom === "*" && mon === "*" && dow === "*") return `Daily at ${hour}:${min.padStart(2, "0")}`;
  return cron;
}

export default function RunNowPanel({ agentName, onRunStarted }: RunNowPanelProps) {
  const { data: triggers } = useQuery({
    queryKey: ["triggers", agentName],
    queryFn: () => listTriggers(agentName),
  });

  const scheduleTriggers = (triggers || []).filter(
    (t: AgentTrigger) => t.trigger_type === "schedule"
  );

  const mutation = useMutation({
    mutationFn: () =>
      createPlaygroundRun(agentName, undefined, undefined),
    onSuccess: (data) => {
      onRunStarted(data.run_id);
    },
  });

  return (
    <div className="space-y-4">
      <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider flex items-center gap-1.5">
        <Clock size={12} />
        Scheduled Agent
      </h3>

      {scheduleTriggers.length > 0 ? (
        <div className="space-y-2">
          {scheduleTriggers.map((trigger: AgentTrigger) => (
            <div
              key={trigger.id}
              className="rounded-lg border border-slate-200 bg-slate-50 p-3"
            >
              <p className="text-sm font-mono text-slate-700">
                {trigger.cron_expression}
              </p>
              <p className="text-xs text-slate-500 mt-0.5">
                {parseCronHuman(trigger.cron_expression || "")}
                {trigger.timezone && ` (${trigger.timezone})`}
              </p>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm text-slate-400">No schedule triggers configured.</p>
      )}

      <button
        onClick={() => mutation.mutate()}
        disabled={mutation.isPending}
        className="btn-primary text-sm"
      >
        {mutation.isPending ? (
          <><Loader2 size={14} className="animate-spin" /> Running…</>
        ) : (
          <><Play size={14} /> Run Now (Test Fire)</>
        )}
      </button>

      {mutation.isError && (
        <p className="text-xs text-red-500">
          {mutation.error instanceof Error ? mutation.error.message : "Failed to launch"}
        </p>
      )}
    </div>
  );
}
