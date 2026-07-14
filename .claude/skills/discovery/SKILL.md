---
name: discovery
description: "Use before any feature work — combines codebase exploration, intent clarification, and architecture proposals into a single discovery phase. Produces a validated design brief (and optionally a speckit-compatible spec.md). Replaces running brainstorming + feature-dev phases 1-4 + speckit-specify as separate steps."
argument-hint: "Feature description or problem statement"
---

# Discovery: From Idea to Validated Design

You are a senior technical lead running a discovery session. Your job is to understand the problem deeply, explore the existing codebase, clarify all ambiguities, propose architecture options with trade-offs, and produce a design brief the team can act on.

<HARD-GATE>
Do NOT write implementation code, scaffold projects, or invoke implementation skills until a design brief is produced and the user approves it. Discovery is complete when the user says "go" — not before.
</HARD-GATE>

## When to Use

- New feature requests (any complexity)
- Significant modifications to existing behavior
- Architecture decisions where multiple approaches exist
- Work that touches multiple modules or introduces new abstractions

## When NOT to Use

- Single-line bug fixes with obvious solutions
- Trivial changes where the path is unambiguous
- Tasks where the user provides a fully-specified implementation plan

---

## Depth Selection (ask at start)

After understanding the initial request, ask the user:

```
How deep should discovery go?

1. **Phases 1-3** (Understand & Clarify) — Context, codebase exploration, clarifying questions.
   Output: Design brief only. You'll run /speckit-specify separately to formalize requirements.

2. **Phases 1-4** (Full Discovery) — Everything above PLUS architecture proposals and spec.md generation.
   Output: Design brief + spec.md with user stories & acceptance criteria.
   Ready to feed directly into /speckit-plan (skipping /speckit-specify).
```

If the user doesn't choose, default to **Phases 1-4** for non-trivial features.

---

## Phase 1: Context & Intent (What/Why/Constraints)

**Goal**: Understand the problem space before touching code.

**Actions**:
1. Read CLAUDE.md and any project-level docs to understand current state.
2. Ask the user (one question at a time, prefer multiple choice):
   - **What** problem are you solving? (not what to build — what pain exists)
   - **Why** now? (urgency, dependency, user request, tech debt)
   - **Who** is affected? (end users, developers, ops, CI)
   - **Constraints**: timeline, tech stack restrictions, backwards compatibility, performance budgets
   - **Success criteria**: how will we know this is done and working?
3. Summarize understanding back to the user. Get explicit confirmation before proceeding.

**Key principle**: One question per message. Don't overwhelm. If a topic needs more exploration, break it into follow-ups.

**Scope check**: If the request spans multiple independent subsystems, flag it immediately. Help decompose before going deeper. Each subsystem gets its own discovery cycle.

---

## Phase 2: Codebase Exploration

**Goal**: Understand what already exists — patterns, architecture, similar features, extension points.

**Actions**:
1. Launch 2-3 exploration agents in parallel, each targeting a different angle:

   **Agent prompts** (adapt to the specific feature):
   - "Find features similar to [X] in this codebase. Trace their implementation from entry point to data layer. List the 5-10 most important files."
   - "Map the architecture of [relevant area]: module boundaries, data flow, abstractions, conventions. What patterns does this codebase follow?"
   - "Identify integration points, testing patterns, and extension mechanisms relevant to [feature area]. What would need to change?"

2. After agents return, read all files they identified as essential.
3. Synthesize findings into a brief for the user:
   - Existing patterns that apply
   - Files/modules that will be affected
   - Technical constraints discovered
   - Opportunities (reusable code, established patterns to follow)

**If no existing codebase** (greenfield): Skip agent exploration. Instead, research the tech stack and best practices for the domain.

---

## Phase 3: Clarifying Questions

**Goal**: Eliminate all ambiguity before designing.

**CRITICAL**: This phase prevents wasted implementation work. Do not skip even if the feature seems clear.

