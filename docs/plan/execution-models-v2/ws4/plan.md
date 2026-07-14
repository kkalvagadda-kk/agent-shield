# WS-4 Implementation Plan — Event-driven e2e + dual-mode client-id / allowlist / HMAC signing

**Slice:** WS-4 of Execution Models v2 (spec §5 WS-4; B-series webhook auth). **Covers WS-4 ONLY.**
**Independent of the WS-0→WS-3 spine** — off the critical path, can land in parallel (needs WS-0 only for the
`agent_class=daemon` default on event agents; the auth work is standalone).
**Companion artifacts:** `data-model.md` (`webhook_clients` + `auth_mode`), `contracts/webhook-signing.md`.

> **Migration PROVISIONAL** — next free after the spine's migrations. WS-4 adds `webhook_clients` +
> `agent_triggers.auth_mode`. Confirm head at impl.

> ⚠️ **Plan status — design stable, specifics indicative.** The architecture, sequencing, and locked
> decisions (D1–D4, R1–R3, parity gates, gap ledger) here are **stable and reviewable now** — that is what
> writing ahead buys. The execution specifics — `file:line`, migration numbers, image tags, orphan-greps,
> exact task order — are **indicative against the 2026-07-12 tree** and WILL drift as the WS-0→ spine merges.
> **Re-ground every specific against live code when this slice is minted into its own `tasks.md`** (the
> just-in-time step). Never treat a `file:line` or migration number here as ground truth. (CLAUDE.md: design
> docs go stale — verify in code before relying.)

## 1. Goal

Upgrade the webhook gateway from a **single coarse per-trigger bearer token** to **per-application
client-id + allowlist + HMAC request signing**, dual-mode so existing token senders keep working during
migration. Covers **both** the agent hook (`/hooks/{name}/{token}`, `event-gateway/main.py:314`) and the
workflow hook (`/hooks/workflow/{name}/{token}`, `:199`) with **no schema change** for the workflow case
(clients key on `trigger_id`, and workflow triggers are `agent_triggers` rows with `workflow_id` set).
Concretely, after WS-4:

1. **`webhook_clients` table** `(id, trigger_id FK, client_id, secret_hash, enabled, created_by, created_at,
   UNIQUE(trigger_id, client_id))` — per-application credentials, allowlisted per trigger.
2. **`agent_triggers.auth_mode ∈ {token, client_signed}`** — dual-mode. New webhook triggers default
   `client_signed`; existing stay `token`; the gateway accepts either **per-trigger** (explicit mode, no
   silent fallthrough — No-Bandaid). Migrate senders one at a time, delete the flag later.
3. **Signed wire contract** — `X-Client-Id`, `X-Timestamp`, `X-Signature: sha256=HMAC_SHA256(secret,
   f"{ts}.{raw_body}")`. Verify order: client-id ∈ allowlist + enabled → `|now−ts| ≤ 300s` →
   constant-time HMAC compare → existing filter + rate-limit → dispatch. **Uniform 401** for
   unknown/bad-sig/stale/disabled/wrong-trigger (identical body — no oracle). Stamp `agent_events.client_id`.
4. **Registration API** `POST /api/v1/triggers/{id}/clients` — returns the secret **once**, stores the hash.
5. **Studio panel (in this slice)** — trigger-config client registration: add client, **reveal secret once**,
   enable/disable, per-client audit; the event log shows the resolved `client_id`.
6. **Event durable daemon** — event agents authored `agent_class=daemon` (WS-0 default); with the spine, an
   event **durable** run runs durable + async routing (WS-1/WS-2). WS-4 itself only owns the auth hop.

**Out of scope:** replay **nonce store** (WS-4 uses a timestamp window only — documented gap); the durable
run behavior (WS-1/WS-2 — WS-4 stops at "authenticated dispatch").

## 2. Architecture — the verify hop wraps both handlers (parity)

