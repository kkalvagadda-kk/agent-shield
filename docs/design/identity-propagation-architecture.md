# Identity Propagation & Chain-of-Custody — Architecture

**Status:** CONSOLIDATED — the single source of truth for run identity / chain-of-custody.
Design proposed; **implementation 0% (re-verified in code 2026-08-02, see §3.0).**
Absorbs `sdk-agent-gaps.md` Gap 1 and `authorization-model-spec.md` §Phase 3 + §10 (see §11).
**Scope:** registry-api, declarative-runner, SDK, scheduler, event-gateway, eval-runner, Studio, Helm chart, Keycloak realm.

> **§0 — Boundary.** Three authorization layers, independent, failing differently:
> **RBAC** ([`rbac-and-artifact-authorization.md`](rbac-and-artifact-authorization.md)) — may this
> *person* press Deploy? · **This doc** — whose authority does the resulting *run* carry, at every
> hop? · **OPA** ([`opa-authorization-contract.md`](opa-authorization-contract.md)) — may this
> *agent pod* call this *tool*? This doc owns identity **transport and attribution**, never the
> allow/deny decision itself: it delivers `user_id` to OPA and a requester to HITL; those layers
> decide.
**Related:** [`authorization-model-spec.md`](todo/authorization-model-spec.md) (machine identity + OPA), [`hitl-approval-system.md`](hitl-approval-system.md), [`opa-authorization-contract.md`](opa-authorization-contract.md), [`event-gateway-threat-model.md`](event-gateway-threat-model.md) (T-8 internal-auth), [`mcp-tool-source-architecture.md`](mcp-tool-source-architecture.md) §7a (a downstream consumer of `RunContext.user_sub` — MCP's on-behalf-of identity mode for internal MCP servers is blocked on this doc's Phase 0–2), [`sdk-agent-gaps.md`](sdk-agent-gaps.md) Gap 1 (independently confirms Drop point 2 below from the SDK-runtime-parity angle). Addresses `spec.md` §"Internal-auth on `/api/v1/internal/*`".

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
- **RFC 8693 *delegation* (`subject_token`) and per-tool scoped-down tokens, as a general internal model.** Deferred; the actor-chain model here is the minimal real chain-of-custody. Note the precision (§4.8.5): the chosen MCP mechanism **is** RFC 8693 token exchange — what is excluded is the *delegation* flavour that presents the user's live token as `subject_token`, in favour of the *impersonation* flavour that passes `requested_subject=<user_sub>`. **Narrower exception (2026-07-19):** MCP's on-behalf-of mode for internal MCP servers is a concrete consumer of `RunContext.user_sub` that does need an exchange (Decision 29). Delegation is ruled out structurally — this doc never propagates a re-presentable access token internally, by choice, for reasons that survive its own implementation (§4.8.5). This doesn't change `RunContext`'s design; it's an additive downstream use of `user_sub` once it exists.
- Replacing Keycloak or the OPA/HITL governance model. This threads identity *into* them.

**Delegated tool-call credentials — where this doc's authority ends (added 2026-08-02).**
A tool call can carry a *credential the tool itself validates*, which is a different mechanism
from everything above and is **not** owned here. Current platform-wide truth:

| Tool kind | Credential the tool receives | Who validates | Status |
|---|---|---|---|
| External MCP server, `external_auth_mode="oauth"` | the end user's **own upstream OAuth access token**, resolved per-request from `user_sub` | the external server (e.g. GitHub), against that user's scopes | **BUILT** — `mcp_oauth_grants` (`0074`), `routers/internal_mcp.py`, `suite-87`; per-request-user fix in SDK `0.2.7` |
| Internal MCP server, `identity_mode="service_identity"` | ⚠️ **NOT audienced — see the correction below.** A Keycloak client-credentials token asserting *the platform*, but the `audience` parameter is silently ignored, so every server receives the SAME unscoped token | the internal server — which cannot tell WHICH server the token was minted for | **CODE PATH BUILT, PROPERTY NOT DELIVERED** — `mcp-proxy/keycloak_client.py:80` |
| Internal MCP server, `identity_mode="on_behalf_of"` | a token minted **for** `user_sub`, audienced to that server (impersonation exchange) | the internal server — **assumed, not contracted** | **STUB THAT FAILS CLOSED**, blocked on this doc's Phase 0–2 — Decision 29 |
| Internal MCP server, `identity_mode="none"` | static per-server credentials | the server, if it bothers | **BUILT** (Phase 1) |
| HTTP / Python platform tools | none | n/a — governance is OPA-side only | by design |

