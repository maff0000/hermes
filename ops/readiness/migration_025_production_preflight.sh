#!/usr/bin/env bash
# ============================================================================
# Migration-025 PRODUCTION PREFLIGHT (READ-ONLY) — WO-HELM-HERMES-ADVANCED-V1-PRODUCTION-READINESS-0001
#
# Inspects the LIVE production instruments SSOT read-only (SELECT / information_schema ONLY) via the production
# container's OWN pymysql + governed DB config (the container ships no mysql/mariadb CLI). Performs NO mutation.
# Credentials never leave the container and are never echoed/written to evidence. Emits JSON evidence.
# Run: bash ops/readiness/migration_025_production_preflight.sh
# ============================================================================
set -euo pipefail
DELL="root@192.168.11.10"; C="e9e72efd4ddd"
HERE="$(cd "$(dirname "$0")/../.." && pwd)"
EVID="$HERE/ops/evidence/WO-HELM-HERMES-ADVANCED-V1-PRODUCTION-READINESS-0001"
OUT="$EVID/migration_025_production_preflight.json"; mkdir -p "$EVID"

read -r -d '' PYSRC <<'PY' || true
import json
from env_config import get_db_config
import pymysql
cfg = get_db_config()
cx = pymysql.connect(host=cfg['host'], port=cfg['port'], user=cfg['user'], password=cfg['password'], database=cfg['database'])
cur = cx.cursor()
def one(q):
    cur.execute(q); r = cur.fetchone(); return r[0] if r else None
out = {}
out['rowcount'] = one('SELECT COUNT(*) FROM instruments')
cur.execute('SELECT symbol FROM instruments ORDER BY symbol'); out['symbols'] = [r[0] for r in cur.fetchall()]
ct = one("SELECT COLUMN_TYPE FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='instruments' AND column_name='category'") or ''
out['category_enum_has_energy'] = 'energy' in ct
out['wtico_category'] = one("SELECT category FROM instruments WHERE symbol='WTICO_USD'")
out['adv_v1_cols_present'] = one("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='instruments' AND column_name IN ('price_precision','tick_size','tick_contract_enabled','indicator_contract_enabled','gap_detection_enabled','metadata_version')")
cur.execute("SELECT engine,table_rows FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='instruments'"); out['engine_rows'] = list(cur.fetchone())
cx.close()
print("PREFLIGHT_JSON=" + json.dumps(out, default=str))
PY

B64=$(printf '%s' "$PYSRC" | base64 -w0)
RES=$(ssh "$DELL" "echo $B64 | base64 -d | docker exec -i $C python3 -" 2>/dev/null | grep '^PREFLIGHT_JSON=' | sed 's/^PREFLIGHT_JSON=//')
[ -n "$RES" ] || { echo "preflight produced no JSON (check container/DB availability)"; exit 1; }
echo "$RES" > "$OUT"
echo "=== MIGRATION-025 PRODUCTION PREFLIGHT (READ-ONLY) — production $C ==="
echo "$RES" | python3 -m json.tool
echo "written: $OUT"
echo
echo "NOTES: migration 025 = ADD COLUMN IF NOT EXISTS + ENUM MODIFY + targeted UPDATEs (additive/idempotent; no row/table"
echo "delete). Registry loader uses an explicit column list (no SELECT *), so appended columns do not shift consumers."
echo "Expect a brief sub-second metadata lock on the 14-row InnoDB table. Rollback: 025_..._rollback.sql."
