# POC-5 — Conversations & Memory in the Product

**Status**: Proposed (2026-07-16)
**Branch**: `worktree-ux-preview-context-storage` (commit only here; never merge to main — Karthik merges manually)
**Companion**: [`context-storage-ux-roadmap.md`](./context-storage-ux-roadmap.md) §6 (sequencing) · [`context-storage-architecture.md`](./context-storage-architecture.md) §11 (subsystem spec)
**Live baseline**: `registry-api:0.2.189`, `studio:0.1.142`
**Mock**: interactive nav mock published 2026-07-16 (Studio nav before/after · env-filtered Conversations page · docked History both envs · deployment Overview Conversations tab)

> Build order is **POC-2 → POC-3 → POC-4 → POC-5**; POC-5 is scheduled last but is **independent of POC-3/POC-4** (depends only on the POC-0/1 storage that already exists). This doc captures the full POC-5 design so nothing is lost; the formal `/plan` + `/tasks` artifacts get generated **when POC-5 starts**, re-grounded against the then-current code (POC-2b lands first and touches some of the same chat surfaces — generating implementation tasks now would drift before use).

---

## 1. Why this exists

POC-0/1 persist every conversation (fail-loud checkpointer + `agent_memory` transcript). But the product never *shows* them. POC-5 is the surface layer: **list your past conversations, reopen one, keep chatting** — everywhere a chat is exposed, in sandbox and production.

**Reason from the running product.** Today "Conversations" and "Multi-agent Chat" exist **only** under a demo-gated *Context Preview* section in the sidebar (`Sidebar.tsx` `PREVIEW_ITEMS`, `/preview/*`, shown when the `DEMO` flag is on). Real users never see them. The stored data is real; the surfaces are scaffolding. POC-5 makes them real.

---

## 2. Scope decisions (captures this discussion)

1. **Promote `Conversations` into the real menu; retire `Context Preview`.** It becomes a top-level nav item (personal, primary surface), not a demo screen. "Multi-agent Chat" does **not** get its own nav item — it's the renderer *inside* each console (Playground / Catalog / Workflow Builder), so a link would be redundant.
2. **One entry + in-page environment filter, not two links.** The menu already splits sandbox/production for dashboards (`Prod Dashboard` / `Sandbox Dashboard`, `OBSERVE_ITEMS`). Conversations deliberately does **not** copy that — a single `Conversations` entry with an **All / Sandbox / Production** in-page filter avoids nav clutter. (Env is carried per `ConversationSummary`.)
3. **Three surfaces, one machinery.** All three reuse the same `ConversationSidebar` component and the same two endpoints — the only difference is scope (`deployment_id` present or not):
   - **Standalone `Conversations` page** — cross-agent (`/me/conversations`), env filter.
   - **Docked `History` panel** in each chat console — `AgentChatPage` (sandbox) and `CatalogChatPage` (production consumer).
   - **`Conversations` tab on `DeploymentOverviewPage`** — deployment-scoped; sandbox or production.
4. **`Conversations` ≠ `Memory`.** The existing `Memory` tab (`MemoryTab.tsx`, admin: view / `deleteMemoryThread` / `clearAgentMemory`) is the **operator inspect/manage** lens. `Conversations` is the **user resume** lens. Same store, two intents — they sit side by side on the deployment Overview.
5. **No new storage.** The list is a read-side aggregate over `agent_memory`. **Continue already works** — reusing a thread's `session_id` on the next `/chat` POST reloads its earlier turns as context (`declarative-runner/main.py::_load_memory_context`). The only new backend piece is the list query.
6. **Title = first user message** (Haiku titling stays deferred to POC-1b).

---

## 3. Architecture

### 3.1 Backend (one new read query, two endpoints)

```python
# memory.list_conversations(user_id, agent_name?, deployment_id?) -> [ConversationSummary]
#   aggregate agent_memory grouped by thread_id:
#     title         = first user message (array_agg(content ORDER BY message_index) FILTER (role='user'))[1]
#     message_count = count(*)
#     last_activity = max(created_at)
#     session_id, agent_name, environment          # environment carried for the filter/badge
#   surfaced via the ConversationStore port; caller-scoped (require_user), ownership-filtered.
```

