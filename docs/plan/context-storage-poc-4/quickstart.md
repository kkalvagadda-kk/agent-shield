# POC-4 Quickstart — build, deploy, prove

A cold agent should be able to implement POC-4 from `plan.md` + `contracts/` + `data-model.md`
+ `research.md` alone. This file is the run/verify loop.

## 0. Ground rules
- Commit to `worktree-ux-preview-context-storage` ONLY. Never merge/push/PR to main (Karthik
  verifies + merges).
- Read `research.md` first — it locks the three prerequisites and the 6 findings (F-1…F-6)
  that shaped every task. Do NOT re-litigate the "Python vs HTTP tool" decision (F-1) or bump
  declarative-runner (F-4).
- Shared constant `EMBEDDING_DIM = 384` appears in the migration (`vector(384)`), the sidecar,
  and `PgVectorStore`. If you change the model, change all three.

## 1. Prerequisite gate (CP-0) — do this before writing code
```bash
bash scripts/plan-poc4/verify-prereqs.sh    # pgvector PRESENT + minio REACHABLE on EKS
```
Expected on EKS: both PASS (pgvector ships as the portable image; MinIO is deployed). If
pgvector is absent, STOP and escalate — semantic retrieval can't work; do not silently ship
keyword-only on EKS.

## 2. Build order (mirrors the task graph)
1. Sidecar (T-002/T-003) → **CP-1**: `/embed` returns a 384-vector.
2. Migration + models (T-004/T-005) → `alembic upgrade head`, `configure_mappers()` clean.
3. Ports + factory (T-006/T-007/T-008).
4. Ingest + public API (T-009/T-010/T-011) → **CP-2**.
5. Internal endpoint + seeded tool (T-012/T-013).
6. Frontend real pages + citation wiring (T-014…T-017) + Vitest (T-018).
7. suite-77 + Playwright + bumps/docs (T-019…T-021) → **CP-3**.

## 3. Deploy (image bumps already in T-021)
```bash
bash scripts/deploy-eks.sh     # user-gated; builds+deploys registry-api 0.2.195,
                               # studio 0.1.146, embedding-sidecar 0.1.0 (NOT declarative-runner)
bash scripts/seed-defaults.sh  # seeds the knowledge_search HTTP tool (idempotent)
```

## 4. Prove it (CP-2 + CP-3)
```bash
# vertical slice (backend):
bash scripts/plan-poc4/smoke-knowledge.sh          # upload→ready→chunks→retrieval + isolation
bash scripts/e2e/suite-77-knowledge-rag.sh         # 5 cases incl. headline tenant-isolation

# frontend:
cd studio && npm run typecheck && npm run test     # Vitest incl. citation rendering
cd - && bash scripts/studio-e2e.sh                 # Playwright: upload→status→retrieve→attach→cite
```

## 5. The end-to-end demo (what "done" looks like)
1. Studio → **Build › Knowledge** → New Knowledge Base "Company Policies".
2. Sources tab → upload `refund-policy.txt` (contains "Refunds over $500 need manager
   approval."). Watch status Queued → Processing → Ready; View shows the chunks.
3. Test retrieval tab → "When do refunds need approval?" → the $500 chunk ranks top.
4. Attach the `policy-qa` agent (picker) — this wires `knowledge_search` + the KB binding.
5. Chat with `policy-qa`: "Do I need approval to refund $700?" → it answers using the policy
   **and a citation chip `refund-policy.txt · Company Policies` renders under the reply**
   (the POC-2b slot, now real).
6. As a **different team**, the same query returns nothing from this KB — isolation holds.

## 6. Definition-of-Done statement to write when reporting done
- **Journey proof:** `studio/e2e/knowledge.spec.ts` drives upload→status→retrieval→attach→
  citation-chip in the browser.
- **Save→reload:** suite-77 `T-S77-002` re-reads sources+chunks from the backend after upload.
- **No orphan:** greps in T-021 (`BlobStore`, `VectorStore`, `get_*_store`, `embed`,
  `knowledge_search`, `ingest_source`, `agent_knowledge_bindings`, `citations`) each resolve to
  a live caller.
- **Tenant isolation:** suite-77 `T-S77-005` asserts team-A can't retrieve team-B chunks at
  both the store and API layers, fail-closed.
- **Gaps:** recorded in `docs/testing/manual-ui-e2e-test-plan.md` (docx, durable worker,
  multi-KB, orphan-blob GC, signed token, S7 content-scan).

## 7. Gotchas (from research.md)
- HTTP-tool headers substitute from `os.environ` — `{{AGENTSHIELD_AGENT_TEAM}}`/`{{AGENT_NAME}}`
  resolve to the pod's real env; the model can't touch them. This is the tenant binding.
- `PgVectorStore.search` requires `(team, kb_id)` — there is no "search all" path; passing an
  empty team raises. Keep it that way (S5).
- Citations already have a renderer (`AttributedBubble.citations`) — you only FEED it from
  `tool_call_end.result`; don't build a new component or touch the runner.
- MinIO client needs path-style addressing + `endpoint_url`; the bucket `knowledge-sources` is
  created on first `put`.
- Migration is guarded — on a stock dev Postgres the vector column is skipped; test on EKS (or a
  local pgvector) for the semantic path.
