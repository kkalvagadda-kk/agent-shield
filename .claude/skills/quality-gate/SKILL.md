---
name: quality-gate
description: "Autonomous quality gate — runs tests, dispatches parallel review agents, fixes findings, re-tests until clean. Works on local changes (git diff) or remote PRs (gh pr). Use after implementation to validate before shipping."
argument-hint: "[local|pr <number>] [--fix] [--strict] [--no-test] [--comment]"
---

# Quality Gate

Thin orchestrator — parses intent, runs tests, dispatches review agents, consolidates, reports.

## Parse Arguments

From `$ARGUMENTS`, determine:
- **source**: `local` (default) or `pr <number>`
- **auto_fix**: `--fix` present (default: yes for local, no for PR)
- **strict**: `--strict` present (fail on Important+)
- **skip_tests**: `--no-test` present
- **post_comments**: `--comment` present (PR mode only)

## Execute

1. **Run tests** (unless `--no-test`):
   -    Auto-detect by checking project root for build files (first match wins):                                                                                                  
     1. **README.md** — scan for a "Build and Test" or "Testing" section; use the documented test command if present                                                           
     2. **pom.xml** → `mvn test` (or `mvn verify` if integration tests exist under `src/test/`)                                                                                
     3. **build.gradle** / **build.gradle.kts** → `./gradlew test`                                                                                                             
     4. **Makefile** with `test` target → `make test`                                                                                                                          
     5. **package.json** with `"test"` script → `npm test`                                                                                                                     
     6. **Cargo.toml** → `cargo test`                                                                                                                                          
     7. **pyproject.toml** / **setup.py** / **tests/** dir → `python -m pytest tests/ -x --tb=short`                                                                           
     8. **go.mod** → `go test ./...`                                                                                                                                           
     9. **mix.exs** → `mix test`
   - If tests fail → report failures, STOP. Do not review broken code.

2. **Get the diff**:
   - Local: `git diff` (or `git diff HEAD~1..HEAD` if clean)
   - PR: `gh pr diff <number>` + `gh pr view <number> --json title,body`

3. **Dispatch 3 review agents in parallel** (use Agent tool with `subagent_type: "general-purpose"`):
   - Agent 1: Correctness reviewer (see `agents/correctness.md`)
   - Agent 2: Quality reviewer (see `agents/quality.md`)
   - Agent 3: Security reviewer (see `agents/security.md`)
   - Pass each agent: the diff content, list of files changed, CLAUDE.md path

4. **Consolidate** results from all 3 agents:
   - Filter: drop findings with confidence < 80
   - Deduplicate: same file:line → keep higher confidence
   - Rank: Critical → Important → Minor
   - Classify: fixable vs needs-human-judgment

5. **Decision gate**:
   - No findings ≥80 → **PASS**
   - Only Minor → **PASS** (report as suggestions)
   - Important (default mode) → **PASS WITH NOTES**
   - Important (strict mode) → **FAIL**
   - Critical → **FAIL**

6. **Auto-fix** (if `--fix` and fixable findings):
   - Apply fixes for Critical + Important fixable findings
   - Loop back to step 1 (re-test)
   - Max 3 iterations

7. **Report verdict** to user (see output format below)

8. **Post PR comments** (if `--comment` and PR mode):
   - `gh pr review <number> --comment --body "<findings>"`

## Output Format

**PASS**:
```
## Quality Gate: PASS ✓
Mode: [local|pr #N] [flags]
Tests: [N passed (Xs)]
Review: 3 agents, [N] findings above threshold
[Fix iterations: N (if --fix applied changes)]
Ready to ship.
```

**FAIL**:
```
## Quality Gate: FAIL ✗
Mode: [local|pr #N] [flags]
Tests: [pass/fail summary]
[Critical/Important findings with file:line, issue, fix suggestion]
Action required: [what to do]
```

## PR Mode Extras

When source is `pr`:
- Check PR description (explains what/why?)
- Check diff size (>500 lines → suggest splitting)
- Check test coverage (new code paths tested?)

## Key Rules

- Tests MUST pass before review starts
- Only report confidence ≥ 80
- Max 3 fix iterations (prevent infinite loop)
- Critical blocks shipping in all modes
- Never auto-fix "needs human judgment" findings
