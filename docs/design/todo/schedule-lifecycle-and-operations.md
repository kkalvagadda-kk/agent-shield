# Schedule lifecycle & operations

**Status:** requirements brief — input to `/arch-design`
**Written:** 2026-07-28 (Kalyan + Claude)
**Trigger:** a scheduled run for `deamon-agent-test` failed hourly; the UI showed a red **Failing** badge directly above **"Last Run: No runs yet."**

Investigating that one screenshot surfaced three distinct defects and one missing surface. All evidence below was read off the running EKS cluster (`test-cluster-964-10086`, registry-api `0.2.233`) and the current tree — not from design docs.

---

## Finding 1 — Schedules fire on deleted and unpublished artifacts

No lifecycle path anywhere disarms a trigger.

- `routers/agents.py::delete_agent` (L362-399) is a **soft delete**: sets `status='deprecated'`, flips deployments to `terminating`. Never touches `agent_triggers`.
- `routers/composite_workflows.py::archive_workflow` (L347-351) is three lines: sets `status='archived'`, commits. Never touches `agent_triggers`.
- Undeploy / suspend — same. Grep confirms zero writes to `AgentTrigger` outside the trigger CRUD routers.

And `services/scheduler/main.py::_fetch_schedule_triggers` (L60-78) filters on **`t.enabled` alone** — no `a.status`, no `w.status`, no deployment check.

So artifact liveness has no bearing whatsoever on whether its schedule fires. Armed **right now**:

| workflow | status | cron |
|---|---|---|
| `s71-sequential-5c6c93`, `s71-conditional-…`, `s71-handoff-…`, `s71-supervisor-…`, `s71-wf-…` | archived | `0 0 * * *` |
| `s70-wf-716781`, `s70-wf-f56b4c`, `s34-wf-1783477084` | archived | `0 0 * * *` / `0 9 * * 1` |
| `trigger-demo-flow` | **draft** | `*/15 * * * *` |

A draft workflow — never published — firing every 15 minutes. Eight archived ones firing daily.

**The e2e suites are the zombie factory.** `s71-*`, `s70-*`, `s34-*` are leftovers whose cleanup archives the workflow and soft-deletes the agent, which by design leaves the trigger armed. They have been seeding the scheduler for weeks.

**Design flaw:** trigger arming and artifact lifecycle are fully decoupled in both directions. Note the webhook door has identical exposure (`event-gateway/webhook_auth.py::_TRIGGER_SQL` joins agents/workflows with no status predicate) — a fix that only filters the scheduler query is a bandaid that leaves webhooks armed.

---

## Finding 2 — Admission guard and dispatch target disagree about environment

`routers/internal.py::start_internal_run` admits a run if **any** `deployments` row is `running` (L394-404, no environment filter), then hardcodes `-production` in the URL (L124 durable, L143 reactive):

```python
runner_url = f"http://{agent_name}-production.{ns}.svc.cluster.local:8080"
```

`deamon-agent-test` is deployed to **sandbox only** — never published, no `production_deployments` row, and the cluster has only `deamon-agent-test-sandbox`. The sandbox row passes the guard; the URL resolves to nothing:

```
agent_runs 46333d3c-8a67-4fad-896f-e9c0122c3761
  status         failed
  trigger_type   schedule
  error_message  dispatch failed: [Errno -2] Name or service not known
```

**1,197 failed scheduled runs** in the DB from this class.

**The webhook door shares this code** — `event-gateway/main.py:319` POSTs the same `/internal/runs/start`. It fails identically, but worse: dispatch is fire-and-forget (`asyncio.create_task`, `internal.py:506`), so the gateway returns **202 Accepted to the sender** before the failure happens. Manual webhook testing passed only because it targeted agents that *do* have production deployments (`trigger-demo-a/b`, `poc-answerer/researcher`).

---

## Finding 3 — A schedule's outcome has no home in the UI

Three stacked causes for the blank screen:

1. **Two scopes on one card.** `OverviewScheduled.tsx` reads Last Run via `listDeploymentRuns(deploymentId, {context})` — scoped to the deployment being viewed — while its health badge reads `GET /agents/{name}/health`, which queries `agent_runs` by **agent_name alone** (`agents.py:821-843`, no deployment, no context, no time bound). Hence Failing + No runs yet, on one card.
2. **Trigger-driven runs have no deployment FK.** `internal.py:473-486` sets neither `production_deployment_id` nor `sandbox_deployment_id`. All **1,328** trigger-driven runs have both NULL — including the 118 that succeeded. None has ever appeared on a deployment page.
3. **`error_message` is never rendered.** It is in the payload (`registryApi.ts:1601`) but `OverviewScheduled.tsx` shows only status + timestamp (L164-187, L195-211). `AgentHealthResponse` carries no error field at all, so the badge cannot explain itself at its source.

