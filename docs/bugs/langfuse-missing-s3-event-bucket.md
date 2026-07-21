# Langfuse "Trace not found" — S3 event bucket never created (name mismatch)

**Found:** 2026-07-20 (EKS test-cluster, langfuse enabled via `values-eks.yaml`)
**Fixed:** 2026-07-20 — chart values `langfuse.s3.defaultBuckets: langfuse-media` (charts/agentshield/values.yaml) + belt-and-suspenders bucket-ensure in `scripts/deploy-eks.sh`. No image rebuild (chart-only).

## Symptom

Running an agent in the Studio playground produced a response and a **View Trace** link, but clicking it landed on Langfuse's **"Trace not found — The trace is either still being processed or has been deleted."** This held for every fresh run, not just old ones. SSO and the langfuse subdomain worked fine; the traces list page loaded but showed "No results".

## Root cause

Langfuse v3 does **not** write ingestion events straight to ClickHouse. The flow is:

```
agent (OTEL) → langfuse-web /api/public/ingestion
             → upload raw event JSON to S3  (bucket: langfuse-media)   ← FAILED HERE
             → publish ref to redis ingestion-queue
langfuse-worker → read event JSON back from S3 → write rows to ClickHouse
```

The S3 bucket `langfuse-media` **did not exist** in the langfuse-bundled MinIO
(`agentshield-s3` pod). Every upload aborted with:

```
Failed to upload JSON to S3 .../trace/<id>/<evt>.json  The specified bucket does not exist
Error: Failed to upload events to blob storage, aborting event processing
```

Because the event never reached S3, the worker had nothing to read, and nothing
ever reached ClickHouse — so the trace list was empty and each trace lookup 404'd.
No error surfaced in Studio; the run itself completed normally, so the failure was
**silent** at every user-visible layer.

**The design flaw (class bug), not the surface error:** the langfuse-k8s subchart has
two independent bucket settings that must agree but don't validate each other —

- `langfuse.s3.bucket` — the bucket langfuse **writes to** (all three upload types:
  event / batch-export / media all resolve to this). We override it to `langfuse-media`.
- `langfuse.s3.defaultBuckets` — the bucket the bundled Bitnami MinIO **auto-creates**
  at boot. Chart default: `langfuse`.

We overrode `bucket` → `langfuse-media` but left `defaultBuckets` at its `langfuse`
default. MinIO dutifully created `langfuse/`; langfuse wrote to `langfuse-media/`. The
two silently diverged, and the only evidence was a log line in langfuse-web.

## Fix

1. **`charts/agentshield/values.yaml`** — added `langfuse.s3.defaultBuckets: "langfuse-media"`
   so the auto-created bucket equals the configured `bucket`. Bitnami MinIO re-runs
   `mc mb --ignore-existing` for each default bucket on every boot, so a **fresh** S3 PV
   provisions the right bucket and future deploys self-heal. Loud comment added tying the
   two keys together as an invariant (they can't self-reference in plain YAML).
2. **`scripts/deploy-eks.sh`** — belt-and-suspenders: after the langfuse post-helm patches,
   `mc mb --ignore-existing langfuse-media` inside the running s3 pod (using the pod's own
   root creds in-place). This covers the case an **existing** S3 PV already booted with the
   old `langfuse`-only default and won't re-derive the new bucket name.
3. **Immediate unblock (already applied to the live cluster):** created the bucket by hand
   via `mc mb` in the `agentshield-s3` pod.

## Verification (real user journey, end-to-end in browser)

After creating the bucket: Studio playground → serper-agent-5 (sandbox) → "What is 7 times 8?"
→ agent answered "56" → clicked **View Trace** → Langfuse rendered the **full** trace
`serper-agent-5: b135cdb8…` — span tree (LangGraph → agent → ChatBedrockConverse), input
"What is 7 times 8?", output "56", latency 1.78s, cost $0.0025, llm-judge 0.90. The
immediately-prior run (`d93b5a8f…`, sent before the bucket existed) stays "Trace not found" —
its S3 upload had already aborted, confirming the bucket was the gate.

## Lessons

- Langfuse v3 ingestion is **S3-first**; a missing/misnamed blob bucket looks exactly like
  "traces don't work" with zero error in the producing app. Always check langfuse-web logs
  for `Failed to upload ... to S3` before suspecting the worker or ClickHouse.
- When a subchart splits "the thing you use" from "the thing that gets provisioned" into two
  values, overriding one without the other is a latent config bug. Pin both to the same value
  and comment the coupling.

## No automated test (gap)

There is no e2e that asserts a trace round-trips into Langfuse (langfuse isn't in
`scripts/e2e/run-all.sh`; it's EKS-only infra). The structural guard is the aligned
`defaultBuckets` + the deploy-time `mc mb` ensure. Recorded as a gap; a post-deploy smoke
check ("emit one trace, assert it appears in ClickHouse within N s") would close it.

Related: [langfuse-trace-access-sso-and-membership.md](./langfuse-trace-access-sso-and-membership.md),
[langfuse-clickhouse-oom.md](./langfuse-clickhouse-oom.md)
