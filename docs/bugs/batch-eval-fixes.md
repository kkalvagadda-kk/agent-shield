# Batch Eval Known Issues — Fix Plan (2026-07-07)

## Symptom

Eval pipeline works mechanically (agents deployed, responses produced, scores recorded) but:
- Judge always falls back to keyword match ("judge unavailable")
- `save_run_to_dataset` produces items with no `expected_output` → keyword match auto-passes
- `evalRunnerImage` tag not in values.yaml → fragile, diverges on bumps
- `kubectl set image` skips alembic init container → silent schema drift

## Root Causes (4 issues)

### 1. Judge doesn't support Bedrock provider

**Where:** `services/registry-api/judge.py`

**Problem (3 bugs):**
- Line 135: `from crypto import decrypt_value` — function doesn't exist (only `decrypt_json` in `crypto.py:39`). Causes ImportError → empty string → "no provider"
- Line 143: `.where(LLMProvider.provider == "anthropic")` — DB has `provider = "bedrock"` → query returns None
- Lines 98–113: Hardcoded `https://api.anthropic.com/v1/messages` with `x-api-key` header — won't work with Bedrock credentials (needs boto3 `invoke_model`)

**Fix:**
- Rename `_resolve_provider_key` → `_resolve_provider`, return `(provider_type, model, creds_dict)` tuple
- Import `decrypt_json` (not `decrypt_value`); `decrypt_json` returns dict directly
- Remove `.provider == "anthropic"` filter — find ANY provider for the team
- Split into `_call_judge_anthropic` (existing urllib code) + `_call_judge_bedrock` (new, boto3)
- Dispatch: env var ANTHROPIC_API_KEY → anthropic path; else resolve from DB → bedrock or anthropic path
- Add `boto3>=1.35.0` to `requirements.txt`

### 2. evalRunnerImage missing from values.yaml

**Where:** `charts/agentshield/values.yaml`, `services/registry-api/k8s.py:26-28`

**Problem:** Template (`deployment.yaml:88-89`) has inline default `0.1.1`. Python fallback (`k8s.py:27`) is stale `0.1.0`. values.yaml (canonical tag source for all images) has no `evalRunnerImage` key. Tag bumps require editing 3 places instead of 1.

**Fix:**
- Add `evalRunnerImage: "registry.internal/agentshield/eval-runner:0.1.1"` to values.yaml near registry-api section
- Update `k8s.py:27` Python fallback from `0.1.0` → `0.1.1`

### 3. kubectl set image skips alembic init container

**Where:** `scripts/deploy-cpe2e.sh`, `charts/agentshield/charts/registry-api/templates/deployment.yaml:48-50`

**Problem:** `kubectl set image deployment/agentshield-registry-api registry-api=NEW_TAG` updates only the main container. The `alembic-migrate` init container (line 48) stays pinned to old Helm-rendered tag. New migrations silently skipped → schema diverges → runtime errors.

**Fix:** Add deploy-safety documentation in `scripts/deploy-cpe2e.sh`:
```bash
# IMPORTANT: Always use `helm upgrade` to deploy registry-api changes.
# `kubectl set image` only updates the main container — the alembic-migrate
# init container stays pinned to the old tag, skipping new migrations.
# If you must use kubectl, update BOTH containers:
#   kubectl set image deployment/agentshield-registry-api \
#     alembic-migrate=registry.internal/agentshield/registry-api:$REGISTRY_API_TAG \
#     registry-api=registry.internal/agentshield/registry-api:$REGISTRY_API_TAG \
#     -n agentshield-platform
```

### 4. save_run_to_dataset drops output_text

**Where:** `services/registry-api/routers/playground.py:791-800`

**Problem:** `new_item` dict omits `run.output_text`. Dataset items saved from playground runs have no expected value. When judge is unavailable, eval-runner keyword match checks `expected.lower() in response.lower()` — with empty expected, it auto-passes.

**Fix:** Add `"expected_output": run.output_text or ""` to the `new_item` dict. Key name matches `eval-runner/main.py` which reads `item.get("expected_output", "")`.

## Image Tags

| File | Key | Old | New |
|------|-----|-----|-----|
| `scripts/deploy-cpe2e.sh` | `REGISTRY_API_TAG` | `0.2.71` | `0.2.72` |
| `charts/agentshield/values.yaml` | registry-api `image.tag` (L517) | `0.2.71` | `0.2.72` |

