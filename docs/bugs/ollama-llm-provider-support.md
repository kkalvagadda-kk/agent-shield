# LLM Provider Abstraction + Ollama Support — Plan (2026-07-12)

## Goal

Add Ollama as a third supported LLM provider (alongside `anthropic` and `bedrock`),
but do it by first introducing a provider **abstraction** so that Ollama — and any
future provider — is a registry entry per layer instead of a new `if/elif` branch
scattered across the codebase.

## Current State (the problem)

`anthropic`/`bedrock` are hardcoded as if/elif chains and static type unions in five
independent places: the Postgres CHECK constraint, the Pydantic schema, the judge's
dispatch logic, the SDK's LangChain factory, the deploy-controller's env-var mapping,
and the Studio form (two hardcoded JSX blocks). Adding a provider today means editing
all of them.

There is **no shared package** across `services/registry-api`, `services/deploy-controller`,
and `sdk/agentshield_sdk` — confirmed via independent `requirements.txt`/`pyproject.toml`;
only `declarative-runner` installs the `sdk` package (bakes it from source in its
Dockerfile). So this is **five small, parallel registries** — one per process/language
boundary — not one shared module. Each collapses its own if/elif into a dict; a static
parity check keeps the three backend ones in sync.

## Design Decisions

1. **Drop the DB CHECK constraint** (`ck_llm_providers_provider`). Confirmed via grep
   that `llm_providers` is written only through `routers/llm_providers.py` (no raw
   SQL/fixture writes elsewhere). The constraint is redundant with the new Pydantic-layer
   registry validation and is the one place that would otherwise force a migration per
   new provider — undercutting the point of this work.
2. **Ollama MVP is `base_url`-only** — no `api_key`/auth field. Ollama has no built-in
   auth; a reverse-proxy-bearer-token use case is speculative and would force
   optional-field branching through all five layers for a feature nobody asked for.
   Extending later is a one-line `optional_credential_fields` bump.
3. **Frontend keeps `superRefine` over a dynamic discriminated union** — a `.map()`-built
   `z.discriminatedUnion` loses TS tuple narrowing anyway. Instead:
   `provider: z.enum([...specKeys])` + credential values in a dynamic
   `Record<string,string>` keyed by field name, so the JSX-rendering loop and the
   validation loop are both registry-driven.
4. **New `GET /api/v1/llm-providers/specs` endpoint** exposes the backend's
   `LLM_PROVIDER_SPECS` (key, label, required credential fields) so the frontend
   consumes provider/field *existence* from the backend instead of re-declaring it.
   Studio keeps only UI-only concerns locally (badge color, model dropdown-vs-freeform,
   placeholders/help text).

## Layer-by-Layer Plan

### 1. `services/registry-api/llm_provider_specs.py` (new)

```python
@dataclass(frozen=True)
class LLMProviderSpec:
    key: str
    label: str
    required_credential_fields: tuple[str, ...]
    optional_credential_fields: tuple[str, ...] = ()
    validate: Callable[[dict], str | None] | None = None  # error string or None

LLM_PROVIDER_SPECS: dict[str, LLMProviderSpec] = {
    "anthropic": LLMProviderSpec("anthropic", "Anthropic", ("api_key",)),
    "bedrock": LLMProviderSpec("bedrock", "Amazon Bedrock",
        ("aws_access_key_id", "aws_secret_access_key", "aws_region")),
    "ollama": LLMProviderSpec("ollama", "Ollama", ("base_url",),
        validate=_validate_ollama_base_url),  # must start with http:// or https://
}
```

Adding a 4th provider later = one dict entry here (+ handler/factory/env-map entries
in layers 2-5 below).

### 2. `services/registry-api/schemas.py`

- `LLMCredentials`: add `base_url: str | None = None`.
- `LLMProviderCreate.provider`: `Literal["anthropic","bedrock"]` → `str`, plus a
  `model_validator` checking `provider in LLM_PROVIDER_SPECS`, all
  `required_credential_fields` present/non-empty, and the spec's `validate` hook if
  present — one clear `ValueError` (→ 422) on failure. Replaces the DB CHECK constraint
  as the enforcement point.

