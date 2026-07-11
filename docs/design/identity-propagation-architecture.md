# Identity Propagation & Chain-of-Custody — Architecture

**Status:** Proposed.
**Scope:** registry-api, declarative-runner, SDK, scheduler, event-gateway, eval-runner, Studio, Helm chart, Keycloak realm.
**Related:** [`authorization-model-spec.md`](todo/authorization-model-spec.md) (machine identity + OPA), [`hitl-approval-system.md`](hitl-approval-system.md), [`opa-authorization-contract.md`](opa-authorization-contract.md), [`event-gateway-threat-model.md`](event-gateway-threat-model.md) (T-8 internal-auth). Addresses `spec.md` §"Internal-auth on `/api/v1/internal/*`".

---

## 1. Problem

When an agent acts on a user's behalf — and especially when it hands off to another agent, calls a tool, or runs a whole workflow — the platform must be able to answer, at every hop: *whose authority is this running under, and can that be verified?* Today it cannot.

AgentShield captures the end-user's identity correctly at the HTTP edge (Keycloak JWT `sub` via `auth_middleware.py`) but drops it at nearly every internal hop. Trust between internal services is implicit — based on Kubernetes DNS/network location, not on any verifiable credential. Two of the gaps are outright authentication holes: identity is asserted with an unsigned, self-declared header that any pod on the network can forge.

The consequence is that tool governance — OPA policy + human-in-the-loop (HITL) approval, the platform's core safety story — mostly runs blind. For most execution paths OPA evaluates with `user_id=""`, per-user policy is impossible, and the HITL approval record cannot say *who* the action was taken on behalf of, only who approved it. Multi-hop handoff/supervisor workflows have no way to trace back to the initiating human at all.

## 2. Goals / Non-goals

**Goals**
- One explicit, verifiable identity object minted once at each authenticated edge and *threaded* — never re-derived — through every downstream hop (dispatch, agent-to-agent handoff, tool call, workflow orchestration).
- Identity survives the HITL pause/resume cycle, which can last up to 24 hours and resume in a different pod.
- Correct, first-class handling of **autonomous agents** (long-running, scheduled, event-triggered) that run under a *service* identity with a human authorizer, not a live human driver.
- Every internal service-to-service caller authenticates with an *unforgeable* credential; no self-asserted identity headers.
- The HITL approval record and Studio surface the real requesting/authorizing human.

**Non-goals**
- Full RFC 8693 token exchange / per-tool scoped-down tokens. Deferred; the actor-chain model here is the minimal real chain-of-custody.
- Replacing Keycloak or the OPA/HITL governance model. This threads identity *into* them.
- Reworking the agent runtime or LangGraph checkpointer beyond reading/writing identity.

## 3. Current state — where identity drops today

Verified against source (file:line current as of this writing). Latest Alembic migration is `0050`; latest e2e suite is `suite-44`.

| # | Hop | What happens today | Evidence |
|---|---|---|---|
| 1 | registry-api → declarative-runner durable `/run` | `caller` resolved and stored on `PlaygroundRun.user_id`, but **not passed** to dispatch; `/run` body has no user field; runner never sets the identity ContextVar for durable runs | `playground.py:74,134-136,210-249`; `declarative-runner/main.py:507-599` |
| 2 | SDK production agent pod | `/chat`,`/chat/stream` never read `x-user-sub`; ContextVar defaults to `{}`, so every SDK agent sends `user_id=""` to OPA | `sdk/agentshield_sdk/server.py:164-204`; `graph_builder.py:51-55` |
| 3 | eval-runner → registry-api | Owner check bypassed via **self-asserted, unsigned** `X-User-Sub: eval-runner`; any pod can forge it; `user_id` for eval runs is literally `"eval-runner"` | `playground.py:41-43,78-87`; `eval-runner/main.py` (8 sites) |
| 4 | agent → agent (handoff / supervisor) | Orchestrator dispatch sends `{message, thread_id}` with **no headers at all**; `run_by` persisted for DB audit only, never forwarded; no actor/delegation claim exists anywhere | `workflow_orchestrator.py:69-94,274-287,647-671`; `sdk/agentshield_sdk/handoff.py:34-72` |
| 5 | OPA → HITL record | OPA sometimes gets `user_id` (user_delegated only); HITL POST omits it; `Approval` has no requester column; `opa_decisions` table exists for this but is never written | `opa_client.py:130-140`; `hitl.py:73-89`; `models.py:714-791`; `opa_decisions.py` |
| 6 | HITL pause → resume | Resume is a fresh `POST /resume/{thread_id}` carrying only `{decision, reviewer_id, reason}`; ContextVar not re-set; post-approval OPA re-check sees `user_id=""`. Approvals live 30 min–24 h, so no short-lived in-flight token can bridge this | `approvals.py:434`; `declarative-runner/main.py:166-169,469-500`; `approvals.py:37` |
| 7 | scheduler / event-gateway → registry-api | `POST /internal/runs/start` has **no auth**, takes `run_by` verbatim from the body; both services send a static `serviceaccount:*` string; `AgentTrigger` has no `created_by` to attribute a schedule to a human | `internal.py:194-196`; `scheduler/main.py:113,121`; `event-gateway/main.py:293,395`; `models.py:1558` |

