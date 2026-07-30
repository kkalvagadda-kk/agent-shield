// UX-preview fixtures for the Schedules operations page (R5) and the arm/disarm
// screens. Demo-mode only — nothing here is imported by a production code path.
//
// The data is NOT invented. It is the evidence recorded in
// docs/design/todo/schedule-lifecycle-and-operations.md, read off the live EKS
// cluster on 2026-07-28: eight archived workflows on a daily cron, one DRAFT
// workflow firing every 15 minutes, and a sandbox-only agent failing hourly with
// a DNS error. That fleet is the whole argument for the page (brief L111 — "nine
// archived workflows armed, next to a column of red"), so a fixture of pretty
// green rows would demo the wrong product.
import type { ScheduleListItem } from "../api/registryApi";

// `will_fire`/`why_not` are DERIVED, never authored — the fixtures carry only the
// facts a row actually stores, and `deriveFireState` computes the verdict. Typing
// the base rows this way makes an authored `will_fire: true` a compile error, which
// is the point: a hand-set verdict is how a mock starts lying about the product.
type ScheduleFacts = Omit<ScheduleListItem, "will_fire" | "why_not">;

// Offsets are relative to the moment the page loads, NOT to a pinned timestamp.
// A fixed "now" looked tidier and rendered "next fire: 16h ago" the first time this
// was opened a day after the constant was written — nonsense on the one column
// whose whole job is to say when something will happen next. Vitest builds its own
// rows, so nothing depends on these being frozen.
function iso(offsetMinutes: number): string {
  return new Date(Date.now() + offsetMinutes * 60_000).toISOString();
}

// ── The zombie fleet ────────────────────────────────────────────────────────
// Eight archived workflows + one draft, all `enabled` and all armed, because no
// lifecycle path in the tree has ever written to agent_triggers. These are the
// rows that should be structurally impossible after Phase B.
const ARCHIVED_WORKFLOWS: Array<[string, string]> = [
  ["s71-sequential-5c6c93", "0 0 * * *"],
  ["s71-conditional-a41e08", "0 0 * * *"],
  ["s71-handoff-7bd214", "0 0 * * *"],
  ["s71-supervisor-3f90aa", "0 0 * * *"],
  ["s71-wf-c82d55", "0 0 * * *"],
  ["s70-wf-716781", "0 0 * * *"],
  ["s70-wf-f56b4c", "0 9 * * 1"],
  ["s34-wf-1783477084", "0 9 * * 1"],
];

const zombieWorkflows: ScheduleFacts[] = ARCHIVED_WORKFLOWS.map(([name, cron], i) => ({
  trigger_id: `zw-${String(i + 1).padStart(2, "0")}`,
  trigger_type: "schedule",
  artifact_kind: "workflow",
  artifact_id: `wf-${String(i + 1).padStart(2, "0")}`,
  artifact_name: name,
  artifact_team: "platform",
  artifact_status: "archived",
  cron_expression: cron,
  timezone: "UTC",
  next_fire_at: cron === "0 9 * * 1" ? iso(60 * 24 * 3) : iso(60 * 14),
  input_payload: null,
  enabled: true,
  armed_at: "2026-07-08T11:04:00Z",
  armed_by: "e2e:suite-runner",
  disarmed_at: null,
  disarm_reason: null,
  last_run_id: `run-zw-${i + 1}`,
  last_run_status: "failed",
  last_run_at: iso(-60 * 9),
  // The brief's Open Question 2: the workflow run path records an EMPTY
  // error_message, so these rows are less debuggable than the agent ones.
  last_run_error: "",
  alert_email: null,
  alert_on_failure: false,
}));

