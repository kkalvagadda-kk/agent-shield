# Phase A-B-C: Design Decision Rationale

**Authoritative spec:** `docs/plan/plan-phases-abc.md`
**This file:** Captures the *why* behind each implementation choice so future engineers don't re-litigate settled questions.

---

## Decision 1: Client-side UUID→name enrichment (A3)

**Choice:** Fetch all assets client-side in `AdminPublishRequestsPage` and build a `Record<uuid, {name, description, team}>` map via `useMemo`.

**Rejected alternative:** Add `asset_name` + `asset_description` columns to `PublishRequestResponse` via a DB JOIN in `routers/admin.py`.

**Why client-side wins:**
- The `publish_requests` table stores `asset_id` as a generic UUID spanning agents, tools, skills, and workflows — four different tables. A JOIN would require a UNION or a polymorphic lookup, complicating the query.
- The four list endpoints already exist and are already called by other pages in the same admin session. React Query caches them under stable `queryKey`s — the second call is free.
- Backend stays clean: `PublishRequestResponse` remains a direct ORM→Pydantic mapping with no JOIN.
- The asset lists are small (tens of rows, not thousands) so network cost is negligible.

**Caveat:** If an asset is deleted between submission and admin review, the UUID cell falls back to `"${uuid.slice(0, 8)}…"`. This is acceptable UX — a deleted-asset publish request is an edge case the admin should reject anyway.

---

## Decision 2: `grantee_teams` optional, not removed (A4)

**Choice:** Change `PublishRequestApprove.grantee_teams: list[str]` to `list[str] = Field(default_factory=list)`. Keep the field in the schema and the loop in the router.

**Why not make Promote and Grant completely separate API calls:**
- The existing approve endpoint already handles both concerns. Splitting it would require a new `/grant` endpoint, a new router function, and two API calls from the frontend for the common case.
- Making `grantee_teams` optional achieves the same UX separation with one-line change: pass `{}` to promote, pass `{"grantee_teams": ["team-a"]}` to promote+grant in one step. Both paths work.
- Zero migration risk — existing callers that pass `grantee_teams` still work identically.

**Why remove the grantee_teams input from the Promote form:**
- Mixing approval authority (admin's call) with access grant (also admin's call, but separate step) in one form caused confusion in UX review. The admin should explicitly navigate to Access Control to grant — that makes the two-step nature visible, auditable, and reversible independently.

---

## Decision 3: SSE reuse for consumer chat (B1)

**Choice:** `POST /api/v1/agents/{name}/chat` returns `{run_id, session_id, stream_url}` synchronously. Client then opens `EventSource(stream_url)` for token streaming. Same pattern as playground.

**Rejected alternative:** Upgrade to WebSocket.

**Why SSE:**
- Playground already uses SSE (`/api/v1/playground/{run_id}/stream`). Reusing the pattern means the frontend team already knows EventSource and the browser compatibility story.
- SSE is unidirectional (server→client) which is exactly what token streaming requires. WebSocket bi-directionality adds complexity for no benefit — the user sends one message per `POST`, not a continuous stream.
- SSE works through Nginx ingress without any special config. WebSockets require `proxy_read_timeout` + upgrade headers. The Helm chart already works for SSE.
- FastAPI's `StreamingResponse` with `text/event-stream` is production-proven in this codebase.

---

## Decision 4: No new DB tables for Phase B (B1)

**Choice:** Reuse `PlaygroundRun` table with `context="production"`, `sandbox=False` for consumer chat runs.

**Why:**
- The `PlaygroundRun` table already has all the fields needed: `user_id`, `agent_name`, `input_message`, `status`, `started_at`, `context`, `sandbox`. The `context` column was designed explicitly for this separation (playground vs production).
- Adding a `ChatSession` table would duplicate the schema and fragment the run history across two tables with no query benefit.
- HITL filtering already uses `context='production'` to separate production from playground — extending that to chat is consistent.

**Session tracking:** `session_id` is a UUID generated client-side and passed in the request body. It's stored nowhere server-side in Phase B (the server returns it back to the client for continuity across page refreshes). If multi-turn conversation history becomes a requirement, a `ChatSession` table can be added in a future phase without breaking Phase B's API contract.

---

## Decision 5: Grant check via `user_team_assignments` (B1)

**Choice:** Look up `user_sub → team_name` via a `SELECT` on `user_team_assignments`, then check `asset_grants` for that team.

**Why not check Keycloak JWT roles:**
- Keycloak roles reflect org-level roles (admin, reviewer), not data-level team grants. A user's JWT doesn't include which specific agents their team has been granted.
- `asset_grants` is the authoritative grant store. Checking it directly is correct.
- `user_team_assignments` is populated at login/assignment time. It's a simple lookup.

**The check hierarchy:**
1. If user is in the same team as the agent's `owner_team` → allow (owner always has access)
2. Else if user has no team assignment → 403 "User has no team assignment"
3. Else check `asset_grants` for (agent_id, user's team) — if not found or expired → 403

This means an agent owner can always chat with their own agent without an explicit grant. Cross-team access requires an explicit grant.

---

## Decision 6: `AdminArtifactsPage` is read-only (A5)

**Choice:** No action buttons on the Admin All Artifacts view. Agent row links to `/agents/:name` (existing detail page). No edit/delete from this view.

**Why:**
- The Admin role should observe, not mutate. Mutations happen in Playground (developer action) or Access Control (admin action). Mixing edit into a read-only audit view creates accidental-mutation risk.
- This is consistent with how enterprise platforms work: an admin "view all" page shows cross-team assets as read-only context; the developer who owns the asset makes edits via their own Playground workspace.

---

## Decision 7: Deployments page polling interval (A7)

**Choice:** `refetchInterval: 30_000` (30 seconds) in the `useQuery` for `listAllDeployments`.

**Why 30s not 5s:**
- Deployments are long-running K8s processes. Status transitions (pending→deploying→running) happen on the order of minutes, not seconds.
- 30s is fast enough for a human watching a deploy to see progress without making 12 API calls per minute per user.
- The K8s reconciler loop in `deploy-controller` runs every ~30s anyway, so checking faster than that gives no additional freshness.

**Why not WebSocket/SSE push:**
- Deployment count per org is small. Polling is simple and avoids a persistent connection that the K8s ingress might idle-close. SSE would be appropriate at scale (hundreds of deploys/hour) — not warranted here.

---

## Decision 8: `listAgents` status filter behavior (A3, A5)

**Choice:** `listAgents(limit, offset, status?)` sends `?status=` only when `status !== undefined`. Call sites:
- Default (`listAgents()`) → `status=active` → hides deprecated test agents from Playground
- Admin Artifacts page → `listAgents(200, 0, undefined)` → no status filter → shows all

**Why not two separate functions:**
- DRY. The underlying HTTP call is identical except for the query param. One function with an optional param is cleaner than `listActiveAgents` + `listAllAgents`.
- TypeScript optional param with a conditional spread (`...(status !== undefined ? { status } : {})`) is straightforward and explicit about the intent.
