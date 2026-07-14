---
name: plan
description: "Generate a technical implementation plan from docs/spec.md or a change-brief. Dispatches a planning agent that produces plan.md, research.md, data-model.md, contracts/, and quickstart.md. Works with any project structure."
argument-hint: "[path/to/spec.md] — or auto-detects from project"
---

# Plan Generation

Thin orchestrator — locates inputs, dispatches planning agent, reports artifacts.

## Parse Arguments

From `$ARGUMENTS`:
- If a file path is given → use it as the spec/design-brief input
- If empty → auto-detect (see below)

## Execute

1. **Locate spec/design input** (priority order):
   - Argument path (if provided)
   - `docs/plan/change-brief.md` (produced by `/improvement` for implementation-level changes)
   - `docs/spec.md` (unified spec — primary source of truth)
   - `specs/*/spec.md` (spec-kit convention)
   - `.specify/memory/spec.md`
   - `.specify/memory/design-brief.md`
   - `docs/design-brief.md`
   - If none found: ask user "Where is your spec or design brief?"

   **Note on change-brief.md**: If both `change-brief.md` and `docs/spec.md` exist, read BOTH. The change-brief scopes what to plan (the specific change), while spec.md provides the full system context. The plan should address the change-brief's scope within the spec's architectural constraints.

2. **Determine output directory**:
   - If `specs/{feature}/` exists → write there
   - Otherwise → create `docs/plan/` and write there

3. **Locate constitution** (optional, enhances quality):
   - `.specify/memory/constitution.md`
   - `docs/constitution.md`
   - `CLAUDE.md` (project principles section)
   - If none found → proceed without constitution check

4. **Dispatch planning agent** (use Agent tool):
   - Read `agents/planner.md` for the prompt template
   - Fill: spec path, change-brief path (if exists), output directory, constitution path, user arguments
   - Agent produces: plan.md, research.md, data-model.md, contracts/, quickstart.md

5. **Report**: Output directory, list of generated artifacts.
