import { useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, ArrowLeft, CheckCircle2, Clock, Rocket, X } from "lucide-react";
import { toast } from "sonner";
import { cronHint } from "../../lib/cron";

// UX-preview mock of the production moment.
//
// This is the screen that answers Kalyan's question. Schedule *config* does not
// belong here — `agent_triggers` has no deployment_id, no environment and no
// version_id, so a cron field on a deploy form would promise per-deployment
// schedules the schema cannot hold. What belongs here is the ARMING: the one
// moment where the operator has the context to say "yes, start firing".
//
// Two states are worth seeing, and the toggle at the top switches between them:
//   ready    — the deploy targets production, so arming is possible
//   sandbox  — the deploy targets sandbox, where a schedule can never fire, so
//              arming is refused with the dispatch door's own message
//
// Note the real DeployModal.tsx:30 hardcodes `environment: "sandbox"` and has no
// environment selector at all. That is why this mock shows the selector: the arm
// gesture is only coherent once the deploy form can say where it is going.

const SCHEDULES = [
  { id: "s-1", cron: "0 9 * * 1", tz: "America/New_York", payload: "weekly-report" },
  { id: "s-2", cron: "0 2 * * *", tz: "UTC", payload: null },
];

const REFUSAL =
  "Schedule and webhook triggers dispatch to production. A sandbox deployment cannot " +
  "satisfy them — deploy to production (or publish the agent) before arming.";

