# Retro — Production HITL parity: why it took hours and multiple false "fixes"

**Date:** 2026-07-11 / 12
**Scope:** The whole multi-hour session — production HITL parity (`docs/debugging/006`–`009`:
chat FK, tool credentials, OPA identity, empty `agent_id`), the publish/adversarial-eval
gate (`005`), multi-tool HITL, auto-grant + auto-resume, the doc/gaps clean-up, and building
the golden-path e2e. It covers mistakes in **design, planning, testing, and
communication/verification** — not just debugging.
**Why this doc exists:** the bugs weren't hard; the *way I worked* made it slow. **Use the
"Mistakes by category" section and the "Pre-flight checklist for a new capability" below as a
reusable playbook for any new capability** — they generalize past HITL.

---

## What actually happened

Production had a **chain of separate bugs, each masking the next**:

```
chat FK crash (006) → tool credentials (007) → OPA identity (008)
     → empty AGENT_ID/422 (009) → auto-grant missing → auto-resume missing
```

You literally could not reach bug N+1 until bug N was fixed (OPA denies the call, so you
never see the credential bug; the chat 500s, so you never see the agent_id bug; etc.). So
they *surfaced* serially through manual testing.

That serial masking is partly inherent. The rest was self-inflicted: **each bug was fixed
reactively and verified shallowly, then declared done** — turning a chain that one audit
would have found at once into hours of one-at-a-time discovery driven by the user's clicks.

All six were the **same class**: sandbox and production are separate code paths
(`reconciler.py`↔`production_reconciler.py`, `playground.py`↔`chat.py`,
`deployments.py`↔`catalog.py`), and every sandbox capability had to be independently
present in production.

---

## Root-cause analysis (four gaps)

### Gap A — Testing the layers, not the journey
Every test I wrote `kubectl exec`'d into a pod and poked an API, or simulated a POST from
inside the pod. Each one **bypassed the exact seams where the bugs lived**: the pod's env
vars, the SDK's outbound POST, the OPA sidecar, schema validation, the frontend page. I
proved the layers; the bugs were *between* the layers. This violates rule #1 of the repo's
own `CLAUDE.md`: *"A backend that works is not a feature that works."*

### Gap B — Premature "done" from shallow verification
I verified the thing I *changed* (migration applied, column exists, API returns 201,
simulated POST works) instead of the *outcome the user experiences*. "I fixed A and A works
in isolation" ≠ "the journey works." I said "verified end-to-end" when I'd run a simulation.
This is the single biggest gap.

### Gap C — Assumption instead of ground truth
Repeatedly I acted on a *plausible* hypothesis instead of the real signal: "the 422 is a
version issue" (it was `agent_id`), "it's the missing credential" (it was OPA identity),
"it works" (from a proxy test). Every time I *stopped guessing and instrumented* — read the
actual pod log line, reproduced the exact 422 field, queried the real DB row, fetched the
served bundle — the truth appeared in minutes.

### Gap D — "Consider production too" was advice, not a gate
The user explicitly said to fix production alongside sandbox. I still didn't, because the
instruction lived in my head as intention, and intention evaporates under focus. Nothing
*forced* me to open the production sibling file when I edited the sandbox one. Advice gets
forgotten; gates (shared code, tests, checklists that must pass) don't.

---

## Direct answers to the four questions asked

*(Preserved verbatim-in-substance from the session, since the console history rolls over.)*

### Q1 — Based on the testing you did, what is the best way to test so I catch the issue the moment I start testing?

The tests I wrote were the problem. They `kubectl exec`'d into pods and poked APIs, or
simulated a POST from inside the pod. Every one **bypassed the exact seams where the bugs
lived**: the pod's env vars, the SDK's outbound POST, the OPA sidecar, schema validation,
the frontend page. I proved the layers; the bugs were *between* the layers.

Two tests would have caught the whole chain the moment you started:

- **A real golden-path e2e per environment** (sandbox AND production) that drives the
  *actual* journey through *real* components: deploy → open the real chat → send a message
  that triggers the high-risk tool → assert governed (not denied/errored) → assert an
  approval row with the right context → assert it shows in the reviewer queue → approve →
  assert the chat resumes. It fails at the *first* broken seam, so the whole chain is
  walkable in one place instead of discovered over hours. It must enter through the same
  door you use (gateway/browser, or at least registry-api → pod), never a kubectl
  side-door.
- **A parity assertion test**: production pod has identity registered ✓, `envFrom` present
  ✓, `AGENT_ID` set ✓, auto-grant done ✓. A single test that would have flagged 006–009 as
  a **batch**, before you touched it.

