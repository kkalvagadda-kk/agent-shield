import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Check, Circle, Loader2 } from "lucide-react";
import { getAgentHealth, getDeployments, listTriggers, listVersions } from "../../api/registryApi";
import type { Agent } from "../../api/registryApi";

// ── Route to production ──────────────────────────────────────────────────────
//
// WHY THIS EXISTS. Getting a SCHEDULED agent to actually run takes six steps
// across five screens:
//
//   create (schedule armed) → deploy to sandbox → pass an eval
//     → Publish (agent page) → approve (Admin ▸ Publish Queue)
//     → Deploy Latest (Marketplace ▸ the artifact)
//
// Steps 4-6 sit in three different nav sections; one is admin-only and one is
// collapsed by default. Until now the product mentioned ONE of them, in four
// separate warnings that each described a single blocker with no sense of where
// it sat in the sequence. The recurring failure was not any single message being
// wrong — it was that an operator who did the named thing, watched it succeed,
// and came back to an unchanged screen had no way to tell whether they were
// finished, half-way, or had misunderstood entirely.
//
// So this replaces "here is your current blocker" with "here is the whole path,
// and here is where you are on it".
//
// THE LAST STEP IS NOT COMPUTED HERE. `dispatch_error` comes from
// `resolve_dispatch_target` — the same call the run door makes. Deriving
// "is it in production?" from, say, a deployments list would be a SECOND
// definition of reachability, and this repo has paid for that twice already
// (the guard/dispatch environment mismatch, and three restatements of artifact
// liveness). If the strip says a schedule will fire, it is because the code that
// fires it agrees.
//
// Rendered only for agents that HAVE a trigger. A reactive chat agent never
// needs production, and showing it an unfinished checklist would be a lie about
// what "done" means for that agent.

type StepState = "done" | "current" | "todo";

interface Step {
  key: string;
  label: string;
  state: StepState;
  /** Where the operator goes to do this. Null when there is nothing to click. */
  href?: string;
  /** One line, only when this is the step they are on. */
  hint?: string;
}

const DOT: Record<StepState, string> = {
  done: "bg-green-100 text-green-700 border-green-200",
  current: "bg-amber-100 text-amber-800 border-amber-300",
  todo: "bg-slate-100 text-slate-400 border-slate-200",
};

export default function RouteToProduction({ agent }: { agent: Agent }) {
  const name = agent.name;

  const { data: triggers = [] } = useQuery({
    queryKey: ["triggers", name],
    queryFn: () => listTriggers(name),
  });
  const { data: deployments = [] } = useQuery({
    queryKey: ["deployments", name],
    queryFn: () => getDeployments(name),
  });
  const { data: versions = [] } = useQuery({
    queryKey: ["versions", name],
    queryFn: () => listVersions(name),
  });
  const { data: health, isLoading: healthLoading } = useQuery({
    queryKey: ["agent-health", name],
    queryFn: () => getAgentHealth(name),
  });

  // Only agents that dispatch to production have a route to production.
  if (triggers.length === 0) return null;

  const sandboxRunning = deployments.some(
    (d) => d.status === "running" || d.status === "deploying",
  );
  const evalPassed = versions.some((v) => v.eval_passed === true);
  const published = agent.publish_status === "published";
  const pendingReview = agent.publish_status === "pending_review";
  // The authoritative one — same resolver the dispatch door uses.
  const inProduction = !!health && !health.dispatch_error;

  const steps: Step[] = [
    {
      key: "sandbox",
      label: "Sandbox",
      state: sandboxRunning ? "done" : "current",
      hint: sandboxRunning ? undefined : "Deploy to sandbox to evaluate it.",
    },
    {
      key: "eval",
      label: "Eval passed",
      state: evalPassed ? "done" : sandboxRunning ? "current" : "todo",
      href: "/playground",
      hint: evalPassed ? undefined : "Run an eval in Eval Runs, then mark the version passed.",
    },
    {
      key: "published",
      label: pendingReview ? "Awaiting review" : "Published",
      state: published ? "done" : pendingReview ? "current" : evalPassed ? "current" : "todo",
      href: pendingReview ? "/admin/publish-requests" : undefined,
      hint: pendingReview
        ? "Submitted — an admin approves it in Admin ▸ Publish Queue."
        : evalPassed && !published
          ? "Publish this agent, then have it approved."
          : undefined,
    },
    {
      key: "production",
      label: "Deployed to production",
      state: inProduction ? "done" : published ? "current" : "todo",
      href: published && !inProduction ? "/catalog" : undefined,
      // The step everyone misses: publishing produces a catalog LISTING, and the
      // running deployment is a separate action on a different page.
      hint:
        published && !inProduction
          ? "Publishing only listed it. Open it in Marketplace and choose Deploy Latest."
          : undefined,
    },
  ];

  const remaining = steps.filter((s) => s.state !== "done").length;

  return (
    <div
      data-testid="route-to-production"
      className="mb-4 rounded-lg border border-slate-200 bg-white px-4 py-3"
    >
      <div className="flex items-center justify-between mb-2.5">
        <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
          Route to production
        </p>
        <p className="text-xs text-slate-400" data-testid="route-to-production-summary">
          {healthLoading
            ? "checking…"
            : remaining === 0
              ? "This agent's triggers can fire."
              : `${remaining} step${remaining === 1 ? "" : "s"} left before its triggers can fire`}
        </p>
      </div>

      <ol className="flex flex-wrap items-center gap-x-1.5 gap-y-2">
        {steps.map((s, i) => (
          <li key={s.key} className="flex items-center gap-1.5">
            <span
              data-testid={`route-step-${s.key}`}
              data-state={s.state}
              className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${DOT[s.state]}`}
            >
              {s.state === "done" ? (
                <Check size={12} />
              ) : s.state === "current" && healthLoading ? (
                <Loader2 size={12} className="animate-spin" />
              ) : (
                <Circle size={12} />
              )}
              {s.href && s.state === "current" ? (
                <Link to={s.href} className="hover:underline">
                  {s.label}
                </Link>
              ) : (
                s.label
              )}
            </span>
            {i < steps.length - 1 && <span className="text-slate-300">→</span>}
          </li>
        ))}
      </ol>

      {/* Exactly one hint: the step they are actually on. Four simultaneous
          warnings is the state this component replaces. */}
      {steps.find((s) => s.state === "current")?.hint && (
        <p className="mt-2 text-xs text-amber-800" data-testid="route-to-production-hint">
          {steps.find((s) => s.state === "current")!.hint}
        </p>
      )}
    </div>
  );
}
