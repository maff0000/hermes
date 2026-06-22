#!/bin/sh
# docker/entrypoint.sh — HARD resource-cap boot gate.
# WO-HERMES-OPS-BOOT-GATE-ENFORCEMENT.
#
# Converts the PR#36 advisory cgroup cap check into an IMMUTABLE execution barrier: the container must
# NEVER boot the application if the effective cgroup v2 caps do not match the configured pins. The cap
# assertion runs first; only on a clean PASS does control exec into the real command (CMD).
#
# Scope: the CONTAINER image only. The bare systemd runtime (which invokes `python main.py` directly,
# not this script) is unaffected. POSIX sh — no bashisms (slim image ships dash as /bin/sh).
set -eu

APP_HOME="${APP_HOME:-/app}"
CAP_VERIFY="${APP_HOME}/ops/staging/resource_cap_verify.py"
GATE_FAIL_RC=101

echo "[BOOT_GATE] resource-cap verification (HARD gate) — asserting cgroup v2 caps vs configured pins ..." >&2

# Cap assertion ONLY (no mock load). Configured caps come from HERMES_CPUS / HERMES_MEM_LIMIT /
# HERMES_PIDS_LIMIT (env). Non-zero exit => GOV-STAGE-CAP-001 (drift/uncapped) / -002 (unreadable or
# unparseable cgroup) / -003 (configured cap absent). Capture rc without tripping `set -e`.
set +e
gate_output="$(python "${CAP_VERIFY}" 2>&1)"
gate_rc=$?
set -e

# Surface the verifier's full diagnostics to stderr regardless of outcome.
printf '%s\n' "${gate_output}" >&2

if [ "${gate_rc}" -ne 0 ]; then
    gov_code="$(printf '%s\n' "${gate_output}" | grep -oE 'GOV-STAGE-CAP-00[123]' | head -n1 || true)"
    [ -n "${gov_code}" ] || gov_code="GOV-STAGE-CAP-UNKNOWN"
    echo "[BOOT_GATE] ABORT — resource-cap verification FAILED (${gov_code}); refusing to boot the" >&2
    echo "[BOOT_GATE] application under a cap violation. Exiting RC=${GATE_FAIL_RC}." >&2
    exit "${GATE_FAIL_RC}"
fi

echo "[BOOT_GATE] PASS — effective caps bound to configured pins; launching application." >&2
exec "$@"
