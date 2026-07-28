/**
 * THE verdict vocabulary. One definition, imported everywhere.
 *
 * WHY THIS FILE EXISTS: the rule "did this eval pass?" used to live in four places
 * across three services — the publish gate, the eval-runner's per-item verdict, and
 * the Studio's verdict + colour band — each defaulting to 0.7. They agreed, so
 * nothing ever errored. Then per-run thresholds shipped and the copies diverged:
 * `AdminPublishRequestsPage` and `DatasetsPage` kept rendering against a literal
 * while the gate used the run's own threshold, so a 0.85 run on a 0.9-threshold
 * dataset showed GREEN in the UI and was refused by the product it reports on.
 *
 * The server resolves the threshold (`eval_runner.effective_pass_threshold`) and puts
 * it on the wire. This module renders it. Nothing here re-derives a threshold, and
 * nothing here has a default — a default IS the fifth copy.
 *
 * See docs/decisions.md Decision 32, docs/design/eval-state-of-play.md.
 */

/** `near` is "close, but the gate still says no" — an amber band, not a pass. */
export type Verdict = "pass" | "near" | "fail" | "unknown";

/** Where a publish-queue score came from. See Decision 32. */
export type EvalSource = "version" | "agent_latest" | "none";

/** The amber band starts at this fraction of the run's OWN threshold — never a literal. */
const NEAR_BAND = 0.6;

/**
 * The verdict for a score against the threshold THAT RUN used.
 *
 * Returns `"unknown"` — never `"pass"` — when either input is absent. That is
 * deliberate and load-bearing: a publish request may legitimately have no eval, and
 * guessing a threshold here would re-declare the rule the server already owns. Fail
 * closed, render neutral, show no pass affordance.
 */
export function verdictOf(
  score: number | null | undefined,
  threshold: number | null | undefined,
): Verdict {
  if (score == null || threshold == null) return "unknown";
  // Inclusive boundary, matching the server gate's `overall_score >= threshold`.
  // An exclusive one here would render "failed" for a run that actually publishes.
  if (score >= threshold) return "pass";
  if (score >= threshold * NEAR_BAND) return "near";
  return "fail";
}

/** True only for a definite pass. `unknown` is not a pass. */
export function passesGate(
  score: number | null | undefined,
  threshold: number | null | undefined,
): boolean {
  return verdictOf(score, threshold) === "pass";
}

/**
 * Tailwind classes for the verdict band.
 *
 * Moved VERBATIM from EvalResultsPage.tsx, comment included — that comment is the
 * institutional memory of this bug and paraphrasing it would lose the reason:
 *
 *   The colour band is a VERDICT, so it uses the run's OWN threshold — never a
 *   literal. Green = "this would publish", amber = "it would not". Hardcoded, a 0.85
 *   run on a 0.9-threshold dataset rendered GREEN while the gate refused it: the UI
 *   contradicted the product it reports on.
 *
 * An absent threshold yields the NEUTRAL band (empty string): fail-closed, never a
 * confident wrong verdict.
 */
export function scoreColor(
  score: number | null,
  threshold: number | null | undefined,
): string {
  switch (verdictOf(score, threshold)) {
    case "pass":
      return "bg-green-50 text-green-700";
    case "near":
      return "bg-amber-50 text-amber-700";
    case "fail":
      return "bg-red-50 text-red-700";
    case "unknown":
    default:
      return "";
  }
}

/**
 * "0.85 / needs 0.90" — so a human can see WHY a good-looking score did not publish.
 *
 * Without this the queue shows a number and no verdict rule, and the reviewer cannot
 * tell a strict run from a failing agent.
 */
export function thresholdLabel(
  score: number | null | undefined,
  threshold: number | null | undefined,
): string {
  if (score == null) return "—";
  if (threshold == null) return score.toFixed(2);
  return `${score.toFixed(2)} / needs ${threshold.toFixed(2)}`;
}

/** Signed delta between two composites, for run-to-run comparison (Wave 1). */
export function formatDelta(
  current: number | null | undefined,
  baseline: number | null | undefined,
): string {
  if (current == null || baseline == null) return "—";
  const d = current - baseline;
  return `${d >= 0 ? "+" : "-"}${Math.abs(d).toFixed(2)}`;
}
