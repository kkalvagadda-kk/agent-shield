# AgentShield Platform: Agent Lifecycle Guide

This is a practical walkthrough of how you go from "I want to build an agent" to "it's running in production serving real users." No fluff. Just what the product does and how to use it.

---

## 1. Overview

AgentShield is a self-hosted platform for building, testing, governing, and deploying AI agents on Kubernetes. It gives you guardrails (safety scanning, policy enforcement, human approval gates) without requiring you to build them yourself.

The lifecycle looks like this:

```
Create  →  Configure  →  Version  →  Deploy to Sandbox  →  Test in Playground
  →  Evaluate  →  Publish  →  Admin Approval  →  Deploy to Production
```

Every agent and workflow goes through this path. There are no shortcuts to production -- a version has to pass evaluation and get admin approval before it can serve real traffic. That's the whole point.

The platform has four main areas in the UI sidebar:

- **Build** -- create and configure agents, tools, skills, workflows
- **Evaluate** -- playground testing, datasets, eval runs
- **Catalog** -- published artifacts ready for production deployment
- **Observe** -- traces and dashboards via Langfuse

---

## 2. Sandbox Journey

### Creating an Agent

Start at the Agents list page (`/`). Click "Create Agent." You'll need to provide:

- **Name** -- unique across the platform. Pick something descriptive. Can't change it later.
- **Team** -- determines which Kubernetes namespace your agent lives in (`agents-{team}`).
- **Description** -- what the agent does.
- **Agent Type** -- this is the key choice:
  - **SDK** (`sdk`) -- you bring your own container image. You write Python code using the AgentShield SDK, build a Docker image, push it to a registry, and the platform deploys that image. Full control, full responsibility.
  - **Declarative** (`declarative`) -- platform-managed. No code. You configure instructions, tools, and model through the UI. The platform runs your agent on a shared declarative-runner image that interprets your configuration at startup.
- **Execution Shape** -- how the agent runs:
  - `reactive` -- request/response. You send a message, you get a response. Done.
  - `durable` -- long-running with step tracking. The agent can checkpoint, pause for approval, and resume.

The API call under the hood is `POST /api/v1/agents`, which creates the agent record in Postgres and returns a UUID.

### Configuring the Agent

Once created, go to the agent detail page (`/agents/{name}`). The Settings tab lets you configure:

- **System prompt / Instructions** -- stored in the agent's `metadata.instructions` field. This is what the LLM sees as its system message.
- **LLM Provider** -- which model to use. The platform supports Anthropic and Bedrock providers. You set this up first under Settings > Models, where you provide API keys (encrypted and stored as K8s Secrets). Then link the provider to your agent.
- **Tools** -- attach tools from the platform registry. Tools come in four types:
  - `http` -- calls an external HTTP endpoint with configurable method, URL, headers, and body template.
  - `python` -- runs a Python snippet in a sandboxed executor sidecar.
  - `mcp_tool` -- a tool discovered from an MCP server connection.
  - `native` -- built-in platform tools.
  Each tool has a risk level (`low`, `medium`, `high`, `critical`). High-risk tools trigger human approval at runtime. Critical-risk tools block deployment entirely.
- **Memory** -- toggle cross-session memory on/off. When enabled, the agent can recall context from previous conversations.
- **Execution Shape** -- switch between reactive and durable.

### Creating Versions

Every time you deploy, the platform creates a **version** that snapshots the agent's current configuration. You can also create versions manually via `POST /api/v1/agents/{name}/versions`.

A version captures:

- `version_number` -- auto-incrementing integer (1, 2, 3, ...)
- `config` -- snapshot of `instructions`, `tools`, and `llm_provider_id` at creation time
- `image_tag` -- for SDK agents, the container image tag
- `tools` -- list of tool snapshots (name, risk level, type)
- `eval_passed` -- whether this version has passed evaluation (starts as `false`)
- `adversarial_eval_passed` -- for versions with high/critical-risk tools
- `git_sha` / `git_branch` -- optional source tracking

