#!/usr/bin/env bash
# =============================================================================
# deploy-eks.sh — deploy AgentShield to a self-managed k8s cluster on AWS EC2
#                 (EBS-CSI storage, aws-load-balancer-controller), exposed via
#                 an INTERNAL AWS NLB in front of Envoy Gateway.
#
# This is the CLOUD sibling of scripts/deploy-cpe2e.sh (which is local/kind only
# and CANNOT work here: it builds `registry.internal/...` images and relies on a
# shared Docker daemon; EC2 nodes can't see local images).
#
# Usage:
#   KUBECONFIG=~/.kube/test-cluster-kube-config.yaml bash scripts/deploy-eks.sh
#   SKIP_BUILD=1 ... bash scripts/deploy-eks.sh     # reuse images already in ECR
#
# WHAT BIT US (encoded here so it never bites again) --------------------------
#  1. amd64 != AMD. `amd64` is the x86-64 ISA (runs on Intel AND AMD). Building
#     on an Apple-silicon Mac yields arm64 -> nodes fail with
#     "no match for platform in manifest". ALWAYS buildx --platform linux/amd64.
#  2. pgvector AVX-512 SIGILL. Bitnami's stock pgvector 0.8.0 is compiled
#     -march=native on an AVX-512 builder. On nodes WITHOUT AVX-512 (Broadwell
#     t2; AMD Zen1-3 c5a/c6a/t3a) `CREATE INDEX ... USING ivfflat` kills the
#     backend (signal 4: Illegal instruction) -> the whole Alembic transaction
#     rolls back -> 0 tables -> registry-api init-crashloops forever.
#     Fix: services/postgresql-pgvector (pgvector rebuilt with OPTFLAGS="").
#  3. global.imageRegistry is a BITNAMI global — setting it also repoints
#     postgres/redis. The chart uses global.appImageRegistry instead.
#  4. Vendored .tgz go stale: re-run `helm dependency update` after ANY
#     sub-chart template edit or helm silently uses the OLD packaged chart.
#  5. Envoy Gateway v1.8.2 CrashLoops on Gateway-API v1.4.1 CRDs (wants TLSRoute
#     at v1; cluster serves v1alpha2/3). v1.7.5 works.
#  6. The internal-NLB annotations must exist at Service CREATION (scheme is
#     immutable) -> EnvoyProxy CRD + GatewayClass parametersRef (in the chart).
#  7. AGENT pods don't use the namespace default SA — they run under a per-agent
#     SA (machine identity), so patching default's imagePullSecrets does NOT
#     reach them and every agent ImagePullBackOffs with "no basic auth
#     credentials". deploy-controller >=0.1.38 puts imagePullSecrets on the pod
#     spec via AGENT_IMAGE_PULL_SECRETS (chart: global.imagePullSecrets).
#     Invisible on kind, which side-loads images and needs no auth at all.
#  8. infra/ is NOT part of the chart. opa-bundle-server + the opa-sidecar-config
#     ConfigMap + playground RBAC are applied separately (step 3b). Without the
#     ConfigMap every agent pod hangs in ContainerCreating; the controller only
#     self-creates it on the PRODUCTION path, never for sandbox deploys.
#  9. Anything injected into AGENT pods must be namespace-QUALIFIED. Agents run
#     in agents-*, so a bare `agentshield-postgresql` does not resolve there and
#     the fail-loud checkpointer CrashLoopBackOffs the pod. postgres-passwords
#     therefore stores `<release>-postgresql.<ns>` — correct from every namespace
#     (registry-api included). Same reason LANGFUSE_HOST is qualified.
#
# RESTORING A BACKUP AFTERWARDS: see the note at the bottom — you MUST scale the
# DB clients to 0 first or the restore SILENTLY half-applies.
# =============================================================================
set -euo pipefail

# ── Config ───────────────────────────────────────────────────────────────────
AWS_PROFILE_NAME="${AWS_PROFILE_NAME:-kkalyan-aws-key}"
REGION="${REGION:-us-west-2}"
ACCOUNT="${ACCOUNT:-517602344783}"
ECR="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
NS="${NS:-agentshield-platform}"
RELEASE="${RELEASE:-agentshield}"
EG_VERSION="${EG_VERSION:-v1.7.5}"      # v1.8.2 is INCOMPATIBLE — see note 5
CHART="charts/agentshield"
VALUES="charts/agentshield/values-eks.yaml"
SKIP_BUILD="${SKIP_BUILD:-0}"

