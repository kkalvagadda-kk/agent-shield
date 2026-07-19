# Webhook Application Identity & Invoker Grants — Design Spec

**Status**: Draft (design only — nothing in this doc is implemented yet)
**Date**: 2026-07-19
**Author**: Kalyan + Claude
**Version**: 1.0.0
**Decision**: `docs/decisions.md` §Decision 30
**Related**: `docs/design/todo/rbac-design.md` (§Decision 25 — the `artifact_role_grants` model this extends; read that doc first, this one assumes its vocabulary), `contracts/webhook-signing.md` (WS-4 — the current per-trigger `webhook_clients` mechanism this replaces), `docs/design/event-gateway-threat-model.md` (public-ingress threat model — its invariants are preserved, not touched). **Independent, parallel-safe workstream**: `docs/design/mcp-tool-source-architecture.md` (Decisions 27–29) touches a disjoint set of services/tables (MCP Proxy, `governed_tool`'s output-scan gate, tool dispatch) — no shared business logic with this design, only shared linear resources (migration numbers, e2e suite numbers) to coordinate at merge time. Its own §7 explicitly defers artifact-scoped RBAC for MCP servers to future work, which should reuse this design's generic `/api/v1/artifacts/{type}/{id}/grants` endpoint (§8.1) rather than re-inventing delegation.
**Implementation**: none yet. Touches `services/registry-api/{models.py, rbac.py, routers/webhook_clients.py, routers/triggers.py}`, `services/event-gateway/webhook_auth.py`, `studio/src/components/agent-detail/SettingsTab.tsx`, `studio/src/components/workflow/WorkflowTriggersPanel.tsx`, `studio/src/api/registryApi.ts`

---

## 1. Problem Statement

A webhook trigger on an agent or workflow can run in `client_signed` mode: a sender proves identity with a per-application `client_id` + HMAC signature instead of a shared bearer token. Today that "application" is not a real identity — it's a row in `webhook_clients` keyed on `(trigger_id, client_id)`, with its own independent secret. Two consequences:

1. **No reuse.** A real sending system that needs to call five different agent webhooks is registered five separate times, gets five unrelated secrets to distribute and rotate, and there is no single place to revoke it — an operator has to know and find every trigger it touches.
2. **No authorization on who manages any of this.** `routers/triggers.py` and `routers/webhook_clients.py` — create/list/rotate/delete a trigger, register/enable/revoke a signing client — enforce nothing. `get_optional_user` is called only to stamp an audit field (`armed_by`, `created_by`); it never gates the request. Anyone who can reach registry-api can mint a webhook token or a signing secret for any agent.

Decision 25 already shipped the right-shaped role for problem #2 — `agent-admin`, scoped per artifact, stored in `artifact_role_grants` — but nothing wires trigger/webhook management into it, and (as covered in §2) the delegation mechanism Decision 25 describes was never actually built for *any* grantee type. This design closes both gaps together by making the webhook-sending application a real, RBAC-governed principal.

---

## 2. Verified Current State (code-checked 2026-07-19)

### Built

- **Event Gateway** (`services/event-gateway/`) — public webhook ingress, `POST /hooks/{agent}/{token}` and `POST /hooks/workflow/{name}/{token}`. Dual auth mode per trigger (`agent_triggers.auth_mode ∈ {token, client_signed}`), rate limiting + replay-nonce protection (Redis), uniform `401` on every failure (WS-4). None of this is being replaced — see §7 for exactly what changes underneath it.
- **`agent_triggers`** table — trigger CRUD via `routers/triggers.py` (agents) and `routers/composite_workflows.py` (workflows); `rotate-token` endpoint; server-generated token, SHA-256 hash stored.
- **`webhook_clients`** table — per-trigger signing-client allowlist via `routers/webhook_clients.py`; Studio `ClientPanel` inside `SettingsTab.tsx` (agent detail page only — no workflow equivalent).
- **`artifact_role_grants`** table (migration `0044`) — `agent-admin` / `approver` roles, `grantee_type ∈ {user, team}`, creator auto-grant on agent/workflow create (`rbac.grant_creator_admin`), read-side policy functions in `rbac.py` (`has_artifact_role`, `can_deploy_to_production`, `can_approve_hitl`, `can_delegate_role`).
- **`asset_grants`** + `AdminGrantsPage.tsx` — a **different, pre-existing feature**: platform-admin-only visibility grants ("can team X see/bind published asset Y"). Explicitly independent of `artifact_role_grants` per Decision 25 ("visibility vs. authority... having visibility does not imply authority"). Called out here because the name is easy to confuse with the delegation UI this design adds — they are not the same table, the same page, or the same concern.

### Gaps this design closes

| Gap | Where verified |
|---|---|
| `webhook_clients` is per-trigger, not reusable across an application's real footprint | `models.py` `WebhookClient.trigger_id` FK, `UniqueConstraint(trigger_id, client_id)` |
| Trigger + webhook-client management endpoints have zero authorization | `routers/triggers.py` — no `Depends` gating `list_triggers`/`get_trigger`/`update_trigger`/`delete_trigger`; `routers/webhook_clients.py` — same, `get_optional_user` is audit-only |
| No delegation/grant-management endpoint exists for **any** grantee type | Grepped `services/registry-api` for `artifact_role_grants` writers — only `rbac.grant_creator_admin` (system auto-grant) exists. Decision 25's documented rule ("agent-admin can grant agent-admin and approver within their scope") has no API behind it. |
| `rbac.py` enforcement is off platform-wide | `rbac.py`: `ENFORCE = False` under `require_global_role`, explicit "Phase 1" stub comment |
| No workflow equivalent of the agent `ClientPanel` UI | `WorkflowTriggersPanel.tsx` has no client/application code path |

---

## 3. Goals / Non-Goals

**Goals**
- One reusable credential per real sending application — not one per `(application, trigger)`.
- Trigger and application management enforced through the *same* RBAC surface as human access (`artifact_role_grants`), not a separate, ungated path.
- A working kill switch: disable an application everywhere it's granted, in one action.
- Close the "no delegation endpoint" gap generically — the grants API this design adds works for `user`/`team`/`application` grantees alike, so it also finishes Decision 25, not just this feature.
- **Provide the first live-traffic proof that `artifact_role_grants` delegation actually works.** `rbac.has_artifact_role` and `rbac.can_delegate_role` exist and are unit-tested (suite-42) but, as of this writing, have **zero callers in any router** — nothing in the live product exercises them. `rbac-design.md` §12 already specified delegation test cases (T-S32-010 through T-S32-012) that could never actually run, because no endpoint existed to run them against. This design's generic grants endpoint (§8.1) is the first live consumer of that machinery; §11 below adds the suite that finally makes those test cases real, for all three grantee types — not just `application`/`invoker`.

**Non-goals (deferred — see §12, tracked so they aren't silently assumed away)**
- Cross-team application reuse (an application usable by two different teams' agents).
- Splitting `agent-admin` into separate manage-user vs. manage-application capabilities.
- A bounded fallback policy for what happens when an application-triggered run hits a HITL approval gate with nobody present. **This is the one that matters most** — `invoker` grants are functionally live without it, they just inherit today's `awaiting_approval`-forever behavior.
- Flipping `rbac.py`'s `ENFORCE` flag platform-wide — pre-existing, separate concern; this design's new endpoints call the same policy functions everything else will eventually be gated by.
- **Wiring `can_deploy_to_production` into the production-deploy endpoint or `can_approve_hitl` into HITL routing.** Both are also unwired today (confirmed by the same grep that found the delegation gap), but wiring them changes the behavior of already-shipped, security-sensitive paths — that's the remainder of `rbac-design.md` §13.2's own TODO list, a separate and materially larger change, not something to pull in here as a side effect of a webhook feature.

---

## 4. Principals & Roles

| Principal | Scope | Notes |
|---|---|---|
| **Application** *(new)* | Owned by one team | A named, reusable machine identity for a webhook-sending system. One secret. Not tied to any single agent/workflow at creation time. |
| **`invoker`** role *(new)* | Per agent or workflow (artifact-scoped, same granularity as `agent-admin`/`approver`) | "This application may send authenticated webhooks to this artifact's `client_signed` trigger(s)." Stored as an `artifact_role_grants` row with `grantee_type='application'`. |

Extends the existing table from `rbac-design.md` §2.2:

| Role | Scope | Grants |
|---|---|---|
| `agent-admin` | agent/workflow | *(unchanged)* suspend/resume/scale/deploy/rollback/config; can grant `agent-admin`, `approver`, **and now `invoker`** within their artifact scope |
| `approver` | agent/workflow | *(unchanged)* receives HITL approvals |
| **`invoker`** | agent/workflow | **(new)** the holder (an `application`, never a `user`/`team`) may authenticate to this artifact's webhook trigger(s) in `client_signed` mode |

---

## 5. Data Model

### 5.1 New table — `applications`

```sql
CREATE TABLE applications (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_name        VARCHAR(255) NOT NULL,          -- owning team; mirrors agents.team
    name             VARCHAR(128) NOT NULL,          -- human-chosen, e.g. "billing-service"
    secret_encrypted TEXT NOT NULL,                  -- Fernet, same AGENTSHIELD_ENCRYPTION_KEY as today
    enabled          BOOLEAN NOT NULL DEFAULT true,   -- kill switch, independent of any one grant
    created_by       VARCHAR(255) NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    rotated_at       TIMESTAMPTZ NULL,
    CONSTRAINT uq_applications_team_name UNIQUE (team_name, name)
);
CREATE INDEX idx_applications_team ON applications(team_name);
```

One secret, full stop — rotating it rotates it everywhere the application holds `invoker`. `enabled=false` disables the application against *every* grant at once; this is distinct from revoking one grant, which only removes access to one artifact.

### 5.2 Widen `artifact_role_grants` (migration `0044`) — additive, no data loss

```sql
ALTER TABLE artifact_role_grants DROP CONSTRAINT ck_arg_grantee_type;
ALTER TABLE artifact_role_grants ADD CONSTRAINT ck_arg_grantee_type
    CHECK (grantee_type IN ('user', 'team', 'application'));

ALTER TABLE artifact_role_grants DROP CONSTRAINT ck_arg_role;
ALTER TABLE artifact_role_grants ADD CONSTRAINT ck_arg_role
    CHECK (role IN ('agent-admin', 'approver', 'invoker'));
```

`grantee_id` (already polymorphic `TEXT`) holds `applications.id::text` when `grantee_type='application'`. No column shape change. `invoker` is deliberately scoped per **artifact**, not per **trigger** — if an agent has two enabled webhook triggers, one `invoker` grant covers both. This matches the granularity every other artifact-scoped role already uses; the trigger's path token still addresses *which* URL a request hits, exactly as WS-4 already established for `client_signed` mode — it names the endpoint, not the caller.

### 5.3 Migrating `webhook_clients` rows

For every existing `webhook_clients` row: insert one `applications` row (`team_name` = the owning agent/workflow's team, `name` = `client_id`, `secret_encrypted` copied byte-for-byte — same Fernet key, no forced rotation) and one `artifact_role_grants` row (`role='invoker'`, `artifact_id` = the trigger's `agent_id`/`workflow_id`). If the same `client_id` string was independently registered under two different teams' triggers, it becomes two distinct `applications` rows — no cross-team merge. `webhook_clients` and its router are kept **read-only** for one release after cutover, then dropped in a follow-up migration (same phasing Decision 22 used for the `workflows`→`agent_graphs` rename).

---

## 6. Grant & Delegation Model

Extends `rbac-design.md` §3 (Grant Model) with a third grantee kind and a new consumer of the delegation rule.

- **Who creates an application:** any user with global role ≥ `contributor` **and** membership in the owning team — mirrors `rbac.can_create_agent`. Deliberately *not* gated on `agent-admin` of any specific artifact: application identity is team-scoped, so its creation authority sits one level above any single agent. This is what prevents the ownership-fragmentation problem (two agent-admins each inventing their own "billing-service").
- **Who grants `invoker`:** an `agent-admin` on the target artifact, granting an application that **already exists** in their team's registry — they pick from a list, they never mint a new identity from the agent's Settings screen. `rbac.can_delegate_role` extends to accept `target_role='invoker'` under the existing "agent-admin can delegate within scope" rule.
- **Revocation:** soft-delete (`revoked_at`), identical semantics to `agent-admin`/`approver` revocation today — orphan-keep, no cascade (Decision 25's existing rule).
- **Kill switch vs. revoke:** `applications.enabled=false` denies the application on *every* grant simultaneously (team-level action, for "this credential is compromised, shut it off everywhere now"). Revoking one `artifact_role_grants` row removes access to *one* artifact only (agent-admin-level action, for "this application shouldn't reach this specific agent anymore"). Both are live-read, no cache — same posture as today's `webhook_clients.enabled` check ("a disable takes effect on the very next webhook").

**Permission check** (extends `rbac.py`, same shape as existing functions):

```python
async def can_invoke(db, application_id: uuid.UUID, artifact_id: uuid.UUID) -> bool:
    app = await get_application(db, application_id)
    if app is None or not app.enabled:
        return False
    return await has_artifact_role(
        db, grantee_type="application", grantee_id=str(application_id),
        artifact_id=artifact_id, role="invoker",
    )
```

(`has_artifact_role` in `rbac.py` today only checks `grantee_type IN ('user','team')` against a `user_sub`/`team` — it needs a third branch for `application`, since the caller here isn't a human session.)

---

## 7. Gateway Verification Flow

`event-gateway/webhook_auth.py::_verify_client_signed` is the only runtime code that changes. Today it resolves `lookup_webhook_client(trigger_id, client_id)` — a direct lookup against `webhook_clients`. It becomes:

```
1. Resolve the trigger's owning artifact (agent_id or workflow_id) — _TRIGGER_SQL already selects this.
2. Look up an `applications` row by (team_name, client_id-as-name).
   → not found ⇒ deny (indistinguishable from every other failure — unchanged)
3. Check an ACTIVE, non-revoked artifact_role_grants row:
   (role='invoker', grantee_type='application', grantee_id=applications.id,
    artifact_type/artifact_id = the trigger's artifact)
   → no active grant ⇒ deny
4. Check applications.enabled
   → disabled ⇒ deny
5. Decrypt applications.secret_encrypted, HMAC-verify exactly as today.
```

**What does not change:** fail-closed posture, the single `_DENY` sentinel / uniform `401` body (T-9, still no enumeration oracle — "no grant" and "app disabled" and "bad signature" remain byte-identical to the caller), constant-time comparison, and reading live on every request with no cache (a revoked grant or a disabled application takes effect on the very next webhook, same as `webhook_clients.enabled` today). This is a change of *where the allowlist row lives*, not a change to any WS-4 security invariant — the threat model in `docs/design/event-gateway-threat-model.md` is unaffected.

---

## 8. API Surface

### 8.1 Generic artifact grants (new — closes Decision 25's undelivered delegation endpoint for *all* grantee types)

```
POST   /api/v1/artifacts/{artifact_type}/{artifact_id}/grants
       { "grantee_type": "user" | "team" | "application", "grantee_id": "...", "role": "agent-admin" | "approver" | "invoker" }
GET    /api/v1/artifacts/{artifact_type}/{artifact_id}/grants
DELETE /api/v1/artifacts/{artifact_type}/{artifact_id}/grants/{grant_id}
```
Gated by `rbac.can_delegate_role`. Note this single endpoint set is *not* application-specific — a `user`/`team` `agent-admin`/`approver` grant goes through the identical path. Building one generic surface instead of an `invoker`-only one-off avoids re-creating the exact "two parallel paths" pattern this codebase has already been burned by (WS-4 header, citing `docs/bugs/side-effecting-lost-on-declarative-runner-path.md`).

### 8.2 Applications (new)

```
POST   /api/v1/teams/{team}/applications                     — create; { "name": "billing-service" }; secret returned ONCE
GET    /api/v1/teams/{team}/applications                     — list (no secret field, ever)
POST   /api/v1/teams/{team}/applications/{id}/rotate-secret  — new secret, returned ONCE
PATCH  /api/v1/teams/{team}/applications/{id}                — { "enabled": bool } — kill switch
DELETE /api/v1/teams/{team}/applications/{id}                — hard delete, cascades grants
```

Response shapes mirror the reveal-once pattern already used for webhook tokens (`RotateTokenResponse`) and today's `WebhookClientCreatedResponse` — `ApplicationResponse` has no `secret` field at all, so no read path can leak it, structurally, the same guarantee `webhook_clients.py`'s header calls out for the mechanism being replaced.

### 8.3 `routers/triggers.py` / `routers/webhook_clients.py` — authorization added

`create_trigger`, `update_trigger`, `delete_trigger`, `rotate_token` gain `Depends` on `rbac.can_deploy_to_production`-equivalent artifact-scoped check (`agent-admin` on the target agent/workflow) — currently these have **no** gate at all (§2). `webhook_clients.py` itself is retired per §5.3's phased migration; its replacement is §8.1 (grant `invoker`) + §8.2 (manage the application), not a direct port.

---

## 9. Studio UX — End-to-End Flows

### 9.1 Personas

| Persona | Role | Does what in this design |
|---|---|---|
| **Priya** | `contributor`, member of team `payments` | Creates and owns the `billing-service` application on behalf of her team |
| **Amit** | `agent-admin` on agent `invoice-processor` (auto-granted, he created it) | Grants `billing-service` `invoker` access to `invoice-processor` |
| **billing-service** | external system (not a Studio user) | Sends signed webhooks to `invoice-processor`'s trigger |
| **Dana** | `agent-admin` on `invoice-processor`, a different person than Amit (Amit delegated `agent-admin` to her) | Rotates the secret after a suspected leak; revokes access entirely later |

### 9.2 Screen inventory

| Screen | Location | New/changed |
|---|---|---|
| **Team Applications panel** | New — team settings area (alongside where team membership is already managed) | New |
| **Trigger card → "Invoke access" section** | `studio/src/components/agent-detail/SettingsTab.tsx`, replacing today's inline-create `ClientPanel` | Changed |
| **Workflow trigger card → "Invoke access" section** | `studio/src/components/workflow/WorkflowTriggersPanel.tsx` | New (parity gap closed — today this panel has no client/application UI at all) |
| **Artifact grants list** (`agent-admin`, `approver`, `invoker` holders on one artifact) | New — same Settings surface, above or alongside the trigger card | New — this is also where a human `agent-admin`/`approver` grant would be managed now that the delegation endpoint exists (§8.1) |

### 9.3 Flow A — Priya creates the application

1. Priya, in the `payments` team's settings, opens **Applications** → **New application**.
2. Names it `billing-service`. Clicks **Create**.
3. Studio shows the secret **once**, in a copy-able box, with the same "copy it now, it's never shown again" warning pattern as today's `ClientPanel` — if lost, rotate (§9.6), don't try to recover it.
4. `billing-service` now appears in the `payments` team's Applications list, **not yet usable anywhere** — creating it grants it access to nothing. It has zero `invoker` grants until an agent-admin adds one.

### 9.4 Flow B — Amit grants `billing-service` access to his agent

1. Amit opens `invoice-processor` → **Settings** → its webhook trigger card.
2. Under **Invoke access**, clicks **Grant access**. A picker lists applications *he can see* — every application owned by a team he belongs to (today just `payments`, since that's Amit's team too).
3. He selects `billing-service`. Before confirming, Studio shows an explicit acknowledgment distinct from a human grant: *"billing-service will be able to trigger runs on invoice-processor without a human present. Approval-gated steps in this agent may stall if nobody is watching."* (This is risk-asymmetry Option 1 from the design conversation — cheap now, the real fix is §12's deferred bounded-approval policy.)
4. Confirms. The trigger card's auth-mode badge (already present today, `token` vs `client_signed`) flips to `client_signed` the moment the *first* `invoker` grant lands on this artifact — same upgrade-on-first-registration behavior as today, just driven by a grant instead of a `webhook_clients` insert.
5. `billing-service` now appears in the trigger card's **Invoke access** list, next to any human `agent-admin`/`approver` grants on the same artifact, each independently revocable.

No secret is ever visible on this screen — granting access and holding a credential are two different actions performed by two different people in this example (Priya owns the credential, Amit grants access to it), which is the entire point of decoupling application identity from artifact grant.

### 9.5 Flow C — `billing-service` calls the webhook (happy path)

1. `billing-service` signs its payload with the secret from Flow A: `X-Client-Id: billing-service`, `X-Timestamp`, `X-Signature`.
2. `POST /hooks/invoice-processor/{token}`.
3. Gateway resolves the trigger's artifact → looks up `applications` by `(payments, billing-service)` → finds the active `invoker` grant from Flow B → checks `enabled` → verifies the signature → dispatches the run.
4. From `billing-service`'s point of view this is byte-identical to today's WS-4 flow — same headers, same signing recipe (`webhook_auth.sign_webhook` is unchanged, §7). The only thing that moved is what the gateway checks *server-side*.

### 9.6 Flow D — Dana rotates a suspected-leaked secret

1. Dana (or anyone with access to the `payments` Applications panel) opens `billing-service` → **Rotate secret**.
2. New secret shown once, same reveal-once UX as creation.
3. Every artifact `billing-service` holds `invoker` on — potentially several agents across the team, not just `invoice-processor` — starts requiring the new secret **immediately**, in one action. This is the reuse win the whole redesign is for: today's equivalent would require finding and rotating N separate `webhook_clients` rows across N triggers.

### 9.7 Flow E — Revoke access vs. kill switch

- **Revoke one grant** (agent-admin action, on the trigger card's Invoke access list): `billing-service` immediately stops being able to call *this one* agent. Its access to any other agent it's granted on is untouched.
- **Kill switch** (`enabled=false`, team Applications panel): `billing-service` immediately stops working *everywhere*, without touching any individual grant — for "this credential leaked, shut it down now, sort out which agents need it back later."

### 9.8 Error / edge states surfaced in the UI

| Situation | What the UI shows |
|---|---|
| Agent-admin opens the grant picker, team has zero applications | Empty state pointing at the team Applications panel — "No applications registered for your team yet." (Not an inline create — creation lives at the team level only, §6.) |
| Application is disabled (kill switch) but still has active grants | Grant list shows the grant with an "application disabled" badge, not silently hidden — an agent-admin should be able to see *why* the trigger stopped working without cross-referencing the team Applications panel. |
| Webhook request fails auth (bad signature, revoked grant, disabled application) | **No UI signal at all**, by design — the gateway's uniform-401 invariant (§7) is unchanged, and that is a deliberate security property, not a gap. The operator-facing diagnosis is the gateway log (`webhook_auth._deny`'s reason), not the Studio UI. This should be called out explicitly to whoever builds the UX so nobody "fixes" it into a distinguishable error. |

### 9.9 Full lifecycle, narrated (mirrors `rbac-design.md` §11 style)

```
1. Priya (contributor, payments team) creates application "billing-service"
   → secret shown once; zero grants; unusable until an agent-admin grants it something

2. Amit (agent-admin on invoice-processor) grants billing-service "invoker"
   → can_delegate_role: ✓ (Amit is agent-admin on invoice-processor)
   → trigger auth_mode flips to client_signed on this artifact

3. billing-service sends a signed webhook to invoice-processor
   → resolved via (team, client_id) → active invoker grant → enabled → signature verifies
   → run starts

4. Amit delegates agent-admin on invoice-processor to Dana
   → can_delegate_role: ✓ (existing Decision 25 rule, unchanged)

5. Dana rotates billing-service's secret after a suspected leak
   → every artifact billing-service holds invoker on requires the new secret immediately

6. Frank (contributor, payments team, NOT agent-admin on invoice-processor)
   → tries to grant billing-service invoker on invoice-processor → 403
   → tries to create a new application under payments → ✓ (team-level action, §6)

7. Dana revokes billing-service's invoker grant on invoice-processor specifically
   → billing-service's other grants (if any, on other agents) are untouched

8. Priya disables billing-service entirely (kill switch)
   → every remaining grant across every agent stops authenticating on the next request
```

---

## 10. Migration Strategy

1. **Ship additive.** New `applications` table, widened `artifact_role_grants` constraints, new endpoints (§8) — none of this touches the live gateway path yet. `webhook_clients` keeps serving traffic.
2. **Backfill.** Run the §5.3 migration: one `applications` row + one `invoker` grant per existing `webhook_clients` row. Verify counts match before cutover.
3. **Cut over the gateway.** Deploy the `webhook_auth.py` change (§7) reading from `applications` + `artifact_role_grants` instead of `webhook_clients`. Fail-closed by construction — if backfill missed a row, that application starts 401ing (loud, safe) rather than silently succeeding against stale data.
4. **Studio cutover.** Ship the new UI (§9) in the same release as step 3 — don't leave the old `ClientPanel` pointing at a table the gateway no longer reads.
5. **Deprecate `webhook_clients`.** Keep the table + router read-only for one release (rollback safety net), then drop both in a follow-up migration.

---

## 11. E2E Test Plan

Two suites. §11.1 is **scope added specifically because this design is the first live consumer of `artifact_role_grants` delegation** (§3) — it validates the RBAC foundation itself, through the new generic grants endpoint, independent of webhooks. §11.2 is the webhook/application-specific behavior on top of it. Keeping them separate means a failure in one doesn't obscure which layer broke: "delegation is broken" vs. "delegation works but the gateway isn't reading it correctly."

### 11.1 Suite A — Artifact Delegation Foundation (new — closes the `rbac-design.md` §12 gap)

New bash suite `scripts/e2e/suite-XX-artifact-grants.sh`. Test IDs below map directly onto `rbac-design.md` §12's originally-specified delegation cases (T-S32-010, T-S32-011, T-S32-012) — those were written down but never implementable, since no endpoint called `can_delegate_role` until now. This suite is what makes them real, plus coverage `rbac-design.md` didn't anticipate needing (team-grantee resolution, platform-admin bypass, duplicate/invalid grants) because it was written before any concrete consumer existed.

| ID | Test | Proves |
|---|---|---|
| T-ARG-001 | agent-admin grants `agent-admin` to another user on their artifact → 201, row exists in `artifact_role_grants` | Basic delegation, `user` grantee — realizes rbac-design.md T-S32-010 |
| T-ARG-002 | agent-admin grants `approver` to a **team** → 201; a member of that team subsequently passes `has_artifact_role` for that role | `team` grantee resolution (`rbac.get_user_team` join) — the more complex of the two non-application grantee paths, previously untested against live traffic |
| T-ARG-003 | agent-admin grants `invoker` to an application they own → 201 | Ties §11.1 to §11.2 — same endpoint, third grantee kind |
| T-ARG-004 | contributor with **no** scoped role on the artifact attempts any grant → 403 | Delegation blocked without authority — realizes rbac-design.md T-S32-011 |
| T-ARG-005 | `platform-admin` grants a role on an artifact they hold no `agent-admin` grant on → 201 | `can_delegate_role`'s platform-admin bypass branch |
| T-ARG-006 | Revoke a grant (`DELETE .../grants/{id}`) → subsequent `has_artifact_role` check returns false, `GET .../grants` no longer lists it | Soft-delete (`revoked_at`) actually takes effect — realizes rbac-design.md T-S32-012 |
| T-ARG-007 | Attempt to grant a role outside `{agent-admin, approver, invoker}` → 422 | `ck_arg_role` CHECK constraint enforced at the API layer, not just the DB |
| T-ARG-008 | Attempt to grant to a `grantee_id` that doesn't resolve (unknown user sub / unknown team / unknown application id) → 400/404, not a silent no-op insert | No orphan grants pointing at nothing |
| T-ARG-009 | Grant the same `(artifact, role, grantee)` twice → second attempt is a conflict, not a duplicate row | `uq_arg_active_grant` unique-active-grant index actually reachable via the API |
| T-ARG-010 | Cleanup test artifacts | Housekeeping |

### 11.2 Suite B — Webhook Applications (invoker-specific)

New bash suite `scripts/e2e/suite-YY-webhook-applications.sh` (number assigned at implementation time — next free slot after Suite A):

| ID | Test | Proves |
|---|---|---|
| T-SYY-001 | Create application under team → row exists, no grants | Application creation is inert on its own |
| T-SYY-002 | agent-admin grants `invoker` to an application on their agent → 201, trigger `auth_mode` flips to `client_signed` | Grant + upgrade-on-first-grant |
| T-SYY-003 | contributor (not agent-admin on that artifact) attempts to grant `invoker` → 403 | Delegation gate enforced (specific instance of T-ARG-004, kept here too so this suite is self-contained) |
| T-SYY-004 | Signed webhook from a granted application → 202, run dispatched | Happy path end-to-end through the real gateway |
| T-SYY-005 | Signed webhook from a *revoked*-grant application → uniform 401 | Revocation takes effect live, no cache |
| T-SYY-006 | Signed webhook from a *disabled* (kill-switch) application → uniform 401, byte-identical body to T-SYY-005 | No enumeration oracle introduced |
| T-SYY-007 | Rotate secret → old secret's signature now fails, new secret's succeeds | Rotation propagates without re-granting |
| T-SYY-008 | Same application granted `invoker` on two different agents → revoking one grant leaves the other working | Grant scope is per-artifact, not global |
| T-SYY-009 | Migrated `webhook_clients` row produces an equivalent `applications` + `invoker` grant pair; old signature still verifies post-cutover | Migration correctness (§10 step 2–3) |
| T-SYY-010 | Cleanup test artifacts | Housekeeping |

Register both in `scripts/e2e/run-all.sh`; Suite A should run (and pass) before Suite B, since Suite B's grant steps depend on the same delegation endpoint Suite A validates in isolation.

### 11.3 Playwright (`studio/e2e/`)

Required per this repo's Definition of Done — a bug in the reactive gateway path, or in the delegation endpoint, can't be caught by a component test:

- `artifact-grants.spec.ts` — new: from an agent's Settings, grant `agent-admin`/`approver` to a user or team, assert it appears in the grants list, reload, assert it survived (save→reload→assert rule). This is the first Playwright coverage of delegation at all, human-grantee or not.
- `webhook-applications.spec.ts` — new: create an application, grant it `invoker` on an agent from Settings, assert the grant appears, reload, assert it survived.
- Extend `webhook-clients.spec.ts` or retire it alongside `webhook_clients.py` per §10 step 5 — don't leave it asserting against a dead code path.
- Extend `workflow-builder.spec.ts` / add a workflow-trigger-specific spec to cover the new parity: granting `invoker` from `WorkflowTriggersPanel.tsx`.

---

## 12. Deferred / Future Improvements (honest gap ledger)

| Item | Status | Why |
|---|---|---|
| Cross-team application reuse | **deferred (intentional)** | Option A (team-owned) chosen over B (platform-owned) in Decision 30; revisit if a real cross-team sender shows up |
| Split manage-user-access vs. manage-application-access RBAC capability | **deferred (intentional)** | Seam preserved — `invoker` is already a distinct role value, not folded into `agent-admin` — so this can split later without a data migration |
| **Bounded unattended-approval fallback policy** | **not-yet-wired (debt)** | `invoker` grants are functionally live without it. An application-triggered run that hits a HITL checkpoint sits `awaiting_approval` with nobody specifically notified faster than the existing failure-alert path (Decision 23). **Must land before this is relied on for anything production-critical** — this is the one deferred item that isn't just "nice to have later." |
| `rbac.py` global-role enforcement (`ENFORCE=False` on `require_global_role`) | **pre-existing debt, inherited, not introduced here** | Gates `platform-admin`/`contributor`/`viewer` admin-route access only — orthogonal to artifact-scoped delegation (see next row). Flipping it on is a separate, global change (same caution as Decision 26's OPA-enforcement canary). |
| `can_deploy_to_production` / `can_approve_hitl` unwired (no router calls them) | **pre-existing debt, inherited, explicitly NOT addressed here** | Confirmed still true as of this design. §11.1 proves the underlying `has_artifact_role`/`can_delegate_role` machinery works correctly via the new grants endpoint — but that's a different code path from these two functions, which remain uncalled by the production-deploy and HITL-decide endpoints. Wiring them is the rest of `rbac-design.md` §13.2's TODO list; out of scope for this design (§3 non-goals) because it changes behavior of already-shipped, security-sensitive routes and deserves its own review, not a ride-along. |
| Tool-level / per-trigger `invoker` granularity (finer than whole-artifact) | **deferred (intentional)** | Mirrors Decision 25's existing deferred "tool-level approver granularity" — artifact-level is judged sufficient for now |
| Role audit log (who granted/revoked what, when, beyond `granted_by`/`granted_at`/`revoked_at`) | **deferred (intentional)** | Same deferral as Decision 25 §14 — not introduced or worsened by this design |

---

## 13. Open Questions

- **Application discovery scope**: the grant picker in Flow B (§9.4) shows "every application owned by a team the agent-admin belongs to." If an agent-admin belongs to multiple teams, should the picker show all of them, or only the team that owns the *agent* being configured? Leaning toward the latter (owning-team-only) for tighter scoping, but not locked — worth deciding before `/plan` fixes the query shape.
- **What happens to an `invoker` grant if the owning application's team changes/is deleted?** Not addressed here; `applications.team_name` has no `ON DELETE` behavior specified in §5.1. Needs a decision before the migration ships (likely: block team deletion while it owns any application, mirroring how agent deletion is presumably guarded today — verify against actual team-deletion code before assuming).
