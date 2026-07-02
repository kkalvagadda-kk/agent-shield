# AgentShield UX Phases A-B-C — Tasks

**Spec:** `docs/plan/plan-phases-abc.md` (authoritative — takes precedence over all other docs)
**Images:** registry-api:0.2.26 (A4 backend), studio:0.1.25 (Phase A), registry-api:0.2.27 (B1), studio:0.1.26 (Phase B)

---

## Phase A: Studio UX Gaps
> Frontend-only except A4 (one `schemas.py` field change). No new DB tables.

### A1 — Rename "Test" → "Evaluate"

- [X] `studio/src/components/Sidebar.tsx`: in `PLAYGROUND_TEST` array, change `label: "Test"` → `label: "Evaluate"`
- [X] `studio/src/pages/PlaygroundPage.tsx`: change `<h2>` heading `"Playground"` → `"Evaluate"` (line ~51)

**Verify:** `cd studio && npm run typecheck`. Sidebar shows "Evaluate".

---

### A2 — HITL Dashboard: production context only

- [X] `studio/src/pages/HITLDashboardPage.tsx`: change page `<h1>` to `"Production HITL Queue"`
- [X] Change subtitle to `"Human-in-the-loop approval requests from deployed agents. Playground approvals are handled inline in the Evaluate tab."`
- [X] Remove `playground: "bg-purple-100 text-purple-700"` from `CONTEXT_CHIP` — it will never appear
- [X] Add info callout below header (before filter controls):
  ```tsx
  <div className="rounded-md bg-blue-50 border border-blue-200 px-4 py-2 text-xs text-blue-700 mb-4">
    Showing production approvals only. Sandbox approvals appear in the Evaluate tab during testing.
  </div>
  ```

**Verify:** `npm run typecheck`. Page title updated, no TS errors from removed CONTEXT_CHIP key.

---

### A3 — Publish Queue: show asset name + description instead of raw UUID

- [X] `studio/src/api/registryApi.ts`: update `listAgents` to pass `?status=` only when defined (not always):
  ```typescript
  params: { limit, offset, ...(status !== undefined ? { status } : {}) }
  ```
  Change the third param from `status = "active"` to `status?: string` (no default).
- [X] `studio/src/api/registryApi.ts`: update `listTools` signature to accept `(limit, offset, params?)`:
  ```typescript
  export const listTools = async (
    limit = 100, offset = 0, params?: { team?: string }
  ): Promise<Paginated<RegistryTool>> => {
    const { data } = await http.get<Paginated<RegistryTool>>('/tools/', {
      params: { limit, offset, ...params },
    });
    return data;
  };
  ```
  Update any existing callers that passed `{ team: "..." }` to use `listTools(100, 0, { team: "..." })`.
- [X] `studio/src/api/registryApi.ts`: update `listSkills` signature to accept `(limit, offset, params?)`:
  ```typescript
  export const listSkills = async (
    limit = 100, offset = 0, params?: { team?: string }
  ): Promise<Paginated<Skill>> => {
    const { data } = await http.get<Paginated<Skill>>('/skills/', {
      params: { limit, offset, ...params },
    });
    return data;
  };
  ```
- [X] `studio/src/pages/AdminPublishRequestsPage.tsx`: add four parallel asset queries (after existing `useQuery` for publish-requests):
  ```typescript
  const { data: agentsPage }    = useQuery({ queryKey: ["pq-agents"],    queryFn: () => listAgents(200, 0, "active") });
  const { data: toolsPage }     = useQuery({ queryKey: ["pq-tools"],     queryFn: () => listTools(200, 0) });
  const { data: skillsPage }    = useQuery({ queryKey: ["pq-skills"],    queryFn: () => listSkills(200, 0) });
  const { data: workflowsList } = useQuery({ queryKey: ["pq-workflows"], queryFn: () => listWorkflows() });
  ```
- [X] Build name map with `useMemo`:
  ```typescript
  const assetNameMap = useMemo(() => {
    const m: Record<string, { name: string; description?: string | null; team?: string | null }> = {};
    for (const a of agentsPage?.items ?? [])  m[String(a.id ?? a.name)] = { name: a.name, description: a.description, team: a.team };
    for (const t of toolsPage?.items ?? [])   m[String(t.id)]           = { name: t.name, description: t.description, team: t.owner_team };
    for (const s of skillsPage?.items ?? [])  m[String(s.id)]           = { name: s.name, description: s.description, team: s.team };
    for (const w of workflowsList ?? [])      m[String(w.id)]           = { name: w.name, description: w.description, team: w.team };
    return m;
  }, [agentsPage, toolsPage, skillsPage, workflowsList]);
  ```
