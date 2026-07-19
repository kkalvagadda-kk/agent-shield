# Three silent failures on the `test-event` door (Eval v2 E-4, P5–P7)

**Slice:** Eval v2 E-4 (webhook eval). **Found by:** `suite-77` + `suite-22`, during P5–P7.
**Fixed in:** registry-api `0.2.189` (bug 2) and `0.2.190` (bug 3); eval-runner `0.1.12` (bug 1).

Three defects, found in one slice, that share one signature: **they all failed *safe-looking*.** Nothing
errored, no pod crashed, no static check went red. Each produced a plausible result that was wrong — which is
the worst possible failure mode for a system whose entire job is to be a trustworthy publish gate.

---

## Symptom 1 — a webhook eval would have run the WRONG path, LIVE

**Symptom.** A `mode='webhook'` eval launched via the API scored `{"response": x}` and reported a pass. It
never fired a filter. Worse, it **delivered real side effects**.

### Root cause — priority fallthrough in the eval-runner's dispatch

**Where:** `services/eval-runner/main.py::run_eval`.

**Problem.** Dispatch was a priority if-chain (`if MODE == "scheduled" … if MODE == "durable" … if
WORKFLOW_ID …`) with the **reactive path as the untyped tail**. Any `MODE` without a branch fell through to
it. CP1a had opened the launch guard for `webhook` one phase *before* P5 gave the runner a webhook branch, so
`MODE=webhook` reached the runner and dropped through to reactive:

- an **empty `input_message`** (a webhook item carries neither `input` nor `input_message`),
- **no `eval_mode` ⇒ defaults `'live'` ⇒ REAL SIDE EFFECTS DELIVERED** (E-2's seam reads the persisted column),
- the filter **never fired at all**,
- and a plausible `{"response": x}` **PASS** — a "webhook eval passed" that never tested a filter.

A missing branch failed *safe-looking* instead of loudly.

**Fix — structural, not another `if`.** Dispatch is now an explicit **mode→handler map**
(`_resolve_item_handler`). `reactive` is a **registered handler** (`_run_reactive_item`, extracted from the
tail), not the default. A mode with no handler resolves to `None`, and every item is recorded **failed** with
the mode named, having created **no run**. Adding a mode to the guard without a handler is now a loud,
testable failure instead of a fake green. This also collapsed five duplicated result-POST blocks into one.

**Gate:** `suite-77` `T-S77-010` — the REAL eval-runner image, launched by the product's own Job builder with
an unhandled MODE, must record every item failed and create **zero** `playground_runs` (asserted directly).

---

## Symptom 2 — a matched webhook run never saw its own event

**Symptom.** `suite-77`'s matched items fail-closed with `dimension_scores = null`. The agent's real response
was: *"I have not been provided with any event payload."* A REAL run, really scored, that never saw the event.

### Root cause — `input_message` does not reach a durable agent

**Where:** `services/registry-api/routers/playground.py::test_event`.

**Problem.** `test_event` called the shared builder with `input_message=json.dumps(payload)` and
**`input_payload=None`**. But the durable dispatch body (`durable_dispatch.py`) carries **only
`input_payload`** — the runner derives its driving turn from it — so `input_message` never reaches a durable
agent. The matched run dispatched `{}`.

**Why it was invisible.** Before D2 (this same slice), `test_event` **never dispatched a durable run at all**
— it hung at `running` forever. Fixing that hang is what first made this reachable. One bug was hiding behind
another.

**Fix.** `test_event` now feeds the **identical production shape** the real webhook door
(`routers/internal.py`) uses: `input_payload=payload`, and the driving turn derived with that door's own line
(`payload.get("message") or json.dumps(payload)`, extracted as `_webhook_driving_message`).

**Gate:** `suite-77` `T-S77-004` — **the positive control**. This is the entire reason it is mandatory: a
filter **miss** scoring `1.0` and "the eval never ran at all" are the *same observable* (`filter: 1.0`, no
run). Only a **match on the same agent and the same dataset** distinguishes them. A miss-only suite would have
been fully green with the eval completely broken.

---

## Symptom 3 — the door returned a 200 echo of its own request body

**Symptom.** `suite-22` dropped from **4/0 to 1/3**: `AttributeError: 'str' object has no attribute 'get'`.
`POST /playground/test-event` returned **200** with body `"{\"agent_name\": \"nonexistent-xyz\", …}"` — a JSON
**string** — for every input, including a nonexistent agent (which must 404).

### Root cause — a decorator binds to the NEXT function

**Where:** `services/registry-api/routers/playground.py`.

**Problem.** `_webhook_driving_message` (the symptom-2 fix) was inserted **between** the
`@router.post("/test-event")` decorator and `async def test_event`. A decorator always binds to the *next*
function, so the **route silently rebound to the helper** — `POST /test-event` became
`json.dumps(request_body)` and the real handler became **unreachable**.

**Why every check missed it.** `ast.parse` passed. The module imported. The pod started clean and stayed
`Running`. All five tag pins agreed. The deployed image genuinely contained the new code — every
content-verification grep passed, because the *content was there*, just wired to the wrong name. **Only
`suite-22` — a real HTTP request to the real door — caught it.**

**Fix.** Helper moved above the decorator. suite-22 restored to 4/0.

**Guard (the class, not the instance).** `scripts/smoke-test-cp1-e4-constitution.sh` now AST-walks **all** of
`services/registry-api/routers/` and fails if any `@router.<method>` decorates a `_private` name — a route
handler is always a public endpoint function, so a private one under a decorator is *always* a helper that
stole a route. The guard was verified by re-injecting the exact bug and watching it fail.
`smoke-test-cp1-e4-mvp.sh` adds the runtime half: probe the deployed door with an unknown agent and require
**404 + a JSON object**, not 200 + a string.

---

## Lessons

1. **A tag is a claim about content — and content is a claim about *wiring*.** E-3's lesson (verify the
   deployed image contains the new symbols) was necessary but **not sufficient**: bug 3's symbols were all
   present in the image and the route was still wrong. The only complete verification is *driving the real
   door*.
2. **The positive control is not optional.** Bug 2 is invisible to any test that only checks the negative
   case. "Correctly filtered" and "nothing ran" are indistinguishable without a match on the same fixture.
3. **Fix the dispatch, not the branch.** Bug 1 was one missing `if` away from a patch. The patch would have
   left the *next* mode with the same live-delivery hazard. A handler map makes it unrepresentable.
4. **Fixing one bug can expose another that was hiding behind it.** Bug 2 only became reachable once D2 fixed
   the durable-dispatch hang. Re-run the gate after every fix; do not assume the remaining failures share the
   cause you just addressed.
5. **`ast.parse` is not verification, and neither is a clean rollout.** Both bugs 3 and the earlier
   `Literal`-unimported bug passed static checks. The local interpreter is 3.9 and cannot even import the 3.12
   models, so `pytest` silently skipped 44 of 103 tests — run the suite in a container off the real image.
