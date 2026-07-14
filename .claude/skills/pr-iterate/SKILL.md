---
name: pr-iterate
description: "One-command PR review-response cycle: fetches unresolved comments, triages, plans fixes, generates tasks, implements, runs quality gate, and pushes to the existing PR branch. Invoke once per round of reviewer feedback."
argument-hint: "<PR URL or number> [--sequential|--parallel] [--no-quality-gate] [--dry-run]"
---

# PR Iterate: Automated Review-Response Cycle

Thin orchestrator — handles PR comment triage (unique to this workflow), then delegates planning through shipping to standalone skills. Artifacts on disk are the handoff between steps.

## Parse Arguments

From `$ARGUMENTS`:
- PR URL (`https://github.com/owner/repo/pull/N`) or bare number → required
- `--sequential` → pass to implement step (default)
- `--parallel` → pass to implement step
- `--no-quality-gate` → skip quality gate step
- `--dry-run` → run improvement/triage only, stop after change-brief

If no PR URL/number provided: ask "What's the PR URL or number?"

## Pre-flight: Locate Plan Directory and Checkout Branch

1. **Get PR branch name:** `gh pr view {PR_NUMBER} --json headRefName --jq '.headRefName'`
2. **Resolve Jira ID:** normalize branch name to uppercase (e.g., `compx-26874` → `COMPX-26874`)
3. **Find plan directory:** glob `docs/plan/*_{JIRA_ID}/` — use most recent if multiple exist
4. If found: `PLAN_DIR` = that path, write its name to `docs/plan/current`
5. If not found: compute `DATE=$(date +%Y-%m-%d)`, create `docs/plan/{DATE}_{JIRA_ID}/`, write `docs/plan/current`

6. **Check for dirty working tree:**
   - Run: `git status --porcelain`
   - If non-empty:
     > "Your working tree has uncommitted changes. Please choose:
     > - **Stash** — I'll run `git stash` and restore after the workflow
     > - **Commit** — commit the changes yourself, then re-run `/pr-iterate`
     > - **Abort** — cancel the workflow"
     - If **Stash**: run `git stash`, set `STASHED=true`
     - If **Commit** or **Abort**: halt workflow
   - If empty: proceed

7. **Checkout PR branch:**
   - `git fetch origin {PR_BRANCH}`
   - `git checkout {PR_BRANCH}`
   - If checkout fails: halt and report error

8. Report: "Checked out branch `{PR_BRANCH}`. Proceeding to triage."

## Execute

### Step 1: Dispatch PR Comment Triage Agent

Dispatch an Agent with the following prompt:

```
You are running the /improvement skill for a PR review iteration.

## Input

PR URL: {PR_URL}
Project root: {PROJECT_ROOT}
Plan directory: {PLAN_DIR}

## Instructions

1. Read `docs/spec.md` at {PROJECT_ROOT}/docs/spec.md (if it exists)

2. Fetch PR data:
   - Parse the PR number and repo from the URL
   - For GitHub Enterprise URLs (not github.com), set GH_HOST to the hostname before gh commands
   - `gh pr view <N> --json title,body,state,headRefName,baseRefName`
   - `gh api repos/<owner>/<repo>/pulls/<N>/reviews --jq '.[].body'`
   - `gh api repos/<owner>/<repo>/pulls/<N>/comments --jq '.[] | {id: .id, path: .path, line: (.line // .original_line), body: .body, in_reply_to_id: .in_reply_to_id}'`
   - Write `{PLAN_DIR}/.pr-context` with the PR URL (single line)

3. Check for prior state:
   - If `{PLAN_DIR}/.pr-iterate-state.json` exists, read it
   - Extract the list of previously addressed comment IDs
   - Skip any comments whose ID appears in the state file

4. Triage each NEW review comment:
   - Read the actual code at the referenced file:line
   - Classify as Accept, Reject, or Discuss
   - For Reject: reply to the PR comment via `gh api repos/<owner>/<repo>/pulls/<N>/comments --method POST --field body="[Reject]: <reasoning>" --field in_reply_to=<comment_id>`
   - For Discuss: reply similarly with analysis + question
   - Only Accepted findings proceed

5. If ALL findings rejected or no new comments: write a summary and stop

6. Assess impact:
   - If change affects spec → amend docs/spec.md
   - If implementation-only → write {PLAN_DIR}/change-brief.md

## Output

Report a single summary line:
"Triaged N comments: X accepted, Y rejected, Z discuss. [Change-brief written to {PLAN_DIR}/change-brief.md | Spec amended]"
```

