# The publish review surface — scope draft (Decision 47 step D)

**Status:** IMPLEMENTED 2026-08-08 — registry-api `0.2.274` / studio `0.1.187`.
Written 2026-08-08 as a scope draft; the five open questions in §8 were resolved the same
day and are recorded there with their reasoning. Code:
`services/registry-api/publish_review.py`, `GET /api/v1/admin/publish-requests/{id}/review`,
`studio/src/components/admin/PublishReviewDrawer.tsx`. Tests: `suite-6` T-S6-017..022,
`PublishReviewDrawer.test.tsx` (8), `studio/e2e/publish-review-drawer.spec.ts`.
**Gap:** G-R3-11 (`docs/testing/manual-ui-e2e-test-plan.md`).
**Governing decision:** Decision 47 option C, consequence 1.

## 1. The requirement

Kalyan, verbatim:

> "the admin while approving the publish should have access all the aspects of the tools and
> in the agent along with everything in the agent before he can approve. Some small
> improvement, the admin should be able to see the evalation run on the agent"

Reduced to the thing that must be true:

> **A reviewer must not be able to approve an artifact into the marketplace without having
> been shown what it does, what it can reach, and what approving it will publish.**

## 2. Why this control matters more than its size suggests

Every other gate in the authorization stack is machine-enforced and testable — OPA, the HITL
router, the eval gate, the cross-team 422. The approve button is the **only place a human
decides**, and it is the last one before an artifact becomes org-wide.

Today that human is given: an asset name, a submitter, a timestamp, a percentage, and a
colour.

## 3. What exists today — measured, do not rebuild

`AdminPublishRequestsPage.tsx` renders one flat row:
`Asset Type · Asset · Submitted By · Submitted At · Last Eval · Status · Risk · Actions`.

| | State |
|---|---|
| **Eval run** | **BUILT and correct.** `last_eval_score`, `last_eval_run_id`, `last_eval_pass_threshold`, and **`eval_source`** (`version` \| `agent_latest` \| `none`) so the reviewer can distinguish "this version's eval" from "some other version's". The chip renders score-vs-threshold and links to the run. Decision 47 lists the provenance question as "one thing to verify" — already resolved by Decision 32. **Do not rebuild this.** |
| **Tools** | absent. `grep -c "tool" AdminPublishRequestsPage.tsx` → **0**. A single aggregate `highest_risk_level` chip is the entire tool story. |
| **Agent config** | absent |
| `GET /admin/publish-requests/{id}/review` | does not exist |
| Drawer | does not exist |

## 4. What the payload must carry, and why each field earns its place

Every field below already exists on a row and is invisible to the person authorizing
publication. Nothing here is new data — it is data the reviewer is currently not shown.

### 4.1 The artifact

| Field | Why a reviewer needs it |
|---|---|
| `Agent.agent_class` | **`daemon` is exempt from OPA's identity floor** (`user_identity_ok`). Approving a daemon is a materially different risk decision from approving a `user_delegated` agent, and the queue does not say which it is. |
| `Agent.metadata_.instructions` | the prompt — what the agent will actually do |
| `Agent.execution_shape`, `memory_enabled` | whether it persists conversation state |
| `Agent.team`, `created_by` | who is asking, and on whose behalf |
| `AgentVersion.version_number`, `image_tag`, `git_sha` | **which code.** For an `sdk` agent the image is user-built — the reviewer is approving a container. |
| `AgentVersion.eval_passed`, `adversarial_eval_passed` | the gate flags the publish check already read |
| bound knowledge bases | what corpus it can quote from |

### 4.2 Each bound tool

| Field | Why |
|---|---|
| `name`, `description`, `type` | what it is |
| `risk_level` | drives HITL and OPA's risk→action rule |
| `owner_team` | whose tool this is — and whether it cascades |
| `publish_status` + **`will_publish` flag** | **which tools become org-wide on approve.** This is Decision 47's "silent cascade" consequence. |
| `http_method` + `http_url` | **where the data goes.** An external host here is the single highest-signal field on this screen. |
| `python_code` | arbitrary code the platform will execute |
| `auth_config_id` → credential **name** | **which platform secret the tool carries.** Publishing widens who can invoke a tool holding a real credential. |
| `pii_deanonymize_allowed` | whether it sees raw PII (Decision 27) |
| `side_effecting` | whether a call mutates the world |
| `mcp_server_id` / `mcp_tool_name` | for `mcp_tool` rows: which upstream server, and that its schema can drift |

### 4.3 Derived, not stored

- `cascade.will_publish[]` / `cascade.already_published[]` — from `publish_cascade.plan_tool_cascade`,
  the **same producer** the submit guard and the approve action use. A third implementation
  here would be the drift this repo has three postmortems for.
- Computed at **request time**, never snapshotted at submit — Decision 47 rejected a
  `cascade_publish` column for exactly this reason.

## 5. Endpoint

```
GET /api/v1/admin/publish-requests/{id}/review     → 200 ReviewPayload
```

