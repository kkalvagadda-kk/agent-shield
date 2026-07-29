# Scheduled/webhook runs dispatched to a `-production` Service that was never deployed

**Found:** 2026-07-27 (reported from the Studio UI — a red "Failing" badge above "Last Run: No runs yet")
**Fixed:** 2026-07-28 — registry-api `0.2.243`, studio `0.1.172`, branch `schedule-lifecycle`

## Symptom

A scheduled run for `deamon-agent-test` failed every hour. The deployment overview showed a red
**Failing** badge directly above a **Last Run** card reading **"No runs yet."** No reason anywhere
on screen.

The reason was in the database the whole time:

```
agent_runs 46333d3c-8a67-4fad-896f-e9c0122c3761
  status         failed
  trigger_type   schedule
  error_message  dispatch failed: [Errno -2] Name or service not known
```

Scale, measured on the EKS cluster before the fix:

| trigger | status | runs | both deployment FKs NULL |
|---|---|---|---|
| schedule | failed | 1,197 | 1,197 |
| schedule | completed | 118 | 118 |
| schedule | awaiting_approval | 5 | 5 |
| webhook | completed | 4 | 4 |
| webhook | failed | 4 | 4 |

1,328 trigger-driven runs, none of them visible on any deployment page — including the 118 that
succeeded.

## Root cause

Three faults, one shape: **two places independently deciding the same thing, and disagreeing.**

### 1. Admission and dispatch disagreed about environment

`routers/internal.py::start_internal_run` admitted a run if **any** `deployments` row was
`running` — no environment filter:

```python
select(Deployment).where(Deployment.agent_id == agent.id, Deployment.status == "running")
```

Then dispatch hardcoded the environment into the URL (`:124` durable, `:143` reactive):

```python
runner_url = f"http://{agent_name}-production.{ns}.svc.cluster.local:8080"
```

`deamon-agent-test` was deployed to sandbox only. The sandbox row satisfied the guard; the URL named
a Service that does not exist; DNS failed 300ms later. Every sandbox-only agent with a trigger
failed this way, on every fire, forever.

**This is a known class in this repo, and this was a surviving instance of it.**
`agent_endpoints.py` exists precisely because "the pod URL was built in EIGHT places, some
environment-aware and some hardcoding `-production`" — its module docstring records a prior live
defect where sandbox approval resumes POSTed to a nonexistent `{agent}-production` Service and 68
sandbox + 133 playground approvals were marked resolved without ever resuming. `internal.py`
imported `team_namespace` from that module and then hand-built the URL anyway.

**The webhook door shares this code.** `event-gateway/main.py:319` POSTs the same
`/internal/runs/start`. It failed identically — but worse: dispatch is fire-and-forget
(`asyncio.create_task`, `internal.py:506`), so the gateway returned **202 Accepted to the sender**
before the failure occurred. Manual webhook testing passed only because it happened to target
agents that *do* have production deployments.

### 2. The failure was refused into a void

The guard raised `409 CONFLICT` back to the scheduler, which logs a warning nobody reads. No run
row, no alert, no UI trace. Silence is indistinguishable from "nothing was scheduled".

### 3. The UI asked two different questions and rendered both answers

```
badge  ← GET /agents/{name}/health       (every run for the agent, no deployment/context filter)
list   ← GET /deployments/{id}/runs      (runs carrying that deployment's FK)
```

`internal.py:473-486` set neither `production_deployment_id` nor `sandbox_deployment_id`, so the
list was permanently empty while the badge went red. And `error_message` — present in the payload
at `registryApi.ts:1601` — was never rendered by `OverviewScheduled.tsx`.

### Why "just stamp the deployment FK" is not the fix

The obvious repair is unavailable. The two FK columns target **different tables**:

- `agent_runs.sandbox_deployment_id` → `deployments` (the table with the `environment` column)
- `agent_runs.production_deployment_id` → `production_deployments` (the published-artifact lifecycle)

`deployments` with `environment='production'` is what the deploy-controller turns into a
`{agent}-production` Service — verified: the 4 running rows match the 4 `-production` Services in
the cluster exactly. So the row a dispatch validates **cannot** be written to
`production_deployment_id`. Two tables, one confusable name.

## Fix

**One resolver owns both questions.** `agent_endpoints.resolve_dispatch_target(db, agent,
environment=...)` validates the deployment *for the requested environment* and returns the address
built from that same row. Two things that must agree can no longer be asked in two places. It lives
beside `agent_pod_base` in the module whose entire purpose is to be the single source of agent
addresses.

