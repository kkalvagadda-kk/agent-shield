# WS-5 — In-Browser SDK Agent Build: Design & Artifact Index

**Status:** 🟡 **Deferred, not blocked-forever.** The only unbuilt item of Execution Models v2 (9/10 shipped).
**Owner decision pending:** one infra choice gates everything (see §7).
**Last grounded:** 2026-07-16.

> This is the **durable design reference** — what WS-5 is, why it's shaped the way it is, and where every
> execution artifact lives. It is intentionally short. The blow-by-blow lives in the linked docs; read this
> first to decide *whether and how* to pick WS-5 up.

---

## 0. Artifact index (all live and ready)

| Artifact | Path | What it is |
|---|---|---|
| **Escalation brief** | [`ws5-escalation-in-browser-build.md`](./ws5-escalation-in-browser-build.md) | The decision brief — the four false premises, the options, why it was deferred. **Read before starting.** |
| **Plan** | `docs/plan/execution-models-v2/ws5/plan.md` | Full slice plan (design-stable; specifics indicative). |
| **Tasks** | `docs/plan/execution-models-v2/ws5/tasks.md` | **28 impl + 5 checkpoint tasks**, re-grounded against live code (R1–R16). Authoritative for execution. |
| **Data model** | `docs/plan/execution-models-v2/ws5/data-model.md` | `agent_versions.source_code` + `build_status`; migration **0065**. |
| **Contracts** | `docs/plan/execution-models-v2/ws5/contracts/` | Build API + Dockerfile contract (both corrected in the tasks). |
| **This doc** | you are here | Design + index. |

Related memory: `todo-sdk-editor` (blocker summary, pickup order).

---

## 1. Goal (and the honest reframe)

**Goal:** a non-DevOps user writes `agent.py` in a browser tab → gets a **running, governed** durable agent,
with **no local Docker toolchain**.

**The reframe that changes WS-5's priority (verified live 2026-07-16):**

> **The SDK path already works end-to-end today — WS-5 is an *ergonomics* win, not a *capability* unlock.**

Proven by actually deploying one: create an `sdk` agent → create a version with a **locally-built**
`image_tag` (`registry.internal/agentshield/echo-agent:0.1.0`) → deploy → pod `Running` in ~5s, running the
**user's own image** with the OPA governance sidecar attached. It works because Docker Desktop **shares the
host image store with the nodes** (host/node/pod image IDs are byte-identical — see
[`project-kind-cluster-deploy`] memory), so a `docker build` on the laptop is immediately visible to the
kubelet. **No registry is needed for the local path.**

**Consequence:** nobody is *blocked* from custom-Python agents; they just need Docker locally. WS-5 removes
that requirement. So the sensible order is:

1. **Now (~1–2h, not WS-5):** expose an `image_tag` field in Studio's create/deploy UI and retire the dead
   `metadata.source_code` textarea (a write nothing reads — the "Code" option today produces a success toast
   for an agent that can never run). This makes the *working* path honest and self-service **today**. Tracked
   as **§8 below**, separate from WS-5.
2. **Later (WS-5):** automate the `docker build` behind that field via an in-cluster build.

---

## 2. Why WS-5 is real infra, not a UI change (the four false premises)

Full detail in the escalation brief; in one line each:

1. **🔴 No image registry.** Kaniko runs *in-cluster* and can only push to an OCI registry over HTTP — it
   **cannot** write the host image store the local path relies on. A registry is a WS-5 **deliverable**, and
   its **push endpoint (in-cluster DNS) vs pull endpoint (kubelet)** are different strings for the same
   storage — prove the round-trip before any UI.
2. **🔴 `pip install agentshield-sdk` is a supply-chain bug** — the SDK is never published; that line installs
   a PyPI squatter into a credentialed image. Use the shipped `COPY sdk/` + `pip install /tmp/sdk/` via a
   prebaked `agent-base`.
3. **🔴 The entrypoint ships UNGOVERNED agents.** The specified `CMD` can't run; the only working fallback
   (`agentshield dev`) **fails open into `mock_opa` + `mock_safety`**. Needs a fail-closed `agentshield serve`.
