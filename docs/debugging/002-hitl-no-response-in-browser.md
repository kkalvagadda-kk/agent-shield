# 002 — HITL approval_requested never reaches browser

## Symptom

User chats with agent that has a high-risk tool. Agent pauses for HITL approval (interrupt fires), but browser shows nothing — no text, no error, no approval banner. Just a loading spinner.

**Variant**: On cold pod start (first 5 min), the tool call fails with "authentication issue" instead of triggering HITL at all.

## Root Causes

### Root Cause 1: Stale nginx subchart tgz

**Where**: `charts/agentshield/charts/studio-0.1.1.tgz` (Helm subchart package)

**Problem**: The tgz was built Jul 7 and didn't include the SSE-specific nginx location block added later to `charts/studio/templates/configmap.yaml`. Helm uses the tgz over the unpacked directory when both exist. Result: all SSE streams go through the generic `/api/` location with default nginx buffering ON. Events sit in the buffer and never reach the browser.

**Fix**: Delete stale tgz → `helm package` from the directory → redeploy. The configmap.yaml has an SSE-specific block:
```nginx
location ~ ^/api/v1/playground/runs/.*/(stream|resume-stream)$ {
    proxy_buffering off;
    proxy_cache off;
    ...
}
```

### Root Cause 2: OPA sidecar has no readiness probe

**Where**: `services/deploy-controller/manifest_builder.py` OPA container spec

**Problem**: Pod is marked Ready when agent container passes readiness (~20s after start), but OPA sidecar takes ~5 min to download and load its bundle. During that window, OPA returns empty `{"result": {}}`, SDK fail-closes (`allow=False`), LLM sees "Tool denied by policy" and tells user it has "authentication issues."

**Fix**: Add readiness probe using `/health?bundles` — OPA returns 200 only when all bundles are loaded. Pod stays not-Ready until OPA is fully operational.

## Investigation Steps

1. **Direct agent pod call** (`kubectl exec` → `POST localhost:8080/chat/stream`) → `approval_requested` emitted correctly. Proved SDK/OPA chain works.
2. **OPA logs**: Zero `/v1/data/agentshield` eval calls during user's chat, only health checks. Bundle loaded at `17:43:37Z` (~5 min after start).
3. **OPA direct query** from agent pod → correct `require_approval: true` response after bundle loaded.
4. **Container env check**: `AGENTSHIELD_OPA_URL=http://localhost:8181` set, `DEV_MODE=False`.
5. **Deployed nginx ConfigMap** vs chart template → missing SSE block in deployed version.
6. **Subchart tgz** vs directory → tgz stale (Jul 7), directory updated (Jul 10).

## Key Insight

E2e tests ran inside the registry-api pod (bypassing nginx), so they passed. The browser path goes through nginx, where buffering blocked events. Always test the full browser path for SSE features.

## Image Tags

- deploy-controller: 0.1.30 → 0.1.31 (OPA readiness probe)
- studio: 0.1.106 (nginx configmap repackaged, no image change)

## Files Changed

- `services/deploy-controller/manifest_builder.py` — OPA readiness + liveness probes
- `charts/agentshield/charts/studio-0.1.1.tgz` — repackaged with SSE nginx block
- `scripts/deploy-cpe2e.sh` — DEPLOY_CONTROLLER_TAG bump
- `charts/agentshield/values.yaml` — mirror tag

## Follow-up (2026-07-10): the ACTUAL browser bug was a missing UI handler

Root Causes 1 & 2 above (nginx SSE buffering, OPA readiness) were real infra
issues and were fixed. But they were **not** why the user saw nothing in the
browser for the deployed-agent chat. The real bug only surfaced by driving the
**actual page**, not the API:

**Root Cause 3: `AgentChatPage` never handled `approval_requested`.** The
deployment chat page (`/agents/{name}/d/{depId}/chat`) only handled `token` and
`done` SSE events — the `approval_requested` event arrived and was silently
dropped, so the spinner span forever. (`CatalogChatPage` handled it; `AgentChatPage`
did not — two chat surfaces, one wired.)

**Why it slipped through:** every test to that point ran via `kubectl exec` +
httpx with `X-User-Sub` headers — bypassing the browser, the real Keycloak JWT,
EventSource, and every React handler. The backend was provably correct the whole
time; the defect was 100% in the un-exercised UI layer.

**Fix (see design §8b):** `AgentChatPage` now shows a waiting banner on
`approval_requested`, polls `GET /agents/{name}/chat/{run_id}/approval-status`
(requester-scoped), and auto-resumes when a reviewer decides in the `/hitl`
console — mirroring the design's console-approval model. Proven by
`studio/e2e/hitl-deployment-chat.spec.ts` (real browser: send → waiting banner →
approve in console → chat auto-resumes → reload asserts persistence).

**Also fixed the Playwright harness:** it now runs against the https gateway
(`scripts/studio-e2e.sh` gateway mode + `ignoreHTTPSErrors`), because Keycloak's
`Secure` session cookies don't replay over the http port-forward (SSO breaks).

## Generalized Principles

1. **Helm subchart tgz wins over directory** — after editing subchart templates, always repackage the tgz
2. **SSE needs explicit nginx unbuffering** — `proxy_buffering off` must be set for any SSE endpoint
3. **Sidecar readiness matters** — if a sidecar provides critical functionality, gate pod readiness on it
4. **Test the browser path, not just the API** — `kubectl exec` API tests bypass nginx, JWT auth, and the entire React layer. A "green" API test proves the backend, not the feature. The missing `approval_requested` handler was invisible until a Playwright spec clicked the actual button.
5. **When SSE/auth "works in API tests but not the browser," suspect the UI event handler and the auth cookie/JWT path first** — those are exactly what `kubectl exec` can't see.
