# POC-3 — Quickstart (cold-agent onboarding)

You are implementing **user-profile response preferences** on branch
`worktree-ux-preview-context-storage`. Commit here ONLY — never merge/PR to main (Karthik merges
manually).

## Read first (in order)
1. `plan.md` — tasks T01–T17 + checkpoints CP1–CP3. Follow the order; `[P]` tasks are parallel-safe.
2. `research.md` — where every seam lives in the *current* code (verified against 0.2.190 /
   0.1.143 / 0.1.55). Trust this over any line number in the design doc.
3. `contracts/enums.md` — the ONE canonical vocabulary. Every layer must match it.
4. `contracts/composition-contract.md` — `PHRASE_MAP` + the three function contracts + verbatim
   expected outputs (tests assert these).
5. `contracts/endpoints.md` — HTTP + pod-body + runner-schema + frontend-client shapes.
6. `data-model.md` — migration 0065 DDL + `UserProfile` model + Pydantic schemas.

## The one-paragraph mental model
A user saves enum presets → `user_profiles` (keyed by JWT sub). At dispatch time the **registry**
composes those enums into ONE bounded advisory string (`compose_directive_for_user`) and sends it
as a `user_directive` field to the agent pod. The **runner** appends it as a `SystemMessage` after
the author instructions — so it's the weakest voice in the prompt. Daemons have no user
(`user_id==""`) → no directive. The runner never touches `user_profiles`.

## Build order (vertical slice)
- **DB+model+compose+endpoint**: T01→T02→T03→T04 (then pytest T11).
- **Dispatch wiring**: T05→T06 (runner) then T07→T08 (chat) and T09→T10 (workflow).
- **Frontend**: T12→T13→T14/T15.
- **e2e + bump + deploy**: T16, then T18, then CP1 (`bash scripts/deploy-eks.sh`), CP2, CP3.

## Golden path to prove it end-to-end
```bash
# after CP1 deploy:
bash scripts/e2e/suite-76-preferences.sh     # API + compose + ownership + 422 + 2-user + daemon
# after CP2 deploy:
cd studio && npm run typecheck && npm run test -- PreferencesPage
cd .. && bash scripts/studio-e2e.sh          # Playwright: set → Save → reload → persisted
```

## Non-negotiables (CLAUDE.md)
- **Image bumps in all three files** (`deploy-cpe2e.sh`, `deploy-eks.sh`, `values.yaml`) for
  registry-api 0.2.191 / declarative-runner 0.1.56 / studio 0.1.144 — same commit. A code edit not
  built+deployed leaves the pod on old code.
- **No orphans** — CP3 greps each new symbol for a live caller.
- **Save→reload→assert** — suite-76 T-S76-002 + Playwright reload are mandatory, not optional.
- **No fakes in e2e** — suite-76 drives the real endpoints + real DB; the compose/422/ownership
  cases FAIL on breakage, never SKIP.
- **Reason from the running product** — if a seam moved since 0.2.190, re-ground before editing;
  update `research.md` if a line number drifted.

## Gotchas surfaced during grounding
- The enum vocabulary had **three conflicting drafts**; `contracts/enums.md` is the reconciled
  canonical set (matches the deployed frontend + architecture §8). Do not copy the POC-3 §3.1 SQL
  comment's `neutral/friendly/formal` / `bullet_points` — they're stale.
- `AgentRun.user_id` is **not** stamped on workflow parent runs today — T09 adds it; it's the
  daemon-vs-user discriminator T10 reads. Skipping T09 breaks the daemon-skip.
- The runner directive goes into the **message list at invoke time** (T05), NOT into
  `graph_builder.build_graph` (that prompt is per-agent and cached).
- New endpoints go in the **existing** `routers/me.py` (`/api/v1/me` prefix, already registered) —
  do not create a new router.