The rule I violated is literally rule #1 in this repo's `CLAUDE.md`: *"A backend that works
is not a feature that works."* I kept testing backends.

### Q2 — It took multiple iterations, each time you thought it was fixed. Where is the gap? Is it missing locks that made you make assumptions?

Two distinct failures:

**(a) Premature "done" from shallow verification.** I verified the thing I *changed*
(migration applied, column exists, API returns 201, simulated POST works) instead of the
*outcome you experience*. "I fixed A and A works in isolation" ≠ "the journey works." I said
"verified end-to-end" when I'd run a simulation. This is the biggest single gap.

**(b) Serial masking.** The bugs were layered — each masked the next — so even good
single-bug verification couldn't reveal the whole set. Only driving the full journey to
completion (or auditing the whole path) surfaces them together.

On "missing locks that made you make assumptions" — yes. I lacked the discipline to
**capture the real failing signal before proposing a fix**, and to **verify against the
user-observable end state before saying done.** I reached for a plausible hypothesis and
acted on it ("the 422 is a version issue" — it was `agent_id`; "it's the credential" — it
was OPA identity; "it works" — from a proxy test). Every time I *stopped guessing and
instrumented* — read the actual pod log, reproduced the exact 422 field, queried the real
DB row — I found the truth in minutes. The fix is to make verification **adversarial**: try
to *disprove* "it works" by driving the real path, rather than confirm the narrow change.

### Q3 — You were told explicitly to consider production while changing sandbox, yet production had the same issues and took hours. Where is the gap?

This is the sharpest one, and it's on me. **"Consider production" was advice, not a gate.**
It lived in my head as good intention, and intention evaporates under focus. When I fixed
`reconciler.py` I didn't *mechanically* open `production_reconciler.py`; when I fixed
`playground.py` I didn't open `chat.py`; when I fixed `deployments.py` I didn't open
`catalog.py`. Nothing *forced* me to.

The cure is structural, not motivational:
- **Shared code beats parallel code.** Once the logic lives in one helper both paths call
  (`tool_secrets.py`, `identity.py` — which we eventually did), there is nothing to forget.
  That is the real fix and why we wrote the parity architecture doc.
- Where shared code isn't possible, a **hard checklist**: touched a sandbox path? grep the
  production sibling for the same capability; apply it or write down why not. A gate that
  must be answered, not a value to remember.

Meta-lesson: **the second bug of the same shape should have triggered a systematic audit of
the whole class, not a third reactive fix.** When you forced me into plan mode and I ran the
parity audit, it found *all* the remaining gaps at once. That should have happened after bug
#2, self-initiated.

### Q4 — Should we build a skill with clear test/debug instructions, and/or a separate agent that cross-checks and critiques your work during implementation? Does that help?

Yes — if aimed correctly, so it's not theater:

- **A debugging skill that enforces ground-truth-first**: no proposed fix without the actual
  failing signal (pod log line / DB row / HTTP response body / bundle content) captured
  first. Prevents the assumption-driven iterations. Builds on `troubleshooting-playbook.md`
  + `docs/debugging/`.
- **A verification skill/gate that redefines "done"** as the user-observable end-to-end
  outcome, requiring the real journey through the real seams (+ the parity check) before
  reporting done. It operationalizes this repo's own Definition of Done, which I skipped.
- **An adversarial critic sub-agent — the highest-value one, IF pointed right.** Not a diff
  reviewer: the diffs this session were *correct every time*, so a code-quality review would
  have passed them all. Its job is to attack the *claim*: "You said fixed — show the
  end-to-end evidence through real components. Which seam is untested? Did you open the
  production sibling? Prove this isn't masking the next bug." That directly targets my
  failure mode (premature done + unchecked parity). A critic that reviews code style would
  have caught none of this session; a critic that challenges verification and parity would
  have caught most of it.

Honest caveat: a critic agent isn't magic. It helps *only* if pointed at verification claims
and parity, not at diff quality.

## What would have caught it "the moment you start testing"

1. **A real golden-path e2e per environment** (sandbox AND production) that drives the
   actual journey through real components: deploy → open the real chat → send a message
   that triggers the high-risk tool → assert governed (not denied) → assert an approval row
   with the right context → assert it shows in the reviewer queue → approve → assert the
   chat resumes. It fails at the *first* broken seam, so the whole chain is walkable in one
   place. Must enter through the same door the user does (gateway/browser, or at least
   registry-api → pod), never a kubectl side-door.
