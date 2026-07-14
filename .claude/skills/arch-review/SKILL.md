---
name: arch-review
description: "Structured critique of docs/spec.md — evaluates both architecture quality (ATAM, AWS Well-Architected) and requirement quality (testability, completeness, ambiguity). Use after /arch-design or /improvement to validate before planning. Triggered automatically by /improvement-workflow and /pr-iterate when spec is amended."
argument-hint: "[path-to-spec] — defaults to docs/spec.md"
---

# Spec Review: Architecture + Requirement Quality

Thin orchestrator — locates spec, dispatches parallel review agents, consolidates verdict.

## Input Resolution

1. If argument provided → use that path
2. If no argument → read `docs/spec.md`
3. If neither exists → "No spec found. Run `/arch-design` first to produce `docs/spec.md`."

## Execute

1. **Read the spec** — load the full content of the target spec file.

2. **Lock evaluation criteria** — Before dispatching agents, select 8-12 evaluation criteria relevant to this system's category. Draw from established dimensions:

   ### Architecture Dimensions

   | Dimension | Source | What It Evaluates |
   |-----------|--------|-------------------|
   | Failure modes | ATAM | Where does the system break? What's the blast radius? |
   | Quality attribute tradeoffs | ATAM | Where does satisfying one attribute degrade another? |
   | Reversibility & lock-in | AWS/ATAM | Vendor lock-in, data format lock-in, contract lock-in. Cost of changing course at 6mo, 18mo. |
   | Complexity calibration | Fitness functions | Over-engineering vs under-engineering. |
   | Abstraction quality | ATAM modifiability | Are boundaries where the system actually changes? |
   | Operational burden | AWS Operational Excellence | Can you deploy, monitor, debug, and recover? |
   | Cost trajectory | AWS Cost Optimization | How does cost scale with growth? |
   | Security posture | AWS Security | Does the architecture make secure implementation easy or hard? |
   | Observability | ThoughtWorks | Can you tell what's happening in production? |
   | Better-path analysis | — | Given the same constraints, is there a strictly superior approach? |

   ### Requirement Quality Dimensions

   | Dimension | Source | What It Evaluates |
   |-----------|--------|-------------------|
   | Testability | IEEE 830 | Can each requirement be verified by a concrete test? |
   | Completeness | Requirements Eng. | Are there obvious functional gaps? |
   | Ambiguity | IEEE 830 | Can two engineers implement the same requirement differently? |
   | Edge case coverage | — | Are boundary conditions, error states, concurrency scenarios specified? |
   | Integration specification | — | Are external system interactions fully defined? |
   | Scope clarity | — | Is out-of-scope explicit? |
   | Acceptance criteria | INVEST | Is every requirement paired with verifiable acceptance criteria? |
   | Consistency | — | Do requirements contradict each other? |

3. **Dispatch 3 parallel review agents** (use Agent tool):

   Split the locked criteria across 3 agents, each handling a focused subset:

   - **Agent 1: Architecture Stress Test** — Evaluate: failure modes, quality attribute tradeoffs, complexity calibration, abstraction quality, better-path analysis. Must steelman the design first, then evaluate each criterion with VULNERABLE/finding/scenario/severity/suggestion format.

   - **Agent 2: Operational & Security Review** — Evaluate: operational burden, security posture, observability, cost trajectory, reversibility & lock-in. Same structured format per criterion.

   - **Agent 3: Requirement Quality Review** — Evaluate: testability, completeness, ambiguity, edge case coverage, integration specification, scope clarity, acceptance criteria, consistency. Quote specific requirement text that's problematic.

   **Each agent receives:**
   - The full spec content
   - Their assigned criteria (with "why relevant to this system" annotation)
   - The steelman prompt (restate the design in its strongest form before critiquing)
   - The structured output format (per criterion):
     ```
     ## [CRITERION NAME]
     VULNERABLE: YES / NO / PARTIALLY
     FINDING: [one sentence]
     SCENARIO: [concrete failure sequence]
     TRADEOFF: [what quality attribute is sacrificed vs gained]
     SEVERITY: BLOCKING / SERIOUS / WORTH-NOTING
     EVIDENCE: [why you believe this]
     SUGGESTION: [concrete alternative or mitigation]
     ```
   - Anti-sycophancy rules:
     1. No reconciliation — don't end with "overall solid design"
     2. No softening — use "this will fail when", not "might want to consider"
     3. Specificity or silence — if you cannot construct a concrete scenario, mark NO and skip

4. **Consolidate verdict** from all 3 agents:

   Collect all findings, then produce the final verdict:

   ```
   BLOCKING: [count]
   - [one-line summary each]

   SERIOUS: [count]
   - [one-line summary each]

   WORTH-NOTING: [count]
   - [one-line summary each]

   VERDICT: [one of]
   - PROCEED: No blocking issues.
   - PROCEED WITH MODIFICATIONS: Blocking issues have clear fixes. [list changes]
   - RETHINK [specific aspect]: Blocking issues are structural. [explain]
   ```

5. **Requirement Gap Report** — If Agent 3 found any requirement dimension VULNERABLE:

   ```
   REQUIREMENT GAPS:
   - [Gap 1]: [which requirement] → [what needs to be added/clarified]
   - [Gap 2]: ...

   These gaps should be addressed in docs/spec.md before proceeding to /plan.
   ```

6. **Alternatives** (only if verdict includes RETHINK):
   - Dispatch a Sonnet agent to propose alternatives that address the specific blocking issues
   - Alternatives must be evaluated against the SAME locked criteria
   - Must state what they sacrifice vs. the original

## Output Routing

- 3 or fewer findings, all WORTH-NOTING or SERIOUS → output verdict inline.
- More than 3 findings OR any BLOCKING → write full review to `docs/<date>-arch-review-<topic>.md`. Report file path + verdict inline.

## Anti-Sycophancy Rules (for consolidation)

1. **No reconciliation.** The evaluation stands as-is.
2. **No softening.** Use: "this will fail when", "this locks you into", "this is more complex than the problem requires".
3. **Hold under pushback.** Do not retreat unless new evidence is provided.
4. **No compliments.** This is a stress test.
5. **Specificity or silence.** If you cannot articulate a specific issue, don't manufacture one.
