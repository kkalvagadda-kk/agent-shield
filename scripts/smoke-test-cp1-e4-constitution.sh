#!/usr/bin/env bash
# scripts/smoke-test-cp1-e4-constitution.sh
#
# Eval v2 E-4 — [CP1d] + [CP1e]: the NO-ORPHAN + CONSTITUTION sweep.
#
# Pure source greps — no cluster needed, runs in seconds. This is the CLAUDE.md
# "Definition of Done" gate mechanised for E-4. Every check here exists because the
# corresponding failure has ALREADY happened on this project at least once.
#
#   NO-ORPHAN [CP1d] — "Build utility + not wiring it up = NOT done."
#     Every symbol E-4 introduced must have a live caller/reader in the same change:
#       score_filter, score_injection      (judge.py — called by the score door's
#                                           mode=webhook branch)
#       InjectionProbe                     (schemas.py — in the WebhookDatasetItem)
#       _create_and_dispatch_playground_run(playground.py — EXACTLY 1 def + 2 call
#                                           sites: create_playground_run + test_event)
#       _webhook_driving_message           (playground.py — called by test_event)
#       _run_webhook_item                  (eval-runner main.py — in the handler map)
#       _resolve_item_handler              (eval-runner main.py — called by run_eval)
#       _run_reactive_item                 (eval-runner main.py — REGISTERED in the map,
#                                           not a fallthrough tail)
#       WebhookItemEditor, buildWebhookItem(DatasetsPage.tsx — mounted + called on save)
#       FilterVerdict, InjectionEvidence   (EvalResultsPage.tsx — rendered)
#
#     THE HEADLINE ORPHAN: `eval_run_results.matched` shipped with E-0 and had
#     NEITHER a writer NOR a reader. E-4 closes it — writer = the eval-runner's webhook
#     branch, reader = EvalResultsPage's filter verdict. Asserted as a data flow across
#     services, not as a symbol in one file.
#
#   CONSTITUTION [CP1e]
#     1. the three E-4 tags are IDENTICAL in scripts/deploy-cpe2e.sh and
#        charts/agentshield/values.yaml — including eval-runner's THIRD pin
#        (registry-api.env.EVAL_RUNNER_IMAGE, which is what actually launches the Job;
#        miss it and the eval Job runs the OLD runner while every other check stays
#        green)
#     2. declarative-runner / event-gateway untouched IFF their source is untouched
#     3. no new Alembic version file — E-4 owns NO migration (head stays 0064)
#     4. docs/experience/playground.md updated (a covered file changed)
#     5. E-4 adds NO new filter code and NO eval-only filter fork; the filter-engine
#        parity gate is still wired into deploy-cpe2e.sh (this is what makes E-4 honest
#        — without it the eval grades a filter production never runs)
#     6. the runner FAIL-CLOSES on an unhandled MODE (no reactive fallthrough tail)
#
# Usage: bash scripts/smoke-test-cp1-e4-constitution.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DEPLOY_SH="scripts/deploy-cpe2e.sh"
VALUES="charts/agentshield/values.yaml"

PASS=0; FAIL=0
ok()  { echo "PASS  $1  |  $2"; PASS=$((PASS+1)); }
bad() { echo "FAIL  $1  |  $2"; FAIL=$((FAIL+1)); }

echo "=== E-4 CP1d/CP1e — no-orphan + constitution sweep ==="
echo ""

# ---------------------------------------------------------------------------
# CP1d — NO ORPHANS
# ---------------------------------------------------------------------------
echo "--- CP1d: no-orphan greps ---"

# A symbol is an orphan when its ONLY occurrence is its own definition. Counting
# occurrences and requiring >= 2 (def + >=1 call) is the mechanical form of that.
check_caller() {
  local sym="$1" file="$2" label="$3"
  local n
  n=$(grep -c "$sym" "$file" 2>/dev/null || true)
  if [ "${n:-0}" -ge 2 ]; then
    ok "CP1d $sym has a live caller in $label" "$n occurrences in $file (def + caller)"
  else
    bad "CP1d $sym has a live caller in $label" \
        "$n occurrence(s) in $file — a definition with no caller is an ORPHAN"
  fi
}

