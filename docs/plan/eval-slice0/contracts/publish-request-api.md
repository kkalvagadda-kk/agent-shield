# Contract — `GET /api/v1/admin/publish-requests`

**Change type:** additive. Two new response fields, no removals, no renames, no migration.
**Auth:** unchanged — admin-gated. This route is **not** affected by the deny-by-default fix.

---

## Response (changed fields only)

```jsonc
{
  "items": [
    {
      "id": "9f1c…",                       // PublishRequest.id — the NEW eval map key
      "asset_id": "3ab2…",
      "asset_type": "agent",
      "source_version_id": "77de…",        // existed; was never consulted by the eval query
      "asset_name": "refund-agent",
      "asset_team": "payments",

      "last_eval_score": 0.85,             // existed
      "last_eval_run_id": "51cc…",         // existed
      "last_eval_pass_threshold": 0.90,    // NEW — the bar THAT RUN used
      "eval_source": "version"             // NEW — "version" | "agent_latest" | "none"
    }
  ]
}
```

### `eval_source` semantics

| Value | Meaning | Client renders |
|---|---|---|
| `"version"` | The eval belongs to `source_version_id` | Score + `"0.85 / needs 0.90"` |
| `"agent_latest"` | Request pins **no** version; this is the agent's most recent completed run | Same **plus** an amber `"from a different version"` chip |
| `"none"` | No eval resolved | Existing `"No eval"` badge |

### Invariants (asserted by `T-S89-001..004`)

```
eval_source == "none"          ⟺ last_eval_score, last_eval_run_id,
                                 last_eval_pass_threshold ALL null
eval_source == "version"       ⟹ resolved EvalRun.agent_version_id == source_version_id
eval_source == "agent_latest"  ⟹ source_version_id IS NULL
last_eval_pass_threshold       == effective_pass_threshold(resolved_run)   // never 0.7
```

**The invariant that is the bug:** a request pinning a version with **no** eval for that version resolves
to `"none"`. It must never fall back to another version's score. Today it does, because the map is keyed
by `asset_id` and `source_version_id` is never read.

---

## Behaviour delta

| Scenario | Today | After |
|---|---|---|
| Request pins v2; eval exists on v1 only | v1's score, no threshold | `eval_source: "none"`, all eval fields null |
| Request pins v2; eval exists on v2 | v2's score if it is the agent's latest, else **another version's** | v2's score, `"version"`, that run's threshold |
| Request pins no version | agent's latest score | same score, now labelled `"agent_latest"` |
| Two pending requests, same agent, different versions | **identical** score on both | per-request resolution |

---

## Security contract — the two listing routes

**Both changed. This is the only access control on these endpoints:** registry-api installs no global
auth middleware (`main.py:176` = CORS + trace-ID only) and both routes use `get_optional_user`, which
returns `None` rather than raising `401`.

### `GET /api/v1/playground/eval-runs`
### `GET /api/v1/playground/datasets`

| Caller | Today | After |
|---|---|---|
| Valid JWT / `X-User-Sub` | own rows only | **unchanged** — own rows only |
| No JWT **and** no `X-User-Sub` | **every row on the platform** | `[]` |

```python
if caller:
    q = q.where(EvalRun.user_id == caller)
else:
    q = q.where(sa.false())   # DENY-BY-DEFAULT
```

Response shape is unchanged; only the row set narrows. No client change is required — Studio always
authenticates.

**Precedent:** `agents.py:167-170`, `tools.py:189`, `skills.py:99`, `composite_workflows.py:202` all
already carry this branch. `agents.py` documents why: *"previously a missing caller skipped the filter
entirely and leaked every agent."* These two routes were skipped because they have no `publish_status`
column for that fix's template to key on.

**Regression tests:** `T-S89-005` (eval-runs anonymous ⇒ `[]`), `T-S89-006` (datasets anonymous ⇒ `[]`),
`T-S89-007` (authenticated still sees own rows — guards against over-correction).

---

## Compatibility

- **Older clients:** both new fields are additive with defaults; a client that ignores them behaves as
  today except that a pinned-version-without-eval now correctly shows no score.
- **Older servers:** the TS type declares `last_eval_pass_threshold: number | null`, and
  `verdictOf(score, null)` returns `"unknown"` — a new client against an old server renders neutral, never
  a confident wrong verdict.
- **No migration**, so rollback is an image revert with no data step.
