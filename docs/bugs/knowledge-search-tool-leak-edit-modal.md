# `knowledge_search` still listed as a tool in the Edit Agent modal (Task 13 left one surface unfixed)

**Found:** 2026-07-18, from a user screenshot of the **Edit Agent — kb-agent** modal showing
`knowledge_search` as a checked tool in the Tools list. **Fixed:** studio `0.1.155`.

**Regression test:** `studio/src/pages/AgentListPage.test.tsx` — *"Edit Agent modal hides knowledge_search
from Tools and shows the KB picker"*. **Related:** [make knowledge_search a special config, Task 13].

## Symptom
The instruction was: knowledge_search must NOT appear as a hand-pickable tool during agent **creation and
update** — it is configured via a Knowledge Bases picker (attaching a KB wires the tool server-side). After
Task 13 shipped (studio `0.1.152/0.1.153`), the user still saw `knowledge_search` as a checkable tool — but
only on the **inline "Edit Agent" modal** reached from the Agents list (Pencil icon), not on the full-page
Create Agent or the Agent Settings tab.

## Root cause
The tools-picker (and KB-picker) markup was **copy-pasted across three agent-editing surfaces**, and Task 13
fixed only two of them:

| Surface | Component | Filtered `knowledge_search`? | KB picker? |
|---|---|---|---|
| Create Agent (full page) | `CreateAgentPage.tsx` | ✅ | ✅ |
| Agent Settings tab | `AgentDetailPage.tsx` | ✅ | ✅ |
| **Edit Agent modal** | **`AgentListPage.tsx` (`AgentEditForm`)** | ❌ rendered raw `tools?.items` | ❌ none |

`AgentListPage.tsx` rendered `{tools?.items.map(...)}` directly (no `.filter(t => t.name !== "knowledge_search")`)
and had no Knowledge Bases picker at all. It showed *checked* because `kb-agent` has a KB bound, so
`knowledge_search` is genuinely in its `agent_tools` server-side (the intended fan-out invariant) — and this
modal was the one surface still exposing that as a togglable checkbox.

This is the classic "fix the instance, not the class" trap: three duplicated pickers, a per-surface filter,
one surface forgotten. Adding the filter only to the modal would have left a 4th surface free to regress.

## Fix (the class-fix)
Extracted two shared, presentational components and used them on **all three** surfaces so the filter and the
KB-picker exist in exactly one place each:
- `studio/src/components/agent/ToolsPicker.tsx` — owns the `KNOWLEDGE_SEARCH_TOOL` constant and filters it
  out of the rendered list. No surface can list it again.
- `studio/src/components/agent/KnowledgeBasePicker.tsx` — the shared KB multi-select.

`CreateAgentPage` and `AgentDetailPage` now import these (their local `pickableTools`/inline markup removed).
The **Edit Agent modal** gained the KB picker + the same bind/unbind reconciliation as `AgentDetailPage`
(fetch current bindings → pre-select → on save unbind removed + bind added, then `updateAgent`) and now strips
`knowledge_search` from the persisted `metadata.tools` (the KB binding is the source of truth; the backend
re-asserts `knowledge_search ∈ agent_tools ⟺ ≥1 KB binding`).

**Verification:** `AgentListPage.test.tsx` regression (knowledge_search absent from `tools-picker`, `kb-picker`
present) + `CreateAgentPage`/`AgentDetailPage` tests still green (36/36) + `tsc --noEmit` clean; browser-verified
in the running product (open Edit Agent → knowledge_search gone, KB picker present, save→reload survives).
