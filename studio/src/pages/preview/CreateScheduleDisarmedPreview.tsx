import { useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, Clock, Info, Webhook } from "lucide-react";
import { cronHint } from "../../lib/cron";

// UX-preview mock of the create-agent form's Schedule block.
//
// The change here is one banner and nothing else. Cron, timezone, alert email and
// the JSON job spec all stay exactly where they are (CreateAgentPage.tsx:115-158) —
// creating an agent and its schedule in one gesture is the right flow and this
// pass does not touch it.
//
// What is broken today is not the placement, it is the honesty. `createTrigger`
// fires straight after `createAgent` (CreateAgentPage.tsx:322-331) and
// `agent_triggers.enabled` defaults true, so the schedule is ARMED on save — on a
// draft agent with no production deployment. The scheduler picks it up within 60s,
// dispatches to an address that does not exist, and records a failed run. 1,197 of
// them. The form currently reads as if the user just scheduled something that
// works.

const COMMON_TZ = [
  "UTC",
  "America/New_York",
  "America/Chicago",
  "America/Los_Angeles",
  "Europe/London",
  "Asia/Kolkata",
];

export default function CreateScheduleDisarmedPreview() {
  const [hasSchedule, setHasSchedule] = useState(true);
  const [hasWebhook, setHasWebhook] = useState(false);
  const [cron, setCron] = useState("0 9 * * 1");
  const [tz, setTz] = useState("UTC");
  const [alertEmail, setAlertEmail] = useState("");
  const [payload, setPayload] = useState("");

  return (
    <div className="max-w-3xl mx-auto px-6 py-8">
      <Link
        to="/preview/schedules"
        className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800 mb-4"
      >
        <ArrowLeft size={14} /> All preview screens
      </Link>

      <h1 className="text-2xl font-bold text-slate-900">Create agent</h1>
      <p className="text-sm text-slate-500 mt-0.5">
        Mock of the Triggers block on the real create form.
      </p>

      <div className="card p-6 mt-6 space-y-5">
        <div>
          <span className="text-xs text-slate-500 uppercase">Agent name</span>
          <input
            defaultValue="weekly-digest"
            className="mt-1 w-full text-sm border border-slate-300 rounded px-2 py-1.5"
          />
        </div>

        <div>
          <span className="text-xs text-slate-500 uppercase block mb-2">Triggers</span>
          <div className="space-y-2" role="group" aria-label="Triggers">
            <label className="flex items-center gap-2 text-sm text-slate-700 cursor-pointer">
              <input
                type="checkbox"
                checked={hasSchedule}
                onChange={(e) => setHasSchedule(e.target.checked)}
                className="accent-indigo-600"
              />
              <Clock size={14} className="text-indigo-600" /> Schedule (cron)
            </label>
            <label className="flex items-center gap-2 text-sm text-slate-700 cursor-pointer">
              <input
                type="checkbox"
                checked={hasWebhook}
                onChange={(e) => setHasWebhook(e.target.checked)}
                className="accent-indigo-600"
              />
              <Webhook size={14} className="text-indigo-600" /> Webhook (inbound events)
            </label>
            <p className="text-xs text-slate-400">
              Manual / API invocation is always available. Add one or more automated triggers above.
            </p>
          </div>
        </div>

        {hasSchedule && (
          <div className="rounded-lg border border-slate-200 p-4 space-y-3 bg-slate-50/50">
            {/* ── THE ONLY NEW THING ── */}
            <div
              className="flex items-start gap-2 rounded-md border border-blue-200 bg-blue-50 px-3 py-2"
              data-testid="schedule-disarmed-notice"
            >
              <Info size={14} className="text-blue-600 mt-0.5 shrink-0" />
              <p className="text-xs text-blue-800">
                <strong>Saved with the agent — it will not fire yet.</strong> Schedules only run
                against production. Deploy this agent to production, then arm the schedule from its
                Settings tab or the Schedules page.
              </p>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <span className="text-xs text-slate-500 uppercase">Cron expression</span>
                <input
                  className="mt-1 w-full font-mono text-sm border border-slate-300 rounded px-2 py-1.5"
                  value={cron}
                  onChange={(e) => setCron(e.target.value)}
                  placeholder="0 9 * * 1"
                />
                <span className="text-xs text-slate-400">{cronHint(cron)}</span>
              </label>
              <label className="block">
                <span className="text-xs text-slate-500 uppercase">Timezone</span>
                <select
                  className="mt-1 w-full text-sm border border-slate-300 rounded px-2 py-1.5"
                  value={tz}
                  onChange={(e) => setTz(e.target.value)}
                >
                  {COMMON_TZ.map((z) => (
                    <option key={z} value={z}>
                      {z}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <label className="block">
              <span className="text-xs text-slate-500 uppercase">
                Failure alert email (optional)
              </span>
              <input
                type="email"
                className="mt-1 w-full text-sm border border-slate-300 rounded px-2 py-1.5"
                value={alertEmail}
                onChange={(e) => setAlertEmail(e.target.value)}
                placeholder="oncall@example.com"
              />
            </label>

            <label className="block">
              <span className="text-xs text-slate-500 uppercase">
                Input payload — JSON job spec (optional)
              </span>
              <textarea
                className="mt-1 w-full font-mono text-xs border border-slate-300 rounded px-2 py-1.5 resize-none"
                rows={4}
                value={payload}
                onChange={(e) => setPayload(e.target.value)}
                placeholder={'{\n  "task": "weekly-report"\n}'}
              />
              <span className="text-xs text-slate-400">
                The agent receives this as its input on each fire. One agent can have several
                schedules with different payloads.
              </span>
            </label>
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2 border-t border-slate-100">
          <button className="btn-secondary text-sm">Cancel</button>
          <button className="btn-primary text-sm">Create Agent</button>
        </div>
      </div>

      <div className="mt-8 card p-5 bg-slate-50">
        <h3 className="font-semibold text-slate-900 text-sm">
          Why the cron stays on the create form
        </h3>
        <p className="text-sm text-slate-600 mt-2">
          Moving it to the deploy dialog was the tempting fix, and it is worse.{" "}
          <code className="text-xs bg-white rounded px-1">agent_triggers</code> has no{" "}
          <code className="text-xs bg-white rounded px-1">deployment_id</code>, no{" "}
          <code className="text-xs bg-white rounded px-1">environment</code> and no{" "}
          <code className="text-xs bg-white rounded px-1">version_id</code> — the schedule belongs to
          the agent, not to a deployment. A cron field on the deploy form would imply per-deployment
          schedules that cannot exist, and every redeploy would raise &ldquo;did my schedule follow
          me?&rdquo; with no honest answer.
        </p>
        <p className="text-sm text-slate-600 mt-2">
          The defect was never the placement. It was that saving the form armed it.
        </p>
      </div>
    </div>
  );
}