# Image tags (keep in sync with values-eks.yaml / values.yaml)
REGISTRY_API_TAG="0.2.258"   # 0.2.258: deleting a workflow version that had ever been deployed returned 500. The handler set its deployments to status='terminated' and left the rows; workflow_deployments.version_id is a NO ACTION FK, so the version delete that followed hit workflow_deployments_version_id_fkey. The agent path did it right (detach agent_runs, then delete the deployment rows) — two copies of one rule with the wrong copy untested. Both now call deployment_lifecycle.detach_and_delete_deployments, which takes the deployment model and the AgentRun FK column as explicit arguments rather than sniffing what it was handed. agent_runs.workflow_deployment_id is also NO ACTION and had no detach on the workflow side at all. suite-41 T-S41-006.   #   # 0.2.257: GET /schedules returns recent_runs — the last 10 run statuses per trigger, newest first, from a lateral on agent_runs in the SAME query as the rest of the row (a per-row fetch would be N+1 against an endpoint that already has the joins). Feeds the run-history sparkline: one last-run status cannot tell FLAKY from BROKEN from FINE.   # 0.2.256: agent DELETE now REMOVES the agent's schedule triggers instead of only disarming them (trigger_lifecycle.delete_schedule_triggers) + migration 0078 applies the rule to the backlog. A disarmed schedule on a deleted agent is inert — disarmed in the same transaction, and T-S95-004 proves the scheduler ignores it regardless — so it was pure noise on an operations page: 63 of 100 schedule rows on the cluster belonged to long-deleted agents. WEBHOOK triggers are exempt and keep the disarm treatment, because webhook_clients.trigger_id is ON DELETE CASCADE and removing one silently destroys the applications registered against it. Archive and quarantine are unchanged — quarantine is incident response and must not destroy evidence. T-S95-001 rewritten to prove the asymmetry on one agent carrying both kinds.   # 0.2.255: publish is IDEMPOTENT. publish_agent db.add()ed a PublishRequest unconditionally — no query for an existing pending row, no uniqueness constraint — so two agents on the cluster carry two pending requests each (3s apart, two different callers, one pinning a version and one not). A second submission is the same intent restated; a second queue row pushes the ambiguity onto a reviewer with nothing saying which supersedes which. Now returns the existing request, re-pointing it at the asked-for version if it differs. Idempotent not 409: there is no withdraw endpoint and status admits only pending_review/approved/rejected, so a refusal strands the operator. T-S17-010.   # 0.2.254: the refusal no longer appends "then re-enable the trigger" — unconditional and wrong in the ordinary case (a trigger armed on a sandbox-only agent was never disabled; it fires the moment production exists, proven by the schedule-lifecycle journey). This function takes an AGENT, not a trigger, so it cannot know, and advice to use a control that does not apply costs more than silence. Arm state already has two honest surfaces.   # 0.2.253: the production remedy names ALL THREE steps. "Publish the agent" was reachable but INSUFFICIENT — publishing writes published_artifacts (a catalog listing), NOT production_deployments (approve_publish_request never touches ProductionDeployment, and its artifact_id FKs to published_artifacts.id), so an operator did the named thing, watched it succeed, and got the identical message back. Now: publish -> Admin Publish Queue approve -> Marketplace Deploy Latest. + catalog suspend wrote "suspending", which the production_deployments CHECK forbids, so EVERY production suspend answered 500 since the endpoint shipped (suite-39 covered the sandbox path, whose constraint does admit it). T-S96-010, T-S39-007.   # 0.2.252: arm state IS `enabled`; one shared trigger-PATCH interpreter (the agent/workflow handlers had drifted on clearing the disarm record); /schedules stops synthesising armed_at. T-S96-007/008/009.   # 0.2.251:   # 0.2.251: /schedules armed_at reflects ARM STATE, not creation time. The page derives isArmed as `armed_at != null` (lib/triggerArm.ts), so mapping it from created_at made EVERY row render "Armed" — deprecated agents showed an Armed pill beside "this schedule is disabled". Caught on the real page.   # 0.2.250: R5 — GET /api/v1/schedules (routers/schedules.py), the cross-artifact operations read. Read-only: the page routes every mutation back to the artifact-scoped trigger routers, so no second writer. will_fire is computed from the SAME trigger_liveness view the scheduler reads (migration 0077) and the SAME resolve_dispatch_target the run door uses — not a fourth restatement of "runnable", which was wrong twice in one day when stated independently. Deny-by-default + team-scoped per R7 (Decision 33 shape, applied before the endpoint exists rather than after a leak). suite-96.   # 0.2.249: lockstep tag   # 0.2.249: no registry-api change; moves with the scheduler/gateway workflow-liveness fix.   # 0.2.248: lockstep tag   # 0.2.247: lifecycle disarm — delete_agent / archive_workflow / quarantine_agent call trigger_lifecycle.disarm_triggers in the SAME transaction, so the DB cannot hold an armed trigger on a dead artifact. Migration 0076 adds disabled_reason/disabled_at and reaps the 37 already armed. Re-enable is an explicit human act and clears the reason. Chrome journey leg 8 showed the UI delete producing a deprecated agent + enabled hourly schedule in one click. suite-95.   # 0.2.246: resolve_dispatch_target owns BOTH production legs. An agent reaches production two ways, in DIFFERENT namespaces: deployments(environment=production) -> agents-{team}, and Publish -> production_deployments with its own production-{artifact}-{id8} namespace. 0.2.243-0.2.245 read only the first and always composed agents-{team}, so a PUBLISHED agent — the only production route Studio offers — was told "no running production deployment" and advised to Publish: the action that lands in the unseen leg. Namespace now comes from the validated row (k8s_namespace / pd.namespace), never recomposed. sandbox-production-parity-architecture §41; mirrors bundle_generator's UNION. T-S94-008.   # 0.2.245: scheduled health is CONFIG-FIRST, not last-run-derived. It asked resolve_dispatch_target the LIVE question instead of reading the last run status, so (a) a schedule that can never fire no longer reads "healthy" just because nothing failed yet, and (b) the badge clears the moment production exists rather than waiting up to an hour for the next fire ("I deployed it and it still says Failing" — reported from the UI). New AgentHealthResponse.dispatch_error (CURRENT, actionable) kept separate from last_error (HISTORICAL) — one field with two meanings is Decision 32's lesson. failing = cannot dispatch; degraded = dispatchable but last run failed.   # 0.2.244: dispatch-refusal message names a remedy the operator can actually reach — Studio has NO deploy-to-production control (its Deploy button opens a "Deploy to sandbox" modal; the only UI route is Publish, eval-gated per Decision 20), so the previous "deploy the agent to production" sent readers hunting for a button that does not exist. Caught by driving the real screen (claude-in-chrome-schedule-failure-journey leg 7).   # 0.2.243: trigger dispatch resolves ONE environment — resolve_dispatch_target owns admissibility AND address (built from the validated row); refusal recorded as a legible failed run, not a DNS error. + health.last_error + GET /agents/{name}/triggers/{id}/runs. MUST match values.yaml.   # 0.2.242: HITL decide/list resolve the caller from the JWT Bearer (get_optional_user) — there is NO Envoy SecurityPolicy injecting claim headers in this deploy, so the browser (JWT-only) decide fell back to reviewer_id="studio-user" -> 403. In-cluster suites still send X-User-Sub. suite-93 + live leg 12b. MUST match values.yaml.   # 0.2.241: HITL approvals read the caller from X-User-Id (Envoy JWT claimToHeaders injects sub->X-User-Id, NOT X-User-Sub) in decide_approval + list_approvals — the browser decide was falling back to reviewer_id="studio-user" (403) and the console listed UNSCOPED. In-cluster suites still send X-User-Sub. suite-93 T-S93-004. MUST match values.yaml.   # 0.2.240: HITL decide platform-admin special case — an admin-role caller (user_team_assignments role 'platform-admin') may decide ANY approval without a per-tool ApprovalAuthority grant; fixed _ADMIN_ROLES spelling (hyphen 'platform-admin' was underscore, never matched); _has_authority_for_tool/role-query use .first() not scalar_one_or_none() (was 500 MultipleResultsFound on 2+ grants). Fixes prod-HITL reviewer 403 (journey leg 12b); suite-93. MUST match charts/agentshield/values.yaml.   # 0.2.239: Playground HITL resume-stream (resume_stream_playground_run) resolves the run by session_id OR PK. The F-F session_id threading (0.2.235) made thread_id=session_id diverge from PlaygroundRun.id, so PlaygroundPage sent the thread_id but the endpoint matched only PlaygroundRun.id -> 404 "Playground run not found" -> approved run never resumed ("Stream connection lost"). Caught by the Claude-in-Chrome journey leg 12; suite-92 regression. MUST match charts/agentshield/values.yaml.   # 0.2.238: Decouple memory-save from agent-recall — save_turn always persists the transcript (History for every agent); list_memory for_agent_context gates recall on memory_enabled.   # 0.2.237: F-E (Issue 2) — pod_stream translates message_start→agent_start + reasoning→rationale so AgentChatPage/Workflow/Catalog chat split a single agent's turns into separate bubbles.   # 0.2.236: F-B (Issue 3) — trace drawer falls back to durable Postgres run_steps when Langfuse has no spans (get_trace_detail + playground get_trace_by_id), access-scoped.   # 0.2.235: F-F (Issue 1) — Playground session_id threading: PlaygroundRunCreate.session_id + shared builder stamps it on PlaygroundRun so reactive turns thread into ONE conversation (thread_id = session_id).   # 0.2.234: Eval Slice 0 — per-request eval resolution + eval_source provenance + deny-by-default reads (Decisions 32-33). MUST match charts/agentshield/values.yaml: this file drives the BUILD, values.yaml drives the DEPLOY, and a mismatch is an ImagePullBackOff, not an error message.   # 0.2.232: OAuth authorize requests the RESOURCE scopes (RFC 9728 PRM) not the AS openid scope — GitHub token can call tools not just discover.   # 0.2.231: MCP OAuth tolerates no refresh token (RFC 6749 optional) — store+serve the access token when the AS issues no refresh (classic GitHub OAuth App: non-expiring access token).   # 0.2.230: mcp_oauth path-aware PRM discovery (RFC 9728 §3.1 _prm_candidates) — GitHub-style path-scoped protected-resource metadata is found instead of 404ing to the AS fallback.   # 0.2.225: MCP-as-tool-source — mcp_servers router + migration 0072 + mcp-proxy client (matches values.yaml). 0.2.224: HITL reactive-chat approval-status fix (_chat_thread_id keyed by session_id). 0.2.221: Decision 30 — matches values.yaml
MCP_PROXY_TAG="0.1.6"   # 0.1.5: bound call_tool by MCP_CONNECT_TIMEOUT_SECONDS (revoked-token upstream 401 fails closed instead of hanging). 0.1.0: NEW centralized MCP wire client (tool discovery + governed tool calls). Dockerfile COPYs scripts/e2e/fixtures/stub_mcp_server.py → REPO-ROOT build context.
DEPLOY_CONTROLLER_TAG="0.1.41"   # 0.1.41: parity with deploy-cpe2e.sh — EKS lagging the controller has already caused one agent-CrashLoop incident here.   # 0.1.40:   # 0.1.40: sandbox pods get AGENTSHIELD_PLAYGROUND/SANDBOX=true (was hardcoded false) so sandbox HITL approvals are playground-context (inline + resumable), not routed to the reviewer console. 0.1.39: per-provider env map; >=0.1.38 imagePullSecrets on agent pods (note 7)
DECLARATIVE_RUNNER_TAG="0.1.67"   # 0.1.67: memory decouple — _load_memory_context sends for_agent_context=true (scope=agent) so recall gates on memory_enabled while the transcript still saves.   # 0.1.66: rebuilds sdk 0.2.9 — F-E (Issue 2): stream_events emits message_start per LLM turn + reasoning as its own event (Bedrock thinking blocks).   # 0.1.65: rebuilds sdk 0.2.8 — HTTP tool body_template JSON-escape fix (parse-then-substitute-in-leaves; free-text bodies with quotes/newlines no longer break JSON). 0.1.62: rebuilds sdk 0.2.5 (MCP arg-marshaling fix — drop omitted-optional None args; fixes Tavily). 0.1.61/0.1.59: prior (matches values.yaml declarativeRunnerTag)
STUDIO_TAG="0.1.180"   # 0.1.180: Schedules page R5 second pass — run-history sparkline (10 outcomes, oldest to newest; empty renders "no runs yet" so a newly armed schedule does not look broken) + edit-in-place modal for cron/timezone/payload, routed back to the artifact-scoped trigger routers so the schedules endpoint stays read-only. "Run now" is deliberately NOT built — /internal/runs/start has no authentication; see docs/bugs/internal-run-door-has-no-authentication.md.   # 0.1.179: the Playground Publish button LATCHES to a disabled green "Awaiting review", matching its two siblings in the same panel which already did. It reverted to looking un-clicked, so the panel kept no record of what happened once the toast faded — and the natural answer to "did that work?" is to click again. Toast now names where the request went (Admin > Publish Queue). NOTE: the earlier claim that publish gave NO feedback was wrong — both paths always toasted; see the rewritten bug doc.   # 0.1.178: scheduled-agent UX. NEW RouteToProduction strip on the agent page — the six-step path across five screens, shown as one picture with the operator's position on it, replacing four disconnected warnings; its final step reads health.dispatch_error so it cannot disagree with the code that fires the schedule. Create wizard: llm_provider_id now REQUIRED (an agent with no model can never run and was accepted silently), the schedule notice names all three production steps instead of "Publish", and the Model select gets an aria-label (Field renders its label as a sibling, so the control had no accessible name). Publish tooltip names WHERE to run the eval.   # 0.1.177: ONE arm control on the Schedules page (the Disarm button wrote an undeclared field: 200 OK, nothing written) + STUDIO_BUILD marker synced, now gate-enforced.   # 0.1.175:   # 0.1.175: /schedules un-gated (route + nav in lockstep) now that its backend exists.   # 0.1.174: ARM-TIME warning   # 0.1.174: ARM-TIME warning — prevention instead of post-hoc explanation. Create wizard says a new agent's schedule will not fire until published (unconditional: a new agent is definitionally not in production); Settings warns conditionally from health.dispatch_error, the same resolver the dispatch door uses so the two surfaces cannot disagree. Reported twice — "the scheduled run failed and the UX does not show why", then "I still see this when deploying" (Deploy targets SANDBOX, which a schedule never reads).   # 0.1.173: health box names WHICH question it answers — "This schedule cannot run" (red, config) vs "The last run failed" (amber, history) — and dispatch_error outranks a stale run error so the operator is not sent to debug the wrong layer. Reason box now renders for degraded too.   # 0.1.172: scheduled overview reads runs by TRIGGER (deployment FKs are NULL on every trigger run, so the card was empty while the badge went red); renders error_message + health.last_error; alert card warns "On — but not delivered". MUST match values.yaml.   # 0.1.171: Agent-list Delete uses an in-app confirm modal (data-testid delete-agent-modal) instead of native window.confirm() — native confirm blocks browser automation + is inconsistent with the Deploy/Edit modals (journey leg 22). MUST match values.yaml.   # 0.1.170: F-E (Issue 2) — attachRationale accumulates (streaming reasoning); chatStream surfaces split per-turn via the new agent_start mapping.   # 0.1.169: F-E (Issue 2) — ChatPane opens a bubble per LLM turn (message_start → openAuthorBubble) + renders a distinct reasoning block. MUST match values.yaml.   # 0.1.168: F-F (Issue 1) — Playground threads session_id (ChatPane forwards chatKey) + auto-rehydrates the agent's latest thread on select. MUST match values.yaml.   # 0.1.167: Eval Slice 0 — lib/evalVerdict single owner; publish queue + dataset dot grade against the run's own threshold. MUST match values.yaml (see note on REGISTRY_API_TAG).   # 0.1.161: MCP-as-tool-source Studio UI (Phases 12-14). 0.1.160: human-grantee grant creation (Decision 30) + T024
SCHEDULER_TAG="0.1.5"   # 0.1.5: reads the trigger_liveness view instead of restating the predicate.   # 0.1.4: status <> archived   # 0.1.4: workflow liveness = status <> 'archived', matching internal.py's run door. 0.1.2 used w.status='published' (nothing writes it -> every workflow schedule died); 0.1.3 used w.publish_status='published' (reachable but TOO STRICT -> a workflow reaches production by deploying its MEMBER AGENTS and its own row stays draft/private, so suite-66 broke). Door and filter now share ONE definition.   # 0.1.3: workflow liveness reads publish_status, NOT status. 0.1.2 gated on w.status='published' — a value NOTHING writes (only writer sets 'archived'; publication lives in publish_status). 0 of 140 workflows matched, so every workflow schedule silently died, and suite-95 stayed green because "dead things do not fire" is also true when nothing fires.   # 0.1.2: read-side defence — schedule query filters a.status=active / w.status=published. Defence in depth behind the write-side disarm; the scheduler and event-gateway are SEPARATE services, so filtering one leaves the other armed.
EVENT_GATEWAY_TAG="0.1.8"   # 0.1.8: reads the trigger_liveness view instead of restating the predicate.   # 0.1.7: lockstep liveness fix   # 0.1.7: same workflow-liveness fix, in lockstep.   # 0.1.6: publish_status (too strict)   # 0.1.6: same publish_status fix, changed in lockstep — separate images with different definitions of runnable is the drift this filter guards.   # 0.1.5: read-side status filter in webhook_auth._TRIGGER_SQL   # 0.1.4: Decision 30 gateway cutover — webhook_auth.py resolves applications+artifact_role_grants (not webhook_clients) — matches values.yaml
PYTHON_EXECUTOR_TAG="0.1.0"
EMBEDDING_SIDECAR_TAG="0.1.0"   # mirrors deploy-cpe2e.sh; values-eks.yaml deploys it, so EKS must push it
ECHO_AGENT_TAG="0.1.0"   # 0.1.0: e2e fixture image for suite-2 SDK-agent leg. It existed only on the local Docker Desktop cluster from a hand-run docker build, so on EKS the pod sat in ImagePullBackOff ("registry.internal ... no such host") and T-S2-005 timed out. Built by both deploy scripts now, so the fixture follows the platform instead of one machine.
EVAL_RUNNER_TAG="0.1.14"   # 0.1.14: parity with deploy-cpe2e.sh.   # 0.1.10:
MINIO_CP1_TAG="0.1.0"
PGVECTOR_TAG="17.6.0-portable"