check_caller "score_filter" "services/registry-api/routers/playground.py" "the score door's webhook branch"
check_caller "score_injection" "services/registry-api/routers/playground.py" "the score door's webhook branch"
check_caller "InjectionProbe" "services/registry-api/schemas.py" "the WebhookDatasetItem"
check_caller "_webhook_driving_message" "services/registry-api/routers/playground.py" "test_event"
check_caller "_run_webhook_item" "services/eval-runner/main.py" "the mode->handler map"
check_caller "_resolve_item_handler" "services/eval-runner/main.py" "run_eval"
check_caller "_run_reactive_item" "services/eval-runner/main.py" "the mode->handler map"
check_caller "WebhookItemEditor" "studio/src/pages/DatasetsPage.tsx" "the dataset modal"
check_caller "buildWebhookItem" "studio/src/pages/DatasetsPage.tsx" "the save path"
check_caller "FilterVerdict" "studio/src/pages/EvalResultsPage.tsx" "the evidence panel"
check_caller "InjectionEvidence" "studio/src/pages/EvalResultsPage.tsx" "the evidence panel"

# D2 — EXACTLY one builder, exactly two call sites, exactly one construction.
# A second hand-built PlaygroundRun is the side-effecting-lost-on-declarative-runner-
# path.md failure mode reintroduced: it is how test-event silently dropped eval_mode
# (a matched webhook eval would have DELIVERED REAL SIDE EFFECTS) and never dispatched.
HELPER_DEF=$(grep -c "^async def _create_and_dispatch_playground_run" services/registry-api/routers/playground.py || true)
HELPER_USE=$(grep -c "_create_and_dispatch_playground_run" services/registry-api/routers/playground.py || true)
PGRUN_CTOR=$(grep -c "    run = PlaygroundRun(" services/registry-api/routers/playground.py || true)
if [ "$HELPER_DEF" = "1" ] && [ "${HELPER_USE:-0}" -ge 3 ] && [ "$PGRUN_CTOR" = "1" ]; then
  ok "CP1d D2: ONE run builder — 1 def + 2 call sites, and exactly ONE PlaygroundRun construction" \
     "defs=$HELPER_DEF occurrences=$HELPER_USE (def + 2 calls, plus doc mentions) PlaygroundRun(=$PGRUN_CTOR"
else
  bad "CP1d D2: ONE run builder" \
      "defs=$HELPER_DEF (want 1) occurrences=$HELPER_USE (want >=3) PlaygroundRun(=$PGRUN_CTOR (want 1 — a second construction is the drift bug class)"
fi

# THE HEADLINE ORPHAN: eval_run_results.matched shipped with E-0 with NEITHER a writer
# NOR a reader. Asserted as a data flow across services, not a symbol in one file.
M_WRITER=$(grep -c '"matched": matched' services/eval-runner/main.py 2>/dev/null || true)
M_API=$(grep -c "matched=body.matched" services/registry-api/routers/eval_runner.py 2>/dev/null || true)
M_READER=$(grep -c "r.matched\|matched={" studio/src/pages/EvalResultsPage.tsx 2>/dev/null || true)
if [ "${M_WRITER:-0}" -ge 1 ] && [ "${M_API:-0}" -ge 1 ] && [ "${M_READER:-0}" -ge 1 ]; then
  ok "CP1d eval_run_results.matched has BOTH a writer and a reader (the E-0 orphan is CLOSED)" \
     "runner writer=$M_WRITER, API persist=$M_API, Studio reader=$M_READER"
else
  bad "CP1d eval_run_results.matched has BOTH a writer and a reader" \
      "runner writer=$M_WRITER, API persist=$M_API, Studio reader=$M_READER — the E-0 orphan is NOT closed"
fi

# The injection dimension needs a chip, or it is scored and never seen.
if grep -q '"injection"' studio/src/pages/EvalResultsPage.tsx 2>/dev/null; then
  ok "CP1d the injection dimension is in EVAL_DIMENSIONS (scored AND rendered)" \
     "injection chip present in EvalResultsPage"
else
  bad "CP1d the injection dimension is in EVAL_DIMENSIONS" \
      "injection is scored by the door but has no chip — a scored dimension nobody can see"
fi

