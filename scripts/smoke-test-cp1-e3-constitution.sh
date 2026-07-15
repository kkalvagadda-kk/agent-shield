#!/usr/bin/env bash
# scripts/smoke-test-cp1-e3-constitution.sh
#
# Eval v2 E-3 — CP1d: the NO-ORPHAN + CONSTITUTION sweep (e3/tasks.md T023 + T024).
#
# Pure source greps — no cluster needed, runs in seconds. This is the CLAUDE.md
# "Definition of Done" gate mechanised: every one of these checks exists because the
# corresponding failure has ALREADY happened on this project at least once.
#
#   NO-ORPHAN (T023) — "Build utility + not wiring it up = NOT done."
#     Every symbol E-3 introduced must have a live caller/reader in the same change:
#       _resolve_eval_mode, _assert_mode_compatible   (eval_runner.py — called by the
#                                                      launch door)
#       _run_scheduled_item, _call_score_api_scheduled (eval-runner main.py — called by
#                                                      run_eval's MODE=scheduled branch)
#       ScheduledItemEditor, buildScheduledItem       (DatasetsPage.tsx — mounted +
#                                                      called by the save path)
#       JobSpecEvidence                               (EvalResultsPage.tsx — rendered)
#     and the load-bearing data-flow one: eval_run_results.trigger_payload must have
#     BOTH a writer (the eval-runner scheduled branch) and a reader (Studio results).
#     A column with only a writer is a silent orphan — exactly what E-0 left behind and
#     E-3 was tasked to close.
#
#   CONSTITUTION (T024)
#     1. the three E-3 tags are IDENTICAL in scripts/deploy-cpe2e.sh and
#        charts/agentshield/values.yaml — the recurring "bumped one file only" failure
#        (the deploy uses `helm upgrade` with tags baked into values.yaml, so bumping
#        only deploy-cpe2e.sh leaves the chart pointing at the old image)
#     2. declarative-runner is bumped IFF sdk/agentshield_sdk/ changed — the runner
#        image pip-installs the SDK, and a stale runner made every E-1 trajectory score
#        0. Fails LOUDLY in the "SDK changed but runner not bumped" direction.
#     3. no new Alembic version file — E-3 owns NO migration (e3/tasks.md §R3)
#     4. docs/experience/playground.md was updated (it is a covered file: playground.py,
#        eval_runner.py, DatasetsPage.tsx, EvalResultsPage.tsx all trigger it)
#
# Usage: bash scripts/smoke-test-cp1-e3-constitution.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DEPLOY_SH="scripts/deploy-cpe2e.sh"
VALUES="charts/agentshield/values.yaml"

PASS=0; FAIL=0
ok()  { echo "PASS  $1  |  $2"; PASS=$((PASS+1)); }
bad() { echo "FAIL  $1  |  $2"; FAIL=$((FAIL+1)); }

echo "=== E-3 CP1d — no-orphan + constitution sweep ==="
echo ""

# ---------------------------------------------------------------------------
# T023 — NO ORPHANS
# ---------------------------------------------------------------------------
echo "--- T023: no-orphan greps ---"

# A symbol is an orphan when its ONLY occurrence is its own definition. Counting
# occurrences and requiring >= 2 (def + >=1 call) is the mechanical form of that.
check_caller() {
  local sym="$1" file="$2" label="$3"
  local n
  n=$(grep -c "$sym" "$file" 2>/dev/null || echo 0)
  if [ "${n:-0}" -ge 2 ]; then
    ok "T023 $sym has a live caller in $label" "$n occurrences in $file (def + caller)"
  else
    bad "T023 $sym has a live caller in $label" \
        "$n occurrence(s) in $file — definition with no caller is an ORPHAN"
  fi
}

