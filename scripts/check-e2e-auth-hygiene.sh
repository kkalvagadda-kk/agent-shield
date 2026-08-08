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
# Routes gated by R2/R3 (agents) and G-R3-6 (tools, skills). A mutating call to one of
# these with no Authorization will 401/403 against a current image.
#
# `/tools` and `/skills` were added 2026-08-07 with G-R3-6. Note the ordering trap: the
# agents pattern must NOT match `/agents/{n}/tools` (a READ with a machine caller), so
# tool/skill matching is anchored to the COLLECTION prefix, not the substring.
# `/mcp-servers` added 2026-08-07 with 0.2.270: registration now needs a credential
# because ownership is derived from the caller. suite-87 registered servers with a
# header and NO token; this gate could not see it until the route was gated.
# THE F-STRING FORM. Suites write both `'/api/v1/tools/'` and `f"{BASE}/tools/"`, and every
# literal-path pattern here was blind to the second — which is how suite-87 (zero E2E_TOKEN,
# registering MCP servers with a bare header) and suite-80 (POST /tools/ and /agents/ with no
# credential, red since 0.2.267) both sat unnoticed through three sweeps whose lists were
# derived by grepping for the literal path. Third instance of "the grep pattern was narrower
# than the thing it was looking for". Match BOTH shapes.
GATED = re.compile(
    r"/api/v1/(agents|tools|skills|mcp-servers)\b"
    r"|\{BASE\}/(agents|tools|skills|mcp-servers)"
    r"|\+ '/agents/'|base \+ '/agents/'"
    # RELATIVE paths. Suites that build an httpx.Client(base_url=...) call
    # `c.post('/agents/', ...)` with no prefix at all, so neither the literal
    # `/api/v1/...` nor the `{BASE}/...` form matches. suite-30 and suite-35 have ZERO
    # E2E_TOKEN references and were invisible to every rule here for exactly that reason.
    # Fourth shape of "the pattern was narrower than the thing it was looking for".
    r"|['\"]/agents/?['\",]|['\"]/tools/?['\",]|['\"]/skills/?['\",]"
)
# Reads that now require a caller (0.2.271). Narrower than GATED on purpose: /agents/{n}
# and /agents/{n}/memory are still open for deploy-controller and eval-runner, which have no
# credential until identity Phase 3, so flagging them would be noise a reader learns to skip.
GATED_READ = re.compile(
    r"/api/v1/tools/|\{BASE\}/tools/|/tools/\?|/api/v1/skills/|\{BASE\}/skills/|/tools\?limit"
    r"|/agents/[^'\"]*/tools"   # the BINDING endpoint — user token OR agent SA token
)

