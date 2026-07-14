---
name: ship
description: "Commit changes and ship to PR. Detects whether a PR exists: creates one if not, pushes to existing if so. Handles Jira context, PR description, and comment replies automatically."
argument-hint: "[optional: JIRA-ID or summary override]"
---

# Ship: Context-Aware Commit & PR

Commits current changes and ships them. Detects whether a PR already exists for the current branch and acts accordingly.

## Pre-flight

1. **Confirm branch**:
   - Run `git rev-parse --abbrev-ref HEAD`
   - If on `master` or `main` → HALT: "Cannot ship from main. Create or switch to a feature branch first."

2. **Resolve Jira ID** (best-effort, all optional):
   - From `$ARGUMENTS` if provided
   - Else: parse from branch name (e.g., `COMPX-123` or `compx-123`)
   - Else: read `docs/plan/current` → check `docs/plan/{current}/.jira-context`
   - Else: glob `docs/plan/*/.jira-context` (most recently modified)
   - If none found: proceed without Jira ID (use diff summary instead)

3. **Resolve PLAN_DIR** (best-effort):
   - If `docs/plan/current` exists → `docs/plan/{contents of current}/`
   - Else: glob `docs/plan/*/change-brief.md` (most recently modified)
   - May be empty — that's fine

## Execute

### Step 1: Stage and Commit

1. Run `git status` to see what's changed
2. Stage changes:
   - `git add` all modified/new source and test files
   - Do NOT stage `docs/plan/` artifacts (change-brief, plan, tasks, decisions, research are working docs)
   - DO stage `docs/spec.md` if modified
   - DO stage `docs/constitution.md` if modified
3. If nothing to commit → skip to Step 2 (there may be unpushed commits)
4. Commit:
   - If Jira ID available:
     ```
     {JIRA_ID}: {short summary from change-brief or diff}
     ```
   - If no Jira ID:
     ```
     {short summary derived from staged diff}
     ```

### Step 2: Detect PR State

Run: `gh pr view --json url,number,state 2>/dev/null`

- **If command fails (no PR exists)** → go to Step 3A (Create PR)
- **If PR exists and state is OPEN** → go to Step 3B (Update PR)
- **If PR exists and state is MERGED/CLOSED** → HALT: "PR already {state}. Create a new branch for further work."

### Step 3A: Create New PR

1. Push: `git push -u origin {BRANCH_NAME}`

2. Gather PR description context:
   - If `{PLAN_DIR}/change-brief.md` exists → use it for summary
   - Else if Jira MCP tool available → fetch ticket summary
   - Else → summarize from `git diff main...HEAD`

3. Identify changes:
   - Functional changes: files in `src/main/` (or equivalent source dirs)
   - Test coverage: files in `src/test/` (or equivalent test dirs)

4. Create PR:
   ```
   gh pr create --base master --title "{TITLE}" --body "{BODY}"
   ```
   - Title: `{JIRA_ID}: {short summary}` (or just summary if no Jira ID)
   - Body format:

   ```markdown
   ## Summary

   {1-3 sentences from change-brief or diff summary}

   ## Jira

   [{JIRA_ID}]({JIRA_URL}) (omit this section if no Jira ID)

   ## Changes

   ### Functional
   - {list of functional changes}

   ### Test Coverage
   - {list of tests added/modified}

   ## Verification

   - [ ] Build passes
   - [ ] All new code has test coverage
   - [ ] No regressions in existing tests
   ```

5. If source files changed but no test files changed → append:
   > ⚠ No test coverage added for functional changes

### Step 3B: Update Existing PR

1. Push: `git push origin {BRANCH_NAME}`

2. Reply to addressed comments (if applicable):
   - If `{PLAN_DIR}/.pr-iterate-state.json` exists:
     - Read it, find comment IDs marked as accepted in the latest iteration that haven't been replied to yet
     - Get the just-pushed commit SHA: `git rev-parse HEAD`
     - For each addressed comment ID:
       ```
       gh api repos/{OWNER}/{REPO}/pulls/{PR_NUMBER}/comments \
         --method POST \
         --field body="Addressed in {COMMIT_SHA}" \
         --field in_reply_to={COMMENT_ID}
       ```
   - If no state file → skip (nothing to reply to)

3. Update state file:
   - If `.pr-iterate-state.json` exists: append this iteration (timestamp, commit SHA, replied comment IDs)
   - If not: skip (don't create one for manual pushes)

## Report

Output a summary:
```
Shipped to: {PR_URL}
Branch:     {BRANCH_NAME}
Action:     {Created new PR | Pushed to existing PR}
Commit:     {SHORT_SHA} — {commit message first line}
Comments:   {N replies posted (if any)}
```

## Rules

- **Never push to main directly** — halt if on main
- **Never force-push** — always regular push
- **Skip docs/plan/ from staging** — these are working artifacts
- **PR title starts with Jira ID** when available
- **Flag missing test coverage** on new PRs with source changes