**After Agent 1 completes:**
- If report says "0 accepted" or "no new comments" → stop, report to user
- If `--dry-run` → stop, report triage results
- Verify `{PLAN_DIR}/change-brief.md` exists (or spec was amended)

---

### Step 1.5: User Review Gate

1. Show the user what was produced:
   - Display triage summary (accepted/rejected/discuss counts)
   - If spec amended: display `git diff docs/spec.md`
   - Display contents of `{PLAN_DIR}/change-brief.md`

2. Ask for confirmation:
   > "Here's my understanding of the PR review feedback:
   >
   > [Show triage summary]
   > [Show spec diff if amended]
   > [Show change-brief contents]
   >
   > Does this accurately capture the changes needed?
   >
   > Options:
   > - **Yes** — proceed to planning
   > - **No** — tell me what's wrong and I'll revise"

3. If user says No → re-dispatch Step 1 with user's correction, loop back here
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

- **RETHINK** → halt and report

---

### Step 2: Invoke /plan

Invoke the `/plan` skill. It will auto-detect inputs via `docs/plan/current`.

**After /plan completes:** Verify `{PLAN_DIR}/plan.md` exists.

---

### Step 3: Invoke /tasks

Invoke the `/tasks` skill. It will auto-detect inputs via `docs/plan/current`.

**After /tasks completes:** Verify `{PLAN_DIR}/tasks.md` exists.

---

### Step 4: Invoke /implement

Invoke the `/implement` skill with the strategy flag: `{STRATEGY}` (default `--sequential`).

**After /implement completes:** If failures → halt and report to user.

---

### Step 5: Invoke /quality-gate (unless --no-quality-gate)

Invoke the `/quality-gate` skill with `local --fix`.

**After /quality-gate completes:** If FAIL → halt and report to user.

---

### Step 6: Invoke /ship

Invoke the `/ship` skill. It will:
- Detect we're on the PR branch
- Detect an existing PR → push + reply to addressed comments
- Use `.pr-iterate-state.json` for comment replies

**After /ship completes:** Capture the output.

---

### Step 7: Report

```
PR Iterate Complete
━━━━━━━━━━━━━━━━━━
PR:       {PR_URL}
Branch:   {PR_BRANCH}
Commit:   {SHA from /ship output}

Pipeline:
  Triage:     {X accepted, Y rejected, Z discuss}
  Plan dir:   {PLAN_DIR}
  Plan:       {PLAN_DIR}/plan.md
  Tasks:      N tasks across M phases
  Implement:  All tasks completed
  Quality:    Tests passed, analysis clean
  Ship:       Pushed to {PR_BRANCH}

Next: Wait for reviewer response. Run `/pr-iterate {PR_URL}` again after new comments.
```

**Post-flight:** If `STASHED=true` → run `git stash pop` and report: "Restored stashed changes."

---

## Rules

1. **Step 1 is the only inline agent** — all other steps invoke standalone skills
2. **Never create a new PR** — `/ship` detects the existing PR and pushes to it
3. **Halt on failure** — if any skill reports failure, stop and report to user
4. **State is cumulative** — `.pr-iterate-state.json` tracks all iterations
5. **Triage is mandatory** — read code, classify, reply to rejected/discuss comments
6. **GHE-aware** — for non-github.com URLs, set `GH_HOST=<hostname>` before `gh` commands
7. **Artifacts are per-ticket** — change-brief.md, plan.md, tasks.md are overwritten within the same ticket directory per iteration