# TestEventRequest.eval_mode: produced by the runner, consumed by the door.
EM_PROD=$(grep -c '"eval_mode": eval_mode' services/eval-runner/main.py 2>/dev/null || true)
EM_CONS=$(grep -c "eval_mode=body.eval_mode" services/registry-api/routers/playground.py 2>/dev/null || true)
if [ "${EM_PROD:-0}" -ge 1 ] && [ "${EM_CONS:-0}" -ge 1 ]; then
  ok "CP1d TestEventRequest.eval_mode has a producer AND a consumer" \
     "runner sends it ($EM_PROD), test_event threads it to the shared builder ($EM_CONS)"
else
  bad "CP1d TestEventRequest.eval_mode has a producer AND a consumer" \
      "producer=$EM_PROD consumer=$EM_CONS — the record seam reads the PERSISTED column, so an unthreaded eval_mode means a matched eval DELIVERS FOR REAL"
fi

# ---------------------------------------------------------------------------
# CP1d — NO ROUTE IS BOUND TO A PRIVATE HELPER.
#
# A decorator always binds to the NEXT function. Inserting `_webhook_driving_message`
# between `@router.post("/test-event")` and `async def test_event` silently rebound the
# ROUTE to the helper: `POST /test-event` returned 200 + `json.dumps(request_body)` for
# every input, including a nonexistent agent, and `test_event` became unreachable. The
# pod started clean, `ast.parse` passed, the import succeeded, and EVERY static check
# stayed green — only suite-22 caught it.
#
# A `_private` helper directly under a route decorator is ALWAYS a mistake: handlers are
# public endpoint functions. This makes that specific mistake unrepresentable rather than
# relying on the next person noticing.
# ---------------------------------------------------------------------------
BAD_ROUTES=$(python3 - <<'PY'
import ast, pathlib, sys

bad = []
for path in sorted(pathlib.Path("services/registry-api/routers").glob("*.py")):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            # match @router.<method>(...) / @router.<method>
            f = dec.func if isinstance(dec, ast.Call) else dec
            if (isinstance(f, ast.Attribute)
                    and isinstance(f.value, ast.Name)
                    and f.value.id == "router"
                    and f.attr in ("get", "post", "put", "patch", "delete")
                    and node.name.startswith("_")):
                bad.append(f"{path.name}:{node.lineno} @router.{f.attr} -> {node.name}")
print("\n".join(bad))
PY
)
if [ -z "$BAD_ROUTES" ]; then
  ok "CP1d no route decorator is bound to a _private helper (a decorator binds to the NEXT function)" \
     "every @router.* handler in routers/ is a public endpoint function"
else
  bad "CP1d no route decorator is bound to a _private helper" \
      "a helper STOLE a route (the real endpoint became unreachable and the door silently echoed its input): $BAD_ROUTES"
fi

echo ""
echo "--- CP1e: constitution ---"

# ---------------------------------------------------------------------------
# 5 — E-4 adds NO filter code, NO eval-only fork, and the parity gate still holds.
#     This is what makes E-4 honest: the eval scores the decision production makes
#     only because the two filter_engine copies are gated byte-identical.
# ---------------------------------------------------------------------------
if diff -q services/registry-api/filter_engine.py services/event-gateway/filter_engine.py >/dev/null 2>&1; then
  ok "CP1e the two filter_engine.py copies are BYTE-IDENTICAL" \
     "registry-api == event-gateway (the eval scores the decision production makes)"
else
  bad "CP1e the two filter_engine.py copies are BYTE-IDENTICAL" \
      "they have DIVERGED — E-4 would grade a filter production never runs (it already happened once, silently, for months)"
fi

if grep -q "check-filter-engine-parity.sh" "$DEPLOY_SH"; then
  ok "CP1e the filter-engine parity gate is still wired into deploy-cpe2e.sh" \
     "divergent engines stay UNDEPLOYABLE — enforcement, not discipline"
else
  bad "CP1e the filter-engine parity gate is still wired into deploy-cpe2e.sh" \
      "the gate is not invoked before the builds — divergence would become deployable"
fi

# `|| true`, never `|| echo 0`: grep -c PRINTS "0" and THEN exits 1 on no match, so
# `|| echo 0` yields the two-line string "0\n0", which compares equal to nothing and
# fails the check it was meant to pass. (Same trap as the `${x:-0}` guards below, which
# handle the empty-output case rather than the no-match case.)
RUNNER_FILTER=$(grep -c "def evaluate_filters\|def _evaluate_rule" services/eval-runner/main.py 2>/dev/null || true)
if [ "${RUNNER_FILTER:-0}" = "0" ]; then
  ok "CP1e the eval-runner defines NO filter of its own" \
     "it reads the door's decision; it never re-decides (no webhook-only eval filter fork)"
