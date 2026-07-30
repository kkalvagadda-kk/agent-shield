import { useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, Clock, Plus, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import { cronHint } from "../../lib/cron";
import { ARM_PILL_STYLES, armDetail, armLabel, armTone, isArmed } from "../../lib/triggerArm";

// UX-preview mock of the agent Settings → Schedule Triggers section.
//
// A COPY, deliberately: the real SettingsTab.tsx is untouched by this pass, so no
// production component carries a DEMO conditional. The layout mirrors
// components/agent-detail/SettingsTab.tsx (TriggerRow, L350) closely enough to
// judge the change, which is only ever additive to that row:
//   1. an arm-state pill + who/why line
//   2. Arm / Disarm buttons
//   3. a Delete button (deleteTrigger has existed unwired since it was written)
// Everything the row already had — cron, timezone, approver role, alerts, the
// enabled checkbox — stays exactly where it is. Schedule config does NOT move.

interface MockTrigger {
  id: string;
  cron_expression: string;
  timezone: string;
  enabled: boolean;
  armed_at: string | null;
  armed_by: string | null;
  disarmed_at: string | null;
  disarm_reason: string | null;
  // Mock-only: stands in for `resolve_dispatch_target` succeeding. In the real
  // build the server answers this, using the same call the run door uses, so
  // "can I arm?" and "where would it dispatch?" cannot disagree.
  productionReady: boolean;
}

const INITIAL: MockTrigger[] = [
  {
    id: "t-1",
    cron_expression: "0 9 * * 1",
    timezone: "America/New_York",
    enabled: true,
    armed_at: "2026-07-14T09:00:00Z",
    armed_by: "kalyan",
    disarmed_at: null,
    disarm_reason: null,
    productionReady: true,
  },
  {
    id: "t-2",
    cron_expression: "0 2 * * *",
    timezone: "UTC",
    enabled: true,
    armed_at: null,
    armed_by: null,
    disarmed_at: null,
    disarm_reason: null,
    productionReady: false,
  },
];

const REFUSAL =
  "agent 'weekly-digest' has no running production deployment — it is deployed to sandbox. " +
  "Schedule and webhook triggers dispatch to production; deploy the agent to production " +
  "(or publish it) before arming a trigger.";

export default function ArmDisarmSettingsPreview() {
  const [triggers, setTriggers] = useState<MockTrigger[]>(INITIAL);
  const [pendingDelete, setPendingDelete] = useState<MockTrigger | null>(null);

  const patch = (id: string, next: Partial<MockTrigger>) =>
    setTriggers((ts) => ts.map((t) => (t.id === id ? { ...t, ...next } : t)));

  const arm = (t: MockTrigger) => {
    if (!t.productionReady) {
      // The server's 409 detail, surfaced verbatim. The message is already written
      // for an operator — re-wording it in the client would be a second voice for
      // one fact, and the less accurate one.
      toast.error(REFUSAL);
      return;
    }
    patch(t.id, {
      armed_at: new Date().toISOString(),
      armed_by: "demo",
      disarmed_at: null,
      disarm_reason: null,
    });
    toast.success("Armed — this schedule will fire on its next cron tick.");
  };

  const disarm = (t: MockTrigger) => {
    patch(t.id, {
      armed_at: null,
      disarmed_at: new Date().toISOString(),
      disarm_reason: "disarmed by operator",
    });
    toast.success("Disarmed. Re-arming is an explicit action — nothing re-arms it for you.");
  };

  return (
    <div className="max-w-3xl mx-auto px-6 py-8">
      <Link
        to="/preview/schedules"
        className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800 mb-4"
      >
        <ArrowLeft size={14} /> All preview screens
      </Link>

      <h1 className="text-2xl font-bold text-slate-900">weekly-digest</h1>
      <p className="text-sm text-slate-500 mt-0.5">Settings — mock of the real Settings tab</p>

      <div className="flex gap-2 mt-4 border-b border-slate-200">
        {["Deployments", "Versions", "Settings"].map((t) => (
          <span
            key={t}
            className={`px-3 py-2 text-sm ${
              t === "Settings"
                ? "border-b-2 border-blue-500 text-blue-600 font-medium"
                : "text-slate-400"
            }`}
          >
            {t}
          </span>
        ))}
      </div>

      <section className="mt-6">
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-semibold text-slate-900">Schedule Triggers</h2>
          <button className="btn-secondary text-xs inline-flex items-center gap-1">
            <Plus size={12} /> New schedule trigger
          </button>
        </div>

        <div className="space-y-3">
          {triggers.map((t) => (
            <div
              key={t.id}
              className="border border-slate-200 rounded-lg p-4 space-y-3"
              data-testid={`preview-trigger-${t.id}`}
            >
              {/* ── NEW: arm state, at the top of the row ── */}
              <div className="flex items-start justify-between gap-3 border-b border-slate-100 pb-3">
                <div>
                  <span
                    data-testid="trigger-arm-state"
                    className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                      ARM_PILL_STYLES[armTone(t)]
                    }`}
                  >
                    {armLabel(t)}
                  </span>
                  <p className="text-xs text-slate-400 mt-1">
                    {armDetail(t) ??
                      "never armed — it will not fire until an operator arms it in production"}
                  </p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {isArmed(t) ? (
                    <button
                      data-testid="trigger-disarm-btn"
                      onClick={() => disarm(t)}
                      className="btn-secondary text-xs"
                    >
                      Disarm
                    </button>
                  ) : (
                    <button
                      data-testid="trigger-arm-btn"
                      onClick={() => arm(t)}
                      className="btn-primary text-xs"
                    >
                      Arm
                    </button>
                  )}
                  <button
                    data-testid="trigger-delete-btn"
                    onClick={() => setPendingDelete(t)}
                    className="text-slate-400 hover:text-red-600"
                    title="Delete this trigger"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>

              {/* ── UNCHANGED: everything the row already had ── */}
              <div className="grid grid-cols-2 gap-3">
                <label className="block">
                  <span className="text-xs text-slate-500 uppercase">Cron expression</span>
                  <input
                    value={t.cron_expression}
                    onChange={(e) => patch(t.id, { cron_expression: e.target.value })}
                    className="mt-1 w-full font-mono text-sm border border-slate-300 rounded px-2 py-1.5"
                  />
                  <span className="text-xs text-slate-400">
                    {cronHint(t.cron_expression)}
                  </span>
                </label>
                <label className="block">
                  <span className="text-xs text-slate-500 uppercase">Timezone</span>
                  <input
                    value={t.timezone}
                    readOnly
                    className="mt-1 w-full text-sm border border-slate-300 rounded px-2 py-1.5 bg-slate-50"
                  />
                </label>
              </div>

              <div className="border-t border-slate-100 pt-3">
                <label className="inline-flex items-center gap-2 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    checked={t.enabled}
                    onChange={(e) => patch(t.id, { enabled: e.target.checked })}
                    className="rounded"
                  />
                  Enabled
                </label>
                <p className="text-xs text-slate-400 mt-1">
                  &ldquo;Enabled&rdquo; is your pause switch. It is not the same as armed — a paused
                  schedule keeps its arming, and an armed schedule that you pause stops firing
                  without losing who authorised it.
                </p>
              </div>
            </div>
          ))}
        </div>

        <div className="mt-6 flex items-center gap-2 text-xs text-slate-400">
          <Clock size={12} /> Schedule configuration stays here, on the artifact. Only arming moved.
        </div>
      </section>

      {pendingDelete && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => setPendingDelete(null)}
        >
          <div className="card w-full max-w-md p-6 bg-white" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-lg font-semibold text-slate-900">Delete this schedule?</h2>
              <button
                onClick={() => setPendingDelete(null)}
                className="text-slate-400 hover:text-slate-600"
              >
                <X size={18} />
              </button>
            </div>
            <p className="text-sm text-slate-600">
              <code className="text-xs bg-slate-100 rounded px-1.5 py-0.5">
                {pendingDelete.cron_expression}
              </code>{" "}
              on <strong>weekly-digest</strong>. The agent is not affected.
            </p>
            <div className="flex justify-end gap-2 mt-6">
              <button onClick={() => setPendingDelete(null)} className="btn-secondary text-sm">
                Cancel
              </button>
              <button
                data-testid="trigger-delete-confirm"
                onClick={() => {
                  setTriggers((ts) => ts.filter((x) => x.id !== pendingDelete.id));
                  setPendingDelete(null);
                  toast.success("Schedule deleted.");
                }}
                className="btn-primary text-sm bg-red-600 hover:bg-red-700"
              >
                Delete schedule
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
