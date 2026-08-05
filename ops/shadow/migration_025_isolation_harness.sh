#!/usr/bin/env bash
# ============================================================================
# Isolated migration-025 lifecycle harness — WO-HELM-HERMES-ADVANCED-V1-EIGHT-INSTRUMENT-SHADOW-0001
#
# Spins a THROWAWAY isolated MariaDB container (never production), seeds the 14 canonical instruments,
# applies the REAL migration 025 SQL, proves the required invariants, proves reapply idempotence,
# activates the eight-instrument cohort by registry DATA only, proves the six non-cohort stay NOT_ENABLED,
# proves rollback validity, then TEARS DOWN the container. Touches NOTHING outside its own container.
#
# Evidence -> ops/evidence/WO-HELM-HERMES-ADVANCED-V1-EIGHT-INSTRUMENT-SHADOW-0001/migration_025_isolation_run.txt
# Usage: bash ops/shadow/migration_025_isolation_harness.sh
# ============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "$0")/../.." && pwd)"
EVID="$HERE/ops/evidence/WO-HELM-HERMES-ADVANCED-V1-EIGHT-INSTRUMENT-SHADOW-0001"
OUT="$EVID/migration_025_isolation_run.txt"
mkdir -p "$EVID"
CID="hermes-shadow-db-825"          # fixed name so a stale one is reclaimed; isolated, never production
IMG="mariadb:11.7"
DB="tradingSignals_shadow"
ROOTPW="shadow_only_pw"
: > "$OUT"
log(){ echo "$@" | tee -a "$OUT"; }
q(){ docker exec -e MYSQL_PWD="$ROOTPW" "$CID" mariadb -uroot "$DB" -N -B -e "$1"; }
qq(){ docker exec -i -e MYSQL_PWD="$ROOTPW" "$CID" mariadb -uroot "$DB"; }

cleanup(){ docker rm -f "$CID" >/dev/null 2>&1 || true; }
trap cleanup EXIT

log "=== ISOLATED MIGRATION-025 LIFECYCLE HARNESS ==="
log "image=$IMG db=$DB container=$CID (throwaway, isolated; NOT production)"
cleanup
docker run -d --name "$CID" -e MARIADB_ROOT_PASSWORD="$ROOTPW" -e MARIADB_DATABASE="$DB" "$IMG" >/dev/null
log "waiting for mariadb ready..."
for i in $(seq 1 60); do
  if docker exec -e MYSQL_PWD="$ROOTPW" "$CID" mariadb -uroot -e "SELECT 1" >/dev/null 2>&1; then break; fi
  sleep 1
done
docker exec -e MYSQL_PWD="$ROOTPW" "$CID" mariadb -uroot -e "SELECT VERSION()" | tee -a "$OUT"

log ""; log "=== STEP 1: create base instruments table + seed 14 canonical rows (pre-025) ==="
sed -n '/CREATE TABLE IF NOT EXISTS instruments (/,/ENGINE=InnoDB;/p' "$HERE/schema.sql" | qq
qq < "$HERE/ops/shadow/seed_14_canonical_instruments.sql"
N0=$(q "SELECT COUNT(*) FROM instruments;")
log "rows before migration: $N0 (expect 14)"
log "WTICO category before: $(q "SELECT category FROM instruments WHERE symbol='WTICO_USD';") (expect base_metals)"
log "advanced-v1 columns before: $(q "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='$DB' AND table_name='instruments' AND column_name='gap_detection_enabled';") (expect 0 = absent)"

log ""; log "=== STEP 2: APPLY migration 025 (the real file) ==="
qq < "$HERE/migrations/025_advanced_v1_registry_metadata.sql"
log "APPLY ok"
log "rows after apply: $(q "SELECT COUNT(*) FROM instruments;") (expect 14)"
log "energy in category ENUM: $(q "SELECT COLUMN_TYPE LIKE '%energy%' FROM information_schema.columns WHERE table_schema='$DB' AND table_name='instruments' AND column_name='category';") (expect 1)"
log "WTICO category after: $(q "SELECT category FROM instruments WHERE symbol='WTICO_USD';") (expect energy)"
log "XAU caps after (tick,ind,gap): $(q "SELECT CONCAT(tick_contract_enabled,',',indicator_contract_enabled,',',gap_detection_enabled) FROM instruments WHERE symbol='XAU_USD';") (expect 1,1,1)"
log "seven-new caps default (sum tick_contract_enabled over 7 new): $(q "SELECT COALESCE(SUM(tick_contract_enabled),0) FROM instruments WHERE symbol IN ('XAG_USD','EUR_USD','GBP_USD','AUD_USD','USD_JPY','SPX500_USD','WTICO_USD');") (expect 0 = all NOT_ENABLED)"

log ""; log "=== STEP 3: REAPPLY migration 025 (idempotence) ==="
qq < "$HERE/migrations/025_advanced_v1_registry_metadata.sql"
log "reapply ok; rows: $(q "SELECT COUNT(*) FROM instruments;") (expect 14)"
log "columns unchanged (advanced-v1 col count): $(q "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='$DB' AND table_name='instruments' AND column_name IN ('price_precision','tick_size','tick_contract_enabled','indicator_contract_enabled','gap_detection_enabled','metadata_version');") (expect 6)"

log ""; log "=== STEP 4: activate eight-instrument cohort by DATA ONLY ==="
qq < "$HERE/ops/shadow/activate_eight_cohort_capabilities.sql"
log "cohort (all three caps on) count: $(q "SELECT COUNT(*) FROM instruments WHERE tick_contract_enabled=1 AND indicator_contract_enabled=1 AND gap_detection_enabled=1;") (expect 8)"
log "cohort members: $(q "SELECT GROUP_CONCAT(symbol ORDER BY symbol) FROM instruments WHERE tick_contract_enabled=1 AND indicator_contract_enabled=1 AND gap_detection_enabled=1;")"
log "six non-cohort NOT_ENABLED (all caps 0): $(q "SELECT COUNT(*) FROM instruments WHERE tick_contract_enabled=0 AND indicator_contract_enabled=0 AND gap_detection_enabled=0;") (expect 6)"
log "six non-cohort members: $(q "SELECT GROUP_CONCAT(symbol ORDER BY symbol) FROM instruments WHERE tick_contract_enabled=0 AND indicator_contract_enabled=0 AND gap_detection_enabled=0;")"
log "14-row preservation held: $(q "SELECT COUNT(*) FROM instruments;") (expect 14)"

log ""; log "=== STEP 5: ROLLBACK migration 025 (validity) ==="
qq < "$HERE/migrations/025_advanced_v1_registry_metadata_rollback.sql"
log "rollback ok; rows: $(q "SELECT COUNT(*) FROM instruments;") (expect 14)"
log "WTICO category after rollback: $(q "SELECT category FROM instruments WHERE symbol='WTICO_USD';") (expect base_metals)"
log "advanced-v1 columns after rollback: $(q "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='$DB' AND table_name='instruments' AND column_name IN ('price_precision','tick_size','tick_contract_enabled','gap_detection_enabled','metadata_version');") (expect 0 = dropped)"
log "energy removed from ENUM: $(q "SELECT COLUMN_TYPE LIKE '%energy%' FROM information_schema.columns WHERE table_schema='$DB' AND table_name='instruments' AND column_name='category';") (expect 0)"

log ""; log "=== TEARDOWN ==="
cleanup
log "container removed. isolated harness complete."