// ── The rest of the fleet ───────────────────────────────────────────────────
const otherRows: ScheduleFacts[] = [
  // A DRAFT workflow — never published — firing every 15 minutes for days.
  {
    trigger_id: "zw-09",
    trigger_type: "schedule",
    artifact_kind: "workflow",
    artifact_id: "wf-trigger-demo-flow",
    artifact_name: "trigger-demo-flow",
    artifact_team: "platform",
    artifact_status: "draft",
    cron_expression: "*/15 * * * *",
    timezone: "UTC",
    next_fire_at: iso(3),
    input_payload: { task: "demo" },
    enabled: true,
    armed_at: "2026-07-21T16:30:00Z",
    armed_by: "kalyan",
    disarmed_at: null,
    disarm_reason: null,
    last_run_id: "run-zw-09",
    last_run_status: "failed",
    last_run_at: iso(-12),
    last_run_error: "",
    alert_email: null,
    alert_on_failure: false,
  },
  // The agent from the screenshot that started the investigation: deployed to
  // SANDBOX only, so the production dispatch address resolves to nothing.
  {
    trigger_id: "ag-01",
    trigger_type: "schedule",
    artifact_kind: "agent",
    artifact_id: "ag-deamon-agent-test",
    artifact_name: "deamon-agent-test",
    artifact_team: "platform",
    artifact_status: "active",
    cron_expression: "0 * * * *",
    timezone: "UTC",
    next_fire_at: iso(48),
    input_payload: { task: "hourly-digest" },
    enabled: true,
    armed_at: "2026-07-26T08:00:00Z",
    armed_by: "kalyan",
    disarmed_at: null,
    disarm_reason: null,
    last_run_id: "run-ag-01",
    last_run_status: "failed",
    last_run_at: iso(-12),
    last_run_error: "dispatch failed: [Errno -2] Name or service not known",
    alert_email: "oncall@acme.com",
    alert_on_failure: true,
  },
  // Three healthy production rows, for contrast — without these the page reads
  // as "everything is broken" rather than "these nine are".
  {
    trigger_id: "ag-02",
    trigger_type: "schedule",
    artifact_kind: "agent",
    artifact_id: "ag-trigger-demo-a",
    artifact_name: "trigger-demo-a",
    artifact_team: "platform",
    artifact_status: "active",
    cron_expression: "0 9 * * 1",
    timezone: "America/New_York",
    next_fire_at: iso(60 * 24 * 3),
    input_payload: { task: "weekly-report", recipients: ["oncall@acme.com"] },
    enabled: true,
    armed_at: "2026-07-14T09:00:00Z",
    armed_by: "kalyan",
    disarmed_at: null,
    disarm_reason: null,
    last_run_id: "run-ag-02",
    last_run_status: "completed",
    last_run_at: iso(-60 * 30),
    last_run_error: null,
    alert_email: "oncall@acme.com",
    alert_on_failure: true,
  },
  {
    trigger_id: "ag-03",
    trigger_type: "schedule",
    artifact_kind: "agent",
    artifact_id: "ag-trigger-demo-b",
    artifact_name: "trigger-demo-b",
    artifact_team: "platform",
    artifact_status: "active",
    cron_expression: "*/30 * * * *",
    timezone: "UTC",
    next_fire_at: iso(18),
    input_payload: null,
    enabled: true,
    armed_at: "2026-07-14T09:02:00Z",
    armed_by: "kalyan",
    disarmed_at: null,
    disarm_reason: null,
    last_run_id: "run-ag-03",
    last_run_status: "completed",
    last_run_at: iso(-12),
    last_run_error: null,
    alert_email: null,
    alert_on_failure: false,
  },
  {
    trigger_id: "ag-04",
    trigger_type: "schedule",
    artifact_kind: "agent",
    artifact_id: "ag-poc-answerer",
    artifact_name: "poc-answerer",
    artifact_team: "research",
    artifact_status: "active",
    cron_expression: "0 6 * * *",
    timezone: "Europe/London",
    next_fire_at: iso(60 * 21),
    input_payload: null,
    enabled: true,
    armed_at: "2026-07-02T06:00:00Z",
    armed_by: "priya",
    disarmed_at: null,
    disarm_reason: null,
    last_run_id: "run-ag-04",
    last_run_status: "completed",
    last_run_at: iso(-60 * 3),
    last_run_error: null,
    alert_email: null,
    alert_on_failure: false,
  },
  // An author-paused schedule: intent is still on the artifact, the operator's
  // arming still stands, but the author flipped `enabled` off. This row exists to
  // show `enabled` and `armed_at` are orthogonal — the whole point of the split.
  {
    trigger_id: "ag-05",
    trigger_type: "schedule",
    artifact_kind: "agent",
    artifact_id: "ag-nightly-reconcile",
    artifact_name: "nightly-reconcile",
    artifact_team: "platform",
    artifact_status: "active",
    cron_expression: "0 2 * * *",
    timezone: "UTC",
    next_fire_at: iso(60 * 17),
    input_payload: null,
    enabled: false,
    armed_at: "2026-07-11T02:00:00Z",
    armed_by: "kalyan",
    disarmed_at: null,
    disarm_reason: null,
    last_run_id: "run-ag-05",
    last_run_status: "completed",
    last_run_at: iso(-60 * 55),
    last_run_error: null,
    alert_email: null,
    alert_on_failure: false,
  },
  // Already reaped by lifecycle: the row that shows what R2 reads like once the
  // archive path disarms. Reactivating the workflow will NOT bring this back.
  {
    trigger_id: "zw-10",
    trigger_type: "schedule",
    artifact_kind: "workflow",
    artifact_id: "wf-legacy-digest",
    artifact_name: "legacy-digest-flow",
    artifact_team: "platform",
    artifact_status: "archived",
    cron_expression: "0 7 * * *",
    timezone: "UTC",
    next_fire_at: null,
    input_payload: null,
    enabled: true,
    armed_at: null,
    armed_by: "kalyan",
    disarmed_at: "2026-07-27T14:22:00Z",
    disarm_reason: "workflow archived",
    last_run_id: "run-zw-10",
    last_run_status: "completed",
    last_run_at: "2026-07-27T07:00:00Z",
    last_run_error: null,
    alert_email: null,
    alert_on_failure: false,
  },
  // Born disarmed from the create-agent form — the state every new schedule will
  // have after Phase B, and the one the create-form banner is describing.
  {
    trigger_id: "ag-06",
    trigger_type: "schedule",
    artifact_kind: "agent",
    artifact_id: "ag-weekly-digest",
    artifact_name: "weekly-digest",
    artifact_team: "platform",
    artifact_status: "active",
    cron_expression: "0 9 * * 1",
    timezone: "UTC",
    next_fire_at: null,
    input_payload: { task: "weekly-report" },
    enabled: true,
    armed_at: null,
    armed_by: null,
    disarmed_at: null,
    disarm_reason: null,
    last_run_id: null,
    last_run_status: null,
    last_run_at: null,
    last_run_error: null,
    alert_email: "oncall@acme.com",
    alert_on_failure: true,
  },
];

