#!/usr/bin/env bash
# =============================================================================
# DEFERRED — written but NOT executed this run; run after deploying.
# Requires a live cluster.
# =============================================================================
# CP1b — MCP as a Tool Source (Phase 1): infrastructure smoke.
#
# Asserts the registry-api foundational deploy is real at the schema level:
#   - registry-api pod Ready
#   - alembic current == 0072
#   - mcp_servers has all 6 new columns (identity_mode / is_external /
#     transport_config / health_detail / list_changed_supported / scan_results)
#     with the correct types, NOT NULL flags, defaults, and the identity_mode CHECK
#   - tools has pii_deanonymize_allowed (boolean NOT NULL default false)
#
# Exit 0 on full pass, non-zero on the first failure. Ends with `echo "PASS"`.
set -euo pipefail

echo "=== Checkpoint CP1: infra smoke (migration 0072 + schema) ==="

NAMESPACE="${NAMESPACE:-agentshield-platform}"

fail() { echo "FAIL: $1" >&2; exit 1; }

# ── 1. registry-api pod Ready ─────────────────────────────────────────────────
echo "--- registry-api pod Ready ---"
kubectl wait --for=condition=Ready pod \
  -l app.kubernetes.io/name=registry-api -n "$NAMESPACE" --timeout=180s \
  || fail "registry-api pod not Ready within timeout"
API_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$API_POD" ] || fail "no Running registry-api pod found"
echo "  OK: pod $API_POD Ready"

# ── 2. alembic current == 0072 ────────────────────────────────────────────────
echo "--- alembic current == 0072 ---"
CUR="$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- alembic current 2>/dev/null || true)"
echo "  alembic current: ${CUR:-<empty>}"
echo "$CUR" | grep -q "0072" || fail "alembic head is not 0072 (got: ${CUR:-<empty>})"
echo "  OK: alembic at 0072"

# ── 3. Column / type / default / CHECK assertions (via information_schema) ─────
echo "--- mcp_servers + tools columns/types/defaults/CHECK ---"
RESULT="$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 - <<'PY'
import asyncio, sys
from sqlalchemy import text

def check(cond, tid, msg):
    print(f"RESULT {tid} {'PASS' if cond else 'FAIL'} {msg}")
    if not cond:
        sys.exit(1)

async def main():
    from db import AsyncSessionLocal
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(text("""
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_name = 'mcp_servers'
        """))).all()
        cols = {r[0]: (r[1], r[2], r[3]) for r in rows}

        # name -> (data_type substring, is_nullable 'NO'/'YES', default substring or None)
        expected = {
            "identity_mode":          ("character varying", "NO",  "none"),
            "is_external":            ("boolean",           "NO",  "false"),
            "transport_config":       ("jsonb",             "YES", None),
            "health_detail":          ("jsonb",             "NO",  "{}"),
            "list_changed_supported": ("boolean",           "NO",  "false"),
            "scan_results":           ("boolean",           "NO",  "true"),
        }
        for name, (dt, nn, dflt) in expected.items():
            present = name in cols
            check(present, f"CP1B-col-{name}", f"present={present}")
            adt, ann, adflt = cols[name]
            check(dt in adt, f"CP1B-type-{name}", f"data_type={adt} want~{dt}")
            check(ann == nn, f"CP1B-null-{name}", f"is_nullable={ann} want {nn}")
            if dflt is not None:
                ok = adflt is not None and dflt in adflt
                check(ok, f"CP1B-def-{name}", f"default={adflt} want~{dflt}")

        # identity_mode CHECK constraint present
        chk = (await s.execute(text("""
            SELECT cc.check_clause
            FROM information_schema.check_constraints cc
            JOIN information_schema.constraint_column_usage ccu
              ON cc.constraint_name = ccu.constraint_name
             AND cc.constraint_schema = ccu.constraint_schema
            WHERE ccu.table_name = 'mcp_servers' AND ccu.column_name = 'identity_mode'
        """))).all()
        check(len(chk) >= 1, "CP1B-check-identity_mode",
              f"check_clauses={[c[0] for c in chk]}")

        # tools.pii_deanonymize_allowed
        trow = (await s.execute(text("""
            SELECT data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_name = 'tools' AND column_name = 'pii_deanonymize_allowed'
        """))).first()
        check(trow is not None, "CP1B-tools-pii-col", f"present={trow is not None}")
        check(trow[0] == "boolean", "CP1B-tools-pii-type", f"data_type={trow[0]}")
        check(trow[1] == "NO", "CP1B-tools-pii-null", f"is_nullable={trow[1]}")
        check(trow[2] is not None and "false" in trow[2],
              "CP1B-tools-pii-def", f"default={trow[2]}")

    print("ALLPASS")

asyncio.run(main())
PY
)" || { echo "$RESULT"; fail "schema assertions failed"; }
echo "$RESULT"
echo "$RESULT" | grep -q "ALLPASS" || fail "schema assertion block did not complete"

echo "PASS"