### Load-bearing constraint discovered

The two deployment FKs point at **different tables**:

- `agent_runs.sandbox_deployment_id` → `deployments` (the table with the `environment` column)
- `agent_runs.production_deployment_id` → `production_deployments` (published artifacts)

`deployments` with `environment='production'` is what produces the k8s `-production` Service — the 4 running rows match the 4 `-production` Services exactly (`manifest_builder.build_service:38`). So **a validated `deployments` row id cannot be stamped into `production_deployment_id`.** "Stamp the FK" is not implementable as stated.

`agent_runs.trigger_id` **is** already populated on every trigger-driven run. A schedule's runs are a property of the schedule, not of whichever deployment happens to be open — keying run history on `trigger_id` resolves the contradiction at its root with no schema change.

---

## Finding 4 — The tests are rigorous and still missed all of this

15 suites cover this area with real pods, real Keycloak, explicit no-fakes discipline. The gap is *what they assert*.

`suite-71-scheduled-e2e.sh` is the WS-3 acceptance gate. Its `T-S71-005` comment (L464-469), verbatim:

> A daemon+durable agent deployed to **SANDBOX ONLY**: it has a service identity + a running sandbox deployment (**so /internal/runs/start passes its running-deployment check** and resolve_principal succeeds), but the durable dispatch targets the **UNDEPLOYED {agent}-production pod** → dispatch_durable_run fails → … REAL failure path, no injected error.

Finding 2 is described in prose and **used as the failure fixture** for the alerting test. The defect is load-bearing on a green test. Any fix turns `T-S71-005` red and it must be rewritten in the same change.

| Suite | Asserts | Why it misses |
|---|---|---|
| `71` T-S71-000 | no `trigger_type == "schedule"` branch in internal/durable_dispatch/identity | proves no *fork*, not that the shared path is correct |
| `71` T-S71-001-004 | real runs, steps, daemon identity, HITL park/resume, 4 orchestration modes | **deploys to production itself** (L227-228) — precondition set by the test, never by a user's path |
| `66` | scheduler + gateway → completed production run | deploys PRODUCTION first, then fires. Happy path only |
| `26` | APScheduler registers/unregisters the job | never asserts a run happens |
| `28` | gateway door: matched/401/filtered/429/rotate/event-log | T-S28-001 asserts a run **row was created**, not that it reached a pod |
| Playwright `scheduled-overview` | cards are visible; alert-email save→reload | own header: *"not a scheduled RUN firing"* |
| Vitest `OverviewScheduled.test.tsx` | 14 cases incl. `No runs yet` and the health badge | both mocked **independently** — nothing asserts they agree |

Every suite tests a *hop*. None tests the journey: create → deploy → schedule → wait → see the outcome. The unowned seam is the handoff between admission guard and dispatch target.

---

## Finding 5 — No Schedules surface exists

No `/schedules` route (`studio/src/App.tsx`), no cross-artifact trigger endpoint. Triggers are reachable only per-artifact: `GET /api/v1/agents/{name}/triggers` (`triggers.py:125`) and `GET /api/v1/workflows/{id}/triggers` (`composite_workflows.py:813`).

Full CRUD already exists for both shapes (`triggers.py` L47/125/139/158/193/240; `composite_workflows.py` L757/813/827/846/886) — what is missing is a **cross-artifact read** and a page.

This page is what would have made findings 1-3 visible on day one: nine archived workflows armed, next to a column of red.

---

## Requirements

**R1 — An armed trigger on a dead artifact must be unrepresentable.** Liveness gates arming at the *write* (delete / archive / quarantine), with a read-side status filter in **both** consumers (scheduler query and gateway `_TRIGGER_SQL`) as defense-in-depth. Existing zombies reaped by migration.

**R2 — Disarm is reversible only by explicit operator action.** *(decided)* Lifecycle disarm sets `enabled=false` and records why. Reactivating the artifact does **not** re-arm.

**R3 — One owner for "is this dispatchable" and "where does it go."** The guard and the URL must be the same call, so they cannot disagree. The environment must come from the validated row, never a `-production` literal. A non-dispatchable fire fails with an operator-readable reason, not a DNS error.

**R4 — A failed run states why, in the UI.** Both the health badge and the run list must be schedule-scoped so they cannot contradict; `error_message` must render.

**R5 — One page to see and manage every schedule** across agents and workflows: artifact + status, cron, next fire, enabled, disarm reason, last run status + reason, run history, create/edit/delete/toggle.

