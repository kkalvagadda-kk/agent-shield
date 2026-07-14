# WS-6 Implementation Plan — Operate-surface parity cleanup + moved gap-analysis items

**Slice:** WS-6 of Execution Models v2 (spec §5 WS-6; absorbs the items moved out of
`execution-models-gap-analysis.md` on 2026-07-12). **Covers WS-6 ONLY.**
**Depends on WS-1 (inbox page + authority) for the nav badge; otherwise standalone.**
**No companion artifacts** — WS-6 is UI/operate parity + worker parameterization; no new schema, no new
external contract.

> **No migration.** Every item reuses existing tables/workers. WS-6 is a **parity + polish** slice, not a
> data-model slice.

> ⚠️ **Plan status — design stable, specifics indicative.** The architecture, sequencing, and locked
> decisions (D1–D4, R1–R3, parity gates, gap ledger) here are **stable and reviewable now** — that is what
> writing ahead buys. The execution specifics — `file:line`, migration numbers, image tags, orphan-greps,
> exact task order — are **indicative against the 2026-07-12 tree** and WILL drift as the WS-0→ spine merges.
> **Re-ground every specific against live code when this slice is minted into its own `tasks.md`** (the
> just-in-time step). Never treat a `file:line` or migration number here as ground truth. (CLAUDE.md: design
> docs go stale — verify in code before relying.)

## 1. Goal

Close the five operate-surface items relocated from the gap-analysis doc into this plan, each as a **parity
fix** (shared/parameterized code, not a fork). Concretely, after WS-6:

1. **[TODO-1] Global Approvals Inbox nav badge.** The inbox page + `agent:reviewer` authority ship in WS-1;
   WS-6 adds the **global nav badge** (pending-count) that routes to `/approvals` (`App.tsx:87`). Producer =
   the pending-approvals count endpoint (exists — `listPendingApprovals`); reader = the badge.
2. **[TODO-3] `eval_passed` auto-gate — a done dependency.** Already shipped (`eval_runner.py:309,326`); WS-6
   records it as a **satisfied precondition** of the v2 publish gate — no build, just the honest ledger entry.