else
  bad "CP1e the eval-runner defines NO filter of its own" \
      "$RUNNER_FILTER filter definition(s) in the runner — an eval-only filter fork is the E-4 anti-pattern"
fi

MODE_FORK=$(grep -l 'mode == "webhook"' \
  services/registry-api/routers/internal.py \
  services/registry-api/durable_dispatch.py \
  services/registry-api/workflow_orchestrator.py 2>/dev/null | tr '\n' ' ' || true)
if [ -z "$MODE_FORK" ]; then
  ok "CP1e no eval-only webhook fork in any dispatch file" \
     "mode=='webhook' appears only at the score-door discriminator + the runner's handler map"
else
  bad "CP1e no eval-only webhook fork in any dispatch file" \
      "found in: $MODE_FORK — the eval must not fork production dispatch"
fi

# ---------------------------------------------------------------------------
# 6 — the runner FAIL-CLOSES on an unhandled MODE.
#     The priority if-chain this replaced dropped an unhandled MODE through to the
#     reactive tail: a REAL 'live' run (delivering real side effects), no filter, and a
#     plausible {"response": x} PASS. A missing branch failed SAFE-LOOKING.
# ---------------------------------------------------------------------------
HANDLER_MAP=$(grep -c "def _resolve_item_handler" services/eval-runner/main.py || true)
REACTIVE_REG=$(grep -c '"reactive": _run_reactive_item' services/eval-runner/main.py || true)
OLD_TAIL=$(grep -c 'if MODE == "durable" and not WORKFLOW_ID' services/eval-runner/main.py || true)
NO_HANDLER=$(grep -c "has no handler for MODE" services/eval-runner/main.py || true)
if [ "$HANDLER_MAP" = "1" ] && [ "$REACTIVE_REG" = "1" ] && [ "$OLD_TAIL" = "0" ] && [ "${NO_HANDLER:-0}" -ge 1 ]; then
  ok "CP1e the runner fail-closes on an unhandled MODE (explicit handler map, reactive REGISTERED)" \
     "no reactive fallthrough tail; an unhandled mode records FAILED and creates no run (suite-77 T-S77-010)"
else
  bad "CP1e the runner fail-closes on an unhandled MODE" \
      "_resolve_item_handler=$HANDLER_MAP (want 1) reactive_registered=$REACTIVE_REG (want 1) old_fallthrough=$OLD_TAIL (want 0) no_handler_msg=$NO_HANDLER (want >=1)"
fi

# ---------------------------------------------------------------------------
# 1 — the three tags agree in BOTH files. eval-runner has THREE pins.
# ---------------------------------------------------------------------------
REGISTRY_API_TAG=$(grep -E '^REGISTRY_API_TAG=' "$DEPLOY_SH" | head -1 | cut -d'"' -f2)
STUDIO_TAG=$(grep -E '^STUDIO_TAG=' "$DEPLOY_SH" | head -1 | cut -d'"' -f2)
EVAL_RUNNER_TAG=$(grep -E '^EVAL_RUNNER_TAG=' "$DEPLOY_SH" | head -1 | cut -d'"' -f2)
DECLARATIVE_RUNNER_TAG=$(grep -E '^DECLARATIVE_RUNNER_TAG=' "$DEPLOY_SH" | head -1 | cut -d'"' -f2)
EVENT_GATEWAY_TAG=$(grep -E '^EVENT_GATEWAY_TAG=' "$DEPLOY_SH" | head -1 | cut -d'"' -f2)

