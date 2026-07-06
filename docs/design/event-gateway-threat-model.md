# Event Gateway — Threat Model

**Status:** Draft — prerequisite for Phase 9 implementation (T134). Must be reviewed/accepted before `services/event-gateway/` is built.
**Scope:** The public webhook ingress for event-driven agents: `POST /hooks/{agent_name}/{token}` → token validation → rate limit → replay check → filter evaluation → dispatch to `POST /api/v1/internal/runs/start`.
**Owner:** Platform / security.
**Related:** `docs/design/execution-modes-production.md` (§ Event Gateway flow), `docs/plan/execution-modes-tasks.md` (Phase 9, T135–T153), Decision 23 (alerting), `todo-envoy-gateway-edge` (the *separate* outbound edge — not in scope here).

> This is the **highest-attack-surface component in the platform**: the only service that accepts unauthenticated traffic from the public internet and turns it into agent execution. Everything else sits behind Keycloak JWT or is cluster-internal. Treat every field of the request as hostile.

---

## 1. System overview & trust boundaries

```
                 ┌─────────────────────── UNTRUSTED (public internet) ───────────────────────┐
                 │  Any external system: SaaS webhooks, cron pingers, attackers, scanners      │
                 └───────────────────────────────────┬────────────────────────────────────────┘
                                                      │ HTTPS  POST /hooks/{agent}/{token}
                                                      ▼
   ── TRUST BOUNDARY 1: public edge (Ingress + TLS) ──────────────────────────────────────────
                                                      │
                                                      ▼
                        ┌─────────────────── event-gateway (DMZ tier) ───────────────────┐
                        │ 1. token: sha256(token) == agent_triggers.token_hash            │
                        │ 2. rate limit: per-agent + per-source-IP sliding window (Redis) │
                        │ 3. replay: X-Webhook-Timestamp / X-Webhook-Nonce                │
                        │ 4. filter: filter_engine against payload                        │
                        │ 5. persist agent_events (matched|filtered|rejected)             │
                        └───────────────────────────────┬─────────────────────────────────┘
                                                         │ POST /api/v1/internal/runs/start
   ── TRUST BOUNDARY 2: cluster-internal (NetworkPolicy) ─────────────────────────────────────
                                                         ▼
                        registry-api  /api/v1/internal/runs/start  (⚠ currently UNAUTHENTICATED)
                                                         │
                                                         ▼
                        agent pod  {agent}-production.{ns}:8080/chat   (untrusted payload enters agent context)
```

**Assets to protect**
- **Agent execution / compute** — arbitrary webhook → run is the whole point; abuse = cost + downstream tool calls.
- **The internal dispatch endpoint** — `/api/v1/internal/runs/start` is unauthenticated and trusts its caller. The gateway is effectively a privilege boundary in front of it.
- **Tenant isolation** — a token for agent A must never start agent B; one tenant must not exhaust another's capacity.
- **Webhook tokens** — bearer secrets; leak = impersonation until rotated.
- **Downstream tools/data** — the agent may call governed tools (OPA/HITL still apply per-tool), but the *trigger* itself is only as trusted as this gateway.

**Trusted vs untrusted**
- Untrusted: request path, headers, body, `token` value, claimed `agent_name`, `X-Forwarded-For`.
- Trusted (with caveats): `filter_conditions` and `token_hash` (authored by the agent owner via authenticated Studio/registry-api — semi-trusted, but a compromised/careless owner is in scope for ReDoS/logic).

---

## 2. STRIDE summary

