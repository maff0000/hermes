#!/usr/bin/env bash
# HMT-2 canonical-compute launcher -- ports Trinity's /usr/local/sbin/hmt2-run.sh.
#
# Places the given command inside the HMT-2 containment slice via systemd-run, running as the
# canonical compute identity, matching the shape
# market_truth.acquisition.hmt2_slice_guard.require_hmt2_slice() checks for (a scope/service
# nested under the slice).
#
# Every host-specific value (slice name, compute uid/gid, TMPDIR, canonical research root) is
# read from the config env file below -- NEVER hardcoded here -- so this exact script is
# deployable byte-for-byte identical on every host; only the env file differs per host. See
# deployment/hmt2/hmt2-ops.env.example and deployment/hmt2/README.md.
set -euo pipefail

HMT2_OPS_ENV_FILE="${HMT2_OPS_ENV_FILE:-/etc/hmt2/hmt2-ops.env}"
if [ -f "${HMT2_OPS_ENV_FILE}" ]; then
  # shellcheck disable=SC1090
  source "${HMT2_OPS_ENV_FILE}"
fi

: "${HMT2_SLICE_NAME:?HMT2_SLICE_NAME must be set (via ${HMT2_OPS_ENV_FILE} or the environment)}"
: "${HMT2_COMPUTE_UID:?HMT2_COMPUTE_UID must be set}"
: "${HMT2_COMPUTE_GID:?HMT2_COMPUTE_GID must be set}"
: "${HMT2_SCRATCH_DIR:?HMT2_SCRATCH_DIR must be set}"
: "${HMT2_CANONICAL_RESEARCH_ROOT:?HMT2_CANONICAL_RESEARCH_ROOT must be set}"

if [ "$#" -eq 0 ]; then
  echo "usage: hmt2-run.sh <command> [args...]" >&2
  exit 2
fi

exec systemd-run --scope --slice="${HMT2_SLICE_NAME}" --uid="${HMT2_COMPUTE_UID}" --gid="${HMT2_COMPUTE_GID}" \
    --setenv=TMPDIR="${HMT2_SCRATCH_DIR}" \
    --setenv=HMT2_CANONICAL_RESEARCH_ROOT="${HMT2_CANONICAL_RESEARCH_ROOT}" \
    -- "$@"
