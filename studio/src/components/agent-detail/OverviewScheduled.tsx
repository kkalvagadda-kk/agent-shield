import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Clock,
  Play,
  Pause,
  CalendarClock,
  Bell,
  BellOff,
  AlertTriangle,
} from "lucide-react";
import {
  listTriggers,
  enableTrigger,
  disableTrigger,
  listTriggerRuns,
  getAgentHealth,
  AgentHealthStatus,
  DeploymentContext,
} from "../../api/registryApi";

interface Props {
  agentName: string;
  deploymentId: string;
  context: DeploymentContext;
}

// Lightweight human hint for the common cron shapes (no external dep).
function describeCron(expr: string | null): string {
  if (!expr) return "—";
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return expr;
  const [min, hr, dom, mon, dow] = parts;
  if (expr === "* * * * *") return "every minute";
  if (min !== "*" && hr !== "*" && dom === "*" && mon === "*" && dow === "*")
    return `daily at ${hr.padStart(2, "0")}:${min.padStart(2, "0")}`;
  if (min.startsWith("*/")) return `every ${min.slice(2)} minutes`;
  if (hr.startsWith("*/")) return `every ${hr.slice(2)} hours`;
  return expr;
}

// Rolled-up schedule-health badge styling (matches the last-run badge palette).
const HEALTH_STYLES: Record<AgentHealthStatus, string> = {
  healthy: "bg-green-100 text-green-700",
  degraded: "bg-amber-100 text-amber-700",
  failing: "bg-red-100 text-red-700",
};

