import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { X, Loader2 } from "lucide-react";
import { toast } from "sonner";
import {
  updateTrigger,
  updateWorkflowTrigger,
  type ScheduleListItem,
} from "../../api/registryApi";
import { cronHint } from "../../lib/cron";

// Edit a schedule in place (R5).
//
// Routes back to the EXISTING artifact-scoped trigger routers, branching on
// `artifact_kind` — the same rule the rest of this page follows. The schedules
// endpoint stays read-only; adding a write path here would make a second writer for
// rows that already have one, which is the drift this workstream exists to remove.
//
// Cron is NOT validated client-side beyond "five fields". The server owns the
// contract (`croniter` in `_next_fire`), and a second, weaker copy of that rule in the
// browser would eventually disagree with it — rejecting expressions the scheduler
// accepts, or worse, accepting ones it does not.

const TIMEZONES = ["UTC", "America/New_York", "America/Chicago", "America/Los_Angeles",
  "Europe/London", "Europe/Berlin", "Asia/Kolkata", "Asia/Tokyo", "Australia/Sydney"];

export default function EditScheduleModal({
  schedule,
  onClose,
}: {
  schedule: ScheduleListItem;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [cron, setCron] = useState(schedule.cron_expression ?? "");
  const [tz, setTz] = useState(schedule.timezone ?? "UTC");
  const [payload, setPayload] = useState(
    schedule.input_payload ? JSON.stringify(schedule.input_payload, null, 2) : "",
  );
  const [payloadError, setPayloadError] = useState<string | null>(null);

  const fields = cron.trim().split(/\s+/).filter(Boolean).length;
  const cronLooksWrong = cron.trim() !== "" && fields !== 5;

  const save = useMutation({
    mutationFn: () => {
      // Parsed HERE, not on submit-and-hope: a malformed payload would otherwise be
      // sent as a string and land as a 422 the operator has to decode.
      let parsed: Record<string, unknown> | null = null;
      if (payload.trim()) parsed = JSON.parse(payload) as Record<string, unknown>;
      const body = { cron_expression: cron.trim(), timezone: tz, input_payload: parsed };
      return schedule.artifact_kind === "agent"
        ? updateTrigger(schedule.artifact_name, schedule.trigger_id, body)
        : updateWorkflowTrigger(schedule.artifact_id, schedule.trigger_id, body);
    },
    onSuccess: () => {
      toast.success(`Updated the schedule on ${schedule.artifact_name}.`);
      qc.invalidateQueries({ queryKey: ["schedules"] });
      onClose();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const submit = () => {
    setPayloadError(null);
    if (payload.trim()) {
      try {
        JSON.parse(payload);
      } catch (e) {
        setPayloadError((e as Error).message);
        return;
      }
    }
    save.mutate();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4">
      <div
        className="w-full max-w-lg rounded-lg bg-white shadow-xl"
        data-testid="edit-schedule-modal"
      >
        <div className="flex items-start justify-between border-b border-slate-100 px-5 py-4">
          <div>
            <h2 className="text-sm font-semibold text-slate-900">Edit schedule</h2>
            <p className="text-xs text-slate-500 mt-0.5">
              {schedule.artifact_kind} <strong>{schedule.artifact_name}</strong>
            </p>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700">
            <X size={16} />
          </button>
        </div>

        <div className="space-y-4 px-5 py-4">
          <label className="block">
            <span className="text-xs font-medium uppercase tracking-wider text-slate-500">
              Cron expression
            </span>
            <input
              data-testid="edit-schedule-cron"
              value={cron}
              onChange={(e) => setCron(e.target.value)}
              className="input mt-1 font-mono text-sm"
              placeholder="0 9 * * 1"
            />
            <p className="mt-1 text-xs text-slate-400">
              {cronLooksWrong
                ? `${fields} field${fields === 1 ? "" : "s"} — a cron expression has 5`
                : cronHint(cron) ?? " "}
            </p>
          </label>

          <label className="block">
            <span className="text-xs font-medium uppercase tracking-wider text-slate-500">
              Timezone
            </span>
            <select
              data-testid="edit-schedule-tz"
              aria-label="Timezone"
              value={tz}
              onChange={(e) => setTz(e.target.value)}
              className="input mt-1"
            >
              {/* Whatever the row already carries stays selectable even if it is not
                  in the shortlist — otherwise opening the modal would silently
                  rewrite a timezone the operator never touched. */}
              {(TIMEZONES.includes(tz) ? TIMEZONES : [tz, ...TIMEZONES]).map((z) => (
                <option key={z} value={z}>{z}</option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="text-xs font-medium uppercase tracking-wider text-slate-500">
              Input payload — JSON job spec (optional)
            </span>
            <textarea
              data-testid="edit-schedule-payload"
              value={payload}
              onChange={(e) => setPayload(e.target.value)}
              rows={5}
              className="input mt-1 resize-none font-mono text-xs"
              placeholder={'{\n  "task": "weekly-report"\n}'}
            />
            {payloadError && (
              <p className="mt-1 text-xs text-red-600" data-testid="edit-schedule-payload-error">
                {payloadError}
              </p>
            )}
          </label>
        </div>

        <div className="flex justify-end gap-2 border-t border-slate-100 px-5 py-3">
          <button onClick={onClose} className="btn-secondary text-xs py-1.5">
            Cancel
          </button>
          <button
            data-testid="edit-schedule-save"
            onClick={submit}
            disabled={save.isPending || cronLooksWrong || cron.trim() === ""}
            className="btn-primary text-xs py-1.5 disabled:opacity-50"
          >
            {save.isPending ? <Loader2 size={12} className="animate-spin" /> : null}
            Save changes
          </button>
        </div>
      </div>
    </div>
  );
}
