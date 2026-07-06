import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Webhook, KeyRound, Copy, Check, CheckCircle2, XCircle, Filter } from "lucide-react";
import { toast } from "sonner";
import {
  listTriggers,
  listAgentEvents,
  rotateToken,
  type AgentEvent,
} from "../../api/registryApi";

interface Props {
  agentName: string;
}

const STATUS_META: Record<
  AgentEvent["status"],
  { icon: typeof CheckCircle2; cls: string; label: string }
> = {
  matched: { icon: CheckCircle2, cls: "text-green-600", label: "matched" },
  filtered: { icon: Filter, cls: "text-amber-600", label: "filtered" },
  rejected: { icon: XCircle, cls: "text-red-600", label: "rejected" },
};

export default function OverviewEventDriven({ agentName }: Props) {
  const qc = useQueryClient();
  const [revealed, setRevealed] = useState<Record<string, string>>({});
  const [copied, setCopied] = useState(false);

  const { data: triggers = [] } = useQuery({
    queryKey: ["triggers", agentName],
    queryFn: () => listTriggers(agentName),
  });
  const { data: events = [] } = useQuery({
    queryKey: ["agentEvents", agentName],
    queryFn: () => listAgentEvents(agentName, { limit: 50 }),
    refetchInterval: 15_000,
  });

  const rotate = useMutation({
    mutationFn: (triggerId: string) => rotateToken(agentName, triggerId),
    onSuccess: (res) => {
      setRevealed((r) => ({ ...r, [res.trigger_id]: res.webhook_url }));
      toast.success("Token rotated — copy the URL now; it won't be shown again.");
      qc.invalidateQueries({ queryKey: ["triggers", agentName] });
    },
    onError: () => toast.error("Failed to rotate token"),
  });

  const webhooks = triggers.filter((t) => t.trigger_type === "webhook");

  const total = events.length;
  const matched = events.filter((e) => e.status === "matched").length;
  const matchRate = total > 0 ? Math.round((matched / total) * 100) : null;
  const lastEvent = events[0];

  const copy = (url: string) => {
    navigator.clipboard.writeText(url);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="space-y-4">
      {webhooks.length === 0 && (
        <div className="card p-5 text-sm text-slate-500">
          No webhook trigger configured. Add a webhook trigger in Settings.
        </div>
      )}

      {webhooks.map((t) => (
        <div key={t.id} className="card p-5">
          <div className="flex items-center gap-3 mb-3">
            <Webhook size={18} className="text-blue-600" />
            <h3 className="text-sm font-semibold text-slate-700">Webhook Endpoint</h3>
            <span
              className={`badge text-xs ${
                t.enabled ? "bg-green-100 text-green-700" : "bg-slate-100 text-slate-500"
              }`}
            >
              {t.enabled ? "enabled" : "disabled"}
            </span>
          </div>

          {/* Webhook URL — plaintext token is only available right after rotation */}
          {revealed[t.id] ? (
            <div className="flex items-center gap-2">
              <code className="flex-1 text-xs bg-slate-50 border border-slate-200 rounded px-2 py-1.5 break-all">
                {revealed[t.id]}
              </code>
              <button
                onClick={() => copy(revealed[t.id])}
                className="btn-secondary text-xs py-1.5"
              >
                {copied ? <Check size={12} /> : <Copy size={12} />}
              </button>
            </div>
          ) : (
            <p className="text-xs text-slate-500 font-mono bg-slate-50 border border-slate-200 rounded px-2 py-1.5">
              POST /hooks/{agentName}/••••••••  (token hidden — rotate to reveal a new URL)
            </p>
          )}

          {/* Filter conditions */}
          {t.filter_conditions != null && (
            <div className="mt-3">
              <span className="text-xs text-slate-500 uppercase">Filter conditions</span>
              <pre className="mt-1 text-xs bg-slate-50 border border-slate-200 rounded p-2 overflow-x-auto">
                {JSON.stringify(t.filter_conditions, null, 2)}
              </pre>
            </div>
          )}

          <button
            onClick={() => rotate.mutate(t.id)}
            disabled={rotate.isPending}
            className="btn-secondary text-xs py-1.5 mt-3 disabled:opacity-50"
          >
            <KeyRound size={12} />
            {rotate.isPending ? "Rotating…" : "Rotate Token"}
          </button>
        </div>
      ))}

      {/* Match rate + last event */}
      <div className="card p-5">
        <h3 className="text-sm font-semibold text-slate-700 mb-3">Activity (last {total} events)</h3>
        <div className="flex items-center gap-6 text-sm">
          <div>
            <p className="text-2xl font-semibold text-slate-800">
              {matchRate == null ? "—" : `${matchRate}%`}
            </p>
            <p className="text-xs text-slate-500">match rate</p>
          </div>
          <div>
            <p className="text-sm text-slate-700">
              {lastEvent ? new Date(lastEvent.received_at).toLocaleString() : "—"}
            </p>
            <p className="text-xs text-slate-500">last event</p>
          </div>
        </div>
      </div>

      {/* Event log */}
      {events.length > 0 && (
        <div className="card p-5">
          <h3 className="text-sm font-semibold text-slate-700 mb-3">Event Log</h3>
          <div className="space-y-1.5">
            {events.map((e) => {
              const meta = STATUS_META[e.status];
              const Icon = meta.icon;
              return (
                <div key={e.id} className="flex items-center gap-2 text-xs">
                  <Icon size={13} className={meta.cls} />
                  <span className={`${meta.cls} font-medium w-16`}>{meta.label}</span>
                  <span className="text-slate-500">
                    {new Date(e.received_at).toLocaleString()}
                  </span>
                  {e.filter_reason && (
                    <span className="text-slate-400 truncate">— {e.filter_reason}</span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
