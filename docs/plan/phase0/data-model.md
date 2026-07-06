# Phase 0 — Data Model

No new tables. One CHECK-constraint change (`deployments.environment`), one response-schema extension (`PlaygroundRunResponse`), and one request-schema pattern widening (`DeploymentCreate`). Existing columns on `AgentVersion`, `PlaygroundRun`, `EvalRun`, and `EvalRunResult` are read/used unchanged.

---

## Deployment (`deployments`) — CHECK constraint change (T-10)

**Today** (`services/registry-api/models.py`, `Deployment.__table_args__`):
```python
CheckConstraint(
    "environment IN ('production','staging','canary')",
    name="ck_deployments_env",
),
```
Column: `environment String(64) NOT NULL server_default 'production'`.

**After:**
```python
CheckConstraint(
    "environment IN ('production','staging','canary','sandbox')",
    name="ck_deployments_env",
),
```

**Exact ALTER (what the migration performs):**
```sql
ALTER TABLE deployments DROP CONSTRAINT ck_deployments_env;
ALTER TABLE deployments ADD CONSTRAINT ck_deployments_env
    CHECK (environment IN ('production','staging','canary','sandbox'));
```
(Postgres cannot modify a CHECK in place; it must be dropped and re-added.)

### Alembic migration outline — `0015_deployments_env_add_sandbox.py`

Create `services/registry-api/alembic/versions/0015_deployments_env_add_sandbox.py`:

```python
"""deployments_env_add_sandbox

Add 'sandbox' to the deployments.environment CHECK constraint (Decision 20 / T-10).
Enables ungated sandbox deploys for the playground evaluation loop.

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-03
"""
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_deployments_env", "deployments", type_="check")
    op.create_check_constraint(
        "ck_deployments_env",
        "deployments",
        "environment IN ('production','staging','canary','sandbox')",
    )


def downgrade() -> None:
    # NOTE: fails if any deployment rows still have environment='sandbox'.
    # In a dev cluster, delete/repoint those rows before downgrading.
    op.drop_constraint("ck_deployments_env", "deployments", type_="check")
    op.create_check_constraint(
        "ck_deployments_env",
        "deployments",
        "environment IN ('production','staging','canary')",
    )
```

**Request-schema companion** (`services/registry-api/schemas.py`, `DeploymentCreate`):
```python
environment: str = Field("production", pattern="^(production|staging|sandbox)$")
```
(`canary` remains absent from the request schema — unchanged; it is set by internal flows, not this endpoint.)

---

## AgentVersion (`agent_versions`) — read by the new publish gate (no schema change)

Relevant existing columns (unchanged):
- `version_number Integer NOT NULL` — unique per agent (`uq_agent_versions`); the publish gate selects the row with the **max** `version_number` for the agent.
- `eval_passed Boolean NOT NULL DEFAULT false` — publish is blocked (`422 eval_not_passed`) unless the latest version has this `true`. Also the (now production-scoped) deploy gate.
- `adversarial_eval_passed Boolean NOT NULL DEFAULT false` — publish is blocked (`422 adversarial_eval_not_passed`) when the latest version's declared tools **or** the agent's bound tools include `high`/`critical` risk and this is `false`.
- `tools JSONB NOT NULL DEFAULT '[]'` — list of `{name, risk, description?}`; scanned for `risk in ('high','critical')` in the risky-tool check.
- `status`, `image_tag`, `workflow_id`, etc. — untouched.

`eval_passed` is still set via `PATCH /api/v1/agents/{name}/versions/{id}` (`AgentVersionPatch`). Auto-setting it from a passing `EvalRun` (T-4) is out of scope.

---

## PlaygroundRun (`playground_runs`) — judge fields exposed (no DB change)

Existing columns (unchanged in DB):
- `judge_score Numeric(4,3) NULL` — written by `judge.py._write_score` after `score_run`.
- `judge_status String(32) NULL` — one of `completed` / `timeout` / `error` / `no_provider` (terminal); `null` while pending.
- `judge_reason Text NULL` — one-sentence judge rationale.
- `user_id`, `agent_name`, `context`, `sandbox`, `status`, `started_at`, `completed_at`, `input_message`, `langfuse_trace_id` — unchanged.

**Response schema extension** (`PlaygroundRunResponse`, `services/registry-api/schemas.py`) — additive, non-breaking:
```python
judge_score: Optional[float] = None
judge_status: Optional[str] = None
judge_reason: Optional[str] = None
```
These populate via `model_validate` (`from_attributes=True`) directly from the ORM columns and are consumed by the eval-runner's judge poll (`GET /playground/runs/{id}`). The existing `GET /playground/runs` list response now carries them too (additive).

---

## EvalRun (`eval_runs`) & EvalRunResult (`eval_run_results`) — lifecycle unblocked (no schema change)

- `EvalRun.status String(32)` — the bug leaves this stuck at `'running'`; Slice A guarantees it always reaches `'completed'` (or `'failed'`) via the resilient loop + terminal `PATCH`.
- `EvalRun.total_items / passed_count / failed_count / overall_score` — set by the terminal `PATCH /eval-runs/{id}`; `overall_score = passed_count / total`.
- `EvalRunResult.judge_score Float NULL`, `judge_reasoning Text NULL`, `passed Boolean NULL` — now populated from the Haiku judge score (`reasoning="llm-judge (haiku)"`, `passed = score >= 0.7`) or the keyword fallback (`reasoning="keyword match (judge unavailable)"`), or the failed-item record (`judge_score=0.0`, `passed=false`, `reasoning="run-create failed: ..."`).
- `EvalRunResultCreate` / `EvalRunStatusUpdate` request schemas — unchanged; the eval-runner already posts these shapes.

---

## Entity interaction (Phase 0 loop)

```
create agent ─▶ create version (eval_passed=false)
                     │
                     ▼
  deploy {environment:"sandbox"} ── ungated ──▶ Deployment(status=pending → running)
                                                     │
                     ┌───────────────────────────────┘
                     ▼
  playground run (X-User-Sub: eval-runner, owner-bypass) ─▶ PlaygroundRun
                     │  stream ends → _complete_run → judge (Haiku)
                     ▼
        judge_score / judge_status / judge_reason written to PlaygroundRun
                     │  eval-runner polls GET /playground/runs/{id}
                     ▼
  EvalRunResult(passed = score>=0.7)  ─▶  EvalRun(status=completed, overall_score)
                     │  (T-4 out of scope) operator PATCHes version eval_passed=true
                     ▼
  publish ── eval-gated ──▶ 202 (pending_review)   [422 if latest version not eval_passed]
```