# Platform credentials (dev defaults; match scripts/deploy-cpe2e.sh AND the
# pg_dumpall backups so a restore doesn't break auth).
PG_PASS="${PG_PASS:-DevPass2024}"
REDIS_PASS="${REDIS_PASS:-RedisPass2024}"
MINIO_USER="${MINIO_USER:-agentshield-admin}"
MINIO_PASS="${MINIO_PASS:-MinioPass2024}"
KC_ADMIN_PASS="${KC_ADMIN_PASS:-AdminPass2024}"
KC_PLATFORM_ADMIN_PASS="${KC_PLATFORM_ADMIN_PASS:-PlatformAdmin2024}"
KC_REVIEWER_PASS="${KC_REVIEWER_PASS:-Reviewer2024}"
ENCRYPTION_KEY="${ENCRYPTION_KEY:-dGVzdGtleS10ZXN0a2V5LXRlc3RrZXktdGVzdGtleTA=}"

export AWS_PROFILE="$AWS_PROFILE_NAME"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "=== AgentShield EKS/EC2 deploy ==="
echo "    cluster : $(kubectl config current-context)"
echo "    ecr     : ${ECR}"
echo "    ns      : ${NS}"

# ── Step 0: preflight ────────────────────────────────────────────────────────
echo ""
echo "[0/7] Preflight..."
# Retry, don't abort on the first blip: the private API endpoint (VPN) flaps, and a
# single failed /readyz should not throw away a 30-min build. ~5 min of patience.
_wait_reachable() {
  local i
  for i in $(seq 1 30); do
    kubectl get --raw='/readyz' >/dev/null 2>&1 && return 0
    echo "  cluster not reachable yet (attempt $i/30) — retry in 10s (VPN flapping?)"
    sleep 10
  done
  return 1
}
_wait_reachable || { echo "FATAL: cluster unreachable after ~5min (VPN up?)"; exit 1; }
kubectl get storageclass block-storage >/dev/null 2>&1 || echo "  WARN: no 'block-storage' StorageClass — PVCs may not bind"
kubectl get deploy -n kube-system aws-load-balancer-controller >/dev/null 2>&1 || echo "  WARN: aws-load-balancer-controller absent — the internal NLB won't provision"
# Warn loudly if nodes lack AVX-512 — we ship the portable pgvector anyway, but
# this is the #1 historical failure and worth surfacing.
echo "  note: using portable pgvector (${PGVECTOR_TAG}) — safe on nodes without AVX-512"