```
POST /hooks/{name}/{token}            (agent   — main.py:314)
POST /hooks/workflow/{name}/{token}   (workflow — main.py:199)
        │  both call ▼
   verify_webhook_auth(trigger, headers, raw_body):        ← ONE shared function (parity core)
     if trigger.auth_mode == "token":       existing hmac.compare_digest(token_hash, sha256(token))  (:99,:136)
     elif trigger.auth_mode == "client_signed":
        client = lookup(webhook_clients, trigger_id, X-Client-Id)   → 401 if missing/disabled
        assert |now - X-Timestamp| <= 300                            → 401 if stale
        hmac.compare_digest(client.secret_hash-derived, X-Signature) → 401 if bad (constant-time)
     → on pass: existing filter_engine + rate_limiter, then dispatch, stamp agent_events.client_id
```

**Dual-mode is an explicit per-trigger `auth_mode`** — the gateway branches on the stored mode, never
"try token, fall back to signed" (that priority fallthrough is the No-Bandaid anti-pattern). Both hook
handlers call the **same** `verify_webhook_auth`; the workflow hook needs no new code beyond calling it.

## 3. Migration / Schema — see `data-model.md`

`webhook_clients` table + `agent_triggers.auth_mode VARCHAR NOT NULL DEFAULT 'token'` (existing rows → token;
the create endpoint sets `client_signed` on new webhook triggers). Idempotent, guarded.

## 4. Constitution / retro gates (condensed)

- **Parity:** one `verify_webhook_auth` wraps both the agent and workflow hook — grep proves no per-handler
  copy. The signing helper (sender-side) is shipped as a reference so the test and real senders share it.
- **Golden-path per environment:** bash `suite-59` exercises valid-signed→200, bad-sig/stale/unknown/disabled/
  wrong-trigger→401 (identical body), legacy token under `auth_mode=token`→200 — **against the real gateway**,
  and the **same suite hits `/hooks/workflow/{name}/{token}`**. Playwright: register a client in Studio,
  secret shown once, disable → 401. Fails (not skips) on missing fixture.
- **Ship the gate's producer:** the `client_id` allowlist (producer = registration API + Studio panel) ships
  with the gateway check that reads it — no orphan gate (a required client-id with no way to register it).
- **Fail-closed:** unknown/bad/stale/disabled → **401 deny**, uniform body (no timing/enumeration oracle).
- **No-Bandaid:** explicit `auth_mode` per trigger, not silent fallthrough; secret stored hashed, revealed
  once.

## 5. File Structure

### event-gateway
| File | C/M | Responsibility |
|---|---|---|
| `services/event-gateway/main.py` | M | `verify_webhook_auth(trigger, headers, raw_body)` shared fn; both hook handlers (`:199`, `:314`) call it; stamp `agent_events.client_id`. |
| `services/event-gateway/webhook_auth.py` | **C** | Client lookup + timestamp-window + constant-time HMAC verify; uniform-401. |

### registry-api
| File | C/M | Responsibility |
|---|---|---|
| `services/registry-api/routers/triggers.py` | M | `POST /triggers/{id}/clients` (secret once), `GET/DELETE /clients`, enable/disable; set `auth_mode=client_signed` on new webhook triggers. |
| `services/registry-api/models.py` | M | `WebhookClient` model; `AgentTrigger.auth_mode`. |
| `services/registry-api/alembic/versions/00NN_webhook_clients.py` | **C** | `webhook_clients` + `auth_mode` (provisional number). |
| `services/registry-api/schemas.py` | M | Client create/response (secret-once) shapes. |

### Studio
| File | C/M | Responsibility |
|---|---|---|
| `studio/src/pages/AgentDetailPage.tsx` (trigger config) | M | Client registration panel: add / reveal-once / enable-disable / per-client audit; event log shows `client_id`. |
| `studio/src/api/registryApi.ts` | M | `createTriggerClient`/`listTriggerClients`/`setClientEnabled`. |

### Tests + infra
| File | C/M | Responsibility |
|---|---|---|
| `scripts/e2e/suite-59-webhook-client-signing.sh` | **C** | Signed 200 + 5×401 variants + legacy token; **same suite hits the workflow hook**; sign helper doubles as sender ref. |
| `scripts/e2e/run-all.sh` | M | Register suite-59. |
| `studio/e2e/webhook-clients.spec.ts` | **C** | Register client, secret shown once, disable → 401. |
| `studio/src/pages/AgentDetailPage.test.tsx` | M | Vitest: client panel renders, secret-once reveal, disable. |
| `scripts/deploy-cpe2e.sh` + `charts/agentshield/values.yaml` | M | Bump event-gateway, registry-api, studio. |
| `docs/experience/playground.md` | M | Webhook client-id / signing UX + event-log `client_id`. |

