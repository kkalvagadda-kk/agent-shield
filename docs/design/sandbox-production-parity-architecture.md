# Sandbox ↔ Production Parity Architecture

**Status:** Living reference. Created 2026-07-10 after the third production-only bug
traced to the same root cause (see "Recurring root cause").
**Audience:** anyone touching deploy-controller, the OPA/identity/governance path, or any
table that references a deployment.

## Why this doc exists

An agent runs in two environments that share almost everything (same pod image, same SDK,
same `manifest_builder` pod spec) but are provisioned by **two separate code paths**.
Every time a feature is added or fixed in the sandbox path, it can silently be absent from
the production path, and the gap only surfaces later as a user-facing production failure.
Three such bugs have now occurred (chat FK, tool credentials, OPA identity). This doc
captures the model and the rules that keep the two paths in lockstep so the next change
doesn't repeat it.

## The two code paths

| | Sandbox | Production |
|---|---|---|
| Reconciler | `services/deploy-controller/reconciler.py` | `services/deploy-controller/production_reconciler.py` (`reconcile_production`, `reconcile_workflow_production`) |
| Trigger | agent `deployments` rows | `production_deployments` rows, polled via `/api/v1/catalog/internal/production-deployments` |
| Namespace | shared, pre-provisioned (`agents-platform`) | one per artifact, self-created (`production-<name>-<hash>`) |
| Deployment table | `deployments` | `production_deployments` |
| Tool/version source | `agent_versions` (live) | `published_versions.config_snapshot` (pinned) |
| Pod image / spec | `manifest_builder.build_deployment` — **shared** | same |
| SDK / runner | agent image — **shared** | same |

**Legitimate differences** (do NOT try to merge away): namespace lifecycle, the
deployment table, and the version/tool source. Sandbox assumes a pre-provisioned
namespace + secrets; production creates its own namespace, SA, LLM secret, and OPA
configmap.

**What MUST stay in lockstep**: every *per-pod provisioning and governance* step — SA
creation, machine-identity registration, tool-credential injection, OPA bundle inclusion,
and the pod env/labels emitted by the shared builder.

## Recurring root cause: the two-table split

Sandbox deployments live in `deployments`; production deployments in
`production_deployments`. Any column or query that assumes "a deployment id is a
`deployments` id" breaks for production. Occurrences:

1. `PlaygroundRun.deployment_id` FK→`deployments` → production chat 500'd on insert
   (doc 006, migration 0054).
2. Tool-credential `envFrom` only wired in the sandbox reconciler → production tools got
   no credentials (doc 007).
3. `agent_identities.deployment_id` FK→`deployments` **and** `bundle_generator` INNER JOIN
   on `deployments` → production identities can't be stored or bundled → OPA denies every
   production tool call as `agent_unauthenticated` (doc 008, migration 0055).

### Canonical pattern — a second explicit FK column (never a polymorphic id)

When a row must reference "the deployment, whichever environment," give it **two nullable
FK columns**, exactly one set per row:

- `PlaygroundRun`: `deployment_id` (FK `deployments`) + `production_deployment_id` (FK `production_deployments`).
- `AgentRun`: `sandbox_deployment_id` + `production_deployment_id`.
- `AgentIdentity`: `deployment_id` + `production_deployment_id`.

Do **not** drop the FK to hold either id (loses integrity, ambiguous which table), and do
**not** add an `environment` discriminator with one loosely-typed id column. Readers
coalesce across the two columns (e.g. `_load_provenance` in `approvals.py`; the
`bundle_generator` UNION).

## Identity → OPA bundle → tool governance (the data flow)

For a tool call to be governed (allow / deny / require_approval → HITL) the agent pod's
**ServiceAccount subject must be in the OPA bundle's `data.agents`**. The pod presents its
projected SA-token subject; `sdk/agentshield_sdk/opa_client.py` sends it to the OPA
sidecar; `opa_policy/agentshield.rego` is fail-closed (`default allow := false`) and
returns `deny_reason="agent_unauthenticated"` if `data.agents[subject]` is absent.

The bundle is **pull-generated live**: `GET /api/v1/bundle/bundle.tar.gz` runs
`generate_bundle_data(db)` every ~30s sidecar poll (`push_bundle_to_configmap` is dead
code). So making an identity appear is purely: (a) write the `agent_identities` row, (b)
have the generator's query select it. No push/trigger.

Both environments must:
1. **Create the SA** — `k8s.ensure_service_account` (both paths do).
2. **Register the identity** — `POST /api/v1/agents/{name}/identities` with the SA subject
   + the deployment id in the correct column. Extracted into
   `services/deploy-controller/identity.py::register_agent_identity`, called by BOTH
   reconcilers.
3. **Enter the bundle** — `bundle_generator.generate_bundle_data` UNIONs a sandbox leg
   (via `deployments`→`agent_versions`) and a production leg (via
   `production_deployments`→`published_versions.config_snapshot->'tools'`), gating each on
   its own table's `status IN ('deploying','running')`.

## The anti-drift rule (how we stop repeating this)

**Any per-pod provisioning/governance step lives in one shared module that both
reconcilers call — never inline in one path.** Current shared helpers:

- `services/deploy-controller/tool_secrets.py::resolve_and_copy_tool_secrets` — resolve an
  agent's tool-credential secrets and copy them into the pod namespace for `envFrom`.
- `services/deploy-controller/identity.py::register_agent_identity` — register the pod SA
  subject as a machine identity.

When adding a new per-pod step, put it in a shared helper and wire both reconcilers in the
same change. When touching a table that references a deployment, use the two-column FK
pattern above.

## Parity matrix (per-pod steps)

| Step | Sandbox | Production (agent) | Shared mechanism |
|---|---|---|---|
| ensure_namespace | assumes pre-provisioned | `ensure_namespace` | — (legit differ) |
| ensure_service_account | yes | yes | `k8s_client` |
| LLM secret | assumes pre-provisioned | `ensure_secret` | — (legit differ) |
| OPA configmap | assumes pre-provisioned | `ensure_opa_configmap` | — (legit differ) |
| Tool-credential envFrom | yes | yes | **`tool_secrets.py`** (shared) |
| Machine-identity registration | yes | yes | **`identity.py`** (shared) |
| Bundle inclusion | via `deployments` leg | via `production_deployments` leg | `bundle_generator` UNION |
| Pod spec / env / probes | shared | shared | `manifest_builder.build_deployment` |
| SDK / runner behavior | shared | shared | agent image |
| Envoy HTTPRoute | yes (best-effort) | **not built** (Envoy not installed) | — (known gap) |
| Workflow member tool creds | resolver no-ops | resolver no-ops | known gap (both envs) |

## Known gaps (tracked, not yet closed)
- **Workflow-production member tool credentials** — `resolve_and_copy_tool_secrets`
  resolves via `/agents/{name}/tools`; a workflow name isn't an agent, so it no-ops. Same
  in sandbox. Needs a member-aware resolver.
- **Envoy HTTPRoute in production** — sandbox builds one; production doesn't. No functional
  impact until Envoy Gateway is installed.

## Related
- Debugging deep-dives: `docs/debugging/006` (chat FK), `007` (tool creds), `008`
  (OPA identity).
- HITL model: `docs/design/hitl-approval-system.md`.
- Tool credential design: `docs/design/todo/tool-credential-management.md`.