### 3. `services/registry-api/models.py` + new Alembic migration `0058_drop_llm_provider_check.py`

- `op.drop_constraint("ck_llm_providers_provider", "llm_providers", type_="check")` in
  `upgrade()`; recreate the old 2-value form in `downgrade()`.
- Remove `CheckConstraint` from `LLMProvider.__table_args__`.

### 4. `services/registry-api/routers/llm_providers.py`

- Add `GET /api/v1/llm-providers/specs` → `list[LLMProviderSpecResponse]`, built
  directly from `LLM_PROVIDER_SPECS`. New `LLMProviderSpecResponse` schema.

### 5. `services/registry-api/judge.py`

- Widen `_call_judge_anthropic(prompt, api_key)` → `_call_judge_anthropic(prompt, model, creds)`
  (extracts `api_key` internally) for signature uniformity — **keep its existing
  behavior of ignoring `model`** (hardcoded `_JUDGE_MODEL` constant); this is
  pre-existing behavior, not a regression to fix here.
- Add `_call_judge_ollama(prompt, model, creds)`: POST to `{creds['base_url']}/api/chat`
  (Ollama native endpoint, simplest fit for this file's hand-rolled `urllib.request`
  style) with `{"model": model, "messages": [...], "stream": false}`, extract
  `body["message"]["content"]`. Must use the resolved `model` — no sane hardcoded
  default exists for arbitrary user-pulled Ollama models.
- Extract `_parse_score_text(text: str)` from `_parse_score(body)` (unchanged behavior
  for the two existing providers); `_call_judge_ollama` calls it directly.
- Replace the `_call_judge()` if/elif with `_JUDGE_HANDLERS: dict[str, Callable]`
  dispatch. Leave the existing `ANTHROPIC_API_KEY` env-var fast-path untouched.

### 6. `sdk/agentshield_sdk/llm.py`

- Refactor into `_build_anthropic(model)` / `_build_bedrock(model)` / `_build_ollama(model)`
  functions + `_FACTORIES: dict[str, Callable[[str], Any]]` dispatch. `_build_ollama`
  reads `OLLAMA_BASE_URL` (required) and returns `ChatOllama(base_url=..., model=model)`.
- Add `langchain-ollama>=0.2` to `sdk/pyproject.toml`. **Verify at implementation time**
  that `ChatOllama(base_url=..., model=...)` is the correct call shape for the pinned
  version — not yet confirmed, not a blocker, but must be checked before calling this done.

### 7. `services/deploy-controller/manifest_builder.py`

- Replace the flat `_ENV_NAME_MAP` with per-provider `_PROVIDER_ENV_MAPS: dict[str, dict[str,str]]`
  keyed by `llm_provider_type` (anthropic/bedrock/ollama → their own credential-key→env-var-name
  maps), falling back to `key.upper()` for unmapped entries. Fixes a latent bug where
  `api_key` was unconditionally mapped to `ANTHROPIC_API_KEY` regardless of provider.

### 8. `scripts/check-llm-provider-parity.sh` (new)

Static, dependency-free `python3`+`ast` script parsing the dict-literal keys out of
`LLM_PROVIDER_SPECS`, `_JUDGE_HANDLERS`, `_FACTORIES`, `_PROVIDER_ENV_MAPS` via source
parsing (no imports needed — independent packages) and asserting all four key-sets are
equal. Wired as a preflight step at the top of `scripts/e2e/run-all.sh`.

### 9. `studio/src/lib/llmProviders.ts` (new)

UI-only per-provider config (`PROVIDER_UI_CONFIGS`: badge color, model
dropdown-vs-freeform) + `CREDENTIAL_FIELD_UI` (field-name-keyed label/input-type/placeholder,
generic across providers).

### 10. `studio/src/api/registryApi.ts`

- Loosen `LLMProvider`/`LLMProviderCreate.provider` from `"anthropic" | "bedrock"` to `string`.
- Add `LLMProviderSpec` interface + `listProviderSpecs()`.