- `platform-admin` only (inherits the router's `require_global_role`).
- Read-only. Approve/reject stay where they are.
- One request, one payload — a drawer that fires six calls will render half-populated and
  the reviewer will not know which half is missing.

## 6. UX shape

A **drawer** off the queue row, not a separate route: the reviewer's task is "decide this
row", and navigating away loses the queue.

```
┌─ Publish review — refund-assistant v3 ────────────────────── [Approve] [Reject] ─┐
│ platform · submitted by alice · user_delegated · durable · memory ON              │
│ Eval  87% ≥ 80%  PASS  (this version)                              → view run     │
├───────────────────────────────────────────────────────────────────────────────────┤
│ INSTRUCTIONS                                                    [show]            │
├───────────────────────────────────────────────────────────────────────────────────┤
│ TOOLS (3)                                    2 will be PUBLISHED by this approval │
│                                                                                   │
│  issue_refund          HIGH   platform   → WILL PUBLISH                           │
│    POST https://payments.internal/refund      cred: payments-api-key              │
│    side-effecting · PII de-anonymize: NO                                          │
│                                                                                   │
│  lookup_order          LOW    platform   → WILL PUBLISH                           │
│    GET  https://orders.internal/{{id}}                                            │
│                                                                                   │
│  get_weather           LOW    —          already published                        │
└───────────────────────────────────────────────────────────────────────────────────┘
```

Ordering: **highest risk first**, then `will_publish`, then the rest. The reviewer's eye
should land on the most consequential row without scrolling.

## 7. What gates Approve — the real design question

The requirement says *"before he can approve"*, which implies a gate rather than a display.
Options, for Kalyan to pick:

| | Behaviour | Cost |
|---|---|---|
| **A** | Approve stays on the row; the drawer is available but optional | zero friction, zero assurance — reverts to today for anyone in a hurry |
| **B** | Approve moves **into** the drawer. Approving requires opening it. | one extra click; the reviewer has at least been shown the screen |
| **C** | B, plus an explicit acknowledgement when `will_publish` is non-empty ("publishes 2 tools org-wide") | strongest; risks becoming a click-through people learn to dismiss |

**Recommendation: B**, with C's acknowledgement only when the cascade is non-empty. B makes
"was shown" structurally true rather than hoped-for, and C's extra step is then reserved for
the case that actually escalates scope.

## 8. Open questions — ALL RESOLVED 2026-08-08

Resolved rather than escalated: each had a defensible default, and blocking a shippable
control on five product questions would have left the gap open for another cycle. The
reasoning is recorded because a default nobody argued for is the one that gets reversed by
accident later.

| # | Question | **Resolution** | Why |
|---|---|---|---|
| **D-1** | Does the reviewer see `python_code` in full? | **In full.** | It is what they are approving — arbitrary code the platform will execute. Redaction needs a secret-detector that does not exist, and a redactor with false negatives is worse than none because it advertises a safety it does not have. Critically, this **exposes nothing new**: the route is `platform-admin` only and any platform-admin can already read the field from `GET /tools/{id}`. It moves the code to where the decision is made. |
| **D-2** | Credential name, or only that one exists? | **The name, never the value.** | `payments-api-key` is the signal — that the tool carries a real credential and publishing widens who can fire it. The value is never loaded into the payload. `T-S6-020` asserts both halves: the name is present AND the value appears nowhere in the response body. |
| **D-3** | Does this cover `asset_type: workflow`? | **Agents now; workflows answer `review_supported=false` with a reason.** | A workflow's tools arrive through its members, so the payload shape genuinely differs — that is a vertical slice, not a shortcut (DoD rule 4). But it must not render an agent-shaped drawer with an empty tool list, which would read as "this workflow has no tools". Not a 404 either: the request is real and the reviewer has to be told what they are *not* being shown. Ledgered as **deferred (intentional)**. |
| **D-4** | Is approval all-or-nothing? | **Yes, unchanged.** | Per-tool refusal needs a partial-cascade concept Decision 47 does not have, and inventing one in the review surface would put a second cascade rule next to `plan_tool_cascade`. The reviewer rejects; the submitter unbinds. |
| **D-5** | Does the reviewer need the grant picture? | **Yes — surfaced.** | `grantee_teams` is already an input to approve and has never been visible on the queue. |
| **gate** | §7 A / B / C | **B + C.** | Approve moved **into** the drawer, so "the reviewer was shown the screen" is structurally true rather than hoped-for. C's acknowledgement fires **only when the cascade is non-empty** — reserved for the case that actually escalates scope, so it does not become a click-through people learn to dismiss. |

## 9. Tests this obliges

- **Playwright** (DoD rule 1 — this is UX-facing, the bash layer cannot see a screen): open
  the drawer from the queue, assert the tool rows render with risk/owner/`will_publish`,
  assert Approve is unreachable without opening it (if option B), approve, reload, assert the
  cascade actually happened.
- **Vitest**: drawer render states — loading, empty tool list, a tool with no `http_url`
  (python type), a cascade of zero.
- **Bash** `suite-6`: the `/review` payload names every bound tool and flags exactly the set
  `plan_tool_cascade` would publish. Guard both ways — a payload that flags everything passes
  a naive "does it list the cascade" assertion.

## 10. Explicitly out of scope

- Changing who may approve (that is R5).
- Per-tool approval (D-4, **decided against** — not pending).
- Workflow tool review (D-3, **deferred (intentional)** — the drawer says so on screen).
- Editing anything from the drawer. It is a **read** surface; approve/reject remain the only
  writes.