# ── Step 1: ECR repos + login ────────────────────────────────────────────────
echo ""
echo "[1/7] ECR login + repos..."
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$ECR" >/dev/null
# The repo list is DERIVED from the build list below, not maintained beside it.
# They are the same set by definition — every image this script pushes needs a
# repository to push to — and keeping two hand-written copies means adding a service
# to one and not the other. That is exactly what happened with embedding-sidecar:
# the builder was added, the repo was not, and the push failed with "The repository
# with name 'agentshield/embedding-sidecar' does not exist", which fails the WHOLE
# deploy on the first attempt. A missing repo took down an unrelated memory bump.
ECR_REPOS=$(grep -oE '^[[:space:]]+b[[:space:]]+[a-z0-9-]+' "${BASH_SOURCE[0]}" | awk '{print $2}' | sort -u)
if [ -z "$ECR_REPOS" ]; then
  echo "FATAL: could not derive the ECR repo list from this script's build calls." >&2
  echo "       The 'b <svc> ...' lines are the source of truth; if their shape changed," >&2
  echo "       fix this grep rather than reintroducing a second hand-written list." >&2
  exit 1
fi
for r in $ECR_REPOS; do
  aws ecr describe-repositories --repository-names "agentshield/$r" --region "$REGION" >/dev/null 2>&1 \
    || { echo "  creating missing repo agentshield/$r"; aws ecr create-repository --repository-name "agentshield/$r" --region "$REGION" >/dev/null; }
