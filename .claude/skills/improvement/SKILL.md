---
name: improvement
description: "Assess a change (feature, bug fix, improvement) against existing docs/spec.md. Amends spec if the change affects design/requirements, or writes a scoped change-brief if it's implementation-only. Entry point for all incremental changes."
argument-hint: "Change description, Jira ticket reference, PR URL/number, or file path to change request"
---

# Improvement: Incremental Change Assessment

You are assessing whether a proposed change requires updates to the system's spec (`docs/spec.md`) or is purely an implementation-level fix. This is the entry point for ALL changes to an existing system — features, bug fixes, refactors, improvements.

## Input

From `$ARGUMENTS`:
- A description of the change ("add rate limiting to the API", "fix the login timeout bug")
- A Jira ticket reference (read the ticket for context)
- A PR reference (URL or `pr #N`) — fetches review comments as the change request
- A file path to a change request document
- (empty) → ask: "What change are you making?"

---

## Step 1: Load Context

1. **Read `docs/spec.md`** — the current system spec (requirements + architecture)
   - If it doesn't exist: "No spec found at `docs/spec.md`. For a new system, use `/arch-design` first. For a quick fix without a spec, go directly to `/plan` with your change description."

2. **Read the change input** — understand what's being proposed:
   - If Jira reference: fetch the ticket details (summary, description, acceptance criteria)
   - If PR reference (URL like `https://github.com/org/repo/pull/N`, or `pr #N`, or `PR-N`):
     1. Parse the PR number and repo (use GH_HOST env for GitHub Enterprise URLs)
     2. Fetch PR metadata: `gh pr view <N> --json title,body,state`
     3. Fetch review comments: `gh api repos/<owner>/<repo>/pulls/<N>/reviews --jq '.[].body'`
     4. Fetch inline review comments: `gh api repos/<owner>/<repo>/pulls/<N>/comments --jq '.[] | {id: .id, path: .path, line: (.line // .original_line), body: .body, in_reply_to_id: .in_reply_to_id}'`
     5. Persist PR context: write `docs/plan/.pr-context` with the PR URL (consumed by `/ship`)
     6. **Proceed to Step 1.5: Triage PR Comments** (do NOT blindly accept all comments)
   - If file path: read the document
   - If description: use as-is

3. **Clean up stale artifacts** — if `docs/plan/change-brief.md` exists from a previous run, delete it

---

## Step 1.5: Triage PR Comments (PR input only)

When the input is a PR reference, do NOT accept all review comments as valid. Critique each one.

### For each review comment/finding:

1. **Read the actual code** at the referenced file:line
2. **Reason about validity** — consider:
   - Is the issue real? Can you reproduce the scenario described?
   - Does the spec already cover this case (and the code violates it)?
   - Is this a false positive (e.g., the reviewer missed a guard elsewhere, or the framework handles it)?
   - Is this debatable (valid concern but the current approach is also defensible)?

3. **Classify into one of three buckets:**

| Verdict | Meaning | Action |
|---------|---------|--------|
| **Accept** | Issue is real, code needs to change | Include in change request for Step 2 |
| **Reject** | False positive or already handled | Reply to the PR comment explaining why |
| **Discuss** | Valid concern but multiple valid approaches | Reply to the PR comment with your reasoning and ask for input |

### Replying to PR comments:

For **Rejected** findings, reply to the specific inline comment:
```
gh api repos/<owner>/<repo>/pulls/<N>/comments --method POST \
  --field body="<reasoning>" \
  --field in_reply_to=<comment_id>
```

For **Discussed** findings, reply with your analysis and a question:
```
gh api repos/<owner>/<repo>/pulls/<N>/comments --method POST \
  --field body="<analysis + question>" \
  --field in_reply_to=<comment_id>
```

Reply format:
> **[Accept/Reject/Discuss]**: <1-2 sentence reasoning>
>
> <For Reject: explain what handles this — cite the file:line or spec section>
> <For Discuss: present the tradeoff and ask the question>

### After triage:

- Report a summary to the user:
  > "Triaged N review comments:
  > - Accepted: X (will fix)
  > - Rejected: Y (replied with reasoning)
  > - Discuss: Z (replied asking for input)
  >
  > Proceeding with the X accepted findings."

- Only the **Accepted** findings flow into Step 2 as the change request
- If ALL findings are rejected: report this and stop — no change-brief or spec change needed
- If some are "Discuss": proceed with Accepted findings now; revisit Discuss items if the reviewer responds

---

## Step 2: Assess Impact

Evaluate the proposed change against the spec:

**Does this change affect:**

| Area | Question | If YES → spec change needed |
|------|----------|---------------------------|
| Requirements | Does it add, remove, or modify what the system must do? | Yes |
| Architecture | Does it change components, boundaries, data flow, or key decisions? | Yes |
| Integrations | Does it add or modify external system interactions? | Yes |
| Constraints | Does it change non-functional requirements or compliance needs? | Yes |
| Scope | Does it bring something from "out of scope" into scope? | Yes |