This is the key concept: **a version is immutable**. When you deploy version 3 to production, it uses the exact config that was captured when version 3 was created. Not "whatever the agent is configured as right now."

### Deploying to Sandbox

On the agent detail page, click **Deploy**. This creates a deployment record (`POST /api/v1/agents/{name}/deploy`) in `pending` status with `environment=sandbox`.

Here's what happens behind the scenes:

1. The **deploy-controller** polls for pending deployments every few seconds.
2. It runs pre-flight checks: team ownership verification, tool grant checks.
3. For sandbox deployments, there's **no eval gate** -- you can deploy any version. The eval gate is at publish time, not deploy time. This lets you iterate freely in sandbox.
4. For declarative agents, it swaps the image to the platform's `declarative-runner` container and injects the agent's configuration as environment variables.
5. It creates K8s resources in the team's namespace (`agents-{team}`):
   - A **ServiceAccount** for the agent (machine identity)
   - A **Deployment** named `{agent-name}-sandbox` with 1 replica
   - A **ClusterIP Service** so other services can reach the agent
   - An **HTTPRoute** (if Envoy Gateway is installed) for external routing
6. It polls until a replica is available (60s timeout), then marks the deployment `running`.

The deploy-controller reports status back to the registry API by PATCHing `/api/v1/deployments/{id}`. The UI polls and updates in real time.

### Testing in the Playground

Once you have a running sandbox deployment, open the **Playground** (`/playground`). It's a three-panel layout:

**Left panel** -- agent/workflow selector. Pick your agent, version, and deployment. You'll see promotion controls here too (more on that later).

**Center panel** -- the interaction surface. What you see depends on the agent type:
- **Reactive agents** get a **chat interface**. Type a message, hit send. The response streams in via Server-Sent Events (SSE). The registry-api proxies your message to the agent's pod (`http://{k8s-deployment-name}.{namespace}.svc.cluster.local:8080/chat`) and relays the SSE events back to your browser.
- **Durable agents** get a **JSON payload editor** and a **StepTracker** that shows live step updates as the agent progresses through its workflow.
- **Scheduled agents** (agents with a cron trigger) get a **RunNowPanel** that displays the cron schedule and a "Run Now (Test Fire)" button.
- **Event-driven agents** (agents with a webhook trigger) get a **TestTriggerPanel** where you can configure filter conditions and fire a test event.

**Right panel** -- the **Trace Panel**. As your agent runs, trace events appear in real time: tool calls, LLM interactions, safety scan results, OPA policy decisions. You don't need to switch to Langfuse to see what happened.

**HITL (Human-in-the-Loop) Approval Flow**: If your agent calls a high-risk tool during a playground run, the **HITL Panel** slides in. It shows you the tool name, risk level, and redacted arguments. You approve or deny right there. In the playground, you can self-approve (in production, a separate reviewer handles it -- self-approval is blocked).

After a run completes, the platform automatically runs **LLM-as-Judge scoring** (using Claude Haiku) and shows a score badge on the run. You can also give thumbs up/down feedback.

SSE events the playground handles: `text_delta` (streaming text), `tool_call_start`, `tool_call_end`, `approval_requested`, `done`, `error`.

### Execution Shapes in Detail

**Reactive** (request/response): Send a message, get a response. One shot. This is the default and the simplest shape. Good for Q&A agents, chatbots, lookup tools.

**Durable** (long-running with step tracking): The agent runs through a series of steps, each tracked in the `run_steps` table. Steps can be `pending`, `running`, `completed`, `failed`, or `awaiting_approval`. The UI shows a live step tracker. If a step requires approval, the whole run pauses until a human approves it, then continues from where it left off. Good for multi-step workflows, data processing, anything that takes more than a few seconds.

**Scheduled** (cron-triggered): You attach a schedule trigger to the agent with a cron expression (e.g., `0 9 * * *` for daily at 9am). The platform's scheduler service fires the agent on schedule. Each fire creates an `AgentRun` with `trigger_type=schedule`. The trigger can also carry an `input_payload` -- a JSON job spec that gets fed to the agent as input. You can set up failure alerting via `alert_email`.