## 6. Tasks (dependency-ordered)

### T1 — Migration + models (`webhook_clients` + `auth_mode`)
- **Files:** migration `00NN` (C), `models.py` (M). Contract: `data-model.md`.
- **Acceptance:** upgrade round-trips idempotently; existing triggers → `auth_mode='token'`; `WebhookClient`
  mapper configures; `UNIQUE(trigger_id, client_id)` enforced.
- **Deps:** none. **Verify:** `ast.parse` + `configure_mappers()`; migration up/down/up.

### T2 — Registration API (secret once)
- **Files:** `routers/triggers.py` (M), `schemas.py` (M).
- **Contract:** `POST /triggers/{id}/clients` → `{client_id, secret}` **once**, stores `secret_hash`; new
  webhook trigger create sets `auth_mode='client_signed'`; list/enable/disable.
- **Acceptance:** create returns secret once; re-GET never returns it; disable flips `enabled`.
- **Deps:** T1. **Verify:** suite-59 registration cases.

### T3 — Gateway shared verify (`webhook_auth.py`) wrapping both hooks
- **Files:** `event-gateway/webhook_auth.py` (C), `main.py` (M).
- **Contract:** `contracts/webhook-signing.md` — verify order, uniform 401, constant-time compare, 300s window;
  both hook handlers call `verify_webhook_auth`; stamp `agent_events.client_id`.
- **Acceptance:** valid signed → 200 + dispatch; bad-sig/stale/unknown/disabled/wrong-trigger → 401 identical
  body; `auth_mode=token` trigger still works with the legacy bearer token.
- **Deps:** T1, T2. **Verify:** suite-59 (all variants + workflow hook); `grep -c "verify_webhook_auth" services/event-gateway/main.py` → 2 call sites, 1 def.

### T4 — Studio client panel
- **Files:** `AgentDetailPage.tsx` (M), `registryApi.ts` (M), `AgentDetailPage.test.tsx` (M),
  `webhook-clients.spec.ts` (C).
- **Acceptance:** register client → secret shown once → disable → gateway 401 (Playwright drives the real
  gateway); event log shows resolved `client_id`.
- **Deps:** T2, T3. **Verify:** `cd studio && npm run typecheck && npm run test`; `bash scripts/studio-e2e.sh`.

### T5 — Suite-59 + deploy
- **Files:** `suite-59-webhook-client-signing.sh` (C), `run-all.sh` (M), `deploy-cpe2e.sh`+`values.yaml` (M),
  `docs/experience/playground.md` (M).
- **Acceptance:** suite green incl. the workflow-hook cases; tags bumped in both files.
- **Deps:** T1–T4. **Verify:** `bash scripts/e2e/suite-59-webhook-client-signing.sh`.

## 7. Gap Ledger
| Item | Status | Note |
|---|---|---|
| Replay **nonce** store | **deferred (intentional) → future** | v1 uses the 300s timestamp window only; a replay inside the window is possible. Documented spec gap. |
| Per-client rate limits | deferred (intentional) | Rate-limit stays per-trigger (existing `rate_limiter`); per-client limits are a follow-up. |
| Auto-migrate all senders off `token` + delete the flag | not-yet-done (debt, intentional) | Dual-mode ships; the flag deletion happens once every sender is on `client_signed`. |

No orphan flags: `webhook_clients` (producer=registration API + Studio panel, reader=gateway verify),
`auth_mode` (producer=trigger create, reader=gateway branch), `client_id` stamp (producer=verify, reader=event
log UI) — all shipped together.

## 8. Execution Notes
- **Off the spine** — WS-4 can proceed in parallel with WS-1/2/3; it only needs WS-0's `agent_class=daemon`
  default for event agents (cosmetic to the auth work).
- **Uniform 401** — never leak which check failed (unknown client vs bad sig vs stale) via body or timing.
- **Ship the signing helper as a sender reference** so the e2e test and real applications sign identically —
  one implementation, no drift.
- **Workflow hook is free** — it's an `agent_triggers` row with `workflow_id`; `verify_webhook_auth` wraps it
  with zero schema change.
