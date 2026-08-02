# `POST /api/v1/internal/runs/start` requires no authentication

**Found** 2026-08-02 (scoping a "Run now" control for the Schedules page) · **Status: OPEN — reported, not fixed**

## What

The endpoint that starts a triggered run declares no auth dependency:

```python
@router.post("/runs/start", ...)
async def start_internal_run(
    body: InternalRunStartRequest,
    db: AsyncSession = Depends(_get_db),
)
```

No `require_user`, no `get_optional_user`, no service-identity check — and registry-api
installs no global auth middleware. Its docstring says "cluster-internal only", but it is
mounted under the same `/api/v1` prefix as everything else and served by the same
listener.

Probed from outside the cluster, with a deliberately invalid body so nothing could
actually start:

```
POST /api/v1/internal/runs/start   {}          → 422  (reached the handler; only the body shape was rejected)
GET  /api/v1/schedules             (no token)  → 401  (correctly refused)
```

A 422 means the request passed routing and authentication and failed only on validation.
The read endpoint beside it returns 401 — so the inconsistency is inside one service, not
a property of the deployment.

## Scope — accurately

The gateway NLB carries `service.beta.kubernetes.io/aws-load-balancer-scheme = internal`.
It is **VPC-internal, not internet-facing**. The probe above succeeded because the prober
was already on that network.

So: *any workload or user with VPC network reach can start production agent runs without
credentials.* That is not the same as public exposure, and this doc should not be read as
claiming it is. It is the absence of a control, not an open door.

## Why it matters more than an unauthenticated read

`run_by` is **caller-supplied** and is the field that decides whose authority the run
carries — the daemon-identity trail that WS-2 built (`service:X on behalf of {armed_by}`).
An unauthenticated caller chooses it. So the endpoint permits both starting production
work and labelling who asked for it.

It also sidesteps R7 entirely. The schedules *read* was deliberately built deny-by-default
and team-scoped, so one team cannot see another's schedules. A caller hitting this door
directly can *fire* another team's schedule, having never been asked who they are.

## What this blocked

A **"Run now"** control was planned for the Schedules page (matching the ▶ in the
reference design). It is NOT being built against this endpoint. A browser button here
would mean the product's only manual-fire path performs no authorization check at all,
and would normalise calling an unauthenticated internal door from the front end. The
control is worth having — it is how an operator verifies a fix without waiting for the
next tick — but it needs an authenticated route first.

## Suggested fix

Not applied here: the scheduler and the event-gateway both call this endpoint
service-to-service, so adding `require_user` breaks them, and ~15 suites POST to it
without a token. That is a change with its own blast radius and its own regression pass,
not a rider on a UI feature.

Two directions, probably both:

1. **Authenticate the caller as a service.** The platform already mints service identities
   (`resolve_principal`, the `serviceaccount:scheduler` convention this endpoint's own
   `run_by` values use). Require one, and stop trusting `run_by` to be self-declared.
2. **Stop routing `/api/v1/internal/*` from the gateway listener.** Defence in depth: the
   path is only meant to be reachable in-cluster, and nothing outside needs it. Cheap, and
   independent of (1).

A user-facing manual fire should be a *separate, authenticated* endpoint that checks team
scope and then calls this one internally — not the same door with a different caller.

## Related

- `docs/bugs/` — this is the third auth gap found in this workstream. The others:
  `routers/triggers.py::list_triggers` has no auth (read), and Decision 33's original
  finding (eval runs / datasets filtered inside `if caller:` with no `else`).
  `routers/schedules.py` was written deny-by-default specifically to avoid being another.
- Gap ledger: `docs/testing/manual-ui-e2e-test-plan.md`.

## Lessons

1. **"Cluster-internal only" in a docstring is a comment, not a control.** If the path is
   mounted on the same router prefix and the same listener, it is as reachable as
   everything else.
2. **Check the door before building a button for it.** The reason this was found at all is
   that scoping the UI control meant reading the endpoint's dependencies.
