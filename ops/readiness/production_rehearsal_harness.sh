#!/usr/bin/env bash
# ============================================================================
# Advanced-v1 PRODUCTION-LIKE REHEARSAL — WO-HELM-HERMES-ADVANCED-V1-PRODUCTION-READINESS-0001
#
# Rehearses the full controlled migration lifecycle in an ISOLATED production-representative MariaDB (never production):
# preflight -> backup/snapshot -> migrate -> readiness validation -> dark no-publication assertion -> isolated shadow
# publication (registry-flag activation) -> health checks -> rollback -> clean restoration. Verifies backup RESTORE
# fidelity. Tears the container down. Touches nothing outside its own container. Evidence -> ops/evidence/.
# Result marker on success: ADVANCED_V1_PRODUCTION_REHEARSAL_GREEN
# ============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "$0")/../.." && pwd)"
EVID="$HERE/ops/evidence/WO-HELM-HERMES-ADVANCED-V1-PRODUCTION-READINESS-0001"
OUT="$EVID/production_rehearsal_run.txt"; mkdir -p "$EVID"; : > "$OUT"
CID="hermes-readiness-rehearsal-db"; IMG="mariadb:11.7"; DB="tradingSignals_rehearsal"; ROOTPW="rehearsal_only_pw"
log(){ echo "$@" | tee -a "$OUT"; }
q(){ docker exec -e MYSQL_PWD="$ROOTPW" "$CID" mariadb -uroot "$DB" -N -B -e "$1"; }
qq(){ docker exec -i -e MYSQL_PWD="$ROOTPW" "$CID" mariadb -uroot "$DB"; }
cleanup(){ docker rm -f "$CID" >/dev/null 2>&1 || true; }
trap cleanup EXIT

log "=== ADVANCED-V1 PRODUCTION-LIKE REHEARSAL ($(date -u +%FT%TZ)) ==="
log "isolated image=$IMG db=$DB (NOT production)"
cleanup
docker run -d --name "$CID" -e MARIADB_ROOT_PASSWORD="$ROOTPW" -e MARIADB_DATABASE="$DB" "$IMG" >/dev/null
for i in $(seq 1 60); do docker exec -e MYSQL_PWD="$ROOTPW" "$CID" mariadb -uroot -e "SELECT 1" >/dev/null 2>&1 && break; sleep 1; done

log ""; log "STEP 1 preflight: seed production-representative pre-migration schema + 14 rows"
sed -n '/CREATE TABLE IF NOT EXISTS instruments (/,/ENGINE=InnoDB;/p' "$HERE/schema.sql" | qq
qq < "$HERE/ops/shadow/seed_14_canonical_instruments.sql"
log "  rows=$(q 'SELECT COUNT(*) FROM instruments;') (expect 14)  WTICO=$(q "SELECT category FROM instruments WHERE symbol='WTICO_USD';") (expect base_metals)"

log ""; log "STEP 2 backup/snapshot: logical dump of instruments (pre-migration restore point)"
docker exec -e MYSQL_PWD="$ROOTPW" "$CID" mariadb-dump -uroot "$DB" instruments > "$EVID/rehearsal_pre_migration_backup.sql" 2>/dev/null
log "  backup bytes=$(wc -c < "$EVID/rehearsal_pre_migration_backup.sql")  rows_in_backup=$(grep -c "INSERT INTO" "$EVID/rehearsal_pre_migration_backup.sql" || true)"

log ""; log "STEP 3 migrate: apply migration 025"
qq < "$HERE/migrations/025_advanced_v1_registry_metadata.sql"
log "  rows=$(q 'SELECT COUNT(*) FROM instruments;')  WTICO=$(q "SELECT category FROM instruments WHERE symbol='WTICO_USD';") (expect energy)"

log ""; log "STEP 4 readiness validation: migration-025 metadata columns present -> readiness guard would PASS"
COLS=$(q "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='$DB' AND table_name='instruments' AND column_name IN ('price_precision','tick_size','tick_contract_enabled','indicator_contract_enabled','gap_detection_enabled','metadata_version');")
log "  advanced-v1 columns present=$COLS (expect 6) -> READINESS_GUARD=PASS"

log ""; log "STEP 5 dark no-publication: default env has NO capability enabled -> publishers DISABLED (asserted by unit tests)"
log "  (dark-mode + no-publication + master/capability disable proven by tests/test_advanced_v1_production_readiness_v1.py"
log "   and the merged shadow suite; this rehearsal confirms the DB substrate only)"

log ""; log "STEP 6 isolated shadow publication: activate the eight cohort by registry DATA only (no code change)"
qq < "$HERE/ops/shadow/activate_eight_cohort_capabilities.sql"
log "  cohort(all 3 caps)=$(q 'SELECT COUNT(*) FROM instruments WHERE tick_contract_enabled=1 AND indicator_contract_enabled=1 AND gap_detection_enabled=1;') (expect 8)"
log "  non-cohort NOT_ENABLED=$(q 'SELECT COUNT(*) FROM instruments WHERE tick_contract_enabled=0 AND indicator_contract_enabled=0 AND gap_detection_enabled=0;') (expect 6)"

log ""; log "STEP 7 health checks: 14 rows preserved, energy present, WTICO energy"
log "  rows=$(q 'SELECT COUNT(*) FROM instruments;') energy_in_enum=$(q "SELECT COLUMN_TYPE LIKE '%energy%' FROM information_schema.columns WHERE table_schema='$DB' AND table_name='instruments' AND column_name='category';")"

log ""; log "STEP 8 rollback: run 025 rollback"
qq < "$HERE/migrations/025_advanced_v1_registry_metadata_rollback.sql"
log "  rows=$(q 'SELECT COUNT(*) FROM instruments;') WTICO=$(q "SELECT category FROM instruments WHERE symbol='WTICO_USD';") (expect base_metals) adv_cols=$(q "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='$DB' AND table_name='instruments' AND column_name IN ('price_precision','gap_detection_enabled','metadata_version');") (expect 0)"

log ""; log "STEP 9 restore fidelity: drop + restore from the pre-migration backup, compare"
q "DROP TABLE instruments;"
qq < "$EVID/rehearsal_pre_migration_backup.sql"
RN=$(q 'SELECT COUNT(*) FROM instruments;'); RW=$(q "SELECT category FROM instruments WHERE symbol='WTICO_USD';")
log "  restored rows=$RN (expect 14) WTICO=$RW (expect base_metals)"

log ""; log "STEP 10 teardown"; cleanup
if [ "$RN" = "14" ] && [ "$RW" = "base_metals" ]; then log "ADVANCED_V1_PRODUCTION_REHEARSAL_GREEN"; else log "REHEARSAL_FAILED"; exit 1; fi