// `deploymentId`/`context` are part of the shared `OverviewProps` contract (all four
// shape overviews take them) but are deliberately NOT read here. Runs on this surface
// are scoped to the SCHEDULE, not to the deployment being viewed — see the run query
// below. Destructuring only what we use keeps that deliberate choice visible instead
// of leaving two unused parameters that look like an oversight.
export default function OverviewScheduled({ agentName }: Props) {
  const qc = useQueryClient();

  const { data: triggers = [] } = useQuery({
    queryKey: ["triggers", agentName],
    queryFn: () => listTriggers(agentName),
  });
  // Mode-aware health signals: next_fire_at (croniter over the first enabled cron),
  // last_run_status, last_error, missed_fires and a rolled-up `health`.
  const { data: health } = useQuery({
    queryKey: ["agent-health", agentName],
    queryFn: () => getAgentHealth(agentName),
  });

  const toggle = useMutation({
    mutationFn: ({ id, on }: { id: string; on: boolean }) =>
      on ? enableTrigger(agentName, id) : disableTrigger(agentName, id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["triggers", agentName] }),
  });

  const schedules = triggers.filter((t) => t.trigger_type === "schedule");
  // The schedule this surface reports on: the first enabled one, else the first.
  // Alert config and run history both key off it, so the cards on this page always
  // describe the SAME schedule rather than mixing sources.
  const primarySchedule = schedules.find((t) => t.enabled) ?? schedules[0];
  const alertTrigger = primarySchedule;

  // Runs come from the SCHEDULE, not the deployment. This card used to call
  // `listDeploymentRuns`, which filters on `production_deployment_id` /
  // `sandbox_deployment_id` — both NULL on every trigger-driven run — so it was
  // permanently empty while the health badge (which reads across the agent) went
  // red. That is how "Failing" ended up rendered directly above "No runs yet".
  // Keyed on the trigger, badge and list now answer from the same set.
  const { data: runs = [] } = useQuery({
    queryKey: ["trigger-runs", agentName, primarySchedule?.id],
    queryFn: () => listTriggerRuns(agentName, primarySchedule!.id, { limit: 10 }),
    enabled: Boolean(primarySchedule?.id),
  });

  const lastRun = runs[0];
  // A failure is only actionable with its reason. Prefer the run's own
  // `error_message`; fall back to the health producer's `last_error` so the badge
  // is never the only thing on screen saying something is wrong.
  const lastFailureReason =
    (lastRun?.status === "failed" ? lastRun.error_message : null) ?? health?.last_error ?? null;

  return (
    <div className="space-y-4">
      {/* Schedule cards */}
      {schedules.length === 0 && (
        <div className="card p-5 text-sm text-slate-500">
          No schedule configured. Add a schedule trigger in Settings.
        </div>
      )}
      {schedules.map((t) => (
        <div key={t.id} className="card p-5">
          <div className="flex items-start justify-between">
            <div className="flex items-center gap-3">
              <Clock size={18} className="text-purple-600" />
              <div>
                <p className="font-mono text-sm text-slate-800">{t.cron_expression}</p>
                <p className="text-xs text-slate-500 mt-0.5">
                  {describeCron(t.cron_expression)} · {t.timezone || "UTC"}
                </p>
              </div>
            </div>
            <button
              onClick={() => toggle.mutate({ id: t.id, on: !t.enabled })}
              className={`inline-flex items-center gap-1.5 px-3 py-1 rounded text-xs font-medium ${
                t.enabled
                  ? "bg-green-50 text-green-700 hover:bg-green-100"
                  : "bg-slate-100 text-slate-500 hover:bg-slate-200"
              }`}
            >
              {t.enabled ? <Play size={12} /> : <Pause size={12} />}
              {t.enabled ? "Enabled" : "Disabled"}
            </button>
          </div>
        </div>
      ))}

      {/* Schedule health: next-fire + rolled-up health badge */}
      {schedules.length > 0 && (
        <div className="card p-5">
          <div className="flex items-start justify-between">
            <div className="flex items-center gap-3">
              <CalendarClock size={18} className="text-purple-600" />
              <div>
                <p className="text-sm font-semibold text-slate-700">Next Fire</p>
                <p className="text-sm text-slate-600 mt-0.5">
                  {health?.next_fire_at
                    ? new Date(health.next_fire_at).toLocaleString()
                    : "—"}
                </p>
                {typeof health?.missed_fires === "number" && health.missed_fires > 0 && (
                  <p className="text-xs text-amber-600 mt-0.5">
                    {health.missed_fires} missed fire{health.missed_fires === 1 ? "" : "s"}
                  </p>
                )}
              </div>
            </div>
            {health?.health && (
              <span
                className={`badge text-xs capitalize ${HEALTH_STYLES[health.health]}`}
                title="Rolled-up schedule health"
              >
                {health.health}
              </span>
            )}
          </div>
          {/* A red badge must be able to explain itself. Previously "Failing" was the
              entire message and the reason lived only in a run row this page never
              read — the operator's only next step was kubectl. */}
          {health?.health === "failing" && lastFailureReason && (
            <div
              data-testid="schedule-health-reason"
              className="mt-3 flex items-start gap-2 rounded bg-red-50 border border-red-200 p-2.5"
            >
              <AlertTriangle size={14} className="text-red-500 shrink-0 mt-0.5" />
              <p className="text-xs text-red-700 break-words">{lastFailureReason}</p>
            </div>
          )}
        </div>
      )}

      {/* Alert-config summary */}
      {schedules.length > 0 && (
        <div className="card p-5">
          <div className="flex items-center gap-3">
            {alertTrigger?.alert_on_failure ? (
              <Bell size={18} className="text-purple-600" />
            ) : (
              <BellOff size={18} className="text-slate-400" />
            )}
            <div>
              <p className="text-sm font-semibold text-slate-700">Failure Alerts</p>
              {/* "On" with no address is OFF in practice: alerting.py returns at the
                  `if not trigger.alert_email` guard and logs at debug. Rendering that
                  state as a green "On" told the operator they were covered when no
                  notification could ever be sent. Say what actually happens. */}
              {alertTrigger?.alert_on_failure && !alertTrigger?.alert_email ? (
                <>
                  <p
                    data-testid="alert-config-incomplete"
                    className="text-sm text-amber-700 mt-0.5 font-medium"
                  >
                    On — but not delivered
                  </p>
                  <p className="text-xs text-amber-600 mt-0.5">
                    No alert email set, so failures notify nobody. Add one in Settings.
                  </p>
                </>
              ) : (
                <>
                  <p className="text-sm text-slate-600 mt-0.5">
                    {alertTrigger?.alert_on_failure ? "On" : "Off"}
                  </p>
                  <p className="text-xs text-slate-500 mt-0.5">
                    {alertTrigger?.alert_email
                      ? `Notifies ${alertTrigger.alert_email}`
                      : "No alert email set"}
                  </p>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Last run status */}
      <div className="card p-5">
        <h3 className="text-sm font-semibold text-slate-700 mb-3">Last Run</h3>
        {lastRun ? (
          <div>
            <div className="flex items-center gap-3 text-sm">
              <span
                className={`badge text-xs ${
                  lastRun.status === "completed"
                    ? "bg-green-100 text-green-700"
                    : lastRun.status === "failed"
                    ? "bg-red-100 text-red-700"
                    : "bg-slate-100 text-slate-600"
                }`}
              >
                {lastRun.status}
              </span>
              <span className="text-slate-500">
                {new Date(lastRun.started_at).toLocaleString()}
              </span>
              {lastRun.trigger_type && (
                <span className="text-xs text-slate-400">via {lastRun.trigger_type}</span>
              )}
            </div>
            {/* The reason was in the payload all along (`error_message`) and simply
                never rendered — the single highest-value line on this page. */}
            {lastRun.status === "failed" && lastRun.error_message && (
              <p
                data-testid="last-run-error"
                className="mt-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2.5 break-words"
              >
                {lastRun.error_message}
              </p>
            )}
          </div>
        ) : (
          <p className="text-sm text-slate-500">No runs yet.</p>
        )}
      </div>

      {/* Recent run history */}
      {runs.length > 0 && (
        <div className="card p-5">
          <h3 className="text-sm font-semibold text-slate-700 mb-3">Recent Runs</h3>
          <div className="space-y-1.5">
            {runs.map((r) => (
              <div key={r.id}>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-slate-500">
                    {new Date(r.started_at).toLocaleString()}
                  </span>
                  <span className="text-slate-400">{r.trigger_type || "—"}</span>
                  <span
                    className={
                      r.status === "completed"
                        ? "text-green-600"
                        : r.status === "failed"
                        ? "text-red-600"
                        : "text-slate-500"
                    }
                  >
                    {r.status}
                  </span>
                </div>
                {r.status === "failed" && r.error_message && (
                  <p className="text-[11px] text-red-600 mt-0.5 break-words">
                    {r.error_message}
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
