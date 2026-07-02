# AgentShield Studio — Phases A, B, C: UX Gaps + Consumer Chat

**Self-contained implementation plan.** Everything needed to implement all three phases is documented
here. No context from prior conversations is required.

---

## 1. Goal and E2E Flow

The platform implements a three-actor lifecycle:

```
Developer  →  Admin       →  Consumer
  Build          Promote        Discover
  Evaluate       Grant          Chat / Deploy
  Publish        ↑ separate     
                 two steps
```

**Developer (Playground section)**
1. Create agents, skills, tools, workflows
2. Evaluate interactively (chat + trace), iterate
3. Run structured eval against datasets
4. Submit agent for admin review via "Publish" button

**Admin (Administration section)**
1. See ALL artifacts across all teams (read-only browse)
2. Review pending publish requests — see enriched name/description, not raw UUID
3. Promote: approve the artifact into the catalog (flips `publish_status=published`)
4. Grant: separately assign which teams get access (Access Control tab)
5. Manage HITL production queue (production context only, no playground noise)

**Consumer (My Agents + Catalog)**
1. See agents their team has been granted access to ("My Agents")
2. Chat directly with any running agent via a clean UI (no dev overlays)
3. Browse the org-wide Catalog to discover what's published
4. Deploy agents/workflows their team has rights to

---

## 2. Can E2E Tests Cover the UX?

**Short answer: No — not the current shell-based tests.**

All 13 existing suites (`scripts/e2e/suite-*.sh`) make `curl` and `kubectl exec` API calls.
They test HTTP contracts. They use a `MANUAL` category for anything requiring browser interaction.

| Coverage level | Tool | Status |
|---|---|---|
| API contract (new endpoints) | curl in shell scripts | **Phase C will add this** |
| Browser UI (React flows, navigation) | Playwright / Cypress | **Not in scope — not set up** |

Phase C adds `suite-14-consumer-chat.sh` covering the new backend API contracts. Browser UX
verification remains in the `MANUAL` checklist category already established by existing suites.

---

## 3. Current Codebase Snapshot

### Studio (`studio/src/`)

| File | Purpose |
|---|---|
| `App.tsx` | Router — all routes defined here |
| `components/Sidebar.tsx` | Left nav — two collapsible sections: Playground, Administration |
| `api/registryApi.ts` | All API calls via axios; Keycloak Bearer interceptor |
| `contexts/AuthContext.tsx` | `useAuth()` → `{ user, token, logout, hasRole }` |
| `lib/keycloak.ts` | PKCE OIDC init; `getKeycloak()`, `getParsedToken()` |

**Current routes in `App.tsx`:**
```
/                           → AgentListPage
/agents/new                 → CreateAgentPage
/agents/:name               → AgentDetailPage       (has Publish button)
/agents/:name/deploy        → DeployAgentPage
/providers                  → ProvidersPage
/workflows, /workflows/new, /workflows/:id → WorkflowsPage / CanvasPage
/tools                      → ToolsPage
/skills                     → SkillsPage
/catalog                    → CatalogPage
/playground                 → PlaygroundPage         (chat + trace + HITL banner)
/playground/datasets        → DatasetsPage
/playground/eval-runs/:id   → EvalResultsPage
/admin/publish-requests     → AdminPublishRequestsPage
/admin/access               → AdminAccessPage        (Users / Teams / Grants tabs)
/admin/grants               → AdminGrantsPage
/admin/approval-authority   → AdminApprovalAuthorityPage
/hitl                       → HITLDashboardPage
```

**Current sidebar nav groups (`Sidebar.tsx`):**
```typescript
const PLAYGROUND_BUILD = [
  { label: "Agents",    to: "/",          end: true },
  { label: "Skills",    to: "/skills",    end: false },
  { label: "Tools",     to: "/tools",     end: false },
  { label: "Workflows", to: "/workflows", end: false },
];
const PLAYGROUND_TEST = [
  { label: "Test",     to: "/playground",         end: true },   // ← rename to Evaluate
  { label: "Datasets", to: "/playground/datasets", end: false },
];
const ADMIN_ITEMS = [
  { label: "Access Control", to: "/admin/access" },
  { label: "Publish Queue",  to: "/admin/publish-requests" },
  { label: "HITL Queue",     to: "/hitl" },
  { label: "Approvers",      to: "/admin/approval-authority" },
];
```

### Registry API (`services/registry-api/`)

| File | Purpose |
|---|---|
| `main.py` | FastAPI app; mounts all routers |
| `models.py` | SQLAlchemy ORM models |
| `schemas.py` | Pydantic request/response schemas |
| `auth_middleware.py` | JWKS JWT verification; `get_optional_user`, `require_user` |
| `db.py` | Async session factory; `get_db` |
| `routers/agents.py` | CRUD for agents; `/api/v1/agents/` |
| `routers/admin.py` | Publish requests, grants, approvers; `/api/v1/admin/` |
| `routers/admin_users.py` | Keycloak user management; `/api/v1/admin/users/` |
| `routers/deployments.py` | Deployment CRUD; `/api/v1/agents/{name}/deployments/` and `/api/v1/deployments/` |
| `routers/playground.py` | Playground runs + approvals; `/api/v1/playground/` |

