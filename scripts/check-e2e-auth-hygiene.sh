#!/usr/bin/env bash
# =============================================================================
# check-e2e-auth-hygiene.sh — static audit of how the bash e2e suites authenticate.
#
# Cluster-free, ~1s. Run it before deploying a router change, next to
# scripts/check-tag-content-coupling.sh.
#
# WHY THIS EXISTS
# ---------------
# Three RBAC phases in a row shipped a router change that turned suites red, and
# each time the fix was a hand sweep across dozens of files:
#   R1  `require_user` on ten routers        -> 32 suites needed a Bearer
#   R2  `can_create_agent` on POST /agents/  -> I verified a HAND-PICKED list of 17
#                                               suites and missed 28 more; several
#                                               stayed red for a full phase
#   R3  mutations on /agents/{name}          -> 61 call sites across 35 suites
#
# The R2 miss is the reason this is a script and not a checklist: the sweep list
# was written by hand, so it was wrong, and nothing said so. Derive it from the
# tree instead.
#
# The bulk edits then introduced four distinct bugs of their own, all invisible to
# `bash -n` because the damage is inside a Python string:
#   1. a duplicate `headers=` kwarg          -> SyntaxError: keyword argument repeated
#   2. a duplicate 'Authorization' KEY       -> NOT an error; the later value silently
#      inside one dict                          wins, so a persona's call goes out as
#                                               someone else and the test proves nothing
#   3. an anonymous probe rewritten into an authenticated one (suite-15 T-S15-002)
#   4. a case that lost the unauthenticated half it existed to assert (suite-42)
# (3) and (4) need a human; (1) and (2) are mechanical and are checked here.
#
# Exit 1 on any finding.
# =============================================================================
set -uo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python3 - <<'PY'
import re, sys, pathlib

FAIL = []

def request_spans(t):
    """Yield every balanced urllib.request.Request(...) span."""
    pos = 0
    while True:
        k = t.find("urllib.request.Request(", pos)
        if k == -1:
            return
        j = k + len("urllib.request.Request("); d = 1
        while j < len(t) and d > 0:
            if t[j] == "(": d += 1
            elif t[j] == ")": d -= 1
            j += 1
        yield k, t[k:j]
        pos = j

def header_dicts(t):
    """Yield the inner text of every headers={...}."""
    for m in re.finditer(r"headers=\{", t):
        i = m.end() - 1; d = 0; j = i
        while j < len(t):
            if t[j] == "{": d += 1
            elif t[j] == "}":
                d -= 1
                if d == 0: break
            j += 1
        yield i, t[i+1:j]

# Agent routes that R2/R3 gated. A POST/PUT/PATCH/DELETE to one of these with no
# Authorization is a suite that will 401 or 403 against a current image.
# Match ANY /api/v1/agents path, then subtract the sub-resources R2/R3 did not gate.
# The first version of this regex required `agents/` to be followed by a quote, so it
# matched the collection endpoint and /publish|/quarantine but NEVER /agents/{name} —
# i.e. it was blind to exactly the routes R3 gated. suite-5's uncredentialed
# `DELETE /agents/hitl-s5-agent` sailed through it. Start broad, subtract deliberately.
GATED = re.compile(r"/api/v1/agents\b|\+ '/agents/'|base \+ '/agents/'")
NOT_AGENT_CREATE = re.compile(r"/versions|/deploy|/triggers|/identities|/tools|/memory|/chat|/runs|/deployments|/stats|/health")

for p in sorted(pathlib.Path("scripts/e2e").glob("suite-*.sh")):
    t = p.read_text()
    line_of = lambda idx: t[:idx].count("\n") + 1

    # 1 — duplicate headers= kwarg (a hard SyntaxError inside the in-pod driver)
    for k, span in request_spans(t):
        if span.count("headers=") > 1:
            FAIL.append(f"{p.name}:{line_of(k)}  duplicate `headers=` kwarg — SyntaxError in the in-pod driver")
    for m in re.finditer(r"httpx\.\w+\((?:[^()]|\([^()]*\))*\)", t):
        if m.group(0).count("headers=") > 1:
            FAIL.append(f"{p.name}:{line_of(m.start())}  duplicate `headers=` kwarg on an httpx call")

    # 2 — duplicate Authorization key inside one dict (silent: last value wins)
    for i, inner in header_dicts(t):
        if inner.count("'Authorization'") + inner.count('"Authorization"') > 1:
            FAIL.append(
                f"{p.name}:{line_of(i)}  TWO 'Authorization' keys in one headers dict — not an "
                f"error, the LAST one wins, so this call goes out as the wrong identity"
            )

    # 3 — a gated agent mutation with no credential
    for k, span in request_spans(t):
        if not GATED.search(span) or NOT_AGENT_CREATE.search(span):
            continue
        if not re.search(r"method=['\"](POST|PUT|PATCH|DELETE)['\"]", span):
            continue
        if "Authorization" in span:
            continue
        FAIL.append(f"{p.name}:{line_of(k)}  agent mutation with NO Authorization — 401/403 since R2/R3")

    # 3b — same rule, for httpx. The first version of this script checked httpx only for
    # duplicate `headers=` and missed suite-14's `httpx.post(.../publish, json=...)`,
    # which then 401'd in the sweep. A gate with a blind spot is the thing it is meant to
    # prevent, so both client styles are checked by the same rule.
    for m in re.finditer(r"httpx\.(?:post|put|patch|delete)\((?:[^()]|\([^()]*\))*\)", t):
        span = m.group(0)
        if not GATED.search(span) or NOT_AGENT_CREATE.search(span):
            continue
        if "Authorization" in span:
            continue
        FAIL.append(f"{p.name}:{line_of(m.start())}  agent mutation via httpx with NO Authorization")

    # 4 — the token is referenced but never obtained
    if "E2E_TOKEN" in t and "e2e-auth.sh" not in t:
        FAIL.append(f"{p.name}  references ${{E2E_TOKEN}} but never sources lib/e2e-auth.sh — it expands to empty")

if FAIL:
    print(f"=== e2e auth hygiene: {len(FAIL)} FINDING(S) ===")
    for f in FAIL:
        print("  " + f)
    print("\nSee the header of scripts/check-e2e-auth-hygiene.sh for what each one means.")
    sys.exit(1)

print("=== e2e auth hygiene: clean ===")
print("  no duplicate headers kwargs, no duplicate Authorization keys,")
print("  no uncredentialed agent mutations, no unsourced ${E2E_TOKEN}.")
PY
