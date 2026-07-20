# Bug: invoker grant never flips webhook `auth_mode` → `client_signed`

**Found/Fixed:** 2026-07-20 — fix in `registry-api:0.2.222` (`create_grant` flip), branch
`webhook-improvements`. Regression guard: `scripts/smoke-test-cp2-appid-behaviour.sh`
T-CP2C-APPID-002 (fails against 0.2.221, passes on 0.2.222). *Deploy + checkpoint run
pending EKS cluster reachability (VPN down at fix time).*

## Symptom

Granting an application the `invoker` role on an agent/workflow returned `201`, but the
target's webhook trigger stayed `auth_mode='token'`. The event-gateway branches on the
**stored** `agent_triggers.auth_mode`; in `token` mode it authenticates anyone holding the
path token and **never consults the invoker grant**. Net effect: the entire Decision 30
signed-webhook path was inert on the new (applications + `artifact_role_grants`) model —
the grant "bought nothing," and a `client_signed`-only sender could not be enforced. The
API + CP1 (grant CRUD) were green the whole time, hiding it — the classic "backend works,
journey broken" trap.

This is exactly the behaviour design doc `docs/design/todo/webhook-application-identity.md`
requires: §9.4 / line 231 ("the badge flips to `client_signed` the moment the *first*
`invoker` grant lands"), the §9.4 flow (line 270), and test **T-SYY-002** (§11, line 333:
"grants `invoker` … → trigger `auth_mode` flips to `client_signed`", "upgrade-on-first-grant").
It is **not** in the §12 deferred/gap ledger — so it was a required, unimplemented behaviour,
i.e. a defect, not a known gap.

## Root Cause

1. **Flip logic lived only in the retired path.**
   - **Where:** `services/registry-api/routers/webhook_clients.py::create_webhook_client`
     (lines 158-180) and `services/registry-api/routers/artifact_grants.py::create_grant`.
   - **Problem:** the "upgrade-on-first-registration" flip (`trigger.auth_mode = 'client_signed'`)
     was implemented **only** in `create_webhook_client`. Phase 4 (T011) retired that endpoint
     to `410 Gone`, and Phase 3's replacement, `create_grant`, only INSERTs the grant row — it
     never touches `agent_triggers`. So on the new path nothing flips `auth_mode`, and the
     event-gateway's `token` branch (`webhook_auth.verify_webhook_auth`) returns OK on a bare
     path-token match without ever reaching `has_active_invoker_grant`.
   - **Fix:** `create_grant` now performs the flip in the SAME transaction as the grant insert
     (`get_db` commits on handler return), guarded to `role == 'invoker' AND grantee_type ==
     'application'`: it UPDATEs every `trigger_type='webhook'` row for the artifact
     (`agent_id`/`workflow_id` selected by an explicit `artifact_type`→FK map — validated to
     `{agent,workflow}` by `_resolve_artifact`, never type-sniffed or raw-interpolated) from
     `auth_mode <> 'client_signed'` to `'client_signed'`. This mirrors the retired
     `webhook_clients` upgrade exactly, now driven by an invoker grant instead of a
     `webhook_clients` row.

2. **The flip is ONE-WAY (design-correct, preserved).**
   - **Where:** `create_grant` (no downgrade) + `revoke_grant` (unchanged).
   - **Problem/decision:** revoking the last invoker grant must NOT revert to `token` — that
     would silently re-open the coarse per-trigger bearer token the operator upgraded away
     from. The retired `webhook_clients` path was explicitly one-way for the same reason
     (webhook_clients.py lines 166-169).
   - **Fix:** `revoke_grant` is left untouched. A trigger whose last invoker grant is revoked
     stays `client_signed` and correctly authenticates nobody (fail closed) — this is what
     makes CP2c's "revoked grant → 401" and "disabled app → 401" hold.

## How it was caught

Authoring the missing CP2 checkpoint (`scripts/smoke-test-cp2-appid-behaviour.sh`) forced
reading the real gateway/grant code end-to-end (per CLAUDE.md "reason from the running
product"): the gateway reads a stored `auth_mode` that nothing on the new path ever writes.

## Image Tags

- registry-api `0.2.221` → **`0.2.222`** (the fix; new code ⇒ new tag).
- event-gateway `0.1.4` unchanged (no gateway code change — it already reads
  `applications` + `artifact_role_grants` correctly; it was only never handed a
  `client_signed` trigger to act on).

## Files Changed

- `services/registry-api/routers/artifact_grants.py` — `create_grant` flips webhook
  `auth_mode` on first application invoker grant (upgrade-on-first-grant, one-way).
- `charts/agentshield/values.yaml`, `scripts/deploy-eks.sh`, `scripts/deploy-cpe2e.sh` —
  registry-api tag `0.2.221` → `0.2.222`.
- `scripts/smoke-test-cp1-appid-infra.sh`, `scripts/deploy-cp1-appid.sh` — expected tag
  bumped to `0.2.222`.
- `scripts/smoke-test-cp2-appid-infra.sh`, `scripts/smoke-test-cp2-appid-behaviour.sh` —
  NEW: the CP2 checkpoint the branch never wrote (gateway cutover + signed-webhook e2e +
  uniform-401 byte-identity). T-CP2C-APPID-002 is the direct regression guard for this bug.

## Lessons

- **A grant is meaningless until something consumes it.** Splitting "record the grant" from
  "make the grant take effect" across two files, then retiring one of them, dropped the
  effect silently. When a write moves paths, its side effects must move with it — grep the
  old path's side effects, not just its endpoint.
- **The proof is the round-trip, not the 201.** CP1 (grant CRUD) passed throughout; only a
  test that drives grant → auth_mode → gateway enforcement (T-SYY-002 / CP2c) exposes it.
- **Retiring an endpoint is not just a 410.** `create_webhook_client` carried load-bearing
  logic (the flip) below its insert; the retirement kept the file but stranded that logic.
