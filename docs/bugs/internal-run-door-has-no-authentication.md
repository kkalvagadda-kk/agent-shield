# `POST /api/v1/internal/runs/start` requires no authentication

**Found** 2026-08-02 (scoping a "Run now" control for the Schedules page) · **Status: OPEN — already designed, not yet built**

> **This was a rediscovery, not a discovery.** `docs/design/identity-propagation-architecture.md`
> has documented this since it was written: **Drop point 7** names it exactly ("`POST
> /internal/runs/start` has **no auth**, takes `run_by` verbatim from the body"), §4.1 lists the
> edge as needing a verified service JWT, §4.5 specifies the mechanism, and **Phase 3** schedules
> the work with a non-negotiable negative test for a forged `run_by`. I wrote this up as a new
> finding without checking the design docs first.
>
> What this doc adds that the design did not have: **measured evidence** (below) rather than
> source reading, and the **manual-fire use case** — a human-initiated "Run now" is a third
> caller shape that §4.5's service-identity model does not cover. Both are now folded back into
> the identity doc (Drop point 7a, §4.2's manual-fire row, Phase 3a). **That doc owns the fix;
> this one is the evidence and the reason the button was not built.**

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

## Fix — owned by identity-propagation Phase 3 / 3a

Not applied here: the scheduler and the event-gateway both call this endpoint
service-to-service, so adding `require_user` breaks them, and ~15 suites POST to it
without a token. That is a change with its own blast radius and its own regression pass,
not a rider on a UI feature.

The design already specifies it — **identity-propagation §4.5 / Phase 3**: Keycloak confidential
clients for `scheduler` / `event-gateway` / `eval-runner`, an `is_trusted_service()` check reusing
the same JWKS verification as `require_user`, callers switching to `Authorization: Bearer`, and the
receiver deriving `service_name` from the token instead of the body. Two measured facts to carry
into that phase:

- **Neither caller has any credential today**, and `auth_middleware` has no service-identity
  validator — only Keycloak *user* JWT verification. So this cannot be a one-line
  `Depends(require_user)`: the callers must gain an identity first or every scheduled and webhook
  run stops. Sequence: mint → send → verify → then tighten.
- **~15 e2e suites POST to this endpoint with no token** and must be updated in the same change.

Worth adding as cheap defence in depth, independent of the above: **stop routing
`/api/v1/internal/*` from the gateway listener.** The path is only meant to be reachable
in-cluster and nothing outside needs it.

A user-facing manual fire is **Phase 3a** — a *separate*, authenticated, team-scoped route that
resolves the caller with `require_user`, checks they may act on that trigger (the same predicate
R7's read uses), and calls this door in-process. Not the same endpoint with a different caller.

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