/**
 * The server-side `will_fire` / `why_not` ladder, mirrored for the mock.
 *
 * In Phase B this is computed by the backend from `v_live_triggers` — the SAME
 * view the scheduler reads, so the page cannot disagree with what actually runs.
 * Reproducing it here is deliberately a demo-only concern: it lives in `demo/`,
 * never in `lib/`, so nobody mistakes it for the real predicate and ships a
 * second copy of a rule that is supposed to have exactly one owner.
 */
export function deriveFireState(row: ScheduleFacts): ScheduleListItem {
  const artifactLive =
    row.artifact_kind === "agent"
      ? row.artifact_status === "active"
      : row.artifact_status === "published";

  let why_not: string | null = null;
  if (row.disarm_reason) why_not = row.disarm_reason;
  else if (!row.enabled) why_not = "paused (disabled)";
  else if (!row.armed_at) why_not = "never armed — arm it after it reaches production";
  else if (!artifactLive)
    why_not =
      row.artifact_kind === "agent"
        ? `agent is ${row.artifact_status}`
        : "workflow is not published";

  return { ...row, will_fire: why_not === null, why_not };
}

/** A fresh copy of the fleet. Callers own their copy; nothing shares state. */
export function buildScheduleFleet(): ScheduleListItem[] {
  return [...zombieWorkflows, ...otherRows].map(deriveFireState);
}