- [X] Change table column header `"Asset ID"` → `"Asset"`
- [X] Replace UUID cell with enriched cell:
  ```tsx
  <td className="px-4 py-3">
    <p className="text-sm font-medium text-slate-800">
      {assetNameMap[pr.asset_id]?.name ?? `${pr.asset_id.slice(0, 8)}…`}
    </p>
    {assetNameMap[pr.asset_id]?.description && (
      <p className="text-xs text-slate-400 line-clamp-1 mt-0.5">
        {assetNameMap[pr.asset_id]!.description}
      </p>
    )}
    {assetNameMap[pr.asset_id]?.team && (
      <p className="text-xs text-slate-400">
        Team: <span className="font-medium">{assetNameMap[pr.asset_id]!.team}</span>
      </p>
    )}
  </td>
  ```
- [X] Add `useMemo` to React import if not present

**Verify:** `npm run typecheck`. Publish Queue shows `"customer-intelligence-agent"` not `"14991bdf…"`.

---

### A4 — Separate Promote from Grant

**Backend (`registry-api`):**
- [X] `services/registry-api/schemas.py` line ~664: make `grantee_teams` optional:
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
  No router change needed — `for team in body.grantee_teams:` already handles empty list.

**Frontend (`studio`):**
- [X] `studio/src/api/registryApi.ts`: update `approvePublishRequest` body type — `grantee_teams` optional:
  ```typescript
  export const approvePublishRequest = async (
    id: string,
    body: { grantee_teams?: string[]; expires_at?: string } = {}
  ): Promise<{ approved: boolean; grants_created: number }> => { ... }
  ```
- [X] `studio/src/pages/AdminPublishRequestsPage.tsx`:
  - Remove `teamsInput` state and its `<input>` field from the inline approve form
  - Remove `teamsInput.split(...)` logic from `handleApprove`
  - Change `handleApprove` call to: `approveMutation.mutate({ id: pr.id, teams: [] })`
  - Rename expand button in Actions column: `"Approve"` → `"Promote"`
  - Rename `"Confirm Approve"` submit button → `"Promote to Catalog"`
  - Update `onSuccess` toast: `toast.success("Promoted to catalog. Go to Access Control to grant team access.")`

**Verify:** Build and deploy registry-api:0.2.26. `curl -X POST .../approve -d '{}'` returns `{ approved: true, grants_created: 0 }`. Agent `publish_status` = `published`.

---

### A5 — Admin: All Artifacts view (read-only) [P]

- [X] Create `studio/src/pages/AdminArtifactsPage.tsx`
- [X] Define type:
  ```typescript
  interface ArtifactRow {
    id: string; name: string;
    type: "agent" | "tool" | "skill" | "workflow";
    team: string; status: string;
    publish_status?: string; risk_level?: string;
    created_at: string; description?: string | null;
  }
  ```
- [X] Four `useQuery` calls: agents (no status filter — pass `undefined`), tools, skills, workflows
- [X] Build `rows: ArtifactRow[]` with `useMemo` from all four lists
- [X] `typeFilter` state: `"" | "agent" | "tool" | "skill" | "workflow"`, default `""`
- [X] Filter chips row: All | Agents | Tools | Skills | Workflows (with counts)
- [X] Table: Name | Type badge | Team | Status badge | Publish Status badge | Created At
- [X] Status badge colors: `active`→green, `deprecated`→amber, `quarantined`→red, `archived`/`inactive`→slate
- [X] Publish status badge: `published`→green, `pending_review`→amber, `private`→slate, `undefined`→omit
- [X] No action buttons anywhere — read-only
- [X] Agent row click: `navigate(`/agents/${row.name}`)`. Others: no navigation.
- [X] `App.tsx`: add `import AdminArtifactsPage` + `<Route path="/admin/artifacts" element={<AdminArtifactsPage />} />`

**Verify:** `npm run typecheck`. `/admin/artifacts` shows all 3 agents + 9 tools + 3 skills with type/status badges.

---

### A6 — Catalog: Chat + Deploy buttons on granted agent cards [P]

- [X] `studio/src/pages/CatalogPage.tsx`: add `publish_status?: string` to `CatalogEntry` interface
- [X] In agents mapping: add `publish_status: a.publish_status`
- [X] Update `shared` filter: exclude unpublished agents:
  ```typescript
  const shared = entries.filter(
    (e) => e.grantedTo.length > 0 &&
           (e.type !== "agent" || e.publish_status === "published")
  );
  ```
- [X] Import `Link` from `react-router-dom` and `MessageSquare`, `Rocket` from `lucide-react`
- [X] In `CatalogCard`, add action row for granted agents:
  ```tsx
  {entry.type === "agent" && entry.grantedTo.length > 0 && (
    <div className="flex gap-2 mt-2 pt-2 border-t border-slate-100">
      <Link to={`/agents/${entry.name}/chat`}
        className="flex items-center gap-1 text-xs font-medium text-blue-600 hover:text-blue-800 bg-blue-50 hover:bg-blue-100 px-3 py-1.5 rounded transition-colors">
        <MessageSquare size={11} /> Chat
      </Link>
      <Link to={`/agents/${entry.name}/deploy`}
        className="flex items-center gap-1 text-xs font-medium text-slate-600 hover:text-slate-800 bg-slate-100 hover:bg-slate-200 px-3 py-1.5 rounded transition-colors">
        <Rocket size={11} /> Deploy
      </Link>
    </div>
  )}
  ```

