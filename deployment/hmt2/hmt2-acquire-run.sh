#!/usr/bin/env bash
# HMT-2 ACQUISITION launcher -- ports Trinity's /usr/local/sbin/hmt2-acquire-run.sh.
#
# Places the given command inside the HMT-2 containment slice via systemd-run, running as the
# acquisition identity (never the canonical compute identity) with the acquire-secrets
# supplementary group active, so ONLY acquisition invocations through THIS launcher can read the
# Databento credential file. The canonical compute identity is never a member of that
# supplementary group and stays permanently unable to read it.
#
# CRITICAL, empirically-verified detail -- DO NOT "simplify" this to match hmt2-run.sh's shape:
# this launcher runs as a transient SERVICE unit (`--unit=`, never `--scope`). Only service
# units apply `SupplementaryGroups=` via systemd's exec framework: a `--scope` invocation as the
# acquisition identity was verified to come back with the base group only (no secret access),
# while `--unit=` + `SupplementaryGroups=` came back with the secrets group correctly present.
# `--wait --pipe` are required for a service unit (unlike `--scope`) so this launcher's caller
# still sees the child's real stdout/stderr/exit code. This is WHY the two launchers use
# different systemd-run invocation shapes -- see hmt2-run.sh's own header for the other half.
#
# Every host-specific value is read from the config env file below -- NEVER hardcoded here.
set -euo pipefail

HMT2_OPS_ENV_FILE="${HMT2_OPS_ENV_FILE:-/etc/hmt2/hmt2-ops.env}"
if [ -f "${HMT2_OPS_ENV_FILE}" ]; then
  # shellcheck disable=SC1090
  source "${HMT2_OPS_ENV_FILE}"
fi

: "${HMT2_SLICE_NAME:?HMT2_SLICE_NAME must be set (via ${HMT2_OPS_ENV_FILE} or the environment)}"
: "${HMT2_DATA_UID:?HMT2_DATA_UID must be set}"
: "${HMT2_DATA_GID:?HMT2_DATA_GID must be set}"
: "${HMT2_ACQUIRE_SECRETS_GID:?HMT2_ACQUIRE_SECRETS_GID must be set}"
: "${HMT2_SCRATCH_DIR:?HMT2_SCRATCH_DIR must be set}"
: "${HMT2_ACQUIRE_SECRET_PATH:?HMT2_ACQUIRE_SECRET_PATH must be set}"

if [ "$#" -eq 0 ]; then
  echo "usage: hmt2-acquire-run.sh <command> [args...]" >&2
  exit 2
fi

unit_name="hmt2-acquire-$(date -u +%Y%m%dT%H%M%SZ)-$$"

exec systemd-run \
  --quiet \
  --unit="${unit_name}" \
  --slice="${HMT2_SLICE_NAME}" \
  --uid="${HMT2_DATA_UID}" \
  --gid="${HMT2_DATA_GID}" \
  --property=SupplementaryGroups="${HMT2_ACQUIRE_SECRETS_GID}" \
  --same-dir \
  --collect \
  --wait \
  --pipe \
  --setenv=TMPDIR="${HMT2_SCRATCH_DIR}" \
  --setenv=DATABENTO_HISTORICAL_API_KEY_PATH="${HMT2_ACQUIRE_SECRET_PATH}" \
  -- "$@"
