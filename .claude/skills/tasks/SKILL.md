---
name: tasks
description: "Generate a dependency-ordered tasks.md from plan + spec artifacts. Dispatches a task generation agent. Works with any project structure — auto-detects plan.md and spec.md locations."
argument-hint: "[path/to/plan.md] [--tdd] — or auto-detects from project"
---

# Task Generation

Thin orchestrator — locates design artifacts, dispatches task generation agent, reports results.

## Parse Arguments

From `$ARGUMENTS`:
- File path → use as plan.md input
- `--tdd` or `include tests` → instruct agent to generate test tasks
- (empty) → auto-detect

## Execute

1. **Locate design artifacts** (priority order):
   - Plan: argument path → `specs/*/plan.md` → `docs/plan/plan.md` → `docs/plan.md`
   - Spec: same directory as plan → `specs/*/spec.md` → `.specify/memory/spec.md` → `docs/spec.md`
   - Optional: `data-model.md`, `contracts/`, `research.md`, `quickstart.md` (same directory as plan)
   - Constitution: `.specify/memory/constitution.md` → `docs/constitution.md`
   - If plan.md not found: "No plan found. Run `/plan` first."

2. **Determine output path**:
   - Same directory as plan.md → `tasks.md`

3. **Dispatch task generation agent** (use Agent tool):
   - Read `agents/task-generator.md` for the prompt template
   - Fill: plan path, spec path, available docs, constitution path, output path, user arguments
   - Always append these constraints to `{ARGUMENTS}` regardless of user input:
     - **File-level granularity**: each task targets at most 1-3 closely related files (~200 lines total). Never combine a router + model + schema into one task. This keeps each task within a single agent's context window.
     - **Checkpoint phases are mandatory**: insert a checkpoint phase (CP1, CP2, ...) after every 2-3 implementation phases. Each checkpoint writes 2-3 executable shell scripts under `scripts/` — a deploy script (`helm upgrade` or equivalent) and smoke test scripts with real `curl`/`kubectl`/`jq` assertions. Checkpoints use IDs like `[CP1a]`, `[CP1b]` — never `[T###]`. See `agents/task-generator.md` for the full checkpoint format.
   - Agent reads all artifacts and generates tasks.md

4. **Verify output** before reporting:
   - Confirm tasks.md contains at least one `## Checkpoint` section
   - Confirm no single task mentions more than 3 files
   - If either check fails, re-dispatch the agent with explicit correction

5. **Report**: Path to tasks.md, total task count (implementation + checkpoint separately), checkpoint locations, MVP scope.
