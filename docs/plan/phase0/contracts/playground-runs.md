# Contract — Playground Runs (Slice A)

Base path: `/api/v1/playground`. Auth: internal `X-User-Sub` header (no header ⇒ dev/`system`). Handlers in `services/registry-api/routers/playground.py`.

---

## POST /api/v1/playground/runs — start a run (owner check + eval-runner bypass)

**Request body** (`PlaygroundRunCreate`):
```json
{ "agent_name": "smoke-agent", "input_message": "hello", "agent_version_id": null }
```
Headers: `X-User-Sub: <caller>` (optional).

**Owner-check rule (changed):** let `caller = X-User-Sub`.
- No header → allowed (dev).
- `caller in {"eval-runner"}` (reserved service identity) → **allowed for any agent** (skip owner check). ← NEW
- `caller == agent.created_by` → allowed.
- Otherwise → **403**.

### 201 Created
```json
{ "run_id": "3f1c…", "stream_url": "/api/v1/playground/runs/3f1c…/stream" }
```

### 403 Forbidden — non-owner, non-service caller
```json
{ "detail": "Only the agent owner can run it in the playground." }
```

### 404 Not Found — unknown agent
```json
{ "detail": "Agent 'no-such-agent' not found." }
```

### 422 — missing `agent_name` / `input_message` (FastAPI validation)

**Examples**
| Caller (`X-User-Sub`) | `agent.created_by` | Result |
|---|---|---|
| `eval-runner` | `smoke-user` | **201** (bypass) |
| `eval-runner` | `system` | **201** (bypass) |
| `mallory` | `smoke-user` | **403** |
| `smoke-user` | `smoke-user` | **201** |
| *(none)* | `smoke-user` | **201** (dev) |

Tests: T-S8-022 (bypass 201), T-S8-023 (403), T-S9-011 (bypass for `created_by='system'` agent).

---

## GET /api/v1/playground/runs/{run_id} — single run incl. judge fields (NEW)

No owner check (consistent with `/stream`, `/trace`). Response model `PlaygroundRunResponse` (now includes judge fields).

### 200 OK
```json
{
  "id": "3f1c…",
  "user_id": "eval-runner",
  "agent_name": "smoke-agent",
  "agent_version_id": null,
  "context": "playground",
  "sandbox": true,
  "input_message": "hello",
  "status": "completed",
  "started_at": "2026-07-03T18:00:00Z",
  "completed_at": "2026-07-03T18:00:04Z",
  "judge_score": 0.92,
  "judge_status": "completed",
  "judge_reason": "Accurate and complete."
}
```
- `judge_score`: float 0.0–1.0 or `null` (still pending / judge unavailable).
- `judge_status`: `completed` | `timeout` | `error` | `no_provider` | `null`.
- `judge_reason`: string or `null`.

### 422 — invalid UUID
```json
{ "detail": "Invalid run_id format" }
```

### 404 — unknown run
```json
{ "detail": "Playground run '3f1c…' not found." }
```

Tests: T-S8-024, T-S9-012. Consumed by the eval-runner judge poll (see `eval-runner-integration.md`).

---

## Unchanged in this slice
`GET /playground/runs` (list — now also serializes judge fields, additive), `GET /playground/runs/{id}/stream`, `/trace`, `/save-to-dataset`, `/feedback`. The judge itself (`judge.py`, Haiku `claude-haiku-4-5-20251001`) still fires from `_complete_run` on stream end and writes `judge_score`/`judge_status`/`judge_reason` onto the `PlaygroundRun` row — this slice only exposes those fields via the new GET.
