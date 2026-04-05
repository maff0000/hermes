#!/bin/bash
# HERMES Soak Monitor — WO-HERMES-PROOF-OF-WORKING-0006
# Runs via cron every 5 minutes. Captures health snapshot to log file.
# Setup: */5 * * * * /srv-dev/tradingSignals/ops/soak_monitor.sh

EVIDENCE_DIR="/srv-dev/tradingSignals/ops/evidence/WO-HERMES-PROOF-OF-WORKING-0006/soak"
LOG_FILE="${EVIDENCE_DIR}/soak_log.jsonl"
HEALTH_URL="http://localhost:8211/health"
DB_PW=$(grep DEV_DB_PASSWORD /srv-dev/tradingSignals/.env | cut -d= -f2)

# Get health snapshot
HEALTH=$(curl -s --max-time 5 "$HEALTH_URL" 2>/dev/null)
if [ -z "$HEALTH" ]; then
    HEALTH='{"error":"health_endpoint_unreachable"}'
fi

# Get latest M1 timestamp
LATEST_M1=$(mysql -h 127.0.0.1 -P 3307 -u root -p"$DB_PW" tradingSignals -N -e \
    "SELECT MAX(timestamp) FROM candles_M1 WHERE instrument='XAU_USD'" 2>/dev/null)

# Get unresolved gap count
GAP_COUNT=$(mysql -h 127.0.0.1 -P 3307 -u root -p"$DB_PW" tradingSignals -N -e \
    "SELECT COUNT(*) FROM hermes_data_gaps WHERE status NOT IN ('RESOLVED','INVALIDATED')" 2>/dev/null)

# Get open incident count
INCIDENT_COUNT=$(mysql -h 127.0.0.1 -P 3307 -u root -p"$DB_PW" tradingSignals -N -e \
    "SELECT COUNT(*) FROM hermes_incidents WHERE status IN ('OPEN','RESOLVING')" 2>/dev/null)

# Build snapshot
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
echo "{\"ts\":\"$TIMESTAMP\",\"health\":$HEALTH,\"latest_m1\":\"$LATEST_M1\",\"unresolved_gaps\":$GAP_COUNT,\"open_incidents\":$INCIDENT_COUNT}" >> "$LOG_FILE"