**A refusal is evidence, not silence.** `_record_denied_run` writes a failed `AgentRun` carrying an
operator-readable reason and fires the failure alert. It is shared with the pre-existing
`PrincipalResolutionError` path — both are "refused before dispatch, fail closed, leave a row", so
they are one function rather than two that drift. The message names the cause:

> agent 'x' has no running production deployment — it is deployed to sandbox. Schedule and webhook
> triggers dispatch to production; deploy the agent to production (or publish it) before arming a
> trigger.

**Run reads are re-scoped, not patched.** New `GET /api/v1/agents/{name}/triggers/{id}/runs` keys on
`agent_runs.trigger_id`, which was already populated on every trigger-driven run (including refused
ones). A schedule's runs are a property of the schedule, not of whichever deployment the operator
has open — so the badge and the list now answer from the same set by construction.

**The badge explains itself.** `AgentHealthResponse.last_error`, selected in the *same query* as the
status it justifies, so the two can never come from different runs.

**Alert config stops lying.** `alert_on_failure` with no `alert_email` renders "On — but not
delivered" instead of a green "On" — `alerting.py` returns at its `if not trigger.alert_email` guard
and logs at debug, so that configuration notifies nobody.

## Files changed

| File | Change |
|---|---|
| `services/registry-api/agent_endpoints.py` | `DispatchTarget`, `DispatchTargetError`, `resolve_dispatch_target` |
| `services/registry-api/routers/internal.py` | env-blind guard → resolver; `_record_denied_run` shared with the identity path; both dispatch branches use `target.base_url`; dropped the now-unused `team_namespace`/`Deployment` imports |
| `services/registry-api/routers/triggers.py` | `GET /{name}/triggers/{id}/runs` |
| `services/registry-api/routers/agents.py` | `last_error` populated in the `scheduled` health branch, one query |
| `services/registry-api/schemas.py` | `AgentHealthResponse.last_error` |
| `studio/src/api/registryApi.ts` | `listTriggerRuns`; `AgentHealth.last_error` |
| `studio/src/components/agent-detail/OverviewScheduled.tsx` | runs by trigger; render reasons; alert-config warning |

## Tests

Regression-test-first (CLAUDE.md #7) — each was red against the old code:

- **`scripts/e2e/suite-94-trigger-dispatch-environment.sh`** — T-S94-001 asserts the reason names the
  environment and is **not** a DNS error; -002 the refusal is a recorded run carrying `trigger_id`;
  -003 the trigger-scoped read returns it; -004 health exposes `last_error`; -005 **positive
  control** — a production-deployed agent is still admitted (the fix must not "pass" by refusing
  everything).
- **`studio/src/components/agent-detail/OverviewScheduled.test.tsx`** — 20 cases including the named
  regression *"never shows a failing badge next to 'No runs yet' with no reason"*, driven by one
  mock dataset feeding both the badge and the list so they cannot contradict.
- **`studio/e2e/schedule-failure-reason.spec.ts`** — seeds the failure through the **real** door,
  then asserts in a browser that the reason renders and `waitForResponse` sees the trigger-scoped
  endpoint. Vitest mocks `registryApi` wholesale and so cannot catch the component calling the wrong
  endpoint — the exact seam this bug lived in.
- **`docs/testing/claude-in-chrome-schedule-failure-journey.md`** — human-watchable 8-leg run;
  leg 7 proves the message was *actionable* by fixing the cause and watching it clear.

### The suite that stayed green through 1,197 failures

`suite-71-scheduled-e2e.sh` T-S71-005 described this defect in prose and **used it as its failure
fixture** — "it has a running sandbox deployment (so /internal/runs/start passes its
running-deployment check) but the durable dispatch targets the UNDEPLOYED {agent}-production pod".
The bug was load-bearing on a passing test, which is why 15 rigorous suites never caught it.

The fixture is still valid (a sandbox-only agent genuinely cannot serve a production trigger) and
the case still passes — the refusal is now deliberate instead of a DNS accident. Its comment has
been rewritten to say so, with an explicit instruction not to restore the old mechanism.

## Lessons

- **A comment that explains why a bug is expected behavior is a bug report nobody filed.** Grep test
  fixtures for prose that describes a mechanism as broken-but-relied-upon.
- **Guards and the actions they guard must be one call.** A check that asks a different question than
  the operation performs will drift the moment either side changes.
- **Refusing into a 409 on an internal path is silence.** If a machine caller is the only recipient,
  the refusal needs a row a human can find.
- **Two columns whose names differ by a prefix but whose FKs target different tables will be
  confused.** `production_deployment_id` / `sandbox_deployment_id` cost an entire wrong fix
  direction before the constraint surfaced.

### Process gap found while shipping this

Image tags live in **three** files, not two:

