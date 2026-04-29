#!/bin/bash
# WO-TRADING-SIGNALS-UTCNOW-CLEANUP-0001 — runtime check.
# Asserts signal-service-dev.service is active+enabled and recent ticks are
# being ingested (proof that the post-cleanup runtime is alive).
set -euo pipefail
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "[$TS] verify-trading-signals-runtime"

ACTIVE=$(systemctl is-active signal-service-dev.service 2>/dev/null || true)
ENABLED=$(systemctl is-enabled signal-service-dev.service 2>/dev/null || true)
ACTIVE=${ACTIVE:-unknown}
ENABLED=${ENABLED:-unknown}

if [ "$ACTIVE" != "active" ]; then
  echo "FAIL: signal-service-dev.service is $ACTIVE (expected active)"
  exit 1
fi
if [ "$ENABLED" != "enabled" ]; then
  echo "FAIL: signal-service-dev.service is $ENABLED (expected enabled)"
  exit 1
fi

# Recent tick / signal log line within the last 5 minutes.
RECENT=$(journalctl -u signal-service-dev.service --since "5 minutes ago" --no-pager 2>/dev/null | wc -l || echo 0)
if [ "${RECENT:-0}" -lt 5 ]; then
  echo "FAIL: signal-service-dev journal nearly silent in last 5 minutes (${RECENT} lines)"
  exit 1
fi

echo "PASS: signal-service-dev active+enabled, ${RECENT} log lines in last 5 minutes"