done
echo "  $(echo "$ECR_REPOS" | wc -w | tr -d ' ') repos ready"

# ── Step 2: build + push (linux/amd64!) ──────────────────────────────────────
if [ "$SKIP_BUILD" = "1" ]; then
  echo ""
  echo "[2/7] SKIP_BUILD=1 — reusing images already in ECR"
else
  echo ""
  echo "[2/7] Building + pushing images (linux/amd64)..."
  docker buildx inspect eksbuilder >/dev/null 2>&1 || docker buildx create --name eksbuilder >/dev/null
  docker buildx use eksbuilder
  b() { # <svc> <tag> <context> [dockerfile]
    local svc="$1" tag="$2" ctx="$3" df="${4:-}" attempt
    echo "  -> $svc:$tag"
    # Retry build+push: the ECR push is the other thing the flapping VPN/DNS kills
    # ("no such host" / EOF mid-upload). buildx layer cache makes a retry cheap (only
    # the missing layers re-push), and we refresh the ECR token each round in case it
    # was the ~12h expiry rather than the network.
    for attempt in 1 2 3 4; do
      if [ -n "$df" ]; then
        docker buildx build --platform linux/amd64 -t "$ECR/agentshield/$svc:$tag" -f "$df" --push "$ctx" >/dev/null && return 0
      else
        docker buildx build --platform linux/amd64 -t "$ECR/agentshield/$svc:$tag" --push "$ctx" >/dev/null && return 0
      fi
      echo "     $svc:$tag build/push attempt $attempt/4 failed — refresh ECR auth + retry in 15s"
      aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$ECR" >/dev/null 2>&1 || true
      sleep 15
    done
    echo "FATAL: $svc:$tag failed to build/push after 4 attempts (VPN/DNS?)"; return 1
  }
  b registry-api        "$REGISTRY_API_TAG"       services/registry-api/
  # mcp-proxy: REPO-ROOT context (.) + explicit Dockerfile — its Dockerfile COPYs
  # scripts/e2e/fixtures/stub_mcp_server.py, which lives outside services/mcp-proxy/.
  b mcp-proxy           "$MCP_PROXY_TAG"          .  services/mcp-proxy/Dockerfile
  b deploy-controller   "$DEPLOY_CONTROLLER_TAG"  services/deploy-controller/
  b declarative-runner  "$DECLARATIVE_RUNNER_TAG" .  services/declarative-runner/Dockerfile
  b studio              "$STUDIO_TAG"             studio/
  b scheduler           "$SCHEDULER_TAG"          services/scheduler/
  b event-gateway       "$EVENT_GATEWAY_TAG"      services/event-gateway/
  b python-executor     "$PYTHON_EXECUTOR_TAG"    services/python-executor/
  b eval-runner         "$EVAL_RUNNER_TAG"        services/eval-runner/
  b echo-agent          "$ECHO_AGENT_TAG"         services/echo-agent/
  # values-eks.yaml points embeddingSidecar at ECR, so it MUST be pushed there or the
  # pod sits in ImagePullBackOff for the life of the deployment — which is exactly what
  # it had been doing, unnoticed, because nothing checked that every image the chart
  # references has a builder. The coupling gate now does.
  b embedding-sidecar   "$EMBEDDING_SIDECAR_TAG"  services/embedding-sidecar/
  b minio-cp1           "$MINIO_CP1_TAG"          services/minio-cp1/
  b postgresql-pgvector "$PGVECTOR_TAG"           services/postgresql-pgvector/
fi

# ── Step 3: namespaces + ECR pull secret ─────────────────────────────────────
echo ""
echo "[3/7] Namespaces + ECR pull secret..."
for ns in "$NS" agents-platform agentshield-playground; do
  kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
done
TOKEN="$(aws ecr get-login-password --region "$REGION")"
for ns in "$NS" agents-platform agentshield-playground; do
  kubectl create secret docker-registry agentshield-ecr \
    --docker-server="$ECR" --docker-username=AWS --docker-password="$TOKEN" \
    -n "$ns" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  # studio + the raw templates do NOT propagate global.imagePullSecrets — patch
  # the default SA so every pod in the ns can pull from ECR.
  kubectl patch serviceaccount default -n "$ns" \
    -p '{"imagePullSecrets":[{"name":"agentshield-ecr"}]}' >/dev/null
done
echo "  3 namespaces + pull secret + SA patched"
echo "  NOTE: the ECR token expires in ~12h. Re-run this step to refresh it."
# NOTE: patching the *default* SA does NOT cover agent pods — they run under a
# per-agent SA (machine identity). Their pull secret comes from the controller's
# AGENT_IMAGE_PULL_SECRETS env (chart: global.imagePullSecrets), deploy-controller
# >= 0.1.38. Older controllers => agents ImagePullBackOff "no basic auth credentials".

# ── Step 3b: cluster infra the chart does NOT own ────────────────────────────
# These live in infra/ and are applied by deploy-cpe2e.sh, so they are easy to
# miss on a fresh cluster. Both are load-bearing:
#  - opa-bundle-server: nginx that serves the policy bundle registry-api builds;
#    every agent's OPA sidecar polls it.
#  - opa-sidecar-config: mounted into each agent pod's OPA sidecar. MISSING => the
#    pod hangs forever in ContainerCreating ("configmap opa-sidecar-config not
#    found"). The controller only self-creates it on the PRODUCTION path
#    (production_reconciler), never for sandbox deploys.
#  - playground-runner: ClusterRole+Binding letting registry-api drive playground
#    pods/jobs.
echo ""
echo "[3b/7] Cluster infra (OPA bundle server + sidecar config + playground RBAC)..."
kubectl apply -f infra/opa-bundle-server/configmap-nginx-conf.yaml >/dev/null
kubectl apply -f infra/opa-bundle-server/service.yaml >/dev/null
kubectl apply -f infra/opa-bundle-server/deployment.yaml >/dev/null
kubectl apply -f infra/opa-bundle-server/configmap-opa-config.yaml >/dev/null
kubectl apply -f infra/rbac/playground-runner-clusterrole.yaml >/dev/null
# Langfuse alias Services (load-bearing when langfuse.enabled=true): the Bitnami
# clickhouse/minio sub-charts name their Services agentshield-clickhouse / agentshield-s3,
# but langfuse derives agentshield-langfuse-clickhouse / agentshield-langfuse-s3 — without
# these aliases langfuse-web CrashLoopBackOffs ("lookup agentshield-langfuse-clickhouse ...
# no such host" on the clickhouse migration). Harmless if langfuse is disabled (unused svcs).
kubectl apply -f infra/langfuse/clickhouse-alias-svc.yaml >/dev/null
echo "  opa-bundle-server + opa-sidecar-config + playground-runner + langfuse-aliases applied"

