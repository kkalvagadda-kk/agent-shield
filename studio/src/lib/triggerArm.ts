// Arm-state derivation — one place, one field.
//
// ARM STATE IS `enabled`. There is no `armed` column, no `armed_at` column, and no
// `armed` field on the PATCH body (`AgentTriggerUpdate`). A schedule fires iff its
// trigger row is enabled, and `disabled_reason` records who turned it off and why —
// an author's pause and a lifecycle disarm are the same switch, distinguished by the
// reason rather than by a second boolean.
//
// This file previously derived arm state from `armed_at`, on the theory that the
// author's pause switch and the operator's production gesture are independent. They
// are not, in the schema that exists: the server had to synthesise `armed_at` from
// `created_at` to feed it, which made every row — including agents the lifecycle gate
// had just disarmed — render an "Armed" pill next to "this schedule is disabled".
// Two booleans over one observable behaviour is the same defect as one field with two
// meanings, just inverted. If arm and enable ever become genuinely separate, they need
// separate COLUMNS and an arm endpoint that can refuse (409 when nothing is deployed
// to dispatch to) — not a derived timestamp.

/** The minimum shape both `AgentTrigger` and `ScheduleListItem` satisfy. */
export interface ArmStateFields {
  enabled: boolean;
  /** The human whose authority a daemon run carries — stamped at create time. */
  armed_by?: string | null;
  disarmed_at?: string | null;
  disarm_reason?: string | null;
}

export type ArmTone = "armed" | "disarmed";

export function isArmed(t: ArmStateFields): boolean {
  return t.enabled;
}

export function armTone(t: ArmStateFields): ArmTone {
  return isArmed(t) ? "armed" : "disarmed";
}

/** Short pill text. Detail (by whom / why) belongs on a second line, not in here. */
export function armLabel(t: ArmStateFields): string {
  return isArmed(t) ? "Armed" : "Disarmed";
}

/**
 * The one-line explanation under the pill. Returns null when there is nothing
 * honest to say — a trigger with no recorded authorizer and no recorded reason gets
 * no invented story, which is why `disarm_reason` being absent is a distinct case
 * from it being set.
 */
export function armDetail(t: ArmStateFields): string | null {
  if (isArmed(t)) {
    return t.armed_by ? `by ${t.armed_by}` : null;
  }
  if (t.disarm_reason) {
    const when = t.disarmed_at ? new Date(t.disarmed_at).toLocaleDateString() : null;
    return when ? `${t.disarm_reason} · ${when}` : t.disarm_reason;
  }
  return null;
}

export const ARM_PILL_STYLES: Record<ArmTone, string> = {
  armed: "bg-green-100 text-green-700",
  disarmed: "bg-slate-200 text-slate-600",
};

/**
 * A schedule the author still wants (`enabled`) that will nevertheless not run.
 *
 * This is the predicate the Schedules page and the nav badge are both built on —
 * it is what turns silently-dead crons from invisible into a number someone sees.
 * It lives here, next to the arm helpers, so the page and the sidebar count the
 * same thing; two definitions is how a badge starts disagreeing with its own page.
 *
 * The `disarm_reason` clause is load-bearing, not a nicety. Three different states
 * all satisfy "enabled but not firing", and only one of them is a problem:
 *   - armed on a dead artifact, or never armed at all → nobody chose this and
 *     nobody knows. That is the zombie, and it is what this count exists for.
 *   - an operator disarmed it → chosen, and recorded.
 *   - lifecycle disarmed it on archive → the fix working as designed.
 * Counting the last two makes the badge alarm about its own success, and a badge
 * that cries wolf trains people to stop reading it — the same reason the pill is
 * hidden at zero rather than rendering "0".
 */
export function needsAttention(s: {
  enabled: boolean;
  will_fire: boolean;
  disarm_reason?: string | null;
}): boolean {
  return s.enabled && !s.will_fire && s.disarm_reason == null;
}
