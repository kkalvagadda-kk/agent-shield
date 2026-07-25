# mcp-proxy Dockerfile — hand-maintained COPY list drops new modules → CrashLoopBackOff

**Found/Fixed:** 2026-07-25 · found on the first Phase-2 deploy of mcp-proxy `0.1.2` to EKS; fixed in mcp-proxy `0.1.3`.

## Symptom

The mcp-proxy `0.1.2` pod went `CrashLoopBackOff` immediately on rollout (registry-api + studio rolled out fine). Logs:

```
File "/app/main.py", line 32, in <module>
    import identity
ModuleNotFoundError: No module named 'identity'
```

## Root cause

The `services/mcp-proxy/Dockerfile` copied modules with an **explicit, hand-maintained per-file list**:

```dockerfile
COPY services/mcp-proxy/config.py services/mcp-proxy/schemas.py ... services/mcp-proxy/main.py ./
```

Phase 2 added three new modules — `identity.py`, `keycloak_client.py`, `subscription_manager.py` (imported by `main.py`) — but nothing updated that list, so the modules were **absent from the image** while `main.py` imported them. Every static gate passed (AST parse, `helm lint`, unit imports on a dev box that has the files on disk) because they all see the source tree, not the built image layer. Only feeding the code to a real image build + running it surfaced the gap — which is exactly why the checkpoint is a *deploy* step, not a static check.

This is the same failure *class* as `docs/bugs/mcp-proxy-dockerfile-context-mismatch.md`: the Dockerfile's module manifest and the actual module set drift silently. There a build-context mismatch; here a stale file list. Both are "the Dockerfile encodes a fact that has to be re-derived by hand every time a module is added."

## Fix

Replace the per-file list with a glob so the image always contains every shippable module:

```dockerfile
COPY services/mcp-proxy/*.py ./
```

`services/mcp-proxy/` holds only shippable modules (no colocated tests), so the glob is safe, and the repo-root build context resolves it correctly. This is the class-fix: a new module is picked up automatically, so this bug cannot recur. A comment at the COPY line records why the list must NOT be reverted to explicit form.

## Lessons

- A per-file `COPY` list is a manifest that must be kept in lockstep with the module set by hand — a guaranteed drift point. Prefer a glob (or a package dir) when the directory contains only shippable code.
- Static verification (AST, lint, dev-box import) cannot catch "a file isn't in the image." The **deploy + run** checkpoint is the layer that can — this is precisely the DoD "test the layer that can actually fail" rule, applied to container packaging.
- When adding a module to a flat-layout service, the Dockerfile is part of the change's blast radius even though nothing imports it in Python.