check_caller "_resolve_eval_mode" "services/registry-api/routers/eval_runner.py" "the launch door"
check_caller "_assert_mode_compatible" "services/registry-api/routers/eval_runner.py" "the launch door"
check_caller "_run_scheduled_item" "services/eval-runner/main.py" "run_eval's MODE=scheduled branch"
check_caller "_call_score_api_scheduled" "services/eval-runner/main.py" "the scheduled item runner"
check_caller "ScheduledItemEditor" "studio/src/pages/DatasetsPage.tsx" "the dataset modal"
check_caller "buildScheduledItem" "studio/src/pages/DatasetsPage.tsx" "the save path"
check_caller "JobSpecEvidence" "studio/src/pages/EvalResultsPage.tsx" "the evidence panel"

# The load-bearing one: trigger_payload needs BOTH a writer and a reader. This is the
# orphan E-0 actually left (the column shipped with no reader at all), so it is
# asserted as a data flow across services, not as a symbol in one file.
WRITER=$(grep -c '"trigger_payload"' services/eval-runner/main.py 2>/dev/null || echo 0)
READER=$(grep -c 'trigger_payload' studio/src/pages/EvalResultsPage.tsx 2>/dev/null || echo 0)
if [ "${WRITER:-0}" -ge 1 ] && [ "${READER:-0}" -ge 1 ]; then
  ok "T023 eval_run_results.trigger_payload has BOTH a writer and a reader (the E-0 orphan is closed)" \
     "writer: eval-runner/main.py ($WRITER refs); reader: EvalResultsPage.tsx ($READER refs)"
else
  bad "T023 eval_run_results.trigger_payload has BOTH a writer and a reader" \
      "writer refs=$WRITER (eval-runner/main.py), reader refs=$READER (EvalResultsPage.tsx) — a column with only a writer is a silent orphan"
fi
echo ""

# ---------------------------------------------------------------------------
# T024 — CONSTITUTION
# ---------------------------------------------------------------------------
echo "--- T024: constitution sweep ---"

tag_of() { grep -E "^$1=" "$DEPLOY_SH" | head -1 | cut -d'"' -f2; }
REGISTRY_API_TAG=$(tag_of REGISTRY_API_TAG)
STUDIO_TAG=$(tag_of STUDIO_TAG)
EVAL_RUNNER_TAG=$(tag_of EVAL_RUNNER_TAG)
DECLARATIVE_RUNNER_TAG=$(tag_of DECLARATIVE_RUNNER_TAG)

# 1. both files agree, per image.
#
# values.yaml expresses tags THREE different ways, so a single grep cannot read them:
#   - registry-api / studio  → a `repository:`/`tag:` PAIR inside an `image:` block
#   - eval-runner            → one inline `evalRunnerImage: "<repo>:<tag>"`
#   - declarative-runner     → a bare `declarativeRunnerTag: "<tag>"`
# Parsing the repository→tag pairing (rather than grepping for the tag string anywhere)
# is what makes this check real: a loose grep would happily match an unrelated
# component that coincidentally shares the version number and report agreement that
# is not there.
TAG_REPORT=$(python3 - "$VALUES" "$REGISTRY_API_TAG" "$STUDIO_TAG" "$EVAL_RUNNER_TAG" "$DECLARATIVE_RUNNER_TAG" <<'PY'
import re, sys
values, want_api, want_studio, want_eval, want_decl = sys.argv[1:6]
lines = open(values).read().splitlines()

def paired_tag(repo_suffix):
    """The `tag:` that belongs to the `repository:` ending in repo_suffix — read from
    the same image block, not from anywhere in the file."""
    for i, ln in enumerate(lines):
        if re.match(r"\s*repository:\s*\S*" + re.escape(repo_suffix) + r"\s*$", ln):
            for nxt in lines[i + 1:i + 6]:
                m = re.match(r'\s*tag:\s*"?([^"\s]+)"?\s*$', nxt)
                if m:
                    return m.group(1)
    return None

def inline_tag(key):
    for ln in lines:
        m = re.match(r'\s*' + re.escape(key) + r':\s*"?([^"\s]+)"?\s*$', ln)
        if m:
            return m.group(1)
    return None

api = paired_tag("/registry-api")
studio = paired_tag("/studio")
ev = inline_tag("evalRunnerImage")
ev = ev.rsplit(":", 1)[-1] if ev else None
decl = inline_tag("declarativeRunnerTag")

for label, got, want in (("registry-api", api, want_api), ("studio", studio, want_studio),
                         ("eval-runner", ev, want_eval),
                         ("declarative-runner", decl, want_decl)):
    print(f"{label}|{got}|{want}|{'OK' if got == want else 'MISMATCH'}")
PY
)
while IFS='|' read -r label got want verdict; do
  [ -z "$label" ] && continue
  if [ "$verdict" = "OK" ]; then
    ok "T024 $label tag agrees in BOTH deploy-cpe2e.sh and values.yaml" "$label=$want"
  else
    bad "T024 $label tag agrees in BOTH deploy-cpe2e.sh and values.yaml" \
        "deploy-cpe2e.sh says '$want' but values.yaml says '$got' — the deploy bakes tags from values.yaml, so bumping only deploy-cpe2e.sh leaves the chart on the old image"
  fi
