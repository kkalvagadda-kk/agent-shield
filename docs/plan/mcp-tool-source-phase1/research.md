# Research — MCP as a Tool Source, Phase 1

This document covers two kinds of decisions:

1. **Grounding corrections** — facts discovered by reading the *running* code (not the design docs) during planning, that materially change how Phase 1 must be implemented. Numbered continuing from the architecture doc's own §2 list (which stopped at 7).
2. **Implementation-level decisions** the architecture doc and requirements doc deliberately left unpinned (module structure, library choice, exact request/response shapes, field semantics) — each with alternatives considered and why the chosen option won.

Where the architecture doc (`docs/design/mcp-tool-source-architecture.md`) or `docs/decisions.md` (Decisions 15, 27, 28, 29) already settled something, this doc does not re-derive it — it is cited by reference.

---

## Part A — New Grounding Corrections (continuing the architecture doc's §2 numbering)

### 8. The live OPA enforcement path is `bundle_generator.py` + the checked-in static `opa_policy/agentshield.rego`, NOT `policy_generator.py`'s per-agent Rego

The architecture doc's §2 point 2 says OPA policy generation happens at deployment time via `policy_generator.generate_and_store` reading `AgentVersion.tools`. That is true as a *call site*, but reading the actual code shows `policy_generator.py`'s per-agent Rego (`package agentshield.agent.{name}`) is **legacy and inert at runtime**: its own docstring says "Phase 9.1 completion: per-agent ConfigMap is retired... the ConfigMap write is a no-op." The row it writes to `agent_policies` (`risk_map`, `tool_allowlist`, `rego_policy`) is kept only as an audit/history artifact — nothing serves it to an OPA sidecar.

The system that actually authorizes every tool call today is:
- `services/registry-api/opa_policy/agentshield.rego` — one **static**, checked-in Rego file (`package agentshield`), shared by every agent's OPA sidecar.
- `services/registry-api/bundle_generator.py::generate_bundle_data()` — builds the **data** (`data.agents[sa_subject]`, `data.grants[team]`) the static policy evaluates against, live, on every request.
- `services/registry-api/routers/bundle.py` — serves `GET /api/v1/bundle/bundle.tar.gz` (data.json + policy.rego, freshly generated **on every poll**, no ConfigMap patch trigger to manage). OPA sidecars poll this every 30–60s.
- `sdk/agentshield_sdk/opa_client.py::check_tool()` — the client every tool call actually goes through; it POSTs to `/v1/data/agentshield` and reads back `{allow, require_approval, reason, deny_reason}`.

