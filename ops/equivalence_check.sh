#!/bin/bash
# Equivalence Check — WO-HERMES-PROOF-H
# Runs via cron every hour. Compares canonical_m1 vs candles_M1 OHLC.
# Setup: 0 * * * * /srv-dev/tradingSignals/ops/equivalence_check.sh

EVIDENCE_DIR="/srv-dev/tradingSignals/ops/evidence/WO-HERMES-PROOF-H"
LOG_FILE="${EVIDENCE_DIR}/equivalence_log.jsonl"
PW=$(grep DEV_DB_PASSWORD /srv-dev/tradingSignals/.env | cut -d= -f2)

mkdir -p "$EVIDENCE_DIR"

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# M1 equivalence: canonical_m1 vs candles_M1 for last hour
M1_RESULT=$(mysql -h 127.0.0.1 -P 3307 -u root -p"${PW}" tradingSignals -N -e "
SELECT
    COUNT(*) as total,
    SUM(CASE WHEN ABS(cm.open - c.open) < 0.001
              AND ABS(cm.high - c.high) < 0.001
              AND ABS(cm.low - c.low) < 0.001
              AND ABS(cm.close - c.close) < 0.001
         THEN 1 ELSE 0 END) as matches
FROM canonical_m1 cm
JOIN candles_M1 c ON cm.instrument = c.instrument AND cm.minute_bucket_utc = c.timestamp
WHERE cm.minute_bucket_utc >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 HOUR)
" 2>/dev/null)

M1_TOTAL=$(echo "$M1_RESULT" | awk '{print $1}')
M1_MATCHES=$(echo "$M1_RESULT" | awk '{print $2}')
M1_TOTAL=${M1_TOTAL:-0}
M1_MATCHES=${M1_MATCHES:-0}

M1_PCT=$(python3 -c "print(round(${M1_MATCHES} * 100 / max(${M1_TOTAL},1), 2))")

# Health check
HEALTH=$(curl -s --max-time 5 http://localhost:8211/health 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('health_state','UNKNOWN'))" 2>/dev/null || echo "UNREACHABLE")

# Canonical M1 row count for last hour
CM1_COUNT=$(mysql -h 127.0.0.1 -P 3307 -u root -p"${PW}" tradingSignals -N -e "
SELECT COUNT(*) FROM canonical_m1 WHERE minute_bucket_utc >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 HOUR)
" 2>/dev/null)
CM1_COUNT=${CM1_COUNT:-0}

echo "{\"ts\":\"$TIMESTAMP\",\"m1_total\":$M1_TOTAL,\"m1_matches\":$M1_MATCHES,\"m1_pct\":$M1_PCT,\"canonical_m1_count\":$CM1_COUNT,\"health\":\"$HEALTH\"}" >> "$LOG_FILE"
