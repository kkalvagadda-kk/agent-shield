# WS-5 Tasks — SDK in-browser build (Kaniko): write `agent.py` in a tab, get a running durable agent

**Slice:** WS-5 of Execution Models v2 (spec §5 WS-5; `docs/spec.md:1004` + `:1168` "In-Browser SDK Agent Editor
+ Platform-Managed Image Build"; plan `ws5/plan.md`). **Covers WS-5 ONLY.**
**Depends on WS-1** (SDK `/run` — **verified live**: `sdk/agentshield_sdk/server.py:253` `@app.post("/run")`).
Independent of WS-2/3/4.

**Totals: 33 tasks — 28 implementation + 5 checkpoint ([CP1a]–[CP1e]).**
**Migration: YES — `0065_agent_version_source_build.py`** (head verified `0064`; see R2).
**Suite:** `scripts/e2e/suite-78-sdk-browser-build.sh` (IDs `T-S78-00x`), registered **after suite-77**
(`run-all.sh:126`).
**Image bumps:** registry-api `0.2.190 → 0.2.191`, studio `0.1.143 → 0.1.144`, declarative-runner
`0.1.48 → 0.1.49` (**forced** — WS-5 touches `sdk/agentshield_sdk/`; see R3/D4), **plus two NEW images**
(`agent-base`, and the `registry:2` upstream image — see R4/D3). **No `BUILD_SERVICE_TAG`** — WS-5 ships **no
new service** (D1).

> **Alignment Check:** the goal is *a non-DevOps user ships a durable SDK agent from a browser tab*. The
> success condition is **a pod running the user's `agent.py`**, not "a Kaniko Job exited 0". Everything below is
> ordered by what can *kill that goal*: there is **no image registry in this cluster** (R4) and **no correct
> container entrypoint for an SDK agent** (R9). Those are proven at **[CP1a]** and **[CP1b]** — before a line of
> Studio code. If [CP1a] fails, WS-5 is blocked on infrastructure and must be escalated, **not** degraded into
> "the build succeeds but nothing can run it."

---

## Re-grounding against the live tree (read before executing — the plan's two central premises are FALSE)

The plan carries the *design-stable / specifics-indicative* banner and says so itself ("**Never treat a
`file:line` or migration number here as ground truth**"). Verified against the live tree (2026-07-15, HEAD
`cc4335b`, registry-api `0.2.190`). **Use these, not the plan's numbers.**

| # | Plan said | Code truth (verified 2026-07-15) | Effect on tasks |
|---|---|---|---|
| **R1** | `suite-60-sdk-build.sh` | Suites **1..77 all exist** (77 = E-4, `run-all.sh:126`). | Suite = **`suite-78-sdk-browser-build.sh`**, IDs **`T-S78-00x`**, registered **after suite-77**. |
| **R2** | migration `00NN` (PROVISIONAL) | Alembic head = **`0064_webhook_clients.py`** (WS-4). | WS-5 = **`0065_agent_version_source_build.py`**, `down_revision="0064"`. **`ADD CONSTRAINT IF NOT EXISTS` (data-model §Migration) is not valid PostgreSQL** — the data-model hedges on this itself. House style: `op.execute("ALTER TABLE … ADD COLUMN IF NOT EXISTS …")` (`0062`) + a **`DO $$ … pg_constraint …$$`** guard for the CHECK (exactly what WS-4's `0064` did). |
| **R3** | "Add `BUILD_SERVICE_TAG`; bump registry-api, studio" | Live tags (`deploy-cpe2e.sh:269-277`): registry-api **`0.2.190`** (`:269`), studio **`0.1.143`** (`:272`), declarative-runner **`0.1.48`** (`:274`), eval-runner `0.1.12`, event-gateway `0.1.3`. `values.yaml` pins: registry-api **`:614`**, studio **`:933`**. | Bump registry-api **`0.2.191`**, studio **`0.1.144`**, **declarative-runner `0.1.49`** — the last is **not optional**: its Dockerfile `pip install`s the SDK from `COPY sdk/`, and D4 changes `sdk/agentshield_sdk/cli.py`. `scripts/smoke-test-cp1-e3-constitution.sh` **already enforces** "declarative-runner is bumped IFF `sdk/agentshield_sdk/` changed" and will **fail the slice** if missed. **No `BUILD_SERVICE_TAG`** (D1). |
| **R4** | §2/§5: Kaniko "pushes the built image to the **internal registry**"; `agent_version(image)` → deploy | **THERE IS NO REGISTRY. This is the slice's blocker and the plan is silent on it.** `deploy-cpe2e.sh:314-340` runs **`docker build -t registry.internal/agentshield/<svc>:<tag>`** and there is **no `docker push` anywhere in `scripts/`** (grep: 0 hits). `registry.internal` is a **naming convention that resolves nowhere** — it is not a Service, not in any chart (grep over `charts/`: 0 hits), not in DNS. Images work only because the cluster resolves the **host's local image store** + `pullPolicy: IfNotPresent` (`values.yaml:615` et al). The cluster is Docker Desktop's **kind-based** k8s (`kubectl get nodes` → `desktop-control-plane` + `desktop-worker`, v1.31.1; CNI **kindnet**). **Kaniko runs in-cluster and can only emit to an OCI registry over HTTP(S)** — it cannot write into the host image store, and the host-image-sharing affordance does not work in reverse. ⇒ **A real registry is a WS-5 deliverable** (Phase 2 + **[CP1a]**), and the push endpoint (in-cluster DNS) vs the pull endpoint (kubelet) are **different strings for the same storage** — which must be **empirically proven first**, not assumed. |
| **R5** | §1.2 + data-model §MinIO: "reuse the existing MinIO, **already deployed for Langfuse** — no second object store"; bucket `agent-source` | **Half true, and the half that is false destroys the rationale.** A MinIO **is** running (`agentshield-minio`, 28h) — but it is **Langfuse's subchart MinIO**: `langfuse.s3.deploy: true` (`values.yaml:550-551`), image **`registry.internal/agentshield/minio-cp1:0.1.0`** (a hand-built image from `services/minio-cp1/` that **`deploy-cpe2e.sh` does not build** — it is absent from the `[1/8] Building images` list), creds **`langfuse-admin`/`LangfuseMinio2024` hardcoded in values.yaml** (`:554-560`), and its only bucket is **`langfuse-media`**. The **platform** MinIO (Bitnami, `minio-credentials` secret, `defaultBuckets: postgres-backups,clickhouse-backups,langfuse-media,eval-artifacts`) is declared **twice** (`values.yaml:84` **and** `:330` — a **duplicate top-level YAML key**) and is **`enabled: false`** in both, i.e. **not deployed**; its `minio-credentials` secret is a **manual `kubectl create secret` step** (`values.yaml:19` comment), not created by `deploy-cpe2e.sh`. ⇒ "reuse the existing MinIO" means **either** coupling agent source to Langfuse's lifecycle and creds **or** standing up the disabled second MinIO — i.e. the plan's own "no second object store" argument **collapses**. See **D2**. |
| **R6** | §1.1 + §5: **`services/build-service/` (new)** — FastAPI that spawns the Kaniko Job, + chart template + SA/RBAC + `BUILD_SERVICE_TAG` + a status callback | **Unnecessary — and it would manufacture the repo's #1 bug class (a second Job-creation path).** registry-api **already** has cluster-wide Job RBAC (`charts/agentshield/charts/registry-api/templates/rbac.yaml`: `apiGroups: ["batch"], resources: ["jobs"], verbs: ["create","get","list","delete"]`), **already** creates K8s Jobs (`k8s.py:_create_eval_job_sync:113` / `create_eval_job:189`), **already** writes ConfigMaps (`k8s.py:apply_configmap:90`), and **already** streams SSE (`routers/playground.py`, `routers/chat.py`). The **shipped precedent is eval-runner: a Job *image* launched by registry-api — not a standalone always-on service.** Kaniko is likewise a Job image. A build-service would add a second Job-creation helper, a second tag to forget, an internal callback hop, and **zero isolation** (the isolation boundary is the *Job*, not the service that creates it — the build-service would never execute user code). **DROPPED** — see **D1**. |
| **R7** | §5: `services/registry-api/minio_client.py` (M/**C**) — "reuse existing MinIO client" | **There is no MinIO client to reuse.** `minio_client.py` does not exist; no `boto3.client("s3")` anywhere. `boto3>=1.35.0` **is** in `services/registry-api/requirements.txt` — used **only for Bedrock** (`judge.py:799`/`:820`/`:822` → `boto3.client("bedrock-runtime", …)`). | Moot under **D2** (no object store in MVP). Recorded so nobody "reuses" a client that isn't there. |
| **R8** | `contracts/build-service-api.md` baked Dockerfile: `RUN pip install --no-cache-dir agentshield-sdk` | **This is a supply-chain bug, not a build step.** The SDK is **`agentshield-sdk` v0.1.1** (`sdk/pyproject.toml:6-7`) and is **local-only — never published** (no `twine`/`publish` in any script; grep: 0 hits). `pip install agentshield-sdk` reaches **public PyPI** and installs either nothing or **whatever squatter owns the name** — into an image that then receives platform credentials. The real, shipped pattern is `services/declarative-runner/Dockerfile`: **`COPY sdk/ /tmp/sdk/` + `RUN pip install --no-cache-dir /tmp/sdk/`**. | **D3** — a prebaked `agent-base` image. Also **deletes the PyPI egress requirement** the plan builds its NetworkPolicy around. |
| **R9** | contract: `CMD ["python", "-m", "agentshield_sdk.server"]` | **This CMD cannot work — twice over.** (a) `sdk/agentshield_sdk/server.py` defines `app = FastAPI(…)` (**`:76`**) and has **no `if __name__ == "__main__"` and no `uvicorn.run`** → `python -m agentshield_sdk.server` imports and **exits**. (b) Immediately below `app` sits **`runner: Any = None`** with the comment **"Set by cli.py before uvicorn starts"** — so even if it served, **every `/chat` and `/run` would hit a `None` runner**. The real entrypoint is the `agentshield` console script (`pyproject.toml:38-39` → `cli.py:app` `:30`) → **`dev`** (`cli.py:48`), which imports `--agent` (**default `agent:agent`** — which happens to match `COPY agent.py`), sets `server.runner` (`:106`), then `uvicorn.run("agentshield_sdk.server:app", …)` (`:112`). **But `dev` is a dev entrypoint that fails OPEN into mocks:** `--safety/--no-safety` **defaults to `False`** and *clears* `AGENTSHIELD_SAFETY_URL` (`cli.py:50-54`) → `mock_safety` (`safety_client.py:5`); and OPA falls back to **`mock_opa`** whenever `AGENTSHIELD_OPA_URL` is unset (`opa_client.py:23`). Shipping browser-built agents on `agentshield dev` would ship **ungoverned agents whose governance silently no-ops**. | **D4** — add a fail-closed `agentshield serve`, sharing **one** wiring helper with `dev`. This is why declarative-runner must bump (R3). |
| **R10** | data-model §ORM: "`models.py` `AgentVersion` (~`:516`)"; §1.3 "auto-create an `agent_version` (+ image)" | `class AgentVersion` is at **`models.py:533`**. **`image_tag: Mapped[str | None] = mapped_column(String(512))` already exists** → WS-5 adds **no** image column. `status` already `CHECK IN ('pending','eval_passed','eval_failed','deployed','retired')` → **`'pending'` is already legal**. `versions.py:63-71` already validates `image_tag` non-blank for sdk agents and `:114` already writes it. | Migration adds **only** `source_code` + `build_status` (D2). The auto-create path **reuses** `versions.py`'s writer — no second version writer. |
| **R11** | **The plan contradicts itself on when the version row exists.** contract: `POST /agents/{name}/builds` → `202 {"version_id": …, "build_status": "pending"}` and "set `agent_versions.source_url` + `build_status='pending'`"; SSE is keyed `GET /agents/{name}/versions/{version_id}/build-logs`. data-model §"State machine": "**No `agent_version` row is created until `succeeded`**". | **Both cannot hold.** You cannot set columns on, return the id of, or stream logs keyed to a row that does not exist. The contract's own shape **proves the row must exist up-front**. | **D5** — the row is created up-front (`status='pending'`, `build_status='pending'`, `image_tag=NULL`) and **fail-closed is enforced structurally**: no image ⇒ nothing to deploy, and `eval_passed=false` ⇒ the production gate refuses it (`deployments.py:560`). "No *deployable* version from a failed build" is preserved; "no *row*" was never implementable. |
| **R12** | §5 Studio: `studio/src/pages/EditAgentPage.tsx` (M/**C**) | **`EditAgentPage.tsx` does not exist.** Agent detail/edit lives in `studio/src/pages/AgentDetailPage.tsx` + `studio/src/components/agent-detail/SettingsTab.tsx` (**the exact drift WS-4 hit** — its plan also named `AgentDetailPage.tsx` for a surface that lives in `SettingsTab.tsx`). | Edit+rebuild lands as a **tab in the existing agent-detail shell**, not a new page (T024). |
| **R13** | §5 Studio: "Monaco editor" | **Monaco is not a dependency** — `studio/package.json` has no `monaco-editor`/`@monaco-editor/react`/`codemirror`. Additionally `@monaco-editor/react` **CDN-loads Monaco from jsdelivr by default**, which a locked-down Studio origin will block and which breaks offline. | T022 adds **both** `monaco-editor` + `@monaco-editor/react` and **pins the loader to the bundled copy** (`loader.config({ monaco })`) — no CDN fetch. Asserted at [CP1e]. |
| **R14** | §1.4/§5: "replaces the `metadata.source_code` **stub** CodeForm" | **Confirmed, precisely:** `CreateAgentPage.tsx` — `CODE_TEMPLATE` (`:369`), `CodeForm` (`:932`), `source_code: z.string().min(1)` (`:942`), default `CODE_TEMPLATE` (`:956`), `agent_type: "sdk"` (`:970`), **`metadata: { source_code: values.source_code }`** (`:974`), and the literal comment **"Source code editor (textarea placeholder for Monaco)"** (`:1027`) over a `<textarea>` (`:1029`). The plan's read of the stub is one of the few specifics that is exactly right. | T023 replaces the textarea in place; the `metadata.source_code` write is **retired** by T023 (it is a write with no reader — the stub never built anything). |
| **R15** | §2/§4 "**Safety posture**": "the build Job has a NetworkPolicy restricting egress"; "A malicious `agent.py` can't … reach the cluster network" | **NetworkPolicy is NOT ENFORCED in this cluster.** CNI is **kindnet** (`kube-system`: `kindnet-*`; no Calico/Cilium/Weave/Antrea — grep of `kube-system` pods: 0 hits). kindnet ships **no NetworkPolicy controller**, so a `NetworkPolicy` object is **accepted and silently ignored**. The repo already carries this fiction (`infra/network-policies/platform-default-deny.yaml`, `agents-allow-egress.yaml`, `charts/agentshield/charts/event-gateway/templates/networkpolicy.yaml`). | WS-5 **still authors** the NetworkPolicy (correct on any enforcing CNI, and it is the artifact prod needs) but **must not claim the boundary**. **Gap-ledgered as `not-enforced (environmental, debt)`** and asserted honestly: [CP1d] verifies the object **exists**, and **explicitly does not** assert egress is blocked. Claiming an unenforced control is worse than not having it. **D3 shrinks the blast radius for real** (no PyPI egress needed at all) — that is a *structural* mitigation, which is the only kind that works here. |
| **R16** | contract: `POST /internal/builds/{version_id}/status` (callback) | The internal router prefix is **`/api/v1/internal`** (`routers/internal.py:36`), not `/internal`. | **Moot under D1** (no second service ⇒ no callback ⇒ no internal auth hop to get wrong). Contract corrected in T001. |

> **Tags — read from `scripts/deploy-cpe2e.sh:269-277`, never guessed** (they moved repeatedly today):
> registry-api **`0.2.190`**, studio **`0.1.143`**, declarative-runner **`0.1.48`**, eval-runner `0.1.12`,
> event-gateway `0.1.3`. WS-5 bumps **registry-api `0.2.191`**, **studio `0.1.144`**, **declarative-runner
> `0.1.49`**, and introduces **`AGENT_BASE_TAG`** + **`REGISTRY_TAG`** (R4/D3).

---

## Locked decisions

### D1 — No `services/build-service/`. registry-api spawns the Kaniko Job through the **shipped** `k8s.py` path.

R6. The plan's build-service is a control-plane wrapper around `create_namespaced_job` that registry-api
**already** performs, with the RBAC it **already** holds, in a service that **already** streams SSE. Adding it
buys nothing and costs: a second Job-creation helper to drift (`docs/bugs/side-effecting-lost-on-declarative-runner-path.md`
is exactly "a second hand-maintained builder silently dropped a field"), a second image + tag + chart + values
pin to forget (`docs/bugs/e3-never-ran-tag-not-bumped.md` is exactly "a tag never moved and the code never ran"),
an internal callback hop, and **zero** added isolation — the build-service would never execute user code; the
**Kaniko Job** is the sandbox, and it is a Job either way. `create_build_job()` lands in `k8s.py` **beside**
`create_eval_job` (`:189`), sharing its client, namespace, and TTL idioms. `T-S78-000` asserts **1 definition**
of the Job-spawn helper and **no** `services/build-service/` directory.

### D2 — Source lives in **`agent_versions.source_code TEXT`**, not MinIO. `source_url` is **not** created.

R5/R7. The plan's justification ("reuse the existing MinIO — no second object store") is **factually false**:
the running MinIO is **Langfuse's subchart**, with Langfuse's hardcoded creds and only a `langfuse-media`
bucket; the platform MinIO is **disabled** behind a **duplicate YAML key** and needs a **manually-created**
secret. So "reuse" means either *couple agent source to Langfuse's lifecycle* (turn Langfuse off → lose every
agent's source) or *stand up the second object store the plan says it is avoiding* — plus a client
(`minio_client.py`) that does not exist.

Against that: the artifact is **one small text file per version**, and there is already a durable,
transactional, backed-up (`docs`: off-cluster `pg_dump` scripts), per-version-keyed home for it — the
`agent_versions` row it belongs to. `source_code TEXT` is written in the **same transaction** as the version,
survives a Langfuse teardown, needs **no** new client/creds/bucket/secret, and makes save→reload→assert
(DoD #2) a single row read. Kaniko needs the bytes in a **build context** regardless (a ConfigMap, T012) —
MinIO would be a *third* copy, not a source of truth.

**This is a scope reduction, not a capability loss:** nothing in WS-5's goal ("no local Docker") depends on
object storage. Object storage becomes correct when sources stop being one small text file (multi-file
projects, wheels, assets) — **gap-ledgered as `deferred (intentional)`** with that explicit trigger, and
`source_url` is **not** added as a column so WS-5 leaves **no orphan** behind (DoD #3). **Fail-closed size
guard:** T012 rejects source > **900 KiB** at the door (ConfigMap's hard 1 MiB cap) with a 413 — an explicit
refusal, never a truncation.

### D3 — The base image is **prebaked and pushed by `deploy-cpe2e.sh`**; the Kaniko Dockerfile does **no `pip install`**.

R8. Baked Dockerfile becomes **two lines** over a base the platform builds from `COPY sdk/` (the
declarative-runner pattern, verbatim):

```dockerfile
FROM <registry>/agentshield/agent-base:<AGENT_BASE_TAG>
COPY agent.py /app/agent.py
```

This (a) **eliminates the unpublished-package supply-chain bug** — the SDK arrives from the repo, not from
whoever owns `agentshield-sdk` on public PyPI; (b) makes builds **fast and network-free** — Kaniko's *only*
egress becomes the in-cluster registry (base pull + push), so the plan's "registry **+ PyPI**" egress rule
becomes "**registry only**", a strictly tighter boundary that holds **structurally** even where NetworkPolicy
is ignored (R15); (c) keeps the SDK version a **platform** decision with a bumpable tag, not a floating
`pip install` resolved at each user's build time. The user still supplies **only** `agent.py` bytes — the
`FROM`, the base, the CMD, and the pip set remain server-side constants (the plan's "illegal states
unrepresentable" intent, delivered more completely).

### D4 — `agentshield serve`: one fail-closed production entrypoint, sharing **one** wiring helper with `dev`.

R9. There is **no** correct entrypoint for an SDK agent container today (`python -m agentshield_sdk.server`
does not serve, and would serve a `None` runner if it did). The only working wiring is inside `cli.py:dev`,
which **fails open into `mock_safety`/`mock_opa`**. Copying `dev`'s wiring into a Dockerfile bootstrap =
a second builder to drift (forbidden). So: extract `_wire_runner_and_serve(agent_module, port, *, require_governance: bool)`
— **1 definition, 2 call sites** (`dev` passes `False`, `serve` passes `True`) — and add `serve`, which
**refuses to start** (non-zero exit, loud message) when `AGENTSHIELD_OPA_URL` or `AGENTSHIELD_SAFETY_URL` is
unset. `dev`'s behaviour is **unchanged** (local dev must keep working offline). `T-S78-000` greps 1 def / 2
call sites. This is the SDK change that forces declarative-runner `0.1.49` (R3).

### D5 — The version row exists from `pending`; fail-closed is **structural**, not the row's absence.

R11. `POST …/builds` creates the `agent_versions` row (`status='pending'`, `build_status='pending'`,
`image_tag=NULL`) and returns its id — which the contract's own `202 {"version_id"}` and
`/versions/{version_id}/build-logs` **require**. A failed build sets `build_status='failed'` and **never
writes `image_tag`**; with no image there is nothing to deploy, and `eval_passed=false` keeps the production
gate shut (`deployments.py:560`). The invariant that matters — **a failed build can never produce a running
agent** — is enforced by the *absence of an image*, which is checkable, rather than by the absence of a row,
which the contract makes impossible. `T-S78-006` asserts it against the DB **and** by attempting a real deploy.

---

## Summary

| Phase | Tasks | What it lands |
|---|---|---|
| **P1 — Setup & re-grounding** | T001 (1) | The plan/contract/data-model corrected in place (no registry, no MinIO, no build-service, broken CMD, self-contradiction). |
| **P2 — The registry (R4, highest risk first)** | T002–T005 (4) | `registry:2` + PVC + NodePort; `deploy-cpe2e.sh` pushes; push⇄pull endpoint pair. |
| **[CP1a] Registry round-trip** | [CP1a] (1) | **A real in-cluster push is pulled by the real kubelet into a real pod.** If this fails, WS-5 stops. |
| **P3 — Runnable agent image (R8/R9)** | T006–T009 (4) | `agent-base` image; `agentshield serve` fail-closed + shared wiring; declarative-runner bump. |
| **[CP1b] A hand-built agent.py runs** | [CP1b] (1) | **The entrypoint contract is real** — proven before any build pipeline exists. |
| **P4 — Schema + ORM** | T010–T011 (2) | `0065`: `source_code` + `build_status` (+ guarded CHECK). |
| **P5 — The build path** | T012–T017 (6) | ConfigMap context; `k8s.py:create_build_job`; `POST …/builds`; SSE logs; terminal watch → `image_tag`; fail-closed. |
| **P6 — suite-78 (no-fakes)** | T018–T021 (4) | Crash-loud + ID census; parity greps; real build → real image → **real running pod**; failure paths. |
| **[CP1c] MVP gate** | [CP1c] (1) | **MVP: browser-shaped API call → running durable SDK agent, end to end, no local Docker.** |
| **P7 — Studio** | T022–T026 (5) | Monaco (bundled, no CDN); build-log panel; edit+rebuild tab; Vitest; Playwright journey. |
| **P8 — Post-impl gates** | T027–T028 (2) | Tags/register/docs/ledger. |
| **[CP1d] Security posture (honest)** | [CP1d] (1) | Unprivileged, isolated ns, no creds beyond push, **NP present-but-unenforced stated, not claimed**. |
| **[CP1e] Orphan + constitution sweep** | [CP1e] (1) | Live caller per symbol; all pins agree; `AUDIT_REF=HEAD` coupling gate. |

**MVP scope line: MVP = through [CP1c]** — a real `agent.py` POSTed to the real API, a real Kaniko Job in a
real isolated namespace, a real image in a real registry, a real `agent_versions.image_tag`, and **a real agent
pod running the user's code and answering a real durable `/run`**. P7 puts a browser on the proven path;
[CP1d]/[CP1e] are the constitution sweep, not new capability.

> **NO-FAKES ACCEPTANCE (non-negotiable — CLAUDE.md "No Fakes in E2E").** The 7-defect durable-workflow bug
> (`docs/bugs/durable-workflow-live-path.md`) proved faked seams hide exactly the bugs living in them — six
> suites shipped green while the real path was broken. `suite-78` MUST POST **real** source to the **real**
> API, let the **real** Kaniko Job build, assert the **real** image exists **in the real registry**, and
> **deploy and invoke a real pod running it**. **NO** mocked httpx, **NO** monkeypatched Job creation, **NO**
> hand-written `agent_versions` rows, **NO** pre-seeded images, **NO** `page.route` stubs. **A build that
> "succeeds" but produces no runnable pod is the exact failure this suite exists to catch** — so the terminal
> assertion is a **pod answering**, never a Job exit code. **Fail LOUDLY (never skip)** on an unreachable
> fixture. Model on `scripts/e2e/suite-76-webhook-client-signing.sh` + `suite-75-eval-v2-scheduled.sh`.

> **CRASH-LOUD CENSUS (mandatory — the suite-74 lesson).** suite-74 once reported **"✅ PASSED"** while
> silently dropping **6 of 11** cases: the driver crashed mid-run and `PASS>0 FAIL==0` read as green.
> suite-78 carries **both** guards (T018): (a) a wrapping `except Exception` recording **`T-S78-999 driver ran
> every case without crashing`** as a **FAIL** + traceback; (b) an **ID-based** census **`T-S78-COMPLETE`** over
> `REQUIRED_IDS`. **IDs, never a count** — a count drifted and cannot say *which* case vanished. Per-invocation
> `/tmp` paths (`RUN_TAG="$(date +%s)$$"`); **write results BEFORE cleanup**.

---

## Phase 1 — Setup & re-grounding

- [ ] [T001] Record the re-grounding table above into `ws5/plan.md` §status and **correct the five doc-level breaks in place** — each is a claim the code contradicts, and leaving them is how the next reader rebuilds the bug: (a) `contracts/build-service-api.md` §"Baked Dockerfile" `pip install agentshield-sdk` → the **D3** prebaked-base form (R8 — the SDK is unpublished; this line installs a stranger's package into a credentialed image); (b) the same file's `CMD ["python","-m","agentshield_sdk.server"]` → **`agentshield serve`** (R9 — the current CMD serves nothing and would serve a `None` runner); (c) `data-model.md` §"State machine" "No `agent_version` row is created until `succeeded`" → **D5** (it contradicts the same doc-set's `202 {"version_id"}` + `/versions/{id}/build-logs`); (d) `data-model.md` §MinIO + plan §1.2 → **D2** (the MinIO is Langfuse's; `source_url` is **not** added); (e) plan §1.1/§5 `services/build-service/` + `BUILD_SERVICE_TAG` → **D1** (registry-api already has the RBAC + the Job helper). Also record: suite=**78**, migration=**0065**, tags **`0.2.191`/`0.1.144`/`0.1.49`**, Studio file = **`AgentDetailPage`/`SettingsTab`** (no `EditAgentPage`), **no registry exists** (R4), **NetworkPolicy is unenforced under kindnet** (R15) — `docs/plan/execution-models-v2/ws5/plan.md` + `docs/plan/execution-models-v2/ws5/contracts/build-service-api.md` + `docs/plan/execution-models-v2/ws5/data-model.md`
  - **Verify:** `grep -n "suite-78\|0065\|agent-base\|agentshield serve\|source_code" docs/plan/execution-models-v2/ws5/*.md docs/plan/execution-models-v2/ws5/contracts/*.md` and `grep -rn "pip install agentshield-sdk\|python.*-m.*agentshield_sdk.server\|source_url" docs/plan/execution-models-v2/ws5/` → **0 matches**

---

## Phase 2 — The registry (R4) — the thing that must exist before anything else can work

> **Do this first and prove it first.** Every downstream task assumes an image can leave Kaniko and arrive in a
> kubelet. That assumption is **unverified in this cluster** and is the single biggest risk in WS-5. CLAUDE.md
> §4: wire one thin path end-to-end and prove it before starting the next capability.

- [ ] [T002] Deploy an in-cluster OCI registry — `Deployment` (upstream **`registry:2`**, unprivileged, no user code, resource limits), `Service` **`agentshield-registry`** (ClusterIP `:5000` **and** `type: NodePort` `nodePort: 30500` — verified free: `kubectl get svc -A` shows **no NodePort in use**), and a **`PersistentVolumeClaim`** (`REGISTRY_DATA_PVC`, 10Gi). **The PVC is load-bearing, not hygiene:** without it a registry restart drops every browser-built image, and each agent Deployment's `image_tag` becomes unpullable on its next reschedule — a fleet that dies on a pod eviction. Guard it with the release-scoped toggle idiom used by the other components (`values.yaml:72` block) — `charts/agentshield/templates/registry.yaml`
  - **Verify:** `helm template charts/agentshield --set registry.enabled=true | grep -A2 "kind: Service" | grep -n "30500"` and `helm template charts/agentshield | grep -c "PersistentVolumeClaim"` ≥ 1
- [ ] [T003] Add the `registry` values block — `enabled: true`, `image.repository: registry`, **`image.tag: "2"` pinned top-level** in `charts/agentshield/values.yaml` (**not** in a sub-chart: `charts/agentshield/charts/*.tgz` can **shadow** sub-chart values, and `deploy-cpe2e.sh` **swallows `helm dependency update` failures** — this is exactly why event-gateway's tag was moved top-level, `values.yaml:138`), `service.nodePort: 30500`, `persistence.size: 10Gi`, and the **two endpoint strings** as explicit values (T004) — `charts/agentshield/values.yaml`
  - **Verify:** `grep -n "^registry:" -A12 charts/agentshield/values.yaml` and `helm template charts/agentshield >/dev/null`
- [ ] [T004] Wire the **endpoint pair** as explicit config — the push and pull endpoints are **different strings for the same storage** and conflating them is the failure mode: `AGENT_IMAGE_PUSH_ENDPOINT` = **`agentshield-registry.agentshield-platform.svc.cluster.local:5000`** (in-cluster DNS — what the **Kaniko pod** can reach; the kubelet cannot resolve cluster DNS) and `AGENT_IMAGE_PULL_ENDPOINT` = **`localhost:30500`** (the NodePort — what the **kubelet** can reach, and which container runtimes treat as **insecure/plaintext by default**, so no TLS or node config is needed). The host portion is **not** part of the manifest: a repo pushed via the DNS endpoint is byte-identical when pulled via the NodePort endpoint. Add both to the registry-api Deployment `env:` (mirroring how `EVAL_RUNNER_IMAGE` is threaded, `values.yaml` registry-api `env:` block) and read them in `config.py`. **Two explicit named values, never one string with a rewrite rule** (CLAUDE.md: explicit parameters over implicit behavior) — `charts/agentshield/charts/registry-api/templates/deployment.yaml` + `charts/agentshield/values.yaml` + `services/registry-api/config.py`
  - **Verify:** `grep -rn "AGENT_IMAGE_PUSH_ENDPOINT\|AGENT_IMAGE_PULL_ENDPOINT" charts/ services/registry-api/config.py` shows both in both places
- [ ] [T005] Teach `deploy-cpe2e.sh` to push — add a **`[1b/8] Pushing base images`** step after the build block (`:314-340`) that `docker push`es the **`agent-base`** image (T006) to **`localhost:30500/agentshield/agent-base:${AGENT_BASE_TAG}`**, and `kubectl apply`s the new namespace (T014) beside the existing three (`:344-346`). **This is the repo's first-ever `docker push`** (R4 — grep proves 0 today), so it must **fail loudly** with a diagnostic naming the NodePort if the registry is unreachable — a silent skip here yields a Kaniko Job that cannot resolve its own `FROM`, surfacing as an inscrutable build error three phases later. Keep the existing build steps untouched; retag/push only — `scripts/deploy-cpe2e.sh`
  - **Verify:** `bash -n scripts/deploy-cpe2e.sh && grep -n "docker push\|30500" scripts/deploy-cpe2e.sh`

## [CP1a] Checkpoint — the registry round-trip (**the go/no-go gate**)

_Gate: Phase 2 complete. **Run before writing any other WS-5 code.**_
_What you prove: an image pushed **from inside the cluster** is pulled **by the real kubelet** into a **real
running pod**. This is the assumption every remaining task rests on and it is unverified today (R4)._

- [ ] [CP1a] **Infra smoke** `scripts/smoke-test-cp1-ws5-registry.sh` — `#!/usr/bin/env bash`, `set -euo pipefail`, real `kubectl`/`curl` assertions, `exit 0` only on all-pass. Deploy by **delegating to `bash scripts/deploy-cpe2e.sh`** (never bare helm/docker/kubectl — CLAUDE.md "Deploy Script Only"), **wait for `kubectl rollout status deploy/agentshield-registry`** (asserting seconds after a rollout produces phantom failures), then: **T-CP1A-001** the registry pod is `Running`, crashloop=0, and its PVC is `Bound`; **T-CP1A-002** the **v2 API answers on both endpoints** — `curl -sf http://localhost:30500/v2/` from the host **and** `kubectl run --rm` a throwaway pod that curls `http://agentshield-registry.agentshield-platform.svc.cluster.local:5000/v2/`; **T-CP1A-003** **the round-trip** — run a **real Kaniko Job** in-cluster building a 2-line throwaway Dockerfile and pushing to the **DNS** endpoint, then create a **real Deployment** referencing that image via the **`localhost:30500` pull** endpoint and assert the pod reaches `Running` (i.e. **the kubelet really pulled what Kaniko really pushed** — the exact push⇄pull pairing of D-R4, proven, not assumed); **T-CP1A-004** the `agent-base` image pushed by T005 is present (`curl -sf …/v2/agentshield/agent-base/tags/list | jq -e '.tags | index(env.AGENT_BASE_TAG)'`). **If T-CP1A-003 fails, STOP and escalate** — do **not** work around it by pre-loading images onto nodes, which would make the browser-build path a fiction that only ever works for images built outside the browser — `scripts/smoke-test-cp1-ws5-registry.sh`
  - **Verify:** `bash scripts/smoke-test-cp1-ws5-registry.sh`

---

## Phase 3 — A runnable agent image (R8/R9) — the second unverified assumption

- [ ] [T006] Create the **`agent-base`** image (D3) — `FROM python:3.12-slim`; `WORKDIR /app`; `COPY sdk/ /tmp/sdk/`; `RUN pip install --no-cache-dir /tmp/sdk/ && rm -rf /tmp/sdk`; `EXPOSE 8080`; `CMD ["agentshield","serve","--port","8080"]` (T007's default `--agent agent:agent` matches the `COPY agent.py` the Kaniko layer adds). **Mirror `services/declarative-runner/Dockerfile` exactly** — it is the shipped, working way the SDK enters an image, and it is built from the **repo root** (`docker build -f services/declarative-runner/Dockerfile .`) so `COPY sdk/` resolves; `agent-base` must be built the same way. **No `pip install agentshield-sdk`** (R8: unpublished name → public PyPI) — `services/agent-base/Dockerfile`
  - **Verify:** `grep -n "COPY sdk/" services/agent-base/Dockerfile && grep -c "pip install --no-cache-dir agentshield-sdk" services/agent-base/Dockerfile` → **0**
- [ ] [T007] Add **`agentshield serve`** + extract the shared wiring (D4) — in `cli.py`, extract `_wire_runner_and_serve(agent_module: str, port: int, *, require_governance: bool)` holding **exactly** what `dev` does today at `cli.py:96-118`: import the `module:variable` (`:71`), build `Runner(agent_instance)`, `asyncio.run` the setup that assigns **`server.runner`** (`:106` — the sentinel `server.py` documents as "Set by cli.py before uvicorn starts"), then `uvicorn.run("agentshield_sdk.server:app", host="0.0.0.0", port=port)` (`:112`). Rewire `dev` (`:48`) to call it with `require_governance=False` — **behaviour-identical, no functional change to local dev**. Add `serve` with the **same `--agent`/`--port` options** and `require_governance=True`: it **exits non-zero with a loud message** if `AGENTSHIELD_OPA_URL` or `AGENTSHIELD_SAFETY_URL` is unset/empty. **This is the fail-closed core of WS-5's safety story:** without it a browser-built agent silently runs on `mock_safety` (`safety_client.py:5`) and `mock_opa` (`opa_client.py:23`) — governance that *reports success while enforcing nothing*. **1 definition, 2 call sites** — copying `dev`'s wiring into `serve` would rebuild `docs/bugs/side-effecting-lost-on-declarative-runner-path.md` — `sdk/agentshield_sdk/cli.py`
  - **Verify:** `python3 -c "import ast; ast.parse(open('sdk/agentshield_sdk/cli.py').read())"`; `grep -c "def _wire_runner_and_serve" sdk/agentshield_sdk/cli.py` → **1**; `grep -c "_wire_runner_and_serve(" sdk/agentshield_sdk/cli.py` → **3** (1 def + 2 callers)
- [ ] [T008] [P] Unit-pin the entrypoint contract — `serve` **exits non-zero** with both governance envs unset, with **only** `AGENTSHIELD_OPA_URL` set, and with **only** `AGENTSHIELD_SAFETY_URL` set (three separate cases — a single "both unset" test would pass an `or`/`and` inversion); `serve` proceeds when both are set; **`dev` still starts with neither** (the offline-dev regression pin — D4 must not break local dev); `--agent` defaults to **`agent:agent`** (the default the baked `COPY agent.py` relies on — if this default ever moves, every browser-built image breaks and nothing else would catch it) — `sdk/tests/test_cli_serve.py`
  - **Verify:** `cd sdk && python3 -m pytest tests/test_cli_serve.py -q`
- [ ] [T009] Build + push `agent-base` from `deploy-cpe2e.sh` — add `AGENT_BASE_TAG="0.1.0"` beside the other tags (`:269-277`), a build line `docker build -t "localhost:30500/agentshield/agent-base:${AGENT_BASE_TAG}" -f services/agent-base/Dockerfile .` (**repo-root context** — T006) in the `[1/8]` block, its push in T005's step, and a comment-header entry (`:1-20` idiom). **Bump `DECLARATIVE_RUNNER_TAG` `0.1.48 → 0.1.49`** (`:274`) — T007 changed `sdk/agentshield_sdk/`, the runner image pip-installs the SDK from `COPY sdk/`, and `scripts/smoke-test-cp1-e3-constitution.sh` **already fails the build** on "declarative-runner bumped IFF sdk changed". Mirror `0.1.49` in **`charts/agentshield/values.yaml`** (`deploy-controller.declarativeRunnerTag`) — `scripts/deploy-cpe2e.sh` + `charts/agentshield/values.yaml`
  - **Verify:** `grep -n "AGENT_BASE_TAG\|0.1.49" scripts/deploy-cpe2e.sh charts/agentshield/values.yaml` and `AUDIT_REF=HEAD bash scripts/smoke-test-cp1-e3-constitution.sh`

## [CP1b] Checkpoint — a hand-written `agent.py` actually serves

_Gate: Phase 3 complete. What you prove: **the entrypoint contract is real** — before any build pipeline
depends on it. R9 showed the documented CMD serves nothing; this is where that stops being a document claim._

- [ ] [CP1b] **Behaviour smoke** `scripts/smoke-test-cp1-ws5-base.sh` — `set -euo pipefail`, exit 0 only on all-pass. Delegate build+deploy to **`bash scripts/deploy-cpe2e.sh`**, wait for rollouts, then: **T-CP1B-001** a **real Kaniko Job** builds `FROM agent-base` + `COPY agent.py` (a real minimal SDK agent, HTTP tools only — python-type tools crash the pod, `docs/bugs/python-tool-graph-build-kwargs.md`) and pushes to the **DNS** endpoint; **T-CP1B-002** a **real Deployment** on the **pull** endpoint reaches `Running` and **`GET /health` answers 200** — i.e. `agentshield serve` **really serves**, which `python -m agentshield_sdk.server` provably would not (R9); **T-CP1B-003** **`POST /run` answers** (the WS-1 durable entrypoint, `server.py:253`) — this is the WS-1⇄WS-5 seam and the whole reason the slice exists; **T-CP1B-004** the **fail-closed gate fires**: the same image with `AGENTSHIELD_OPA_URL` **unset** **CrashLoops / exits non-zero** rather than serving — assert the pod does **not** reach Ready and the log names the missing env. **T-CP1B-004 is the load-bearing one:** if it passes only because the image failed for some *other* reason, the governance gate is a fiction — assert the **specific** message, not merely non-Ready — `scripts/smoke-test-cp1-ws5-base.sh`
  - **Verify:** `bash scripts/smoke-test-cp1-ws5-base.sh`

---

## Phase 4 — Schema + ORM (`source_code` + `build_status`)

- [ ] [T010] Create migration **`0065_agent_version_source_build.py`** (`revision="0065"`, `down_revision="0064"` — head verified R2). House style, **not** the data-model's invalid SQL: (a) `op.execute("ALTER TABLE agent_versions ADD COLUMN IF NOT EXISTS source_code TEXT")` — **`source_code`, not `source_url`** (D2: there is no object store to point at, and an unpopulated pointer column would be a **new orphan**, DoD #3); (b) `op.execute("ALTER TABLE agent_versions ADD COLUMN IF NOT EXISTS build_status VARCHAR(16)")`; (c) the `build_status IN ('pending','building','succeeded','failed')` CHECK inside a **`DO $$ … IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_agent_versions_build_status') THEN … END IF; $$`** guard — **`ADD CONSTRAINT IF NOT EXISTS` is not valid PostgreSQL** (the data-model's version **fails**; `0064` set the correct precedent); allow `NULL` (pre-existing CLI-built versions have no build). `downgrade()` drops all three idempotently. **Both columns nullable + data-preserving** — every existing version keeps working untouched — `services/registry-api/alembic/versions/0065_agent_version_source_build.py`
  - **Verify:** `python3 -c "import ast; ast.parse(open('services/registry-api/alembic/versions/0065_agent_version_source_build.py').read())"` and `grep -n 'down_revision = "0064"' services/registry-api/alembic/versions/0065_agent_version_source_build.py` and `grep -c "ADD CONSTRAINT IF NOT EXISTS" services/registry-api/alembic/versions/0065_agent_version_source_build.py` → **0**
- [ ] [T011] Add the ORM to **`class AgentVersion` (`models.py:533`** — the data-model's `~:516` is stale, R10): `source_code: Mapped[str | None] = mapped_column(Text, nullable=True)` and `build_status: Mapped[str | None] = mapped_column(String(16), nullable=True)`, placed beside the existing **`image_tag: Mapped[str | None] = mapped_column(String(512))`** — which **already exists**, so WS-5 adds **no** image column (R10). Surface both on `AgentVersionResponse` (`schemas.py`) so the Studio panel (T024) can read them — a status column no screen can read is an orphan gate (plan §4) — `services/registry-api/models.py` + `services/registry-api/schemas.py`
  - **Verify:** `python3 -c "import ast; ast.parse(open('services/registry-api/models.py').read())"` (mapper config runs **on-cluster** at [CP1c] — the local default `python3` is **3.9** and dies on PEP-604 `Mapped[str | None]`; a **py3.11+** venv can run `configure_mappers()` locally)

---

## Phase 5 — The build path (D1: registry-api spawns Kaniko through the shipped `k8s.py`)

- [ ] [T012] `POST /api/v1/agents/{name}/builds` — a **new** `routers/builds.py` (`APIRouter(prefix="/api/v1/agents", tags=["builds"])`, `get_optional_user` + `AsyncSessionLocal` per `routers/triggers.py:17-35`). Body `{source_code: str}`. **Authz — who may trigger a build (the plan is silent; this executes user code on the platform):** the caller must own/belong to the agent's team, mirroring the existing owner check idiom (`playground.py:83-92`) — **403 otherwise**, fail-closed; stamp `created_by = current_user.sub`. **Fail-closed size guard:** reject `len(source_code.encode()) > 900_000` → **413** (D2 — ConfigMap's hard 1 MiB cap; refuse explicitly, never truncate). Create the `agent_versions` row via the **existing** writer path (`versions.py:114` — no second version writer) with `status='pending'`, `build_status='pending'`, `source_code=body.source_code`, `image_tag=NULL` (**D5** — the contract's `202 {"version_id"}` and `/versions/{id}/build-logs` both require the row to exist). Return **202** `{version_id, build_status: "pending"}`. **Reject non-`sdk` agents 422** (a declarative agent has no `agent.py`) — `services/registry-api/routers/builds.py`
  - **Verify:** `python3 -c "import ast; ast.parse(open('services/registry-api/routers/builds.py').read())"`
- [ ] [T013] Mount the router — `from routers.builds import router as builds_router` beside the other router imports + `app.include_router(builds_router)` beside the other includes. **Without this the entire slice is orphaned** (DoD #3; WS-4's T006 is the same task and the same reason) — `services/registry-api/main.py`
  - **Verify:** `grep -c "builds_router" services/registry-api/main.py` → **2** (import + include)
- [ ] [T014] The build namespace + NetworkPolicy — `infra/namespaces/agentshield-builds.yaml` (beside the existing three, applied at `deploy-cpe2e.sh:344-346`), a `ServiceAccount` with **no** RBAC (the Kaniko pod needs **zero** API access — it only builds and pushes; any RoleBinding here is a finding), and `infra/network-policies/builds-egress.yaml` restricting egress to **the in-cluster registry + DNS only** — **not** "registry + PyPI" as the plan says, because **D3 removes the pip step entirely**, so PyPI egress is no longer needed and granting it would widen the boundary for nothing. **Honesty requirement (R15):** the CNI is **kindnet**, which ships **no NetworkPolicy controller** — this object is **accepted and ignored** in this cluster. Author it (it is correct on any enforcing CNI and is what prod needs), add a **header comment stating plainly that it is unenforced here**, and ledger it. Do **not** let any doc, task, or test claim egress is blocked — `infra/namespaces/agentshield-builds.yaml` + `infra/network-policies/builds-egress.yaml`
  - **Verify:** `kubectl apply --dry-run=client -f infra/namespaces/agentshield-builds.yaml -f infra/network-policies/builds-egress.yaml` and `grep -in "not enforced\|kindnet" infra/network-policies/builds-egress.yaml`
- [ ] [T015] `create_build_job()` in **`k8s.py`, beside `create_eval_job` (`:189`)** — **D1/R6: this is the ONE Job-spawn path; do not add a second** (`_create_eval_job_sync:113` is the template — same `client.BatchV1Api()`, same `V1Job`/`V1JobSpec` idioms, same TTL). Spec: namespace **`agentshield-builds`**; image `gcr.io/kaniko-project/executor:<pinned>`; **`securityContext: runAsNonRoot`, no privileged, no host mounts, no Docker socket**; args `--dockerfile=/workspace/Dockerfile`, `--context=dir:///workspace`, `--destination=${AGENT_IMAGE_PUSH_ENDPOINT}/agentshield-agents/{agent}:{version_number}` (**unique per version — never a reused tag**, the platform rule), `--insecure` (plaintext in-cluster registry); the **build context is a ConfigMap** written via the **existing `apply_configmap` (`k8s.py:90`)** carrying the **server-side-constant Dockerfile** (D3 — 2 lines, no user `FROM`, no build args, no `RUN`) + the user's `agent.py`, mounted at `/workspace`; `backoffLimit: 0` (**a retry would re-push the same tag — one build, one verdict**); `ttlSecondsAfterFinished` + resource limits. The **only** user-controlled bytes are `agent.py`, `COPY`d in — a custom base or a privileged build is **unrepresentable**, not validated-against — `services/registry-api/k8s.py`
  - **Verify:** `python3 -c "import ast; ast.parse(open('services/registry-api/k8s.py').read())"`; `grep -c "def create_build_job" services/registry-api/k8s.py` → **1**; `grep -c "privileged\|/var/run/docker.sock" services/registry-api/k8s.py` → **0**
- [ ] [T016] `GET /api/v1/agents/{name}/versions/{version_id}/build-logs` (**SSE**) — stream the Kaniko pod's log via `CoreV1Api().read_namespaced_pod_log(..., follow=True)` (the client is already initialised, `k8s.py:47`), relayed as `text/event-stream` with the **`StreamingResponse` idiom already used in `routers/playground.py`/`routers/chat.py`** — reuse it, do not invent a second SSE shape. Terminal event carries `{"status": "succeeded"|"failed", "image": …|null}`. Handle **pod-not-yet-scheduled** by polling until the pod exists (a Job's pod is not instant — streaming immediately yields a 404 that reads to a user as "the build vanished"). **404 if the version is not this agent's**; **403** reuses T012's ownership check — `services/registry-api/routers/builds.py`
  - **Verify:** `python3 -c "import ast; ast.parse(open('services/registry-api/routers/builds.py').read())"` and `grep -n "StreamingResponse\|read_namespaced_pod_log" services/registry-api/routers/builds.py`
- [ ] [T017] The terminal watch → `build_status` + `image_tag` (**the fail-closed core, D5**) — a `background_tasks` watcher (the `_dispatch_durable_run` idiom, `playground.py`) sets `build_status='building'` when the Job's pod starts, then on Job completion: **succeeded** → `build_status='succeeded'` **and** `image_tag = f"{AGENT_IMAGE_PULL_ENDPOINT}/agentshield-agents/{agent}:{version_number}"` (**the PULL endpoint — T004**; writing the push/DNS endpoint here yields a row that looks perfect and an agent that **`ImagePullBackOff`s forever**, since the kubelet cannot resolve cluster DNS — this single line is the likeliest silent bug in the slice and `T-S78-005` exists for it); **failed** → `build_status='failed'`, **`image_tag` stays NULL**, logs retained. Dispatch on the Job's terminal condition by an **explicit map** (`Complete` → succeeded, `Failed` → failed, **unknown → `failed` + a loud log**) — **never a priority fallthrough** that degrades an unhandled state into a plausible success (`docs/bugs/…` D4 of E-4: a fallthrough dispatch produced a confident wrong answer). **No auto-deploy** — the plan's "(+ optional deploy)" is **not** MVP: it would take a browser build straight to a running pod with no human step, and the deploy path already exists one click away (gap-ledgered) — `services/registry-api/routers/builds.py`
  - **Verify:** `grep -n "AGENT_IMAGE_PULL_ENDPOINT" services/registry-api/routers/builds.py` (the pull endpoint, not the push one) and `python3 -c "import ast; ast.parse(open('services/registry-api/routers/builds.py').read())"`

---

## Phase 6 — suite-78: real source, real Kaniko, real image, **real running pod**

_One file, appended sequentially (T018→T021). NO FAKES._

- [ ] [T018] Scaffold **`suite-78-sdk-browser-build.sh`** + the **crash-loud and census guards FIRST** — `#!/usr/bin/env bash`, `set -euo pipefail`, executable, `NAMESPACE`/`API_POD`/`ADMIN_SUB="75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6"` (the **real** interactive platform-admin Keycloak sub — deny-by-default hides resources whose `created_by` ≠ the caller's sub), modeled on **`suite-76`**/**`suite-75`**. **Per-invocation `/tmp` paths**: `RUN_TAG="$(date +%s)$$"`, `DRIVER=/tmp/s78_driver_${RUN_TAG}.py`, `OUTFILE=/tmp/s78_out_${RUN_TAG}.txt`, `RUNLOG=/tmp/s78_run_${RUN_TAG}.log` — a fixed path lets two overlapping runs read each other's results. (a) Wrap the driver in `except Exception` recording **`T-S78-999 driver ran every case without crashing`** as a **FAIL** + `traceback.format_exc()[-400:]`, re-raising into `finally` so cleanup still runs, and **write the result file BEFORE cleanup**. (b) ID-based census `REQUIRED_IDS="000 001 002 003 004 005 006 007"` → on any miss emit **`FAIL T-S78-COMPLETE every gate assertion ran | NEVER RAN:$MISSING`** + the driver-log tail. **IDs, never a count.** Exit non-zero on `FAIL≠0` **or** `PASS==0` (inconclusive). Fixture: a real **`agent_class=daemon`** SDK agent (a `user_delegated` agent with no live user ⇒ OPA `missing_user_identity` deny) with **HTTP tools only**. **Fail LOUDLY, never skip**, if the registry/build namespace is unreachable — **note the wall-clock**: a real Kaniko build + rollout is minutes, so use the detached-in-pod driver pattern the long suites use — `scripts/e2e/suite-78-sdk-browser-build.sh`
  - **Verify:** `bash -n scripts/e2e/suite-78-sdk-browser-build.sh` and `grep -n "T-S78-999\|T-S78-COMPLETE\|REQUIRED_IDS" scripts/e2e/suite-78-sdk-browser-build.sh`
- [ ] [T019] Append **T-S78-000 — the parity/architecture grep** (cheap, run first, guards the repo's #1 bug class): **(a)** `grep -rc "create_namespaced_job" services/registry-api/` shows Job creation **only in `k8s.py`** (D1 — one Job-spawn path; a second is `side-effecting-lost-on-declarative-runner-path.md` reintroduced); **(b)** **`test ! -d services/build-service`** (D1 — the dropped service stays dropped); **(c)** `grep -c "def _wire_runner_and_serve" sdk/agentshield_sdk/cli.py` == **1** and its call count == **3** (1 def + `dev` + `serve`) — **no copied wiring** (D4); **(d)** `grep -rc "pip install --no-cache-dir agentshield-sdk" services/ docs/plan/execution-models-v2/ws5/` == **0** (R8 — the unpublished-package install never comes back); **(e)** `grep -rc "python.*-m.*agentshield_sdk.server" services/ docs/plan/execution-models-v2/ws5/` == **0** (R9 — the CMD that cannot serve never comes back); **(f)** the Kaniko spec has **no** `privileged`/`docker.sock`/`hostPath` (T015) — `scripts/e2e/suite-78-sdk-browser-build.sh`
  - **Verify:** `bash scripts/e2e/suite-78-sdk-browser-build.sh 2>&1 | grep "T-S78-000"`
- [ ] [T020] Append **T-S78-001..004 — the golden path, ending at a POD, not a Job exit code** — `scripts/e2e/suite-78-sdk-browser-build.sh`
  - `T-S78-001` — **real source in, real row out.** POST a **real** `agent.py` (a genuine SDK agent using a real HTTP tool) to the **real** `POST /api/v1/agents/{name}/builds` → **202** + `version_id`; **save→reload→assert**: re-read `agent_versions` **from the DB** and confirm `source_code` survived **byte-identical** and `build_status='pending'` (DoD #2 — this surface writes data).
  - `T-S78-002` — **a real Kaniko Job runs in the real isolated namespace.** A real Job appears in **`agentshield-builds`**, its pod is **not** privileged and mounts **no** docker socket, and `build_status` reaches **`succeeded`** (poll with a real timeout; **fail loudly** on timeout — never skip).
  - `T-S78-003` — **the image really exists in the real registry.** `curl` the registry v2 API (`/v2/agentshield-agents/{agent}/tags/list`) from in-cluster and assert the version's tag is listed — the artifact, not the exit code.
  - `T-S78-004` — **THE POINT OF THE SLICE: a real pod runs the user's code.** Deploy that version through the **real** deploy path and assert the pod reaches `Running` **and answers a real `POST /run`** (WS-1's durable entrypoint, `server.py:253`) with a real terminal result. **Without this, "build succeeded" and "we shipped a tarball nothing can run" are indistinguishable** — which is precisely the state R4 and R9 show the plan would have shipped.
- [ ] [T021] Append **T-S78-005..007 — the failure paths and the endpoint trap** — `scripts/e2e/suite-78-sdk-browser-build.sh`
  - `T-S78-005` — **the pull-endpoint trap (T017).** Assert the persisted `image_tag` starts with the **`AGENT_IMAGE_PULL_ENDPOINT`** (`localhost:30500/…`), **not** the in-cluster DNS push endpoint — a row written with the push endpoint looks perfectly correct in every API response and **`ImagePullBackOff`s forever** on the node. Assert the **string**, because `T-S78-004`'s running pod is the only other thing that would catch it, and only after a multi-minute timeout.
  - `T-S78-006` — **FAIL-CLOSED: a broken build produces nothing deployable (D5).** POST **syntactically invalid** `agent.py` → `build_status` reaches **`failed`**, **`image_tag IS NULL`** (re-read from the DB), the build logs are **retained and non-empty** via the real SSE endpoint, and — the load-bearing half — **attempting a real deploy of that version fails**. Assert the negative **directly**; "no image was written" is the invariant, and a test that only checks `build_status` would pass on a version that silently kept a **stale** `image_tag` from an earlier build.
  - `T-S78-007` — **authz + limits (T012).** A build POST from a **non-owner** sub → **403**; `source_code` > 900 KiB → **413** (explicit refusal, not truncation); a build POST against a **declarative** agent → **422**. Three real requests through the real door.
  - **Verify:** `bash scripts/e2e/suite-78-sdk-browser-build.sh 2>&1 | grep -E "T-S78-00[567]"`

## [CP1c] Checkpoint — MVP gate

_Gate: Phases 4–6 complete. **This is the MVP.**_
_What you prove: real source → real Kaniko Job → real image in a real registry → a **real pod running the
user's `agent.py`** answering a real durable `/run`. No local Docker anywhere in that sentence._

- [ ] [CP1c] **MVP smoke** `scripts/smoke-test-cp1-ws5-mvp.sh` — `set -euo pipefail`, exit 0 only on all-pass. Delegate build+deploy to **`bash scripts/deploy-cpe2e.sh`**, **wait for `kubectl rollout status`** on registry-api before asserting. Then: **T-CP1C-001** registry-api pods `Running` on **`0.2.191`** (`kubectl get pod -o jsonpath` over `.spec.containers[].image`), crashloop=0; **T-CP1C-002** alembic head == **`0065`** (`kubectl exec` → `alembic current`); **T-CP1C-003** schema landed — `agent_versions.source_code` + `build_status` exist with `ck_agent_versions_build_status` (`information_schema`/`pg_constraint`); **T-CP1C-004** the **mapper-config gate** — `kubectl exec` the registry-api pod, **from a WRITABLE temp dir** (`cd /tmp && PYTHONPATH=/app python3 -c "import routers.builds, models; from sqlalchemy.orm import configure_mappers; configure_mappers(); print('ok')"`) — `/app` is **read-only** so a bare `cd /app` exec cannot write `__pycache__`, and **importing the real app is the only check that catches a missing import or a `response_model=None`-less DELETE/204 route, which `ast.parse` passes and which CrashLoops the pod**; **T-CP1C-005** **the DEPLOYED IMAGE carries WS-5's code, not just the tag** — `kubectl exec` → `grep -c "def create_build_job" /app/k8s.py` ≥ 1 **and** `test -f /app/routers/builds.py` (**a tag is a claim about content**: `docs/bugs/e3-never-ran-tag-not-bumped.md` — E-3's code never executed for an entire slice because a tag never moved while **both** tag files agreed and every static check stayed green); **T-CP1C-006** the **deployed base image** carries `serve` — pull `agent-base:${AGENT_BASE_TAG}` from the registry into a throwaway pod and assert `agentshield serve --help` exits 0; **T-CP1C-007** `bash scripts/e2e/suite-78-sdk-browser-build.sh` **fully green** (`T-S78-000..007` + `T-S78-COMPLETE`, **0 skips**); **T-CP1C-008** suite-78 registered in `run-all.sh` — `scripts/smoke-test-cp1-ws5-mvp.sh`
  - **Verify:** `bash scripts/smoke-test-cp1-ws5-mvp.sh`

> **To run:** `bash scripts/deploy-cp1-ws5.sh` → wait for rollouts → `bash scripts/smoke-test-cp1-ws5-registry.sh && bash scripts/smoke-test-cp1-ws5-base.sh && bash scripts/smoke-test-cp1-ws5-mvp.sh`
> **Pass criteria:** all exit 0; no CrashLoopBackOff; alembic head `0065`; the deployed image contains
> `create_build_job` + `routers/builds.py`; suite-78 green with `T-S78-COMPLETE`.

---

## Phase 7 — Studio: Monaco on a proven path

- [ ] [T022] Add Monaco **bundled, not CDN-loaded** — add `monaco-editor` + `@monaco-editor/react` to `studio/package.json` (**neither is present today**, R13) and configure the loader against the **bundled** copy (`import * as monaco from 'monaco-editor'; loader.config({ monaco })`) in the editor component. **`@monaco-editor/react` fetches Monaco from a jsdelivr CDN by default** — on a locked-down Studio origin that is a blank editor with a console error, and it makes the whole slice depend on public CDN reachability at page load. Vite worker config as needed; keep the bundle-size impact visible in the PR — `studio/package.json` + `studio/vite.config.ts`
  - **Verify:** `cd studio && npm install && npm run build` succeeds and `grep -rn "loader.config" studio/src` shows the bundled pin
- [ ] [T023] `<AgentCodeEditor>` — **one** component shared by create + edit (plan §4 "one component, not two"): Monaco (`language="python"`, the existing **`CODE_TEMPLATE` (`CreateAgentPage.tsx:369`)** as the default value), wired to the same `react-hook-form` field the stub used. Replace the stub **in place**: `CodeForm` (`CreateAgentPage.tsx:932`) — swap the `<textarea>` (`:1029`, under the literal comment **"textarea placeholder for Monaco"**, `:1027`) for `<AgentCodeEditor>`, keep the `source_code` zod rule (`:942`), and **retire the `metadata: { source_code: … }` write (`:974`)** in favour of `submitBuild` → `POST …/builds`: the metadata write is a **write with no reader** (the stub never built anything), and leaving it would leave two writers of the same bytes disagreeing about which is real — `studio/src/components/AgentCodeEditor.tsx` + `studio/src/pages/CreateAgentPage.tsx`
  - **Verify:** `cd studio && npm run typecheck` and `grep -c "metadata: { source_code" studio/src/pages/CreateAgentPage.tsx` → **0**
- [ ] [T024] `<BuildLogPanel>` + the **edit + rebuild** surface — the panel consumes the real SSE `build-logs` stream (reuse Studio's existing SSE/EventSource idiom — do not invent a second one), renders live Kaniko output, and shows the terminal `build_status` (`pending`/`building`/`succeeded`/`failed`) with the failure logs **kept on screen** for a failed build (a failed build whose logs vanish is unactionable — the logs *are* the feature). **R12: there is no `EditAgentPage.tsx`** — the plan's file does not exist; edit+rebuild lands as a tab in the existing agent-detail shell (`AgentDetailPage.tsx` / `components/agent-detail/`), loading `source_code` from the version and POSTing a rebuild. Add `submitBuild` + `streamBuildLogs` to `registryApi.ts`. **Every new API method needs a live caller in this same change** (DoD #3) — `studio/src/components/BuildLogPanel.tsx` + `studio/src/pages/AgentDetailPage.tsx` + `studio/src/api/registryApi.ts`
  - **Verify:** `cd studio && npm run typecheck` and `grep -rn "submitBuild\|streamBuildLogs" studio/src` shows a **live caller** for each
- [ ] [T025] [P] Vitest — `AgentCodeEditor` renders with the template and submit posts the source; `BuildLogPanel` renders streamed lines, the `building` state, the **`succeeded`** state, and the **`failed`** state **with logs still visible**; the edit tab loads existing `source_code`; the empty/error states. Mock the API via `vi.mock('../api/registryApi')`, render via `renderWithProviders` (`src/test/utils.tsx`). **Monaco needs a jsdom stub** — mock `@monaco-editor/react` to a plain `<textarea>` so these stay fast and hermetic (the editor widget is not what is under test; its wiring is) — `studio/src/components/AgentCodeEditor.test.tsx` + `studio/src/components/BuildLogPanel.test.tsx`
  - **Verify:** `cd studio && npm run test`
- [ ] [T026] Playwright **`sdk-build.spec.ts`** — the WS-1+WS-5 journey (DoD #1/#2). Real Keycloak login (`e2e/global-setup.ts`) against the **https gateway `https://agentshield.127.0.0.1.nip.io:8443`** (Secure KC cookies break over an http port-forward). **CREATE the fixture in-spec — never scavenge "the first matching row"** (that made a past spec's verdict track leftover state). Drive: create an SDK agent → type real `agent.py` in Monaco → submit (`page.waitForResponse` on the real `POST **/agents/*/builds`) → **build logs stream in the panel** → `build_status` reaches `succeeded` → **save→reload→assert**: reload from the backend and confirm the version + its `source_code` **survived** → deploy → **the durable SDK agent runs**. **NO `page.route` stubs** — a stubbed route proves nothing about the real path (bug #7's lesson). **NOTE: `studio/tsconfig.json` includes only `"src"` — `e2e/*.spec.ts` is NOT typechecked**, so a typo here passes `npm run typecheck` and fails only at runtime; **run the spec**. Generous timeouts: a real Kaniko build is minutes — `studio/e2e/sdk-build.spec.ts`
  - **Verify:** `bash scripts/studio-e2e.sh`

---

## Phase 8 — Post-implementation gates

- [ ] [T027] [P] Bump tags in **BOTH** files, same commit — `scripts/deploy-cpe2e.sh`: `REGISTRY_API_TAG` `0.2.190→0.2.191` (`:269`), `STUDIO_TAG` `0.1.143→0.1.144` (`:272`), **`DECLARATIVE_RUNNER_TAG` `0.1.48→0.1.49`** (`:274` — **forced by T007's SDK change**, R3), `AGENT_BASE_TAG` (T009), plus a WS-5 comment-header entry (`:1-20` idiom); `charts/agentshield/values.yaml`: registry-api (`:614`) → `0.2.191`, studio (`:933`) → `0.1.144`, `deploy-controller.declarativeRunnerTag` → `0.1.49`, `registry.image.tag` + `agentBaseTag` (T003). **The deploy uses `helm upgrade` with tags baked into values (no `--set`)** — bumping only `deploy-cpe2e.sh` leaves the chart on the old image and the new code never runs while every check stays green (`docs/bugs/e3-never-ran-tag-not-bumped.md`). **Pin `registry.image.tag` and `agentBaseTag` TOP-LEVEL, never in a sub-chart** — a stale `charts/agentshield/charts/*.tgz` **shadows** sub-chart values and `deploy-cpe2e.sh` **swallows `helm dependency update` failures** (the reason event-gateway's pin was moved top-level, `values.yaml:138`) — `scripts/deploy-cpe2e.sh` + `charts/agentshield/values.yaml`
  - **Verify:** `grep -n "0.2.191\|0.1.144\|0.1.49" scripts/deploy-cpe2e.sh charts/agentshield/values.yaml` shows **every** pin in **both** files
- [ ] [T028] [P] Register + document + ledger — (a) register suite-78 **after suite-77** (`run-all.sh:126`): `run_suite "Suite 78: WS-5 in-browser SDK build (Kaniko, no-fakes)" "suite-78-sdk-browser-build.sh"` + `chmod +x`; (b) **`docs/experience/playground.md`** (**mandatory** — `routers/*.py` + Studio pages are covered files, CLAUDE.md §3): the in-browser build UX — write `agent.py` in Monaco, submit, watch the real Kaniko log stream, `build_status` states, **a failed build keeps its logs and produces nothing deployable**, edit+rebuild, and that **no local Docker is required**; (c) the **Gap Ledger** below into the canonical "Known gaps" header (`docs/testing/manual-ui-e2e-test-plan.md`), each tagged **deferred (intentional)** vs **not-yet-wired (debt)** — **including the NetworkPolicy-not-enforced row**, which must never read as shipped (DoD #5) — `scripts/e2e/run-all.sh` + `docs/experience/playground.md` + `docs/testing/manual-ui-e2e-test-plan.md`
  - **Verify:** `grep -n "suite-78" scripts/e2e/run-all.sh && test -x scripts/e2e/suite-78-sdk-browser-build.sh && grep -n "Monaco\|build_status" docs/experience/playground.md && grep -n "WS-5" docs/testing/manual-ui-e2e-test-plan.md`

## [CP1d] Checkpoint — the security posture, stated honestly

_What you prove: the build sandbox is what we say it is — **and nothing more**. An in-browser build service
executes user-supplied code; an overstated control is worse than a missing one, because it stops anyone
looking._

- [ ] [CP1d] **Security smoke** `scripts/smoke-test-cp1-ws5-security.sh` — `set -euo pipefail`, exit 0 only on all-pass. **T-CP1D-001 Dockerfile is a server-side constant** — `grep` the repo: the Kaniko Dockerfile is built **only** in `k8s.py` (T015); **no** code path lets a request supply `FROM`, build args, or `RUN` (a user-supplied `FROM` is unrepresentable, not rejected). **T-CP1D-002 the build pod is unprivileged** — the live Job spec (`kubectl get job -o json` in `agentshield-builds`) has `runAsNonRoot`, no `privileged`, no `hostPath`, no `docker.sock`. **T-CP1D-003 the build SA has no API access** — no RoleBinding/ClusterRoleBinding references the builds SA (`kubectl get rolebindings,clusterrolebindings -A -o json | jq`); a build pod that can read Secrets is a platform compromise, and this is the assertion that keeps it from drifting in later. **T-CP1D-004 credential blast radius** — the Kaniko pod's env/volumes carry **no** LLM secret, **no** `AGENTSHIELD_ENCRYPTION_KEY`, **no** DB URL; its only credential-shaped reach is the plaintext in-cluster registry. **T-CP1D-005 the NetworkPolicy EXISTS — and is NOT claimed to be enforced**: assert the object is applied, and assert the honesty guard `grep -in "not enforced" infra/network-policies/builds-egress.yaml` (**R15**: the CNI is **kindnet**, which has **no NetworkPolicy controller** — the object is accepted and ignored). **Do NOT assert egress is blocked; it is not.** The real, *structural* mitigation is **D3** — the build needs **no** external egress at all — which T-CP1D-006 pins: **T-CP1D-006** the Kaniko args reference **only** the in-cluster registry (no PyPI, no Docker Hub) — `scripts/smoke-test-cp1-ws5-security.sh`
  - **Verify:** `bash scripts/smoke-test-cp1-ws5-security.sh`

## [CP1e] Checkpoint — no-orphan + constitution sweep

- [ ] [CP1e] **Sweep** `scripts/smoke-test-cp1-ws5-constitution.sh` — pure source greps, `set -euo pipefail`, exit 0 only on all-pass. **NO-ORPHAN (DoD #3 — "build utility + not wiring it up = NOT done"):** a live caller/reader for **every** new symbol — `create_build_job` (caller = `routers/builds.py`), `builds_router` (**mounted in `main.py`**, else the whole API is dead code), `_wire_runner_and_serve` (**1 def / 2 callers**), `agentshield serve` (caller = the `agent-base` CMD), `submitBuild`/`streamBuildLogs` (callers = `AgentCodeEditor`/`BuildLogPanel`), `AgentCodeEditor`+`BuildLogPanel` (**rendered**, not merely exported), and the **columns**: **`source_code`** (producer = `POST …/builds` T012; reader = the edit tab T024 + `T-S78-001`) and **`build_status`** (producer = the terminal watch T017; reader = `BuildLogPanel` T024) — a column with only a writer is a silent orphan. Assert **`source_url` was never created** (D2) and **`services/build-service/` does not exist** (D1). **CONSTITUTION:** the three tags are **identical** in `scripts/deploy-cpe2e.sh` and `charts/agentshield/values.yaml`; **declarative-runner is `0.1.49` iff `git diff --name-only` shows a `sdk/agentshield_sdk/` change** (T007 changed it — **it must be bumped**, else fail loudly); `docs/experience/playground.md` modified; exactly **one** new Alembic file (`0065`). Then run the shipped **service-dir ⇄ tag coupling gate** — `AUDIT_REF=HEAD bash scripts/smoke-test-cp1-e3-constitution.sh` — which checks that code changed under `services/registry-api/` and `studio/src/` moved its tag **in the audited commit** — `scripts/smoke-test-cp1-ws5-constitution.sh`
  - **Verify:** `bash scripts/smoke-test-cp1-ws5-constitution.sh && AUDIT_REF=HEAD bash scripts/smoke-test-cp1-e3-constitution.sh`

---

## Gap Ledger (carried from plan §7, **re-grounded** — two rows rewritten, four added)

| Item | Status | Note |
|---|---|---|
| User-editable Dockerfile / base image | **out of scope (intentional, safety)** | Unchanged from the plan, and **D3 strengthens it**: the Dockerfile is a 2-line server-side constant over a platform-built base. No user `FROM`, no build args, no `RUN` — illegal by construction, not by validation. |
| Non-Python SDK agents | deferred (intentional) | Base is `python:3.12-slim` + the repo's SDK; other runtimes are a follow-up. |
| Build cache / layer reuse across versions | not-yet-optimized (debt, low) | Kaniko cold builds first. **D3 already removes the slowest layer** (`pip install` of the SDK moves into the prebaked base), so a browser build is `FROM` + `COPY`. |
| ~~BuildKit alternative to Kaniko~~ | **documented option (unchanged)** | Kaniko chosen (unprivileged, no daemon). |
| ~~"Reuse the existing MinIO — no second object store"~~ → **object storage for source** | **REWRITTEN — the plan's premise is false (R5). Now: deferred (intentional)** | The running MinIO is **Langfuse's subchart** (`langfuse.s3.deploy: true`, `values.yaml:550`), with Langfuse's hardcoded creds and only a `langfuse-media` bucket; the platform MinIO is **`enabled: false`** behind a **duplicate YAML key** (`:84` **and** `:330`) with a **manually-created** secret. Source lives in **`agent_versions.source_code TEXT`** (**D2**) — transactional with its version, backed by the existing `pg_dump` scripts, no new client/creds/bucket. **Revisit when** source stops being one small text file (multi-file projects, wheels, assets) or exceeds the **900 KiB** guard (T012). `source_url` was **never created** — no orphan column left behind. |
| ~~`services/build-service/`~~ | **DROPPED — unnecessary (R6/D1)** | registry-api already holds cluster-wide `batch/jobs` RBAC (`registry-api/templates/rbac.yaml`) and already creates Jobs (`k8s.py:189`). The eval-runner precedent is a **Job image**, not a service. A second Job-creation path is the repo's #1 bug class, and the build-service would add **zero** isolation (the Job is the sandbox). |
| **No image registry existed in this cluster** | **CLOSED BY WS-5 (was an undocumented blocker — R4)** | `deploy-cpe2e.sh` never pushed (0 `docker push` in `scripts/`); `registry.internal/*` resolves nowhere; images worked only via the host image store + `IfNotPresent`. Kaniko can only emit to a registry ⇒ WS-5 ships `registry:2` + a PVC + the push/pull endpoint pair, proven at **[CP1a]**. |
| **NetworkPolicy on the build namespace is authored but NOT ENFORCED** | **not-enforced (environmental, debt) — MUST NOT read as shipped** | The CNI is **kindnet**; it ships **no NetworkPolicy controller**, so the object is accepted and silently ignored (the repo's existing `infra/network-policies/*` + the event-gateway NP are in the same state). The plan's claim "a malicious `agent.py` can't … reach the cluster network" is **not true in this cluster**. The **real** mitigation is structural: **D3** means the build needs **no** external egress at all, and the pod is unprivileged with a zero-RBAC SA and no platform credentials ([CP1d]). **Closing this needs an enforcing CNI** (Calico/Cilium) — a cluster-level change outside WS-5. |
| **`agentshield dev` fails OPEN into `mock_safety`/`mock_opa`** | **CLOSED BY WS-5 (D4) — but `dev` keeps its behaviour by design** | R9: `dev` clears `AGENTSHIELD_SAFETY_URL` by default (`cli.py:50-54`) and OPA falls back to `mock_opa` when unset (`opa_client.py:23`). **`serve`** (the container entrypoint) **refuses to start** without both. `dev` remains permissive **intentionally** — local dev must work offline — which is safe **only because** `serve` is now what ships. |
| Auto-deploy on build success | **deferred (intentional)** | The plan's "(+ optional deploy)" is not MVP: it would take browser-authored code to a running pod with **no human step**. The deploy path is one click away on a proven version. |
| Build queue / concurrency limits / per-team quotas | **not-yet-wired (debt)** | Nothing caps concurrent builds today; a user can spawn Jobs in a loop. `backoffLimit: 0` + TTL bound each Job, but not the fleet. Needs a queue or a ResourceQuota on `agentshield-builds`. |
| Image GC for superseded/failed builds | **not-yet-wired (debt)** | Every version pushes a unique tag and nothing prunes; the registry PVC grows without bound. Related to the known open `DELETE /agents` leak (pods are not GC'd). |

**No orphan flags:** `source_code` (producer = `POST …/builds` T012, reader = the edit tab T024 + `T-S78-001`),
`build_status` (producer = the terminal watch T017, reader = `BuildLogPanel` T024 + `T-S78-002/006`),
`create_build_job` (caller = `routers/builds.py`), `builds_router` (mounted, T013), `serve` (caller = the
`agent-base` CMD), `submitBuild`/`streamBuildLogs` (callers = the editor + panel). **`source_url` is not
created** — WS-5 leaves no orphan column. [CP1e] greps a live caller for each before WS-5 is reported done.

---

## Definition of Done (WS-5)

- **Real user journey proven (DoD #1):** `studio/e2e/sdk-build.spec.ts` (T026) drives Monaco → submit → real
  build-log SSE → version → deploy → **durable SDK run** in a real browser against the https gateway, **no
  `page.route` stubs**. suite-78 drives the **real** Kaniko Job and ends at **a pod answering `/run`**, never
  at a Job exit code.
- **Save → reload → assert (DoD #2):** `T-S78-001` re-reads `source_code` **from the DB** byte-identical after
  the write; T026 reloads the page from the backend and asserts the version + source survived.
- **No orphans (DoD #3):** T013 mounts the router (else the whole API is dead code); [CP1e] greps a live
  caller for every new symbol **and** asserts `source_url` was never created and `services/build-service/`
  does not exist.
- **Vertical slice (DoD #4):** the two unverified assumptions are proven **first and alone** — **[CP1a]** (an
  in-cluster push is pulled by the real kubelet) and **[CP1b]** (`agentshield serve` really serves, and fails
  closed) — before schema, API, or a single line of Studio. The plan's ordering would have reached the browser
  before discovering there is no registry (R4) and no working entrypoint (R9).
- **Honest gap ledger (DoD #5):** above — **the NetworkPolicy row is the load-bearing one**: WS-5 authors it,
  applies it, and **states plainly that kindnet does not enforce it**, rather than inheriting the plan's
  "a malicious `agent.py` can't reach the cluster network." Build queue/quota + image GC = **debt**; object
  storage + auto-deploy = **deferred (intentional)**.
- **Reason from the running product (DoD #6):** every plan specific was re-verified against live code — suite
  60→**78**, migration →**0065**, `AgentVersion` at **`:533`** (not `:516`), `image_tag` **already exists**, the
  **registry that does not exist**, the MinIO that is **Langfuse's**, the build-service the RBAC makes
  **redundant**, the `pip install` of an **unpublished package**, the CMD that **cannot serve**, the
  `EditAgentPage.tsx` that **does not exist**, the Monaco dependency that **is not installed**, the
  NetworkPolicy that **is not enforced**, and the plan's **self-contradiction** on when the version row exists.
  The plan's read of the `CreateAgentPage` stub (`:932`/`:974`/`:1027`) was checked and is **correct**.