**Event-driven** (webhook-triggered): You attach a webhook trigger that generates a unique URL with a bearer token. External systems POST events to that URL. The event gateway validates the token, evaluates filter conditions against the event payload, and fires the agent if the filters match. Each fire creates an `AgentRun` with `trigger_type=webhook`.

### Evaluations

Before a version can be published, it needs to pass evaluation. Here's how that works:

1. **Create a dataset** -- go to Evaluate > Datasets (`/playground/datasets`). A dataset is a named collection of test items. Each item has an input message and an expected output.

2. **Run an eval** -- select an agent and dataset, click "Run Eval." This creates an `EvalRun` record and launches a Kubernetes Job (`eval-{run_id[:8]}`) that:
   - Iterates through each dataset item
   - Sends each input to the agent's sandbox deployment
   - Collects the response
   - Runs LLM-as-Judge scoring on each response (comparing against expected output)
   - Records per-item results (`EvalRunResult` rows) with `judge_score` and `passed` fields
   - Computes an overall score

3. **Review results** -- on the eval results page (`/playground/eval-runs/{id}`), you see:
   - Overall score (color-coded: red < 0.4, amber 0.4-0.7, green > 0.7)
   - Per-item results with pass/fail, judge score, reasoning
   - Filter toggle for failed items only
   - Trace links for each item (clickable to see the full Langfuse trace)
   - A "Re-run" button to run the eval again with the same config

4. **Mark version passed** -- if the overall score is >= 0.7, click "Mark Version Passed." This patches the agent version with `eval_passed=true`. The pass threshold is 0.7 (configured in `EVAL_PASS_THRESHOLD`).

The playground's left panel also has a quick "Mark Version Passed" button that does the same thing without going through the full eval flow. Useful during development.

---

## 3. Workflow Journey

A **workflow** is a composite executable that chains multiple agents together. Instead of building one monolithic agent that does everything, you build several focused agents and wire them together.

### Creating a Workflow

Go to Build > Workflows (`/workflows`). Click "New Workflow." You provide:

- **Name** -- unique within the team
- **Team** -- same concept as agents
- **Description**
- **Orchestration mode** -- how agents are coordinated (see below)
- **Execution shape** -- `reactive` or `durable`

Under the hood: `POST /api/v1/workflows`.

### Adding Members

A workflow's members are existing agents from the same team. You add them via the builder or API (`POST /api/v1/workflows/{id}/members`). Each member has:

- **Role** -- optional label (e.g., `supervisor`, `researcher`, `writer`). The supervisor role has special meaning in supervisor orchestration mode.
- **Position** -- determines execution order for sequential/conditional modes.
- **Routing** -- optional JSON routing configuration.

Important constraint: member agents must be in the same team as the workflow. Cross-team membership isn't allowed.

### Four Orchestration Modes

**Sequential** -- agents run in order (by position or edge chain). Each agent's output becomes the next agent's input. If any agent fails, the whole workflow fails. Simple pipe.

**Conditional** -- at each node, the orchestrator evaluates outgoing edge conditions against the current agent's output and routes to the first match. Conditions use a small DSL (not `eval`): keyword matching (does the output contain "approved"?), or structured rules (`[{"field": "output", "op": "contains", "value": "error"}]`). A blank condition is the default/fallback edge.

**Supervisor** -- a coordinator agent (the member with `role=supervisor`) decides which worker to call next on each turn. The supervisor gets a list of available workers and the conversation history. It outputs the name of the next worker to invoke. The loop continues until the supervisor outputs "DONE" or a `max_iterations` cap is hit.

**Handoff** -- each agent can signal the next agent in its output by returning `{"handoff_to": "agent-name"}`. If no handoff is signaled, the orchestrator follows the agent's sole outgoing edge. The walk continues until there's no next hop. Good for customer service flows where the agent decides when to escalate.