| File | Drives |
|---|---|
| `scripts/deploy-cpe2e.sh` | the local/kind build |
| `charts/agentshield/values.yaml` | the helm deploy (both clusters) |
| **`scripts/deploy-eks.sh`** (L67, L71) | the **EKS** build + push |

`CLAUDE.md`'s Image Version Bumps checklist names only the first two. `deploy-eks.sh`'s own
comments say "MUST match values.yaml", so the requirement is known there but not in the checklist
a contributor reads. Bumping only the documented two leaves the EKS build pushing the *old* tag
while the chart pulls the new one — an ImagePullBackOff, which `deploy-eks.sh` warns is "not an
error message". Worth folding into the CLAUDE.md checklist.

## Not fixed here (see the gap ledger)

- The event-gateway still returns **202** to a webhook sender whose run is then refused. The failure
  is now legible in the UI but not to the caller.
- Workflow scheduled runs record an **empty** `error_message` (`_start_workflow_run`) —
  `trigger-demo-flow` has failed every 15 minutes for days with no reason text at all.
- **Zombie schedules:** 8 archived + 1 draft workflow still have armed schedules firing. No lifecycle
  path disarms a trigger. Separate workstream —
  `docs/design/todo/schedule-lifecycle-and-operations.md`, Finding 1.

---

## Follow-up defect in the fix itself: only ONE of two production legs

**Found:** 2026-07-29, by the reporter asking "I see the same issue with deamon-agent-test —
this agent was created before your fix, does that ring a bell?"
**Fixed:** registry-api `0.2.246`

### Symptom

`GET /agents/cic-journey-agent/health` on an agent that IS published and IS running a
production pod:

```
health          failing
dispatch_error  agent 'cic-journey-agent' has no running production deployment
                — it is deployed to sandbox. … Publish the agent …
```

Demonstrably false. The agent's production pod was live in
`production-cic-journey-agent-ab199885`.

### Root cause

An agent reaches production by **two independent routes, into different namespaces**:

| Route | Table | Namespace | Service FQDN |
|---|---|---|---|
| `POST /agents/{name}/deploy {"environment":"production"}` | `deployments` | `agents-{team}` | `{agent}-production.agents-platform` |
| **Publish** (`routers/catalog.py:279`) | `production_deployments` | `production-{artifact}-{id8}` | `{agent}-production.production-{artifact}-{id8}` |

`resolve_dispatch_target` as first shipped (0.2.243) read **only** `deployments` and always
composed `agents-{team}`. So the *only production route Studio actually offers* was invisible
to it.

And the refusal message told the operator to **Publish** — the exact action that lands in the
leg the resolver could not see. **Self-defeating advice.** Arguably worse than the
`[Errno -2]` it replaced: a DNS error is unhelpful, a confident wrong diagnosis sends you to
do the wrong thing and then reports the same failure.

### Why this was avoidable

`docs/design/sandbox-production-parity-architecture.md` §41 states the rule outright — *"any
column or query that assumes 'a deployment id is a `deployments` id' breaks for production"* —
and lists four earlier bugs from exactly this assumption. `bundle_generator` had already
solved it with a `UNION ALL` over both legs.

The original fix's own `DispatchTarget` docstring even documented the two-table split, for the
FK-stamping question. **Half the lesson was absorbed and the other half missed in the same
change**: "don't stamp a `deployments` id into `production_deployment_id`" was understood,
while "don't ask only `deployments` whether production exists" was not.

### Fix

`resolve_dispatch_target` checks both legs and takes the namespace **from the row it
validated** (`Deployment.k8s_namespace` / `ProductionDeployment.namespace`) rather than
recomposing `agents-{team}`. `DispatchTarget` gains `source_table` so a caller can never treat
the id as belonging to the wrong table.

### Test

`T-S94-008` — arms a schedule on a genuinely published agent and asserts `dispatch_error` is
None. Read-only: no fire, so proving the resolver *sees* the leg needs no side effects. It
skips-as-FAIL when no published agent exists, because an unevaluable case must not read green.

Red against 0.2.245:
```
FAIL T-S94-008 … agent=cic-journey-agent ns=production-cic-journey-agent-ab199885
     health=failing dispatch_error="… has no running production deployment …" (want None)
```

### Lesson

**A message that prescribes a remedy must be tested against the remedy actually working.**
Both `suite-94` and the Playwright spec asserted the reason *named the environment* — true of
the broken advice too. Nothing asserted that following it would help. That gap is what the
Claude-in-Chrome journey's leg 7 ("watch it clear") exists to close, and it caught the
UI-reachability half; it did not catch this half because the fixture used the API route.