> ### ⚠️ CORRECTION 2026-08-04 — `service_identity` does NOT produce a scoped token
>
> The row above previously read "**BUILT**". That was **wrong, and it was my error** — asserted
> from reading `mint_service_account_token(audience)` and seeing an `audience` argument, without
> testing what Keycloak does with it.
>
> `mcp-proxy/keycloak_client.py:80` sends `audience` on a **`grant_type=client_credentials`**
> request. `audience` is RFC 8693's parameter; Keycloak honours it on
> `grant_type=urn:ietf:params:oauth:grant-type:token-exchange`, **not** on client-credentials.
> It is silently ignored. Verified against the live realm on `test-cluster-964-10086`:
>
> | request | HTTP | `aud` in the issued token |
> |---|---|---|
> | no `audience` param | 200 | `account` |
> | `audience=totally-nonexistent-tool-xyz` | 200 | `account` |
> | `audience=langfuse` (a **real** client) | 200 | `account` |
>
> Identical every time. **This is not token exchange — it is a plain service-account grant with an
> ignored hint**, and `_token_cache` keyed by audience is caching one token under many keys.
>
> **Consequence if it were used:** every upstream MCP server receives the *same* unscoped platform
> token. A server can tell "this is the platform" but not which server it was minted for, and the
> token replays against any other server. The three-gate model in §4.8.1 assumes a scoped token at
> gate 3; that assumption does not hold.
>
> **Masked today:** all 13 registered `mcp_servers` rows are `identity_mode="none"` /
> `identity_audience=NULL`, and `MCP_PROXY_KEYCLOAK_CLIENT_ID` has no matching client on the realm
> — so a mint would fail before it could mislead anyone. Nothing is exploitable right now; the
> design claim was simply false.
>
> **What it takes:** (1) MCP-server registration must create or verify a Keycloak client for the
> target and an audience-mapped scope on the proxy's client — that step does not exist at all
> (`registry-api/keycloak_client.py` has only `/users` operations, and `identity_audience` is
> unvalidated operator free text, `mcp_secrets.py:118`); (2) switch the grant to
> `urn:ietf:params:oauth:grant-type:token-exchange`; (3) fail closed on an unregistered audience —
> today the code cannot even detect one.
>
> Sharpens **OQ-5**: a server validating this token sees `aud: account` and has no basis to accept
> or reject it. Postmortem: `docs/bugs/mcp-service-identity-token-is-not-audienced.md`.