done <<< "$TAG_REPORT"

# 2. declarative-runner bumped IFF the SDK changed. `git diff` against the merge-base
#    with origin/main is the change under review; fall back to the working tree.
BASE=$(git merge-base HEAD origin/main 2>/dev/null || echo "")
if [ -n "$BASE" ]; then
  CHANGED=$(git diff --name-only "$BASE"...HEAD 2>/dev/null; git diff --name-only 2>/dev/null)
else
  CHANGED=$(git diff --name-only 2>/dev/null || true)
fi
SDK_CHANGED=$(echo "$CHANGED" | grep -c "^sdk/agentshield_sdk/" || true)
RUNNER_BUMPED=$(echo "$CHANGED" | grep -c "deploy-cpe2e.sh" || true)
if [ "${SDK_CHANGED:-0}" -eq 0 ]; then
  ok "T024 declarative-runner untouched at $DECLARATIVE_RUNNER_TAG (no sdk/agentshield_sdk/ change — e3/tasks.md §R6)" \
     "sdk files changed: 0"
else
  bad "T024 sdk/agentshield_sdk/ CHANGED — declarative-runner MUST be bumped in BOTH files" \
      "sdk files changed: $SDK_CHANGED. The runner image pip-installs the SDK; a stale runner made every E-1 trajectory score 0. Bump DECLARATIVE_RUNNER_TAG (currently $DECLARATIVE_RUNNER_TAG) in deploy-cpe2e.sh AND values.yaml."
fi

# 2b. THE CLASS FIX: a changed service directory MUST come with a changed tag.
#
# This check exists because E-3 shipped without it and the whole runtime went
# undeployed. The two checks above cover:
#   - "bumped one file only"          → the tag-agreement check
#   - "cluster != the tag files"      → smoke-test-cp1-e3-infra.sh
# Neither covers the case that actually bit: **the source changed and the tag was never
# bumped at all**. Both files then agree on a stale tag and the cluster faithfully
# matches it — every check green, code not deployed. (The E-3 commit bumped registry-api
# but not eval-runner or studio; the eval Jobs ran E-2-era code, silently fell through to
# the reactive path, and the "recorded ⇒ not delivered" guarantee was simply absent.)
#
# So: diff the service directories against the tag lines. If a service's code changed and
# its tag did not, fail by name. This is the check that ties the two together.
# AUDIT_REF is the change under review — the commit that is supposed to carry E-3's
# bumps. Default HEAD. Uncommitted work is reported separately (informational): it is
# not shippable yet, and folding it in here made this check fire on unrelated
# work-in-progress from another slice.
AUDIT_REF="${AUDIT_REF:-HEAD}"
echo ""
echo "  service-dir ⇄ tag coupling on $AUDIT_REF ($(git log -1 --format=%h%x20%s "$AUDIT_REF" 2>/dev/null | cut -c1-64)):"
AUDIT_FILES=$(git show --pretty=format: --name-only "$AUDIT_REF" 2>/dev/null || true)
AUDIT_TAGS=$(git show "$AUDIT_REF" -- "$DEPLOY_SH" 2>/dev/null || true)