# Sub-resources R2/R3/G-R3-6 did NOT gate — several have in-cluster machine callers that
# send no Authorization header (declarative-runner, deploy-controller, the SDK resolver).
NOT_AGENT_CREATE = re.compile(r"/versions|/deploy|/triggers|/identities|/agents/[^'\"]*/tools|/memory|/chat|/runs|/deployments|/stats|/health")

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
        FAIL.append(f"{p.name}:{line_of(k)}  mutation with NO Authorization — 401/403 since R2/R3/G-R3-6/0.2.270")

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
        FAIL.append(f"{p.name}:{line_of(m.start())}  mutation via httpx with NO Authorization (agents/tools/skills/mcp-servers)")

    # 6 — a gated READ with no credential.
    # Reads used to be exempt because agent pods called them anonymously. 0.2.271 closed
    # that: pods now ask GET /agents/{name}/tools with a ServiceAccount token, so
    # GET /api/v1/tools/ and the binding endpoint both require a caller. A rule that only
    # watches mutations stops covering the routes the moment the reads are gated too.
    for k, span in request_spans(t):
        if not GATED_READ.search(span):
            continue
        if "Authorization" in span:
            continue
        FAIL.append(f"{p.name}:{line_of(k)}  gated READ with NO Authorization — 401 since 0.2.271")
    for m in re.finditer(r"(?:httpx\.|await c\.|await client\.|\bc\.|\bclient\.)get\((?:[^()]|\([^()]*\))*\)", t):
        span = m.group(0)
        if not GATED_READ.search(span) or "Authorization" in span or "headers=" in span:
            continue
        FAIL.append(f"{p.name}:{line_of(m.start())}  gated READ via a client with NO Authorization — 401 since 0.2.271")

    # 5 — FILE-LEVEL: this suite makes a call we have PRECISELY identified as gated, and the
    # word "Authorization" does not appear anywhere in the file. A span-level regex cannot
    # follow `headers=HDR` to a dict defined 80 lines up, and that is exactly how suite-80
    # shipped `HDR = {"X-User-Sub": USER, "X-User-Team": TEAM}` and stayed red from 0.2.267
    # onward with nothing noticing.
    #
    # Keyed on the SPANS rules 3/3b/6 already matched, NOT on "does the file mention
    # /api/v1/agents anywhere". The first version did the latter and immediately flagged
    # suite-11 (GET /agents/ — the list, ungated) and suite-91 (/agents/{n}/memory — a
    # different router, ungated). A gate that cries wolf is one people learn to skip, which
    # is the failure this whole script exists to prevent.
    gated_call_seen = False
    for _k, _span in request_spans(t):
        if (GATED.search(_span) and not NOT_AGENT_CREATE.search(_span)
                and re.search(r"method=['\"](POST|PUT|PATCH|DELETE)['\"]", _span)) \
           or GATED_READ.search(_span):
            gated_call_seen = True
            break
    if not gated_call_seen:
        for _m in re.finditer(r"(?:httpx\.|await c\.|await client\.|\bc\.|\bclient\.)(?:get|post|put|patch|delete)\((?:[^()]|\([^()]*\))*\)", t):
            _s = _m.group(0)
            if (GATED.search(_s) and not NOT_AGENT_CREATE.search(_s)) or GATED_READ.search(_s):
                gated_call_seen = True
                break
    if gated_call_seen and "Authorization" not in t:
        FAIL.append(
            f"{p.name}  makes a gated agents/tools/skills/mcp-servers call and never sets an "
            f"Authorization header ANYWHERE in the file — every one of those calls is 401/403"
        )

    # 7 — a SPLIT line continuation.
    # A line ending in `\` must be followed by more command text. A blank line or a comment
    # after it means something was inserted BETWEEN the two halves of one command — which is
    # exactly what the scripted Bearer pass did to six suites in R3: it landed inside
    #     API_POD=$(kubectl get pods ... \
    #     <injected auth block>
    #       --field-selector=... )
    # leaving `API_POD` assigned from a truncated command and the second half running as its
    # own. `bash -n` accepts it — it is syntactically valid — so the only symptom was
    # `API_POD: unbound variable` at runtime, and six suites sat broken until a full run.
    #
    # This is the SIXTH scripted-edit defect in this file's history and the first one no
    # existing rule could see, because every other rule reasons about a call; this one is
    # about the shape of the file.
    for _i, _line in enumerate(t.splitlines()):
        if not _line.rstrip().endswith("\\"):
            continue
        # A `\` inside a COMMENT is prose — suites document multi-line kubectl invocations
        # in their headers, and both halves are comment lines. Only real command text can
        # have its continuation broken.
        if _line.lstrip().startswith("#"):
            continue
        _nxt = t.splitlines()[_i + 1] if _i + 1 < len(t.splitlines()) else ""
        if _nxt.strip() == "" or _nxt.lstrip().startswith("#"):
            FAIL.append(
                f"{p.name}:{_i + 1}  line continuation `\\` followed by a blank line or a "
                f"comment — something was inserted between the halves of one command"
            )

    # 8 — a header VARIABLE that carries no Authorization.
    # Rule 5 asks "does the file mention Authorization anywhere", which a suite passes by
    # authenticating just its cleanup. suite-29 and suite-40 did exactly that: the R3 pass
    # added the Bearer to the DELETE in `cleanup()` and left `H={'X-User-Sub':'system'}`
    # feeding every setup call, so the whole suite 401'd while the gate saw a clean file.
    #
    # So: find single-line header dicts assigned to a variable, and flag any that set an
    # identity-ish header but no Authorization. Deliberately narrow — a multi-line dict or a
    # dict built by code is out of scope, because guessing there produces the false
    # positives that get a gate ignored.
    for _m in re.finditer(r"^\s*(\w+)\s*=\s*\{[^}\n]*\}", t, re.M):
        _d = _m.group(0)
        if "Authorization" in _d:
            continue
        if not re.search(r"['\"]X-User-(Sub|Team|Id)['\"]", _d):
            continue
        # Only complain if THIS variable actually feeds a gated call. "The file touches a
        # gated route somewhere" is too loose and immediately produced eight false
        # positives on suite-9-eval, whose header dicts serve /playground/* — ungated.
        # Rule 5's first cut made the same mistake; a gate that cries wolf gets skipped.
        _var = _m.group(1)
        _fed_gated = False
        for _k, _span in request_spans(t):
            if f"headers={_var}" in _span and (
                (GATED.search(_span) and not NOT_AGENT_CREATE.search(_span))
                or GATED_READ.search(_span)):
                _fed_gated = True
                break
        if not _fed_gated:
            for _c in re.finditer(r"(?:httpx\.|await c\.|await client\.|\bc\.|\bclient\.)(?:get|post|put|patch|delete)\((?:[^()]|\([^()]*\))*\)", t):
                _s = _c.group(0)
                if f"headers={_var}" in _s and (
                    (GATED.search(_s) and not NOT_AGENT_CREATE.search(_s)) or GATED_READ.search(_s)):
                    _fed_gated = True
                    break
        if not _fed_gated:
            continue
        FAIL.append(
            f"{p.name}:{t[:_m.start()].count(chr(10)) + 1}  header dict `{_m.group(1)}` sets "
            f"X-User-* but no Authorization — calls using it are 401 on any gated route"
        )

    # 4 — the token is referenced but never obtained
    if "E2E_TOKEN" in t and "e2e-auth.sh" not in t:
        FAIL.append(f"{p.name}  references ${{E2E_TOKEN}} but never sources lib/e2e-auth.sh — it expands to empty")

