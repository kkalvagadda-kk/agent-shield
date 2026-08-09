# MCP `service_identity` mints an unscoped token, not an audienced one

**Found** 2026-08-04 (by inspection + live probe, not by an incident) ·
**Fixed** — not yet; recorded as a design-doc correction and a gap.
**Severity** — latent. Nothing exploitable today (see *Why nothing broke*), but the design
doc asserted a security property that does not hold, which is worse than a known gap.

## Symptom

`docs/design/identity-propagation-architecture.md` §4.8 described `identity_mode="service_identity"`
as delivering "a Keycloak client-credentials token, **audienced to that server**", status **BUILT**.

It does not. Every upstream MCP server would receive the **same unscoped token**.

## Root cause

`services/mcp-proxy/keycloak_client.py:80` builds:

```python
data = {"grant_type": "client_credentials", "client_id": ..., "client_secret": ...}
if audience:
    data["audience"] = audience
```

`audience` is **RFC 8693's** parameter. Keycloak honours it on
`grant_type=urn:ietf:params:oauth:grant-type:token-exchange` — **not** on `client_credentials`,
where it is silently ignored. So this is not token exchange at all; it is a plain service-account
grant carrying a hint the server drops on the floor.

Verified against the live realm on `test-cluster-964-10086`:

| request | HTTP | `aud` in the issued token |
|---|---|---|
| no `audience` param | 200 | `account` |
| `audience=totally-nonexistent-tool-xyz` | 200 | `account` |
| `audience=langfuse` (a **real** client) | 200 | `account` |

Identical every time — including for a *real* client, which rules out "the audience just was not
registered" as the explanation. A consequence worth noting: `_token_cache` is keyed by audience and
is therefore caching **one** token under many keys.

Three separate things are missing, and they compound:

1. **Nothing registers the audience.** `services/registry-api/keycloak_client.py` has only `/users`
   operations — no `create_client`. `charts/agentshield/templates/realm-init-job.yaml` creates a
   fixed four clients at install and never runs again. MCP-server registration never touches
   Keycloak.
2. **`identity_audience` is unvalidated operator free text** — `mcp_secrets.py:118` reads whatever
   was typed into `transport_config`.
3. **The failure is silent.** An unregistered audience yields a 200 and a usable token, so the
   proxy believes it minted a scoped credential. There is no way for the current code to detect the
   difference.

## Impact if it were used

An upstream server can tell "this is the platform" but **not which server the token was minted
for**, and the token replays against any other server that trusts the platform. §4.8.1's three-gate
model assumes a scoped token at gate 3; that assumption is false.

## Why nothing broke

All 13 rows in `mcp_servers` are `identity_mode="none"` with `identity_audience` NULL, so the path
is never taken. And `MCP_PROXY_KEYCLOAK_CLIENT_ID` has no matching client on the realm (it holds
`registry-api`, `envoy-gateway`, `agentshield-studio`, `langfuse` + Keycloak built-ins), so a mint
would fail outright before it could mislead anyone.

## Fix (not yet implemented)

1. MCP-server registration creates or verifies a Keycloak client for the target, plus an
   audience-mapped client scope on the proxy's client. **This step does not exist.**
2. Switch to `grant_type=urn:ietf:params:oauth:grant-type:token-exchange`.
3. **Fail closed** on an unregistered audience rather than returning a generic token.
4. Assert it: a test that mints for a known audience and checks `aud` — the check that would have
   caught this on day one.

## Lesson

The claim was mine, and I made it by reading a function signature — `mint_service_account_token(audience)`
takes an audience, therefore it must scope by audience — instead of testing the behaviour. A
parameter being *accepted* says nothing about it being *honoured*, and OAuth servers ignore
parameters they do not recognise rather than erroring. **For a security property, probe the wire.**
The probe here was three curl calls and it inverted the conclusion.

It also went unnoticed because the code path is unreachable in practice, which is a reminder that
"no incident" is not evidence of correctness for a feature nothing exercises.

## Related

- `docs/design/identity-propagation-architecture.md` §4.8 (corrected in place, 2026-08-04)
- Decision 29 — on-behalf-of impersonation, blocked on the same missing registration step
- OQ-5 — server-side token validation as a contract: a server validating this token sees
  `aud: account` and has no basis to accept or reject it
