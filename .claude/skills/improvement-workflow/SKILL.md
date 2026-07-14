---
name: improvement-workflow
description: "One-command improvement cycle: triggers improvement to update spec.md and generate change-brief.md, plans fixes, generates tasks, implements, runs quality gate, and pushes to create a PR. End-to-end from change request to shipped PR."
argument-hint: "<change description, Jira ticket, or file path> [--sequential|--parallel] [--no-quality-gate] [--dry-run]"
---

# Improvement Workflow: End-to-End Change Pipeline

Thin orchestrator — sequences pipeline steps, manages gates, delegates execution to standalone skills. Artifacts on disk are the handoff between steps.

## Parse Arguments

From `$ARGUMENTS`:
- Change description, Jira ticket reference (e.g., `ACV2-123`), or file path to a change request → required
- `--sequential` → pass to implement step (default)
- `--parallel` → pass to implement step
- `--no-quality-gate` → skip quality gate step
- `--dry-run` → run improvement only, stop after change-brief

If no change input provided: ask "What change are you making? (description, Jira ticket, or file path)"

## Pre-flight: Resolve Branch and Plan Directory

1. **Resolve Jira ID:**
   - If a Jira ID is in the arguments (e.g., `ACV2-123`) → use it
   - If no Jira ID provided → ask: "What Jira ticket ID should this branch be named after? (e.g., ACV2-123)"

2. **Create branch from master:**
   - `git checkout master && git pull origin master && git checkout -b {JIRA_ID_LOWERCASE}`

3. **Create plan directory:**
   - `DATE=$(date +%Y-%m-%d)`
   - `PLAN_DIR={PROJECT_ROOT}/docs/plan/{DATE}_{JIRA_ID}`
   - `mkdir -p {PLAN_DIR}`
   - Write `{DATE}_{JIRA_ID}` to `{PROJECT_ROOT}/docs/plan/current`

## Execute

### Step 1: Dispatch Improvement Agent

Dispatch an Agent with the following prompt (fill `{CHANGE_INPUT}`, `{PROJECT_ROOT}`, and `{PLAN_DIR}`):

```
You are running the /improvement skill to assess a change and produce a change-brief.

## Input

Change request: {CHANGE_INPUT}
Project root: {PROJECT_ROOT}
Plan directory: {PLAN_DIR}

## Instructions

Follow the /improvement skill instructions exactly:

1. Read `docs/spec.md` at {PROJECT_ROOT}/docs/spec.md (if it exists)
   - If it doesn't exist: report "No spec found" and write a change-brief based on the change description alone

2. Read the change input — understand what's being proposed:
   - If Jira reference: fetch the ticket details (summary, description, acceptance criteria)
   - If file path: read the document
   - If description: use as-is

3. Assess impact against the spec:
   - Does this change affect requirements, architecture, integrations, constraints, or scope?

4. If spec change needed (Step 3A):
   - Amend `docs/spec.md` with targeted edits to affected sections only
   - Write `{PLAN_DIR}/change-brief.md` capturing the implementation delta
   - If Jira ID provided: write `{PLAN_DIR}/.jira-context` with the Jira ID

5. If no spec change needed (Step 3B):
   - Write `{PLAN_DIR}/change-brief.md` for implementation-only change
   - If Jira ID provided: write `{PLAN_DIR}/.jira-context` with the Jira ID

## Output

Report a summary:
"Assessment complete: [Spec amended + change-brief written | Change-brief written (implementation-only)]"

Include:
- Whether spec was amended (and which sections)
- Path to change-brief: {PLAN_DIR}/change-brief.md
- Jira ID if resolved (from input or .jira-context)
```

**After Agent 1 completes:**
- If `--dry-run` → stop, report improvement results
- Verify `{PLAN_DIR}/change-brief.md` exists
- Note whether spec was amended

---

### Step 1.5: User Review Gate

1. Show the user what was produced:
   - If spec amended: display `git diff docs/spec.md`
   - Display contents of `{PLAN_DIR}/change-brief.md`