| STRIDE | Primary threat here | Verdict |
|--------|---------------------|---------|
| **S**poofing | Forged/guessed token; spoofed source IP; agent enumeration | Mitigated by token hash + uniform 401 + trusted proxy header |
| **T**ampering | Payload mutation, header injection, `X-Forwarded-For` spoof | Mitigated: payload treated as untrusted end-to-end; source IP from trusted hop only |
| **R**epudiation | "I didn't send that webhook" | Mitigated: `agent_events` logs source_ip + received_at + status + payload |
| **I**nformation disclosure | Token in URL leaking via logs/proxies; enumeration; error verbosity | Partially — see T-1, T-9; requires log hygiene |
| **D**enial of service | Flood, oversized bodies, ReDoS, replay storms | Mitigated by rate limit + body cap + regex guard + replay window |
| **E**levation of privilege | Reaching `/internal` directly; token for A triggers B | Mitigated by NetworkPolicy + scoped token→trigger lookup |

---

## 3. Threats & mitigations (ranked)

Each maps to the implementing task in `execution-modes-tasks.md`.

### T-1 — Token leakage via URL path  · Severity: High
`POST /hooks/{agent}/{token}` puts the bearer secret in the **URL path**, which leaks into ingress/proxy access logs, `Referer` headers, browser history, and APM traces.
**Mitigations:**
- Ingress + gateway access logs MUST NOT log the `{token}` path segment or query string. Log the **agent_name and `token_hash` prefix only**, never the plaintext token (T135/T145).
- Accept an alternative `Authorization: Bearer <token>` / `X-Webhook-Token` header and prefer it when present (documented, header path avoids URL logging). URL form retained for SaaS senders that only support a URL.
- Rotation (T143) is the containment control: leaked token → rotate → old hash invalid immediately.
- **Residual risk:** senders that can only use a URL. Accepted, with log hygiene as the compensating control.

### T-2 — Weak/forged token (spoofing)  · Severity: High
Attacker guesses or brute-forces a token, or exploits timing in comparison.
**Mitigations:**
- Tokens are **≥256 bits of CSPRNG** (`secrets.token_urlsafe(32)`), generated server-side only (T143). Never client-chosen.
- Store `sha256(token)` (hex, ≤128 chars — fits `token_hash`); compare with **`hmac.compare_digest`** (constant-time) against the hash, not the raw token (T136).
- Lookup is scoped: `agent_triggers WHERE agent_name=? AND enabled=true` then constant-time hash check — a token only ever matches its own trigger (see T-6).
- Brute-force is bounded by rate limiting (T-4) applied **before** run dispatch and counted even on 401s.

### T-3 — Replay attacks  · Severity: High
A captured valid request is resent to re-trigger runs (cost, duplicate side-effects).
**Mitigations (T139):**
- `X-Webhook-Timestamp` required when the sender supports it: reject if skew > **5 min** (clock-tolerant window).
- `X-Webhook-Nonce`: store in Redis `SET nonce:{agent}:{nonce}` with **1 h TTL**; duplicate ⇒ reject 409.
- Where the sender signs (e.g. GitHub/Stripe HMAC), prefer verifying the provider signature over the raw body; timestamp+nonce is the generic fallback.
- **Residual risk:** senders with neither timestamp nor nonce get at-least-once semantics. Documented; agent logic for such triggers should be idempotent (call out in Studio when configuring a webhook trigger).

### T-4 — Volumetric DoS / abuse  · Severity: High
Flood of requests (valid or 401) exhausts gateway, Redis, registry-api, or agent compute/LLM budget.
**Mitigations (T137 + T145):**
- **Two-dimensional** Redis sliding-window rate limit (T134 requirement): `ratelimit:agent:{name}` **and** `ratelimit:ip:{source_ip}`, 60 s window, configurable caps (default 100/agent, lower per-IP). 429 with `Retry-After`.
- Rate limit is evaluated **before** token validation cost and **before** dispatch, and increments on **rejected** requests too (else 401 brute-force is unmetered).
- Ingress-level connection/rate annotations (T145) as the first backstop; per-agent app limit as the authoritative one.
- **Body size cap** (see T-5). Redis failure ⇒ **fail-closed** (throttle) rather than allowing unbounded pass-through.
- **DDoS surface note:** the `/hooks/*` ingress is internet-facing. Production should front it with a **CDN/WAF** (rate rules, IP reputation, L7 challenge). This is an operational deployment control, documented here; not a code task. Local/dev has no CDN — the app-level limiter is the only defense there.

