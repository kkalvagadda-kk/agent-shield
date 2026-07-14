# 011 — Workflow/agent traces empty ("No span-level observations" / "trace not yet ingested") — agent pods can't resolve Langfuse

## Symptom
Opening a workflow run's Execution Trace in Studio showed **"No span-level
observations recorded for this trace."** for the parent (`flow-conditional`), and
drilling into a member (`wf-router`) showed **"trace not yet ingested by
Langfuse."** Total Cost read **$0.00000**. The workflow itself ran correctly —
routing worked (`wf-router` → `wf-payout`, both `completed`) — so this was purely
an **observability** failure, not an execution failure.

Reported as: "I still say this is for conditional workflows. This is exactly what
should have been fixed yesterday." — i.e. yesterday's "traces fixed" claim was
wrong; the spans were never actually reaching Langfuse.

## Chain
Registry `_run_step` stamps `child.langfuse_trace_id = uuid(child_id).hex` and
dispatches the durable member via the pod's `/run`
(`workflow_orchestrator._dispatch_durable_member`). The member
(`declarative-runner/main.py:_execute_durable_run`) runs inside
`otel_run_context(req.run_id)` and relies on **OTLP export** (`setup_otel()` →
`agentshield_sdk.otel`) to ship spans to Langfuse's
`/api/public/otel/v1/traces`. The parent envelope is authored separately by
`tracing.trace_create_run` from **registry-api** (which lives in the same
namespace as Langfuse, so it succeeds — that's why the parent envelope existed
but was empty).

## Root cause
**The agent pod could not DNS-resolve the Langfuse host, so every OTLP span
batch export failed and the child traces were never created.**

Evidence from the running product (not the design doc):
- Langfuse API: parent trace `534f21c8…` exists with **0 observations**; child
  traces `a9f4b3a6…` / `b56ba9e5…` return **404 — never created**.
- Member pod (`wf-router-sandbox`, namespace `agents-platform`) startup log:
  `OTEL tracing enabled → http://agentshield-langfuse-web:3000/api/public/otel/v1/traces`
  followed by repeated
  `NameResolutionError: Failed to resolve 'agentshield-langfuse-web'`.
- DNS check from the pod: bare `agentshield-langfuse-web` → **FAIL**;
  `agentshield-langfuse-web.agentshield-platform` → **OK (10.96.221.168)**.

**Where:** the injected env `LANGFUSE_HOST=http://agentshield-langfuse-web:3000`
is a **bare service name**. The `agentshield-langfuse-web` Service lives only in
namespace `agentshield-platform`; the agent pod runs in `agents-platform`. A bare
name resolves **same-namespace only**, so it fails cross-namespace — the export
never leaves the pod.

**Problem (how the bare value got there):**
`deploy-controller/manifest_builder.py:237` injects the deploy-controller's *own*
`os.environ["LANGFUSE_HOST"]` into every agent pod verbatim. The chart template
(`charts/agentshield/charts/deploy-controller/templates/deployment.yaml:67`) sets
that env to the **namespace-qualified** FQDN
(`{{ .Release.Name }}-langfuse-web.{{ .Release.Namespace }}:3000`) — but that
qualifier was only added in commit **`bbed243` "enable agent tracer +
cross-namespace host (Phase 2, partial)"**. The `wf-router-sandbox` **Deployment
was materialized 2026-07-14 01:43 by an older controller that still had the bare
host**; the current controller pod (qualified env) only started 03:44. A pod
reschedule reuses the Deployment's existing env, so the stale bare
`LANGFUSE_HOST` persisted across restarts.

**Fix:** the code + chart + running controller are already correct (qualified
host). The remaining fix is operational: **re-materialize the existing agent
Deployments** (redeploy the agents) so `manifest_builder` re-runs against the
current qualified controller env → agent Deployment gets the qualified
`LANGFUSE_HOST` → the pod resolves Langfuse → OTLP export succeeds → member
traces get created and the parent envelope shows the member spans. New agents
already get the qualified host.

## Why it wasn't caught ("fixed yesterday" but wasn't)
Yesterday's traces work stamped `child.langfuse_trace_id = uuid(child_id).hex`
and asserted "the member's 26-span trace exists; the parent is a thin envelope."
That claim was **never verified against Langfuse** — the trace id was stamped
optimistically. Stamping an id the member is *supposed* to emit to is not the
same as confirming the member *did* emit. The DNS export failure sat one layer
below the stamp, invisible unless you query Langfuse for the actual observations.
The commit even labelled itself **"Phase 2, partial"** — a standing tell that the
round-trip wasn't closed.