2. Ask for confirmation:
   > "Here's my understanding of the change:
   >
   > [Show spec diff if amended]
   > [Show change-brief contents]
   >
   > Does this accurately capture the change? Are you good with these changes before I proceed to planning?
   >
   > Options:
   > - **Yes** — proceed to planning
   > - **No** — tell me what's wrong and I'll revise"

3. If user says No → re-dispatch Step 1 with user's correction as additional context, loop back here
4. If user says Yes → proceed to Step 1.6 (if spec amended) or Step 2

---

### Step 1.6: Arch Review Gate (only if spec was amended)

Invoke the `/arch-review` skill with `docs/spec.md` as input.

**After arch-review completes:**

- **PROCEED** → continue to Step 2
- **PROCEED WITH MODIFICATIONS** → present findings to user:
  > "Arch review found issues in the amended spec:
  >
  > [List BLOCKING/SERIOUS findings with suggestions]
  >
  > Options:
  > - **Fix** — I'll amend the spec to address these, then re-review
  > - **Override** — proceed to planning as-is (you accept the risk)
  > - **Abort** — stop the workflow"
  
  - If **Fix**: re-dispatch Step 1 with arch-review findings as correction input, loop back to Step 1.5
  - If **Override**: proceed to Step 2
  - If **Abort**: halt workflow

- **RETHINK** → halt and report:
  > "Arch review flagged structural issues that need rethinking before implementation can proceed. Run `/arch-review` for the full report, then revise the spec."

---

### Step 2: Invoke /plan

Invoke the `/plan` skill. It will auto-detect:
- Change-brief from `docs/plan/current` → `{PLAN_DIR}/change-brief.md`
- Spec from `docs/spec.md`
- Output directory from `docs/plan/current`

**After /plan completes:** Verify `{PLAN_DIR}/plan.md` exists.

---

### Step 3: Invoke /tasks

Invoke the `/tasks` skill. It will auto-detect:
- Plan from `docs/plan/current` → `{PLAN_DIR}/plan.md`
- Spec from `docs/spec.md`

**After /tasks completes:** Verify `{PLAN_DIR}/tasks.md` exists.

---

### Step 4: Invoke /implement

Invoke the `/implement` skill with the strategy flag: `{STRATEGY}` (from parsed arguments, default `--sequential`).

**After /implement completes:** Check if it reported success. If failures → halt and report to user.

---

### Step 5: Invoke /quality-gate (unless --no-quality-gate)

Invoke the `/quality-gate` skill with `local --fix`.

**After /quality-gate completes:** If it reports unfixable FAIL → halt and report to user.

---

### Step 6: Invoke /ship

Invoke the `/ship` skill. It will:
- Detect we're on a feature branch
- Detect no PR exists yet → create a new one
- Use Jira ID from branch name and change-brief for PR description

**After /ship completes:** Capture the PR URL from its output.

---

### Step 7: Report

```
Improvement Workflow Complete
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Change:   {CHANGE_INPUT summary}
Jira:     {JIRA_ID}
Branch:   {BRANCH_NAME}
PR:       {PR_URL}

Pipeline:
  Improvement:  {Spec amended | Implementation-only} → change-brief.md
  Plan dir:     docs/plan/{DATE}_{JIRA_ID}/
  Plan:         {PLAN_DIR}/plan.md
  Tasks:        N tasks across M phases
  Implement:    All tasks completed
  Quality:      Tests passed, analysis clean
  Ship:         PR created → {PR_URL}

Next: Share the PR for review. If review comments come back, use `/pr-iterate {PR_URL}` to address them.
```

---

## Rules

1. **Step 1 is the only inline agent** — all other steps invoke standalone skills
2. **Always branch from master** — `git checkout master && git pull && git checkout -b {branch}` at the start
3. **Branch name = Jira ID** (lowercase) — always prompt for Jira ID if not provided
4. **Halt on failure** — if any skill reports failure, stop and report to user
5. **Artifacts are per-ticket** — each run writes to `docs/plan/{DATE}_{JIRA_ID}/`. Nothing is overwritten across runs.
6. **Spec changes are valid** — if the improvement agent amends spec.md, that IS committed
7. **Connects to /pr-iterate** — the final report tells the user to use `/pr-iterate` for the next review cycle