export default function ArmAtPublishPreview() {
  const [environment, setEnvironment] = useState<"production" | "sandbox">("production");
  const [armIds, setArmIds] = useState<string[]>(SCHEDULES.map((s) => s.id));
  const [done, setDone] = useState(false);

  const canArm = environment === "production";
  const toggle = (id: string) =>
    setArmIds((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]));

  return (
    <div className="max-w-3xl mx-auto px-6 py-8">
      <Link
        to="/preview/schedules"
        className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800 mb-4"
      >
        <ArrowLeft size={14} /> All preview screens
      </Link>

      <h1 className="text-2xl font-bold text-slate-900">The production moment</h1>
      <p className="text-sm text-slate-500 mt-0.5">
        Mock of the deploy dialog. Arming is a confirmation, not a config form.
      </p>

      <div className="card p-6 mt-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-slate-900">Deploy weekly-digest</h2>
          <X size={18} className="text-slate-300" />
        </div>

        <label className="block mb-4">
          <span className="text-xs text-slate-500 uppercase">Environment</span>
          <select
            data-testid="preview-env-select"
            value={environment}
            onChange={(e) => {
              setEnvironment(e.target.value as "production" | "sandbox");
              setDone(false);
            }}
            className="mt-1 w-full text-sm border border-slate-300 rounded px-2 py-1.5"
          >
            <option value="production">production</option>
            <option value="sandbox">sandbox</option>
          </select>
        </label>

        <div className="grid grid-cols-2 gap-3 mb-4">
          <label className="block">
            <span className="text-xs text-slate-500 uppercase">Replicas</span>
            <input
              type="number"
              defaultValue={1}
              className="mt-1 w-full text-sm border border-slate-300 rounded px-2 py-1.5"
            />
          </label>
          <label className="block">
            <span className="text-xs text-slate-500 uppercase">Auto-terminate (hours)</span>
            <input
              placeholder="Never"
              className="mt-1 w-full text-sm border border-slate-300 rounded px-2 py-1.5"
            />
          </label>
        </div>

        {/* ── The new block ── */}
        <div
          className={`rounded-lg border p-4 ${
            canArm ? "border-blue-200 bg-blue-50/60" : "border-amber-200 bg-amber-50"
          }`}
          data-testid="preview-arm-block"
        >
          <div className="flex items-start gap-2">
            {canArm ? (
              <Clock size={16} className="text-blue-600 mt-0.5 shrink-0" />
            ) : (
              <AlertTriangle size={16} className="text-amber-600 mt-0.5 shrink-0" />
            )}
            <div className="flex-1">
              <p
                className={`text-sm font-medium ${canArm ? "text-blue-900" : "text-amber-900"}`}
              >
                {canArm
                  ? "This agent has 2 schedules that are not yet armed."
                  : "This agent has 2 schedules that cannot be armed here."}
              </p>
              <p className={`text-xs mt-1 ${canArm ? "text-blue-700" : "text-amber-800"}`}>
                {canArm
                  ? "Arming starts unattended execution. Nothing is armed unless you tick it — a schedule you leave alone stays saved and inert."
                  : REFUSAL}
              </p>

              <div className="mt-3 space-y-2">
                {SCHEDULES.map((s) => (
                  <label
                    key={s.id}
                    className={`flex items-center gap-2 text-sm ${
                      canArm ? "text-slate-700 cursor-pointer" : "text-slate-400"
                    }`}
                  >
                    <input
                      type="checkbox"
                      data-testid={`preview-arm-${s.id}`}
                      disabled={!canArm}
                      checked={canArm && armIds.includes(s.id)}
                      onChange={() => toggle(s.id)}
                      className="rounded"
                    />
                    <code className="text-xs bg-white border border-slate-200 rounded px-1.5 py-0.5">
                      {s.cron}
                    </code>
                    <span className="text-xs">
                      {[cronHint(s.cron), s.tz, s.payload].filter(Boolean).join(" · ")}
                    </span>
                  </label>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div className="flex items-center justify-between mt-6">
          <p className="text-xs text-slate-400">
            {canArm && armIds.length > 0
              ? `${armIds.length} schedule${armIds.length === 1 ? "" : "s"} will start firing.`
              : "No schedules will start firing."}
          </p>
          <div className="flex gap-2">
            <button className="btn-secondary text-sm">Cancel</button>
            <button
              data-testid="preview-deploy-btn"
              onClick={() => {
                setDone(true);
                toast.success(
                  canArm && armIds.length > 0
                    ? `Deploying to production — ${armIds.length} schedule(s) armed by demo.`
                    : "Deploying — no schedules armed.",
                );
              }}
              className="btn-primary text-sm inline-flex items-center gap-1"
            >
              <Rocket size={14} /> Deploy
            </button>
          </div>
        </div>
      </div>

      {done && (
        <div
          className="mt-4 rounded-lg border border-green-200 bg-green-50 px-4 py-3 flex items-start gap-2"
          data-testid="preview-arm-result"
        >
          <CheckCircle2 size={16} className="text-green-600 mt-0.5 shrink-0" />
          <p className="text-sm text-green-800">
            {canArm && armIds.length > 0 ? (
              <>
                Armed <strong>{armIds.length}</strong> schedule(s), recorded against{" "}
                <code className="text-xs bg-white rounded px-1">demo</code>. The Schedules page now
                shows them as <strong>Armed · by demo</strong>.
              </>
            ) : (
              <>
                Deployed with nothing armed. The schedules stay saved on the agent and show as{" "}
                <strong>Disarmed</strong> — which is the honest state, not a silent failure.
              </>
            )}
          </p>
        </div>
      )}

      <div className="mt-8 card p-5 bg-slate-50">
        <h3 className="font-semibold text-slate-900 text-sm">Why arming, and not the cron itself</h3>
        <p className="text-sm text-slate-600 mt-2">
          A schedule is part of what the agent <em>is</em> — a &ldquo;daily 9am digest&rdquo; agent
          without its cron is a different artifact, and the reviewer approving publish needs to see
          it. So the cron lives with the agent definition.
        </p>
        <p className="text-sm text-slate-600 mt-2">
          What genuinely belongs to the deploy moment is the decision to let it run unattended. That
          is one bit, it needs a human, and this is the only screen where the operator has the
          context to answer it.
        </p>
      </div>
    </div>
  );
}
