#!/usr/bin/env bash
# HMT-2 disk capacity guard -- ports Trinity's /usr/local/sbin/hmt2-disk-guard.sh.
#
# Monitoring-only: warns at a configurable threshold, logs a STOP-severity message via logger(1)
# at a configurable higher threshold. Never itself halts any process -- the canonical
# orchestrator's own disk_pct() pre-session check (market_truth.acquisition.orchestration.host_guards)
# is the actual enforcement point; this script is the independent, timer-driven early-warning
# complement to it, watching a filesystem that may be shared with other, non-HMT-2 workloads.
#
# All values (mount point, thresholds) come from the config env file -- never hardcoded here.
set -euo pipefail

HMT2_OPS_ENV_FILE="${HMT2_OPS_ENV_FILE:-/etc/hmt2/hmt2-ops.env}"
if [ -f "${HMT2_OPS_ENV_FILE}" ]; then
  # shellcheck disable=SC1090
  source "${HMT2_OPS_ENV_FILE}"
fi

MOUNT="${HMT2_DISK_GUARD_MOUNT:-/srv}"
WARN_PCT="${HMT2_DISK_GUARD_WARN_PCT:-70}"
STOP_PCT="${HMT2_DISK_GUARD_STOP_PCT:-80}"
LOG_TAG="hmt2-disk-guard"

USE_PCT=$(df --output=pcent "$MOUNT" | tail -1 | tr -dc '0-9')

if [ "$USE_PCT" -ge "$STOP_PCT" ]; then
    logger -t "$LOG_TAG" -p daemon.crit -- "STOP: ${MOUNT} at ${USE_PCT}% (>= ${STOP_PCT}% stop threshold) -- HMT-2 must not start new sessions until freed"
    echo "STOP: ${MOUNT} at ${USE_PCT}%"
elif [ "$USE_PCT" -ge "$WARN_PCT" ]; then
    logger -t "$LOG_TAG" -p daemon.warning -- "WARN: ${MOUNT} at ${USE_PCT}% (>= ${WARN_PCT}% warn threshold)"
    echo "WARN: ${MOUNT} at ${USE_PCT}%"
else
    logger -t "$LOG_TAG" -p daemon.info -- "OK: ${MOUNT} at ${USE_PCT}%"
fi