**Key existing API endpoints:**
```
GET  /api/v1/agents/?status=active&limit=100          → PaginatedResponse[AgentResponse]
GET  /api/v1/agents/{name}                            → AgentResponse
GET  /api/v1/deployments/?status=running              → PaginatedResponse[DeploymentResponse]
GET  /api/v1/agents/{name}/deployments/               → list[DeploymentResponse]
GET  /api/v1/admin/publish-requests?status=pending_review → PaginatedResponse[PublishRequestResponse]
POST /api/v1/admin/publish-requests/{id}/approve      → { approved: bool, grants_created: int }
GET  /api/v1/admin/teams-summary                      → list[TeamSummary]  (members + grants)
GET  /api/v1/approvals/?status=pending                → production HITL approvals only
GET  /api/v1/playground/approvals                     → playground HITL approvals only
GET  /api/v1/playground/runs                          → list[PlaygroundRunResponse]
```

**Key schema shapes:**
```python
# PublishRequestResponse (schemas.py:648) — currently missing asset_name
class PublishRequestResponse(BaseModel):
    id: uuid.UUID
    asset_id: uuid.UUID       # raw UUID — needs enrichment
    asset_type: str           # 'agent' | 'tool' | 'skill' | 'workflow'
    submitted_by: str
    submitted_at: datetime
    status: str               # 'pending_review' | 'approved' | 'rejected'
    highest_risk_level: str
    dependency_declaration: dict
    reviewed_by: Optional[str]
    reviewed_at: Optional[datetime]
    review_notes: Optional[str]
    # MISSING: asset_name, asset_description, asset_team

# PublishRequestApprove (schemas.py:663) — grantee_teams currently required
class PublishRequestApprove(BaseModel):
    grantee_teams: list[str]  # ← must become Optional[list[str]] = []
    expires_at: Optional[datetime] = None

# DeploymentResponse (schemas.py — check exact fields)
# Fields include: id, agent_id, agent_name, status, deployed_at, error_message, version_id

# AssetGrant — active grant check:
# WHERE asset_id = <agent.id> AND grantee_team = <caller_team>
#       AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now())
```

**User → team lookup (for grant checking):**
```sql
SELECT team_name FROM user_team_assignments WHERE user_sub = :sub
```
Table: `user_team_assignments(user_sub PK, team_name, role, assigned_by, assigned_at)`

**Keycloak JWT claims (from `auth_middleware.py`):**
The decoded token contains `sub` (Keycloak UUID). The team is looked up via
`user_team_assignments.user_sub = token['sub']`.

**Image versions (current):**
```
registry-api:  0.2.25
studio:        0.1.24
deploy-controller: 0.1.7
declarative-runner: 0.1.1
```
Always bump patch version before building. Update `scripts/deploy-cpe2e.sh` in the same commit.

---

## 4. Phase A — Studio UX Gaps (frontend only)

All changes in this phase are `studio/src/` only. No backend changes. Build studio image as
`0.1.25` when done.

---

### A1 — Rename "Test" → "Evaluate" in sidebar

**File:** `studio/src/components/Sidebar.tsx`

Change:
```typescript
// Before
const PLAYGROUND_TEST = [
  { label: "Test",     to: "/playground",         end: true },
  { label: "Datasets", to: "/playground/datasets", end: false },
];

// After
const PLAYGROUND_TEST = [
  { label: "Evaluate", to: "/playground",         end: true },
  { label: "Datasets", to: "/playground/datasets", end: false },
];
```

Also update the `PlaygroundPage.tsx` internal header label ("Playground" → "Evaluate") and the
"Sandbox mode" panel text to match.

---

### A2 — HITL Dashboard: production context only

**File:** `studio/src/pages/HITLDashboardPage.tsx`

The backend already filters by context — `GET /api/v1/approvals/` only returns
`context=production` approvals (confirmed by suite-8 T-S8-006). The Studio page currently
calls this endpoint without issue, but the page header says "HITL Queue" with no qualifier.

Changes:
1. Update page `<h1>` to "Production HITL Queue"
2. Update subtitle to "Human-in-the-loop approval requests from deployed agents"
3. Add an info callout: "Playground approvals are handled inline in the Evaluate tab."
4. Remove the `CONTEXT_CHIP` colors for `playground` context since they will never appear here.

No API change needed.

---

### A3 — Publish Queue: show asset name, not UUID

**File:** `studio/src/pages/AdminPublishRequestsPage.tsx`

**Problem:** The `PublishRequestResponse` only returns `asset_id` (UUID) and `asset_type`. The
admin table renders `pr.asset_id.slice(0, 8)…` which is meaningless.

**Fix (frontend-side enrichment):**

In `AdminPublishRequestsPage.tsx`, after fetching publish requests, fetch the agent/tool/skill/
workflow lists once and build a name map keyed by UUID:

```typescript
// Fetch all assets in parallel to build id→name map
const [agentsPage, toolsPage, skillsPage, workflowsPage] = await Promise.all([
  listAgents(200, 0, "active"),     // status=active
  listTools(200, 0),
  listSkills(200, 0),
  listWorkflows(),
]);

const assetNameMap: Record<string, { name: string; description?: string; team?: string }> = {};
for (const a of agentsPage.items)  assetNameMap[a.id]  = { name: a.name, description: a.description, team: a.team };
for (const t of toolsPage.items)   assetNameMap[t.id]  = { name: t.name, description: t.description, team: t.owner_team };
for (const s of skillsPage.items)  assetNameMap[s.id]  = { name: s.name, description: s.description, team: s.team };
for (const w of workflowsPage)     assetNameMap[w.id]  = { name: w.name, description: w.description, team: w.team };
```

Use `useQuery` with `queryKey: ["all-assets-for-publish-queue"]` so it fetches once.