# ── Step 4: platform secrets ─────────────────────────────────────────────────
echo ""
echo "[4/7] Platform secrets..."
kubectl create secret generic agentshield-secrets -n "$NS" \
  --from-literal=registry-api-url="http://${RELEASE}-registry-api.${NS}:8000" \
  --from-literal=database-url="postgresql://postgres:${PG_PASS}@${RELEASE}-postgresql:5432/agentshield" \
  --from-literal=direct-database-url="postgresql://postgres:${PG_PASS}@${RELEASE}-postgresql:5432/agentshield" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl create secret generic agentshield-encryption -n "$NS" \
  --from-literal=key="${ENCRYPTION_KEY}" \
  --from-literal=AGENTSHIELD_ENCRYPTION_KEY="${ENCRYPTION_KEY}" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl create secret generic postgres-passwords -n "$NS" \
  --from-literal=keycloak="${PG_PASS}" --from-literal=agentshield="${PG_PASS}" \
  --from-literal=langfuse="${PG_PASS}" --from-literal=langgraph="${PG_PASS}" \
  --from-literal=appsmith="${PG_PASS}" \
  --from-literal=registry-api-url="postgresql+asyncpg://postgres:${PG_PASS}@${RELEASE}-postgresql.${NS}:5432/agentshield" \
  --from-literal=registry-api-direct-url="postgresql+asyncpg://postgres:${PG_PASS}@${RELEASE}-postgresql.${NS}:5432/agentshield" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
# The host is namespace-QUALIFIED deliberately — see note 9 in the header.
kubectl create secret generic redis-password -n "$NS" \
  --from-literal=redis-password="${REDIS_PASS}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl create secret generic minio-credentials -n "$NS" \
  --from-literal=root-user="${MINIO_USER}" --from-literal=root-password="${MINIO_PASS}" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl create secret generic keycloak-admin-password -n "$NS" \
  --from-literal=admin-password="${KC_ADMIN_PASS}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl create secret generic keycloak-user-passwords -n "$NS" \
  --from-literal=platform-admin="${KC_PLATFORM_ADMIN_PASS}" \
  --from-literal=agent-reviewer="${KC_REVIEWER_PASS}" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl create secret generic langfuse-api-keys -n "$NS" \
  --from-literal=public-key="pk-lf-agentshield-dev-local-0001" \
  --from-literal=secret-key="sk-lf-agentshield-dev-local-0001" \
  --from-literal=nextauth-secret="agentshield-nextauth-dev-2024-sec" \
  --from-literal=salt="$(openssl rand -base64 32)" \
  --from-literal=encryption-key="$(openssl rand -hex 32)" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl create secret generic slack-credentials -n "$NS" \
  --from-literal=bot-token="xoxb-placeholder-dev-token" \
  --from-literal=signing-secret="placeholder-signing-secret-dev" \
  --from-literal=webhook-url="https://hooks.slack.com/services/placeholder/dev" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
echo "  9 platform secrets"

# ── Step 4b: gateway TLS ─────────────────────────────────────────────────────
# The Gateway's HTTPS listener references Secret `gateway-tls` via
# certificateRef. TLS is REQUIRED for login: Keycloak's PKCE uses Web Crypto,
# which browsers only expose in a secure context (https). Plain http => no login.
#
# Chicken-and-egg dodge: the ELB DNS isn't known until the Gateway's Service is
# created, but a wildcard SAN `*.elb.<region>.amazonaws.com` matches ANY ELB
# hostname in the region (the ELB name is a single DNS label), so we can mint the
# cert up-front. Self-signed => browser warning; click through and the secure
# context is still valid. Swap in a real cert (or ACM + DNS) for anything real.
#
# CN is a fixed short string, NOT the hostname: X.509 caps CN at 64 bytes and an
# ELB DNS name is ~77 (`openssl req` then dies with "string too long"). Identity
# lives in the SAN, which has no length cap and is the only field browsers have
# honoured since Chrome 58 / RFC 2818 deprecated CN matching.
echo ""
echo "[4b/7] Gateway TLS cert (self-signed, wildcard SAN)..."
if kubectl get secret gateway-tls -n "$NS" >/dev/null 2>&1; then
  echo "  gateway-tls already exists — keeping it (delete it to regenerate)"
else
  TLSDIR="$(mktemp -d)"
  # stderr goes to a file, not /dev/null: openssl's progress dots are stderr, so
  # blanket-suppressing it also hides real errors (that masked the CN overflow).
  if ! openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
      -keyout "$TLSDIR/tls.key" -out "$TLSDIR/tls.crt" \
      -subj "/CN=AgentShield Gateway/O=AgentShield" \
      -addext "subjectAltName=DNS:*.elb.${REGION}.amazonaws.com" \
      -addext "basicConstraints=critical,CA:FALSE" \
      -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
      -addext "extendedKeyUsage=serverAuth" 2>"$TLSDIR/err"; then
    echo "ERROR: openssl failed to build the gateway cert:" >&2
    grep -i "error" "$TLSDIR/err" >&2 || cat "$TLSDIR/err" >&2
    rm -rf "$TLSDIR"; exit 1
  fi
  kubectl create secret tls gateway-tls \
    --cert="$TLSDIR/tls.crt" --key="$TLSDIR/tls.key" -n "$NS" \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  rm -rf "$TLSDIR"
  echo "  gateway-tls created (self-signed, SAN *.elb.${REGION}.amazonaws.com)"
fi

# ── Step 5: Envoy Gateway controller ─────────────────────────────────────────
echo ""
echo "[5/7] Envoy Gateway controller (${EG_VERSION})..."
if helm status eg -n envoy-gateway-system >/dev/null 2>&1; then
  echo "  already installed"
else
  EG_VERSION="$EG_VERSION" bash scripts/setup-envoy-gateway.sh >/dev/null 2>&1 || true
  kubectl wait --timeout=180s -n envoy-gateway-system \
    --for=condition=Available deployment/envoy-gateway >/dev/null 2>&1 \
    || { echo "FATAL: envoy-gateway not Available. If it CrashLoops with"; \
         echo "       'no matches for kind TLSRoute in version .../v1', the EG"; \
         echo "       version is incompatible with the cluster's Gateway API"; \
         echo "       CRDs — v1.7.5 works with the v1.4.1 experimental bundle."; exit 1; }