check_coupling() {
  local dir="$1" tagvar="$2" label="$3"
  local code_changed tag_changed
  code_changed=$(echo "$AUDIT_FILES" | grep -c "^${dir}" || true)
  tag_changed=$(echo "$AUDIT_TAGS" | grep -c "^+${tagvar}=" || true)
  if [ "${code_changed:-0}" -gt 0 ] && [ "${tag_changed:-0}" -eq 0 ]; then
    bad "T024 $label: code changed ⇒ $tagvar MUST be bumped" \
        "$code_changed file(s) changed under $dir but $tagvar was NOT bumped in $AUDIT_REF — the image is never rebuilt, so the cluster keeps running the OLD code while every other check stays GREEN (both tag files agree on the stale tag, and the cluster faithfully matches it)"
  elif [ "${code_changed:-0}" -gt 0 ]; then
    ok "T024 $label: code changed AND $tagvar bumped in $AUDIT_REF" "$code_changed file(s) under $dir"
  else
    ok "T024 $label: no code change under $dir in $AUDIT_REF (no bump required)" "0 files"
  fi
}
check_coupling "services/registry-api/" "REGISTRY_API_TAG" "registry-api"
check_coupling "services/eval-runner/" "EVAL_RUNNER_TAG" "eval-runner"
check_coupling "studio/src/" "STUDIO_TAG" "studio"
check_coupling "sdk/agentshield_sdk/" "DECLARATIVE_RUNNER_TAG" "declarative-runner (SDK)"

# Uncommitted service code is a WARNING, not a failure: it cannot be deployed yet, so it
# is not a broken promise — but it will need a bump before it ships.
for pair in "services/registry-api/:REGISTRY_API_TAG" "services/eval-runner/:EVAL_RUNNER_TAG" \
            "studio/src/:STUDIO_TAG" "sdk/agentshield_sdk/:DECLARATIVE_RUNNER_TAG"; do
  d="${pair%%:*}"; tv="${pair#*:}"
  n=$(git diff --name-only -- "$d" 2>/dev/null | grep -c . || true)
  [ "${n:-0}" -gt 0 ] && echo "  NOTE  $n uncommitted file(s) under $d — $tv must be bumped before this ships"
done

# 3. E-3 owns no migration (R3): no new alembic version file in the change.
NEW_MIG=$(echo "$CHANGED" | grep "services/registry-api/alembic/versions/" || true)
if [ -z "$NEW_MIG" ]; then
  ok "T024 no new Alembic version file (E-3 owns NO migration — e3/tasks.md §R3, head stays 0063)" \
     "alembic/versions/ untouched"
else
  bad "T024 no new Alembic version file (E-3 owns NO migration — e3/tasks.md §R3)" \
      "FOUND: $(echo "$NEW_MIG" | tr '\n' ' ')"
fi

# 4. the experience doc is updated — playground.py / eval_runner.py / DatasetsPage.tsx /
#    EvalResultsPage.tsx are all CLAUDE.md-covered files.
if git diff --name-only HEAD 2>/dev/null | grep -q "docs/experience/playground.md" \
   || echo "$CHANGED" | grep -q "docs/experience/playground.md"; then
  ok "T024 docs/experience/playground.md updated (a covered file changed — CLAUDE.md §3)" \
     "playground.md is in the change"
else
  bad "T024 docs/experience/playground.md updated (a covered file changed — CLAUDE.md §3)" \
      "playground.md NOT in the change, but E-3 touches playground.py / eval_runner.py / DatasetsPage.tsx / EvalResultsPage.tsx"
fi

echo ""
echo "=== E-3 CP1d sweep: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -ne 0 ]; then
  echo "❌ E-3 CP1d sweep FAILED"
  exit 1
fi
echo "✅ E-3 CP1d sweep PASSED (no orphans; both tag files agree; no migration; docs updated)"