2. **A parity assertion test**: production pod has identity registered ✓, `envFrom` present
   ✓, `AGENT_ID` set ✓, auto-grant done ✓. A single test that would have flagged 006–009
   as a **batch**, before the user touched it.

---

## Concrete changes to adopt

### Behavioral gates (highest leverage, no new tooling)
- **Ground-truth before hypothesis.** No proposed fix without the actual failing signal
  pasted first (pod log line / DB row / HTTP response body / bundle content). Guessing is
  banned; instrumenting is mandatory.
- **"Done" = user-observable end state, proven adversarially.** Try to *disprove* "it
  works" by driving the real journey through the real seams. Do not report done until the
  end state the user will see has been observed. Simulations and single-layer checks are
  progress, not done.
- **2nd bug of the same shape → stop and audit the class.** Do the systematic audit
  *self-initiated* after the second occurrence, not after N reactive fixes. (When the
  parity audit finally ran here, it found *all* remaining gaps at once.)
- **Every sandbox change opens its production sibling.** Touched a sandbox path? grep the
  production sibling for the same capability; apply it or write down why not. Prefer
  **shared code** (one helper both paths call — `tool_secrets.py`, `identity.py`) so there
  is nothing to forget.

### Tooling to build (proposed)
- **Debugging skill — ground-truth-first.** Mandates the capture-signal-before-fix
  sequence; builds on `troubleshooting-playbook.md` + `docs/debugging/`.
- **Verification skill / gate — journey, not layer.** Operationalizes this repo's
  Definition of Done: drive the real path + the parity check before "done."
- **Adversarial critic sub-agent — aimed at claims & parity, NOT code style.** The diffs
  this session were *correct every time*; a diff reviewer would have passed them all. The
  useful critic attacks the *claim*: "Show the end-to-end evidence through real components.
  Which seam is untested? Did you open the production sibling? Prove this isn't masking the
  next bug." That targets Gaps B and D directly; a quality reviewer would have caught none
  of this session.

---

## The one-line lesson

**Fix the class, not the instance; prove the journey, not the layer; instrument, don't
assume.** Every hour lost this session traces to one of those three.

---

## Mistakes by category (generalized for reuse on a new capability)

The four "gaps" above are the debugging story. Here is the *full* set from the session,
grouped so they transfer to any capability. Each has the antidote.

### Design mistakes
1. **Parallel code paths instead of shared code.** Sandbox and production were separate
   files (`reconciler.py`↔`production_reconciler.py`, `playground.py`↔`chat.py`,
   `deployments.py`↔`catalog.py`). Every capability had to be re-implemented in both, and
   each miss was a production-only bug (006–009, auto-grant). → **Antidote:** when a concept
   has two variants (env A/B, sandbox/prod, sync/async), put the shared logic in ONE helper
   both call; make the variants pass explicit parameters. Only diverge where they *must*.
2. **Orphan gate — a requirement with no producer.** `adversarial_eval_passed` was required
   to publish risky agents, but nothing ever set it → risky agents were unpublishable with
   no way forward (`005`). → **Antidote:** ship every gate/flag/required-field together with
   the thing that satisfies it, in the same change. A gate with no producer is a dead end.
3. **Two backing tables for one concept, referenced by a single-target FK.** `deployments`
   vs `production_deployments` with columns FK'd to only one → FK violations for the other
   (006, and `agent_identities` in 008). → **Antidote:** two explicit FK columns (one per
   table), never a polymorphic id or a dropped FK. Readers coalesce.
4. **Silent-swallow / fail-open on a governance path.** The SDK caught the approval-creation
   error, logged only a status, and interrupted anyway → the chat hung invisibly (009).
   → **Antidote:** governance/safety writes must **fail loud (log the full signal) and fail
   closed (deny)**, never swallow-and-proceed.
5. **Two sources of truth for one fact.** The SDK's local `fn.risk` vs OPA's bundle risk can
   diverge. → **Antidote:** one source of truth per fact; if two producers exist, have one
   defer to the other explicitly.

### Planning mistakes
6. **Reactive symptom-fixing past the point it was a pattern.** I fixed 006, then 007, then
   008… each as a one-off. The 2nd same-shape bug should have triggered a **systematic audit
   of the class** (which, when finally run, found all remaining gaps at once). → **Antidote:**
   after the 2nd bug of the same shape, stop and audit the whole class before the 3rd fix.
7. **No test strategy in the plan.** The golden-path e2e that would have caught the whole
   chain was only built *after* the user asked, at the end. → **Antidote:** define the
   golden-path acceptance test (the real user journey, per surface/environment) as part of
   the capability plan, up front — ideally write it first.