fi
echo "  controller Available"

TMP_DEP_ERR="$(mktemp)"
trap 'rm -f "$TMP_DEP_ERR"' EXIT

# ── Step 6: deploy (two-phase for publicUrl) ─────────────────────────────────
echo ""
echo "[6/7] Helm deploy..."
# Re-vendor sub-charts. This MUST NOT be best-effort: helm renders the packaged
# .tgz in charts/, not your edited directory, so a stale .tgz means `helm upgrade`
# silently applies OLD templates. It is deceptive rather than loud — image TAGS
# still update (the old template reads .Values.image.tag), so the rollout reports
# success while your template change never shipped. Fail here instead.
if ! helm dependency update "$CHART" 2>"$TMP_DEP_ERR"; then
  echo "ERROR: helm dependency update failed — sub-chart .tgz would be STALE." >&2
  echo "       Deploying now would silently apply old templates. Common cause:" >&2
  echo "       Chart.yaml pins a sub-chart version that differs from its" >&2
  echo "       charts/<name>/Chart.yaml version (that aborts the whole update)." >&2
  cat "$TMP_DEP_ERR" >&2
  exit 1
fi
helm upgrade --install "$RELEASE" "$CHART" -f "$VALUES" -n "$NS" --timeout 15m

echo ""
echo "    waiting for the internal NLB hostname..."
ELB=""
for _ in $(seq 1 40); do
  ELB="$(kubectl get gateway -n "$NS" -o jsonpath='{.items[0].status.addresses[0].value}' 2>/dev/null || true)"
  [ -n "$ELB" ] && break
  sleep 15
done
[ -z "$ELB" ] && { echo "FATAL: Gateway never got an address (check aws-load-balancer-controller)"; exit 1; }
echo "    ELB: ${ELB}"

# ── Langfuse (option B): served at ROOT on its own nip.io subdomain off the internal NLB ──
# The NLB has one DNS name (the ELB) that Studio + every path route already own, so langfuse
# needs a DISTINCT Host. We resolve the NLB IP the ELB points at and use a nip.io host that
# self-resolves to it (public DNS, nothing to provision) — langfuse then runs root + prebuilt
# on that host (no NEXT_PUBLIC_BASE_PATH custom image). Routed via
# envoy-gateway.gateway.langfuseHostname; SSO issuer stays the ELB (patched by the reconcile
# helper below, which also fixes the OIDC back-channel hostAlias).
NLB_IP="$(nslookup "$ELB" 2>/dev/null | awk '/^Address: /{print $2}' | grep -E '^[0-9]+\.' | tail -1)"
LF_SETS=()
if [ -n "$NLB_IP" ]; then
  LF_HOST="langfuse.${NLB_IP}.nip.io"
  echo "    langfuse: https://${LF_HOST}  (nip.io -> NLB ${NLB_IP})"
  LF_SETS+=(--set-string "global.langfuseUrl=https://${LF_HOST}")
  LF_SETS+=(--set-string "envoy-gateway.gateway.langfuseHostname=${LF_HOST}")
  LF_SETS+=(--set-string "langfuse.langfuse.nextauth.url=https://${LF_HOST}")
  # Ensure the gateway-tls SAN covers LF_HOST — the base cert (Step 4b) only covers
  # *.elb.<region>.amazonaws.com, so a browser hitting the langfuse subdomain would
  # otherwise get CERT_COMMON_NAME_INVALID.
  #
  # IDEMPOTENT: only (re)mint when the CURRENT cert does not already cover both ${ELB}
  # and ${LF_HOST}. A self-signed cert is trusted per-fingerprint, so regenerating it on
  # every deploy invalidates every browser's accepted exception — and a cert interstitial
  # that pops mid-OAuth-redirect reads to the user as "login redirect is broken" (it
  # silently blocks the 302 back to Studio). Regenerate ONLY when the SAN set actually
  # needs to change (first langfuse-adding deploy, or a changed NLB IP). See
  # docs/bugs/eks-gateway-tls-regen-breaks-login.md
  CUR_SAN="$(kubectl get secret gateway-tls -n "$NS" -o jsonpath='{.data.tls\.crt}' 2>/dev/null \
    | base64 -d 2>/dev/null | openssl x509 -noout -ext subjectAltName 2>/dev/null || true)"
  if echo "$CUR_SAN" | grep -q "DNS:${LF_HOST}" && echo "$CUR_SAN" | grep -q "DNS:${ELB}"; then
    echo "    gateway-tls already covers ${ELB} + ${LF_HOST} — keeping it (preserves browser cert trust)"
  else
    TLSDIR2="$(mktemp -d)"
    if openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
        -keyout "$TLSDIR2/tls.key" -out "$TLSDIR2/tls.crt" \
        -subj "/CN=AgentShield Gateway/O=AgentShield" \
        -addext "subjectAltName=DNS:*.elb.${REGION}.amazonaws.com,DNS:${ELB},DNS:${LF_HOST}" \
        -addext "basicConstraints=critical,CA:FALSE" \
        -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
        -addext "extendedKeyUsage=serverAuth" 2>/dev/null; then
      kubectl create secret tls gateway-tls --cert="$TLSDIR2/tls.crt" --key="$TLSDIR2/tls.key" \
        -n "$NS" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
      echo "    gateway-tls SAN now covers ${LF_HOST} (regenerated — browsers must re-accept the new cert once)"
    else
      echo "    WARN: cert regen for ${LF_HOST} failed — langfuse SSO may hit a cert warning"
    fi
    rm -rf "$TLSDIR2"
  fi
else
  echo "    WARN: could not resolve NLB IP for ${ELB} — langfuse subdomain routing skipped"
fi

# Phase 2: now that the hostname exists, bake it into publicUrl so server-side
# URL generation (registry-api EVENT_GATEWAY_PUBLIC_URL, webhook URLs shown in
# Studio, Keycloak issuer) matches what the browser actually uses.
echo "    re-applying with publicUrl=https://${ELB} ..."
helm upgrade --install "$RELEASE" "$CHART" -f "$VALUES" -n "$NS" \
  --set-string "global.publicUrl=https://${ELB}" \
  ${LF_SETS[@]+"${LF_SETS[@]}"} \
  --timeout 15m

