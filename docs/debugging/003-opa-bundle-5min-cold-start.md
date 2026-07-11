# 003 — Why OPA bundle takes ~5 min to reflect a new agent on cold start

## Symptom

When a new agent pod starts, its OPA sidecar returns empty `{"result": {}}` /
`agent_unauthenticated` for up to ~5 minutes. During that window tool governance
fails closed (SDK reads empty result → `allow=False`), so the agent tells the
user it has "authentication issues" instead of gating via HITL.

## Key insight

OPA is **not** slow to get *a* bundle — it loads one within the same second it
starts. The delay is the time before the bundle *contains the newly-started
agent's identity*. Until then OPA evaluates against a bundle where the agent's
`sa_subject` is absent.

## Root cause: the bundle only includes agents whose deployment is `status='running'`

`services/registry-api/bundle_generator.py:76-92` gates every agent on an active
identity **and** a running deployment:

```sql
WHERE ai.revoked_at IS NULL
  AND d.status = 'running'
```

Identity is registered early in reconcile (`reconciler.py:214-234`, before the
Deployment is even applied), so identity is not the bottleneck. The
`status='running'` flip is — it happens only after the reconciler polls the K8s
Deployment and sees ≥1 available replica (`reconciler.py:321-335`), which
requires the agent's own app container to pass readiness (image pull + runner
startup + config fetch).

## The chain (each hop is sequential)

| Hop | Mechanism | Where | Time |
|-----|-----------|-------|------|
| 1. pod start → Deployment Ready → DB `status='running'` | reconciler polls availability every 5s (up to 60s) | `reconciler.py:15-16, 321-335` | **~1–4 min** (dominant, variable) |
| 2. `status='running'` → fresh bundle bytes in nginx | `bundle-sync` sidecar `sleep 30`, curls `/api/v1/bundle/bundle.tar.gz`, `mv` into nginx emptyDir | `infra/opa-bundle-server/deployment.yaml:88-101` | up to 30s |
| 3. new bytes → OPA sidecar reload | OPA bundle poll, random delay `min_delay_seconds:30`/`max_delay_seconds:60` | `k8s_client.py:286-288`, `infra/opa-bundle-server/configmap-opa-config.yaml` | up to 60s |

Deterministic floor from the two poll intervals alone: **30 + 60 = ~90s**. Add
the variable pod-ready time and unlucky poll-phase alignment → the observed 3–5 min.

## Live evidence (serper-agent-4-sandbox OPA sidecar)

```
18:17:43  Bundle loaded and activated successfully.   ← OPA has A bundle immediately
18:20:49  Decision → deny_reason="agent_unauthenticated", identity_present=false  ← agent not in bundle
18:21:02  Decision → identity_present=true, tools=[web_search], allow=true         ← first authorized
```

OPA start → first authorized decision = **3m 19s**, entirely because the bundle
didn't carry this agent's `sa_subject` until ~18:21.

## Secondary findings

1. **ETag churn.** registry-api builds the tarball deterministically
   (`routers/bundle.py:108`, `mtime=0`), but `bundle-sync` does `mv` every 30s,
   changing the file mtime → nginx ETag (mtime+size) changes every poll → OPA
   re-downloads the full bundle every time, never a 304. Wasteful, not itself a
   cold-start cause.
2. **60s reconcile poll cliff.** `_POLL_TIMEOUT_SECONDS = 60` (`reconciler.py:15`).
   A cold image pull >60s → reconcile returns `failed` → `status='failed'`, which
   the poll loop doesn't re-pick — the agent never enters the bundle until manually
   redeployed.

The design doc's "~1-2 minutes" (`hitl-approval-system.md:235`) only counts hops
2+3 and ignores hop 1.

## Fix options (ranked)

1. **Lower OPA `min_delay_seconds` 30→5, `max_delay_seconds` 60→15** in *both*
   `k8s_client.py:286-288` and `infra/opa-bundle-server/configmap-opa-config.yaml`
   (else existing ConfigMaps keep the old value). ~45s off hop 3, near-zero risk
   (bundle is ~2 KB).
2. **Shorten `bundle-sync` loop** `sleep 30→5-10` (`deployment.yaml:97`). ~15-25s
   off hop 2; more curls → more live DB regenerations.
3. **Fix ETag churn** — `bundle-sync` only `mv` when bytes differ (hash compare),
   or nginx content-hash ETag. Correctness + stops constant re-downloads once
   polls are faster. Land alongside #1/#2.
4. **Attack hop 1 (biggest real win):** include agents in the bundle as soon as an
   identity is registered against a `deploying` deployment — add
   `status IN ('deploying','running')` to the gate in `bundle_generator.py:92`. The
   agent's identity is known at deploy time; letting the bundle carry it ~1-4 min
   earlier means the OPA sidecar already has the identity by the time the app is
   Ready. Harmless (governance is fail-closed; the agent makes no calls until Ready).

**Recommended:** #1 + #2 for an immediate multi-minute win at near-zero risk, #4
to remove the dominant pod-readiness wait, #3 as the correctness cleanup.

## Note

This is diagnosis only — no code changed. It explains the "5 min auth issue"
window that debugging doc [002](002-hitl-no-response-in-browser.md) worked around
with an OPA readiness probe (which correctly hides the window by keeping the pod
not-Ready until the bundle loads, but doesn't shrink it).
