---
name: implement
description: "Execute tasks from tasks.md by dispatching implementation agents. All context comes from artifacts (plan, spec, data-model, contracts) — no conversation history needed. Works with any project structure."
argument-hint: "[phase N] [--parallel] [--sequential] [path/to/tasks.md]"
---

# Implementation Executor

Thin orchestrator — asks strategy, locates tasks.md, dispatches agents, verifies merges.

## Parse Arguments

From `$ARGUMENTS`:
- `phase N`: Only execute phase N
- `--parallel`: Force parallel execution for [P] tasks (uses git worktrees)
- `--sequential`: Force sequential execution (ignore [P] markers)
- File path: use as tasks.md location
- (empty): Ask user for strategy, auto-detect tasks.md

## Step 1: Ask Implementation Strategy

If neither `--parallel` nor `--sequential` is in arguments, ask:

```
How should I execute [P] parallel tasks?

1. Sequential (recommended for accuracy)
   — Each agent sees previous agents' output
   — Zero merge risk, no worktrees needed
   — Slower but guaranteed correctness

2. Parallel with worktrees
   — [P] tasks run simultaneously in isolated git worktrees
   — Faster, but requires merge verification after each phase
   — Only safe when [P] tasks touch completely different files

Which strategy?
```

Record the choice. It applies to all phases in this run.

## Step 2: Locate Artifacts

**tasks.md** (priority order):
- Argument path → `specs/*/tasks.md` → `docs/plan/tasks.md` → `docs/tasks.md` → `tasks.md`
- Not found → "No tasks.md found. Run `/tasks` first."

**Supporting artifacts** (same directory as tasks.md or project root):
- plan.md, spec.md, data-model.md, contracts/, constitution.md

## Step 3: Create .gitignore (if not present)

Detect language from plan.md or file extensions. Add standard ignores.

## Step 4: Execute Phases

Parse tasks.md into phases. For each phase:

**Execution Rules**:
- **Integration tests are not optional**: If tasks.md specifies integration tests (Testcontainers, docker-compose, test fixtures), write them alongside unit tests — do not defer them
- **Run per-task verification**: After completing each task, run its "Verify:" command if one is listed
- Only mark a task `[X]` AFTER its verification command passes green

### If Sequential Strategy:

```
For each task in the phase (in order):
  1. Dispatch implementation agent (from agents/implementer.md)
     - Agent sees full current state of project
  2. Wait for completion
  3. Run task's "Verify:" command if specified — fix failures before proceeding
  4. Mark [X] in tasks.md
  5. Report: "✓ T0XX: description"
```

After phase completes → dispatch verification agent (agents/verifier.md)

### If Parallel Strategy (with worktrees):

```
1. BEFORE dispatching:
   - Collect all [P] tasks in this phase
   - Extract file paths from each task
   - CHECK FOR FILE OVERLAP:
     If any two [P] tasks mention the same file → ABORT parallel
     Fall back to sequential for THIS phase, warn user

2. Dispatch [P] tasks in parallel:
   - Each agent uses: isolation: "worktree"
   - Each agent gets agents/implementer-worktree.md prompt
   - Sequential (non-[P]) tasks run after parallel tasks complete

3. AFTER all parallel agents complete:
   - Dispatch MERGE VERIFICATION agent (agents/merge-verifier.md):
     a. Check for file conflicts across worktrees
     b. Run full test suite
     c. Verify all imports resolve
     d. Check no shared files were modified by multiple agents
   - If verification FAILS:
     Report conflicts, halt, ask user how to proceed
   - If verification PASSES:
     Mark tasks [X], report success

4. Sequential tasks in the phase run AFTER parallel merge is verified
```

## Step 5: Phase Verification

After EVERY phase (regardless of strategy), dispatch a verification agent:
- Runs the full test suite (unit AND integration tests)
- Checks imports resolve
- Verifies no syntax errors in new files
- Runs all static analysis tools configured in the project (CheckStyle, PMD, SpotBugs, ESLint, clippy, etc.)
- Verifies code coverage meets configured thresholds (merged unit + integration coverage)
- If verification fails → halt, **fix all failures**, then re-verify before proceeding

## Step 6: Final Verification Gate

After ALL phases complete, run the project's full build+verify pipeline:
- e.g., `mvn verify`, `npm run build && npm test`, `cargo clippy && cargo test`, `go test ./...`
- Fix ALL static analysis violations, coverage failures, and test failures
- This is **blocking** — do not report completion until the full pipeline passes green

## Step 7: Report

Summary of completed tasks, verification results (pipeline output), what's next.

## Agent Dispatch Strategy Summary

| Phase Size | Sequential Mode | Parallel Mode |
|------------|----------------|---------------|
| 1-3 tasks | One agent for all | One agent for all (no worktree needed) |
| >3 tasks, no [P] | One agent per task | One agent per task (sequential) |
| >3 tasks, has [P] | Ignore [P], run sequentially | [P] tasks in worktrees, sequential tasks after |

## Progress Tracking

- Mark `- [X]` in tasks.md after each completed task
- Report checkpoint after each phase
- If agent fails: report error, halt phase
- **Do NOT treat informational sections** (e.g., "MVP Scope", "Notes", "Defer to post-MVP") as permission to skip tasks that are listed in the main task list. If a task has a checkbox, it must be completed.
- Only mark a task `[X]` AFTER its verification command passes green

## Done When

- All tasks in tasks.md completed and marked `[X]` (no task skipped without explicit user approval)
- Full project verification pipeline executed successfully
- All static analysis tools pass (CheckStyle, PMD, SpotBugs, ESLint, clippy, etc.)
- Code coverage meets configured threshold (merged unit + integration coverage)
- All integration tests written and passing (not deferred)
- Completion reported to user with summary and verification results