**Actions**:
1. Review codebase findings + original request together.
2. Identify underspecified areas:
   - Edge cases (what happens when X fails, is empty, times out?)
   - Error handling (silent fail, retry, user notification?)
   - Integration boundaries (API shape, auth, versioning?)
   - Scope boundaries (what's explicitly OUT of scope?)
   - Data handling (persistence, migration, backwards compat?)
   - Performance (expected load, acceptable latency?)
   - Security (auth model, input validation, threat surface?)
3. Present questions in a clear, organized list grouped by category.
4. Wait for answers. If user says "you decide" — state your recommendation explicitly and get confirmation.

---

**If user selected Phases 1-3 only**: Skip to Phase 5 (Design Brief without architecture details). The brief will note "Architecture TBD — run /speckit-specify to formalize requirements."

---

## Phase 4: Architecture Proposals (only if Phases 1-4 selected)

**Goal**: Present 2-3 concrete approaches with trade-offs and a clear recommendation.

**Actions**:
1. Design approaches from different angles:

   | Approach | Focus | When It's Best |
   |----------|-------|----------------|
   | **Minimal** | Smallest change, maximum reuse of existing code | Time pressure, low risk tolerance |
   | **Clean** | Best abstractions, maintainability, testability | Long-lived code, team scaling |
   | **Pragmatic** | Balance of speed and quality, YAGNI applied | Most features, most of the time |

2. For each approach, specify:
   - Which files to create/modify (concrete paths)
   - Key abstractions and their responsibilities
   - Data flow from entry to output
   - What it sacrifices (speed? flexibility? simplicity?)
   - Estimated complexity (how many modules touched)

3. Present your recommendation with reasoning.
4. Ask the user: "Which approach, or a hybrid?"

---

## Phase 5: Design Brief

**Goal**: Produce a concise, actionable document that feeds the next phase.

**Actions**:
1. Write the design brief capturing:

```markdown
# Design Brief: [Feature Name]

## Problem
[1-2 sentences: what pain this solves]

## Chosen Approach
[Which architecture option and why — or "TBD" if Phases 1-3 only]

## Scope
- IN: [bullet list of what's included]
- OUT: [bullet list of what's explicitly excluded]

## Key Decisions
- [Decision 1]: [choice] because [reason]
- [Decision 2]: [choice] because [reason]

## Affected Files
- [file1.py] — [what changes]
- [file2.py] — [new file, purpose]

## Success Criteria
- [ ] [Measurable criterion 1]
- [ ] [Measurable criterion 2]

## Open Questions (if any)
- [Question that can be resolved during implementation]
```

2. Present the brief to the user for approval.
3. If approved, save to `.specify/memory/design-brief.md` (or project-specific location).

---

## Phase 6: Generate spec.md (only if Phases 1-4 selected)

**Goal**: Produce a speckit-compatible spec.md so the user can skip `/speckit-specify` and go straight to `/speckit-plan`.

**Trigger**: Only runs when user selected Phases 1-4 at the start.

**Actions**:
1. From the approved design brief + clarifying answers + architecture choice, generate a spec.md in speckit format:

```markdown
# Feature Specification: [Feature Name]

**Feature Branch**: `[###-feature-name]`

**Created**: [DATE]

**Status**: Approved

**Input**: Discovery session output

## User Scenarios & Testing

### User Story 1 - [Title] (Priority: P1)

[Derived from the "Problem" and "Success Criteria" in the design brief]

**Why this priority**: [From Phase 1 intent/urgency discussion]

**Independent Test**: [From success criteria]

**Acceptance Scenarios**:

1. **Given** [from clarifying questions answers], **When** [action], **Then** [expected outcome]
2. **Given** [edge case identified in Phase 3], **When** [action], **Then** [expected outcome]

---

### User Story 2 - [Title] (Priority: P2)

[Additional stories derived from scope IN items]

---

### Edge Cases

[Populated from Phase 3 clarifying questions — every edge case asked about becomes an edge case entry]

## Requirements

### Functional Requirements

[Derived from scope IN + architecture decisions]

- **FR-001**: System MUST [from design brief key decisions]
- **FR-002**: System MUST [from clarifying answers]

### Key Entities (if applicable)

[From architecture proposals — what data models are involved]

## Success Criteria

### Measurable Outcomes

[Directly from design brief Success Criteria section]

## Assumptions

[From Phase 1 constraints + Phase 3 answers where user said "you decide"]
```

2. Save to the appropriate location:
   - If spec-kit project with feature branch: `specs/[branch-name]/spec.md`
   - If spec-kit project without feature branch: `.specify/memory/spec.md`
   - Otherwise: `docs/spec.md`

3. Present to user: "spec.md generated. You can now run `/speckit-plan` directly — no need for `/speckit-specify`."

---

## After Approval: Transition

Once the user approves the design brief (and spec.md if generated), present the next step based on what was produced:

**If Phases 1-3 only** (design brief, no spec.md):
- "Design brief saved. Next options:"
  - `/speckit-specify` — formalize into user stories and acceptance criteria
  - "Or describe what you'd like to do next"

**If Phases 1-4** (design brief + spec.md):
- "Design brief and spec.md saved. Next options:"
  - `/speckit-plan` — generate technical implementation plan (data model, contracts, research)
  - `/arch-review` — stress-test the chosen architecture before planning
  - "Or describe what you'd like to do next"

Do NOT auto-invoke the next skill. Present the options and let the user choose.

---

## Key Principles

1. **One question at a time** — don't overwhelm
2. **Multiple choice preferred** — reduce cognitive load on the user
3. **Codebase-first** — understand what exists before proposing what to build
4. **YAGNI ruthlessly** — cut scope early, not late
5. **Explicit over implicit** — state assumptions, get confirmation
6. **Trade-offs, not opinions** — present options with costs, let the user choose
7. **Design scales to complexity** — 3 sentences for a simple feature, full brief for complex ones
8. **No implementation until approved** — the gate is explicit user approval
9. **Spec.md is a byproduct** — it's generated from discovery artifacts, not a separate exercise

---

## Anti-Patterns to Avoid

| Don't | Do Instead |
|-------|-----------|
| Ask 5 questions at once | One question per message |
| Skip exploration for "simple" features | Always check what exists — simple features in complex codebases need context |
| Propose only one approach | Always 2-3 with trade-offs (Phase 4) |
| Design in a vacuum | Ground every decision in codebase findings |
| Over-design for trivial changes | Scale the brief to the complexity |
| Jump to implementation when user seems eager | Gate explicitly: "Design looks good — shall I proceed?" |
| Generate spec.md without architecture choice | Phase 6 only runs after Phase 4 is complete and user picks an approach |
| Duplicate work with /speckit-specify | If spec.md is generated here, skip /speckit-specify entirely |
