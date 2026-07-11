# Retro — Production HITL parity: why it took hours and multiple false "fixes"

**Date:** 2026-07-11
**Scope:** The session that took production HITL from broken to working across bugs
documented in `docs/debugging/006`–`009` (chat FK, tool credentials, OPA identity, empty
`agent_id`), plus production auto-grant and consumer-chat auto-resume.
**Why this doc exists:** the bugs weren't hard; the *way I worked* made it slow. This
captures the failure pattern and the concrete changes that would have collapsed hours into
one pass — so the next person (or agent) doesn't repeat it.

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

## Related
- `docs/design/sandbox-production-parity-architecture.md` — the structural cure (shared
  helpers, two-column FK pattern, parity matrix).
- `docs/debugging/006`–`009` — the individual bugs.
- `docs/debugging/troubleshooting-playbook.md` — raw material for the debugging skill.
- `CLAUDE.md` "Definition of Done" — the standard I skipped and should have gated on.
