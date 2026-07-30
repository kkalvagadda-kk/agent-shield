import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  Bot,
  CalendarClock,
  CheckCircle2,
  Loader2,
  Pause,
  Play,
  Trash2,
  Workflow as WorkflowIcon,
  X,
} from "lucide-react";
import { toast } from "sonner";
import {
  listSchedules,
  disarmTrigger,
  disarmWorkflowTrigger,
  enableTrigger,
  disableTrigger,
  updateWorkflowTrigger,
  deleteTrigger,
  deleteWorkflowTrigger,
  type ScheduleListItem,
} from "../api/registryApi";
import { cronHint } from "../lib/cron";
import {
  ARM_PILL_STYLES,
  armDetail,
  armLabel,
  armTone,
  isArmed,
  needsAttention,
} from "../lib/triggerArm";

// ── Cross-artifact schedule operations (R5) ──────────────────────────────────
// The one page that answers "what is scheduled on this platform, and is it
// actually going to run?". It exists because that question had no home: triggers
// were reachable only per-artifact, so nine armed schedules on archived and draft
// workflows fired for weeks without anywhere to notice them.
//
// Read-only endpoint, artifact-scoped writes: every action below routes back to
// the agent or workflow trigger router by `artifact_kind`, so this page adds a
// view without adding a second writer.

const ARTIFACT_STATUS_STYLES: Record<string, string> = {
  active: "bg-green-100 text-green-700",
  published: "bg-green-100 text-green-700",
  draft: "bg-amber-100 text-amber-700",
  archived: "bg-slate-200 text-slate-600",
  deprecated: "bg-slate-200 text-slate-600",
  quarantined: "bg-red-100 text-red-700",
};

const RUN_STATUS_STYLES: Record<string, string> = {
  completed: "bg-green-100 text-green-700",
  running: "bg-blue-100 text-blue-700",
  queued: "bg-slate-100 text-slate-600",
  failed: "bg-red-100 text-red-700",
};

const FILTER_TABS = [
  { value: "all", label: "All" },
  { value: "firing", label: "Will fire" },
  { value: "attention", label: "Needs attention" },
  { value: "disarmed", label: "Disarmed" },
] as const;

type FilterValue = (typeof FILTER_TABS)[number]["value"];

function matchesFilter(s: ScheduleListItem, f: FilterValue): boolean {
  if (f === "all") return true;
  if (f === "firing") return s.will_fire;
  if (f === "attention") return needsAttention(s);
  return !isArmed(s);
}

function relTime(iso: string | null): string {
  if (!iso) return "—";
  const deltaMin = Math.round((Date.parse(iso) - Date.now()) / 60_000);
  const abs = Math.abs(deltaMin);
  const unit =
    abs < 60 ? `${abs}m` : abs < 60 * 24 ? `${Math.round(abs / 60)}h` : `${Math.round(abs / 1440)}d`;
  if (abs < 1) return "just now";
  return deltaMin < 0 ? `${unit} ago` : `in ${unit}`;
}

function artifactHref(s: ScheduleListItem): string {
  return s.artifact_kind === "agent"
    ? `/agents/${s.artifact_name}`
    : `/workflows/${s.artifact_id}/builder`;
}

