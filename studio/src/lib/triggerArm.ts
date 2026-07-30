// Arm-state derivation (Decision 34).
//
// `armed` is NOT a field. It is `armed_at != null`, derived in exactly one place.
// The server deliberately does not send a boolean alongside the timestamp: that
// would be two representations of one fact, and the two can disagree — the drift
// class this whole slice exists to delete.
//
// Why arm state is separate from `enabled` at all: `enabled` is the AUTHOR's pause
// switch ("I want this cron"), `armed_at` is the OPERATOR's production gesture
// ("this cron is live"). Conflating them is what let a trigger created on a draft
// agent fire immediately and fail 1,197 times.

/** The minimum shape both `AgentTrigger` and `ScheduleListItem` satisfy. */
export interface ArmStateFields {
  armed_at?: string | null;
  armed_by?: string | null;
  disarmed_at?: string | null;
  disarm_reason?: string | null;
}

export type ArmTone = "armed" | "disarmed";

export function isArmed(t: ArmStateFields): boolean {
  return t.armed_at != null;
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
 * honest to say — a never-armed trigger with no recorded reason gets no invented
 * story, which is why `disarm_reason` being absent is a distinct case from it
 * being set.
 */
export function armDetail(t: ArmStateFields): string | null {
  if (isArmed(t)) {
    const when = t.armed_at ? new Date(t.armed_at).toLocaleDateString() : null;
    if (t.armed_by && when) return `by ${t.armed_by} on ${when}`;
    if (t.armed_by) return `by ${t.armed_by}`;
    return when;
  }
  if (t.disarm_reason) return t.disarm_reason;
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