# ── 5 — the OTHER e2e tree ────────────────────────────────────────────────────
# This script scanned scripts/e2e only, and that omission cost a real failure on
# 2026-08-07: `studio/e2e/lib/api.ts` seeded its fixtures with X-User-Sub headers and no
# token, so when G-R3-6 closed POST /api/v1/tools/ the lifecycle journey died at
# `seedDeterministicTool` with a 401 that surfaced as "leg 1 — create agent with tool
# (UI)". A gate that covers one of two trees is the blind spot it exists to prevent —
# the same lesson its own comments record twice above, applied to a directory instead of
# a client library.
#
# The Playwright layer authenticates four legitimate ways, all of which end in a real
# Bearer, so any of them counts as credentialed:
#   - an `Authorization` header written inline (roles.ts, rbac-role-journeys)
#   - `captureAuthHeaders()` (e2e/lib/apiAuth.ts) — lifts the app's own Bearer off page
#     traffic; the established helper for page-driven specs
#   - `adminAuthHeaders()` (e2e/lib/api.ts) — mints a token by direct access grant and
#     derives X-User-Sub FROM it, for specs that build their own APIRequestContext
#   - `adminApi()` / `userApi()` (e2e/lib/api.ts) — return an already-authenticated context
# Anything else mutating a gated route is running unauthenticated.
#
# This list is deliberately a NAMED SET rather than "does the file mention a token". Add a
# fifth way and you must add it here, which is the point: a gate that guesses at what
# counts as credentialed is a gate that quietly stops failing.
PW_GATED = re.compile(r"/api/v1/(agents|tools|skills|mcp-servers)\b")
PW_NOT_GATED = re.compile(r"/versions|/deploy|/triggers|/identities|/memory|/chat|/runs|/deployments|/stats|/health|/agents/[^'\"`]*/tools")

pw_root = pathlib.Path("studio/e2e")
if pw_root.is_dir():
    for p in sorted(list(pw_root.glob("*.spec.ts")) + list(pw_root.glob("lib/*.ts"))):
        t = p.read_text()
        line_of = lambda idx, _t=t: _t[:idx].count("\n") + 1
        credentialed = any(
            marker in t
            for marker in ("Authorization", "captureAuthHeaders", "adminAuthHeaders",
                           "adminApi(", "userApi(")
        )
        if credentialed:
            continue
        for m in re.finditer(r"\.(post|put|patch|delete)\(\s*[`'\"][^`'\"]*[`'\"]", t):
            span = m.group(0)
            if not PW_GATED.search(span) or PW_NOT_GATED.search(span):
                continue
            FAIL.append(
                f"studio/e2e/{p.relative_to(pw_root)}:{line_of(m.start())}  mutation of a gated route "
                f"with no Authorization and no captureAuthHeaders() — 401 since R1/R2/R3/G-R3-6"
            )

if FAIL:
    print(f"=== e2e auth hygiene: {len(FAIL)} FINDING(S) ===")
    for f in FAIL:
        print("  " + f)
    print("\nSee the header of scripts/check-e2e-auth-hygiene.sh for what each one means.")
    sys.exit(1)

print("=== e2e auth hygiene: clean ===")
print("  no duplicate headers kwargs, no duplicate Authorization keys,")
print("  no uncredentialed agent/tool/skill mutations, no unsourced ${E2E_TOKEN}.")
PY
