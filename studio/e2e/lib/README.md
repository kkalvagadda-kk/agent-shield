# e2e/lib — reusable lifecycle harness

A composable building-block library for browser e2e. **One helper per lifecycle stage.**
Specs are thin compositions of these helpers — the first consumer is
`e2e/lifecycle-journey.spec.ts` (the full 10-leg journey). Add new scenarios (workflow-only,
error-path, scheduled-trigger) as **new specs that reuse these helpers**, never by copy-paste.

## Modules

| Module | Exports | What it does |
|---|---|---|
| `api.ts` | `API_BASE`, `ADMIN_SUB`, `USER_SUB`, `adminApi`, `userApi`, `uniqueName`, `seedDeterministicTool`, `seedReactiveDataset`, `seedConversation` | Header-auth API contexts + fixtures seeded ahead of time (deterministic high-risk tool, reactive dataset). |
| `agents.ts` | `createAgentWithTool`, `deployToSandbox`, `deployAndWaitReady` | Drive the no-code create + tool picker (UI); deploy modal + poll `…/deployments` for `running`. |
| `workflows.ts` | `createWorkflow` | Save a 2-agent workflow through the builder; members+edges seeded via API (the canvas has no pure-UI edge-draw). |
| `chat.ts` | `chatAndAssert` | Send a turn, assert the network POST fired with the session (cold-pod tolerant), optionally assert recall. |
| `evals.ts` | `runEval`, `waitForEvalTerminal` | Launch an eval from DatasetsPage; poll `…/eval-runs/{id}` to terminal. |
| `publish.ts` | `markVersionPassed`, `markAdversarialPassed`, `publishAgent`, `approveToCatalog` | Playground promote buttons + admin `…/approve`. |
| `catalog.ts` | `deployFromCatalog`, `openConsumerChat` | Catalog deploy + consumer chat surface. |
| `observability.ts` | `snapshotDashboard` | Capture a dashboard's network payload + rendered panels for before/after. |

## Conventions
- **Identities.** `USER_SUB` (`75c7c8b3…`, the browser's logged-in user) for ownership-scoped
  read-backs; `ADMIN_SUB` (`047fad5f…`) to seed team-shared fixtures. Match the browser's sub
  when a UI read is owner-scoped, or the fixture won't be visible.
- **waitForResponse before click.** Arm the response wait, then click; assert the request fired.
- **Save→reload→assert.** Every write helper supports a reload round-trip (DoD #2).
- **Cold-pod boundary.** Agent-execution *completion* is tolerated-optional: assert the request
  fired + persistence, annotate-skip on no warm pod. Same boundary the other specs accept.
- **Idempotent + self-cleaning.** `uniqueName(prefix)` keeps runs isolated; helpers expose the
  ids they create so a spec can tear them down.