**Verify:** `npm run typecheck`. Grant `customer-intelligence-agent` to a team — Chat + Deploy buttons appear on its catalog card.

---

### A7 — Deployments page [P]

- [X] `studio/src/api/registryApi.ts`: add function:
  ```typescript
  export const listAllDeployments = async (
    status?: string, limit = 100
  ): Promise<Paginated<Deployment>> => {
    const { data } = await http.get<Paginated<Deployment>>("/deployments/", {
      params: { limit, ...(status ? { status } : {}) },
    });
    return data;
  };
  ```
  Confirm `Deployment` interface is exported (it's used in `DeployAgentPage` — if defined inline there, extract to a named export in `registryApi.ts`).
- [X] Create `studio/src/pages/DeploymentsPage.tsx`
- [X] Status label map (same as `DeployAgentPage.tsx`):
  ```typescript
  const STATUS_LABELS: Record<string, { label: string; cls: string }> = {
    pending:     { label: "Pending",     cls: "bg-amber-100 text-amber-700" },
    deploying:   { label: "Deploying",   cls: "bg-blue-100 text-blue-700"  },
    running:     { label: "Running",     cls: "bg-green-100 text-green-700" },
    failed:      { label: "Failed",      cls: "bg-red-100 text-red-700"    },
    rolled_back: { label: "Rolled back", cls: "bg-slate-100 text-slate-600" },
    terminated:  { label: "Terminated",  cls: "bg-slate-100 text-slate-600" },
  };
  ```
- [X] `statusFilter` state, default `""`
- [X] `useQuery` with `refetchInterval: 30_000`
- [X] Filter tabs: All | Running | Deploying | Failed | Terminated
- [X] Table columns: Agent Name (link to `/agents/:agent_name`) | Status badge | Deployed At | Error | Actions
- [X] Actions: if `d.status === "running"` → `<Link to={/agents/${d.agent_name}/chat}>Chat →</Link>`
- [X] Running rows: `className` includes `"border-l-2 border-l-green-400"`
- [X] `App.tsx`: add `import DeploymentsPage` + `<Route path="/deployments" element={<DeploymentsPage />} />`

**Verify:** `npm run typecheck`. `/deployments` auto-refreshes. Running agents show Chat link.

---

### A8 — Sidebar restructure

- [X] `studio/src/components/Sidebar.tsx`: replace all nav constant arrays:
  ```typescript
  const PLAYGROUND_BUILD = [
    { label: "Agents",    to: "/",          end: true  },
    { label: "Skills",    to: "/skills",    end: false },
    { label: "Tools",     to: "/tools",     end: false },
    { label: "Workflows", to: "/workflows", end: false },
  ];
  const PLAYGROUND_EVAL = [
    { label: "Evaluate", to: "/playground",         end: true  },
    { label: "Datasets", to: "/playground/datasets", end: false },
  ];
  const ORG_ITEMS = [
    { label: "Catalog",     to: "/catalog"     },
    { label: "Deployments", to: "/deployments" },
  ];
  const ADMIN_ITEMS = [
    { label: "All Artifacts",  to: "/admin/artifacts"          },
    { label: "Publish Queue",  to: "/admin/publish-requests"   },
    { label: "Access Control", to: "/admin/access"             },
    { label: "HITL Queue",     to: "/hitl"                     },
    { label: "Approvers",      to: "/admin/approval-authority" },
  ];
  ```
- [X] Update `open` state to add `org` key:
  ```typescript
  const [open, setOpen] = useState({ playground: isPlaygroundRoute(pathname), org: isOrgRoute(pathname), admin: isAdminRoute(pathname) });
  ```
- [X] Add `isOrgRoute` helper:
  ```typescript
  function isOrgRoute(p: string) { return p.startsWith("/catalog") || p.startsWith("/deployments"); }
  ```
- [X] Update `isPlaygroundRoute` to include `/my-agents`
- [X] Add `useEffect` line: `if (isOrgRoute(pathname)) setOpen((o) => ({ ...o, org: true }));`
- [X] Update `toggle` type: `"playground" | "org" | "admin"`
- [X] Update JSX nav section — full new structure:
  ```tsx
  {/* Playground */}
  <Section label="Playground" open={open.playground} onToggle={() => toggle("playground")}>
    {PLAYGROUND_BUILD.map(i => <SideLink key={i.to} to={i.to} label={i.label} end={i.end} />)}
    <div className="my-2 border-t border-slate-800" />
    {PLAYGROUND_EVAL.map(i => <SideLink key={i.to} to={i.to} label={i.label} end={i.end} />)}
  </Section>

  {/* My Agents — placeholder; fully wired in Phase B */}
  <div>
    <p className="px-3 mb-0.5 text-xs font-semibold uppercase tracking-wider text-slate-500">My Agents</p>
    <NavLink to="/my-agents" className={({isActive}) => `block px-3 py-1.5 rounded text-sm transition-colors ${isActive ? "bg-slate-700 text-white font-medium" : "text-slate-400 hover:text-slate-200 hover:bg-slate-800"}`}>
      View my agents →
    </NavLink>
  </div>

  {/* Org */}
  <Section label="Org" open={open.org} onToggle={() => toggle("org")}>
    {ORG_ITEMS.map(i => <SideLink key={i.to} to={i.to} label={i.label} />)}
  </Section>

  {/* Config */}
  <div>
    <p className="px-3 mb-0.5 text-xs font-semibold uppercase tracking-wider text-slate-500">Config</p>
    <SideLink to="/providers" label="Providers" />
  </div>

  {/* Administration */}
  <Section label="Administration" open={open.admin} onToggle={() => toggle("admin")}>
    {ADMIN_ITEMS.map(i => <SideLink key={i.to} to={i.to} label={i.label} />)}
  </Section>
  ```

**Verify:** `npm run typecheck`. Sidebar shows Playground → Evaluate → My Agents → Org (Catalog, Deployments) → Config → Administration (All Artifacts first).

---

### Phase A — Build + Deploy

- [X] `cd studio && npm run build` — zero TypeScript errors
- [ ] `docker build -t registry.internal/agentshield/registry-api:0.2.26 services/registry-api/`
- [ ] `docker build -t registry.internal/agentshield/studio:0.1.25 studio/`
- [X] Update `scripts/deploy-cpe2e.sh`: `REGISTRY_API_TAG="0.2.26"` + `STUDIO_TAG="0.1.25"`
- [ ] `kubectl set image deployment/agentshield-registry-api registry-api=registry.internal/agentshield/registry-api:0.2.26 -n agentshield-platform`
- [ ] `kubectl set image deployment/agentshield-studio studio=registry.internal/agentshield/studio:0.1.25 -n agentshield-platform`
- [ ] `kubectl rollout status deployment/agentshield-registry-api -n agentshield-platform --timeout=60s`
- [ ] `kubectl rollout status deployment/agentshield-studio -n agentshield-platform --timeout=60s`

---

## Phase B: Consumer Chat
> Requires Phase A deployed. New backend router + 3 new Studio pages.

### B1 — Chat proxy router (backend)

- [X] Create `services/registry-api/routers/chat.py` with:

  ```python
  from __future__ import annotations
  import uuid, logging, json, asyncio
  from datetime import datetime, timezone
  from typing import Optional, Any, AsyncGenerator
  from fastapi import APIRouter, Depends, HTTPException, status
  from fastapi.responses import StreamingResponse
  from pydantic import BaseModel
  from sqlalchemy import text, select
  from sqlalchemy.ext.asyncio import AsyncSession
  from auth_middleware import require_user
  from db import get_db
  from models import Agent, AssetGrant, Deployment, PlaygroundRun

  router = APIRouter(prefix="/api/v1/agents", tags=["consumer-chat"])
  logger = logging.getLogger(__name__)

  class AgentChatRequest(BaseModel):
      message: str
      session_id: Optional[str] = None

  async def _caller_team(db: AsyncSession, user_sub: str) -> Optional[str]:
      row = await db.execute(
          text("SELECT team_name FROM user_team_assignments WHERE user_sub = :sub"),
          {"sub": user_sub})
      r = row.first()
      return r.team_name if r else None

  async def _has_grant(db: AsyncSession, agent_id: uuid.UUID, team: str) -> bool:
      now = datetime.now(tz=timezone.utc)
      row = await db.execute(
          select(AssetGrant).where(
              AssetGrant.asset_id == agent_id,
              AssetGrant.asset_type == "agent",
              AssetGrant.grantee_team == team,
              AssetGrant.revoked_at.is_(None),
          ).limit(1))
      grant = row.scalar_one_or_none()
      if grant is None:
          return False
      if grant.expires_at and grant.expires_at.replace(tzinfo=timezone.utc) < now:
          return False
      return True

  async def _running_deployment(db: AsyncSession, agent_id: uuid.UUID) -> Optional[Deployment]:
      row = await db.execute(
          select(Deployment)
          .where(Deployment.agent_id == agent_id, Deployment.status == "running")
          .order_by(Deployment.deployed_at.desc()).limit(1))
      return row.scalar_one_or_none()

  async def _sse_stream(message: str, run_id: str) -> AsyncGenerator[str, None]:
      words = message.split()
      for i, word in enumerate(words):
          content = word + (" " if i < len(words) - 1 else "")
          yield f"data: {json.dumps({'type': 'token', 'content': content})}\n\n"
          await asyncio.sleep(0.05)
      yield f"data: {json.dumps({'type': 'done', 'run_id': run_id})}\n\n"

  @router.post("/{name}/chat", status_code=status.HTTP_200_OK)
  async def start_chat(
      name: str,
      body: AgentChatRequest,
      caller: dict = Depends(require_user),
      db: AsyncSession = Depends(get_db),
  ) -> dict[str, Any]:
      result = await db.execute(select(Agent).where(Agent.name == name, Agent.status == "active"))
      agent = result.scalar_one_or_none()
      if not agent:
          raise HTTPException(status_code=404, detail=f"Agent '{name}' not found.")
      user_sub = caller.get("sub", "")
      caller_team = await _caller_team(db, user_sub)
      if caller_team != agent.team:
          if not caller_team:
              raise HTTPException(status_code=403, detail="User has no team assignment.")
          if not await _has_grant(db, agent.id, caller_team):
              raise HTTPException(status_code=403,
                  detail=f"Team '{caller_team}' does not have access to agent '{name}'.")
      deployment = await _running_deployment(db, agent.id)
      if not deployment:
          raise HTTPException(status_code=503,
              detail=f"Agent '{name}' has no running deployment. Deploy it first.")
      session_id = body.session_id or str(uuid.uuid4())
      run = PlaygroundRun(user_id=user_sub, agent_name=name, context="production",
          sandbox=False, input_message=body.message, status="running",
          started_at=datetime.now(tz=timezone.utc))
      db.add(run)
      await db.flush()
      run_id = str(run.id)
      await db.commit()
      logger.info("chat: run_id=%s agent=%s user=%s team=%s", run_id, name, user_sub, caller_team)
      return {"run_id": run_id, "session_id": session_id,
              "stream_url": f"/api/v1/agents/{name}/chat/{run_id}/stream",
              "agent_name": name, "deployment_id": str(deployment.id)}

  @router.get("/{name}/chat/{run_id}/stream")
  async def stream_chat(
      name: str, run_id: str,
      caller: dict = Depends(require_user),
      db: AsyncSession = Depends(get_db),
  ) -> StreamingResponse:
      parsed_id = uuid.UUID(run_id)
      result = await db.execute(select(PlaygroundRun).where(PlaygroundRun.id == parsed_id))
      run = result.scalar_one_or_none()
      if not run or run.agent_name != name:
          raise HTTPException(status_code=404, detail="Chat run not found.")
      if run.user_id != caller.get("sub", ""):
          raise HTTPException(status_code=403, detail="Not your chat run.")
      return StreamingResponse(_sse_stream(run.input_message, run_id),
          media_type="text/event-stream",
          headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
  ```

- [X] `services/registry-api/main.py`: add after last router import:
  ```python
  from routers.chat import router as chat_router
  ```
  Add in `create_app()` after playground routers:
  ```python
  # --- Consumer chat router (Phase B) ---
  app.include_router(chat_router)
  ```
- [X] Add to `main.py` module docstring:
  ```
  /api/v1/agents/{name}/chat  — consumer chat proxy (grant-checked, SSE)
  ```

**Verify:** `curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8001/api/v1/agents/customer-intelligence-agent/chat -H "Content-Type: application/json" -d '{"message":"hello"}'` → `401`

---

### B2 — My Agents page [P]

- [X] Create `studio/src/pages/MyAgentsPage.tsx`
- [ ] Imports: `useQuery` from react-query; `listAgents`, `listAllDeployments` from registryApi; `useAuth` from contexts/AuthContext; `Link` from react-router-dom; `Bot`, `Loader2`, `MessageSquare`, `Rocket`, `RefreshCw` from lucide-react
- [ ] Local `fetchTeamsSummary` helper (same as in CatalogPage: `fetch("/api/v1/admin/teams-summary")`)
- [ ] Three parallel queries: teams-summary, `listAgents(200, 0, "active")`, `listAllDeployments("running", 100)`
- [ ] Derive `myAgents`:
  ```typescript
  const { user } = useAuth();
  const myTeam = teams?.find(t => t.members.some(m => m.user_sub === user?.sub));
  const grantedNames = new Set(
    (myTeam?.grants ?? []).filter(g => g.asset_type === "agent").map(g => g.asset_name)
  );
  const runningNames = new Set((deploymentsPage?.items ?? []).map(d => d.agent_name));
  const myAgents = (agentsPage?.items ?? [])
    .filter(a => grantedNames.has(a.name))
    .map(a => ({ ...a, isRunning: runningNames.has(a.name) }));
  ```
- [ ] Page header: `"My Agents"` + subtitle `"Agents your team (${myTeam?.name ?? "—"}) has been granted access to"`
- [ ] Card grid (`grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4`) — one `MyAgentCard` per agent:
  - Name + description (2-line clamp)
  - Status badge: `isRunning` → `bg-green-100 text-green-700 "● Running"` ; else `bg-slate-100 text-slate-500 "Not deployed"`
  - Owner team label
  - Actions: if `isRunning` → Chat button (`Link to /agents/:name/chat`, primary blue) + View button; else → Deploy button + View button
- [ ] Empty state: `"No agents have been granted to your team yet. Ask an admin to grant access via Administration → Access Control."`
- [ ] `App.tsx`: add import + `<Route path="/my-agents" element={<MyAgentsPage />} />`

**Verify:** `npm run typecheck`. `/my-agents` shows cards for granted agents with correct Running/Not-deployed states.

---

### B3 — Agent Chat page [P]

- [ ] `studio/src/api/registryApi.ts`: add `startAgentChat`:
  ```typescript
  export const startAgentChat = async (
    name: string,
    body: { message: string; session_id?: string }
  ): Promise<{ run_id: string; session_id: string; stream_url: string; agent_name: string }> => {
    const { data } = await http.post(`/agents/${name}/chat`, body);
    return data;
  };
  ```
- [X] Create `studio/src/pages/AgentChatPage.tsx`
- [ ] Imports: `useParams`, `Link` from react-router-dom; `useQuery` from react-query; `getAgent`, `startAgentChat` from registryApi; `useState`, `useRef`, `useEffect` from react; `ArrowLeft`, `Bot`, `Loader2`, `Send` from lucide-react
- [ ] Types: `interface Message { role: "user" | "assistant"; content: string; }`
- [ ] State: `messages`, `input`, `isStreaming`, `sessionId` (init `crypto.randomUUID()`)
- [ ] `useRef` on message container for auto-scroll; `useEffect` scrolls to bottom on `messages` change
- [ ] `sendMessage` function:
  1. Guard: `!input.trim() || isStreaming` → return
  2. Append user message, clear input, set `isStreaming=true`
  3. `await startAgentChat(name!, { message: userMsg, session_id: sessionId })`
  4. Open `new EventSource(stream_url)` — note: `stream_url` from response is already a full path like `/api/v1/agents/{name}/chat/{run_id}/stream`; use it directly as the EventSource URL
  5. Append empty assistant message; on `message` event: parse JSON, if `type==="token"` update last message content; if `type==="done"` close + `setIsStreaming(false)`
  6. On `onerror`: close + `setIsStreaming(false)`
- [ ] Layout (full-height flex column — no inner scroll except message area):
  ```tsx
  <div className="flex flex-col h-screen bg-white">
    {/* Header — minimal */}
    <div className="border-b border-slate-200 px-6 py-3 flex items-center gap-3 shrink-0">
      <Link to="/my-agents" className="text-slate-400 hover:text-slate-600"><ArrowLeft size={16} /></Link>
      <div className="flex-1 min-w-0">
        <h1 className="text-sm font-semibold text-slate-900 truncate">{agent?.name ?? name}</h1>
        <p className="text-xs text-slate-400 truncate">{agent?.description}</p>
      </div>
      <span className="badge bg-green-100 text-green-700 text-xs">Live</span>
    </div>
    {/* Messages */}
    <div ref={messagesEndRef} className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
      {messages.length === 0 && <EmptyState description={agent?.description} />}
      {messages.map((m, i) => <MessageBubble key={i} role={m.role} content={m.content} />)}
    </div>
    {/* Input */}
    <div className="border-t border-slate-200 px-6 py-4 shrink-0">
      <form onSubmit={e=>{e.preventDefault(); sendMessage();}} className="flex gap-2">
        <input className="input flex-1" value={input} onChange={e=>setInput(e.target.value)}
          disabled={isStreaming} placeholder="Message…" />
        <button type="submit" disabled={isStreaming || !input.trim()} className="btn-primary">
          {isStreaming ? <Loader2 size={14} className="animate-spin"/> : <Send size={14}/>}
        </button>
      </form>
    </div>
  </div>
  ```
- [ ] `MessageBubble`: user → right-aligned, `bg-blue-600 text-white rounded-2xl rounded-br-sm`; assistant → left-aligned, `bg-slate-100 text-slate-800 rounded-2xl rounded-bl-sm`
- [ ] `App.tsx`: add import + `<Route path="/agents/:name/chat" element={<AgentChatPage />} />`

**Verify:** `npm run typecheck`. `/agents/customer-intelligence-agent/chat` shows clean chat UI. No trace panel, no version selector, no sandbox badge. Message streams tokens on send.

---

### B4 — Wire My Agents sidebar (dynamic mini-list)

- [ ] `studio/src/components/Sidebar.tsx`:
  - Add `useQuery` import and local `fetchTeamsSummary` + `listAgents` import
  - Add `useAuth` import
  - Add query:
    ```typescript
    const { user } = useAuth();
    const { data: sidebarTeams } = useQuery({
      queryKey: ["sidebar-teams"],
      queryFn: () => fetch("/api/v1/admin/teams-summary").then(r => r.json()),
      staleTime: 60_000,
    });
    const { data: sidebarAgents } = useQuery({
      queryKey: ["sidebar-agents"],
      queryFn: () => listAgents(200, 0, "active"),
      staleTime: 60_000,
    });
    const myTeamGrants = useMemo(() => {
      const myTeam = (sidebarTeams ?? []).find((t: any) =>
        t.members?.some((m: any) => m.user_sub === user?.sub)
      );
      const grantedNames = new Set(
        (myTeam?.grants ?? []).filter((g: any) => g.asset_type === "agent").map((g: any) => g.asset_name)
      );
      return (sidebarAgents?.items ?? []).filter(a => grantedNames.has(a.name)).slice(0, 5);
    }, [sidebarTeams, sidebarAgents, user?.sub]);
    ```
  - Replace the "My Agents" placeholder `<NavLink>` block with:
    ```tsx
    <div>
      <p className="px-3 mb-0.5 text-xs font-semibold uppercase tracking-wider text-slate-500">My Agents</p>
      {myTeamGrants.length === 0 ? (
        <p className="px-3 py-1 text-xs text-slate-500 italic">No agents granted yet</p>
      ) : (
        <>
          {myTeamGrants.map(a => <SideLink key={a.name} to={`/agents/${a.name}/chat`} label={a.name} />)}
          <NavLink to="/my-agents" className="block px-3 py-1 text-xs text-slate-500 hover:text-slate-300">
            See all →
          </NavLink>
        </>
      )}
    </div>
    ```
  - Add `useMemo` to React import if not already present

**Verify:** `npm run typecheck`. Sidebar My Agents section shows agent names for granted agents, "No agents granted yet" when empty.

---

### Phase B — Build + Deploy

- [ ] `cd studio && npm run build` — zero errors
- [ ] `docker build -t registry.internal/agentshield/registry-api:0.2.27 services/registry-api/`
- [ ] `docker build -t registry.internal/agentshield/studio:0.1.26 studio/`
- [ ] Update `scripts/deploy-cpe2e.sh`: `REGISTRY_API_TAG="0.2.27"` + `STUDIO_TAG="0.1.26"`
- [ ] `kubectl set image deployment/agentshield-registry-api registry-api=registry.internal/agentshield/registry-api:0.2.27 -n agentshield-platform`
- [ ] `kubectl set image deployment/agentshield-studio studio=registry.internal/agentshield/studio:0.1.26 -n agentshield-platform`
- [ ] `kubectl rollout status deployment/agentshield-registry-api -n agentshield-platform --timeout=60s`
- [ ] `kubectl rollout status deployment/agentshield-studio -n agentshield-platform --timeout=60s`

---

## Phase C: E2E Tests
> Write after Phase B is deployed. All tests run via `kubectl exec` into the registry-api pod.

### C1 — Create suite-14-consumer-chat.sh

- [ ] Create `scripts/e2e/suite-14-consumer-chat.sh` — copy boilerplate from `suite-8-playground.sh`:
  - Header comment block listing all test IDs
  - `NAMESPACE` var, `API_POD` lookup via `kubectl get pods -l app.kubernetes.io/name=registry-api`
  - `PASS=0 FAIL=0 MANUAL=0`
  - `pass()`, `fail()`, `check_manual()` functions

- [ ] **T-S14-001** — Chat returns 401 without auth token:
  ```bash
  STATUS=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
  import httpx
  r = httpx.post('http://localhost:8000/api/v1/agents/customer-intelligence-agent/chat',
                 json={'message':'hello'}, timeout=5)
  print(r.status_code)" 2>/dev/null)
  [ "$STATUS" = "401" ] && pass "T-S14-001: chat returns 401 without token" || fail "T-S14-001: expected 401 got $STATUS"
  ```

- [ ] **T-S14-002** — `/deployments/?status=running` only returns running deployments:
  ```bash
  RESULT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
  import httpx, sys
  r = httpx.get('http://localhost:8000/api/v1/deployments/?status=running', timeout=5)
  items = r.json().get('items', [])
  bad = [d.get('status') for d in items if d.get('status') != 'running']
  print('FAIL: ' + str(bad) if bad else 'ok')" 2>/dev/null)
  [ "$RESULT" = "ok" ] && pass "T-S14-002: /deployments/?status=running returns only running" || fail "T-S14-002: $RESULT"
  ```

- [ ] **T-S14-003** — Approve with empty body creates 0 grants and sets publish_status=published:
  ```bash
  RESULT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
  import httpx, sys
  # Create test agent
  r = httpx.post('http://localhost:8000/api/v1/agents/',
      json={'name':'s14-promote-test','team':'platform','agent_type':'declarative'}, timeout=5)
  if r.status_code not in (200,201,409): print(f'agent: {r.status_code}'); sys.exit(1)
  # Submit for publish
  pub = httpx.post('http://localhost:8000/api/v1/agents/s14-promote-test/publish', timeout=5)
  if pub.status_code not in (200,201): print(f'publish: {pub.status_code} {pub.text[:100]}'); sys.exit(1)
  pr_id = pub.json().get('publish_request_id','')
  # Approve with no grantee_teams
  apr = httpx.post(f'http://localhost:8000/api/v1/admin/publish-requests/{pr_id}/approve',
      json={}, timeout=5)
  d = apr.json()
  if apr.status_code != 200: print(f'approve: {apr.status_code} {d}'); sys.exit(1)
  if d.get('grants_created',99) != 0: print(f'expected 0 grants got {d}'); sys.exit(1)
  # Check publish_status
  ag = httpx.get('http://localhost:8000/api/v1/agents/s14-promote-test', timeout=5).json()
  if ag.get('publish_status') != 'published': print(f'publish_status={ag.get(\"publish_status\")}'); sys.exit(1)
  print('ok')" 2>/dev/null)
  [ "$RESULT" = "ok" ] && pass "T-S14-003: approve with no grantee_teams → 0 grants + published status" || fail "T-S14-003: $RESULT"
  ```

- [ ] **T-S14-004** — HITL production queue excludes playground approvals (regression guard):
  ```bash
  RESULT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
  import httpx, sys
  r = httpx.get('http://localhost:8000/api/v1/approvals/?status=pending', timeout=5)
  items = r.json().get('items', [])
  pg = [i for i in items if i.get('context') == 'playground']
  print('FAIL: playground items in prod queue: ' + str(pg) if pg else 'ok')" 2>/dev/null)
  [ "$RESULT" = "ok" ] && pass "T-S14-004: production HITL queue excludes playground approvals" || fail "T-S14-004: $RESULT"
  ```

- [ ] **T-S14-005** — Chat returns 503 when agent has no running deployment (MANUAL):
  ```bash
  check_manual "T-S14-005" "Chat returns 503 when agent has no running deployment" \
    "With valid Bearer token: POST /api/v1/agents/s14-promote-test/chat → 503 (no deployment)"
  ```

- [ ] **T-S14-006** — Chat endpoint SSE stream returns text/event-stream (MANUAL):
  ```bash
  check_manual "T-S14-006" "Chat SSE stream returns Content-Type: text/event-stream" \
    "POST /agents/{name}/chat → use run_id to GET stream_url → verify Content-Type header and 'data:' lines"
  ```

- [ ] **T-S14-007** — Cleanup test artifacts:
  ```bash
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
  import httpx
  httpx.delete('http://localhost:8000/api/v1/agents/s14-promote-test', timeout=5)
  " 2>/dev/null || true
  echo "  (Cleanup: s14-promote-test deleted)"
  ```

- [ ] Add summary block:
  ```bash
  echo ""
  echo "  Suite 14 Results: PASS=${PASS}  FAIL=${FAIL}  MANUAL=${MANUAL}"
  echo "  (MANUAL items require a valid Bearer token and running agent deployment)"
  [ "$FAIL" -eq 0 ] && exit 0 || exit 1
  ```

---

### C2 — Wire suite-14 into run-all.sh

- [ ] `scripts/e2e/run-all.sh`: add after last `run_suite` call:
  ```bash
  run_suite "Suite 14: Consumer Chat (Phase B)" "suite-14-consumer-chat.sh"
  ```

**Verify:** `NAMESPACE=agentshield-platform bash scripts/e2e/suite-14-consumer-chat.sh`
Expected: T-S14-001 through T-S14-004 PASS. T-S14-005, T-S14-006 MANUAL. T-S14-007 cleanup runs. Exit 0.

---

## Final Acceptance Checklist

### Phase A
- [ ] Sidebar: "Evaluate" not "Test"
- [ ] HITL Queue: header = "Production HITL Queue" + info callout
- [ ] Publish Queue: rows show agent name + description, column header = "Asset"
- [ ] Promote button calls approve with `{}` body; no grantee_teams field
- [ ] Toast after promote says "Promoted to catalog. Go to Access Control to grant team access."
- [ ] `/admin/artifacts`: all 3 agents + 9 tools + 3 skills shown, filterable by type, read-only
- [ ] Catalog: agent cards with grants show Chat + Deploy buttons (only when `publish_status=published`)
- [ ] `/deployments`: auto-refreshes, running rows have green border + Chat button
- [ ] Sidebar: Playground → My Agents → Org (Catalog, Deployments) → Config → Administration (All Artifacts first)

### Phase B
- [ ] `POST /api/v1/agents/{name}/chat` → 401 no token, 403 no grant, 503 no deployment, 200 all conditions met
- [ ] `/my-agents`: cards for team-granted agents with Running/Not-deployed status
- [ ] `/agents/:name/chat`: clean chat UI, tokens stream, no trace/version/eval controls
- [ ] Sidebar My Agents section: shows granted agent names, "No agents granted yet" when empty

### Phase C
- [ ] `suite-14-consumer-chat.sh` exits 0 with T-S14-001 through T-S14-004 all PASS
- [ ] `run-all.sh` includes Suite 14