In the table, replace:
```typescript
// Before
<td className="px-4 py-3 font-mono text-xs text-slate-500">
  {pr.asset_id.slice(0, 8)}…
</td>

// After
<td className="px-4 py-3">
  <p className="text-sm font-medium text-slate-800">
    {assetNameMap[pr.asset_id]?.name ?? pr.asset_id.slice(0, 8) + "…"}
  </p>
  {assetNameMap[pr.asset_id]?.description && (
    <p className="text-xs text-slate-400 line-clamp-1 mt-0.5">
      {assetNameMap[pr.asset_id].description}
    </p>
  )}
  {assetNameMap[pr.asset_id]?.team && (
    <p className="text-xs text-slate-400">Team: {assetNameMap[pr.asset_id].team}</p>
  )}
</td>
```

Update the column header from "Asset ID" to "Asset".

Also add a "dependency_declaration" detail row (expandable) showing tool names the agent depends on,
read from `pr.dependency_declaration` (already in the response).

Note: `listAgents` signature is `listAgents(limit, offset, status)`. Add a third optional `status`
parameter to the function in `registryApi.ts` if not already there (it currently passes
`status="active"` as the default — confirm this and keep it).

---

### A4 — Separate Promote from Grant (two-step flow)

**Problem:** `POST /admin/publish-requests/{id}/approve` currently requires `grantee_teams` in the
body AND auto-creates grants. The desired flow is:
1. Admin clicks "Promote" → artifact `publish_status` flips to `published`, no grant created
2. Admin then goes to Access Control to explicitly grant to teams

**Backend change required (minor):**

**File:** `services/registry-api/schemas.py`

```python
# Before
class PublishRequestApprove(BaseModel):
    grantee_teams: list[str]
    expires_at: Optional[datetime] = None

# After
class PublishRequestApprove(BaseModel):
    grantee_teams: list[str] = Field(default_factory=list)
    expires_at: Optional[datetime] = None
```

**File:** `services/registry-api/routers/admin.py` — `approve_publish_request` function

The grant-creation loop already reads `body.grantee_teams`:
```python
for team in body.grantee_teams:   # already a loop — if list is empty, no grants created
    ...
```
This means **no logic change is needed** in the router — making `grantee_teams` optional with
`default_factory=list` is sufficient. If the list is empty, the loop runs zero iterations.

**Frontend change:**

**File:** `studio/src/pages/AdminPublishRequestsPage.tsx`

- Remove the "Grantee teams (comma-separated)" input from the inline approve form
- Replace with a single "Promote to Catalog" button that calls `approvePublishRequest(id, { grantee_teams: [] })`
- After success, show a toast: "Promoted to catalog. Go to Access Control to grant team access."
- Add a link in the toast or below the promoted row: "→ Go to Access Control"
- Rename the column action from "Approve" to "Promote"

**File:** `studio/src/api/registryApi.ts` — `approvePublishRequest` function

Change the call signature so `grantee_teams` is optional:
```typescript
export const approvePublishRequest = async (
  id: string,
  body: { grantee_teams?: string[]; expires_at?: string } = {}
): Promise<{ approved: boolean; grants_created: number }> => { ... }
```

This is a backend schema change → bump `registry-api` to `0.2.26` when deploying.

---

### A5 — Admin: All Artifacts view (read-only)

**New file:** `studio/src/pages/AdminArtifactsPage.tsx`
**New route:** `/admin/artifacts` (add to `App.tsx`)

This page shows every agent, tool, skill, and workflow across all teams. No create/edit/delete
buttons — pure read-only audit view for admins.

**Data sources (all existing endpoints):**
```typescript
GET /api/v1/agents/?limit=200           // all statuses (no status filter)
GET /api/v1/tools/?limit=200
GET /api/v1/skills/?limit=200
GET /api/v1/workflows/
```

**Unified row shape:**
```typescript
interface ArtifactRow {
  id: string;
  name: string;
  type: "agent" | "tool" | "skill" | "workflow";
  team: string;
  status: string;             // active / deprecated / archived / quarantined (agents only)
  publish_status?: string;    // private / pending_review / published (agents only)
  risk_level?: string;        // tools only
  created_at: string;
  description?: string;
}
```

**UI layout:**
- Header: "All Artifacts" + subtitle "Read-only view of every registered artifact"
- Filter bar: type chips (All / Agents / Tools / Skills / Workflows) + status dropdown
- Table columns: Name | Type | Team | Status | Publish Status | Created
- Status badges use color coding:
  - `active` → green, `deprecated` → amber, `quarantined` → red
  - `published` → green, `pending_review` → amber, `private` → slate
- Row click: navigate to the artifact detail page (agent detail, tool detail, etc.)
  - Only agents have a detail page (`/agents/:name`). Others: no-op or modal.
- No action buttons of any kind — this is view-only

**Note:** `listAgents` must be called without a status filter here to show all statuses. Override
the default: `listAgents(200, 0, undefined)` (pass `undefined` for status param, so no filter
is applied to the query string).

---

### A6 — Catalog: Add Chat + Deploy buttons on agent cards

**File:** `studio/src/pages/CatalogPage.tsx`

In `CatalogCard`, for entries with `type === "agent"` and `grantedTo.length > 0`:

```typescript
// Add to CatalogCard component
{entry.type === "agent" && entry.grantedTo.length > 0 && (
  <div className="flex gap-2 mt-2">
    <Link
      to={`/agents/${entry.name}/chat`}
      className="btn-primary text-xs py-1.5 px-3 flex items-center gap-1"
    >
      <MessageSquare size={11} /> Chat
    </Link>
    <Link
      to={`/agents/${entry.name}/deploy`}
      className="btn-secondary text-xs py-1.5 px-3 flex items-center gap-1"
    >
      <Rocket size={11} /> Deploy
    </Link>
  </div>
)}
```

Import `Link` from `react-router-dom`, `MessageSquare` and `Rocket` from `lucide-react`.

