# Workflow test guide — inputs → expected outcomes

Test data provisioned for the execution-models-v2 fork/approval work (team **platform**, all
**durable**, visible to the interactive `platform-admin`). Run each from Studio → **Workflows** →
open the workflow → **Run Workflow** → type the input → **Start Run**, then watch the run panel.

## The member agents

| Agent | Role | Tool | Triggers HITL? |
|---|---|---|---|
| **wf-router** | Classifier — reads the request and emits exactly one word: `refund` / `fraud` / `info` | none | no |
| **wf-payout** | Processes a refund/payout | `refund_action` (**high**) | **yes — parks at `awaiting_approval`** |
| **wf-confirm** | Sends a confirmation / info reply | none | no |
| **wf-supervisor** | Coordinator (only used as a coordinator in supervisor mode) | none | no |
| **wf-triage** | Summarizes/triages a request | none | no |

> Only **wf-payout** calls a high-risk tool, so **the refund branch is the one that pauses for
> approval** (inline in the run panel — Approve/Deny there). Every other branch completes straight
> through. Routing is done by an LLM classifier, so the keyword branches fire reliably but aren't
> 100% deterministic — phrasing the input clearly (below) makes the branch obvious.

---

## 1. `flow-conditional` — 2-way fork
Graph: **wf-router** →`[refund]` **wf-payout**, →`[default]` **wf-confirm**

| # | Input | Router says | Routes to | Expected outcome |
|---|---|---|---|---|
| 1a | `I want a refund of $50 for order #123` | `refund` | wf-payout | wf-payout calls `refund_action` → run **parks at `awaiting_approval`** → inline **ApprovalCard** appears under the step → **Approve** → run advances/**completes**; **Deny** → run ends denied |
| 1b | `What are your customer service hours?` | `info` | wf-confirm (default) | wf-confirm replies → **completes**, no approval |
| 1c | `Someone charged my card without permission` | `fraud` | wf-confirm (default) | 2-way has no fraud branch → falls to the default → wf-confirm → completes (use the 3-way for a distinct fraud path) |

## 2. `flow-conditional-3way` — 3-way fork
Graph: **wf-router** →`[refund]` **wf-payout**, →`[fraud]` **wf-supervisor**, →`[default]` **wf-confirm**

| # | Input | Router says | Routes to | Expected outcome |
|---|---|---|---|---|
| 2a | `Please refund my last payment` | `refund` | wf-payout | **parks for approval** → Approve → advances/completes |
| 2b | `My card was stolen — this looks like fraud` | `fraud` | wf-supervisor | wf-supervisor handles it → **completes**, no approval |
| 2c | `Can you tell me my account balance?` | `info` | wf-confirm (default) | wf-confirm replies → **completes**, no approval |

## 3. `flow-supervisor` — dynamic routing (no edges, by design)
Coordinator **wf-supervisor** + workers **wf-payout**, **wf-confirm**, **wf-triage**. No edges — the
supervisor picks a worker each turn until it decides it's done (or hits max iterations).

| # | Input | Expected outcome |
|---|---|---|
| 3a | `Refund order #900 and then confirm it to the customer` | Supervisor routes to workers turn by turn — e.g. → wf-payout (**parks for approval** → Approve) → wf-confirm → **DONE**. The exact worker order is the supervisor's call each turn (dynamic), not a fixed graph. |
| 3b | `Just tell me the status of my order` | Supervisor routes to a worker (e.g. wf-triage/wf-confirm) → **DONE**, likely no approval |

## 4. `flow-handoff` — sequential handoff (unchanged)
Graph: **wf-triage** → **wf-payout** → **wf-confirm** (single edges; each agent hands off to the next).

| # | Input | Expected outcome |
|---|---|---|
| 4a | `Refund $20 to my account` | wf-triage summarizes → hands to wf-payout (**parks for approval** → Approve) → hands to wf-confirm → **completes** |

---

## What "parks for approval" looks like
When a run reaches wf-payout's `refund_action`, the run status becomes **`awaiting_approval`** and an
**inline approval card** renders under that step in the run panel (sandbox/playground context —
self-service, no trip to Catalog → Approvals). Approve → the workflow **resumes and advances** to the
next node; Deny → the run ends with the denial.

## Negative / validation checks
- **Multiple start nodes (unsupported).** In a `conditional`/`handoff` workflow, add a second member
  with **no incoming edge** and Save → a warning toast fires: *"Multiple start nodes are not supported…
  only '<first>' will run; <others> would be unreachable."* (Sequential and supervisor modes don't use
  a start node, so they're exempt.)
- **Reactive + high-risk member.** Set a workflow with a wf-payout member to shape=Ephemeral (reactive)
  and Save → warning: a reactive workflow can't park, so an approval gate would FAIL the run.