### T-5 — Oversized / malformed payload  · Severity: Medium
Huge JSON body or deeply nested structure exhausts memory/CPU during parse or filter eval.
**Mitigations:**
- Enforce a **max request body** (default 256 KiB, configurable) at both Ingress and gateway; 413 over cap (T135).
- Reject non-JSON / unparseable bodies with 400 before filter eval.
- Bound JSON nesting depth during `_resolve_field` traversal.

### T-6 — Cross-agent / cross-tenant trigger (elevation)  · Severity: High
Token for agent A used on `/hooks/B/...`, or filter/lookup confusion starts the wrong agent.
**Mitigations (T136):**
- Token lookup is **keyed by both** `agent_name` (from path) **and** `sha256(token)`; the row must match both. A valid A-token on B's path finds no matching enabled trigger ⇒ 401.
- Dispatch uses the resolved trigger's own `agent_name`, never a client-supplied one.

### T-7 — ReDoS via `regex` filter operator  · Severity: Medium
`filter_engine` supports `regex`. A catastrophic-backtracking pattern (in owner-authored `filter_conditions`) evaluated against attacker-controlled payload can hang a worker (CPU DoS).
**Mitigations (T138):**
- Evaluate `regex` rules under a **hard time budget** (e.g. run filter eval in a thread with a timeout, or adopt `google-re2` for linear-time matching). On timeout ⇒ treat as **not matched**, log `filtered: regex-timeout` (fail-safe: no run).
- Validate/parse `filter_conditions` at trigger-create time (reject unparseable regex early).
- Since payload is attacker-controlled, this is a real remote CPU-DoS even though the pattern is owner-authored — do not skip.

### T-8 — Reaching `/api/v1/internal/runs/start` directly (elevation)  · Severity: Critical
The internal dispatch endpoint is **currently unauthenticated** and trusts its caller. If it were publicly routable, an attacker bypasses the entire gateway (no token, no rate limit).
**Mitigations:**
- **NetworkPolicy**: `/api/v1/internal/*` reachable only from in-cluster pods (event-gateway, scheduler); the public Ingress exposes **only** `/hooks/*` on the gateway and the platform API ingress must not route `/api/v1/internal/*` (T145 + ingress config).
- **Decision (2026-07-05): NetworkPolicy only for launch.** `/api/v1/internal/*` is restricted to in-cluster pods via NetworkPolicy (T145) and kept off the public ingress; this is the Phase 9 control.
- Defense-in-depth **(tracked as a future improvement, not in Phase 9):** require a shared internal token / mTLS between gateway↔registry-api for `/internal` calls so a rogue in-namespace pod can't dispatch freely. See spec §Future Improvements ("internal-auth on /internal").

### T-9 — Agent enumeration & error verbosity (info disclosure)  · Severity: Low
Distinct responses for "unknown agent" vs "bad token" let an attacker enumerate agent names.
**Mitigations (T136):**
- Return a **uniform 401** for unknown agent, disabled trigger, and bad token alike. No "agent not found" vs "invalid token" distinction on the public path.
- Generic error bodies; details go to logs (with token redacted), not the response.

### T-10 — Unsanitized payload enters agent context  · Severity: Medium (High if safety-orchestrator stays off)
Untrusted webhook content becomes agent input → prompt injection, jailbreak, tool-abuse attempts inside the run.
**Mitigations:**
- **Design intent:** payload passes through the **Safety Orchestrator** (input scan: PII/injection/guardrails) before entering agent context.
- **⚠ Current reality:** `safety-orchestrator.enabled: false` in the local composition, so this pass-through is **absent today** — webhook payloads would reach the agent unsanitized. This is a **known gap**, shared with the deferred `todo-envoy-gateway-edge` work.
- **Interim controls:** per-tool OPA/HITL governance still wraps every tool call the agent makes (blast radius of a poisoned prompt is bounded by tool governance, not the trigger). Body-size + JSON-only + filter matching reduce the raw surface.
- **Decision (2026-07-05): option (b) — ship now + rely on tool governance.** Phase 9 launches webhook triggers with a documented "external input is NOT input-scanned" warning surfaced in Studio when configuring a webhook trigger; per-tool OPA/HITL governance remains the blast-radius control. **When the Safety Orchestrator is (re)implemented, wiring webhook payloads through its input scan for event-driven agents is added to that task's scope** (see spec §Safety Orchestrator responsibilities + `todo-envoy-gateway-edge`). Until then this is an accepted residual risk (R-5).