The `CatalogPage` currently builds `grantedTo` from `teams-summary`. The Catalog should also
filter to only show agents with `publish_status === "published"`. Add a `publish_status` field
to `CatalogEntry` and filter entries. Since `listAgents()` returns `AgentResponse` which includes
`publish_status`, propagate it through:

```typescript
interface CatalogEntry {
  // ... existing fields
  publish_status?: string;  // add this
}

// In entries construction:
...(agentsPage?.items ?? []).map((a) => ({
  ...
  publish_status: a.publish_status,
}))

// Filter: only show published agents in the shared section
const shared = entries.filter(
  (e) => e.grantedTo.length > 0 && (e.type !== "agent" || e.publish_status === "published")
);
```

---

### A7 — Deployments page

**New file:** `studio/src/pages/DeploymentsPage.tsx`
**New route:** `/deployments` (add to `App.tsx`)

**Data source:** `GET /api/v1/deployments/?limit=100` (existing endpoint in `deployments.py`)
`DeploymentResponse` includes: `id`, `agent_id`, `agent_name`, `status`, `deployed_at`,
`error_message`, `version_id`, and the join-added `agent_name`.

Add to `registryApi.ts`:
```typescript
export const listAllDeployments = async (
  status?: string,
  limit = 100
): Promise<Paginated<Deployment>> => {
  const { data } = await http.get<Paginated<Deployment>>("/deployments/", {
    params: { limit, ...(status ? { status } : {}) },
  });
  return data;
};
```

**UI layout:**
- Header: "Deployments" + subtitle "Live agent deployments across the platform"
- Status filter tabs: All | Running | Deploying | Failed | Terminated
- Table columns: Agent Name | Status | Version | Deployed At | Error
- "Running" rows highlighted with green left border
- Each row links to `/agents/:name` (agent detail page)
- "Chat" button on running rows that navigates to `/agents/:name/chat`
- Auto-refresh every 30s (use `refetchInterval: 30_000` in `useQuery`)

---

### A8 — Sidebar restructure

**File:** `studio/src/components/Sidebar.tsx`

**New nav structure:**

```typescript
const PLAYGROUND_BUILD = [
  { label: "Agents",    to: "/",          end: true },
  { label: "Skills",    to: "/skills",    end: false },
  { label: "Tools",     to: "/tools",     end: false },
  { label: "Workflows", to: "/workflows", end: false },
];

const PLAYGROUND_EVAL = [
  { label: "Evaluate", to: "/playground",         end: true },   // renamed
  { label: "Datasets", to: "/playground/datasets", end: false },
];

const ORG_ITEMS = [
  { label: "Catalog",     to: "/catalog"      },
  { label: "Deployments", to: "/deployments"  },
];

const MY_AGENT_SECTION = true;  // dynamic: driven by useAuth team + grants

const ADMIN_ITEMS = [
  { label: "All Artifacts",  to: "/admin/artifacts"          },  // new
  { label: "Publish Queue",  to: "/admin/publish-requests"   },
  { label: "Access Control", to: "/admin/access"             },
  { label: "HITL Queue",     to: "/hitl"                     },
  { label: "Approvers",      to: "/admin/approval-authority" },
];
```

**Rendered sidebar structure:**
```
[AS] AgentShield Studio
─────────────────────────
PLAYGROUND  ▾
  Agents
  Skills
  Tools
  Workflows
  ──────────
  Evaluate
  Datasets

MY AGENTS   ▾            ← new section (Phase B — placeholder in Phase A)
  (Loading… until Phase B)

ORG
  Catalog
  Deployments            ← new

CONFIG
  Providers

ADMINISTRATION  ▾
  All Artifacts          ← new
  Publish Queue
  Access Control
  HITL Queue
  Approvers
─────────────────────────
[user avatar] Name  [logout]
```

Update `isPlaygroundRoute()` and `isAdminRoute()` helpers. Add `isOrgRoute()`:
```typescript
function isOrgRoute(pathname: string) {
  return pathname.startsWith("/catalog") || pathname.startsWith("/deployments");
}
```

The "My Agents" section in Phase A is a collapsible placeholder that shows "Coming soon" or
a grayed-out state. Wire it fully in Phase B.

---

## 5. Phase B — Consumer Chat

This phase adds the consumer-facing chat experience. It requires both backend (registry-api)
and frontend (studio) changes.

**Backend image:** `registry-api:0.2.26` (or `0.2.27` if A4 backend change shipped first as `0.2.26`)
**Studio image:** `0.1.26` (or next available after Phase A)

---

### B1 — Chat Proxy API

**New file:** `services/registry-api/routers/chat.py`

**Endpoint:** `POST /api/v1/agents/{name}/chat`

This endpoint:
1. Verifies the caller's JWT (require authenticated user)
2. Looks up the caller's team from `user_team_assignments`
3. Checks that the caller's team has an active grant on the agent
4. Verifies the agent has a running deployment
5. Forwards the message to the deployed agent's K8s service OR falls back to the playground runner
6. Streams the response back as SSE

**Request schema (new in `schemas.py`):**
```python
class AgentChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None   # for conversation continuity; client generates UUID

class AgentChatResponse(BaseModel):
    run_id: str
    session_id: str
    stream_url: str   # relative: /api/v1/agents/{name}/chat/{run_id}/stream
```

**Full implementation:**

