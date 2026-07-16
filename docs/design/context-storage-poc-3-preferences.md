# POC-3 — User-Profile Response Preferences

**Status**: Proposed (2026-07-16)
**Branch**: `worktree-ux-preview-context-storage` (commit only here; never merge to main)
**Companion**: [`context-storage-ux-roadmap.md`](./context-storage-ux-roadmap.md) §4 · [`context-storage-architecture.md`](./context-storage-architecture.md) §8
**Live baseline** (after POC-2b): registry-api 0.2.190 / studio 0.1.143 / declarative-runner 0.1.55

---

## 1. Why this exists

A user should be able to say, once, "answer me concisely, in bullet points, at an expert level" and have every agent honor it — without re-typing it each chat, and without it ever overriding what the task or safety requires. POC-3 is a small, platform-level personalization layer: **structured, enum-only response presets** compiled into a bounded **advisory** system directive.

**Reason from the running product.** No `user_profiles` table, model, or router exists. The **Preferences page exists only as a preview mock** (`studio/src/pages/preview/PreferencesPage.tsx`), but its real entry point is already wired — route `/preferences` (`App.tsx:68`) and a "Response Preferences" link in the sidebar account footer (`Sidebar.tsx:327`). So POC-3 adds the backend + directive composition and points the existing page at real data.

---

## 2. Scope decisions

1. **Structured enums only — no free text.** Presets are fixed-vocabulary enums (length / tone / format / language / expertise). This is the safety crux: a preference is applied to *someone else's* agent prompt, so it must not be an injection vector. Enum → fixed platform-authored phrase; no user string ever reaches the prompt raw.
2. **Advisory, with hard precedence: `governance > author instructions > workflow settings > user preference`.** A preference **never** overrides a task/format/safety/governance requirement. Enforced two ways: (a) the directive is placed *last* in the system prompt as an explicitly-labeled low-priority advisory, and (b) its wording says so ("honor only where it does not conflict with the above or any format/safety requirement").
3. **`user_delegated` runs only.** A daemon has no user principal → no preference applied (skip cleanly). Matches the identity model.
4. **Platform-level, deployment-independent.** Keyed by `user_id` only; the same preferences follow the user across every agent, sandbox and production.
5. **Composed registry-api-side, not runner-side.** The registry-api owns the DB and composes the enum → advisory string, passing it as ONE bounded field in the dispatch/chat payload. The runner/SDK just appends that platform-provided string to the system prompt. Keeps `user_profiles` out of the runner and keeps composition centrally controlled/bounded (finalize the exact field/seam in `/plan`).

---

## 3. Architecture

### 3.1 Data model (new migration — next number after 0064, i.e. 0065)

```sql
CREATE TABLE IF NOT EXISTS user_profiles (
  user_id         text PRIMARY KEY,              -- JWT sub; platform-level, not per-deployment
  response_length text,                          -- concise | balanced | detailed
  tone            text,                          -- neutral | friendly | formal
  format          text,                          -- prose | bullet_points | structured
  language        text,                          -- auto | en | es | fr | de | ... (bounded set)
  expertise       text,                          -- beginner | intermediate | expert
  updated_at      timestamptz NOT NULL DEFAULT now()
);
```
All preset columns nullable — NULL = "no preference" (that dimension is simply omitted from the directive). CHECK constraints (or app-layer enum validation) pin each column to its vocabulary. Idempotent DDL.

### 3.2 Backend (registry-api)

- `UserProfile` model + `UserPreferences` pydantic schema (all fields `Optional`, enum-validated).
- Two `require_user`, caller-scoped endpoints (a user only ever reads/writes their OWN row — `user_id = caller.sub`, no id in the path):
  - `GET /api/v1/me/preferences` → current row (or an all-null default).
  - `PUT /api/v1/me/preferences` → upsert; rejects any value outside the enum vocabulary (422).
- `compose_preference_directive(prefs) -> str | None` — the ONE composition function: maps set enums to fixed platform-authored phrases and wraps them in the precedence-framed advisory block; returns `None` when nothing is set. Example output:
  > *"[Advisory user preferences — apply only where they do not conflict with the instructions above or any format/safety/governance requirement.] Prefer concise answers. Use bullet points. Assume an expert audience."*
- **Wire into dispatch**: for `user_delegated` interactive chat (`routers/chat.py`) and for `user_delegated` workflow members (`workflow_orchestrator`), look up the caller's prefs, compose the directive, and pass it as a bounded payload field to the member pod's `/chat` / `/chat/stream`. Daemon runs pass nothing.

### 3.3 Runner / SDK (directive application)

- The pod's chat handler accepts the platform-composed `user_directive` field and appends it to the system prompt **after** the author instructions + the existing tool-reasoning nudge (`graph_builder.py:439`), so position reinforces precedence. It is a platform-provided string only — the runner never reads `user_profiles` or composes from raw user input.
- If the field is absent/empty (daemon, or no prefs) the prompt is exactly as today (no behavior change).

### 3.4 Frontend (Studio)

- `getMyPreferences` / `updateMyPreferences` in `registryApi.ts`.
- Point the existing **Preferences** page at real data (lift `pages/preview/PreferencesPage.tsx` into a real `PreferencesPage`, or wire it in place): five enum selectors + Save (optimistic + toast). The route (`/preferences`) and sidebar link already exist — no nav change needed.
- Load current prefs on mount; Save → `PUT`; reflect saved state.

---

## 4. Verification (Definition of Done gate)

- **Playwright** — set preferences (e.g. concise + bullet points) → Save → **reload** → selectors still show the saved values (persistence round-trip). Then, capacity permitting, run the same agent and assert the reply shape reflects the preference (best-effort; the wiring/persistence is the hard gate, model output is the soft one — warm-pods boundary).
- **Two-user proof** — two distinct users with different presets get **differently-formatted** answers from the *same* agent (the roadmap's headline). Where two live users are impractical in Playwright, prove it at the API layer in suite-75: compose-directive differs per user, and the dispatch payload carries the right one.
- **Vitest** — `PreferencesPage` (load/empty/save/enum options); `compose_preference_directive` mapping is unit-tested (each enum → its phrase; NULLs omitted; empty prefs → None).
- **suite-75** (or a new suite-76) — `PUT`/`GET /me/preferences` round-trip + ownership scoping (can't read another user's row); enum rejection (422 on a bad value); `compose_preference_directive` precedence-framed output; daemon run gets no directive.
- **No orphan code** — grep `compose_preference_directive`, `UserProfile`, `getMyPreferences`, `user_directive` for live callers.
- **Image bumps** — registry-api + studio + declarative-runner (applies the directive) in all three files; deploy is a separate user-gated EKS step.

## 5. Known gaps (ledger)

- **Language preset** breadth — start with a bounded set (`auto` + a handful); `auto` = don't force a language. Full i18n of the UI itself is out of scope.
- **Preference vs eval determinism** — evals run without a user, so no directive; keep eval output stable (daemon/no-user path already excludes it).
- **Precedence is prompt-enforced, not hard-enforced** — a sufficiently adversarial preference can't inject (enums only), but the precedence relies on prompt wording + position. Acceptable for structured enums; documented, revisit only if free-text presets are ever added (they are not).