### T-11 — Spoofed source IP defeats per-IP limiting/attribution  · Severity: Medium
`X-Forwarded-For` is attacker-settable; naive parsing lets one attacker forge many "IPs" to dodge per-IP limits and poison `agent_events.source_ip`.
**Mitigations:**
- Derive source IP **only** from the trusted hop (the Ingress/CDN-set rightmost XFF entry or the connecting peer), per a configured `trusted_proxies` count. Never trust client-supplied XFF blindly (T137/T144).

### T-12 — Sensitive data in event log (info disclosure/compliance)  · Severity: Low/Medium
`agent_events.payload` persists full untrusted bodies (may contain PII/secrets).
**Mitigations:**
- Document retention + access control on `agent_events` (team-scoped read, like runs).
- Consider payload truncation/size cap in storage and PII tokenization consistency with the memory/anonymization rules (align with the existing PII store). Flag for the T140 schema review.

---

## 4. Residual risks accepted for Phase 9 launch

| # | Residual risk | Why accepted / compensating control |
|---|---------------|-------------------------------------|
| R-1 | Token can appear in a sender's URL | Log hygiene (never log token) + rotation; header form offered |
| R-2 | At-least-once delivery for senders with no timestamp/nonce | Documented; advise idempotent agent logic on webhook triggers |
| R-3 | `/internal` protected by NetworkPolicy only (no mTLS/token yet) | In-cluster boundary; internal-auth hardening tracked as follow-up (T-8) |
| R-4 | No CDN/WAF in local/dev | App-level 2D rate limit + body cap; CDN is a prod deployment control |
| R-5 | Payload not input-scanned while safety-orchestrator is disabled | Per-tool OPA/HITL still applies; re-enable safety for medium+ risk agents (T-10) |

---

## 5. Security acceptance criteria (must be covered by suite-28, T150)

- [ ] Bad/absent token ⇒ **401**, uniform for unknown-agent vs bad-token (T-2, T-9).
- [ ] Valid token on the **wrong agent's** path ⇒ 401 (T-6).
- [ ] Exceeding the rate limit ⇒ **429** with `Retry-After`; 401s also count toward the limit (T-4).
- [ ] Replayed request (stale timestamp or reused nonce) ⇒ rejected (T-3).
- [ ] Oversized body ⇒ **413** (T-5).
- [ ] Rotated token: old token rejected, new works (T-3 containment / T143).
- [ ] Filtered (non-matching) event ⇒ **202**, logged `filtered` with reason, **no run** (design invariant).
- [ ] `/api/v1/internal/runs/start` is not reachable through the public `/hooks` ingress (T-8) — verified by ingress/NetworkPolicy config review (manual + smoke test).
- [ ] `agent_events` records `source_ip` from the trusted hop, `status`, `received_at` (T-11, repudiation).

---

## 6. Out of scope (explicitly)

- **Outbound edge / agent chat / multi-agent handoff hardening** — that's the deferred `todo-envoy-gateway-edge` (Envoy + Safety Orchestrator proxy on the *outbound* path). The Event Gateway is **inbound-only** and shares no code path with it (see the "no Envoy dependency" analysis).
- **Automatic token expiry/rotation** — launch is manual rotation (T143); auto-expiry is future work (spec §14).
- **Provider-specific signature verification** (GitHub/Stripe HMAC) — generic timestamp+nonce ships first; per-provider verifiers are a future enhancement.
