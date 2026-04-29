#!/bin/bash
# WO-TRADING-SIGNALS-UTCNOW-CLEANUP-0001 — hermes-canary check.
# Asserts hermes-canary.service is active and emitting GREEN health states.
set -euo pipefail
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "[$TS] verify-trading-signals-canary"

ACTIVE=$(systemctl is-active hermes-canary.service 2>/dev/null || true)
ACTIVE=${ACTIVE:-unknown}
if [ "$ACTIVE" != "active" ]; then
  echo "FAIL: hermes-canary.service is $ACTIVE (expected active)"
  exit 1
fi

# Most recent canary line (last minute).
LAST=$(journalctl -u hermes-canary.service --since "2 minutes ago" --no-pager 2>/dev/null | tail -1)
if [ -z "$LAST" ]; then
  echo "FAIL: hermes-canary silent in last 2 minutes"
  exit 1
fi

# Extract health_state from JSON line.
HEALTH=$(echo "$LAST" | python3 -c "
import sys, json, re
line = sys.stdin.read().strip()
# strip systemd prefix up to first '{'
i = line.find('{')
if i < 0:
    print('NO_JSON')
    sys.exit(0)
try:
    obj = json.loads(line[i:])
    print(obj.get('health_state', 'NO_FIELD'))
except Exception as e:
    print(f'PARSE_ERR_{e}')
" 2>/dev/null || echo "ERR")

if [ "$HEALTH" = "GREEN" ]; then
  echo "PASS: hermes-canary GREEN"
else
  echo "FAIL: hermes-canary health_state=$HEALTH (expected GREEN)"
  exit 1
fi
