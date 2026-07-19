# Quickstart — POC-2b Rich Multi-Agent Workflow Console

For the implementing agent. Assumes the worktree
`/Users/kkalyan/repo/agent-platform/.claude/worktrees/ux-preview-context-storage`. Commit ONLY to
`worktree-ux-preview-context-storage`; never merge to main.

## 0. Read first
- `plan.md` (this dir) — tasks, interfaces, file table.
- `research.md` — grounding + the 4 corrections that differ from the design doc.
- `data-model.md`, `contracts/endpoints.md`, `contracts/sse-frames.md`.
- Spec: `docs/design/context-storage-poc-2b-rich-console.md`. Gates: root `CLAUDE.md`.

## 1. Build order (dependency-topological)
```
Backend  : T001 → T002 → T003        (rationale extract → executor → runner persist/emit)
           T004 → T005                (pod reader → single-agent chat)
           T004 → T006 → T007         (pod reader → member stream → mode generators/drain)
           T008 → T009                (schemas → tree projection + /runs/stream)  [needs T007]
Frontend : T010,T011 → T012 → T015    (reducers/chip → bubble → vitest)
           T013 ; then T009+T010+T012+T013 → T014 → T017
Docs     : T018 (any time)
Images   : T019 (with the shipping code, same commit)
```
`[P]` tasks with disjoint files (T001/T004/T008/T010/T011/T013/T018) may run in parallel.

## 2. Per-language verification (run after each task)
- **Python (registry-api)**: `cd services/registry-api && python3 -c "import ast; ast.parse(open('<file>').read())"`. For schema/router changes also: `python3 -c "import routers.composite_workflows, schemas; from sqlalchemy.orm import configure_mappers; configure_mappers()"`.
- **Python (declarative-runner)**: `cd services/declarative-runner && python3 -c "import ast; ast.parse(open('<file>').read())"`.
- **Python (sdk)**: `cd sdk && python3 -c "import ast; ast.parse(open('agentshield_sdk/graph_builder.py').read())"`.
- **TypeScript**: `cd studio && npm run typecheck`.
- **Vitest**: `cd studio && npm run test`.

## 3. CP1 — Backend deploy + smoke (after T001–T009, T016, T019)
Deploy is **user-gated** (shared EKS cluster). After reviewer go:
```
bash scripts/deploy-eks.sh                       # builds+pushes 0.2.190 / 0.1.55 / 0.1.143, Helm upgrade
bash scripts/checkpoints/poc-2b-cp1-smoke.sh     # runs suite-75 (009/010/011) + curls /runs/stream
```
Pass gate: `suite-75` exits 0 (PASS or justified capacity-SKIP, never a broken-pod SKIP); `/runs/stream`
returns `text/event-stream` with an `agent_start` frame carrying `author`; the `/runs` drain yields a
matching terminal tree.

Manual backend spot-check (in-pod):
```
kubectl exec -it -n agentshield-platform <registry-api-pod> -c registry-api -- \
  curl -sN -X POST localhost:8000/api/v1/workflows/<id>/runs/stream \
       -H 'Content-Type: application/json' -H "Authorization: Bearer <jwt>" \
       -d '{"message":"go"}' | head
```

## 4. CP2 — Frontend deploy + browser smoke (after T010–T015, T017, T018)
```
bash scripts/checkpoints/poc-2b-cp2-smoke.sh     # typecheck + vitest + Playwright (context-rich-console.spec)
# or directly:
cd studio && npm run typecheck && npm run test
bash scripts/studio-e2e.sh                        # Playwright (real Keycloak; separate gate)
```
Pass gate: Vitest green; Playwright proves progressive reveal (researcher bubble present while answerer
absent, gated on `waitForResponse` of `/runs/stream`) + avatars + tool chip + rationale toggle + save→reload,
or capacity-skips with no assertion failure.

## 5. Definition-of-Done self-check before reporting done
- [ ] Playwright `context-rich-console.spec.ts` proves the real journey (DoD #1).
- [ ] Reload assertion reads bubbles+rationale from `/memory`/tree, not the store (DoD #2).
- [ ] No orphan: grep each new symbol for a live caller (see plan.md "No-orphan ledger"):
      `grep -rn "stream_pod_chat_frames\|_dispatch_stream\|orchestrate_stream\|ToolCallChip\|attachToolCall\|attachRationale\|_extract_tool_rationale" services/ sdk/ studio/src`.
- [ ] Gaps recorded in `docs/testing/manual-ui-e2e-test-plan.md` (DoD #5).
- [ ] `docs/experience/playground.md` updated (Post-impl #3).
- [ ] Images bumped in all 3 files, tags consistent (Post-impl #2):
      `grep -rn "0.2.190\|0.1.55\|0.1.143" scripts/deploy-cpe2e.sh scripts/deploy-eks.sh charts/agentshield/values.yaml`.
- [ ] `bash scripts/e2e/run-all.sh` (or at least suite-75) green.
- [ ] Reactive tool-call fixture uses a **low-risk** tool (high-risk trips HITL → reactive fails-closed).

## 6. Gotchas (from grounding)
- **`run_steps`, not `agent_run_steps`**; reactive members write none — `_dispatch_stream` must persist the marker rows.
- **`_extract_reasoning` ≠ turn-boundary rationale** — use the new `_extract_tool_rationale` (last AIMessage WITH tool_calls).
- **`/runs/stream` is POST** — frontend uses `fetch`+`ReadableStream`, not `EventSource`.
- **`web_search` is high-risk** — fixtures attach a low-risk tool instead.
- **No new migration** — 0064 already has `message_kind='rationale'`.
- **Keep `_proxy_agent_stream`'s signature** — `stream_chat`/`stream_deployment_chat` must stay untouched.
- **Drain parity** — the non-stream `/runs` must produce the same terminal tree as before (regression risk in T007).
