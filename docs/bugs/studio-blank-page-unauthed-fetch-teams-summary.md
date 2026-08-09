# Studio rendered a blank page on every route after `/admin/teams-summary` began requiring auth

**Found:** 2026-08-06, reported by Kalyan (screenshot of an empty `/agents/`).
**Fixed:** 2026-08-06 — studio `0.1.182` (blank page) + `0.1.183` (the rest of the class),
on branch `schedule-lifecycle`.
**Introduced by:** registry-api `0.2.262` (the RBAC R1 follow-up that added
`dependencies=[Depends(require_user)]` to `admin.py` + `admin_users.py`). The latent
defect in Studio was older; `0.2.262` is what made it reachable.

## Symptom

After deploying registry-api `0.2.262`, Studio rendered a completely blank page — not
an error boundary, not a partial render. Every route, including `/`, `/agents/`,
`/workflows`. Login succeeded; the token was valid; the API was healthy.

Browser console (read live from the user's tab):

```
TypeError: (a ?? []).find is not a function
    at ... Object.I_ [as useMemo] ...
```

## Root cause

Not the 401. The 401 was correct — `GET /api/v1/admin/teams-summary` **should** require
authentication, and `0.2.262` closed a real hole (anonymous
`POST /api/v1/admin/users {"role":"platform-admin"}` was returning **201**).

The defect was that two Studio call sites read that endpoint with a **raw `fetch`** —
no `Authorization` header, and no `r.ok` check:

```ts
// studio/src/components/Sidebar.tsx (0.1.181)
queryFn: () => fetch("/api/v1/admin/teams-summary").then((r) => r.json()),
```

That code only ever worked because the endpoint was unauthenticated. Once it returned
`401 {"detail":"Authentication required"}`:

1. `fetch` does **not** reject on a 4xx — `r.ok` is false but the promise resolves.
2. `r.json()` parses the error envelope into a plain **object**.
3. React Query has no failed promise to observe, so it stores that object as
   **success data**.
4. `const myTeam = (sidebarTeams ?? []).find(...)` throws `find is not a function`.
5. The throw happens inside a `useMemo` during render, uncaught. React unmounts the
   entire tree → blank page on every route, because `Sidebar` is in the app shell.

The `?? []` guard is what made this look safe. It covers `null`/`undefined` only. A
`500`, an HTML error page from the gateway, or any future response-envelope change would
have blanked the app in exactly the same way. **The endpoint's auth state was the
trigger, not the cause** — the cause is a consumer that trusts an unvalidated response
shape and does its own transport instead of using the shared client.

### The process failure behind it

Before adding `require_user` to those routers I audited for **in-cluster machine
callers** under `services/` and treated that as the whole blast radius. I never grepped
the **browser** for callers. That is the same incomplete-sweep error that let R1 miss
`admin_users.py` in the first place: check one class of caller, conclude the set is
covered.

## Fix

Both call sites now go through the shared authed axios client and coerce the response to
an array, so a shape surprise degrades instead of crashing:

- `studio/src/components/Sidebar.tsx` — `http.get("/admin/teams-summary")`, then
  `Array.isArray(data) ? data : []`. Axios throws on 4xx/5xx, so React Query sees an
  **error**, `data` stays `undefined`, and the existing `?? []` finally means what it
  looked like it meant.
- `studio/src/pages/MyAgentsPage.tsx` — same endpoint, same raw `fetch`, but it had
  `if (!r.ok) return []`, so it failed **quietly**: the "Shared With Me" panel rendered
  empty as though the user had no shared agents. Quiet is still wrong, so it was moved to
  the same authed path.

## The bigger half, found by checking my own claim (studio 0.1.183)

`0.1.182` fixed the blank page and the write-up above originally claimed the class was
closed: *"No raw `fetch` to `/api/v1/*` remains in `studio/src`."* Verifying that
sentence instead of asserting it turned up **six more**, all inline in
`AdminAccessPage.tsx`:

```
src/pages/AdminAccessPage.tsx:55   GET    ${API}/admin/users
src/pages/AdminAccessPage.tsx:64   POST   ${API}/admin/users
src/pages/AdminAccessPage.tsx:79   PATCH  ${API}/admin/users/${kc_id}
src/pages/AdminAccessPage.tsx:92   DELETE ${API}/admin/users/${kc_id}
src/pages/AdminAccessPage.tsx:97   POST   ${API}/admin/users/${kc_id}/reset-password
src/pages/AdminAccessPage.tsx:106  GET    ${API}/admin/teams-summary
```

**The entire Access Control users tab had been dead since `0.2.262`** — list, create,
edit, delete, reset-password. It did not blank the app only because these threw on
`!r.ok`, so React Query saw an error instead of a poisoned success. Same defect, louder
failure mode. `studio/e2e/admin-access-roles.spec.ts` confirmed it: **3 failed** in the
blast-radius sweep, including the save→reload persistence case.

### Why this is the class fix, not the instance fix

The instance fix is "add a token to those fetches". That leaves the shape that produced
them — a page component owning its own transport — intact and ready to do it again.

- All six moved into `src/api/registryApi.ts` behind the shared `http` client, which owns
  the Bearer header and the token-refresh interceptor. Anything routed through it is
  authenticated by construction.
- Reads reuse the **existing** `listUsers`, which was already there, already authed, and
  already used by the grant pickers. Adding a `listAdminUsers` beside it would have been
  a second reader of one endpoint — the same duplication that let the raw-fetch copy sit
  unnoticed. One fact, one producer. `AdminUser` absorbed `enabled` / `created_at` rather
  than the page keeping a parallel interface for the same row.
- `getTeamsSummary` coerces with `Array.isArray`, so **any** non-array response degrades
  instead of crashing — not just a 401. A 500 or a gateway HTML error page would have
  blanked the app identically.
- Verified, not asserted: `grep -rn '[^.a-zA-Z]fetch(' studio/src` now returns only
  `/config.json` reads (a static asset, no auth) and the two SSE streams in
  `WorkflowChatPage`/`CatalogChatPage` — those must use `fetch` because axios cannot
  stream a response body, and both already attach `Authorization: Bearer` explicitly.

### The test seam was hiding it

`AdminAccessPage.test.tsx` stubbed **global `fetch`** (`vi.stubGlobal`) because that was
where the page's transport lived. `Sidebar.test.tsx` did not mock the endpoint at all —
its raw `fetch` was invisible to the module mock, so the component's own test could not
see the call that later took the app down.

Both now mock `../api/registryApi` like every other page test in the repo. That is not
cosmetic: **omitting `getTeamsSummary` from the mock factory now fails `Sidebar.test.tsx`
immediately**, so a future bypass cannot be reintroduced silently. The seam and the
production path finally describe the same boundary.

## Regression test

`studio/e2e/app-shell-resilience.spec.ts` (registered in `scripts/test-manifest.txt`
under groups `health,rbac`). Three cases, all driving the real deployed app:

| Case | Asserts |
|---|---|
| 401 from `teams-summary` | app shell still mounts; **zero uncaught `pageerror`s** |
| non-array **200** body | same — covers the shape surprise arriving via the success path |
| `/my-agents` under a 401 | the quietly-degrading second call site still renders |

The `pageerror` listener **is** the assertion. Asserting only "the heading is visible"
would let a future regression that renders but logs an uncaught `TypeError` pass.

**Why Playwright and not Vitest:** reproducing this needed the real router, a real
`QueryClient`, and a real non-array HTTP response. A component test with
`vi.mock('../api/registryApi')` hands the component whatever shape the test author
imagined — it cannot produce the 401 envelope that caused the crash.

**Honest note on the RED-first gate (DoD rule 7).** The spec was **not** demonstrated red
by re-deploying studio `0.1.181` — the browser layer runs against the deployed artifact,
so showing red would have meant putting the known-broken image back on the user's
cluster. Evidence used instead:

1. The captured console `TypeError` above, read from the live tab while `0.1.181` was
   serving — the defect is directly observed, not inferred.
2. A mutation check proving the guard is not vacuous: a throwaway spec that raises the
   identical `(obj ?? []).find is not a function` inside the page was caught by the same
   `pageerror` listener. A listener that never fires would pass forever.

## Files changed

**0.1.182** — `studio/src/components/Sidebar.tsx`, `studio/src/pages/MyAgentsPage.tsx`,
`studio/e2e/app-shell-resilience.spec.ts` (new), `scripts/test-manifest.txt`.

**0.1.183** — `studio/src/api/registryApi.ts` (admin surface + `AdminUser` absorbs
`enabled`/`created_at`), `studio/src/pages/AdminAccessPage.tsx` (six raw fetches removed),
`studio/src/pages/AdminAccessPage.test.tsx` + `studio/src/components/Sidebar.test.tsx`
(seam moved to the module mock), and Sidebar/MyAgentsPage repointed at the shared
`getTeamsSummary`.

Tags bumped in all four places both times: `scripts/deploy-cpe2e.sh`,
`scripts/deploy-eks.sh`, `charts/agentshield/values.yaml`, `studio/src/lib/build.ts`.

## Lessons

1. **A security fix's blast radius includes the browser.** I audited for in-cluster
   machine callers under `services/` and treated that as the whole set. Grep `studio/src`
   for the path too — the UI is a consumer.
2. **Verify the claim, don't just write it.** "No raw fetch remains" was one `grep` away
   from being checkable, and running it found six more instances and a fully dead admin
   page. The sentence was going into a permanent doc as fact.
3. **`fetch` does not throw on 4xx.** `fetch(...).then(r => r.json())` without an `r.ok`
   check feeds the error envelope to its consumer as data. The shared axios client
   already handles this — the bug was bypassing it.
4. **`?? []` is not a type guard.** It defends against exactly two values. If the shape
   can be wrong in a third way, coerce (`Array.isArray`) or validate.
5. **An unauthenticated endpoint hides its consumers' bugs.** Any call site that "works"
   without a token is, by construction, untested against the authenticated path.
6. **A component test that can't see the call can't guard it.** Mocking global `fetch`
   made the page's private transport look tested. The module mock is the boundary that
   actually holds.