```python
# services/registry-api/routers/chat.py
"""
Consumer chat proxy — grant-checked, streamed.

POST /api/v1/agents/{name}/chat       → start a chat turn
GET  /api/v1/agents/{name}/chat/{run_id}/stream → SSE stream
"""
from __future__ import annotations
import uuid, logging
from datetime import datetime, timezone
from typing import Optional, AsyncIterator
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware import require_user
from db import get_db
from models import Agent, AssetGrant, Deployment, PlaygroundRun
from schemas import AgentChatRequest

router = APIRouter(prefix="/api/v1/agents", tags=["consumer-chat"])
logger = logging.getLogger(__name__)


async def _caller_team(db: AsyncSession, user_sub: str) -> Optional[str]:
    """Resolve the caller's team from user_team_assignments."""
    row = await db.execute(
        text("SELECT team_name FROM user_team_assignments WHERE user_sub = :sub"),
        {"sub": user_sub},
    )
    r = row.first()
    return r.team_name if r else None


async def _check_grant(db: AsyncSession, agent: Agent, caller_team: str) -> bool:
    """Return True if caller_team has an active, non-expired grant on this agent."""
    row = await db.execute(
        select(AssetGrant).where(
            AssetGrant.asset_id == agent.id,
            AssetGrant.asset_type == "agent",
            AssetGrant.grantee_team == caller_team,
            AssetGrant.revoked_at.is_(None),
        ).limit(1)
    )
    grant = row.scalar_one_or_none()
    if grant is None:
        return False
    if grant.expires_at and grant.expires_at < datetime.now(tz=timezone.utc):
        return False
    return True


async def _latest_running_deployment(db: AsyncSession, agent_id: uuid.UUID) -> Optional[Deployment]:
    row = await db.execute(
        select(Deployment)
        .where(Deployment.agent_id == agent_id, Deployment.status == "running")
        .order_by(Deployment.deployed_at.desc())
        .limit(1)
    )
    return row.scalar_one_or_none()


@router.post("/{name}/chat", status_code=status.HTTP_200_OK)
async def start_chat(
    name: str,
    body: AgentChatRequest,
    caller: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Grant-checked consumer chat endpoint.

    Flow:
      1. Resolve agent by name
      2. Resolve caller's team from user_team_assignments
      3. Check active grant exists
      4. Verify a running deployment exists
      5. Create a PlaygroundRun record with context='production', sandbox=False
      6. Return run_id + stream_url (same SSE pattern as playground)
    """
    # 1. Agent lookup
    result = await db.execute(select(Agent).where(Agent.name == name, Agent.status == "active"))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found.")

    # 2. Caller team
    user_sub = caller.get("sub", "")
    caller_team = await _caller_team(db, user_sub)
    
    # Agent owner's team always has implicit access (no grant needed for own agent)
    if caller_team != agent.team:
        if not caller_team:
            raise HTTPException(status_code=403, detail="User is not assigned to any team.")
        # 3. Grant check
        if not await _check_grant(db, agent, caller_team):
            raise HTTPException(
                status_code=403,
                detail=f"Team '{caller_team}' does not have access to agent '{name}'.",
            )

    # 4. Running deployment check
    deployment = await _latest_running_deployment(db, agent.id)
    if not deployment:
        raise HTTPException(
            status_code=503,
            detail=f"Agent '{name}' has no running deployment. Deploy it first.",
        )

    # 5. Create run record (reuses PlaygroundRun table with context='production', sandbox=False)
    session_id = body.session_id or str(uuid.uuid4())
    now = datetime.now(tz=timezone.utc)
    run = PlaygroundRun(
        user_id=user_sub,
        agent_name=name,
        context="production",    # distinguishes from dev playground runs
        sandbox=False,
        input_message=body.message,
        status="running",
        started_at=now,
    )
    db.add(run)
    await db.flush()
    run_id = str(run.id)
    await db.commit()

    logger.info("chat: run_id=%s agent=%s user=%s team=%s", run_id, name, user_sub, caller_team)

    # 6. Return stream URL
    return {
        "run_id": run_id,
        "session_id": session_id,
        "stream_url": f"/api/v1/agents/{name}/chat/{run_id}/stream",
        "agent_name": name,
        "deployment_id": str(deployment.id),
    }
```

**SSE streaming endpoint:**

```python
@router.get("/{name}/chat/{run_id}/stream")
async def stream_chat(
    name: str,
    run_id: str,
    caller: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """
    Stream the chat response as SSE.

    Short-term: reuse the same simulated SSE mechanism as playground runs
    (`routers/playground.py:stream_playground_run`). Long-term: proxy to the
    deployed agent's K8s service at:
      http://{name}.agents-{team}.svc.cluster.local:8000/invoke
    """
    parsed_id = uuid.UUID(run_id)
    result = await db.execute(select(PlaygroundRun).where(PlaygroundRun.id == parsed_id))
    run = result.scalar_one_or_none()
    if not run or run.agent_name != name:
        raise HTTPException(status_code=404, detail="Chat run not found.")
    if run.user_id != caller.get("sub", ""):
        raise HTTPException(status_code=403, detail="Not your chat run.")

    # Reuse the same SSE generator as playground stream
    # Import the generator from playground.py or duplicate the pattern here
    from routers.playground import _sse_generator  # if extracted as a shared helper
    return StreamingResponse(
        _sse_generator(run.input_message, run_id, db),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

**Note on `_sse_generator`:** The playground router currently has inline SSE generation. Before
implementing the stream endpoint, extract the SSE generation function from `playground.py` into
a shared helper (e.g., `routers/_sse.py`) so both the playground and chat endpoints can use it.
If extraction is too risky, duplicate the pattern for now and refactor later.

**Wire into `main.py`:**
```python
from routers.chat import router as chat_router
app.include_router(chat_router)
```

---

### B2 — My Agents page

**New file:** `studio/src/pages/MyAgentsPage.tsx`
**Route:** `/my-agents`

**Data sources:**
1. `GET /api/v1/admin/teams-summary` → find the current user's team's grants
2. `GET /api/v1/agents/?status=active` → full agent list
3. `GET /api/v1/deployments/?status=running` → which agents are currently running

**Logic:**
1. From `useAuth()`, get `user.preferred_username` (or `sub`)
2. From teams-summary, find which team the current user belongs to (match by `user_sub`)
3. Get that team's `grants` array → list of `asset_name` values for `asset_type=agent`
4. Filter the agent list to only agents whose `name` appears in grants
5. Cross-reference with deployments to show "Running" or "Not deployed" per agent

```typescript
// In MyAgentsPage.tsx
const { user } = useAuth();