This doc supplies the `user_sub` those flows consume; it does not mint, scope, or validate
tool-facing credentials. **Full treatment — the implemented matrix, the three-gate model, the
RFC 8693 delegation-vs-impersonation analysis, and the fail-closed invariants — is §4.8.** Two
properties are absent by design and want an explicit decision before anyone assumes otherwise
(§10 **OQ-4**/**OQ-5**): credentials are scoped **per server, not per tool**, and **no contract
obliges an MCP server to validate the token it is handed**.
- Reworking the agent runtime or LangGraph checkpointer beyond reading/writing identity.

## 3. Current state — where identity drops today

Verified against source (file:line current as of this writing).

### 3.0 Re-verification, 2026-08-02 — nothing has been built

Every drop point below was re-checked against `main` @ `b73989f`. **The design is 0% implemented**,
so the table stands unchanged in substance; the deltas are locations, not status:

| Check | Result |
|---|---|
| `run_context.py` in registry-api / declarative-runner / SDK | **absent in all three** |
| `run_context` JSONB column on `playground_runs` / `agent_runs` | **absent** (`models.py`) |
| `AgentTrigger.created_by` | **absent** |
| `internal.py::start_internal_run` auth | still `Depends(_get_db)` only (`internal.py:448-451`) |
| SDK `server.py` identity header read | reads only `x-agentshield-trace-id` (`:203,222,265,304`) — no user identity on any route |
| eval-runner self-asserted identity | still sending `X-User-Sub: eval-runner` (`eval-runner/main.py:129,148,1667`); still accepted at `playground.py:184,261` |
| `opa_decisions` writer | **none** — model (`models.py:711`) + router + mount exist, zero writers. FK `Approval.opa_decision_id` never populated |

**Two corrections to this document's own numbers:** latest Alembic migration is **`0078`** (not
`0050`) and the latest e2e suite is **`suite-96`** (not `suite-44`). §5 and §6 are renumbered
accordingly; the allocation across the three authorization docs is RBAC `0079`/`suite-97-98`,
this doc `0080-0082`/`suite-99+`, OPA none.

**One material plan improvement,** found by reading the running code rather than the original
survey: all three durable dispatch paths — sandbox playground (`playground.py:356`), workflow
member (`workflow_orchestrator.py:224`), and production/internal (`internal.py:198`) — now funnel
through **one shared helper**, `durable_dispatch.dispatch_durable_run` (`durable_dispatch.py:41`),
which did not exist as a single choke point when this design was written. Threading the RCT
through that one keyword-only signature covers every durable path in a single edit, instead of
Phase 1 + Phase 4 patching three call sites separately. That helper is now the designated seam.

| # | Hop | What happens today | Evidence |
|---|---|---|---|
| 1 | registry-api → declarative-runner durable `/run` | `caller` resolved and stored on `PlaygroundRun.user_id`, but **not passed** to dispatch; `/run` body has no user field; runner never sets the identity ContextVar for durable runs | `playground.py:74,134-136,210-249`; `declarative-runner/main.py:507-599` |
| 2 | SDK production agent pod | `/chat`,`/chat/stream` never read `x-user-sub`; ContextVar defaults to `{}`, so every SDK agent sends `user_id=""` to OPA | `sdk/agentshield_sdk/server.py:164-204`; `graph_builder.py:51-55` |
| 3 | eval-runner → registry-api | Owner check bypassed via **self-asserted, unsigned** `X-User-Sub: eval-runner`; any pod can forge it; `user_id` for eval runs is literally `"eval-runner"` | `playground.py:41-43,78-87`; `eval-runner/main.py` (8 sites) |
| 4 | agent → agent (handoff / supervisor) | Orchestrator dispatch sends `{message, thread_id}` with **no headers at all**; `run_by` persisted for DB audit only, never forwarded; no actor/delegation claim exists anywhere | `workflow_orchestrator.py:69-94,274-287,647-671`; `sdk/agentshield_sdk/handoff.py:34-72` |
| 5 | OPA → HITL record | OPA sometimes gets `user_id` (user_delegated only); HITL POST omits it; `Approval` has no requester column; `opa_decisions` table exists for this but is never written | `opa_client.py:130-140`; `hitl.py:73-89`; `models.py:714-791`; `opa_decisions.py` |
| 6 | HITL pause → resume | Resume is a fresh `POST /resume/{thread_id}` carrying only `{decision, reviewer_id, reason}`; ContextVar not re-set; post-approval OPA re-check sees `user_id=""`. Approvals live 30 min–24 h, so no short-lived in-flight token can bridge this | `approvals.py:434`; `declarative-runner/main.py:166-169,469-500`; `approvals.py:37` |
| 7 | scheduler / event-gateway → registry-api | `POST /internal/runs/start` has **no auth**, takes `run_by` verbatim from the body; both services send a static `serviceaccount:*` string; `AgentTrigger` has no `created_by` to attribute a schedule to a human | `internal.py:194-196`; `scheduler/main.py:113,121`; `event-gateway/main.py:293,395`; `models.py:1558` |
| 7a | **(evidence, 2026-08-02)** the same hop, measured rather than read | Probed from OUTSIDE the cluster: unauthenticated `POST /api/v1/internal/runs/start {}` → **422** (reached the handler; only the body shape was rejected), while `GET /api/v1/schedules` with no token → **401**. So the hole is inside one service, not a property of the deployment. Reachability is **VPC-internal only** — the gateway NLB carries `aws-load-balancer-scheme=internal` — which bounds the blast radius but is not a control. | live EKS `test-cluster-964-10086`, registry-api 0.2.256 |

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
| **Manual fire of a schedule** (Studio "Run now") | **the live human** | the same human (JWT `sub`) | **false** |

**The manual-fire row is the one this design did not anticipate,** and it breaks an assumption
the rest of §4.5 rests on. Every other route into `/internal/runs/start` is a *service*, so
"verify a service JWT" is a complete answer there. A Studio **Run now** control is a THIRD
caller shape: an authenticated **human** asking to fire a **daemon** schedule immediately.

That combination is not in the table above and is not covered by §4.5:

- The **acting principal is a person**, so `is_service_call=false` — even though the agent is a
  daemon and every scheduled fire of the same trigger is `is_service_call=true`. The same
  trigger therefore produces runs of two different principal shapes depending on who started
  them, and the audit trail must say which.
- **`origin`** is neither `"schedule"` (no cron tick caused it) nor `"production"`. It needs its
  own value — `"manual"` — or the run history cannot distinguish "the cron fired at 09:00" from
  "someone pressed the button at 09:04 to test the fix".
- **Authorization is a different question from authentication.** `is_trusted_service` answers
  "is this a known service?". For a human it must also answer "may THIS person fire THIS
  schedule?" — R7 already made the schedules *read* deny-by-default and team-scoped
  (`routers/schedules.py`), and a fire that skipped the same check would let one team trigger
  another team's production agent. Nothing in §4.5 covers this because no human-initiated
  caller existed when it was written.

**Consequence for Phase 3:** verifying a service JWT is necessary but NOT sufficient to unblock
a Run-now button. The button needs a separate authenticated, team-scoped route that resolves
the caller with `require_user`, checks team scope on the trigger, and then calls the internal
door in-process. Building it against the raw internal endpoint would mean the product's only
manual-fire path performs no authorization check at all — see
`docs/bugs/internal-run-door-has-no-authentication.md`.

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

### 4.6 OPA identity enforcement (Gate 5) — **ALREADY SHIPPED; this section is now a delta list**

> **Correction (2026-08-02).** This section read as unbuilt work. It is not. WS-2 shipped the
> identity floor and it is live in `services/registry-api/opa_policy/agentshield.rego`:
>
> ```rego
> default user_identity_ok := false                                              # :22
> user_identity_ok if { input.agent_class == "daemon" }                           # :101-103
> user_identity_ok if { input.agent_class == "user_delegated"; input.user_id != "" }  # :105-108
> ```
>
> AND-ed into `allow` at `:116`. **The floor landed before propagation did — which is precisely
> what caused `docs/bugs/opa-user-identity-floor-denies-tools-missing-x-user-sub.md`, a total,
> silent outage of tool use on every `user_delegated` agent.** The work in this document is
> therefore not "adding a new risk"; it is paying off debt Gate 5 already created. Phase 2's
> original sequencing note ("add Gate 5 only after the suite shows identity arrives") is
> retro­actively inverted and cannot be followed.

Three deltas remain between the shipped rule and this design's intent. Each is a real change, and
this doc is the only place they are recorded:

| # | Delta | Why it matters |
|---|---|---|
| D-1 | Shipped reads **`input.agent_class`** (self-reported by the pod); design calls for registry-side **`agent.agent_class`** from `data.agents[input.sa_subject]` | A compromised pod can relabel itself `daemon` and skip the identity floor entirely. This is the anti-relabel property in §7 — currently absent |
| D-2 | No `playground`/`sandbox` exemption | Both inputs are carried (`opa-authorization-contract.md` §3) but unused. Whether they *should* exempt is a live question — the sandbox already auto-approves HITL above OPA (OPA contract §10.4), so an exemption here may be redundant rather than missing. **Decide, don't assume** |
| D-3 | `user_identity_ok` gates **`allow` only**, not `require_approval` (`:120-124`) | Design said both. Harmless today *only because* the SDK returns early on `not decision.allow` (`graph_builder.py:303-308`) before reading `require_approval`. Load-bearing on that ordering — if the SDK ever checks approval first, an unidentified high-risk call would park an approval for a call that is denied anyway |

One place the shipped rule is **stricter** than this design, and should stay that way: the design's
`agent.agent_class != "user_delegated"` passes an agent whose class is missing or unrecognized;
the shipped `input.agent_class == "daemon"` denies it. Keep the shipped fail-closed shape when
implementing D-1.

### 4.7 HITL / approval identity + wiring `opa_decisions`

- `Approval` gains `requested_by_user_id`, `requested_by_team`, `is_service_triggered`, `triggering_service_name` (distinct from `reviewer_id`, which stays the *approver*).
- `hitl.require_approval` reads the ambient `RunContext` and includes them in the POST; `routers/approvals.py` persists them.
- Wire up the existing-but-dead `opa_decisions`: `opa_client.check_tool` fire-and-forget POSTs the already-built OPA input (with `user_id`/`sa_subject`) to `/api/v1/opa-decisions/`; the returned id flows into `require_approval` so `Approval.opa_decision_id` (FK already exists) is populated.
- Studio `HITLDashboardPage.tsx` renders "Requested by {user}" / "service:{name} on behalf of {user}".

### 4.8 Identity for MCP tool calls — internal and external servers

The rest of this document threads identity so *AgentShield's own* governance can see it. An MCP
tool call goes one step further: a credential leaves the platform and is presented to a **server
that does its own authorization**. That is a different mechanism with its own matrix, and it is
already substantially built — more than any design doc previously recorded. `services/mcp-proxy/`
is the implementation of record; this section documents it and marks the one hole.

#### 4.8.1 Three independent authorizations, not one

An `mcp_tool` call passes three gates in order. None substitutes for another:

| # | Gate | Where | Principal it judges | Status |
|---|---|---|---|---|
| 1 | **OPA** — may this agent call this tool at all? | in the agent pod, before dispatch (`graph_builder.py:303` deny, `:369` dispatch) | the agent's K8s SA (`sa_subject`) + `user_id` | live |
| 2 | **Proxy team floor** — may this agent's team reach this server/tool? | `mcp-proxy/authz.py:84-115`; caller authenticated by **K8s TokenReview** (`authn.py`), not a header | the calling pod's SA, own-team fast path + cross-team consult | live |
| 3 | **Upstream server** — may this *end user* do this thing? | the MCP server itself, using the credential §4.8.2–4.8.4 hands it | whatever the credential asserts | varies — see 4.8.7 |

Gate 1 is this platform's governance. Gate 3 is the *other* system's. Gate 2 exists because the
proxy is a shared egress point and must not become a confused deputy.

#### 4.8.2 The implemented credential matrix

`mcp-proxy/identity.py::resolve_headers` is the single seam that decides which credential goes
upstream. Reproduced from the implementation (`identity.py:104-167`):

| Selector | Plane | Credential presented upstream | Status |
|---|---|---|---|
| `external_auth_mode="oauth"`, no `user_sub` | any | **raises `OAuthUserRequired`** — never downgrades | live |
| `external_auth_mode="oauth"`, `user_sub` set | any | **the end user's own upstream OAuth access token** | **live** |
| `identity_mode="none"` | any | static per-server credentials, unchanged (Phase 1) | live |
| `identity_mode="service_identity"` | any | **a Keycloak token minted for the platform, audienced to that server** | **live** |
| `identity_mode="on_behalf_of"` | admin (discover/health) | the platform's service token — *discovery never impersonates a user* | live |
| `identity_mode="on_behalf_of"` | data, no `user_sub` | **raises `OnBehalfOfIdentityRequired`** | live |
| `identity_mode="on_behalf_of"` | data, `user_sub` set | **raises `OnBehalfOfNotAvailable`** — the one stub | **BLOCKED** |
| unknown mode | any | static creds; never mints or leaks a platform identity | live |

The OAuth branch is checked **first** and is orthogonal: external servers are always
`identity_mode="none"`, so without that ordering they would fall through to the static path and
never authenticate.

#### 4.8.3 External servers (OAuth 2.1) — built, and it is per-user

This is the case that already behaves the way "the tool validates the token and applies its own
authorization" implies — GitHub, for instance, enforces the user's own scopes.

```
agent pod                     mcp-proxy                    registry-api                 upstream
─────────                     ─────────                    ────────────                 ────────
governed_tool (OPA ok)
  McpToolExecutor
    x-user-sub: <per-request acting user>   ─────────►
    Authorization: <pod SA token>                TokenReview(authn.py)
                                                 team floor (authz.py)
                                                 resolve_headers → oauth branch
                                                   POST /internal/mcp/oauth/access-token
                                                   {server_id, user_sub}      ─────────►
                                                                        TokenReview + subject-pin
                                                                        the mcp-proxy SA
                                                                        grant FOR UPDATE (row lock)
                                                                        refresh live via provider,
                                                                        re-store rotated RT first
                                                   ◄───────── access token
                                                 cache in memory
                                                 Authorization: Bearer <user's token> ────────►
                                                                                        validates,
                                                                                        applies its
                                                                                        own authz
```

Load-bearing details, each verified:
- **The user is per-request, not per-pod.** `tool_executor.py:409-416` reads the request-scoped
  `_current_user_context` — the same ContextVar `governed_tool` reads for OPA — and falls back to
  the static `config.USER_SUB` only for a daemon. Forwarding the pod's static user instead was a
  real outage (`docs/bugs/mcp-oauth-tool-call-used-static-not-per-request-user.md`, fixed SDK
  `0.2.7`). **This is the one place today where identity propagation already visibly matters at
  the tool boundary** — and it is exactly the ContextVar that `sdk`-type agents never populate
  (Drop point 2), so an `sdk` agent on an OAuth server falls back to the daemon path.
- **registry-api never stores an access token** — only the refresh token, behind a
  `CredentialRef`. Every pull performs a live refresh; the proxy caches the result in memory only.
- **Rotation is serialized** — the grant row is taken `FOR UPDATE` so two concurrent refreshes
  cannot double-rotate and permanently lock the user out (RFC 9700).
- **Failure is fail-closed and legible** — no grant, un-authorized grant, or `invalid_grant` on
  refresh yields `needs_auth`/`error`, never a downgrade to static credentials or a service token.

#### 4.8.4 Internal servers — `service_identity` built, `on_behalf_of` is the hole

**`service_identity` works today.** `keycloak_client.mint_service_account_token(audience)`
performs an OAuth2 **client-credentials** grant with the `audience` form parameter, caches with an
expiry skew, and re-reads the client secret from its file mount on every real mint
(`keycloak_client.py:80-125`). The upstream server receives a genuine, audienced Keycloak JWT it
can validate. What it *cannot* tell from that token is which human is behind the call — the token
asserts "the platform", by design.

**`on_behalf_of` is a deliberate, loud stub.** `identity.mint_on_behalf_of_token` always raises
`OnBehalfOfNotAvailable` (`identity.py:77-93`). Its docstring states the rule this design cares
about most: it must **never** substitute a service token for a user's, because the platform acting
*as* a user without that user's verified identity is a privilege escalation. Same for the
no-`user_sub` case on the data plane. Both fail closed rather than silently degrading — which is
the correct behaviour for an unbuilt feature and the reason this gap has never produced a security
incident, only an error message.

What lands in that function when it is unblocked, per Decision 29 and the stub's own note: the
proxy's confidential Keycloak client performs a token exchange with `requested_subject=user_sub`
and the server's `audience`, minting fresh per call (no caching). The `audience` mechanics are
already proven by `service_identity` on the same code path, so the delta is the grant type and the
subject parameter — not new infrastructure.

**Dependency chain, precisely:** on-behalf-of needs a *verified* `user_sub` to reach
`governed_tool`'s MCP branch. For `declarative` agents that already happens via
`_bind_user_context`; for `sdk` agents it does not (Drop point 2). So FR-MCP-21 is blocked on this
document's **Phase 0–2**, not on MCP work. Phases 3–5 are irrelevant to it.

#### 4.8.5 RFC 8693 — the precise position, since this gets misread

Both candidate mechanisms are **RFC 8693 Token Exchange** (`grant_type=urn:ietf:params:oauth:
grant-type:token-exchange`). The choice is *which parameter carries the subject*, and therefore
what is being proven:

| | **Delegation / "classic"** | **Impersonation** (chosen) |
|---|---|---|
| Subject parameter | `subject_token` — the user's real, still-valid access token | `requested_subject` — the user's subject *string* |
| What is proven | Keycloak validates a live token: cryptographic proof a real session authorized this | The proxy's own impersonation grant. Trust rests on the proxy's credential, not the user's session |
| Blast radius if the proxy is compromised | bounded — can only exchange tokens actually in flight | broader — can mint for any subject inside the grant's scope |
| Expiry | inherits the user's session | independent of it |
| **Viable here?** | **No** | **Yes** |

Classic is ruled out by a property this document chose deliberately and independently: **no
re-presentable access token exists anywhere in the internal chain.** §4.3 propagates a verified
*subject string* in an HMAC-signed RCT, not a JWT, because (a) verifying a Keycloak JWT at every
hop needs JWKS infrastructure that does not exist outside registry-api, and (b) identity must
survive HITL pauses of up to 24 hours, by which point any original access token is long expired.
Both reasons hold *after* this design ships in full — so this is not a limitation of today's
plumbing that implementation will lift.

**What would reopen it:** only a decision to carry a live, re-presentable token internally — which
means JWKS verification in every SDK pod and the declarative-runner, plus an answer for the
24-hour-pause case (a re-authentication prompt, or accepting that resumed runs lose delegation).
That is a strictly larger change than this document, and it is the same decision as OQ-4.

#### 4.8.6 Invariants — never weaken these

These are the properties that make the stub safe. Any change to `identity.py` must preserve them:
1. An OAuth server with no `user_sub` **raises**; it never falls back to static headers or a
   service token.
2. `external_auth_mode="static"` skips the OAuth branch entirely — a pre-WS-2 server behaves
   byte-identically.
3. `identity_mode="none"` returns the static headers **unchanged**.
4. On-behalf-of on the **data** plane never resolves to a service token — not when `user_sub` is
   missing, not when the exchange is unavailable.
5. On-behalf-of on the **admin** plane (discovery, health) deliberately *does* use the service
   token: listing a server's tools is the platform acting as itself, not as any user.
6. An unrecognized `identity_mode` falls back to static credentials — never mints a platform
   identity for a mode it does not understand.

#### 4.8.7 What this does NOT give you

Two properties are absent by design. Both are decisions, not oversights, and both are open:

- **Scope is per *server*, not per *tool*.** The credential is audienced to the MCP server; nothing
  narrows it to the single tool being invoked. A token minted to call `search_repositories` is
  equally usable for `delete_repo` if the upstream authorizes it. Per-tool scope-down is where
  classic delegation earns its complexity — **OQ-4**.
- **No contract obliges the server to validate what it is handed.** For external servers this is
  moot (GitHub validates because it is GitHub). For an **internal** MCP server the platform mints
  an audienced token and trusts the receiver to check audience, expiry, and subject — with no
  documented obligation and no conformance test. An internal server that ignores the token is
  indistinguishable from one that enforces on it — **OQ-5**.

## 5. Data model changes (contiguous migrations)

Renumbered 2026-08-02 — the head on disk is `0078`, and `0079` is reserved for RBAC phase R5
(`rbac-and-artifact-authorization.md` §5).

| Migration | Table | Change |
|---|---|---|
| `0080_run_context_column.py` | `playground_runs`, `agent_runs` | `run_context JSONB` — durable identity anchor |
| `0081_agent_trigger_created_by.py` | `agent_triggers` | `created_by TEXT` — schedule/trigger human owner |
| `0082_approval_requesting_user.py` | `approvals` | `requested_by_user_id`, `requested_by_team`, `is_service_triggered BOOL`, `triggering_service_name` + index |

All idempotent (`IF NOT EXISTS`), data-preserving.

## 6. Implementation plan

Each phase is a real vertical slice with its own bash e2e suite (**`suite-99` onward** — renumbered
2026-08-02; `suite-96` is the head on disk and `suite-97/98` are reserved for RBAC), registered in
**`scripts/test-manifest.txt`** (not `run-all.sh` — that is now a thin wrapper with no registry of
its own; verify with `bash scripts/run-tests.sh --audit`), and bumps the touched image tags in
**both** `scripts/deploy-cpe2e.sh` and `charts/agentshield/values.yaml`.

**Phase 0 — Shared token infra.** `run_context.py` ×3; `AGENTSHIELD_INTERNAL_SIGNING_KEY` secret + chart wiring; resolve the Deployment backing the shared `declarative-runner` Service (not found under any current chart template — must be located for the secret mount). *e2e:* `suite-99` mint/verify/expiry/tamper/cap. *Tags:* registry-api, declarative-runner, SDK version.

**Phase 1 — Durable `/run` slice** (highest value, lowest risk; copies the working reactive path). Mint at `create_playground_run`; **add an `rct` keyword to the shared `durable_dispatch.dispatch_durable_run` (`durable_dispatch.py:41`) and send the header there** — one edit covers all three durable callers (`playground.py:356` sandbox, `workflow_orchestrator.py:224` workflow member, `internal.py:198` production), which is why part of the original Phase 4 collapses into this phase; runner verifies and sets the ContextVar before `workflow_executor.run`; migration `0080` + write the anchor at insert. *e2e:* `suite-100` real user → real `user_id` reaches OPA. *Docs:* spec.md Identity Propagation subsection.

**Phase 1.5 — Resume re-hydration** (mandatory; without it every post-approval OPA re-check sees `user_id=""`). Resume paths load `RunContext` from the anchor by `thread_id`, re-set the ContextVar, re-mint the RCT; `ResumeRequest` gains an optional `run_context`. *e2e:* `suite-101` approve after the token would have expired, assert identity still present.

**Phase 2 — SDK pod runtime + Gate 5 hardening.** `server.py` reads/verifies the RCT header (today it reads only `x-agentshield-trace-id`); `start_chat` mints on the production path; transition window accepts legacy `x-user-sub` (RCT wins). **Gate 5 already exists** — this phase does not add it; it closes the three deltas in §4.6 (D-1 registry-side `agent_class`, D-2 decide the playground/sandbox question, D-3 `require_approval` gating), and D-1 is the only one that changes a security property. *Sequencing note:* the original "add Gate 5 only after identity arrives" is moot — the gate has been live and denying since WS-2, so Phase 1's real acceptance test is that the *existing* floor stops denying legitimate `user_delegated` traffic. *e2e:* `suite-102` user_delegated denied without identity; **daemons explicitly asserted unaffected**; self-reported-`daemon` relabel attempt denied (D-1's regression guard).

**Phase 3 — Verifiable service identity** (eval-runner + scheduler + event-gateway). Keycloak service clients; `is_trusted_service`; callers switch to Bearer; `create_playground_run` and `internal.py::start_internal_run` verify and stop trusting body/header; `0081` + wire schedule owner as `user_sub`. *e2e:* `suite-103` positive (run `user_id` = human) **+ non-negotiable negative**: forged `X-User-Sub: eval-runner` and forged body `run_by` both now 403.

> **Blast radius, measured (2026-08-02):** ~15 e2e suites POST to `/internal/runs/start`
> without any token, and neither `services/scheduler/main.py` nor
> `services/event-gateway/main.py` sends a credential today — `auth_middleware` has no
> service-identity validator at all, only Keycloak *user* JWT verification. So this phase
> cannot be a one-line `Depends(require_user)`: the callers must gain an identity first, or
> every scheduled and webhook run on the platform stops. Sequence: mint → send → verify →
> then tighten, with the suites updated in the same change.
>
> **Phase 3a — user-initiated manual fire (new, from the schedules workstream).** A Studio
> "Run now" control needs an authenticated, **team-scoped** route that resolves the caller
> with `require_user`, checks the caller may act on that trigger (the same predicate R7's
> read uses), stamps `origin="manual"` and `is_service_call=false`, and calls the internal
> door in-process. It is deliberately NOT the same endpoint with a different caller. Blocked
> on Phase 3; tracked in `docs/bugs/internal-run-door-has-no-authentication.md`.

**Phase 4 — Handoff / supervisor lineage.** Reduced by Phase 1: the durable dispatch seam is already threaded, so what remains is the *streaming* and in-pod hops — `_dispatch_stream` (`workflow_orchestrator.py:107`), `dispatch_to_orchestrator_pod`/`_run_step`/`orchestrate_*` gain `rct` and **extend** the chain per hop; SDK `handoff.py` sends the RCT + docstring fix; close the unauthenticated `composite_workflows` edge. *e2e:* `suite-104` 3-hop A→B→C, assert C carries the original human + `actor_chain==["A","B"]`.

**Phase 5 — HITL/Approval identity + `opa_decisions` + Studio.** `0082`; writer + reader wiring — note `opa_decisions` is a fully-built table + router with **zero writers** today, so this phase is the one that makes `Approval.opa_decision_id` non-null for the first time; Studio surfacing. *UX-facing:* Playwright spec driving an approval → dashboard shows "Requested by" → survives reload; Vitest for render states. *e2e:* `suite-105` approval requester + non-null `opa_decision_id`.

**Phase 6 — Cleanup.** Remove the legacy header shim; fix `HITLDashboardPage.tsx:48` hardcoded `reviewer_id:"studio-user"` (a separate approver-identity bug); revisit packaging the three `run_context.py` copies only if a 4th consumer appears.

## 7. Security considerations

- **Two forgeable identities are the priority fixes** (Drop points 3, 7); their negative-path 403 tests in `suite-103` are non-negotiable regression guards.
- **RCT is HMAC, internal-only.** It is never accepted at a public ingress; the raw Keycloak JWT remains the only edge credential. Compromise of the signing key is equivalent to intra-cluster compromise, which the mesh trust model already assumes; the key rotates via the existing secret mechanism.
- **Anti-relabel:** Gate 5 reads registry-side `agent_class`, not the pod's self-report.
- **`actor_chain` cap (20)** is a second circuit-breaker beside the orchestrator's `_MAX_STEPS=50` against a runaway handoff loop growing an unbounded token.

## 8. Verification

Definition-of-Done per phase: (a) real journey proven — bash suite for backend phases, Playwright for Phase 5; (b) Phase 5's HITL write is a save→reload→assert; (c) grep each new symbol (`RunContext`, `mint`/`verify`/`extend`, `is_trusted_service`, `run_context` column, new `Approval` columns) for a live caller/reader before calling a phase done; (d) gap ledger current. Two security assertions must never be skipped: forged-service-identity 403s (`suite-103`) and resume-after-TTL identity (`suite-101`).

## 9. Gap ledger (carry into `docs/testing/manual-ui-e2e-test-plan.md`)

- **deferred (intentional):** `HitlPanel.tsx` requester display — playground is always self-triggered.
- **deferred (intentional):** legacy `x-user-sub` header shim kept through Phase 5, removed in Phase 6.
- **deferred (intentional):** pre-existing `agent_triggers` get `created_by=NULL`; no backfill.
- **not-yet-wired (debt):** `HITLDashboardPage.tsx:48` hardcoded `reviewer_id:"studio-user"` — separate approver-identity bug, fixed in Phase 6.
- **infra unknown (resolve before Phase 1):** the Deployment backing the shared `declarative-runner` Service — needed for the secret mount.
- **not-yet-wired (debt), blocked on Phase 0–2 (§4.8.4):** internal MCP `on_behalf_of` — the
  `mint_on_behalf_of_token` stub (`mcp-proxy/identity.py:77`) raises on every data-plane call.
  Fails closed and loud, so it is an unavailable feature rather than a security hole. Unblocked by
  Phases 0–2 only; Phases 3–5 are irrelevant to it.
- **deferred (intentional), §4.8.7:** credentials are audienced per **server**, never per tool
  (**OQ-4**).
- **not-yet-wired (debt), §4.8.7:** no validation contract or conformance test obliges an internal
  MCP server to check the token it is handed (**OQ-5**).
- **not-yet-wired (debt), §4.8.3:** an `sdk`-type agent bound to an **external OAuth** MCP server
  falls back to the pod's static `config.USER_SUB`, because `_current_user_context` is never
  populated for that runtime (Drop point 2). Today's most concrete user-visible consequence of the
  identity gap: the OAuth lookup either misses or resolves to the wrong user.
- **blocked on Phase 3 (2026-08-02):** the Schedules page's **Run now** control. Built as far
  as the design and stopped: `/internal/runs/start` has no authentication, so a button there
  would give the product a manual-fire path with no authorization check. See §4.2 (manual-fire
  row) and Phase 3a.

## 10. Open questions

- Should scheduled/event runs whose trigger `created_by` is NULL be *denied* HITL-gated tools outright (no one can approve), or allowed to autonomously proceed on `sa_subject` scopes? Current design: allow on scopes; revisit if audit requires a named human for every high-risk action.
- Long-term: is per-tool scoped-down delegation (RFC 8693 token exchange) worth it over the `actor_chain` model as a *general* pattern? Narrower now than when this was written — MCP's on-behalf-of mode (`mcp-tool-source-architecture.md` §7a) already needed a concrete answer and got one (impersonation-based exchange, layered on top of `RunContext.user_sub`, not a change to this doc's model). Remaining question is only whether other future integrations need the same treatment, or whether the anchor + actor_chain model stays sufficient everywhere else.
- **OQ-4 — per-tool scope-down** (detail: §4.8.7). Credentials are audienced to the **server**, not
  the tool. If a call should carry a token narrowed to *that tool*, the §2 non-goal reopens — and
  it re-litigates Decision 29, because per-tool narrowing is where RFC 8693 **delegation** earns
  its complexity, and delegation needs a re-presentable token this design deliberately does not
  carry (§4.8.5). Cost of saying yes: JWKS verification in every SDK pod + the declarative-runner,
  plus an answer for resumed runs after a 24-hour HITL pause. **A design decision, not a doc edit.**
- **OQ-5 — is the receiving server obliged to validate?** (detail: §4.8.7). Nothing requires an
  internal MCP server to check audience, expiry, or subject on the token it is handed. The platform
  mints a credential and trusts the receiver. If "the tool validates and applies its own
  authorization" is a platform **requirement** rather than a hope, it needs to become a contract: a
  documented validation obligation for internal servers, plus a conformance test that registers a
  deliberately non-validating server and asserts the platform's behaviour. External servers are
  unaffected — they validate because they are somebody else's product.
- **D-2 (from §4.6):** should `playground`/`sandbox` exempt a call from the identity floor at all? Both inputs already reach OPA and are ignored. The sandbox auto-approves HITL *above* OPA, so an exemption may be redundant. Decide before Phase 2 rather than implementing the original text by default.

---

## 11. Consolidated sources — what this doc absorbed

Each source keeps a `SUPERSEDED BY` / status banner and stays in place.

| Source | What moved here | What stays there |
|---|---|---|
| `sdk-agent-gaps.md` **Gap 1** (`sdk` agents never bind end-user identity → OPA sees `user_id=""`) | The finding itself — it is **Drop point 2** in §3, and Phase 2 is its fix. Independently confirmed from the SDK-runtime-parity angle, which is why the two agreed | Gaps 2–3 and the full declarative-runner ↔ SDK parity comparison |
| `authorization-model-spec.md` **§Phase 3** "User Identity Threading (Class B)" | Superseded outright — that phase is this document, at implementation grade | — |
| `authorization-model-spec.md` **§10** "Agent-to-Agent Handoff (Scope Attenuation)" | The handoff lineage problem → §4.3 `actor_chain` + Phase 4. **Note the deliberate divergence:** §10 proposed *scope attenuation* (each hop narrows permissions); this design proposes *lineage recording* (each hop appends to an audit chain). Attenuation is not implemented and is not planned here | The attenuation design, if that stronger property is ever wanted |
| `event-gateway-threat-model.md` **T-8** (internal-auth) | Same hole as Drop point 7; the fix (verified Keycloak service JWT, §4.5) is owned here | The whole public-ingress threat model — untouched and still authoritative |
| `docs/bugs/internal-run-door-has-no-authentication.md` | Measured evidence (Drop point 7a) + the manual-fire caller shape (§4.2, Phase 3a) | Remains the authoritative postmortem and the record of why "Run now" was not built |
| `docs/bugs/opa-user-identity-floor-denies-tools-missing-x-user-sub.md` | The Gate-5-before-propagation ordering finding (§4.6 banner) | Remains the authoritative postmortem, incl. the 2026-07-20 deploy-lag recurrence |
