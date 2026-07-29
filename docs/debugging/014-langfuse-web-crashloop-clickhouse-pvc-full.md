# 014 — "No traces" → langfuse-web crash-loop → ClickHouse PVC 100% full

**Date:** 2026-07-28. **Cluster:** EKS test-cluster (`agentshield-platform`). Issue 3 / F-C.

## Symptom (named the wrong layer)

Reported as "no traces" in the Playground trace drawer. During the Issue-1 deploy,
`deploy-eks.sh` also surfaced: `langfuse-web` rollout timed out, "langfuse-web still cannot
reach Keycloak OIDC discovery," and a `CrashLoopBackOff` pod. The Keycloak/SSO message was a
red herring — the real failure was one layer down.

## Expected chain

agent pod emits spans → Langfuse (web+worker) ingests → ClickHouse stores → trace drawer
reads via `obs.get_trace`. "No traces" ⇒ a break somewhere on that chain.

## Investigation

| Step | Command | Evidence |
|---|---|---|
| 1. Which langfuse pods are unhealthy | `kubectl get pods \| grep langfuse` | one `langfuse-web` **Running** (old RS), one **CrashLoopBackOff** (new RS from the deploy), worker Running |
| 2. Why does the new pod crash | `kubectl logs <pod> --previous` | `error: code: 243, message: Cannot reserve 1.00 MiB, not enough space` / `Applying clickhouse migrations failed` |
| 3. Is it node disk or the PVC | `kubectl get nodes -o ...DiskPressure` | all nodes **DiskPressure=False** → not node ephemeral storage |
| 4. ClickHouse volume | `kubectl exec clickhouse -- df -h /bitnami/clickhouse` | **4.9G used / 852K free / 100%** |
| 5. Can it be expanded | `kubectl get sc block-storage -o ...allowVolumeExpansion` | `true` (ebs.csi.aws.com) |

## Root cause

The ClickHouse PVC (`data-agentshield-clickhouse-shard0-0`, 5 GiB, StorageClass
`block-storage`) was **100% full**. ClickHouse could not reserve even 1 MiB for its startup
migration, so every fresh `langfuse-web` pod crash-looped applying ClickHouse migrations →
trace ingestion dead → empty drawer. The chronic cluster DiskPressure (thousands of Evicted
pods) is the same disk-exhaustion story on a different volume. **Infra capacity, not code.**

## Fix

**Infra (F-C):** online-expanded the PVC (StorageClass allows it):
```bash
kubectl -n agentshield-platform patch pvc data-agentshield-clickhouse-shard0-0 \
  -p '{"spec":{"resources":{"requests":{"storage":"20Gi"}}}}'
# EBS resized; fs grow was FileSystemResizePending → restart the STS pod to remount+resize:
kubectl -n agentshield-platform delete pod agentshield-clickhouse-shard0-0
# then force the crash-looping web pod to retry immediately (skip the long backoff):
kubectl -n agentshield-platform delete pod <langfuse-web-crashloop-pod>
```
Result: `/bitnami/clickhouse` → **20G, 31% used**; a fresh `langfuse-web` migration
succeeded (0 restarts); `rollout status deploy/agentshield-langfuse-web` → successfully
rolled out, 1/1 Ready.

**StatefulSet caveat (why the chart was NOT bumped):** a StatefulSet `volumeClaimTemplate`
is immutable, so setting `langfuse.clickhouse.persistence.size` in values and running
`helm upgrade` would FAIL with an immutable-field error and break the deploy. The live PVC
was expanded directly instead. To reconcile the chart later: orphan-recreate the STS
(`kubectl delete sts agentshield-clickhouse-shard0 --cascade=orphan`) so the next
`helm upgrade` recreates the template at 20Gi with pods+PVC preserved. Recorded in the gap
ledger.

**Code (F-B, the resilience class-fix, separate change):** the trace drawer read Langfuse
ONLY, so a full ClickHouse took the whole trace view down. `get_trace_detail`
(observability.py) + `get_trace_by_id` (playground.py) now fall back to the durable Postgres
`run_steps` we always own — so a flaky external can never again blank the drawer. See
`docs/bugs/trace-drawer-langfuse-only-no-durable-fallback.md`.

## Lesson

A crash-loop that quotes an SSO/OIDC error can still be a disk problem one layer down —
always read the *previous* container logs and `df` the data volume before chasing the
message the pod prints. And don't let a single external store (ClickHouse) be the only path
to a first-class UI surface: keep a durable fallback on data you own.