## Files Changed

| File | Change |
|------|--------|
| `services/registry-api/judge.py` | Bedrock support, fix decrypt import, provider dispatch |
| `services/registry-api/requirements.txt` | Add `boto3>=1.35.0` |
| `services/registry-api/k8s.py` | Fix stale fallback `0.1.0` → `0.1.1` |
| `services/registry-api/routers/playground.py` | Add `expected_output` to save_run_to_dataset |
| `charts/agentshield/values.yaml` | Add `evalRunnerImage`, bump registry-api tag |
| `scripts/deploy-cpe2e.sh` | Bump REGISTRY_API_TAG, add alembic deploy note |

## Verification

1. Syntax: `python3 -c "import ast; ast.parse(open('services/registry-api/judge.py').read())"`
2. Import: `cd services/registry-api && python3 -c "import judge"`
3. Helm: `helm template charts/agentshield` renders without error, `evalRunnerImage` in output
4. E2e: suite-9 (eval pipeline) still green
5. Deploy 0.2.72 → run eval on simple-qa → judge_status="completed", LLM score (not keyword fallback)

### 5. Inconsistent artifact visibility model

**Where:** `services/registry-api/routers/tools.py`, `services/registry-api/routers/skills.py`

**Problem:** Agents and workflows had proper visibility filters (`published OR created_by == caller`). Tools and skills had NO visibility filter — all records visible to all users regardless of ownership. Inconsistent with user expectation that only published or self-created artifacts are shown.

**Fix:**
- Migration 0033: add `created_by` (VARCHAR 256) and `publish_status` (VARCHAR 32, default 'published') to tools; add `publish_status` to skills
- `tools.py`: add `get_optional_user` + `X-User-Sub` deps, apply `or_(published, created_by == caller)` filter
- `skills.py`: same visibility filter pattern
- `tools.py` create endpoint: populate `tool.created_by = caller`
- Existing tools default to `publish_status='published'` (backward-compatible — all existing tools remain visible)

## Image Tags

| File | Key | Old | New |
|------|-----|-----|-----|
| `scripts/deploy-cpe2e.sh` | `REGISTRY_API_TAG` | `0.2.71` | `0.2.72` |
| `charts/agentshield/values.yaml` | registry-api `image.tag` (L517) | `0.2.71` | `0.2.72` |

## Files Changed

| File | Change |
|------|--------|
| `services/registry-api/judge.py` | Bedrock support, fix decrypt import, provider dispatch |
| `services/registry-api/requirements.txt` | Add `boto3>=1.35.0` |
| `services/registry-api/k8s.py` | Fix stale fallback `0.1.0` → `0.1.1` |
| `services/registry-api/routers/playground.py` | Add `expected_output` to save_run_to_dataset |
| `services/registry-api/routers/tools.py` | Visibility filter + created_by on create |
| `services/registry-api/routers/skills.py` | Visibility filter |
| `services/registry-api/models.py` | Add created_by + publish_status to Tool; publish_status to Skill |
| `services/registry-api/schemas.py` | Expose new fields in ToolResponse + SkillResponse |
| `services/registry-api/alembic/versions/0033_tool_skill_visibility.py` | New migration |
| `charts/agentshield/values.yaml` | Add `evalRunnerImage`, bump registry-api tag |
| `scripts/deploy-cpe2e.sh` | Bump REGISTRY_API_TAG, add alembic deploy note |

## Verification

1. Syntax: `python3 -c "import ast; ast.parse(open('services/registry-api/judge.py').read())"`
2. Import: `cd services/registry-api && python3 -c "import judge"`
3. Helm: `helm template charts/agentshield` renders without error, `evalRunnerImage` in output
4. E2e: suite-9 (eval pipeline) still green
5. Deploy 0.2.72 → run eval on simple-qa → judge_status="completed", LLM score (not keyword fallback)
6. Migration 0033: verify tools/skills tables have new columns, existing rows have publish_status='published'

## Lessons

- Never import a function without verifying it exists in the target module (`decrypt_value` was never real)
- DB provider filter should match what's actually stored — don't hardcode provider assumptions
- Every image tag must live in values.yaml as the single source of truth
- `kubectl set image` is unsafe for pods with init containers that must stay in sync
- Visibility model must be consistent across all artifact types — if agents filter by published/created_by, so should tools/skills/workflows
