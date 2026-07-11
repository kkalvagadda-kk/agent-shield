# Plan (for review) — HITL golden-path browser e2e (sandbox + production)

**Status:** proposal — please review before I implement.
**Why:** the retro (`docs/introspections/2026-07-11-...`) concluded we lacked a test that
drives the *real* journey through the *real* seams and asserts the *UX end state*. Our
existing specs assert wiring/layers and `test.skip` when a pod is absent — they'd have
missed every bug this week. This plan is that missing test: a browser-driven, full-journey
HITL golden path for **both** environments that asserts the UX renders every expected field
and behaves as expected, and **fails** (not skips) when the journey is broken.

---

## 1. Scope

Two Playwright specs, each driving the complete HITL journey end-to-end through the browser
against the deployed Studio (https gateway), using the real `serper-agent-4` + `web_search`
(risk=high) fixture that actually triggers a HITL pause:

- **`hitl-golden-sandbox.spec.ts`** — sandbox deployment chat (self-approve).
- **`hitl-golden-production.spec.ts`** — production consumer chat + reviewer console (two tabs).

**Out of scope (call out if you want them):** the Evaluate-tab `HitlPanel` surface (a third,
distinct sandbox surface) and the batch-eval auto-approve path. I'd add the Evaluate-tab one
as a follow-up if you want all three sandbox surfaces covered.

---

## 2. Preconditions & fixtures

- Runs via `bash scripts/studio-e2e.sh e2e/hitl-golden-*.spec.ts` against the deployed Studio
  at `https://agentshield.127.0.0.1.nip.io:8443`, logged in as `platform-admin` (existing
  `global-setup.ts` storageState).
- Requires `serper-agent-4` **deployed and running in both sandbox and production** with the
  high-risk `web_search` tool — the same fixture the current HITL spec + suite-45 use.
