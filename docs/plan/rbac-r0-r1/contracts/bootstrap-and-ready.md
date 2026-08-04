# Contract — `ensure_platform_admin` and `GET /ready`

## `ensure_platform_admin() -> bool`

**Preconditions.** DB reachable (advisory lock + upsert); `settings.platform_admin_password` non-empty; `KEYCLOAK_URL` + `KEYCLOAK_ADMIN_PASSWORD` present. Keycloak reachability is *not* a precondition — unreachability is a normal, retried outcome.

**Postconditions on `True`.** Either:
- **(a)** a Keycloak user `platform-admin` exists with `email=platform-admin@agentshield.local`, `emailVerified=true`, `enabled=true`, `requiredActions=[]`, and a `user_team_assignments` row `(user_sub=<its id>, 'platform', 'platform-admin', 'system:bootstrap')`; and `bootstrap_state.ok is True`; or
- **(b)** another replica held the lock — no writes, `bootstrap_state` untouched by this attempt.

**Postconditions on `False`.** `bootstrap_state.ok is False`, `last_error` set to `"<ExceptionType>: <message>"`, one ERROR log line. **No partial state is left in the DB** (the upsert is the last step and commits atomically). A Keycloak user may exist without a row if the process dies between step 6 and step 9 — the next attempt finds it by username and completes.

**Invariants.**
- Never raises
- Never deletes
- Never resets an existing user's password
- Always releases the advisory lock (`finally`)
- The email is written on every successful attempt and is never parameterised

**Idempotence.** N successful runs ≡ 1 run. `assigned_at` moves only if `team_name` or `role` differed.

**Observability.**
```
INFO  bootstrap: platform-admin pinned sub=75c7c8b3-… team=platform role=platform-admin created=False
INFO  bootstrap: another replica holds the lock — skipping
ERROR bootstrap: FAILED (attempt 3): ConnectError: [Errno -2] Name or service not known
```

---

## `GET /ready`

| State | Status | Body |
|---|---|---|
| DB ok, bootstrap ok | 200 | `{"status": "ready"}` |
| DB ok, bootstrap pending/failed | **503** | `{"status": "bootstrapping", "detail": "<last_error or 'platform-admin bootstrap has not completed'>", "attempts": "3"}` |
| DB unreachable | 503 | `{"status": "unavailable", "detail": "<exc>"}` (unchanged) |

`GET /health` is unaffected and returns 200 whenever the process is alive — the liveness/readiness split is what keeps a Keycloak outage from restarting the pod.
