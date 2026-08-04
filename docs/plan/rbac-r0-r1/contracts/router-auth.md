# Contract — FR-11 security invariant, per router

Legend: **🔒** router-level `dependencies=[Depends(require_user)]` · **◐** per-endpoint · **🔓** exempt, with verified caller

| Router | File:line | Invariant | Exempt routes and their caller |
|---|---|---|---|
| `workflows.py` | 39 | 🔒 all 7 `/api/v1/agent-graphs` routes require a valid JWT | — |
| `teams.py` | 30 | 🔒 all 5 `/api/v1/teams` routes | — |
| `llm_providers.py` | 39 | 🔒 all 5 `/api/v1/llm-providers` routes (**credential-bearing**) | — |
| `admin.py` | 55 | 🔒 all 11 `/api/v1/admin` routes (grants, publish-requests, approval-authority, bundle regenerate) | — |
| `playground_approvals.py` | 23 | 🔒 `GET /api/v1/playground/approvals` | — |
| `deployments.py::router` | 195 | 🔒 `/{name}/deploy`, `/{name}/rollback`, `GET /{name}/deployments`, `PATCH /{name}/deployments/{id}` — **production deploy and rollback** | — |
| `versions.py::router` | 29 | 🔒 all 4 `/{name}/versions*` routes | — |
| `deployments.py::global_deployments_router` | 200 | ◐ `GET /workflows`, `GET /{id}/stats`, `GET /{id}/runs` | 🔓 `GET /` and `PATCH /{deployment_id}` — `deploy-controller/main.py:54,70,122,176` (**G-R1-2**) |
| `versions.py::versions_global_router` | 316 | ◐ nothing protected | 🔓 `GET /{version_id}` — `deploy-controller/main.py:33` (**G-R1-3**) |
| `auth_configs.py` | 36 | ◐ POST `/`, GET `/`, GET `/{id}`, PUT `/{id}`, DELETE `/{id}` (**credential-bearing**) | 🔓 `GET /{config_id}/secret-ref` — `deploy-controller/tool_secrets.py:45` (**G-R1-4**) |
| `agent_tools.py` | 26 | ◐ `POST /{name}/tools`, `DELETE /{name}/tools/{tool_id}` | 🔓 `GET /{name}/tools` — `deploy-controller/tool_secrets.py:36`, `declarative-runner/workflow_executor.py:171` (**G-R1-5**) |
| `agent_runs.py` | 23 | 🔓 entire router | `declarative-runner/main.py:410,437,148`, `checkpoint.py:27`, `orchestrator.py:35`; `eval-runner/main.py:1254` (**G-R1-1**) |

---

## Anonymous request to any 🔒/◐-protected route

```
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer
{"detail": "Authentication required"}
```

Invalid/expired token → 401 `{"detail": "Invalid or expired token"}`.

## Authenticated request

Byte-identical to pre-R1. **R1 adds authentication only — no role logic, no team scoping, no new 403.** `X-User-Sub` / `X-User-Team` headers keep their existing audit-stamp meaning and are never treated as authentication.

## Exemption governance

The table above **is** the contract. `suite-97` T-S97-011 asserts the protected/exempt partition programmatically against `app.routes`; a new route on any of these routers that is neither protected nor listed here fails the suite.

Why these five are exempt rather than credentialed: `deploy-controller`, `declarative-runner` and `eval-runner` call them with **no** `Authorization` header (verified — a grep for `headers|Authorization|Bearer` across `deploy-controller/main.py` returns nothing, and `eval-runner`'s `_EVAL_HEADERS` is `{"X-User-Sub": "eval-runner"}`, an audit stamp). Giving them credentials is service identity, owned by `docs/design/identity-propagation-architecture.md` (migrations 0080–0082) and out of R0/R1 scope.