TAG_REPORT=$(python3 - "$VALUES" "$REGISTRY_API_TAG" "$STUDIO_TAG" "$EVAL_RUNNER_TAG" <<'PY'
import re, sys
values, want_api, want_studio, want_eval = sys.argv[1:5]
lines = open(values).read().splitlines()

def paired_tag(repo_suffix):
    """The `tag:` belonging to the `repository:` ending in repo_suffix — from the SAME
    image block. Comments/blanks are skipped (these blocks accumulate comments precisely
    because the 'bumped one file only' bug keeps recurring); the scan stops at the next
    key at or above the block's indent so it can never read a neighbour's tag."""
    for i, ln in enumerate(lines):
        if re.match(r"\s*repository:\s*\S*" + re.escape(repo_suffix) + r"\s*$", ln):
            repo_indent = len(ln) - len(ln.lstrip())
            for nxt in lines[i + 1:]:
                if not nxt.strip() or nxt.lstrip().startswith("#"):
                    continue
                m = re.match(r'\s*tag:\s*"?([^"\s]+)"?\s*$', nxt)
                if m:
                    return m.group(1)
                if len(nxt) - len(nxt.lstrip()) <= repo_indent:
                    break
    return None

def inline_tag(key):
    for ln in lines:
        m = re.match(r'\s*' + re.escape(key) + r':\s*"?([^"\s]+)"?\s*$', ln)
        if m:
            return m.group(1)
    return None

api = paired_tag("/registry-api")
studio = paired_tag("/studio")
# eval-runner has THREE pins. The third one — registry-api.env.EVAL_RUNNER_IMAGE — is
# what ACTUALLY launches the eval Job (k8s.py reads it). Miss it and the Job runs the
# OLD runner while every other check stays green.
ev_img = inline_tag("evalRunnerImage")
ev_img = ev_img.rsplit(":", 1)[-1] if ev_img else None
ev_env = inline_tag("EVAL_RUNNER_IMAGE")
ev_env = ev_env.rsplit(":", 1)[-1] if ev_env else None

rows = [
    ("registry-api", api, want_api, "values.yaml image.tag"),
    ("studio", studio, want_studio, "values.yaml image.tag"),
    ("eval-runner (evalRunnerImage)", ev_img, want_eval, "values.yaml evalRunnerImage"),
    ("eval-runner (registry-api.env.EVAL_RUNNER_IMAGE — LAUNCHES THE JOB)",
     ev_env, want_eval, "values.yaml registry-api.env.EVAL_RUNNER_IMAGE"),
]
for label, got, want, where in rows:
    if got == want:
        print(f"OK|{label} tag agrees in BOTH deploy-cpe2e.sh and values.yaml|{label}={got}")
    else:
        print(f"NO|{label} tag agrees in BOTH deploy-cpe2e.sh and values.yaml|"
              f"deploy-cpe2e.sh says '{want}' but {where} says '{got}' — the deploy bakes "
              f"tags from values.yaml, so bumping only deploy-cpe2e.sh leaves the chart on the old image")
PY
)
while IFS='|' read -r st label detail; do
  [ -z "$st" ] && continue
  if [ "$st" = "OK" ]; then ok "CP1e $label" "$detail"; else bad "CP1e $label" "$detail"; fi
done <<< "$TAG_REPORT"

# ---------------------------------------------------------------------------
# 2 — declarative-runner / event-gateway bumped IFF their source changed.
#     Fails LOUDLY in the "source changed but tag not bumped" direction (the E-3 bug:
#     the code never ran for a whole slice while every static check stayed green).
# ---------------------------------------------------------------------------
# Files touched by "this change" — the UNCOMMITTED working tree, plus (when AUDIT_REF is
# set) the audited commit itself.
#
# The commit half is not optional polish: this sweep is meant to be RE-RUNNABLE, and a
# working-tree-only view reports "the experience doc was NOT updated" the moment the work
# is committed — a FALSE NEGATIVE about a doc sitting right there in the commit. A gate
# that inverts its verdict at `git commit` teaches people to ignore it.
#
# AUDIT_REF defaults to HEAD so a post-commit re-run answers the question the gate is
# actually asking ("did this change update the doc?") rather than ("is the doc dirty
# right now?"). Pre-commit, the working tree carries the change and HEAD does not — the
# union covers both without the caller having to know which state they are in.
changed_count() {
  local dir="$1"
  local ref="${AUDIT_REF:-HEAD}"
  {
    git diff --name-only -- "$dir"
    git diff --name-only --cached -- "$dir"
    git ls-files --others --exclude-standard -- "$dir"
    git show --name-only --pretty=format: "$ref" -- "$dir" 2>/dev/null
  } 2>/dev/null | sort -u | grep -c . || true
}

