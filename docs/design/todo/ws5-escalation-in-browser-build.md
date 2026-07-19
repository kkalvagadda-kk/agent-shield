# WS-5 (in-browser SDK build) — escalation, not a degradation

**Status:** 🔴 **BLOCKED on infrastructure + one governance decision. Deliberately deferred 2026-07-15.**
**Why this doc exists:** CLAUDE.md's Strategic Alignment rule — *"if the environment cannot support the
ultimate goal, do NOT silently degrade the parameters. Stop, explain the fundamental limitation, and propose a
structural pivot."* WS-5's plan is **stable in design and false in four premises**. Building it as written
would produce a green slice that either cannot run a user's agent, or runs it **ungoverned**.

The full re-grounding (R1–R16) lives in `docs/plan/execution-models-v2/ws5/tasks.md`. This is the decision
brief.

> **The goal is not "a Kaniko Job exits 0".** It is *a non-DevOps user writes `agent.py` in a browser tab and
> gets a **running, governed** durable agent.* Every blocker below is triaged by whether it kills that.

---

## The four false premises

### 1. 🔴 There is no image registry. Kaniko has nowhere to push.

`registry.internal/agentshield/*` is a **naming convention that resolves nowhere** — not a Service, not in any
chart (`grep -rn registry.internal charts/` → 0 hits), not in DNS. There is **no `docker push` anywhere in
`scripts/`** (grep → 0 hits). Images work today only because `deploy-cpe2e.sh` runs `docker build` on the host
and this kind-backed cluster resolves the **host's local image store** with `pullPolicy: IfNotPresent`.

**Kaniko runs in-cluster and can only emit to an OCI registry over HTTP(S).** It cannot write into the host's
image store, and the host-image-sharing affordance does not work in reverse. So:

> **A real registry is a WS-5 deliverable, not an assumption.** And the *push* endpoint (in-cluster DNS) and
> the *pull* endpoint (what the kubelet resolves) are **different strings for the same storage** — that must be
> proven empirically **before** any UI work, or the slice dies at the last step with a built image nothing can
> pull.

**Options:**
| | Approach | Cost | Risk |
|---|---|---|---|
| **A** *(recommended)* | In-cluster `registry:2` (Deployment + Service + PVC), pinned top-level, `IfNotPresent` unaffected for existing images | ~half a day incl. the push/pull-endpoint proof | Dev-grade (no auth/TLS) — acceptable for the dev cluster, **must not** be the prod story |
| **B** | Point at an external registry (ECR/Harbor) | Credentials + egress + per-environment config | Couples the dev loop to a cloud account; offline dev breaks |
| **C** | Skip the registry; have Kaniko `--no-push --tarPath` and load the tar | Sounds cheap | **Dead end** — nothing in-cluster can load a tar into the kubelet's store; this is the "degrade the parameters" trap |

**Recommendation: A**, gated behind a checkpoint that proves push-then-pull round-trips **before** a line of
Studio code is written. If that checkpoint fails, WS-5 stops and escalates again — it does **not** degrade into
"the build succeeds but nothing can run it".

### 2. 🔴 The plan's Dockerfile is a supply-chain bug

`contracts/build-service-api.md` bakes `RUN pip install --no-cache-dir agentshield-sdk`. The SDK
(`agentshield-sdk` v0.1.1, `sdk/pyproject.toml`) is **local-only and has never been published** (no
`twine`/`publish` in any script — grep → 0 hits). That line reaches **public PyPI** and installs either nothing
or **whatever squatter owns the name** — into an image that is then handed platform credentials.

**Fix (free, and it shrinks the blast radius):** a prebaked **`agent-base`** image using the shipped pattern
from `services/declarative-runner/Dockerfile` — `COPY sdk/ /tmp/sdk/` + `RUN pip install /tmp/sdk/`. The
user's build then only does `COPY agent.py`. This also **deletes the PyPI egress requirement** the plan's
NetworkPolicy was built to contain (see 4).

### 3. 🔴 The specified entrypoint cannot run — and its only working fallback ships **ungoverned** agents

The contract's `CMD ["python", "-m", "agentshield_sdk.server"]` fails twice over:
- `sdk/agentshield_sdk/server.py` defines `app = FastAPI(…)` (`:76`) with **no `__main__` block and no
  `uvicorn.run`** → the module imports and **exits**.
- Immediately below sits `runner: Any = None` — *"Set by cli.py before uvicorn starts"* — so even if it
  served, **every `/chat` and `/run` would hit a `None` runner**.

The real entrypoint is the `agentshield` console script → **`dev`** (`cli.py:48`). **But `dev` fails OPEN into
mocks:**
- `--safety/--no-safety` **defaults to `False`** and *clears* `AGENTSHIELD_SAFETY_URL` (`cli.py:50-54`) →
  `mock_safety` (`safety_client.py:5`).
- OPA falls back to **`mock_opa`** whenever `AGENTSHIELD_OPA_URL` is unset (`opa_client.py:23`).

