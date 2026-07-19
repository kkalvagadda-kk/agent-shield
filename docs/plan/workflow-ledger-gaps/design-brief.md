# Design brief — Workflow ledger gaps (rehydrate chat · Memory tab · per-deployment scope)

**Status:** grounded from the running product (studio 0.1.154 / registry-api 0.2.206). POC-5 shipped
workflow **Conversations** (`memory.list_workflow_conversations`). Three follow-on gaps remain. This
brief scopes them for `/plan`. Reproduce-first + save→reload→assert are mandatory (CLAUDE.md DoD).

---

## G1 — WorkflowChatPage does not rehydrate a selected past session
**Symptom:** Selecting a past workflow session continues the *correct* session (the `?session=<thread_id>`
routing works) but the transcript panel opens on an empty **"Send a message…"** state instead of
**replaying the past turns**. The single-agent chat (`AgentChatPage`) already rehydrates — this is the
reference to mirror.

**Anchors (read the real code, don't assume):**
- `studio/src/pages/WorkflowChatPage.tsx` — session load / initial transcript state (the gap).
- `studio/src/pages/AgentChatPage.tsx` — the WORKING rehydration: how it fetches the prior transcript
  for the selected session on mount and seeds the message list. Mirror the exact fetch + seeding.
- The API that returns a session's transcript (conversation messages / run tree keyed by `thread_id`
  == `session`). Confirm which endpoint AgentChatPage uses and whether a workflow equivalent exists;
  if not, that's part of the work.

**Reproduce-first:** a Vitest (mock the transcript fetch → assert WorkflowChatPage renders the past
turns, not the empty composer) that FAILS today; and a Playwright step that opens a past session and
asserts a prior message bubble is visible.

## G2 — Workflow Memory tab is empty
**Symptom:** The workflow deployment **Memory** tab shows nothing, for the SAME member-name / NULL-user
reason Conversations had (workflow-member `agent_memory` rows carry `agent_name`=member, `user_id`=NULL,
`scope='workflow_run'`). Only **Conversations** was fixed (via `list_workflow_conversations`, which joins
member memory rows → workflow parent runs by `session_id` and derives ownership from the parent run).

**Anchors:**
- `services/registry-api/memory.py` — `list_workflow_conversations` + `_LIST_WORKFLOW_CONVERSATIONS_SQL`.
  Add the analogous `list_workflow_memory` (same DISTINCT semi-join → parent run ownership, but returns
  memory ENTRIES not conversation summaries).
- `services/registry-api/conversation_store.py` (or the memory store Protocol) — add the method to the
  Protocol + Postgres adapter.
- `services/registry-api/routers/composite_workflows.py` — add `GET /workflows/{id}/memory`
  (mirror the `GET /workflows/{id}/conversations` endpoint; `require_user`).
- Studio: the currently-empty workflow **Memory** tab component + `WorkflowConversationsTab.tsx`
  (mirror into the Memory tab wiring; `studio/src/api/registryApi.ts` add `listWorkflowMemory`).

**Reproduce-first:** an e2e/bash suite case that seeds a workflow-member memory row and asserts the new
endpoint returns it (fails before the query exists); a Vitest for the tab; save→reload→assert.

## G3 — Per-deployment scoping (DEFERRED — document only, do NOT implement now)
Playground/builder workflow runs have `workflow_deployment_id = NULL`, so Conversations/Memory are scoped
by **workflow + owner**, not per-deployment. True per-deployment scoping first requires *deployed*
workflow runs to populate `workflow_deployment_id` at run time — a deployed-runtime change, out of scope
here. **Keep deferred; record in the gap ledger** (`docs/testing/manual-ui-e2e-test-plan.md`) with the
reason. The `/plan` must state this explicitly as an intentional deferral, not silently drop it.

---

## Coordinated frontend batch (tracked separately, shared deploy)
The **EditAgentModal `knowledge_search` leak** (`studio/src/pages/AgentListPage.tsx` L499 renders raw
`tools?.items` with no filter + no KB picker — the 3rd agent-editing surface Task 13 missed) is a
class-fix: extract shared `<ToolsPicker>` (filters `knowledge_search`) + `<KnowledgeBasePicker>` used by
CreateAgentPage, AgentDetailPage, AND the modal. Not part of this plan's task list, but **batch its
studio build into the same 0.1.155** as G1/G2 to avoid a redundant deploy.

## Constraints / environment
- node IS on this host (`/opt/homebrew/bin`): Vitest + Playwright run locally.
- Deploy local docker-desktop: `docker build … ` → `helm upgrade --install agentshield charts/agentshield
  -n agentshield-platform --reset-values --force-conflicts`.
- Target images: **registry-api 0.2.207** (G2 backend), **studio 0.1.155** (G1 + G2 wiring + modal fix).
  Bump BOTH `scripts/deploy-cpe2e.sh` AND `charts/agentshield/values.yaml`.
- Verification MUST mimic the end user: Claude-in-Chrome exploratory + Playwright `studio/e2e/*.spec.ts`
  (real Keycloak login, assert UI + network + save→reload survival), plus the bash suite + Vitest.
- Per CLAUDE.md rule 8: add `docs/bugs/*` + `docs/debugging/NNN-*` if the diagnosis is non-obvious.