**If ALL answers are NO** → this is an implementation-level change (bug fix, refactor, performance optimization where the spec is correct but the code is wrong).

---

## Step 3A: Spec Change Needed

If the change affects requirements, architecture, integrations, constraints, or scope:

1. **Report what's changing:**
   > "This change affects the spec:
   > - [Section]: [what's changing and why]
   > - [Section]: [what's changing and why]"

2. **Amend `docs/spec.md`:**
   - Read the current content
   - Make targeted edits to affected sections only
   - Do NOT rewrite unaffected sections
   - Add/modify requirements, update architecture decisions, adjust constraints
   - If adding new requirements, include acceptance criteria
   - Update the `**Date**` field to today

3. **Persist Jira context** (if a Jira ID was provided):
   - Write `docs/plan/.jira-context` with the Jira ID (single line, e.g., `ACV2-123`)
   - This is consumed by `/ship` for branch naming and PR title

4. **Report the delta:**
   > "Updated `docs/spec.md`:
   > - Added: [what was added]
   > - Modified: [what changed]
   > - Removed: [what was removed, if any]
   >
   > **Recommended next step**: `/arch-review` to validate the changes, then `/plan`"

---

## Step 3B: No Spec Change Needed

If the change is implementation-only (spec is correct, code needs fixing):

1. **Report the assessment:**
   > "Spec is current — this is an implementation-level change. The spec already describes the correct behavior; the code needs to match it."

2. **Write `docs/plan/change-brief.md`:**

```markdown
# Change Brief

**Date**: [YYYY-MM-DD]
**Jira**: [JIRA-ID] (e.g., ACV2-123 — used by /ship for branch name and PR title)
**PR**: [PR URL] (if sourced from PR review comments)
**Source**: [Jira ticket / PR review / user description / file reference]

## What's Changing

[1-3 sentences: what's wrong or what needs to improve]

## Why

[Root cause or motivation — why this change is needed]

## Scope

[Which area of the codebase is affected — packages, modules, layers]

## Spec Reference

[Which section of docs/spec.md describes the correct behavior]
[Quote the relevant requirement or architectural decision]

## Acceptance Criteria

- [ ] [Criterion 1: how we know the fix/improvement works]
- [ ] [Criterion 2]

## Out of Scope

- [What this change does NOT touch]
```

3. **Report:**
   > "Change brief written to `docs/plan/change-brief.md`.
   >
   > **Next step**: `/plan` (will auto-detect the change brief)"

---

## Decision Tree Summary

```
Input: Change description / Jira ref / PR ref
         │
         ▼
   Detect input type:
   ├─ PR URL/number → fetch review comments
   │       │
   │       ▼
   │   Triage each comment:
   │   ├─ Accept → include in change request
   │   ├─ Reject → reply to PR comment (explain why)
   │   └─ Discuss → reply to PR comment (ask for input)
   │       │
   │       ▼ (Accepted findings only)
   ├─ Jira ref → fetch ticket details
   ├─ File path → read document
   └─ Description → use as-is
         │
         ▼
   Read docs/spec.md
         │
         ▼
   Does change affect requirements,
   architecture, integrations,
   constraints, or scope?
         │
    ┌────┴────┐
    │         │
   YES        NO
    │         │
    ▼         ▼
 Amend      Write
 spec.md    change-brief.md
    │         │
    ▼         ▼
 /arch-review  /plan
 (recommended)  (auto-detects brief)
    │
    ▼
  /plan
```

---

## Key Rules

1. **Never skip the assessment** — even if the change "obviously" doesn't need a spec update, go through the checklist
2. **Minimal edits** — when amending spec.md, change only what's affected. Don't reorganize or rewrite other sections.
3. **Always clean up** — delete stale `change-brief.md` before writing a new one
4. **Preserve history** — spec.md is versioned by git. Don't add changelog sections; the diff is the changelog.
5. **Quote the spec** — in change-brief.md, always reference which spec section describes the correct behavior. This grounds the plan.
6. **PR reviews as input** — when a PR reference is provided, DO NOT blindly accept all comments. Triage each one: read the actual code, reason about validity, classify as Accept/Reject/Discuss. Only accepted findings drive the assessment. Rejected/Discussed findings get a reply on the PR with reasoning.
7. **GitHub Enterprise** — for GHE URLs (e.g., `github.infra.cloudera.com`), set `GH_HOST` before running `gh` commands: `GH_HOST=<host> gh ...`
8. **Both outputs are valid** — a PR triage can result in spec changes AND PR replies in the same run. Some findings may reveal spec gaps (→ amend spec.md), others are implementation bugs (→ change-brief.md), and others are false positives (→ PR reply only).