> Shipping browser-built agents on `agentshield dev` would ship **agents whose governance silently no-ops** —
> on a platform whose entire reason to exist is that governance. This is the single most dangerous line in the
> WS-5 plan, and the plan does not mention it.

**Fix:** add a **fail-closed `agentshield serve`** that refuses to start when `AGENTSHIELD_OPA_URL` /
`AGENTSHIELD_SAFETY_URL` are unset, sharing **one** wiring helper with `dev` (one builder, two entrypoints —
the parity rule; two copies would drift, which is this repo's #1 bug class). Because the declarative-runner
image `pip install`s the SDK, this **forces a `declarative-runner` bump** — and
`scripts/smoke-test-cp1-e3-constitution.sh` already enforces exactly that coupling.

### 4. 🟡 The isolation boundary the plan relies on is not enforced here

The plan's safety posture rests on *"the build Job has a NetworkPolicy restricting egress"*. This cluster's CNI
is **kindnet**, which ships **no NetworkPolicy controller** — a `NetworkPolicy` object is **accepted and
silently ignored**. (The repo already carries this fiction in `infra/network-policies/` and the event-gateway
chart.)

**Claiming an unenforced control is worse than not having it.** WS-5 should still author the NetworkPolicy (it
is correct on any enforcing CNI and it is the artifact prod needs), but must **gap-ledger it as
`not-enforced (environmental)`** and assert only that the object *exists* — never that egress is blocked. The
real mitigation available today is **structural**: premise 2's `agent-base` removes the build's need for
network egress at all.

---

## What the plan got right (keep it)

- The `CreateAgentPage` stub read is **exactly right**: `CODE_TEMPLATE` (`:369`), `CodeForm` (`:932`),
  `metadata: { source_code: … }` (`:974`), and the literal *"Source code editor (textarea placeholder for
  Monaco)"* (`:1027`). That `metadata.source_code` write is **a write with no reader** — the stub never built
  anything — so WS-5 retires it rather than extending it.
- The overall shape (source → build Job → image → version → deploy) is sound.

## Corrections that shrink the slice

- **No `services/build-service/`.** registry-api **already** has cluster-wide Job RBAC, **already** creates K8s
  Jobs (`k8s.py:_create_eval_job_sync:113`), **already** writes ConfigMaps, **already** streams SSE. The
  shipped precedent is **eval-runner: a Job image launched by registry-api, not a standalone service**. A
  build-service would add a second Job-creation path (the #1 bug class), a second tag to forget, an internal
  callback hop, and **zero** isolation — the isolation boundary is the *Job*, not its creator.
- **No new object store.** "Reuse the existing MinIO" is half true and the false half kills the rationale: the
  running MinIO is **Langfuse's subchart** (creds hardcoded in values.yaml, bucket `langfuse-media`); the
  *platform* MinIO is declared twice and **`enabled: false`** in both. Put `source_code` in the
  `agent_versions` row (migration adds `source_code` + `build_status`; **`image_tag` already exists**).
- **The plan contradicts itself** on when the version row exists (the contract returns/streams by `version_id`;
  the data-model says no row until `succeeded`). The contract's own shape proves the row must exist up-front;
  fail-closed is preserved **structurally** — no image ⇒ nothing to deploy, and `eval_passed=false` ⇒ the
  production gate refuses it (`deployments.py:560`).
- `EditAgentPage.tsx` **does not exist** (edit lives in `AgentDetailPage.tsx` + `SettingsTab.tsx` — the same
  drift WS-4's plan had); Monaco **is not a dependency**, and `@monaco-editor/react` CDN-loads from jsdelivr by
  default, which must be pinned to the bundled copy.

---

## Recommended sequence when WS-5 is picked up

1. **[CP1a] Prove the registry** — stand up `registry:2`; prove **push (in-cluster DNS) → pull (kubelet)**
   round-trips with a throwaway image. **If this fails, stop and escalate.** No UI until this is green.
2. **[CP1b] Prove the entrypoint** — `agentshield serve`, fail-closed on missing OPA/safety; prove a
   hand-built `agent-base` + `COPY agent.py` image runs and its governance is **live, not mocked**.
3. Only then: the build Job, the version row, the Studio editor.

**Estimated:** ~2–3 focused sessions. **Do not interleave** with other slices — it changes the container
entrypoint every SDK agent uses.

## Why this was deferred rather than attempted

WS-5 was reached at the end of a session that had already landed 9 slices. Two of its blockers (no registry,
mock-governance entrypoint) are the kind that produce a *green slice that doesn't work* — the exact failure
this session spent hours hunting (`docs/bugs/e3-never-ran-tag-not-bumped.md`,
`docs/bugs/webhook-eval-door-silent-failures.md`: code present, checks green, feature absent). Starting it with
a few hours left would have optimised for the appearance of completion.

**Related:** `docs/plan/execution-models-v2/ws5/tasks.md` (the full R1–R16 re-grounding + 33 tasks, written and
ready), `ws5/plan.md`, `ws5/data-model.md`, memory `todo_sdk_editor`.