> Drop point 7 is the concrete form of the future improvement already tracked in `spec.md` ("Internal-auth on `/api/v1/internal/*` … NetworkPolicy only … adding a shared internal token / mTLS is a tracked future improvement") and `event-gateway-threat-model.md` T-8. This design resolves it with a verified Keycloak service JWT rather than a shared secret.

## 4. Design

### 4.1 `RunContext` — the one identity object

```
RunContext:
  user_sub: str            # authorizing human's Keycloak sub. "" only for a standing daemon
                           # with no human owner on record.
  user_team: str           # resolved once at the edge (reuses chat.py's _caller_team helper).
  actor_chain: list[str]   # agent names already traversed, root-first. [] at first hop.
                           # Appended (never replaced) at each handoff/orchestrator hop. Cap 20.
  is_service_call: bool    # true when the ACTING principal is a verified service, not a person.
  service_name: str | None # "scheduler" | "event-gateway" | "eval-runner", from a verified
                           # service JWT — never from client input.
  origin: str              # "playground" | "production" | "eval" | "schedule" | "webhook"
```

**Minted once**, at every authenticated edge that originates a run:

| Edge | Location | Auth today | Change |
|---|---|---|---|
| Production chat | `chat.py::start_chat` (334) | `require_user` | mint here |
| Deployment chat | `chat.py::start_deployment_chat` (565) | `require_user` | mint here |
| Playground run | `playground.py::create_playground_run` (74) | optional | mint here |
| Playground test-event | `playground.py::test_event` (1035) | optional | mint here |
| Workflow run | `composite_workflows.py::start_workflow_run` (294) | **none** | **add `require_user`** + mint |
| Scheduler/event convergence | `internal.py::start_internal_run` (194) | **none** | **verify service JWT** + mint service-origin context |

Everywhere else is a reader/forwarder, never a re-deriver.

### 4.2 Two identity models: user-delegated vs autonomous