**Consequence for Decision 27's `allow_deanonymize` field:** it must be threaded through this real pipeline — `bundle_generator.py`'s per-tool dict emission (both the `agents[sa_subject].tools` list and the `grants[team]` list) and the static `agentshield.rego`'s rule set (mirroring how `resolved_risk`/`require_approval` are already computed) — not through `policy_generator.py`'s per-agent Rego, which no sidecar ever reads. `policy_generator.py` is still touched (kept in parity for its own audit-trail purpose, since it's a real DB row a human might inspect), but it is **not** the enforcement path and must never be treated as one.

This is the single most important grounding correction for this plan — every "OPA decision field" or "Rego generation" task in plan.md targets `bundle_generator.py` + `opa_policy/agentshield.rego` as primary, `policy_generator.py` as a secondary audit-parity update.

### 9. `opa_decisions` (the audit log FR-MCP-24 requires) has zero writers today, for any tool type

`services/registry-api/routers/opa_decisions.py` exposes `POST /api/v1/opa-decisions/` with a docstring claiming it is "Called by the OPA sidecar after every policy evaluation." That's aspirational, not real: a stock OPA sidecar cannot make an outbound callback after evaluating a Rego query — nothing in this codebase calls this endpoint. Grepping the entire tree for `OPADecision(` / `/opa-decisions` outside `models.py`/`routers/opa_decisions.py`/`main.py` returns nothing. The table exists, the router exists, the query side (`GET /api/v1/opa-decisions/`) is real — but the write side is orphaned. **This is true for native/http/python tools today, not just MCP.**

**Naming coincidence, not a relationship:** `services/registry-api/models.py`'s SQLAlchemy ORM class for this audit table is *also* named `OPADecision` — same name, same casing, as the unrelated `sdk/agentshield_sdk/opa_client.OPADecision` dataclass this plan extends with `allow_deanonymize`. They live in different services, different languages' import graphs never cross, and neither imports the other. Do not conflate them when implementing Task 6 (which touches the SDK's `OPADecision` dataclass) and Task 8 (whose `record_decision()` call constructs a payload for the registry-api endpoint that, server-side, instantiates `models.OPADecision`) — two classes, same name, no code-level relationship.

FR-MCP-24 says an MCP tool call must write an audit record "same as native tools" — but native tools don't either. Two options: (a) log FR-MCP-24 as unsatisfiable and defer it whole, or (b) since Decision 27 already requires touching the exact seam that has the decision in hand (`governed_tool`, right after `opa_client.check_tool()` returns), add the write there, generically, for all four tool types, in the same change.

**Decision: (b).** This is a few extra lines at a seam already being edited, it turns a currently-false platform claim ("every tool call is audited") into a true one, and it is exactly the "fix the class, not the instance" instinct the project's own bug-fixing discipline calls for — a native-tool-only fix here would be the same kind of special-casing Decision 27 itself rejected. It is folded into Task 8 (wiring the gate into `governed_tool`) as a `record_decision()` call, best-effort (never raises — an audit-log write failing must not block or falsely allow a tool call; it is observability, not authorization). Fail-closed authorization semantics are untouched; only the write is best-effort.

Included in the regression sweep (Task 14 in plan.md): confirm a *native* tool call also now produces an `opa_decisions` row (proving the fix is generic), not just the new MCP suite.

### 10. `AgentVersion.tools` has two independent snapshot sites for sandbox, and a third, weaker path for production

Two call sites build the `{name, risk}` tools list that ultimately reaches `bundle_generator.py`'s `agents[sa_subject].tools`:
- `services/registry-api/routers/versions.py:93` — `{"name": t.name, "risk": t.risk_level or "low"}`, from a live `Tool` join (used when a new `AgentVersion` is minted).
- `services/registry-api/routers/deployments.py:505` — the same shape, from the same live join (used at sandbox-deploy time).

Both must be extended to also carry `"pii_deanonymize_allowed": bool(t.pii_deanonymize_allowed)` (plan.md Task 6), or a tool's de-anonymize permission would silently never reach the sandbox OPA bundle.

**Production is different and weaker.** `bundle_generator.py`'s production leg reads `(pv.config_snapshot -> 'tools')`, i.e. `PublishedVersion.config_snapshot['tools']`, which is copied verbatim from `AgentVersion.config_snapshot` (`routers/catalog.py:569`) — and *that* dict's `'tools'` key comes from `agent_config.build_config_snapshot()` flattening `agent.metadata_['tools']` (a client-authored mirror the Studio agent-create/edit form writes), **not** a fresh join against live `Tool` rows the way the sandbox leg is. This is a **pre-existing parity gap**, already tracked in `docs/design/sandbox-production-parity-architecture.md`, and it already affects `risk_level` itself today (a `risk_level` edit after an agent's `metadata.tools` was captured may not reach a production bundle either). Extending `pii_deanonymize_allowed` through Studio's agent metadata payload construction would be scope creep into a separately-tracked architecture problem that predates this design and applies to every tool type equally.

**Decision:** Phase 1 of MCP tool source does **not** fix this. It is logged in the gap ledger as *"pre-existing, not deepened by this design"* — identical stance to the architecture doc's own treatment of the legacy ungoverned standalone-tool-node path. Concretely: `allow_deanonymize` (and, already today, `risk`-driven `require_approval`) may not be accurate for a **production** deployment whose `metadata.tools` mirror is stale. Sandbox is unaffected (it reads the live join). This is called out explicitly rather than silently narrowing FR-MCP-51/52's scope.

---

## Part B — Implementation-Level Decisions

### B1. MCP protocol client library: the official `mcp` Python SDK, not a hand-rolled JSON-RPC client

**Chosen:** `mcp` (PyPI package, the reference Model Context Protocol Python SDK), specifically `mcp.client.streamable_http.streamablehttp_client` (async context manager yielding read/write streams) + `mcp.ClientSession` (`.initialize()`, `.list_tools()`, `.call_tool(name, arguments)`).

**Alternatives considered:**
- *Hand-rolled httpx + manual JSON-RPC framing.* Rejected — MCP's `streamable_http` transport has real protocol-level behavior (session negotiation, SSE-vs-plain-JSON response handling, capability negotiation during `initialize`) that is exactly the kind of protocol logic a hand-rolled client would get subtly wrong. Re-implementing it duplicates the one piece of work the official SDK already solved, and MCP Proxy's entire reason to exist is to be the place that piece of complexity lives — reinventing it inside the proxy defeats the point.
- *A different community MCP client library.* Rejected — the official SDK is maintained by the protocol's own stewards, is the dependency every other MCP-client implementation in the ecosystem (Claude Desktop, other agent platforms) is built on, and has no licensing or maintenance red flags.

**Consequence:** `services/mcp-proxy/requirements.txt` pins `mcp>=1.2,<2.0` (verify the latest stable minor at implementation time via `pip index versions mcp`; do not silently jump a major). The exact attribute names above (`inputSchema` camelCase on the wire, exposed as `.inputSchema` on the SDK's `types.Tool` pydantic model) should be re-verified against the installed version's source during Task 3 — this plan's contracts describe the *shape*, not a byte-for-byte guarantee against a library the plan's author could not execute against.

### B2. MCP Proxy internal module structure

Mirrors the `python-executor` skeleton's simplicity (single `main.py` + `Dockerfile` + `requirements.txt`) but splits into small modules because MCP Proxy, unlike `python-executor`, is stateful and has three genuinely separate concerns: wire protocol, session lifecycle, and credential resolution. One file would make the credential-resolution logic (K8s API calls) and the MCP wire-protocol logic (async streams) hard to test in isolation.

```
services/mcp-proxy/
  main.py            — FastAPI app; /health, /internal/discover, /internal/tools-call
  config.py           — env vars (REGISTRY_API_URL, PORT, session cache TTL)
  schemas.py          — pydantic request/response models
  mcp_client.py       — thin wrapper over mcp.client.streamable_http + mcp.ClientSession;
                        initialize(), list_tools(), call_tool()
  session_cache.py     — per-replica in-memory {server_id: (ClientSession, last_used)} dict;
                        get_or_create(server_id, connect_fn), evict on error
  credentials.py       — resolve_auth_headers(server_id) -> dict[str,str] | None;
                        calls registry-api for server row + secret-ref, then reads the
                        K8s Secret directly
  k8s_secrets.py        — thin K8s client wrapper (kubernetes python client,
                        in-cluster config, read_namespaced_secret only — mirrors
                        registry-api/k8s.py's _init_k8s() pattern but READ-ONLY)
  Dockerfile
  requirements.txt
```

**Alternative considered:** one flat `main.py` (matching `python-executor` exactly). Rejected — `python-executor` has no state and no outbound service dependency; MCP Proxy has both, and CLAUDE.md's own bar ("no bandaid... would the next developer trip over the same class of bug") argues for separating "how do I resolve credentials" from "how do I speak MCP" now, since Phase 2/3 (health-check loop, per-server-pod routing) will add to `session_cache.py`/`credentials.py` independently.

### B3. MCP Proxy resolves server + credentials by calling registry-api back, not by receiving them inline

Confirmed directly from the architecture doc's own Data Flow section (§3, step 2): registry-api's discover call is already specified as `POST /internal/discover {server_id}` — just the ID. This plan extends the same shape to `/internal/tools-call` (`{server_id, mcp_tool_name, args}`, no server URL/transport/credentials inline) for one reason: MCP Proxy owns credential resolution (per the architecture doc's Key Decision), and the *only* place that knows how to turn `auth_config_id` into usable credentials is MCP Proxy's own K8s Secret read — passing raw connection details on every call would mean the caller (SDK/runner) also needs to know things it has no business knowing (this server's transport config, whether it's external), duplicating data that already lives in `MCPServer`.

MCP Proxy therefore calls back into registry-api on a cache-miss:
1. `GET {REGISTRY_API_URL}/api/v1/mcp-servers/{server_id}` → `server_url`, `transport`, `transport_config`, `auth_config_id`, `is_external`.
2. If `auth_config_id` is set: `GET {REGISTRY_API_URL}/api/v1/auth-configs/{auth_config_id}/secret-ref` → `k8s_secret_ref` (this endpoint already exists and already re-materializes a missing K8s Secret from the durable encrypted copy — reused as-is, zero new registry-api code for this step).
3. Direct `kubernetes` client read of that Secret's data in `agentshield-platform`, via MCP Proxy's own ServiceAccount (RBAC: `get` on `secrets`, scoped to `agentshield-platform`, nothing else — no `list`/`watch` needed since the secret name is always known).

Both the server-row lookup and the secret-ref lookup are cached per `server_id` in `session_cache.py` alongside the live `ClientSession`, refreshed on a cache miss or when a `tools-call` gets an auth error (401/403) — avoids a registry-api round trip on every single tool call, while staying correct after a credential rotation (a rotation changes the K8s Secret's *contents* under the same name, so even a stale cached `k8s_secret_ref` string still reads fresh values — only the *name* is cached, never the secret value itself, and never for longer than the process lifetime).

Both calls use plain unauthenticated `httpx` requests (no Authorization header) — this matches the existing precedent of `deploy-controller/tool_secrets.py`'s calls to the exact same `/auth-configs/{id}/secret-ref` endpoint today: internal service-to-service calls within `agentshield-platform` rely on NetworkPolicy-based trust, not a service JWT. Not introducing a new auth mechanism here is deliberate — adding one would be scope creep unrelated to MCP.

### B4. `Tool.pii_deanonymize_allowed` — a new per-tool boolean column, not an overload of `risk_level`

FR-MCP-51 says `allow_deanonymize` should follow "the same risk-driven generation path as everything else — no MCP-specific policy branch." Read narrowly, this could mean *derived from* `risk_level` (e.g., "low and medium tools may de-anonymize, high/critical may not"). This plan rejects that reading: de-anonymization (should a tool receive **real PII** in its call arguments) is an orthogonal concern to risk (should this tool call be allowed to execute at all, and does it need a human's approval first). Piggy-backing a security-relevant boolean onto a field that already drives two other decisions (`allow`, `require_approval`) is exactly the kind of "one field means three things depending on which reader" implicit coupling this project's own constitution warns against (CLAUDE.md: "Would the next developer touching this code trip over the same class of bug?").

**Decision:** add `Tool.pii_deanonymize_allowed: bool NOT NULL DEFAULT false` — mirroring the *existing, working* precedent in this exact codebase: `Tool.side_effecting` (added migration 0063, "Eval v2 E-2") is a second orthogonal per-tool boolean, defaulted fail-closed, exposed in `ToolCreate`/`ToolUpdate`/`ToolResponse`, carried onto the resolved callable as an attribute the governance seam reads (`fn.side_effecting`), and threaded into `bundle_generator.py`'s / Rego's per-tool data the same way `risk` already is. `pii_deanonymize_allowed` follows the identical shape. "Same risk-driven generation path as everything else" is satisfied at the *mechanism* level (same bundle_generator → same static Rego → same OPA response schema), not by reusing `risk_level`'s value.

Default `false` is the fail-closed choice: no tool receives real PII in its arguments unless an admin explicitly marks it trusted to handle PII — the same posture Decision 27's whole gate exists to enforce.

### B5. The structured de-anonymize primitive substitutes locally from cached PII mappings — it does not call Presidio's `/deanonymize` HTTP endpoint

The existing free-text de-anonymize (`orchestrator.py::_scan_output_inner`, untouched by this plan) calls Presidio's own `/deanonymize` service endpoint, passing the full text plus an `{entity_type: original_text}` dict, and lets Presidio do the substitution. Reading `pii_store.py` shows every `PiiMapping` row already carries both `anonymized_text` (the literal placeholder token Presidio minted, e.g. `<PERSON_0>`) and `original_text` — the exact string pair needed for substitution.

**Decision:** the new structured primitive (`Orchestrator.deanonymize_args`) does a **local, recursive string replace** — for every mapping row for `(session_id, agent_name)`, replace every literal occurrence of `mapping.anonymized_text` with `mapping.original_text` inside every string value of the args dict (recursing into nested dicts/lists; non-string values pass through untouched). It does **not** call Presidio's HTTP endpoint.

**Why:** (a) the substitution is a pure string operation once the mapping rows are in hand — calling out to Presidio's engine adds a network hop and a new failure mode for something that's a dict comprehension; (b) it keeps the new capability available even if Presidio itself is down but Postgres (where mappings live) is up — appropriate, since this is a lower-stakes, best-effort enrichment (see B6), not a security-critical scan; (c) it avoids depending on Presidio's own `/deanonymize` request shape, which was designed for free text, not a nested JSON args tree.

### B6. De-anonymize failure is fail-open (proceed with placeholders); output-scan failure stays fail-closed

`scan_output`/`scan_input` are explicitly fail-closed today (`safety_client.py`'s docstring: "If AGENTSHIELD_SAFETY_URL is set but unreachable → FAIL CLOSED"). This plan keeps that posture **unchanged** for the per-tool-call output-scan step (FR-MCP-50/31) — a failed scan on a tool result must not let unscanned content flow to the LLM, same reasoning as the once-per-turn scan already has.

The **new** de-anonymize-args step is different: if it fails (Safety Orchestrator unreachable, or the new endpoint errors), `governed_tool` logs a warning and proceeds to execute the tool call with the **original, still-anonymized** args. This is a deliberate, narrower posture than fail-closed, for a specific reason: a failure here means the tool receives placeholder text instead of real values — a *functional* degradation (the tool call may do the wrong thing or fail on its own), not a *security* regression (nothing unsafe is exposed; if anything, less real data reaches the tool than intended). Fail-closed here would mean any Safety Orchestrator hiccup blocks every tool call that happens to carry a PII placeholder, which is a much larger blast radius for a much smaller safety benefit than fail-closed output-scanning has. This distinction (fail-closed for "don't let untrusted/unscanned content through," fail-open-with-degradation for "best-effort enrichment") is stated explicitly here so a future reader does not "fix" the asymmetry by making both paths fail-closed without understanding why they differ.

### B7. Output-scan applies the *same* `scan_output` call already used for the once-per-turn scan — including its de-anonymize side-effect — to each tool result

Reusing `scan_output` verbatim per tool call (per FR-MCP-50's literal text: "Reuses the Safety Orchestrator's `scan_output` primitive... called per tool call instead of only once per turn") means the *existing* free-text de-anonymize step inside `_scan_output_inner` also runs on every tool result, not just the final turn message. In principle this could substitute a real PII value into text that is about to re-enter the LLM's context (a tool's raw result), which is a different exposure surface than the final-message case it was designed for (there, the recipient is a human; here, the recipient is the model itself, which could theoretically be manipulated by a prompt-injected tool response into echoing something it swallowed).

**Assessment:** the practical risk is low — `_scan_output_inner`'s de-anonymize only replaces text that **exactly matches** an existing mapping's `anonymized_text` token, which was minted for a specific *user input* anonymization earlier in the *same session*. A tool's own fresh output essentially never contains that literal token string unless the tool happens to echo back one of its own input arguments verbatim (e.g., a confirmation message repeating a name the caller passed in) — a real but narrow edge case, and one that already exists identically for the once-per-turn case today. This plan does not change `_scan_output_inner`'s behavior to add a "skip de-anonymize when called per-tool" mode, because that would require a new parameter threading through an endpoint whose only other caller doesn't need it, for a narrow edge case — instead, this is called out explicitly here and in the gap ledger as a known, low-probability, pre-existing-shaped risk, worth revisiting only if an actual incident surfaces it as real.

### B8. Env var naming: `AGENTSHIELD_MCP_PROXY_URL` (SDK/runner side), `MCP_PROXY_URL` (registry-api side)

The codebase has two conventions in active use, not one:
- SDK config (`sdk/agentshield_sdk/config.py`) and, partially, declarative-runner config (`services/declarative-runner/config.py`) prefix cross-service URLs with `AGENTSHIELD_` (`AGENTSHIELD_SAFETY_URL`, `AGENTSHIELD_OPA_URL`, `AGENTSHIELD_REGISTRY_URL`) — except declarative-runner's own `PYTHON_EXECUTOR_URL`, which is unprefixed, an existing inconsistency this plan does not touch.
- registry-api's own outbound clients (`embedding_client.py`'s `EMBEDDING_SIDECAR_URL`, `k8s.py`'s `REGISTRY_API_URL`) are unprefixed.

**Decision:** new variables follow the *majority* convention on each side rather than the local exception — `AGENTSHIELD_MCP_PROXY_URL` in both `sdk/agentshield_sdk/config.py` and `services/declarative-runner/config.py` (default `http://mcp-proxy.agentshield-platform:8080`), and unprefixed `MCP_PROXY_URL` in `services/registry-api/mcp_proxy_client.py` (default `http://agentshield-mcp-proxy.agentshield-platform.svc.cluster.local:8000`, matching `embedding_client.py`'s exact default-host format). Do not invent a third pattern.

### B9. Studio: MCP servers get their own `mcpServersApi.ts` module, not additions to `registryApi.ts`

Knowledge Bases (the closest recent precedent, per the architecture doc's own recommendation) has its own `knowledgeApi.ts`, separate from `registryApi.ts`, despite riding the same shared `http` axios instance (`import { http } from "./registryApi"` — confirmed in `knowledgeApi.ts`'s own header comment: "Rides the SHARED axios `http` instance from registryApi"). MCP servers are a distinct resource with their own lifecycle (register/sync/delete) that has nothing to do with `Tool` CRUD, so this plan creates `studio/src/api/mcpServersApi.ts` the same way, importing `http` from `registryApi.ts` rather than duplicating the axios instance. `RegistryTool` (in `registryApi.ts`) still gains the three new optional fields it needs (`mcp_server_id`, `mcp_tool_name`, `mcp_server_name`, `pii_deanonymize_allowed`) because those describe a `Tool` row, which is `registryApi.ts`'s existing domain — only the *server* resource gets a new file.

### B11. De-anonymize runs immediately before the *real* dispatch, strictly after the eval-record short-circuit — never before it

The architecture doc's gate ordering is stated as "authorize → approve → de-anonymize → execute → scan, always in that order" (§3). Read completely literally, "de-anonymize → execute" would place the de-anonymize step before `governed_tool`'s existing eval-mode short-circuit (`_should_record(fn)` / `_record_side_effect`, Eval v2 E-2), which — under `eval_mode=record` — never calls the real `fn(**kwargs)` at all; it records the call's args and returns a mock instead. If de-anonymize ran *before* that check, a de-anonymized (real-PII-containing) `kwargs` dict would be the thing `_record_side_effect` persists into the eval trace — moving real PII into a durable eval record that was never one of the two places FR-MCP-30 is trying to keep it out of (the LLM context, and an un-permitted tool). An eval trace is a third destination the requirement text didn't consider, and it is exactly the kind of place PII should not land by accident.

**Decision:** the de-anonymize step is placed structurally *inside* the "3. Deliver" branch of `governed_tool`, specifically **after** the `_should_record(fn)` early-return and **immediately before** the real `fn(**kwargs)` call. This still satisfies "de-anonymize → execute" literally (de-anonymize is the last thing before the real call) while adding the constraint the architecture doc's simplified list didn't state: de-anonymize never runs on a call that eval-mode is about to mock rather than execute. plan.md Task 8 implements it at this exact point, not earlier.

### B10. Fixture MCP server for the bash e2e suite runs inside the MCP Proxy pod itself, using the same `mcp` SDK

Testing real wire-protocol discovery + `tools/call` end-to-end needs a real MCP server to talk to. Rather than stand up new cluster infrastructure (a Job/Deployment/Service) purely for a test fixture, `scripts/e2e/fixtures/stub_mcp_server.py` is a small script (built on `mcp.server.fastmcp.FastMCP`, the same official SDK, run with `transport="streamable-http"`) that is copied into the `mcp-proxy` image (same `COPY` step as the app code) and started via `kubectl exec ... python3 fixtures/stub_mcp_server.py &` inside the already-running `mcp-proxy` pod on a local port (`127.0.0.1:9999`). Because it runs in the *same* pod/network-namespace as MCP Proxy, registering an `MCPServer` row with `server_url=http://localhost:9999/mcp` lets MCP Proxy dial out to a real, protocol-compliant MCP server without any new cluster object. This is a test-only convenience (never imported by production code paths) — flagged as such in the suite's header comment so nobody mistakes it for a real dependency.

**Alternative considered:** a separate K8s Job/Deployment as a standing fixture. Rejected for Phase 1 — adds a permanent cluster object and a Service/DNS entry to maintain for a single e2e suite, when the in-pod approach gets full protocol coverage (real HTTP wire format, real `initialize`/`tools/list`/`tools/call` framing) for zero new infrastructure. Revisit if a second suite needs the same fixture independently.
