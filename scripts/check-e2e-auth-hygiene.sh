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

# ── The lib's contract, DERIVED from lib/e2e-auth.sh ──────────────────────────
# Rule 9 needs two facts about the shared auth library, and must not be told them:
#
#   * which variables it sets, and whether they land at SOURCE time (a top-level
#     assignment — live the instant a suite sources the file) or at CALL time
#     (assigned inside a function body — live only once that function has run)
#   * which function calls provide each call-time variable, transitively
#     (`e2e_refresh_token` provides E2E_TOKEN because it calls `e2e_set_token`)
#
# Derived, never listed. Every miss in this file's history came from a hand-written
# list going stale: four grep patterns each narrower than their target, and one
# 17-suite sweep that missed 28. Add a helper to the lib and rule 9 covers it with
# no edit here.
LIB_PATH = pathlib.Path("scripts/e2e/lib/e2e-auth.sh")
CALL_TIME_VARS = {}   # VAR -> {function names that provide it, transitively}
LIB_FUNCS = set()     # every e2e_* function the lib defines
if LIB_PATH.is_file():
    fn, direct, calls, source_time = None, {}, {}, set()
    heredoc = None
    for ln in LIB_PATH.read_text().splitlines():
        # Skip heredoc bodies. The lib embeds whole Python programs, and `H={...}` in
        # one of them would otherwise register as a variable the lib provides — a
        # false positive on every suite that names a header dict `H`.
        if heredoc is not None:
            if ln.strip() == heredoc:
                heredoc = None
            continue
        hd = re.search(r"<<-?\s*'?\"?([A-Za-z_][A-Za-z0-9_]*)'?\"?\s*$", ln)
        if hd:
            heredoc = hd.group(1)
            continue
        # Function bodies here open with `name() {` and close with `}` in column 0.
        # Brace COUNTING would be wrong: the embedded Python is full of braces.
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{", ln)
        if m:
            fn = m.group(1)
            LIB_FUNCS.add(fn)
            direct.setdefault(fn, set())
            calls.setdefault(fn, set())
            continue
        if fn and ln.startswith("}"):
            fn = None
            continue
        a = re.match(r"\s*(?:export\s+)?([A-Z][A-Z0-9_]+)=", ln)
        if a:
            (direct[fn].add(a.group(1)) if fn else source_time.add(a.group(1)))
        if fn:
            calls[fn] |= {c for c in re.findall(r"\b(e2e_[a-z0-9_]+)\b", ln) if c != fn}
    provides = {f: set(v) for f, v in direct.items()}
    for _ in range(len(provides) + 1):          # transitive closure
        for f, cs in calls.items():
            for c in cs:
                provides[f] |= provides.get(c, set())
    for f, vs in provides.items():
        for v in vs:
            if v not in source_time:            # source-time vars need no call
                CALL_TIME_VARS.setdefault(v, set()).add(f)

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
    # `auth=BearerAuth()` is httpx's own credential hook — the header never appears as a
    # literal, so a text search for "Authorization" cannot see it. suite-95 authenticates
    # that way and was flagged. Recognise the marker rather than widen the search.
    credentialed_bash = ("Authorization" in t) or ("BearerAuth" in t)
    if gated_call_seen and not credentialed_bash:
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

    # 8b — the in-pod DRIVER must compile as Python.
    #
    # `kubectl exec ... python3 -c '<program>'` wraps a whole Python program in a bash
    # SINGLE-quoted string, so the program ends at the very next apostrophe. One `'` in a
    # comment or a message — a contraction is enough — closes it early, bash splices the
    # remainder as shell text, and Python receives a truncated file. The symptom is
    # `SyntaxError: unterminated string literal` at a line number in the DRIVER, which
    # matches no line in the suite file, and EVERY case in that driver fails at once. It
    # reads as "the feature is broken" rather than "the quoting is broken".
    #
    # Same family as rule 7: `bash -n` accepts it, because the suite file IS valid bash —
    # the driver is just a string to it. So the only signal is a cluster run. suite-42 and
    # suite-99 each paid for this; suite-101 paid for it a third time on its first run,
    # over the contraction in "we do not know".
    #
    # The check is the EXACT property, not a proxy for it: slice from the opening quote to
    # the next `'` — which is precisely what bash will hand to python3 — and `compile()` it.
    # That catches the apostrophe case and any other syntax error in a driver, and it
    # cannot false-positive the way an apostrophe-hunt does (an early version flagged
    # suite-99, whose one-line driver legitimately closes mid-line).
    for _m in re.finditer(r"python3 -c '\n", t):
        _start = _m.end()
        _end = t.find("'", _start)
        if _end == -1:
            continue
        _base = t[:_start].count("\n") + 1
        try:
            compile(t[_start:_end], "<driver>", "exec")
        except SyntaxError as _se:
            FAIL.append(
                f"{p.name}:{_base + (_se.lineno or 1) - 1}  in-pod driver does not compile "
                f"as Python ({_se.msg}) — usually an apostrophe closing the single-quoted "
                f"block early, which truncates the program bash hands to python3"
            )

    # 8c — a HEREDOC-written Python driver must compile too.
    #
    # Rule 8b covers `python3 -c '<program>'`. Several suites instead write the driver to a
    # file first (`bash -c "cat > $DRIVER" <<'PY' ... PY`) and run it detached, and 8b is
    # blind to those. That blindness cost SIX suites: the R3 Bearer pass and the E2E_SUB
    # pass both inserted `source .../lib/e2e-auth.sh` and `e2e_set_token ...` INSIDE the
    # heredoc, so Python received shell text and died with `SyntaxError: invalid syntax`.
    #
    # The symptom is why it survived: a driver that never starts produces no result file,
    # so the suite reports "no result file" / "driver did not finish" instead of a test
    # failure. That reads as infrastructure flakiness, not as a broken suite. suite-64, 65,
    # 66, 68, 94 and 96 sat broken from 748c2fd until 2026-08-09, when one of them was run
    # as blast radius for an unrelated change.
    #
    # Same exact property as 8b: compile the heredoc body. Only bodies that are actually
    # Python are checked — either the opening command mentions python3, or the body starts
    # with an import.
    _lines = t.splitlines()
    for _i, _ln in enumerate(_lines):
        _hd = re.search(r"<<-?\s*'([A-Za-z_][A-Za-z0-9_]*)'\s*(?:2>[^ ]*\s*)?$", _ln)
        if not _hd:
            continue
        _term = _hd.group(1)
        _end = None
        for _j in range(_i + 1, len(_lines)):
            if _lines[_j].strip() == _term:
                _end = _j
                break
        if _end is None:
            continue
        _body = "\n".join(_lines[_i + 1:_end])
        if not _body.strip():
            continue
        _first = _body.lstrip().split("\n")[0]
        if "python3" not in _ln and not re.match(r"\s*(import|from)\s", _first):
            continue
        try:
            compile(_body, "<heredoc>", "exec")
        except SyntaxError as _se:
            FAIL.append(
                f"{p.name}:{_i + 1 + (_se.lineno or 1)}  heredoc-written Python driver does "
                f"not compile ({_se.msg}) — usually a bash line (source/e2e_set_token) "
                f"spliced INSIDE the heredoc, which makes the driver never run at all"
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

    # 9 — RUNTIME ORDER: something is used before the thing that provides it.
    #
    # THE CLASS, not one instance of it. Two shapes, both invisible to `bash -n` because
    # ordering is not syntax:
    #
    #   A. use before define      `${E2E_SUB}` read above the `e2e_set_token` that sets it
    #   B. call before its args   `e2e_set_token "$NS" "$API_POD"` above `API_POD=`
    #
    # Shape A shipped three times from mechanical edit passes (suite-20/23/24/25/29/40 in
    # R3, then suite-70, then nine more when ${E2E_SUB} replaced the stale sub literals).
    # Shape B shipped in that same E2E_SUB pass. Each time the insertion point was chosen
    # from a TEXTUAL landmark ("after the source line") while correctness depended on
    # DATAFLOW — and those two agree on the uniform majority of suites and diverge exactly
    # in the tail that resolves API_POD late or inside a function.
    #
    # The first version of this rule hardcoded `E2E_TOKEN`, `E2E_SUB` and `e2e_set_token`.
    # It therefore caught shape A only and could not have seen shape B at all: an instance
    # fix wearing a class fix's clothes. Both names and functions are now derived from the
    # lib above.
    #
    # NOTE — neither shape is silent. All 105 suites run `set -euo pipefail`, so both abort
    # naming the variable. The problem was never silence; it is that the only signal costs a
    # CLUSTER RUN, while the cheap post-edit check is `bash -n`, which answers a different
    # question. This rule is the cheap check that answers the right one.
    _lines = t.splitlines()

    def _scan(pred):
        """First non-comment line index satisfying pred, or None. `trap` lines are skipped:
        their body runs at EXIT, so a trap referencing a variable assigned below it is
        correct, not a bug — flagging it is the kind of noise that gets a gate ignored."""
        for _i, _l in enumerate(_lines):
            _s = _l.lstrip()
            if _s.startswith("#") or _s.startswith("trap "):
                continue
            if pred(_l):
                return _i
        return None

    # (a) shape A — a call-time variable read above everything that could provide it.
    for _var, _provs in sorted(CALL_TIME_VARS.items()):
        _call = re.compile(r"(?<![A-Za-z0-9_])(?:%s)(?![A-Za-z0-9_])" % "|".join(sorted(_provs)))
        # A suite may also mint into the variable itself
        # (`E2E_TOKEN="$(e2e_require_token …)"`) — that assignment is a provider too.
        _assign = re.compile(r"^\s*(?:export\s+)?%s=" % _var)
        _read = re.compile(r"\$\{%s[}:]|\$%s(?![A-Za-z0-9_])" % (_var, _var))
        _prov_at = _scan(lambda l, _c=_call, _a=_assign: bool(_c.search(l) or _a.match(l)))
        _read_at = _scan(lambda l, _r=_read, _c=_call, _a=_assign:
                         bool(_r.search(l)) and not _c.search(l) and not _a.match(l))
        if _read_at is None:
            continue
        if _prov_at is None:
            FAIL.append(
                f"{p.name}:{_read_at + 1}  reads ${{{_var}}} but never calls anything that "
                f"sets it ({', '.join(sorted(_provs))}) — it expands to empty"
            )
        elif _read_at < _prov_at:
            FAIL.append(
                f"{p.name}:{_read_at + 1}  reads ${{{_var}}} at line {_read_at + 1}, but the "
                f"call that provides it is not until line {_prov_at + 1}"
            )

    # (b) shape B — a lib call whose own ARGUMENTS are assigned further down the file.
    # Only flagged when the assignment exists BELOW the call: a variable never assigned in
    # the file may legitimately arrive from the environment (run-tests.sh:190 passes
    # NAMESPACE), and guessing there produces false positives.
    if LIB_FUNCS:
        _anyfn = re.compile(r"(?<![A-Za-z0-9_])(?:%s)(?![A-Za-z0-9_])" % "|".join(sorted(LIB_FUNCS)))
        _assigned_at = {}
        for _i, _l in enumerate(_lines):
            _a = re.match(r"\s*(?:export\s+|local\s+)?([A-Za-z_][A-Za-z0-9_]*)=", _l)
            if _a and _a.group(1) not in _assigned_at:
                _assigned_at[_a.group(1)] = _i
        for _i, _l in enumerate(_lines):
            _s = _l.lstrip()
            if _s.startswith("#") or _s.startswith("trap ") or not _anyfn.search(_l):
                continue
            for _v in set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)[}:]|\$([A-Za-z_][A-Za-z0-9_]*)", _l)):
                _name = _v[0] or _v[1]
                _at = _assigned_at.get(_name)
                if _at is not None and _at > _i:
                    FAIL.append(
                        f"{p.name}:{_i + 1}  calls a lib helper with ${_name}, which is not "
                        f"assigned until line {_at + 1} — unbound at that point under `set -u`"
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
print("  no uncredentialed agent/tool/skill mutations, no unsourced ${E2E_TOKEN},")
print("  no split line continuations, and nothing used before the call that provides it.")
PY