The platform already splits agents by `agent_class ∈ {user_delegated, daemon}` (`schemas.py:77`), and OPA input already carries three identity layers (`opa_client.py:130-140`): `sa_subject` (the agent pod's own K8s ServiceAccount — *which agent is calling the tool*), `agent_class`, and `user_id`/`user_team` (*which human is driving*). `RunContext` makes the same split first-class.

**User-delegated (Class B) — a human drives every action.** Interactive playground, production chat, deployment chat. `user_sub` = the live human; `is_service_call=False`. Identity propagates so per-user policy and HITL attribute to the person on the other end.

**Autonomous (Class A `daemon`) — no human at request time.** Long-running, scheduled (cron), and external-event/webhook-triggered agents. The **acting principal is a verified service identity**, not a person; there is still an **authorizing human** — whoever created the schedule/trigger/deployment. `RunContext` carries both and never conflates them.

| Origin | Acting principal | Authorizing human (`user_sub`) | `is_service_call` |
|---|---|---|---|
| Interactive chat / playground | live user | live user (JWT `sub`) | false |
| Scheduled (cron) | `scheduler` | schedule creator (`AgentTrigger.created_by`) | true |
| External event / webhook | `event-gateway` | trigger creator (`AgentTrigger.created_by`) | true |
| Batch eval | `eval-runner` | eval launcher (`EvalRun.user_id`) | true |
| Standing long-running daemon | agent's own SA (`sa_subject`) | deployment creator (or "" if none) | true |

Governance rules that follow from this split:
- **OPA must not demand a live `user_id` for daemons** — there is none. They are authorized on `sa_subject` + `agent_class` + granted tool scopes. Enforcement (§4.6) applies only to `user_delegated`.
- **HITL for an autonomous run cannot block on a live human.** A 3am cron run that hits an approval gate routes the approval **async to the authorizing human / on-call** and the run **durably waits** (§4.4 makes this possible). The approval record shows "scheduler, on behalf of *alice*'s report job," not an anonymous string.
- **Three identity layers, not two.** `sa_subject` (which agent) ≠ `service_name` (what originated the run) ≠ `user_sub` (authorizing human). A scheduled run of agent X carries all three; none substitutes for another.

### 4.3 Propagation across process boundaries — one signed token

**One mechanism: an HMAC-signed "Run-Context Token" (RCT), carried as header `X-AgentShield-Run-Context` on every internal (K8s-only) hop.** One `mint`/`verify`/`extend` function reused at every hop — no ad-hoc per-path headers.

Rationale: internal traffic is already its own trust domain (service mesh / network-policy). Verifying a full Keycloak JWT at every hop would need JWKS network calls in declarative-runner and every SDK pod — infra those components don't have (`python-jose` is registry-api-only today). An HMAC token verified in-process against a shared secret (same K8s-Secret pattern as the existing `AGENTSHIELD_ENCRYPTION_KEY`) is zero-network-call.

```python
payload_b64 = base64url(json.dumps(claims, sort_keys=True, separators=(",", ":")))
token = payload_b64 + "." + hmac.new(secret, payload_b64.encode(), sha256).hexdigest()
# verify: split on last ".", recompute HMAC, compare_digest, check exp, cap len(actor_chain) <= 20
# extend: verify, append agent_name to actor_chain, re-mint with fresh exp
```

The raw Keycloak JWT stays authoritative only at the true edges (`auth_middleware.require_user`, unchanged). Verified service identity (§4.5) also uses Keycloak, not the RCT — that is an *authentication* problem at the edge, not internal propagation.

New module `run_context.py` lives in three places (no shared package exists across `services/*`/`sdk/`; each vendors deps), kept byte-identical modulo imports:
- `services/registry-api/run_context.py` — dataclass + `mint`/`verify`/`extend`.
- `services/declarative-runner/run_context.py` — `verify`/`extend` (never mints a root token).
- `sdk/agentshield_sdk/run_context.py` — same, **plus the new home of the `_current_user_context` ContextVar** (moved out of `graph_builder.py` so `hitl.py` can read it without the circular import it avoids today), plus `_current_actor_chain`.

Secret `AGENTSHIELD_INTERNAL_SIGNING_KEY`: generated in `scripts/deploy-cpe2e.sh` alongside the encryption key, mounted via `secretKeyRef` into registry-api and every agent pod (`deploy-controller/manifest_builder.py` env list, where `AGENTSHIELD_SA_TOKEN_PATH` is injected today).

### 4.4 Durable anchor + resume re-hydration

The RCT's short TTL cannot survive a multi-hour HITL pause (Drop point 6). So identity is **durably persisted on the run row**, not carried in-flight across the pause:

- **In-flight token (RCT)** — transport for *synchronous* hops (seconds). Short TTL is correct; never relied on across a pause.
- **Durable anchor** — `PlaygroundRun`/`AgentRun` already store `user_id`/`run_by`/`thread_id`. Add a `run_context JSONB` column holding the full serialized `RunContext`, keyed by `thread_id`.
- **Resume re-hydration** — every `/resume/{thread_id}` path re-loads the `RunContext` from the anchor by `thread_id`, re-sets the ContextVar, and re-mints a fresh RCT **before** LangGraph re-enters the governed tool and re-runs the OPA check. This is why a 10-minute token TTL is fine: at resume the token is minted fresh from the anchor.

### 4.5 Verifiable service identity (closes Drop points 3 & 7)

Three internal callers (eval-runner, scheduler, event-gateway) assert identity unforgeably instead of via self-declared strings:

- **Keycloak service clients**: add confidential clients `eval-runner`, `scheduler`, `event-gateway` (`serviceAccountsEnabled=true`), modeled on the existing `registry-api` client (`realm-init-job.yaml` ~139-159). Secrets via the existing `kubectl create secret` idiom.
- **`auth_middleware.is_trusted_service(claims) -> str | None`** — checks `azp` against `_TRUSTED_SERVICE_CLIENTS = {"eval-runner","scheduler","event-gateway"}`, reusing the same JWKS/RS256 verification as `require_user` (unforgeable without Keycloak's private key), as a *distinct* authorization decision from the user-owner check.
- **Callers** mint a `client_credentials` JWT (cached in-process, mirroring `keycloak_client._admin_token()`) and send `Authorization: Bearer`, replacing the self-asserted header / body `run_by`.
- **Receivers** (`playground.py::create_playground_run`, `internal.py::start_internal_run`) verify the token and derive `service_name` from it — never from the body/header.
- **Human authorizer for autonomous runs**: `AgentTrigger` gains `created_by`; trigger-create endpoints set it from `require_user`; scheduler/event-gateway pass it as `RunContext.user_sub`. Pre-existing triggers get `created_by=NULL` (honest service-only lineage; no false attribution).

### 4.6 OPA identity enforcement (Gate 5)

Add to `opa_policy/agentshield.rego`, reading the **registry-side** `agent.agent_class` (not the self-reported `input.agent_class`, so a compromised pod can't relabel itself):

```rego
user_identity_ok if { agent.agent_class != "user_delegated" }        # daemon/autonomous: no live human required
user_identity_ok if { agent.agent_class == "user_delegated"; input.user_id != "" }
user_identity_ok if { input.playground == true }
user_identity_ok if { input.sandbox == true }
```

`AND`ed into the existing `allow` and `require_approval` rules. Enforcement applies only to `user_delegated`; daemons remain authorized on `sa_subject` + scopes.

### 4.7 HITL / approval identity + wiring `opa_decisions`

- `Approval` gains `requested_by_user_id`, `requested_by_team`, `is_service_triggered`, `triggering_service_name` (distinct from `reviewer_id`, which stays the *approver*).
- `hitl.require_approval` reads the ambient `RunContext` and includes them in the POST; `routers/approvals.py` persists them.
- Wire up the existing-but-dead `opa_decisions`: `opa_client.check_tool` fire-and-forget POSTs the already-built OPA input (with `user_id`/`sa_subject`) to `/api/v1/opa-decisions/`; the returned id flows into `require_approval` so `Approval.opa_decision_id` (FK already exists) is populated.
- Studio `HITLDashboardPage.tsx` renders "Requested by {user}" / "service:{name} on behalf of {user}".

## 5. Data model changes (contiguous migrations)

| Migration | Table | Change |
|---|---|---|
| `0051_run_context_column.py` | `playground_runs`, `agent_runs` | `run_context JSONB` — durable identity anchor |
| `0052_agent_trigger_created_by.py` | `agent_triggers` | `created_by TEXT` — schedule/trigger human owner |
| `0053_approval_requesting_user.py` | `approvals` | `requested_by_user_id`, `requested_by_team`, `is_service_triggered BOOL`, `triggering_service_name` + index |

All idempotent (`IF NOT EXISTS`), data-preserving.

## 6. Implementation plan

Each phase is a real vertical slice with its own bash e2e suite (`suite-45` onward), registered in `run-all.sh`, and bumps the touched image tags in **both** `scripts/deploy-cpe2e.sh` and `charts/agentshield/values.yaml`.

**Phase 0 — Shared token infra.** `run_context.py` ×3; `AGENTSHIELD_INTERNAL_SIGNING_KEY` secret + chart wiring; resolve the Deployment backing the shared `declarative-runner` Service (not found under any current chart template — must be located for the secret mount). *e2e:* `suite-45` mint/verify/expiry/tamper/cap. *Tags:* registry-api, declarative-runner, SDK version.

**Phase 1 — Durable `/run` slice** (highest value, lowest risk; copies the working reactive path). Mint at `create_playground_run`; `_dispatch_durable_run` sends the RCT header; runner verifies and sets the ContextVar before `workflow_executor.run`; migration `0051` + write the anchor at insert. *e2e:* `suite-46` real user → real `user_id` reaches OPA. *Docs:* spec.md Identity Propagation subsection.

**Phase 1.5 — Resume re-hydration** (mandatory; without it every post-approval OPA re-check sees `user_id=""`). Resume paths load `RunContext` from the anchor by `thread_id`, re-set the ContextVar, re-mint the RCT; `ResumeRequest` gains an optional `run_context`. *e2e:* `suite-46b` approve after the token would have expired, assert identity still present.

**Phase 2 — SDK pod runtime + Rego Gate 5.** `server.py` reads/verifies the RCT header; `start_chat` mints on the production path; transition window accepts legacy `x-user-sub` (RCT wins). Add Gate 5 **only after** the suite shows user_delegated agents reliably carry `user_id`. *e2e:* `suite-47` user_delegated denied without identity; **daemons explicitly asserted unaffected**.

**Phase 3 — Verifiable service identity** (eval-runner + scheduler + event-gateway). Keycloak service clients; `is_trusted_service`; callers switch to Bearer; `create_playground_run` and `internal.py::start_internal_run` verify and stop trusting body/header; `0052` + wire schedule owner as `user_sub`. *e2e:* `suite-48` positive (run `user_id` = human) **+ non-negotiable negative**: forged `X-User-Sub: eval-runner` and forged body `run_by` both now 403.

**Phase 4 — Handoff / supervisor lineage.** `_dispatch`/`dispatch_to_orchestrator_pod`/`_run_step`/`orchestrate_*` gain `rct` and send the header (extended per hop); `internal.py` dispatch too; SDK `handoff.py` sends the RCT + docstring fix; close the unauthenticated `composite_workflows` edge. *e2e:* `suite-49` 3-hop A→B→C, assert C carries the original human + `actor_chain==["A","B"]`.

**Phase 5 — HITL/Approval identity + `opa_decisions` + Studio.** `0053`; writer + reader wiring; Studio surfacing. *UX-facing:* Playwright spec driving an approval → dashboard shows "Requested by" → survives reload; Vitest for render states. *e2e:* `suite-50` approval requester + non-null `opa_decision_id`.

**Phase 6 — Cleanup.** Remove the legacy header shim; fix `HITLDashboardPage.tsx:48` hardcoded `reviewer_id:"studio-user"` (a separate approver-identity bug); revisit packaging the three `run_context.py` copies only if a 4th consumer appears.

## 7. Security considerations

- **Two forgeable identities are the priority fixes** (Drop points 3, 7); their negative-path 403 tests in `suite-48` are non-negotiable regression guards.
- **RCT is HMAC, internal-only.** It is never accepted at a public ingress; the raw Keycloak JWT remains the only edge credential. Compromise of the signing key is equivalent to intra-cluster compromise, which the mesh trust model already assumes; the key rotates via the existing secret mechanism.
- **Anti-relabel:** Gate 5 reads registry-side `agent_class`, not the pod's self-report.
- **`actor_chain` cap (20)** is a second circuit-breaker beside the orchestrator's `_MAX_STEPS=50` against a runaway handoff loop growing an unbounded token.

## 8. Verification

Definition-of-Done per phase: (a) real journey proven — bash suite for backend phases, Playwright for Phase 5; (b) Phase 5's HITL write is a save→reload→assert; (c) grep each new symbol (`RunContext`, `mint`/`verify`/`extend`, `is_trusted_service`, `run_context` column, new `Approval` columns) for a live caller/reader before calling a phase done; (d) gap ledger current. Two security assertions must never be skipped: forged-service-identity 403s (`suite-48`) and resume-after-TTL identity (`suite-46b`).

## 9. Gap ledger (carry into `docs/testing/manual-ui-e2e-test-plan.md`)

- **deferred (intentional):** `HitlPanel.tsx` requester display — playground is always self-triggered.
- **deferred (intentional):** legacy `x-user-sub` header shim kept through Phase 5, removed in Phase 6.
- **deferred (intentional):** pre-existing `agent_triggers` get `created_by=NULL`; no backfill.
- **not-yet-wired (debt):** `HITLDashboardPage.tsx:48` hardcoded `reviewer_id:"studio-user"` — separate approver-identity bug, fixed in Phase 6.
- **infra unknown (resolve before Phase 1):** the Deployment backing the shared `declarative-runner` Service — needed for the secret mount.

## 10. Open questions

- Should scheduled/event runs whose trigger `created_by` is NULL be *denied* HITL-gated tools outright (no one can approve), or allowed to autonomously proceed on `sa_subject` scopes? Current design: allow on scopes; revisit if audit requires a named human for every high-risk action.
- Long-term: is per-tool scoped-down delegation (RFC 8693 token exchange) worth it over the `actor_chain` model? Out of scope now; the anchor + actor_chain give full traceability without it.