- **Deterministic tool call:** a *forcing* user message ("Use the `web_search` tool to find
  the weather in Austin tomorrow. You must call the tool.") — a soft prompt sometimes makes
  the model answer without the tool; the forcing prompt reliably triggers `web_search`.
- **Precondition = hard failure, not skip (design decision — see §8).** Unlike the existing
  wiring-only specs, the golden path treats "agent not reachable / not deployed" as a
  **failure**, because a working deployment is *part of* the journey we're guarding. A
  `beforeAll` resolves the current running sandbox deployment id + production artifact id via
  the API and fails with a clear message if either is missing (so an injected bug that breaks
  deployment surfaces as a failure, not a silent skip).

---

## 3. Step 0 — Prerequisite instrumentation (small app change)

The reviewer console (`HITLDashboardPage`), the consumer banner (`CatalogChatPage`), and the
Evaluate `HitlPanel` currently have **no `data-testid`s**, so precise field-level assertions
would be brittle text matches. I'll add stable testids (render-only, no behavior change):

| Surface | testids to add |
|---|---|
| `HITLDashboardPage.tsx` (each row + cells) | `hitl-row` (the `<tr>`), and per-cell: `hitl-cell-agent`, `hitl-cell-requested-by`, `hitl-cell-team`, `hitl-cell-deployment`, `hitl-cell-env`, `hitl-cell-tool`, `hitl-cell-reasoning`, `hitl-cell-risk`, `hitl-cell-context`; buttons `hitl-approve`, `hitl-confirm-approve` |
| `CatalogChatPage.tsx` (banner) | `consumer-approval-banner`, `consumer-approval-tool`, `consumer-resume-now` |

(Sandbox panel + production waiting banner already have testids — reused as-is.) Each new
testid gets a matching Vitest assertion so the instrumentation itself is covered.

---

## 4. Sandbox golden path — steps & assertions

Spec: `hitl-golden-sandbox.spec.ts`. Surface: `AgentChatPage` at
`/agents/serper-agent-4/d/<running-sandbox-dep-id>/chat` (sandbox env → self-approve panel).

| # | Action | Assertions (UX + network) |
|---|---|---|
| S1 | `beforeAll`: resolve running sandbox deployment id via API | fail with a clear message if none running |
| S2 | Open the sandbox deployment chat; wait for message input | input visible within 15s (else **fail**, per §2) |
| S3 | Send the forcing message | `waitForResponse` on `POST /agents/{n}/deployments/{dep}/chat` → **200** |
| S4 | Agent calls `web_search` → HITL pause | `sandbox-approval-panel` visible (30s); header `Approvals (1)`; `sandbox` badge present |
| S5 | **Assert WHO/WHY/WHAT on the panel** | `sandbox-approval-row` contains `web_search`; `high risk` badge; **WHO** = `Requested by platform-admin · platform`; **WHY** `sandbox-approval-reasoning` visible + **non-empty**; **WHAT** args `<pre>` present + contains the query (e.g. `weather`/`Austin`) |
| S6 | **Negative UX checks** | no `hitl-waiting-banner` (that's the production surface); input placeholder = `Awaiting tool approval…`; no error text in the transcript |
| S7 | Click `Approve` | `waitForResponse` on `POST /playground/approvals/{id}/decide` → **200** |
| S8 | Chat auto-resumes | `sandbox-approval-panel` hidden (30s); assistant bubble receives resumed content (non-empty final assistant message); input re-enabled |
| S9 | Isolation check | open `/hitl` in a new tab → the just-approved **sandbox** run is **absent** from the production queue (context routing correct) |

---

## 5. Production golden path — steps & assertions

Spec: `hitl-golden-production.spec.ts`. **Two separate browser sessions** — a *consumer*
session (the chat) and a *reviewer* session (the `/hitl` console). This is deliberate, and
it reflects a real constraint you flagged: chat and approval are **different consoles**, and
the consumer's conversation is **in-memory (not persisted)** — so:

- The **consumer session is kept open and never navigated** for the entire flow. Navigating
  it (or losing that session) would drop the conversation — so the test must not do that.
- The approval happens in a **separate browser context** (`browser.newContext()`), i.e. a
  genuinely different session/console — not a second tab sharing the consumer's session.
- After approval elsewhere, we assert the still-alive consumer session **auto-resumes with
  its conversation context intact** (the original user turn is still in the transcript and
  the resumed answer continues *that* conversation). That both proves auto-resume and pins
  down the "don't lose context if you stay in-session" behavior.

**Approve (golden) path:**

| # | Session | Action | Assertions (UX + network) |
|---|---|---|---|
| P1 | — | `beforeAll`: resolve serper-agent-4 production artifact id via API | fail clearly if no running production deployment |
| P2 | Consumer | Open `/catalog/<artifactId>/chat`; header shows `Production` badge | badge present |
| P3 | Consumer | Send the forcing message | `waitForResponse` on `POST /agents/{n}/chat` (context=production) → **200** |
| P4 | Consumer | Agent calls `web_search` → pause | `consumer-approval-banner` visible; `consumer-approval-tool` = `web_search`; text "resumes automatically once approved"; spinner "Waiting…"; `HITL Dashboard` link → `/hitl`. **Record the original user turn text** for the context-preservation check. |
| P5 | **Reviewer (separate context)** | In a fresh `browser.newContext()`, open `/hitl`; filter = Pending | heading **`Production HITL Queue`** (distinguishes from `/admin/publish-requests`); info banner "Showing production approvals only…" present |
| P6 | Reviewer | **Assert the row shows every field** (`hitl-row` for serper-agent-4/web_search) | **WHO** `hitl-cell-requested-by` = `platform-admin`, `hitl-cell-team` = `platform`; **WHERE** `hitl-cell-deployment` non-empty + `hitl-cell-env` = `production`; **WHAT** `hitl-cell-tool` = `web_search`; **WHY** `hitl-cell-reasoning` non-empty; risk `hitl-cell-risk` = `high`; `hitl-cell-context` = `production` |
| P7 | Reviewer | **Negative UX checks** | no `—` placeholders in the WHO/deployment cells (provenance actually populated); not the empty-state "No approvals in this queue"; the row is **not** duplicated |
| P8 | Reviewer | Click `hitl-approve` → `hitl-confirm-approve` | `waitForResponse` on `PATCH /approvals/{id}` → **200**; row leaves the pending filter |
| P9 | Consumer (still open, untouched) | **Auto-resume (no click)** | within ~1 poll (≤5s) the `consumer-approval-banner` disappears **without** touching `consumer-resume-now`; assistant bubble receives resumed content (final non-empty answer) |
| P10 | Consumer | **Conversation context preserved** | the transcript still contains the **original user turn** from P4 above the resumed answer — i.e. staying in-session kept the context; nothing was reset |

---

## 5b. Rejection (negative) path — steps & assertions

A second test in **each** spec that drives the *deny* branch — proving the flow degrades
correctly (agent answers without the tool) instead of hanging, and shows the right copy.

**Sandbox (deny in the self-approve panel):**

| # | Action | Assertions |
|---|---|---|
| SR1–SR4 | Same as S1–S4: open sandbox chat, send forcing message, panel appears with WHO/WHY/WHAT | (as §4) |
| SR5 | Click **`Deny`** on the panel row | `waitForResponse` on `POST /playground/approvals/{id}/decide` (decision=rejected) → **200** |
| SR6 | Chat continues without the tool | panel hidden; assistant bubble shows the denial copy **"You denied the tool call. Responding without it…"**; the run **completes** (does not hang / no perpetual spinner); input re-enabled |

**Production (reject in the reviewer console, separate session):**

| # | Session | Action | Assertions |
|---|---|---|---|
| PR1–PR6 | — / Consumer / Reviewer | Same as P1–P6: chat → banner → console row with all fields | (as §5) |
| PR7 | Reviewer | Click **`Deny`** → **`Confirm Deny`** | `waitForResponse` on `PATCH /approvals/{id}` (decision=rejected) → **200** |
| PR8 | Consumer (still open) | Auto-resume after a **rejection** | banner disappears without a click; assistant bubble shows **"Tool request was denied by a reviewer. Responding without it…"**; the conversation still shows the original user turn; run completes, no hang |

This is the explicit negative test you asked for — it verifies both surfaces handle *denied*
as a first-class outcome (correct copy + graceful completion), not just the happy path.

## 6. UX-completeness checklist (the "nothing missing / nothing unexpected" bar)

Explicitly asserted across the two specs so a regression in *any* of these fails the test:

- **WHO** present and correct on every surface: requester username **and** team (not a raw
  UUID, not `—`).
- **WHY** present and non-empty (the LLM reasoning) on panel + console.
- **WHAT** present: tool name **and** args (not "unknown", args not empty).
- **WHERE/context**: production console shows deployment name + `production` env badge +
  `production` context; sandbox run does **not** appear in the production console.
- **Risk**: `high` badge shown.
- **Behavior (approve)**: sandbox self-approve resumes on click; production auto-resumes
  *without* a click.
- **Behavior (deny — first-class, §5b)**: sandbox shows "You denied the tool call. Responding
  without it…"; production shows "Tool request was denied by a reviewer. Responding without
  it…"; both **complete without hanging**.
- **Conversation continuity**: the consumer session is kept open the whole flow; after resume
  the original user turn is still in the transcript (staying in-session preserves the
  in-memory context — the boundary of the not-yet-persisted gap).
- **Right screen**: `/hitl` is the "Production HITL Queue", never confused with publish
  requests. Approval is driven from a **separate browser session** from the chat.

---

## 7. Bug-catchability matrix (why an injected bug gets caught)

| If a bug breaks… | Which assertion fails |
|---|---|
| LLM reasoning capture (`_extract_reasoning` / nudge) | S5 / P6 WHY (reasoning empty) |
| Requester provenance (`_load_provenance`, username/team) | S5 WHO / P6 WHO + P7 (`—` placeholder) |
| Approval record creation (e.g. empty `agent_id` → 422, doc 009) | P6 (row never appears) / P4 (banner stuck) |
| OPA identity / bundle (doc 008) | S3/P3 tool denied → no pause → S4/P4 never appears |
| Tool credential envFrom (doc 007) | tool errors after approve → S8/P9 no resumed answer |
| Context derivation (`_derive_context`) | S9 (sandbox leaks into prod queue) / P6 context badge |
| Auto-resume poll (CatalogChatPage) | P9 (banner never clears without a click) |
| Deployment/chat routing (doc 006) | S3/P3 non-200 / precondition fail |
| Reject handling (denial copy / hangs instead of completing) | SR6 / PR8 (wrong or missing copy; run never completes) |
| Resume after a rejection not wired | PR8 (banner never clears after Confirm Deny) |
| Conversation lost while waiting (in-memory context dropped) | P10 (original user turn missing after resume) |
| Any surface dropping a field | the specific field assertion in §6 |

This is the design intent: the injected bug you plan to add should map to one of these rows.

---

## 8. Design decisions I want your sign-off on

1. **Fail vs skip on missing deployment.** I propose the golden path **fails** (not `test.skip`)
   when serper-agent-4 isn't reachable, so infra-breaking bugs aren't hidden. (The existing
   specs skip; this one intentionally doesn't.) OK?
2. **Reviewer session & identity.** The reviewer approves from a **separate browser context**
   (a distinct session), never a tab that shares the consumer's session — so the consumer's
   in-memory conversation is preserved and we model the real "different consoles" setup. For
   v1 that separate context reuses the `platform-admin` storageState (same user, separate
   session — enough to prove the cross-session flow). Fuller alternative: log the separate
   context in as the seeded `agent-reviewer` user (needs its password from the
   `keycloak-user-passwords` secret). I propose **separate context, platform-admin**, for v1.
   OK, or do you want the true second user?
3. **Sandbox surface choice.** I propose the **deployment-chat** self-approve panel (direct
   structural analog to production, has testids). Alternative/addition: the **Evaluate-tab**
   `HitlPanel`. Want both, or start with deployment-chat?
4. **Fixture resolution.** Resolve the running sandbox dep-id + prod artifact-id (and the
   running prod deployment-id) dynamically via the API in `beforeAll` (resilient to
   redeploys), with `HITL_E2E_*` env overrides. (The current spec pins a dep-id that goes
   stale.) For production I'll target `/catalog/<artifactId>/chat?dep=<running-prod-dep-id>`
   using the new `?dep` pin so the run hits a known deployment deterministically. OK?
5. **Determinism.** Forcing prompt + assert reasoning/args are *non-empty and contain a known
   substring*, not exact text (the model writes the sentence). OK?

## 9. Deliverables
- `studio/e2e/hitl-golden-sandbox.spec.ts`, `studio/e2e/hitl-golden-production.spec.ts`
- testid additions in `HITLDashboardPage.tsx`, `CatalogChatPage.tsx` (+ Vitest assertions)
- a short entry in `docs/testing/manual-ui-e2e-test-plan.md` pointing at these as the golden gate
- run: `bash scripts/studio-e2e.sh e2e/hitl-golden-sandbox.spec.ts e2e/hitl-golden-production.spec.ts`

## 10. What I will NOT claim
Per the retro: I won't report this "done" until both specs **run green against the live
deployment** (real agent, real approval, real resume) — not just compile or pass typecheck.
And I'll prove the catch by running it against your injected bug and showing the exact
assertion that fails.