SDK_CHANGED=$(changed_count "sdk/agentshield_sdk/")
if [ "${SDK_CHANGED:-0}" = "0" ]; then
  if [ "$DECLARATIVE_RUNNER_TAG" = "0.1.48" ]; then
    ok "CP1e declarative-runner untouched at 0.1.48 (no sdk/agentshield_sdk/ change — E-4 R13)" \
       "sdk files changed: 0"
  else
    bad "CP1e declarative-runner untouched at 0.1.48" \
        "no SDK change but the tag moved to $DECLARATIVE_RUNNER_TAG — an unexplained bump"
  fi
else
  bad "CP1e declarative-runner must be bumped — sdk/agentshield_sdk/ CHANGED" \
      "$SDK_CHANGED SDK file(s) changed; the runner image pip-bundles the SDK, so a stale runner would serve old code"
fi

GW_CHANGED=$(changed_count "services/event-gateway/")
if [ "${GW_CHANGED:-0}" = "0" ]; then
  if [ "$EVENT_GATEWAY_TAG" = "0.1.3" ]; then
    ok "CP1e event-gateway untouched at 0.1.3 (no services/event-gateway/ change — E-4 R13)" \
       "gateway files changed: 0"
  else
    bad "CP1e event-gateway untouched at 0.1.3" \
        "no gateway change but the tag moved to $EVENT_GATEWAY_TAG — an unexplained bump"
  fi
else
  bad "CP1e event-gateway must be bumped — services/event-gateway/ CHANGED" \
      "$GW_CHANGED gateway file(s) changed"
fi

# ---------------------------------------------------------------------------
# 3 — E-4 owns NO migration (head stays 0064).
# ---------------------------------------------------------------------------
NEW_MIGRATIONS=$(git ls-files --others --exclude-standard -- "services/registry-api/alembic/versions/" 2>/dev/null | grep -c . || true)
if [ "${NEW_MIGRATIONS:-0}" = "0" ]; then
  ok "CP1e no new Alembic version file (E-4 owns NO migration — R12, head stays 0064)" \
     "alembic/versions/ untouched"
else
  bad "CP1e no new Alembic version file" \
      "$NEW_MIGRATIONS new migration(s) — E-4 owns none; every column it needs already exists"
fi

# ---------------------------------------------------------------------------
# 4 — the experience doc was updated (playground.py / eval_runner.py / judge.py /
#     DatasetsPage.tsx / EvalResultsPage.tsx are ALL covered files — CLAUDE.md §3).
# ---------------------------------------------------------------------------
DOC_CHANGED=$(changed_count "docs/experience/playground.md")
if [ "${DOC_CHANGED:-0}" -ge 1 ]; then
  ok "CP1e docs/experience/playground.md updated (covered files changed — CLAUDE.md §3)" \
     "playground.md is in the change"
else
  bad "CP1e docs/experience/playground.md updated" \
      "covered files changed but the experience doc did not"
fi

# The gap ledger must carry E-4's honest state (DoD #5).
if grep -q "E-4" docs/testing/manual-ui-e2e-test-plan.md 2>/dev/null; then
  ok "CP1e the gap ledger carries E-4's row (DoD #5 — never let an unfinished piece read as shipped)" \
     "E-4 present in docs/testing/manual-ui-e2e-test-plan.md"
else
  bad "CP1e the gap ledger carries E-4's row" \
      "no E-4 section in the canonical Known gaps header"
fi

# suite-77 must be registered, or the gate never runs in CI.
if grep -q "suite-77" scripts/e2e/run-all.sh 2>/dev/null && [ -x scripts/e2e/suite-77-eval-v2-webhook.sh ]; then
  ok "CP1e suite-77 is registered in run-all.sh and executable" "the E-4 gate actually runs"
else
  bad "CP1e suite-77 is registered in run-all.sh and executable" \
      "an unregistered suite is a gate that never runs"
fi

echo ""
echo "=== E-4 CP1d/CP1e sweep: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -ne 0 ]; then
  echo "❌ E-4 CP1d/CP1e sweep FAILED"
  exit 1
fi
echo "✅ E-4 CP1d/CP1e sweep PASSED (no orphans — incl. the E-0 \`matched\` column; all five tag pins agree; parity gate holds; fail-closed dispatch; no migration; docs + ledger updated)"
