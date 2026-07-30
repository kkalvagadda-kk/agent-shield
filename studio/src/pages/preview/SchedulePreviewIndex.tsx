import { Link } from "react-router-dom";
import { AlertTriangle, ArrowRight, CalendarClock, Rocket, Settings, Sparkles } from "lucide-react";

// UX-preview index for the schedule-lifecycle design.
//
// Demo-mode only. This is a DESIGN ARTIFACT, not the feature: no backend, no
// migration, no scheduler change. The zombie schedules on the real cluster are
// still firing. It exists so the shape can be judged before it is built.

const SCREENS = [
  {
    to: "/schedules",
    icon: CalendarClock,
    title: "Schedules — the operations page",
    req: "R5",
    real: true,
    blurb:
      "Every schedule across agents and workflows in one table, with the question that had no home: will this actually run? Nine armed schedules on archived and draft workflows, next to a column of red.",
  },
  {
    to: "/preview/schedule-settings",
    icon: Settings,
    title: "Agent Settings — arm state on the trigger row",
    req: "R1 · R2",
    real: false,
    blurb:
      "Where a schedule is authored stays where it is. What changes is that the row now shows whether it is armed, who armed it, and — when it is not — why. Arming an agent that is not in production is refused, with the dispatch door's own words.",
  },
  {
    to: "/preview/schedule-arm-at-publish",
    icon: Rocket,
    title: "The production moment — arm on deploy",
    req: "R1",
    real: false,
    blurb:
      "The answer to \"where does the schedule menu go?\". Not a config form at deploy time — a confirmation that lists what is about to start firing, and requires a human to say yes.",
  },
  {
    to: "/preview/schedule-create-disarmed",
    icon: Sparkles,
    title: "Create agent — the honest banner",
    req: "R1",
    real: false,
    blurb:
      "Cron stays on the create form, because a schedule is part of what the agent is. What changes is that it no longer lies: the trigger is saved disarmed, and the form says so.",
  },
];

export default function SchedulePreviewIndex() {
  return (
    <div className="max-w-4xl mx-auto px-6 py-10">
      <h1 className="text-2xl font-bold text-slate-900">Schedule lifecycle — UX preview</h1>
      <p className="text-sm text-slate-500 mt-1">
        Four screens for the design in{" "}
        <code className="text-xs bg-slate-100 rounded px-1 py-0.5">
          docs/design/todo/schedule-lifecycle-and-operations.md
        </code>
        .
      </p>

      <div className="mt-5 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 flex items-start gap-2">
        <AlertTriangle size={16} className="text-amber-600 mt-0.5 shrink-0" />
        <div className="text-sm text-amber-800 space-y-1">
          <p>
            <strong>This is a mock, not the feature.</strong> No backend, no migration, no scheduler
            change — the zombie schedules on the real cluster keep firing. Data is the evidence from
            the brief, served from memory.
          </p>
          <p>
            Controls work (disarm, pause, delete, filter) but nothing persists —{" "}
            <strong>a reload resets everything</strong>.
          </p>
        </div>
      </div>

      <div className="mt-6 space-y-3">
        {SCREENS.map((s) => (
          <Link
            key={s.to}
            to={s.to}
            className="card block p-5 hover:border-blue-300 hover:shadow-sm transition-all group"
          >
            <div className="flex items-start gap-4">
              <div className="w-9 h-9 rounded-lg bg-blue-50 flex items-center justify-center shrink-0">
                <s.icon size={18} className="text-blue-600" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <h2 className="font-semibold text-slate-900">{s.title}</h2>
                  <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-slate-100 text-slate-600">
                    {s.req}
                  </span>
                  {s.real ? (
                    <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-green-100 text-green-700">
                      real page — ships as-is
                    </span>
                  ) : (
                    <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-slate-100 text-slate-500">
                      mock of an existing screen
                    </span>
                  )}
                </div>
                <p className="text-sm text-slate-600 mt-1.5">{s.blurb}</p>
              </div>
              <ArrowRight
                size={16}
                className="text-slate-300 group-hover:text-blue-500 shrink-0 mt-2"
              />
            </div>
          </Link>
        ))}
      </div>

      <div className="mt-8 card p-5 bg-slate-50">
        <h3 className="font-semibold text-slate-900 text-sm">The one idea underneath all four</h3>
        <p className="text-sm text-slate-600 mt-2">
          Today <code className="text-xs bg-white rounded px-1">agent_triggers.enabled</code> does
          two jobs: &ldquo;the author wants this cron&rdquo; and &ldquo;this cron is live against
          production&rdquo;. That is why a schedule typed into the create form on a draft agent is
          armed the moment you hit save — and then fails every fire.
        </p>
        <p className="text-sm text-slate-600 mt-2">
          Split them and the placement question answers itself:{" "}
          <strong>author on the artifact</strong> (Settings / builder / create form),{" "}
          <strong>arm at the production moment</strong>, <strong>operate on this page</strong>.
        </p>
      </div>
    </div>
  );
}
