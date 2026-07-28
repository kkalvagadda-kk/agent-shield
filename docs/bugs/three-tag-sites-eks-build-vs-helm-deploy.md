# The EKS deploy builds one tag and deploys another — there are THREE tag sites, not two

**Found:** 2026-07-27, deploying Eval Slice 0 (registry-api 0.2.234 / studio 0.1.167).
**Fixed:** cluster-state + `scripts/deploy-eks.sh` tag bump. **The class fix is NOT applied — see Follow-up.**

## Symptom

`scripts/deploy-eks.sh` ran to completion, `helm upgrade` reported `REVISION: 44 … STATUS: deployed`, and
the new registry-api pod sat in **`Init:ImagePullBackOff`** forever:

```
Failed to pull image ".../agentshield/registry-api:0.2.234":
  not found

$ aws ecr describe-images --image-ids imageTag=0.2.234
  ImageNotFoundException
```

The Deployment spec carried `0.2.234`. The running pods stayed on `0.2.233`. The deploy script's own
tail said it plainly, after a green helm upgrade:

```
[7/7] Waiting for rollouts...
error: timed out waiting for the condition
FATAL: registry-api did not roll out.
```

## Root cause

**Image tags live in three places, and the project's checklist documents two.**

| File | Role | Was |
|---|---|---|
| `scripts/deploy-cpe2e.sh` | tag list (documented in CLAUDE.md) | 0.2.234 ✅ |
| `charts/agentshield/values.yaml` | what **helm deploys** (documented) | 0.2.234 ✅ |
| **`scripts/deploy-eks.sh`** | what the EKS path **builds and pushes** | **0.2.232** ❌ |

`deploy-eks.sh` carries its own `REGISTRY_API_TAG` / `STUDIO_TAG` / … block at `:67-77`. On the EKS path
that block drives **step [2/7] build+push**, while `values.yaml` drives **step [6/7] helm upgrade**. The
two are independent. When they agree, everything works; when they drift, the build pushes an image nobody
deploys and helm deploys an image nobody built.

The log is unambiguous once you look for it:

```
[2/7] Building + pushing images (linux/amd64)...
  -> registry-api:0.2.232          # deploy-eks.sh's tag
  -> studio:0.1.163                # deploy-eks.sh's tag
...
REVISION: 44                        # values.yaml's tags: 0.2.234 / 0.1.167
```

**Why this hid for so long:** the two lists only drift when someone bumps the documented sites and not the
undocumented one. The previous studio deploy (0.1.166) worked *by accident* — that image had been built by
a separate manual `docker build`, so `values.yaml` happened to point at something real and
`deploy-eks.sh`'s stale tag was never consulted.

**Why a green helm upgrade is not a green deploy:** `helm upgrade` succeeds when the *manifests* apply. It
does not verify the referenced image exists. The failure only appears one step later, at rollout — and
`FATAL:` scrolled past under a `STATUS: deployed` banner.

## Fix (applied)

Bumped `deploy-eks.sh`'s `REGISTRY_API_TAG` → `0.2.234` and `STUDIO_TAG` → `0.1.167`, with a comment on
each stating that this file drives the BUILD and `values.yaml` drives the DEPLOY, so a mismatch is an
ImagePullBackOff rather than an error message.

## Follow-up — the class fix, deliberately NOT applied here

Bumping a third list is the same fix that failed: it keeps three sources of truth and relies on a human
remembering an undocumented one. The structural fix is for **`deploy-eks.sh` to derive its build tags from
`charts/agentshield/values.yaml`** so the build cannot target a tag the deploy will not use.

Not done in this change because it alters the deploy path while that path was being relied on to ship a
slice, with the author away — restructuring the tool you are mid-flight on is how a bad afternoon starts.
Recorded as **not-yet-wired (debt)** in the gap ledger.

Until then, **CLAUDE.md's "bump BOTH" instruction is wrong on the EKS path — it is three:**
`scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`, **and** `scripts/deploy-eks.sh`, plus
`studio/src/lib/build.ts` for the served-bundle marker.

## Detection that would have caught it

A preflight in `deploy-eks.sh`: before `helm upgrade`, assert every tag in `values.yaml` exists in ECR
(`aws ecr describe-images --image-ids imageTag=…`). That turns a 7-minute ImagePullBackOff into a
one-second failure naming the exact tag — and it works regardless of how many tag lists exist.

## Related

`docs/bugs/e3-never-ran-tag-not-bumped.md` — the mirror image: a tag *reused* so K8s served stale code
while every check stayed green. Same root: a tag is a claim about content, and nothing was verifying it.