**R6 — A regression test that is red against today's code** for each of R1 and R3, plus a rewritten `T-S71-005` that no longer depends on the bug.

### Sequencing *(decided)*

Lifecycle (R1, R2) → dispatch (R3, R4) → page (R5). Stop the bleeding, make failures legible, then build the surface on correct data.

### Scope boundaries

- **Undeploy / suspend do NOT disarm.** Reversible infra is not artifact death; disarming there would silently lose schedules across a redeploy. R3 covers "armed but not deployed to production" with a legible rejection instead.
- Run history keys on `trigger_id` (works today). Deployment-FK stamping is out of scope — see the two-table constraint above.
- No new trigger write endpoints; the artifact-scoped routers keep owning create/update/delete/rotate.

## Clarifications — resolved 2026-07-28 (`/arch-design` Step 2)

**R7 — The cross-artifact schedules read is deny-by-default and team-scoped.** `require_user`; results filtered to the caller's team via `user_team_assignments`; `platform-admin` sees all. This is the Decision 33 shape applied before the endpoint exists rather than after a leak.

> Discovered while settling this: `routers/triggers.py:125` `list_triggers` has **no auth dependency at all** — not `require_user`, not `get_optional_user` — and registry-api installs no global auth middleware. Per-artifact trigger listing is already fully unauthenticated. Retrofitting that route is **out of scope here** (blast radius: suites 26/28/31/32/34/66/71/76/83) but must be recorded as a known gap, because R7's endpoint would otherwise be the second instance of a class Decision 33 named yesterday.

**R8 — "Live" for a workflow means `published`.** A schedule fires only for a published workflow; `draft` and `archived` are both disarmed. Consistent with Decision 20 (eval gate at publish).

> Tension to resolve in design: `internal.py:194` rejects only `archived`, so **draft workflow runs are permitted today**. R8 changes the trigger filter but not the run door, leaving two places with different definitions of runnable — structurally the same shape as Finding 2. See spec §Key Decisions.

**R9 — Disarm propagation ≤60s is acceptable.** An archived artifact may fire at most once more, bounded by `RELOAD_INTERVAL_SECONDS`. No new transport; the existing `_sync_jobs` stale-removal (`scheduler/main.py:158-165`) is the mechanism. The window is a documented bound, not an unknown.

**Defaults taken (unchallenged):** success criteria are "zero armed triggers on non-live artifacts" and "every failed trigger run carries a non-empty `error_message`"; page scale assumed hundreds of schedules, not tens of thousands.

### Related — `ENFORCE_TRIGGER_MGMT`

`rbac.py:192` sets `ENFORCE_TRIGGER_MGMT = False` with the comment *"flip to True once frontend guards for trigger CRUD land."* The Schedules page (R5) **is** those guards. R5 is therefore the unblocker for that flag — the flip itself stays out of scope but should be recorded as the follow-on it enables.

## Open questions carried into design

1. **Should the gateway tell the sender?** Today it 202s before dispatch is attempted. R4 makes the failure legible in the UI but not to the caller. Worth a 503 when the door reports an already-failed run?
2. **Workflow runs record an empty `error_message`.** `trigger-demo-flow` has failed every 15 minutes for days with no reason text at all (`_start_workflow_run` path) — less debuggable than the agent path. Fold in, or separate?

## Reference — established facts

- Statuses: agents `active|archived|deprecated|quarantined`; workflows `draft|published|archived`.
- Alembic head in-tree is `0075`; live DB stamped `0074` (see the 0075 docstring on the fork/renumber). Next is `0076`.
- `agent_endpoints.team_namespace` is the single namespace helper — `internal.py:66` already imports it, with a comment (L62-66) documenting the drift that a second copy caused.
- Reusable: `alerting.dispatch_failure_alert`; the `PrincipalResolutionError` fail-closed block (`internal.py:439-471`); the `croniter` next-fire call (`agents.py:840`); `describeCron` (`OverviewScheduled.tsx:20`); `_sync_jobs` stale-removal (`scheduler/main.py:158-165`); `TilePickerDrawer`/`PickerTile` in `components/shared/`; `Sidebar.tsx:75-77` Operate group.
- Image bumps needed (both `scripts/deploy-cpe2e.sh` and `charts/agentshield/values.yaml`): registry-api `0.2.234`, scheduler `0.1.1`, event-gateway, studio `0.1.167`.
- Blast radius: suites 21, 26, 28, 32, 34, 66, 68, 70, 71, 76, 83 + `e2e/scheduled-overview.spec.ts`, `e2e/webhook-applications.spec.ts`.