All four modes have a hard safety cap of 50 total steps to prevent infinite loops.

### The Visual Workflow Builder

The Workflow Builder page (`/workflows/{id}/builder`) provides a canvas where you:

- Add agent nodes (from your team's agent list)
- Draw edges between agents (source -> target)
- Set conditions on edges (for conditional/handoff modes)
- Configure orchestration properties in the side panel
- Set execution shape and memory options

Edges are persisted as `WorkflowEdge` records with `source_agent_id`, `target_agent_id`, and an optional `condition` string.

### Deploying and Testing Workflows

Workflows have their own deployment model (`WorkflowDeployment`). Deploy from the builder or workflow detail page. The deployment record tracks `environment` (sandbox/production), `version_id`, and `status`.

Testing works through the same playground. Toggle "Workflow" in the left panel selector. The orchestrator runs the workflow by creating a parent `AgentRun` and one child `AgentRun` per member invocation. You can see the full run tree via `GET /api/v1/workflows/{id}/runs/{run_id}/tree`.

For workflow runs, the orchestrator dispatches messages to each member agent's deployed pod via their in-cluster Service URL (`http://{agent-name}-{environment}.agents-{team}.svc.cluster.local:8080/chat`). If a member agent isn't deployed, the workflow run fails with a clear error.

### Version Handling for Workflows

Workflow versions (`WorkflowVersion`) snapshot:

- `members` -- JSON array of member agent IDs, names, roles, positions
- `edges` -- JSON array of edge definitions (source, target, condition, position)
- `orchestration` -- the orchestration mode at time of snapshot
- `execution_shape` -- reactive or durable
- `config` -- any additional configuration
- `eval_passed` -- same gate as agents

Creating a version freezes the workflow's composition. Even if you later add or remove member agents, the version still references the original set.

---

## 4. Production Journey

Getting to production is a deliberate, multi-step process. There's no "deploy to prod" button on the agent detail page. You have to go through publish and approval first.

### The Publish Flow

When you're happy with a version, click **Publish** on the agent detail page. Here's what the platform checks before accepting the request (`POST /api/v1/agents/{name}/publish`):

1. **Eval gate** -- the target version must have `eval_passed=true`. If not, the request is rejected with `eval_not_passed`. No exceptions.
2. **Adversarial eval gate** -- if the agent uses high or critical risk tools, the version must also have `adversarial_eval_passed=true`.
3. **Critical risk block** -- if any assigned tool has `risk_level=critical`, publish is blocked entirely. Critical tools can't go to production through the normal path.
4. **Risk assessment** -- the platform computes the highest risk level across all assigned tools and records it on the publish request.

If all checks pass, a `PublishRequest` record is created with:
- `status=pending_review`
- `source_version_id` -- pinned to the specific version that passed eval
- `highest_risk_level` -- the worst risk level across tools
- `submitted_by` -- who clicked the button

The agent's `publish_status` transitions from `private` to `pending_review`.

### Version Pinning -- Why It Matters

The publish request pins `source_version_id` to the exact version you evaluated. Not "latest." Not "whatever the agent is configured as when someone approves it." The exact version.

This matters because:
- You might keep iterating on the agent after submitting the publish request.
- The approval might take hours or days.
- Without pinning, you'd approve something different than what was evaluated.

When the admin approves, the `config_snapshot` baked into the published version comes from the pinned version's data -- its tools list, config, image tag. That snapshot is what the production reconciler uses to configure the pod.

### Admin Approval Queue

Platform admins see pending publish requests under Admin > Publish Queue (`/admin/publish-requests`). Each request shows:

- Asset name and type (agent/workflow/tool/skill)
- Submitter
- Highest risk level (color-coded)
- Latest eval score and link to the eval run
- Submission date

The admin can:
- **Approve** (`POST /api/v1/admin/publish-requests/{id}/approve`) -- requires specifying which teams get access (`grantee_teams`). This:
  - Sets the publish request status to `approved`
  - Sets the asset's `publish_status` to `published`
  - Creates `AssetGrant` records for each grantee team
  - Creates an audit log entry (`GrantAudit`)
  - Upserts a `PublishedArtifact` record (the catalog entry)
  - Creates a `PublishedVersion` with the config snapshot from the pinned version
  - Auto-increments the version label (v1, v2, v3...)

- **Reject** (`POST /api/v1/admin/publish-requests/{id}/reject`) -- sets status to `rejected`, resets the asset's `publish_status` back to `private`.

### The Catalog

After approval, the artifact appears in the **Catalog** (`/catalog`). The catalog is the marketplace for production-ready artifacts. It shows:

- Card grid with artifact name, type badge, description
- Owner team, latest published version label, active deployment count
- Type filter toggles (agent, tool, skill, workflow)

Only artifacts your team owns or has been explicitly granted access to are visible. This is enforced by the `AssetGrant` table.

Click an artifact to see its detail page (`/catalog/{id}`), which shows:
- All published versions with config snapshots
- Active production deployments with status
- Run history for production deployments
- 24h stats (run count, error rate, p50 latency, total cost)
- List of granted teams

### Deploying to Production

From the catalog detail page, click **Deploy** on a specific published version. This creates a `ProductionDeployment` record in `pending` status (`POST /api/v1/catalog/{id}/deploy`).

The **production reconciler** (a separate polling loop in the deploy-controller) picks it up and:

1. Creates a dedicated namespace: `production-{agent-name}`
2. Provisions a ServiceAccount for the agent
3. Creates LLM credential secrets
4. Builds a K8s Deployment named `{agent-name}-production`, using the config snapshot from the published version (not live agent config)
5. Creates a ClusterIP Service
6. Polls for readiness (120s timeout, longer than sandbox's 60s)
7. Reports status back to the catalog API

The production reconciler is completely separate from the sandbox reconciler. It uses a different API path (`/api/v1/catalog/internal/pending-deployments`), different namespace conventions, and different config sources.

### Production vs Sandbox Differences

| Aspect | Sandbox | Production |
|--------|---------|------------|
| Namespace | `agents-{team}` | `production-{agent-name}` |
| Naming | `{agent}-sandbox` | `{agent}-production` |
| Eval gate | No (deploy freely) | Yes (must pass eval to get here) |
| Config source | Live agent metadata | Pinned version config snapshot |
| Readiness timeout | 60s | 120s |
| HITL approval | Self-approve allowed | Requires authorized reviewer |
| Playground chat | Yes | Via catalog chat page |
| Access control | Creator's own agents | Grant-based (admin-controlled) |
| OPA governance | Standard | Full enforcement |

### Managing Production Deployments

Once deployed, you can manage production deployments from the catalog detail page:

- **Upgrade** -- switch to a different published version (PATCH with `action=upgrade`)
- **Suspend** -- scale to 0 replicas (keeps the K8s resources, just stops traffic)
- **Resume** -- scale back up
- **Terminate** -- delete the K8s resources entirely

---

## 5. Version Handling Deep Dive

### Agent Versions

Each agent version is a point-in-time snapshot. When you save agent configuration (instructions, tools, model) and deploy, the platform creates a new version that captures:

```json
{
  "version_number": 3,
  "config": {
    "instructions": "You are a helpful assistant...",
    "tools": ["search-web", "read-document"],
    "llm_provider_id": "uuid-of-provider"
  },
  "tools": [
    {"name": "search-web", "risk": "low", "type": "http"},
    {"name": "read-document", "risk": "medium", "type": "python"}
  ],
  "image_tag": "my-agent:v3",
  "eval_passed": false,
  "adversarial_eval_passed": false
}
```

Versions are immutable. You can't edit version 3 after it's created. You make changes and create version 4.

The version list is available on the agent detail page's **Versions** tab, sorted newest-first. Each version shows its number, eval status, creation date, and notes.

### Workflow Versions

Workflow versions capture the full composition:

```json
{
  "version_number": 2,
  "members": [
    {"agent_id": "uuid", "name": "researcher", "role": "worker", "position": 0},
    {"agent_id": "uuid", "name": "writer", "role": "worker", "position": 1}
  ],
  "edges": [
    {"source": "researcher", "target": "writer", "condition": null}
  ],
  "orchestration": "sequential",
  "execution_shape": "durable",
  "eval_passed": false
}
```

If you later add a third agent to the workflow, that doesn't affect version 2. Version 2 will always have two members.

### The eval_passed Gate

The `eval_passed` flag is the gatekeeper for publish. Here's how it gets set:

1. **Automatic**: When an eval run completes with `overall_score >= 0.7`, the eval runner sets `eval_passed=true` on the associated agent version.
2. **Manual**: Click "Mark Version Passed" in the playground or eval results page. This calls `PATCH /api/v1/agents/{name}/versions/{id}` with `eval_passed=true`.

For agents with high-risk tools, there's also `adversarial_eval_passed` -- same idea but specifically for adversarial/red-team evaluations.

Without `eval_passed=true`, the publish endpoint returns HTTP 422 with `{"error": "eval_not_passed"}`. The UI disables the Publish button and shows a tooltip: "Run an eval that passes before publishing."

### Version Pinning in Publish Requests

When you publish, the request includes `source_version_id`. This pins the publish to that specific version. The flow:

1. You evaluate version 5. It scores 0.85. You mark it passed.
2. You click Publish. The system verifies version 5 has `eval_passed=true` and creates a publish request with `source_version_id = version_5_id`.
3. Meanwhile, you keep working. You create versions 6 and 7.
4. Admin approves the publish request. The system looks up version 5's config snapshot -- not version 7's. That snapshot becomes the `PublishedVersion.config_snapshot`.
5. When you deploy from the catalog, the production reconciler reads that config snapshot. Version 5's exact configuration runs in production.

This chain (evaluate -> pin -> approve -> snapshot -> deploy) ensures that what you tested is exactly what runs in production. No drift.

---

## 6. Observability

### Langfuse Integration

AgentShield ships with Langfuse (v3) deployed as a platform component. Every agent run generates traces that flow into Langfuse automatically. The platform injects Langfuse environment variables (`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`) into agent pods at deploy time via the deploy-controller.

What gets traced:
- LLM calls (prompt, completion, model, token count)
- Tool calls (name, args, result, duration)
- Safety scan results
- OPA policy decisions (allow/deny/require_approval)
- Approval requests and decisions
- Overall run latency and status

### Run History and Status Tracking

Every agent invocation creates an `AgentRun` record. Runs track:

- `status`: queued, running, completed, failed, blocked, awaiting_approval, cancelled
- `trigger_type`: manual, api, schedule, webhook, workflow
- `context`: production or playground
- Cost, token counts, latency
- Langfuse trace ID (links back to the full trace)
- For workflow runs: `parent_run_id` links child runs to the parent, building a run tree

Run stats are computed per-deployment (24h rolling window): run count, error rate, p50/p95 latency, total cost.

### Trace Viewer in the UI

Three ways to view traces:

1. **Playground Trace Panel** -- real-time, right panel during playground testing. Shows events as they stream in.
2. **Deployment Overview** (`/agents/{name}/d/{depId}`) -- runs tab lists all runs for a specific deployment. Each run links to Langfuse.
3. **Observability pages** (`/observability/traces`, `/observability/dashboard`) -- platform-wide trace browser and dashboards. The Traces page embeds the Langfuse UI. The Dashboard page shows aggregate metrics.
4. **Eval Results Trace Drawer** -- on the eval results page, click any result row to open a trace drawer showing the full Langfuse trace for that evaluation item.

Trace URLs are constructed as `{LANGFUSE_PUBLIC_URL}/project/{LANGFUSE_PROJECT_ID}/traces/{trace_id}` and embedded directly in the run response objects.