// Teams summary gives us team membership and grants
const { data: teams } = useQuery({
  queryKey: ["teams-summary"],
  queryFn: fetchTeamsSummary,
});

// Find my team's grants
const myTeam = teams?.find(t =>
  t.members.some(m => m.user_sub === user?.sub)
);
const myGrantedAgentNames = new Set(
  (myTeam?.grants ?? [])
    .filter(g => g.asset_type === "agent")
    .map(g => g.asset_name)
);

// Filter agents to granted ones
const myAgents = (agentsPage?.items ?? []).filter(a => myGrantedAgentNames.has(a.name));
```

**UI layout:**
```
My Agents
"Agents your team (operations) has been granted access to"

┌──────────────────────────────────────────────────┐
│ Customer Intelligence Agent           [Running ●] │
│ Provides 360° customer brief                      │
│ Team: operations                                  │
│                          [Chat]  [View Details]   │
└──────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────┐
│ IP Threat Intelligence                [Not deployed]│
│ Security analysis for IPs                         │
│ Team: platform                                    │
│                          [Deploy]  [View Details] │
└──────────────────────────────────────────────────┘
```

- If the agent has a running deployment: show "Chat" button → `/agents/:name/chat`
- If not deployed: show "Deploy" button → `/agents/:name/deploy`
- "View Details" → `/agents/:name`
- "Running" badge: green dot + "Running" text
- "Not deployed" badge: grey text

Wire the "My Agents" sidebar section in `Sidebar.tsx` to show agent names dynamically
(limit to first 5, with a "See all" link to `/my-agents`):

```typescript
// In Sidebar.tsx — dynamic My Agents section
// Fetch granted agents for the sidebar mini-list
const { data: myAgentsList } = useQuery({
  queryKey: ["my-agents-sidebar"],
  queryFn: fetchMyAgents,   // same logic as MyAgentsPage
  staleTime: 60_000,
});
```

---

### B3 — Agent Chat page (clean consumer UI)

**New file:** `studio/src/pages/AgentChatPage.tsx`
**Route:** `/agents/:name/chat`

This is a clean chat UI — no trace panel, no version selector, no eval buttons, no sandbox badge.
It is the consumer interface. Developer evaluation is at `/playground`.

**API calls:**
```typescript
// Start a chat turn
POST /api/v1/agents/{name}/chat
Body: { message: string, session_id: string }
Response: { run_id, session_id, stream_url }

// Stream the response
GET /api/v1/agents/{name}/chat/{run_id}/stream
Response: text/event-stream
```

**SSE event format** (same as playground, reused):
```
data: {"type":"token","content":"Hello"}
data: {"type":"token","content":", how"}
data: {"type":"done","run_id":"..."}
```

**Component structure:**

```typescript
export default function AgentChatPage() {
  const { name } = useParams<{ name: string }>();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sessionId] = useState(() => crypto.randomUUID());
  const [isStreaming, setIsStreaming] = useState(false);
  const { data: agent } = useQuery({ queryKey: ["agent", name], queryFn: () => getAgent(name!) });

  const sendMessage = async () => {
    if (!input.trim() || isStreaming) return;
    const userMsg = input.trim();
    setInput("");
    setMessages(prev => [...prev, { role: "user", content: userMsg }]);
    setIsStreaming(true);

    // POST to start chat turn
    const { run_id, stream_url } = await startAgentChat(name!, { message: userMsg, session_id: sessionId });

    // Open SSE stream
    const evtSource = new EventSource(`/api/v1${stream_url.replace(/^\/api\/v1/, "")}`);
    let botContent = "";
    setMessages(prev => [...prev, { role: "assistant", content: "" }]);

    evtSource.onmessage = (e) => {
      const ev = JSON.parse(e.data);
      if (ev.type === "token") {
        botContent += ev.content;
        setMessages(prev => {
          const updated = [...prev];
          updated[updated.length - 1] = { role: "assistant", content: botContent };
          return updated;
        });
      }
      if (ev.type === "done") {
        evtSource.close();
        setIsStreaming(false);
      }
    };
    evtSource.onerror = () => { evtSource.close(); setIsStreaming(false); };
  };

  return (
    <div className="flex flex-col h-screen bg-white">
      {/* Header — clean, minimal */}
      <div className="border-b border-slate-200 px-6 py-4 flex items-center gap-3">
        <Link to="/my-agents" className="text-slate-400 hover:text-slate-600">
          <ArrowLeft size={16} />
        </Link>
        <div>
          <h1 className="text-base font-semibold text-slate-900">{agent?.name ?? name}</h1>
          <p className="text-xs text-slate-400 line-clamp-1">{agent?.description}</p>
        </div>
        <span className="ml-auto badge bg-green-100 text-green-700 text-xs">Live</span>
      </div>

      {/* Message thread */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <Bot size={36} className="text-slate-300 mb-3" />
            <p className="text-slate-500 font-medium">Start a conversation</p>
            <p className="text-slate-400 text-sm mt-1">{agent?.description}</p>
          </div>
        )}
        {messages.map((m, i) => (
          <MessageBubble key={i} role={m.role} content={m.content} />
        ))}
      </div>

      {/* Input bar */}
      <div className="border-t border-slate-200 px-6 py-4">
        <div className="flex gap-3">
          <input
            className="input flex-1"
            placeholder="Message…"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === "Enter" && !e.shiftKey && sendMessage()}
            disabled={isStreaming}
          />
          <button onClick={sendMessage} disabled={isStreaming || !input.trim()} className="btn-primary">
            {isStreaming ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
          </button>
        </div>
      </div>
    </div>
  );
}
```

**Add to `registryApi.ts`:**
```typescript
export const startAgentChat = async (
  name: string,
  body: { message: string; session_id?: string }
): Promise<{ run_id: string; session_id: string; stream_url: string; agent_name: string }> => {
  const { data } = await http.post(`/agents/${name}/chat`, body);
  return data;
};
```

---

### B4 — Wire Phase B into sidebar and App.tsx

**`App.tsx` additions:**
```typescript
import MyAgentsPage from "./pages/MyAgentsPage";
import AgentChatPage from "./pages/AgentChatPage";
import DeploymentsPage from "./pages/DeploymentsPage";
import AdminArtifactsPage from "./pages/AdminArtifactsPage";

