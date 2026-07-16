#!/usr/bin/env bash
# WO-HELM-HERMES-MARKET-HOURS-PREDEPLOY-GATE-HARNESS-0001
# INERT pre-deployment CI check. NOT part of container startup / systemd / compose. Run manually or in CI BEFORE a
# deploy to prove the configured instrument inventory and the governed market-hours config are mutually complete and
# free of the WTICO_USD->ICO_USD phantom-substitution defect. Exit 0 = clean pass; non-zero = fail (see tool exit matrix).
#
# Usage:
#   ops/ci/hermes_predeploy_gate_check.sh <inventory-file> [expected-inventory-file]
# Defaults to the checked-in example inventory (the real 14 instruments) as BOTH configured and expected.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

INVENTORY="${1:-tools/hermes_predeploy_gate.inventory.example}"
EXPECTED="${2:-tools/hermes_predeploy_gate.inventory.example}"

exec python3 -m tools.hermes_predeploy_gate_v1 \
    --inventory-file "$INVENTORY" \
    --config config/market_hours_schedule.v1.json \
    --expected-inventory-file "$EXPECTED"
