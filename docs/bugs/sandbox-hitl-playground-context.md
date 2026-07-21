# Bug: sandbox agent HITL hangs — pod hardcoded to production approval context

**Found/Fixed:** 2026-07-20 — fix in `deploy-controller:0.1.40`
(`services/deploy-controller/manifest_builder.py`), branch `webhook-improvements`.

## Symptom

Chatting with a **sandbox** agent in Studio: a tool call parks for HITL approval, the
reviewer approves, and the chat **hangs on an empty response bubble** — it never resumes.
Observed on `serper-agent-5` (sandbox): `web_search` parked, the approval row showed
`status='approved'` (by `playground-user`), yet the chat stayed stuck.

Two oddities pointed at the cause:
- The tool is `risk_level='low'` in the DB, but it **parked as `risk=high`**.
- The run is `context='playground'` (sandbox), but the **approval row was `context='production'`**.

## Root cause

`services/deploy-controller/manifest_builder.py` **hardcoded** the two env vars that decide
a pod's HITL approval context:

```python
k8s_client.V1EnvVar(name="AGENTSHIELD_PLAYGROUND", value="false"),
k8s_client.V1EnvVar(name="AGENTSHIELD_SANDBOX",    value="false"),
```

The SDK (`agentshield_sdk/hitl.py::require_approval`) reads them:

```python
is_playground = os.getenv("AGENTSHIELD_PLAYGROUND", "false").lower() == "true"
context = "playground" if is_playground else "production"
```

So **every** agent pod — including sandbox ones — reported `context="production"`. Two
consequences, both of which reproduce the symptom:

1. **OPA was stricter.** In production context OPA required approval for the tool call
   (parked it), where in playground context it would have run inline / lightweight —
   that's why a DB-`low` tool parked as `high`.
2. **The approval routed to the wrong surface.** A `production`-context approval goes to
   the **reviewer console**, not the inline sandbox chat panel. The sandbox chat's
   approve/resume poll is keyed to `playground`-context approvals, so even after the
   approval was granted, the chat could never see it resolve → it hung forever.

`manifest_builder` already knew the deployment's `environment` (it names the Deployment
`{agent}-{environment}` and labels it) — the values were just never derived from it. The
inline comment even said "false for production deployments," but the code hardcoded false
for all.

## Fix

Derive the two env vars from the deployment's own `environment` (explicit, not hardcoded):

```python
V1EnvVar(name="AGENTSHIELD_PLAYGROUND", value="true" if environment != "production" else "false")
V1EnvVar(name="AGENTSHIELD_SANDBOX",    value="true" if environment == "sandbox"     else "false")
```

- **sandbox** ⇒ `playground` context ⇒ inline approval in the sandbox chat, auto-resume on
  approve.
- **production** ⇒ unchanged (`false`) ⇒ reviewer console.

Both reconcilers already pass the right `environment` (sandbox reconciler → `"sandbox"`,
`production_reconciler` → `"production"`), so the one derivation serves both paths and
can't drift.

## Image Tags

- deploy-controller `0.1.39` → **`0.1.40`** (the env is baked into the agent pod manifest
  the controller writes).

## Deploy / rollout note

The env is stamped at **agent-pod creation**. After deploying `0.1.40`, **existing sandbox
agents keep the old `false` env until redeployed** — redeploy the agent (Studio → Redeploy,
or delete the sandbox Deployment so the reconciler recreates it) to pick up
`AGENTSHIELD_PLAYGROUND=true`. New deploys get it automatically.

## Files Changed

- `services/deploy-controller/manifest_builder.py` — derive `AGENTSHIELD_PLAYGROUND` /
  `AGENTSHIELD_SANDBOX` from `environment`.
- `charts/agentshield/values.yaml`, `scripts/deploy-eks.sh`, `scripts/deploy-cpe2e.sh` —
  deploy-controller tag `0.1.39` → `0.1.40`.

## Lessons

- **A hardcoded "false" that should have been a derived value is a silent policy bug.**
  The controller had `environment` in hand the whole time; the two env vars just weren't
  wired to it. When a value drives governance (which approval surface, which OPA context),
  derive it from the explicit source — never a literal.
- **HITL context is set at deploy time, not chat time.** So this can only be fixed by a
  controller change + an agent redeploy — a registry-api or SDK change alone can't reach an
  already-running agent pod's env.
- **Cross-check "risk in the DB" vs "risk at the approval."** The low-vs-high mismatch was
  the thread that unravelled it — the elevation came from the wrong *context*, not a wrong
  risk on the tool.