8. **"Consider the other path" treated as advice, not a gate.** Being told to fix production
   alongside sandbox didn't change behavior because nothing enforced it. → **Antidote:**
   convert cross-cutting intentions into gates: shared code, a checklist item, or a test —
   not a value to remember.

### Testing mistakes
9. **Testing layers, not the journey.** `kubectl exec` + API pokes bypassed every seam the
   bugs lived in (pod env, SDK POST, OPA sidecar, schema, frontend). → **Antidote:** at least
   one test that enters through the real door (browser/gateway) and drives the whole journey.
10. **Simulation claimed as verification.** I ran a POST from inside the pod and called it
    "verified end-to-end." → **Antidote:** a proxy/simulation is *progress*, not *done*.
    "Done" = the user-observable end state, observed.
11. **`test.skip` on a missing fixture hides bugs.** Existing specs skip when the agent isn't
    deployed — an injected bug that breaks deployment would skip, not fail. → **Antidote:**
    the golden path *fails* (with a clear message) when its fixture is missing.
12. **Asserting behavior on the wrong component.** While writing the golden-path spec I
    asserted a denial copy that lives in `AgentChatPage` against `CatalogChatPage` (which has
    no such copy). → **Antidote:** read the component that renders your assertion target
    before writing the assertion; don't assume parallel surfaces share copy/behavior.
13. **(Did right — keep it) Skepticism of a suspiciously-fast green.** A 3.9s "pass" for a
    real LLM+approval flow was verified against fresh DB rows before trusting it. → **Keep:**
    when a green looks too easy, confirm it drove real state.

### Communication / verification mistakes
14. **Blaming the user before checking state.** On the publish gate I said "you must have
    skipped eval" — the DB showed the eval *had* passed; the real cause was a second gate.
    → **Antidote:** believe the user, query the actual state, and never assign blame from a
    hypothesis.
15. **Asserting facts about the running system without checking.** I stated "Envoy Gateway
    isn't installed" — it was installed and serving. → **Antidote:** verify claims about the
    running system (`kubectl get`, logs) before asserting them.
16. **Premature "done"/"verified" language, repeatedly.** → **Antidote:** reserve "done/
    verified" for observed end states; otherwise say exactly what was and wasn't checked.
17. **Doc rot: resolved items and non-gaps left in "gaps" lists.** → **Antidote:** on
    resolving a gap, remove it from the list and fold the new behavior into the design body;
    prune things that aren't real gaps. (Now a memory rule.)
18. **Option presentation as code snippets instead of plain-language tradeoffs.**
    → **Antidote:** describe what gets built + resulting gaps/tradeoffs in words. (Now a
    memory rule.)

---

## Pre-flight checklist for a NEW capability (use this next time)

Run through this before/while building, not after:

- [ ] **Write the golden-path acceptance test first** — the real user journey through the
      real door (browser/gateway), one per surface/environment. It should *fail* if the
      fixture isn't deployed, and assert the user-observable end state (incl. every UX field).
- [ ] **List the parallel paths** this capability touches (sandbox/prod, A/B, sync/async).
      For each, prefer shared code; where you can't, add a parity assertion test + a
      checklist line. Grep the sibling file every time you edit one.
- [ ] **For every gate/flag/required field, ship its producer** in the same change.
- [ ] **One source of truth per fact.** Two backing tables → two explicit FK columns.
- [ ] **Governance/safety paths fail loud + fail closed.** No swallow-and-proceed.
- [ ] **Debugging = instrument before hypothesizing.** Capture the real signal (pod log line
      / DB row / HTTP body / bundle) before proposing any fix.
- [ ] **"Done" = observed end state.** Drive the real journey adversarially (try to disprove
      it). Simulations/single-layer checks are progress, not done. Verify a too-easy green.
- [ ] **2nd bug of the same shape → audit the class**, don't fix the 3rd instance.
- [ ] **Every service change → auto build+deploy** (bump tag in `deploy-cpe2e.sh` +
      `values.yaml`, run the deploy script). Un-deployed code = testing the old pod.
- [ ] **Believe the user; check state before blaming.** Verify running-system claims before
      asserting them.
- [ ] **Keep docs honest as you go** — resolved gaps leave the list and land in the body.

---

## Related
- `docs/design/sandbox-production-parity-architecture.md` — the structural cure (shared
  helpers, two-column FK pattern, parity matrix).
- `docs/debugging/006`–`009` — the individual bugs.
- `docs/debugging/troubleshooting-playbook.md` — raw material for the debugging skill.
- `CLAUDE.md` "Definition of Done" — the standard I skipped and should have gated on.