3. **[TODO-4] CatalogDetailPage reuses the shared mode-aware Overviews — DELETE the fork.** The
   `OverviewReactive/Durable/Scheduled/EventDriven` components already exist in
   `studio/src/components/agent-detail/` and `AgentDetailPage` uses them. WS-6 makes **CatalogDetailPage**
   (`studio/src/pages/CatalogDetailPage.tsx`) mount the **same** parameterized components — the retro root
   cause was exactly a `AgentDetailPage`↔`CatalogDetailPage` parallel-code split. **Option B ("accept the
   split") is deleted.**
4. **[TODO-7] Sandbox run TTL / auto-cancel — one worker, parameterized by scope.** The
   `approval_timeout_worker.py` already sweeps **production** durable runs (`_agent_pod_url(...,
   environment="production")`, `_DURABLE_RUN_TTL_MINUTES=10`). WS-6 extends the **same worker** to also sweep
   **sandbox** runs (parameterized by environment/scope) — not a second sandbox-only worker.
5. **[Browser Cache] Cache-bust marker enforced on every Studio image bump.** `window.__STUDIO_BUILD`
   (`main.tsx:10`) exists; WS-6 makes the deploy fail-loud if a Studio image bump ships **without** updating
   the marker to the new tag (so a stale bundle can't silently serve). Prevents the recurring "UI looks
   broken but the API is fine" cache class.
6. **[WS-0 OQ-10] Unify `WORKFLOW_REACTIVE_TIMEOUT_S` with the run-timeout worker.** WS-0 shipped a reactive
   workflow wall-clock cap with a default constant; WS-6 folds that cap into the shared timeout worker's
   scope parameterization so there is one run-timeout mechanism, not a constant + a worker.

**Out of scope (stay in `execution-models-gap-analysis.md`):** TODO-2 alerting (verified shipped in WS-3),
TODO-5 full role-based run/memory filtering (the `agent:reviewer` **producer** lands in WS-1/WS-2; the broad
run+memory user-scoping remains a gap-analysis item — cross-referenced, not absorbed), TODO-6 Redis hot path.

## 2. Architecture — parity everywhere

```
[TODO-4] AgentDetailPage ─┐
                          ├─► components/agent-detail/Overview{Reactive,Durable,Scheduled,EventDriven}  (ONE set)
 CatalogDetailPage ───────┘     (was: CatalogDetailPage had its own inline overview → the fork)

[TODO-7 + OQ-10] approval_timeout_worker._sweep_once(scope)  ← one worker
                   scope="production" (exists)  +  scope="sandbox" (WS-6)  +  reactive-workflow cap folded in

[TODO-1] nav badge → GET pending-approvals count → routes to /approvals (WS-1 page)

[Browser Cache] deploy-cpe2e.sh asserts window.__STUDIO_BUILD == STUDIO_TAG before/after build (fail-loud)
```

**The whole slice is anti-fork:** shared Overview components (TODO-4), one timeout worker (TODO-7 + OQ-10),
one build marker check (cache). No new parallel path is introduced; existing forks are collapsed.

## 3. Migration / Schema

**None.**

## 4. Constitution / retro gates (condensed)

- **Parity:** TODO-4 deletes a real parallel-code fork (the retro's canonical example); TODO-7 extends one
  worker rather than mirroring it. Grep proves CatalogDetailPage imports the shared components and that there
  is a single `_sweep_once`.
- **Golden-path per environment:** Playwright asserts the nav badge count + route; a sandbox run past its TTL
  is auto-cancelled (bash suite, sandbox scope) exactly as production is; CatalogDetailPage renders each mode
  Overview identically to AgentDetailPage.
- **Ship the gate's producer:** the badge's count producer already exists; TODO-4's shared components already
  exist — WS-6 wires readers to existing producers (no orphan gate).
- **Reason from running product:** WS-6 corrects the gap-analysis (TODO-3 shipped, TODO-4 components already
  shared, TODO-7 worker already environment-parameterized) — the doc claimed more remained than the code shows.
- **No-Bandaid:** collapse the CatalogDetailPage fork (don't guard two overview code paths); one timeout
  worker parameterized by scope (not `if sandbox` branches sprinkled).

## 5. File Structure

### Studio
| File | C/M | Responsibility |
|---|---|---|
| `studio/src/pages/CatalogDetailPage.tsx` | M | Mount the shared `components/agent-detail/Overview*` components (delete the inline fork). |
| `studio/src/components/layout/*` (nav) | M | Global Approvals Inbox badge (pending count → `/approvals`). |
| `studio/src/api/registryApi.ts` | M (if gap) | `getPendingApprovalsCount` (reuse `listPendingApprovals` if a count endpoint is absent). |
| `studio/src/main.tsx` | M | Keep `window.__STUDIO_BUILD` in sync with the tag (asserted by deploy). |

### registry-api
| File | C/M | Responsibility |
|---|---|---|
| `services/registry-api/approval_timeout_worker.py` | M | Parameterize `_sweep_once(scope)` to also sweep `sandbox` runs; fold in the reactive-workflow cap (OQ-10). |

### infra
| File | C/M | Responsibility |
|---|---|---|
| `scripts/deploy-cpe2e.sh` | M | Fail-loud check: `window.__STUDIO_BUILD` == `STUDIO_TAG` (abort deploy on mismatch). |

### Tests
| File | C/M | Responsibility |
|---|---|---|
| `scripts/e2e/suite-61-operate-parity.sh` | **C** | Sandbox run past TTL auto-cancelled (same worker as prod); pending-count endpoint. |
| `scripts/e2e/run-all.sh` | M | Register suite-61. |
| `studio/e2e/approvals-badge.spec.ts` | **C** | Nav badge shows pending count → routes to inbox. |
| `studio/src/pages/CatalogDetailPage.test.tsx` | M/**C** | Vitest: renders the shared Overview per mode. |
| `docs/design/todo/execution-models-gap-analysis.md` | M | Mark TODO-1/3/4/7 + Browser Cache resolved here; TODO-3 shipped; keep TODO-2/5/6 with cross-refs. |
| `scripts/deploy-cpe2e.sh` + `charts/agentshield/values.yaml` | M | Bump registry-api + studio. |

## 6. Tasks (dependency-ordered)

### T1 — CatalogDetailPage parity (delete the fork) [TODO-4]
- **Files:** `CatalogDetailPage.tsx` (M), `CatalogDetailPage.test.tsx` (M/C).
- **Acceptance:** CatalogDetailPage renders each mode via the shared `Overview*` components, byte-identical to
  AgentDetailPage; no inline overview markup remains. **Deps:** none.
- **Verify:** `grep -n "OverviewReactive\|OverviewDurable\|OverviewScheduled\|OverviewEventDriven" studio/src/pages/CatalogDetailPage.tsx`; `cd studio && npm run typecheck && npm run test`.

### T2 — Sandbox run TTL via the shared worker [TODO-7 + OQ-10]
- **Files:** `approval_timeout_worker.py` (M).
- **Acceptance:** `_sweep_once("sandbox")` auto-cancels a sandbox durable run past `_DURABLE_RUN_TTL_MINUTES`
  using the same sweep logic as production; the reactive-workflow cap (WS-0) is folded into the worker's scope
  handling (one mechanism). **Deps:** WS-0 (cap exists), WS-1 (durable runs exist).
- **Verify:** `ast.parse` + mapper import; suite-61 sandbox-TTL case; `grep -c "_sweep_once" services/registry-api/approval_timeout_worker.py` → single def.

### T3 — Global Approvals Inbox nav badge [TODO-1]
- **Files:** nav component (M), `registryApi.ts` (M if a count endpoint is needed).
- **Acceptance:** the badge shows the pending count and routes to `/approvals`; `agent:reviewer`-gated per
  WS-1. **Deps:** WS-1 (inbox page + authority).
- **Verify:** `bash scripts/studio-e2e.sh` (approvals-badge spec).

### T4 — Cache-bust enforcement [Browser Cache]
- **Files:** `deploy-cpe2e.sh` (M), `main.tsx` (M).
- **Acceptance:** the deploy **aborts** if `window.__STUDIO_BUILD` != `STUDIO_TAG`; bumping the studio tag
  requires updating the marker (fail-loud). **Deps:** none.
- **Verify:** run the deploy check with a deliberately-stale marker → abort; with a matching marker → pass.

### T5 — Gap-analysis reconciliation + deploy [TODO-3 + ledger]
- **Files:** `execution-models-gap-analysis.md` (M), `deploy-cpe2e.sh`+`values.yaml` (M), `run-all.sh` (M).
- **Acceptance:** TODO-1/3/4/7 + Browser Cache marked resolved-in-WS-6; TODO-3 recorded shipped; TODO-2/5/6
  retained with cross-refs; suite-61 registered; tags bumped in both files. **Deps:** T1–T4.
- **Verify:** `grep -n "WS-6\|resolved\|shipped" docs/design/todo/execution-models-gap-analysis.md`.

## 7. Gap Ledger
| Item | Status | Note |
|---|---|---|
| TODO-5 full run/memory role-based **filtering** | **stays in gap-analysis (cross-referenced)** | The `agent:reviewer` producer lands in WS-1/WS-2; broad user-scoping of runs + memory is a separate gap-analysis item, not absorbed by WS-6. |
| TODO-6 Redis memory hot path | stays in gap-analysis | Performance, deferred until latency matters. |
| TODO-2 alerting | verified shipped (WS-3) | Email alerting exists; Slack/PagerDuty future. |

No orphan flags — WS-6 wires readers (badge, CatalogDetailPage, sandbox sweep) to producers that already
exist; it introduces no new producer without a reader.

## 8. Execution Notes
- **WS-6 is the anti-fork slice** — every task collapses a parallel path or parameterizes one worker. If a
  task tempts you to add a second component/worker, that's the bug WS-6 exists to remove.
- **Correct the gap-analysis to match the code** — TODO-3 shipped, TODO-4 components already shared, TODO-7
  worker already environment-parameterized. Reason from the running product (DoD #6).
- **Do WS-6 last or as polish** — it depends on WS-1's inbox for the badge; the rest is standalone and can
  land opportunistically.
