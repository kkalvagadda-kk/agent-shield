# Cost Tracking — Research & Design

## Problem

`agent_runs` has `cost_usd`, `prompt_tokens`, `completion_tokens` columns. Nothing writes them. Stats endpoints return 0. No visibility into LLM spend per agent, team, or model.

## Current State

| Component | Status |
|-----------|--------|
| DB schema (`agent_runs` cost fields) | Exists, always NULL |
| Stats API (`/agents/{name}/stats`, `/health`) | Aggregates cost_usd — returns 0 |
| Portkey gateway (Helm chart) | Fully charted, `enabled: false` |
| Portkey network policies | Written and ready |
| SDK `OPENAI_BASE_URL` config | Reserved, unused |
| Langfuse tracing | Live but uses `span()` not `generation()` — no cost captured |
| Agent pod Langfuse env vars | Not injected by deploy-controller |

## Architecture Decision: Portkey as Cost Source

```
Agent Pod
  → OPENAI_BASE_URL=http://portkey:8787/v1
    → Portkey Gateway (in-cluster)
      → LLM Provider (Anthropic / Bedrock / OpenAI)

Portkey captures per request:
  - prompt_tokens, completion_tokens, thinking_tokens
  - cost_usd (built-in model pricing table)
  - latency_ms, cache_status
```

### Why Portkey over alternatives

| Option | Pros | Cons |
|--------|------|------|
| **Portkey** (recommended) | Already charted, auto cost tables, caching + fallback + retry free, multi-provider | Extra network hop (negligible in-cluster) |
| Langfuse generation() | No new service | Requires LangChain callback wiring everywhere, no caching/fallback, pricing tables less complete |
| LiteLLM | Popular OSS proxy | Not in codebase, would duplicate Portkey's role |
| Manual token counting | Zero deps | Fragile, no standard across providers, must maintain pricing tables |

## Product Gaps (6)

### Gap 1: Portkey disabled
- Flip `portkey.enabled: true` in values.yaml
- Configure provider virtual keys in Portkey config

### Gap 2: Agent pods bypass proxy
- `services/deploy-controller/manifest_builder.py` must inject `OPENAI_BASE_URL=http://portkey:8787/v1`
- All LLM traffic routes through Portkey automatically

### Gap 3: SDK uses native Anthropic client
- `sdk/agentshield_sdk/llm.py` uses `ChatAnthropic` — doesn't respect `OPENAI_BASE_URL`
- Fix: Use Portkey's Anthropic pass-through mode (`x-portkey-provider: anthropic` header) OR switch to `ChatOpenAI` pointed at Portkey (Portkey translates to any backend)
- Portkey approach is cleaner: one client format, swap providers without SDK changes

### Gap 4: No cost writeback
- On run completion, read cost from Portkey logs API (by request ID / trace ID)
- PATCH `agent_runs` with `cost_usd`, `prompt_tokens`, `completion_tokens`
- Alternative: Portkey webhook callback → registry-api endpoint

### Gap 5: No cost UI in Studio
- See UX section below

### Gap 6: Agent pods missing Langfuse env vars
- Deploy-controller must inject `AGENTSHIELD_LANGFUSE_KEY` + `AGENTSHIELD_LANGFUSE_HOST`
- Without this, production agent tracing is broken (only playground traces work)

## UX Design (What Others Do)

### Industry patterns (Helicone, Portkey, LangSmith, Langfuse):

1. **Inline on runs** — token count + cost badge on each run card: `$0.003 · 340↑ 128↓`
2. **Agent stats card** — avg cost/run, total spend this period, token efficiency ratio
3. **Time-series dashboard** — daily spend line chart, stacked by model or agent
4. **Team allocation** — per-team breakdown for chargeback
5. **Budget progress bar** — "Team X: $142 / $500 this month" with threshold alerts
6. **Model comparison table** — same eval across models showing cost vs quality

### Proposed for AgentShield (phased):

**Phase 1 — Plumbing (no UI)**
- Enable Portkey, route traffic, implement writeback
- Cost data starts flowing into `agent_runs`

**Phase 2 — Inline cost in existing UX**
- Token + cost badge on each run in Playground
- Cost column in agents list table
- Cost per eval run in eval results

**Phase 3 — Dedicated /costs page**
- Time-series chart (daily spend)
- Filters: agent, team, model, date range
- Export CSV

**Phase 4 — Budgets & alerts**
- `team_budgets` table: `team_id`, `monthly_limit_usd`, `alert_threshold_pct`
- Alert when 80% / 100% hit
- Optional hard-stop (reject LLM requests at budget)

## Key Files

```
charts/agentshield/values.yaml          — portkey.enabled flag (~L796)
charts/agentshield/charts/portkey/      — full sub-chart
sdk/agentshield_sdk/llm.py             — LLM factory (ChatAnthropic / ChatBedrock)
sdk/agentshield_sdk/config.py          — OPENAI_BASE_URL reserved
services/deploy-controller/manifest_builder.py — agent pod env injection
services/registry-api/schemas.py       — AgentRunCreate/Response (cost fields)
services/registry-api/routers/agents.py — stats/health endpoints summing cost_usd
services/declarative-runner/main.py    — _complete_agent_run() (never writes cost)
infra/network-policies/                — Portkey ingress/egress rules ready
```

## Open Questions

1. Portkey virtual keys vs direct provider keys — do we create one Portkey virtual key per team (for attribution) or one global key?
2. Bedrock pricing — Portkey supports Bedrock but cost tables may differ from on-demand vs provisioned throughput. Verify.
3. Cache savings attribution — when Portkey serves from cache, cost = 0. Do we show "saved $X from cache" in UI?
4. Multi-model runs — a single agent run may call LLM multiple times (tool loops). Sum all calls into one `cost_usd` or show per-step breakdown?