### 11. `studio/src/pages/ProvidersPage.tsx`

- Fetch specs via React Query, merge with `PROVIDER_UI_CONFIGS[spec.key] ?? FALLBACK_UI_CONFIG`.
- Provider `<select>` built from fetched specs. Zod: `provider: z.enum(specKeys)` +
  `.superRefine` over `required_credential_fields`. Credential fields rendered via
  `.map()` instead of hardcoded per-provider JSX blocks. Model field: `<select>` (fixed)
  or free-text `<input>` (freeform, needed for Ollama).

## Tests (per CLAUDE.md Definition of Done)

- **Vitest** (new `studio/src/pages/ProvidersPage.test.tsx`): dropdown from mocked
  specs incl. ollama; selecting ollama swaps in `base_url` + freeform model input;
  valid submit calls `createProvider` with `{provider:"ollama", credentials:{base_url}}`;
  empty `base_url` blocks submit; table renders ollama badge.
- **Playwright** (new `studio/e2e/providers.spec.ts`): real UI flow — add Ollama
  provider, `waitForResponse` on `POST /llm-providers/`, **reload**, assert the row
  survived, delete via UI to stay idempotent. (Mandatory save→reload→assert gate.)
- **Backend e2e** (new `scripts/e2e/suite-54-llm-providers.sh`, registered in
  `run-all.sh`): CRUD an Ollama provider; missing `base_url` → 422; unknown provider
  name → 422 (proves CHECK-constraint removal didn't weaken enforcement, just moved
  it); `GET /llm-providers/specs` includes ollama with `required_credential_fields=["base_url"]`.
- **Parity check**: `scripts/check-llm-provider-parity.sh` green.

## Version Bumps (per CLAUDE.md)

- `REGISTRY_API_TAG` — schemas.py, judge.py, llm_provider_specs.py, migration, router.
- `STUDIO_TAG` — ProvidersPage.tsx, llmProviders.ts, registryApi.ts.
- `DEPLOY_CONTROLLER_TAG` — manifest_builder.py.
- `DECLARATIVE_RUNNER_TAG` — bakes `sdk/` from source; bump because `llm.py` changed.

Bump all four in **both** `scripts/deploy-cpe2e.sh` and `charts/agentshield/values.yaml`
(registry-api ~L588, studio ~L899, deploy-controller ~L650, `declarativeRunnerTag` ~L657),
same commit.

## Docs / Gap Ledger

- `docs/spec.md`: correct the "OpenAI, Anthropic, etc." provider mentions to reflect
  actual supported providers (anthropic, bedrock, ollama), and note agents call
  providers directly today (Portkey disabled by default) — light touch.
- Gap ledger: live inference against a *real* Ollama server is not exercised
  end-to-end in this cluster (no Ollama server deployed in the e2e environment) — only
  provider CRUD, validation, persistence, and config-wiring are verified. Tagged
  **deferred (intentional)**.

## Verification

1. `python3 -c "import ast; ast.parse(open(f).read())"` for every changed `.py` file;
   import `routers.llm_providers` + `sqlalchemy.orm.configure_mappers()`.
2. `bash scripts/check-llm-provider-parity.sh` passes.
3. `cd studio && npm run typecheck && npm run test` green.
4. `bash scripts/e2e/suite-54-llm-providers.sh` green standalone, then in `run-all.sh`.
5. `bash scripts/studio-e2e.sh` (Playwright) green, including `providers.spec.ts`.
6. Grep new exported symbols for callers: `listProviderSpecs`, `GET /llm-providers/specs`,
   `_build_ollama`, `_call_judge_ollama`, `LLM_PROVIDER_SPECS`.
7. Deploy via `scripts/deploy-cpe2e.sh`; manually create an Ollama provider through
   Studio, assign to a test agent, confirm pod env vars (`kubectl exec ... env | grep
   OLLAMA`) show `LLM_PROVIDER=ollama`, `OLLAMA_BASE_URL=...`.