## Lessons (generalizable)
1. **A trace id stamped is not a span ingested.** Verify observability by
   querying the backend for the actual observation count
   (`lf.fetch_trace(id).observations`), not by asserting the id you wrote. "The
   detail lives on the member trace" is a hypothesis until the member trace
   returns non-zero spans.
2. **Cross-namespace service references must be namespace-qualified.** A bare
   Service name only resolves same-namespace. Any env/URL that crosses a
   namespace boundary (agent pods in `agents-*` → platform services in
   `agentshield-platform`) must use `<svc>.<ns>` or the full FQDN. Same class as
   the `python_executor_url` default, which already uses
   `…agentshield-python-executor.agentshield-platform:8080`.
3. **A "partial" commit is unshipped.** Landing a chart change without
   re-materializing the resources that consume it (or verifying the end state)
   leaves a silent gap. Config injected at materialization time is frozen into
   the Deployment; fixing the injector does nothing to already-materialized
   pods until they're re-materialized.
4. **$0.00 cost is an observability smoke alarm.** Cost is derived from Langfuse
   `GENERATION` spans; a flat $0 across real LLM runs means spans aren't landing,
   not that the run was free.
5. **Reason from the running product.** The parent-envelope-vs-member-trace
   design was plausible on paper; only `kubectl logs` (DNS error) + the Langfuse
   404s showed the spans were going nowhere.

## Fix applied (registry-api 0.2.171 + re-materialization)
1. **Member DNS export (root cause).** Re-materialized the member agents
   (`wf-router`, `wf-payout`) via the product deploy path; the new pods inherit
   the qualified `LANGFUSE_HOST=…-langfuse-web.agentshield-platform:3000` from the
   current controller. Verified: new pod log shows `OTEL tracing enabled` with
   **zero** `NameResolutionError`, and the member trace now shows observations in
   Studio. No code change needed — the chart/controller were already correct;
   the fix was operational (stale pods).
2. **Empty parent trace.** The orchestrator now authors a span per member step on
   the parent Langfuse trace via `tracing.trace_workflow_step`, called from
   `_run_step` (forward path) and `resume_orchestration` (the parked member's
   terminal span). Member detail still lives on the member trace (run_id↔trace_id
   cost correlation preserved); the parent shows the workflow's step structure
   with a `child_trace_id` pointer for drill-down.
3. **Swallowed member-failure reason** (found while fixing #2 — the wf-payout
   failure surfaced as a bare "workflow member failed after its approval was
   decided" with an EMPTY `error_message`). The SDK *did* emit the real reason
   (`run crashed: <exc>`), but the `internal` step-update callback set the child's
   terminal `status`/`output` on `run_completed` and **never copied
   `error_message`**. Fixed: on a failed `run_completed` the callback now copies
   the step's `error_message` onto the child run, and `resume_orchestration`
   surfaces `member 'X' failed after approval: <reason>`.

   *(The trigger for the wf-payout failure itself was external: the demo
   `refund_action` tool POSTs to `https://httpbin.org/post`, which was returning
   503 / unreachable. Not a platform regression — but the platform should have
   shown the reason, hence fix #3.)*

## Tests added
- `suite-58` **T-S58-005** — the parent workflow trace carries member step-spans
  (no more empty envelope).
- `suite-58` **T-S58-006** — every member trace ingested real observations
  (directly catches the DNS/OTLP export failure; polls the real Langfuse backend).
- `suite-61` **T-S61-006** — a reactive dataset accepts a durable agent's
  eval-run without a mode-mismatch 422 (the E-0 regression guard).

## Verification checklist
- [x] Member pods inherit the qualified `LANGFUSE_HOST`; no `NameResolutionError`.
- [x] Member trace shows observations in Studio.
- [x] Parent-trace span authoring wired (`trace_workflow_step`, forward + resume).
- [x] Member-failure reason propagated to the child run + parent message.
- [x] e2e guards added (T-S58-005/006, T-S61-006).
- [x] Post-deploy of 0.2.171: ran `flow-conditional` end-to-end (real trigger →
      wf-router completed → approve → wf-payout resumed). Verified against Langfuse:
      **parent trace obs = 2** (one span per member step), **wf-router obs = 9**,
      **wf-payout obs = 18**. Parent failure now reads
      `member 'wf-payout' failed after approval: run crashed: Server error '503 …
      httpbin.org/post'` (was empty). The payout branch fails only because the
      external httpbin.org is 503 — a demo-tool dependency, not a platform bug.