export default function SchedulesPage() {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<FilterValue>("all");
  const [pendingDelete, setPendingDelete] = useState<ScheduleListItem | null>(null);

  const { data: schedules = [], isLoading } = useQuery({
    queryKey: ["schedules"],
    queryFn: () => listSchedules({ trigger_type: "schedule" }),
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["schedules"] });

  // Every mutation branches on `artifact_kind` and calls the existing
  // artifact-scoped endpoint. No schedule-specific write path exists.
  const disarmMut = useMutation({
    mutationFn: (s: ScheduleListItem) =>
      s.artifact_kind === "agent"
        ? disarmTrigger(s.artifact_name, s.trigger_id)
        : disarmWorkflowTrigger(s.artifact_id, s.trigger_id),
    onSuccess: (_d, s) => {
      toast.success(`Disarmed ${s.artifact_name} — it will not fire again until re-armed.`);
      invalidate();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const toggleMut = useMutation({
    mutationFn: (s: ScheduleListItem) => {
      const next = !s.enabled;
      if (s.artifact_kind === "agent") {
        return next
          ? enableTrigger(s.artifact_name, s.trigger_id)
          : disableTrigger(s.artifact_name, s.trigger_id);
      }
      return updateWorkflowTrigger(s.artifact_id, s.trigger_id, { enabled: next });
    },
    onSuccess: () => invalidate(),
    onError: (e: Error) => toast.error(e.message),
  });

  const deleteMut = useMutation({
    mutationFn: (s: ScheduleListItem) =>
      s.artifact_kind === "agent"
        ? deleteTrigger(s.artifact_name, s.trigger_id)
        : deleteWorkflowTrigger(s.artifact_id, s.trigger_id),
    onSuccess: (_d, s) => {
      toast.success(`Deleted the schedule on ${s.artifact_name}.`);
      setPendingDelete(null);
      invalidate();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const rows = useMemo(
    () => schedules.filter((s) => matchesFilter(s, filter)),
    [schedules, filter],
  );
  const attentionCount = useMemo(() => schedules.filter(needsAttention).length, [schedules]);

  return (
    <div className="max-w-7xl mx-auto px-6 py-8" data-testid="schedules-page">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-slate-900">Schedules</h1>
        <p className="text-sm text-slate-500 mt-0.5">
          Every schedule across agents and workflows, and whether it will actually run.
        </p>
      </div>

      {/* The reason this page exists, stated at the top when it applies. */}
      {attentionCount > 0 && (
        <div
          className="mb-4 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3"
          data-testid="schedules-attention-banner"
        >
          <AlertTriangle size={16} className="text-amber-600 mt-0.5 shrink-0" />
          <p className="text-sm text-amber-800">
            <strong>
              {attentionCount} schedule{attentionCount === 1 ? "" : "s"} will not fire.
            </strong>{" "}
            Each one is switched on but blocked — the reason is on the row. A schedule in this
            state does nothing and reports nothing.
          </p>
        </div>
      )}

      <div className="flex items-center gap-2 mb-4 flex-wrap">
        {FILTER_TABS.map((tab) => (
          <button
            key={tab.value}
            data-testid={`schedules-filter-${tab.value}`}
            onClick={() => setFilter(tab.value)}
            className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
              filter === tab.value
                ? "bg-slate-800 text-white"
                : "bg-slate-100 text-slate-600 hover:bg-slate-200"
            }`}
          >
            {tab.label}
            {tab.value === "attention" && attentionCount > 0 && (
              <span className="ml-1.5 text-amber-500">{attentionCount}</span>
            )}
          </button>
        ))}
      </div>

      {isLoading && (
        <div className="flex items-center justify-center py-20 text-slate-400">
          <Loader2 size={20} className="animate-spin mr-2" />
          Loading…
        </div>
      )}

      {!isLoading && (
        <div className="card p-0 overflow-x-auto">
          {rows.length === 0 ? (
            <div
              className="flex flex-col items-center py-16 text-center"
              data-testid="schedules-empty"
            >
              <CalendarClock size={28} className="text-slate-300 mb-2" />
              <p className="text-slate-500 font-medium">No schedules match this filter.</p>
              <p className="text-sm text-slate-400 mt-1">
                Schedules are created on an agent&apos;s Settings tab or in the workflow builder.
              </p>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50">
                  {[
                    "Artifact",
                    "Cron",
                    "Next fire",
                    "Arm state",
                    "On",
                    "Will fire",
                    "Last run",
                    "Actions",
                  ].map((h) => (
                    <th
                      key={h}
                      className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider whitespace-nowrap"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {rows.map((s) => (
                  <tr
                    key={s.trigger_id}
                    data-testid={`schedules-row-${s.trigger_id}`}
                    className={`hover:bg-slate-50 transition-colors ${
                      needsAttention(s) ? "border-l-2 border-l-amber-400" : ""
                    }`}
                  >
                    {/* Artifact */}
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        {s.artifact_kind === "agent" ? (
                          <Bot size={14} className="text-sky-600 shrink-0" />
                        ) : (
                          <WorkflowIcon size={14} className="text-purple-600 shrink-0" />
                        )}
                        <Link
                          to={artifactHref(s)}
                          className="font-medium text-blue-600 hover:text-blue-800 hover:underline"
                        >
                          {s.artifact_name}
                        </Link>
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                            ARTIFACT_STATUS_STYLES[s.artifact_status] ??
                            "bg-slate-100 text-slate-600"
                          }`}
                        >
                          {s.artifact_status}
                        </span>
                      </div>
                      {s.artifact_team && (
                        <p className="text-xs text-slate-400 mt-0.5 ml-6">{s.artifact_team}</p>
                      )}
                    </td>

                    {/* Cron */}
                    <td className="px-4 py-3">
                      <code className="text-xs bg-slate-100 rounded px-1.5 py-0.5">
                        {s.cron_expression ?? "—"}
                      </code>
                      <p className="text-xs text-slate-400 mt-0.5">
                        {[cronHint(s.cron_expression), s.timezone].filter(Boolean).join(" · ")}
                      </p>
                    </td>

                    {/* Next fire */}
                    <td className="px-4 py-3 whitespace-nowrap" data-testid="schedule-next-fire">
                      {s.will_fire && s.next_fire_at ? (
                        <span className="text-slate-700">{relTime(s.next_fire_at)}</span>
                      ) : (
                        <span className="text-slate-400">—</span>
                      )}
                    </td>

                    {/* Arm state */}
                    <td className="px-4 py-3">
                      <span
                        data-testid="schedule-armed-badge"
                        className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                          ARM_PILL_STYLES[armTone(s)]
                        }`}
                      >
                        {armLabel(s)}
                      </span>
                      {armDetail(s) && (
                        <p
                          className="text-xs text-slate-400 mt-0.5"
                          data-testid={
                            isArmed(s) ? "schedule-armed-detail" : "schedule-disarm-reason"
                          }
                        >
                          {armDetail(s)}
                        </p>
                      )}
                    </td>

                    {/* Enabled — the AUTHOR's switch, deliberately a separate column
                        from arm state so the two are visibly not the same thing. */}
                    <td className="px-4 py-3">
                      <button
                        data-testid="schedule-enabled-toggle"
                        onClick={() => toggleMut.mutate(s)}
                        disabled={toggleMut.isPending}
                        title={s.enabled ? "Pause this schedule" : "Un-pause this schedule"}
                        className="text-slate-400 hover:text-slate-700 disabled:opacity-40"
                      >
                        {s.enabled ? <Play size={15} /> : <Pause size={15} />}
                      </button>
                    </td>

                    {/* Will fire */}
                    <td className="px-4 py-3 max-w-[15rem]">
                      {s.will_fire ? (
                        <span className="inline-flex items-center gap-1 text-green-700 text-xs font-medium">
                          <CheckCircle2 size={14} /> Yes
                        </span>
                      ) : (
                        <span
                          className="inline-flex items-start gap-1 text-amber-700 text-xs"
                          data-testid="schedule-will-not-fire"
                        >
                          <AlertTriangle size={14} className="shrink-0 mt-0.5" />
                          <span>{s.why_not ?? "blocked"}</span>
                        </span>
                      )}
                    </td>

                    {/* Last run — status AND the error text. The error is the whole
                        point: a red badge that cannot say why is what produced the
                        "Failing / No runs yet" screenshot this page came from. */}
                    <td className="px-4 py-3 max-w-[18rem]">
                      {s.last_run_status ? (
                        <>
                          <span
                            className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                              RUN_STATUS_STYLES[s.last_run_status] ?? "bg-slate-100 text-slate-600"
                            }`}
                          >
                            {s.last_run_status}
                          </span>
                          <span className="text-xs text-slate-400 ml-1.5">
                            {relTime(s.last_run_at)}
                          </span>
                          {s.last_run_status === "failed" && (
                            <p
                              className="text-xs text-red-600 mt-0.5 break-words"
                              data-testid="schedule-last-run-error"
                            >
                              {s.last_run_error?.trim()
                                ? s.last_run_error
                                : "no reason recorded — the run path did not write an error message"}
                            </p>
                          )}
                        </>
                      ) : (
                        <span className="text-xs text-slate-400">never run</span>
                      )}
                    </td>

                    {/* Actions — no Arm button here on purpose. Arming is refused
                        unless the artifact is live in production, so the useful thing
                        this page can do is name the blocker and link to the artifact,
                        where the arm control sits next to the deploy control that
                        unblocks it. */}
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        {isArmed(s) && (
                          <button
                            data-testid="schedule-disarm-btn"
                            onClick={() => disarmMut.mutate(s)}
                            disabled={disarmMut.isPending}
                            title="Disarm — stops firing until an operator re-arms it"
                            className="text-xs text-slate-500 hover:text-slate-800 disabled:opacity-40"
                          >
                            Disarm
                          </button>
                        )}
                        <button
                          data-testid="schedule-delete-btn"
                          onClick={() => setPendingDelete(s)}
                          title="Delete this schedule"
                          className="text-slate-400 hover:text-red-600"
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {pendingDelete && (
        <DeleteScheduleModal
          schedule={pendingDelete}
          pending={deleteMut.isPending}
          onCancel={() => setPendingDelete(null)}
          onConfirm={() => deleteMut.mutate(pendingDelete)}
        />
      )}
    </div>
  );
}

// An in-app modal, not window.confirm() — studio 0.1.171 replaced a native confirm
// for exactly this kind of destructive action.
function DeleteScheduleModal({
  schedule,
  pending,
  onCancel,
  onConfirm,
}: {
  schedule: ScheduleListItem;
  pending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onCancel}
    >
      <div className="card w-full max-w-md p-6 bg-white" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold text-slate-900">Delete this schedule?</h2>
          <button onClick={onCancel} className="text-slate-400 hover:text-slate-600">
            <X size={18} />
          </button>
        </div>
        <p className="text-sm text-slate-600">
          <code className="text-xs bg-slate-100 rounded px-1.5 py-0.5">
            {schedule.cron_expression}
          </code>{" "}
          on <strong>{schedule.artifact_name}</strong>. The {schedule.artifact_kind} itself is not
          affected — only this schedule is removed, and it cannot be recovered.
        </p>
        <div className="flex justify-end gap-2 mt-6">
          <button onClick={onCancel} className="btn-secondary text-sm">
            Cancel
          </button>
          <button
            data-testid="schedule-delete-confirm"
            onClick={onConfirm}
            disabled={pending}
            className="btn-primary text-sm bg-red-600 hover:bg-red-700"
          >
            {pending ? (
              <>
                <Loader2 size={14} className="animate-spin" /> Deleting…
              </>
            ) : (
              "Delete schedule"
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