4. **🟡 NetworkPolicy is not enforced** (kindnet has no NP controller) — the isolation the plan claims is
   silently ignored. Author it, but ledger it honestly and rely on structural mitigation (premise 2 removes
   the build's egress need).

---

## 3. Architecture (the shape once the premises are fixed)

```
Studio (Monaco editor)
   │  POST /agents/{name}/builds  { source_code }
   ▼
registry-api ── creates a Kaniko BUILD JOB (NOT a new service — reuses k8s.py Job RBAC,
   │            the eval-runner precedent: a Job image launched by registry-api)
   │            • agent_versions row created UP-FRONT (status=pending, image_tag=NULL)
   │            • SSE build-logs streamed back (existing SSE machinery)
   ▼
Kaniko Job  ── builds FROM agent-base (SDK vendored, not pip-installed)
   │            COPY agent.py ; entrypoint = `agentshield serve` (fail-closed governance)
   │            PUSH → in-cluster registry:2
   ▼
deploy-controller ── pulls image_tag, deploys pod + OPA sidecar (UNCHANGED — it already
                     runs whatever image_tag the version names; proven with a BYO image)
```

**Two things it deliberately does NOT add:**
- **No `services/build-service/`** — registry-api already has cluster Job RBAC, creates Jobs (`k8s.py`),
  writes ConfigMaps, and streams SSE. A second Job-creator is the repo's #1 bug class (parallel paths drift)
  and adds zero isolation (the boundary is the Job, not its creator).
- **No new object store** — `source_code` rides the `agent_versions` row (`image_tag` already exists).

---

## 4. Locked design decisions

| # | Decision | Why |
|---|---|---|
| D1 | **No build-service** — Kaniko is a Job launched by registry-api | eval-runner precedent; avoids a 2nd Job-creation path |
| D2 | **`source_code` on `agent_versions`**, not a new object store | the running MinIO is Langfuse's; the platform one is `enabled:false` |
| D3 | **Prebaked `agent-base`** (`COPY sdk/`), never `pip install agentshield-sdk` | SDK is unpublished; also deletes the build's PyPI egress need |
| D4 | **Fail-closed `agentshield serve`**, sharing one wiring helper with `dev` | `dev` fails open to mock governance — unacceptable for shipped agents |
| D5 | **Version row created up-front** (`status=pending`, `image_tag=NULL`) | the contract streams/keys by `version_id`; "no row until success" is unimplementable. Fail-closed is preserved structurally: no image ⇒ nothing to deploy, and `eval_passed=false` ⇒ the publish gate refuses it |

---

## 5. Migration & scope

- **Migration 0065** — `agent_versions.source_code` (TEXT) + `build_status`. `image_tag` **already exists**;
  `status` CHECK already admits `'pending'`. Confirm head at mint (was 0064 on 2026-07-16).
- **New images:** `agent-base`, and the upstream `registry:2`. Pin BOTH **top-level** in values.yaml (the
  sub-chart `.tgz`-shadow trap — see the coupling notes).
- **Tag bumps:** registry-api, studio, and **declarative-runner** (D4 changes the SDK, and the runner
  `pip install`s it — the coupling gate enforces this).

---

## 6. Pickup sequence (checkpoints are the gate)

```
[CP1a]  Stand up registry:2. PROVE push (in-cluster DNS) → pull (kubelet) round-trips
        with a throwaway image.   ← IF THIS FAILS, STOP AND ESCALATE. No UI until green.
[CP1b]  Prove agent-base + `agentshield serve` runs a hand-built agent with LIVE
        governance (real OPA/safety, not mocks).
  then  build Job → up-front version row → Monaco editor → suite-78 (no-fakes).
```

~2–3 focused sessions. **Do not interleave with other slices** — it changes the container entrypoint every
SDK agent uses.

**Gate (no-fakes):** suite-78 must drive the real path — author source in the browser → real Kaniko Job →
real push → real pull → pod Running on the built image → governed tool call succeeds. Fail LOUD on a missing
registry (that is the whole point of CP1a).

---

## 7. THE ONE OPEN DECISION (blocks everything)

**Which registry?**

| | Approach | Cost | Risk |
|---|---|---|---|
| **A** *(recommended)* | in-cluster `registry:2` (Deployment + Service + PVC) | ~½ day incl. the push/pull proof | dev-grade (no auth/TLS) — fine for the dev cluster, **must not** be the prod story |
| **B** | external registry (ECR/Harbor) | credentials + egress + per-env config | couples the dev loop to a cloud account; offline dev breaks |
| **✗** | `--no-push --tarPath` + privileged `ctr import` DaemonSet | — | gives containerd access to a system that builds USER code — a worse hole than the problem. **Refused.** |

Everything downstream (build Job, `serve`, editor) is sequenced *after* this choice and its CP1a proof.

---

## 8. The ~1–2h honest-path fix (do FIRST, independent of WS-5)

Not WS-5, but the reason WS-5 isn't urgent — and it makes the working capability usable today:

- Replace Studio's `metadata.source_code` textarea (dead — no reader) with an **`image_tag` input** on the
  create/deploy surface.
- Keep the code template as **guidance for what to build locally** (`services/echo-agent/` is the working
  reference: Dockerfile + `server.py`).
- No-fakes gate: enter an image in the UI → deploy → pod `Running` on the user's image + OPA sidecar
  (exactly the flow proven manually on 2026-07-16).

This turns "a success toast for an agent that can never run" into "bring your own image, fully governed,
self-service" — the platform's honest SDK story until WS-5 automates the build.

---

## 9. When revisiting, re-ground FIRST

Every plan in this line has drifted 5–12 specifics; the WS-5 tasks.md already carries an R1–R16 correction
table, and it too will age. Before executing: re-verify the migration head, every tag, and every `file:line`
against live code. Never treat a number in these docs as ground truth. (CLAUDE.md DoD #6.)