# Langfuse SSO issuer must be the EKS Keycloak issuer (the ELB) — NOT the nip.io value baked
# into values.yaml (that's the docker-desktop host). langfuse's OIDC discovery + browser
# redirect target this issuer; a wrong host makes "Sign In" fail. Patched post-helm because
# it's a subchart list env (langfuse.langfuse.additionalEnv) that `--set` can't cleanly
# target and helm re-applies the base value on every upgrade. No hostAlias needed on EKS:
# langfuse-web reaches https://<ELB>/realms in-cluster via NLB hairpin (verified at deploy).
if [ -n "${LF_HOST:-}" ] && kubectl get deploy "${RELEASE}-langfuse-web" -n "$NS" >/dev/null 2>&1; then
  echo "    patching langfuse-web AUTH_KEYCLOAK_ISSUER -> https://${ELB}/realms/agentshield"
  kubectl set env deploy/"${RELEASE}-langfuse-web" -n "$NS" \
    "AUTH_KEYCLOAK_ISSUER=https://${ELB}/realms/agentshield" >/dev/null 2>&1 || true
fi

# Ensure the langfuse S3 event bucket exists. langfuse writes every ingestion event to
# S3 (bucket `langfuse-media`) BEFORE the worker reads it into ClickHouse; a missing
# bucket makes every event fail with "The specified bucket does not exist" and traces
# silently never appear ("Trace not found" at the View-Trace link — no error surfaced in
# Studio). values.yaml now aligns langfuse.s3.defaultBuckets to the bucket so a fresh PV
# provisions it at boot, but an EXISTING s3 PV was provisioned with the old default
# ("langfuse") and won't re-run that path — so create it here too (idempotent).
# See docs/bugs/langfuse-missing-s3-event-bucket.md
if kubectl get deploy "${RELEASE}-langfuse-web" -n "$NS" >/dev/null 2>&1; then
  S3POD="$(kubectl get pods -n "$NS" -o name | grep -E "${RELEASE}-s3-" | head -1)"
  if [ -n "${S3POD}" ]; then
    echo "    ensuring langfuse S3 bucket 'langfuse-media' exists"
    kubectl exec "${S3POD}" -n "$NS" -- sh -c '
      MC=/opt/bitnami/minio-client/bin/mc
      "$MC" alias set _lf http://localhost:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1
      "$MC" mb --ignore-existing _lf/langfuse-media >/dev/null 2>&1
    ' >/dev/null 2>&1 && echo "    ok  bucket langfuse-media present" \
      || echo "    WARN could not ensure langfuse-media bucket (traces may not ingest)"
  fi
fi

# Scope the main HTTPRoute (agentshield-routes: Studio + registry + keycloak + minio +
# event-gateway) to the ELB host. With the EKS wildcard listener (gateway.hostname=""),
# agentshield-routes renders with NO hostname, so it attaches to EVERY listener — including
# the langfuse-https listener — and its `/` -> Studio rule then wins the langfuse subdomain,
# serving Studio instead of Langfuse at the trace link. Pinning the route to the ELB host
# keeps it off the langfuse listener while the listener itself stays wildcard.
if kubectl get httproute agentshield-routes -n "$NS" >/dev/null 2>&1; then
  echo "    scoping agentshield-routes -> ${ELB} (so langfuse subdomain isn't shadowed)"
  kubectl patch httproute agentshield-routes -n "$NS" --type=merge \
    -p "{\"spec\":{\"hostnames\":[\"${ELB}\"]}}" >/dev/null 2>&1 || true
fi

# ── Step 7: verify ───────────────────────────────────────────────────────────
echo ""
echo "[7/7] Waiting for rollouts..."
# registry-api is the MIGRATION GATE. A broken/blocked alembic migration leaves the OLD
# pod Running while the new one sits Pending — and a mere WARN here silently ships stale
# code (observed 2026-07-20: migration 0070's dangling down_revision '0069' KeyError left
# registry-api on the old image; every API check then passed against the wrong bytes). So
# registry-api failing to roll out is FATAL, not a warning.
if kubectl rollout status "deploy/${RELEASE}-registry-api" -n "$NS" --timeout=300s; then
  echo "  ok  registry-api"
else
  echo "FATAL: registry-api did not roll out. Most likely the alembic-migrate init" >&2
  echo "       container failed. Inspect it:" >&2
  echo "  kubectl -n ${NS} get pods -l app.kubernetes.io/name=registry-api" >&2
  echo "  kubectl -n ${NS} logs \$(kubectl -n ${NS} get pods -l app.kubernetes.io/name=registry-api --field-selector=status.phase=Pending -o name | head -1) -c alembic-migrate" >&2
  exit 1
fi
for d in studio deploy-controller scheduler event-gateway python-executor mcp-proxy; do
  kubectl rollout status "deploy/${RELEASE}-${d}" -n "$NS" --timeout=240s >/dev/null 2>&1 \
    && echo "  ok  ${d}" || echo "  WARN ${d} not ready"
done

# Pin the platform-admin global role to the LIVE Keycloak sub (self-healing across realm
# recreations — see scripts/seed-platform-admin-role.sh header). Without this the Studio
# Admin menu silently disappears whenever the realm's admin sub changes. Non-fatal: a
# fresh cluster may still be settling Keycloak; the message tells the operator to re-run.
echo ""
echo "[7b/7] Seeding platform-admin role (live Keycloak sub)..."
bash scripts/seed-platform-admin-role.sh \
  || echo "  WARN seed-platform-admin-role failed — re-run: bash scripts/seed-platform-admin-role.sh"

# Keep Langfuse trace-link SSO working after any deploy (self-skips if langfuse not deployed —
# EKS sets langfuse.enabled=false, so this is a no-op there but stays correct if it's enabled).
bash "$(dirname "$0")/reconcile-langfuse-hostalias.sh" "$NS"

echo ""
echo "=== Done ==="
echo ""
kubectl get pods -n "$NS" --no-headers | awk '{print $3}' | sort | uniq -c | sed 's/^/    /'
echo ""
echo "  Access (from the VPN — the NLB is INTERNAL, no public IP):"
echo "    https://${ELB}/"
echo "    (self-signed cert => click through the browser warning)"
echo ""
echo "  Restore a backup — IMPORTANT, read this:"
echo "    pg_dumpall --clean does DROP DATABASE, which FAILS while registry-api /"
echo "    keycloak hold connections. restore-postgres.sh runs ON_ERROR_STOP=0, so"
echo "    it SILENTLY HALF-RESTORES (some tables land, others stay empty)."
echo "    Always:"
echo "      kubectl scale deploy/${RELEASE}-registry-api deploy/${RELEASE}-keycloak \\"
echo "        deploy/${RELEASE}-scheduler deploy/${RELEASE}-event-gateway \\"
echo "        deploy/${RELEASE}-deploy-controller --replicas=0 -n ${NS}"
echo "      # verify: select count(*) from pg_stat_activity where datname in ('agentshield','keycloak');  => 0"
echo "      bash scripts/restore-postgres.sh backups/<newest>.sql.gz"
echo "      # then scale back up (registry-api=2, keycloak=1, scheduler=2, event-gateway=2, deploy-controller=1)"
echo ""
