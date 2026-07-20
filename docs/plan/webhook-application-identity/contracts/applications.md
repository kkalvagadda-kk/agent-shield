# Contract — Team Applications

`services/registry-api/routers/applications.py`. Router prefix `/api/v1/teams/{team}/applications` (path param on the prefix segment before the router's own sub-paths, matching `routers/teams.py`'s existing `/api/v1/teams` + `/{id}/agents` shape one level up).

## Security invariants

- `POST` / `POST .../rotate-secret` / `PATCH` / `DELETE` all require `require_user` **and** `rbac.can_create_application(db, caller_sub, team)` → `403` otherwise. This is deliberately **stricter** than `rbac.can_create_agent` (contributor+ with no team check) — application identity is team-scoped by design (Decision 30 Option A), so creation/rotation/kill-switch/delete authority requires the caller's own team (from `user_team_assignments`) to equal the `{team}` path segment, or `platform-admin`.
- `GET` (list) requires `require_user` only — no team-membership check — matching the read-open convention used elsewhere (any authenticated user can see which applications exist; no application list response ever contains a secret).
- The secret is returned **exactly once**: on `201` from create, and on `200` from rotate-secret. `ApplicationResponse` (the shape every other read path returns) has **no `secret` field at all** — reveal-once is a property of the type, not handler discipline, identical to `WebhookClientResponse`'s existing guarantee.
- Secret is Fernet-encrypted (`crypto.encrypt_json`/`decrypt_json`, `AGENTSHIELD_ENCRYPTION_KEY`) — **not hashed** — because the event-gateway must recompute `HMAC_SHA256(secret, ...)` to verify a signature, so the raw value must be recoverable server-side. Same reasoning `webhook_clients.py`'s header already documents for the mechanism this replaces.
- Secret prefix `whsec_` (reused verbatim from `webhook_clients.py` — same operator-facing meaning: "this is a webhook signing secret," regardless of which table stores it; see `research.md` §10).

---

## `POST /api/v1/teams/{team}/applications`

Create an application. Zero grants on creation — per design doc Flow A, creating an application grants it access to nothing; an `agent-admin` must separately grant it `invoker` via the artifact-grants endpoint.

**Request** (`ApplicationCreate`):
```json
{ "name": "billing-service" }
```

**201 response** (`ApplicationCreatedResponse` — the ONLY shape carrying `secret`):
```json
{
  "id": "b6c1e2a4-9e3f-4c1a-9b0a-7f2d8e1c4a5b",
  "name": "billing-service",
  "secret": "whsec_AbCdEf0123456789...",
  "created_at": "2026-07-19T18:00:00Z"
}
```

**Errors:** `401` no token; `403` caller not contributor+ in `{team}`; `409` `(team, name)` already exists (`uq_applications_team_name` caught via `IntegrityError`, same pattern `webhook_clients.py::create_webhook_client` already uses for its own unique-constraint 409).

---

## `GET /api/v1/teams/{team}/applications`

List a team's applications — never a secret.

**200 response** (`ApplicationResponse[]`):
```json
[
  {
    "id": "b6c1e2a4-9e3f-4c1a-9b0a-7f2d8e1c4a5b",
    "team_name": "payments",
    "name": "billing-service",
    "enabled": true,
    "created_by": "priya-user-sub",
    "created_at": "2026-07-19T18:00:00Z",
    "rotated_at": null
  }
]
```
This is the endpoint the Studio "Invoke access" grant picker (design doc §9.4) calls, scoped to the **artifact's own owning team** (`research.md` §7 — the picker never queries a different team than the one the agent/workflow it's configuring belongs to).

---

## `POST /api/v1/teams/{team}/applications/{id}/rotate-secret`

Rotate — the reuse win the whole design is for: one action, every `invoker` grant this application holds (across however many artifacts) requires the new secret on the very next request.

**200 response** (`ApplicationRotateSecretResponse` — the second and only other shape ever carrying a secret):
```json
{
  "id": "b6c1e2a4-9e3f-4c1a-9b0a-7f2d8e1c4a5b",
  "secret": "whsec_NewSecretValue987654321...",
  "rotated_at": "2026-07-19T19:30:00Z"
}
```
**Errors:** `401`/`403` as above; `404` `{id}` not found under `{team}`.

---

## `PATCH /api/v1/teams/{team}/applications/{id}`

Kill switch. `{ "enabled": bool }` (`ApplicationUpdate`). `enabled=false` denies this application on **every** artifact it holds `invoker` on, simultaneously, on the very next gateway request — independent of and orthogonal to revoking any one `artifact_role_grants` row (design doc §6, §9.7 Flow E). `enabled=true` re-enables without needing to re-grant anything that was never revoked.

**200 response:** `ApplicationResponse` (updated `enabled` value; `rotated_at`/`created_at` unchanged).

---

## `DELETE /api/v1/teams/{team}/applications/{id}`

Hard delete — cascades grants. Because `artifact_role_grants.grantee_id` is a polymorphic `TEXT` column (no FK is possible to a specific table), the cascade is explicit application code, not a DB `ON DELETE CASCADE`: within the same transaction as the `applications` row delete, the handler issues `DELETE FROM artifact_role_grants WHERE grantee_type = 'application' AND grantee_id = :id` (hard delete, matching the application's own hard-delete semantics — not a soft-revoke, since soft-revoking a grant that points at an application id which no longer exists would leave a permanently dangling, unresolvable row).

**204**, empty body. **Errors:** `401`/`403` as above; `404` `{id}` not found under `{team}`.

---

## Example — Flow A + Flow B from the design doc, concretely

```
# Priya (contributor, payments team) creates billing-service
POST /api/v1/teams/payments/applications
Authorization: Bearer <priya's token>
{ "name": "billing-service" }
→ 201 { id: APP_ID, name: "billing-service", secret: "whsec_...", created_at: ... }

# Amit (agent-admin on invoice-processor, also payments team) lists applications
# he can grant from — scoped to invoice-processor's OWN team, "payments"
GET /api/v1/teams/payments/applications
→ 200 [ { id: APP_ID, name: "billing-service", enabled: true, ... } ]

# Amit grants billing-service invoker on invoice-processor (contracts/artifact-grants.md)
POST /api/v1/artifacts/agent/{invoice_processor_id}/grants
{ "grantee_type": "application", "grantee_id": "APP_ID", "role": "invoker" }
→ 201

# Frank (contributor, payments team, NOT agent-admin on invoice-processor)
POST /api/v1/artifacts/agent/{invoice_processor_id}/grants   (as Frank)
{ "grantee_type": "application", "grantee_id": "APP_ID", "role": "invoker" }
→ 403   (blocked — matches design doc §9.9 step 6)

POST /api/v1/teams/payments/applications   (Frank creating his OWN application)
{ "name": "frank-test-app" }
→ 201   (team-level action, allowed — Frank is contributor+ in payments)
```
