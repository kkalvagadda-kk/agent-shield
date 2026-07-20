# Bug: migration 0070 chained to a `0069` that doesn't exist in this branch

**Found/Fixed:** 2026-07-20 — fix in `registry-api:0.2.223` (migration `0070` `down_revision`
`0069` → `0068`), branch `webhook-improvements`.

## Symptom

Every new `registry-api:0.2.222` pod stuck `Pending`: its `alembic-migrate` init container
exited 1 with:

```
File ".../alembic/script/revision.py", line 245, in _revision_map
    down_revision = map_[downrev]
KeyError: '0069'
```

The rollout never completed — the cluster kept serving the old `0.2.190` pods, so the
whole webhook-application-identity feature (and the `0.2.222` `create_grant` flip fix)
never went live, and CP1/CP2 could not run.

## Root cause

`0070_applications_and_invoker_grants.py` declared `down_revision = "0069"`, but **no
migration with `revision = "0069"` exists in this branch's `alembic/versions/`.** The
tree goes `0068 → (nothing) → 0070 → 0071`.

`0069` belongs to a **different, concurrent workstream** — `0069_mcp_server_fields.py`
(MCP-tools branch) — which also forked from `0068` and claimed the `0069` slot. This
branch's migration was renumbered `0069 → 0070` to avoid the ID collision, and its
`down_revision` was set to `"0069"` on the assumption that the MCP branch's `0069` would
already be applied ahead of it. That file is not part of `webhook-improvements`, and the
target DB was at head `0064` (nowhere near `0069`), so alembic could not resolve `0069`
and aborted the entire upgrade.

This is a **cross-branch coupling** defect: a migration in branch A must never depend on a
revision that lives only in branch B. Each branch's migration graph has to be
self-consistent against the shared parent (`0068`).

## Fix

`0070.down_revision = "0068"` — the real common parent that exists in this branch. Chain
is now `0068 → 0070 → 0071`, self-consistent and applies cleanly from any DB at/above
`0068` (and from `0064` via `0065..0068`). `0070` has **no data dependency** on the MCP
fields (it only `CREATE TABLE applications` + widens `artifact_role_grants` CHECKs, which
came from `0044`), so chaining off `0068` is not just expedient — it's correct.

Revision ID stays `0070` (a harmless numbering gap; alembic resolves by DAG, not by
contiguity).

## Merge-time note (not a bug, but don't be surprised)

When `webhook-improvements` and the MCP branch both land on `main`, `0068` will have **two
children** — this `0070` and the MCP `0069`. That is a normal alembic branch point;
reconcile it with a single `alembic merge <0071> <mcp-head>` revision at integration time.
Do NOT re-couple them by editing `down_revision` back to `0069` — that reintroduces this
exact ordering dependency.

## Image Tags

- registry-api `0.2.222` → **`0.2.223`** (migration file lives in the image; the init
  container runs `alembic upgrade head` from it, so the fix requires a rebuild).

## Files Changed

- `services/registry-api/alembic/versions/0070_applications_and_invoker_grants.py` —
  `down_revision "0069" → "0068"` + corrected header rationale.
- `charts/agentshield/values.yaml`, `scripts/deploy-eks.sh`, `scripts/deploy-cpe2e.sh`,
  `scripts/deploy-cp1-appid.sh`, `scripts/smoke-test-cp1-appid-infra.sh`,
  `scripts/smoke-test-cp2-appid-infra.sh` — registry-api tag `0.2.222 → 0.2.223`.

## Lessons

- **A renumber-to-dodge-a-collision must re-point `down_revision` at a revision that
  exists in your OWN tree**, never at the sibling migration you're dodging. Renaming the
  ID and re-pointing the parent are two separate decisions; conflating them created a
  dangling edge.
- **The init container is the real gate.** `alembic upgrade head` failing there is a hard
  `Pending`/rollout-stall — invisible to a bash API suite (it hits the *old* still-Running
  pod and passes). Only checking the NEW pod's tag + init-container exit code (CP1b
  T-CP1B-APPID-001/002) catches it. `kubectl get pods` phase + `initContainerStatuses` is
  the first thing to read on a stuck rollout.