// Add routes:
<Route path="/my-agents" element={<MyAgentsPage />} />
<Route path="/agents/:name/chat" element={<AgentChatPage />} />
<Route path="/deployments" element={<DeploymentsPage />} />
<Route path="/admin/artifacts" element={<AdminArtifactsPage />} />
```

**`Sidebar.tsx` — wire My Agents section:**
The "My Agents" section in the sidebar (added as placeholder in A8) is now fully driven by
the grants query. Show up to 5 agent names with Chat buttons. If > 5, show "+ N more" link
to `/my-agents`. If the user's team has no grants, hide the section or show empty state.

---

## 6. Phase C — E2E Test Coverage

**New file:** `scripts/e2e/suite-14-consumer-chat.sh`

This suite tests all new backend API contracts. Runs inside the registry-api pod via
`kubectl exec` (same pattern as all other suites).

### Test Cases

**T-S14-001: Chat endpoint requires authentication**
```bash
# Without Authorization header → 401
STATUS=$(kubectl exec ... -- curl -s -o /dev/null -w "%{http_code}" \
  -X POST http://localhost:8000/api/v1/agents/customer-intelligence-agent/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"hello"}')
[ "$STATUS" = "401" ] && pass || fail
```

**T-S14-002: Chat blocked when no grant exists**
```bash
# With valid token for a user whose team has no grant → 403
# Requires: create a test user 'chat-test-user' assigned to team 'no-grant-team'
# and get their JWT (or mock via X-User-Sub header in dev mode)
```

**T-S14-003: Chat succeeds when grant exists and agent is running**
```bash
# With valid token for operations team member, granted access to customer-intelligence-agent
# Requires: agent deployed and running
# Expects: 200 with { run_id, session_id, stream_url }
```

**T-S14-004: Agent owner team always has chat access (no grant required)**
```bash
# Agent owned by 'platform' team; caller is in 'platform' team → 200 even without explicit grant
```

**T-S14-005: Chat returns 503 when agent has no running deployment**
```bash
# Agent exists but no running deployment → 503
STATUS=$(... POST /api/v1/agents/undeployed-agent/chat ...)
[ "$STATUS" = "503" ] && pass || fail
```

**T-S14-006: `GET /api/v1/deployments/?status=running` only returns running**
```bash
STATUS_LIST=$(kubectl exec ... -- python3 -c "
import httpx, json
resp = httpx.get('http://localhost:8000/api/v1/deployments/?status=running')
data = resp.json()
statuses = [d['status'] for d in data.get('items', [])]
assert all(s == 'running' for s in statuses), f'Found non-running: {statuses}'
print('ok')
" 2>&1)
[[ "$STATUS_LIST" == "ok" ]] && pass || fail
```

**T-S14-007: Approve publish request without grantee_teams → 0 grants created, publish_status=published**
```bash
# Create a test agent and publish request
# Call approve with empty grantee_teams
# Verify: grants_created=0, agent.publish_status='published'
```

**T-S14-008: Publish request list response includes asset_name (not just UUID)**
```bash
# Requires a publish request to exist
STATUS=$(kubectl exec ... -- python3 -c "
import httpx
resp = httpx.get('http://localhost:8000/api/v1/admin/publish-requests')
items = resp.json().get('items', [])
# Verify no item relies only on asset_id — each should have asset_name OR we handle it client-side
# Since enrichment is client-side (Phase A3), this test verifies the underlying asset is queryable
print('ok')
")
```
Note: since A3 enrichment is done client-side (frontend fetches assets and maps by ID), T-S14-008
is an integration test confirming the asset list endpoints return UUIDs that match publish request
`asset_id` values.

**T-S14-009: HITL production queue excludes playground approvals**
```bash
# This is already tested in suite-8 T-S8-006. Add a cross-reference assertion here.
# Verify: GET /approvals/?status=pending only returns context='production' items
STATUS=$(kubectl exec ... -- python3 -c "
import httpx
resp = httpx.get('http://localhost:8000/api/v1/approvals/?status=pending')
items = resp.json().get('items', [])
playground_items = [i for i in items if i.get('context') == 'playground']
assert len(playground_items) == 0, f'Found playground items in production queue: {playground_items}'
print('ok')
" 2>&1)
```

**T-S14-010: SSE stream returns valid events for a chat run**
```bash
# POST /agents/{name}/chat → get stream_url
# GET stream_url → verify Content-Type: text/event-stream and at least one data: line
```

### Suite structure

```bash
#!/usr/bin/env bash
# scripts/e2e/suite-14-consumer-chat.sh
#
# E2E Suite 14: Consumer Chat (Phase B)
# Tests T-S14-001 through T-S14-010.
#
# What this proves:
#   T-S14-001 — Chat endpoint returns 401 without auth token
#   T-S14-002 — Chat returns 403 when caller team has no grant
#   T-S14-003 — Chat returns 200 when grant exists + agent running
#   T-S14-004 — Agent owner team has implicit chat access
#   T-S14-005 — Chat returns 503 when agent has no running deployment
#   T-S14-006 — GET /deployments/?status=running returns only running deployments
#   T-S14-007 — Approve without grantee_teams creates 0 grants, sets publish_status=published
#   T-S14-008 — Asset IDs in publish requests resolve to real assets in list endpoints
#   T-S14-009 — Production HITL queue excludes playground approvals (regression guard)
#   T-S14-010 — SSE stream returns valid event-stream Content-Type and data events
```

### Update `run-all.sh`

Add suite-14 to the master runner:
```bash
run_suite "Suite 14: Consumer Chat" "suite-14-consumer-chat.sh"
```

---

## 7. Implementation Order and Dependencies

```
Phase A (no backend changes except A4)
  A1 → A2 → A3 → A4 (backend: schemas.py only) → A5 → A6 → A7 → A8
  
  A1–A3: independent, any order
  A4 requires: schemas.py change (grantee_teams optional) → rebuild registry-api:0.2.26
  A5–A8: independent of each other; A8 depends on A5 (needs /admin/artifacts route)

Phase B
  B1 (chat router) must ship before B3 (chat page needs the endpoint)
  B2 (My Agents page) is independent of B1/B3
  B4 (wiring) depends on B1+B2+B3 being done

Phase C
  Can write T-S14-001 through T-S14-005 as soon as B1 is deployed
  T-S14-006 through T-S14-009 are independent of Phase B
  T-S14-010 depends on B1 SSE endpoint
```

---

## 8. Image Version Bumps

| Change | Service | Old Tag | New Tag |
|---|---|---|---|
| A4: make grantee_teams optional | registry-api | 0.2.25 | 0.2.26 |
| A1–A3, A5–A8: Studio UX | studio | 0.1.24 | 0.1.25 |
| B1: chat proxy router | registry-api | 0.2.26 | 0.2.27 |
| B2–B4: My Agents, Chat page, Deployments page | studio | 0.1.25 | 0.1.26 |

Always update `scripts/deploy-cpe2e.sh` with the new tags before building.

---

## 9. Acceptance Criteria

### Phase A — all manual verification in Studio UI

- [ ] Sidebar shows "Evaluate" not "Test"
- [ ] HITL Queue page header says "Production HITL Queue"
- [ ] Publish Queue rows show agent name + description (not raw UUID)
- [ ] Clicking "Promote" in Publish Queue approves with no grant creation
- [ ] Toast after promote links to Access Control
- [ ] Admin Artifacts page shows all agents/tools/skills/workflows with status badges
- [ ] Catalog agent cards show "Chat" and "Deploy" buttons for granted+published agents
- [ ] Deployments page shows running agents with status badges, auto-refreshes
- [ ] Sidebar shows All Artifacts and Deployments links

### Phase B — manual + API verification

- [ ] `POST /api/v1/agents/{name}/chat` returns 401 without token
- [ ] Returns 403 when team has no grant
- [ ] Returns 200 with `run_id` and `stream_url` when grant exists
- [ ] Returns 503 when agent has no running deployment
- [ ] My Agents page shows only agents granted to current user's team
- [ ] "Running" badge visible on agents with a running deployment
- [ ] Chat button navigates to AgentChatPage
- [ ] AgentChatPage sends message, streams response, shows conversation thread
- [ ] No trace panel / version selector / eval buttons visible on AgentChatPage
- [ ] Sidebar My Agents section shows granted agent names (up to 5)

### Phase C — automated

- [ ] `suite-14-consumer-chat.sh` passes all T-S14-001 through T-S14-010
- [ ] `run-all.sh` includes suite-14 and passes
- [ ] Suite-8 T-S8-006 HITL context isolation still passes (regression)

---

## 10. Files Changed Summary

### New files
```
studio/src/pages/AdminArtifactsPage.tsx
studio/src/pages/DeploymentsPage.tsx
studio/src/pages/MyAgentsPage.tsx
studio/src/pages/AgentChatPage.tsx
services/registry-api/routers/chat.py
scripts/e2e/suite-14-consumer-chat.sh
```

### Modified files
```
studio/src/components/Sidebar.tsx          (A1, A8, B4)
studio/src/pages/PlaygroundPage.tsx        (A1 label update)
studio/src/pages/HITLDashboardPage.tsx     (A2)
studio/src/pages/AdminPublishRequestsPage.tsx (A3, A4)
studio/src/pages/CatalogPage.tsx           (A6)
studio/src/App.tsx                         (A5, A7, A8, B4 — new routes)
studio/src/api/registryApi.ts              (A3 listAgents status param, A4 approvePublishRequest, B3 startAgentChat, A7 listAllDeployments)
services/registry-api/schemas.py           (A4: grantee_teams optional)
services/registry-api/main.py              (B1: include chat router)
scripts/deploy-cpe2e.sh                    (image tag bumps)
scripts/e2e/run-all.sh                     (add suite-14)
```