Two `require_user` endpoints (new `ConversationSummary` schema):
- `GET /agents/{name}/memory/conversations?deployment_id=` — **scoped**: docked History panel + deployment Overview tab.
- `GET /me/conversations` — **cross-agent**: the standalone page. Each row carries `environment` so the page filter is a pure client predicate.

### 3.2 Frontend — the menu change

- `Sidebar.tsx`: remove the `PREVIEW_ITEMS` *Context Preview* section; add a real top-level `Conversations` item (`History` icon, route `/conversations`). Env-agnostic entry; the page itself filters.
- Retire `/preview/*` scaffolding once the real screens exist (keep the components; re-point them at real data).

### 3.3 Frontend — three mount points (shared `ConversationSidebar`)

| Surface | File / route | Scope | Env |
|---|---|---|---|
| Standalone page | `ConversationsPage` → `/conversations` | `/me/conversations` (cross-agent) | All / Sandbox / Production filter |
| Docked History | `AgentChatPage` (`/agents/:name/chat`) + `CatalogChatPage` (`/catalog/:artifactId/chat`) | `?deployment_id=` | inherits the console's env |
| Deployment tab | `DeploymentOverviewPage` (`/agents/:name/d/:depId`) — add `Tab = "conversations"` beside `overview \| runs \| memory` | `?deployment_id=<depId>` | that deployment's env (sandbox or production) |

**Shared component** `ConversationSidebar`:
- "New conversation" → fresh uuid `sessionId` + clear messages.
- Select a row → set `sessionId = thread_id` + seed `messages` from `listMemory(thread_id)` → the chat pane rehydrates and the reply box continues on that thread.
- `AgentChatPage.tsx:68` `sessionId` made resettable (the one existing-code change the resume flow needs).

**Deployment Overview tab** specifically: two-pane — conversation list (left) → click → transcript rehydrates in a chat pane with a live reply box. Works identically in sandbox and production (only the `deployment_id` differs).

### 3.4 Memory's third home (unchanged)

The admin `Memory` tab on `DeploymentOverviewPage` already exists — operator inspect/delete over the same store. POC-5 adds `Conversations` beside it; it does **not** change `MemoryTab`. Per-user privacy scoping on the admin Memory view is a Tighten-line item (S9).

---

## 4. Verification (Definition of Done gate)

- **Playwright** — for **each** of the three surfaces, in **both** environments where applicable: reload → conversation still listed → click → prior transcript rehydrates (from `/memory`, not the store) → follow-up **continues with context** (assert it recalls a turn-1 fact). Deployment-tab spec drives `/agents/:name/d/:depId`, flips the env, asserts scoped list + resume.
- **Vitest** — `ConversationSidebar` (list / empty / select / new); env-filter predicate on the standalone page; `DeploymentOverviewPage` renders the new tab and mounts the sidebar.
- **suite-75** — `T-S75-007` per-thread summaries (ownership-scoped), `T-S75-008` `/me/conversations`, plus a deployment-scoped case (`?deployment_id=`) proving sandbox and production each return only their own threads. *(Final IDs assigned at implement time — POC-2b takes T-S75-009..011 first.)*
- **No orphan code** — grep each new symbol (`list_conversations`, `ConversationSummary`, `ConversationSidebar`, the `conversations` tab) for a live caller.
- **Image bumps** — registry-api + studio (in `deploy-cpe2e.sh` + `deploy-eks.sh` + `charts/agentshield/values.yaml`). Deploy is a **separate, user-gated** shared-cluster step.

## 5. Known gaps (ledger)

- **Admin `MemoryTab` per-user privacy** — deferred → Tighten S9 (unchanged by this POC).
- **Haiku titling** — deferred to POC-1b; title = first user message until then.
- **Cross-agent env accuracy** — `environment` must be persisted on / derivable for each thread for the standalone filter to be correct; confirm the write path stamps it (POC-0 `deployment_id` propagation) at `/plan` time.
