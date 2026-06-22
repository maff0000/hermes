#!/bin/sh
# docker/entrypoint.sh — HARD resource-cap boot gate.
# WO-HERMES-OPS-BOOT-GATE-ENFORCEMENT.
#
# Converts the PR#36 advisory cgroup cap check into an IMMUTABLE execution barrier: the container must
# NEVER boot the application if the effective cgroup v2 caps do not match the configured pins. The cap
# assertion runs first; only on a clean PASS does control exec into the real command (CMD).
#
# The verifier's exit code encodes the GOV class (001->1, 002->2, 003->3), so the case below maps the
# failure precisely. Scope: CONTAINER image only — the bare systemd runtime (which runs `python main.py`
# directly, not this script) is unaffected. POSIX sh (slim image ships dash as /bin/sh).
set -eu

echo "[BOOT-GATE] Initializing infrastructure alignment check..."

# Return-code tracker initialised safely left of the conditional operator (works under `set -e`).
RC=0
python3 ops/staging/resource_cap_verify.py || RC=$?

if [ "$RC" -ne 0 ]; then
    echo "=================================================================" >&2
    echo "[CRITICAL FAULT] CONTAINER INFRASTRUCTURE BREAKOUT OR MISMATCH" >&2
    echo "Execution aborted by Guard Gate. Diagnostic Signature Below:" >&2

    case "$RC" in
        1) echo "Diagnostic: GOV-STAGE-CAP-001 (Memory Ceiling Breach / CPU Allocation Mismatch)" >&2 ;;
        2) echo "Diagnostic: GOV-STAGE-CAP-002 (Unparseable or Unreadable Cgroup Core Interface)" >&2 ;;
        3) echo "Diagnostic: GOV-STAGE-CAP-003 (Missing Compose Specification Parameters)" >&2 ;;
        *) echo "Diagnostic: GOV-STAGE-CAP-UNKNOWN (Unexpected Verifier Exit Code: $RC)" >&2 ;;
    esac

    echo "=================================================================" >&2
    exit 101
fi

echo "[BOOT-GATE] Verification passed cleanly. Handing off to runtime..."
exec "$@"
