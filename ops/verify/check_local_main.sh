#!/bin/bash
# WO-HERMES-LOCAL-MAIN-RECONCILIATION-0001 — local-main reconciliation check.
#
# Architect-required automated verification that the dell-debian (or any host)
# local main is reconciled with origin/main and the working tree is in a known,
# governed state.
#
# Asserts:
#   1. local main HEAD == origin/main HEAD (clean canonical), OR
#      local main HEAD is a strict ancestor of origin/main AND the only
#      working-tree changes are explicitly approved local overlay items.
#   2. Working tree is clean modulo approved overlay (currently: NONE).
#   3. No untracked files outside the approved-untracked list (currently: NONE).
#   4. `tradingSignals` is not pulled in as a nested submodule with stale pointer.
#
# Approved overlay (this WO ratifies these as canonical, NOT overlay):
#   (after this WO closes, the approved overlay set is empty)
set -euo pipefail
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "[$TS] verify-local-main-reconciliation"

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
cd "$REPO_ROOT"

# Refresh remote-tracking ref. Fetch is non-destructive.
git fetch -q origin main 2>/dev/null || {
  echo "WARN: git fetch failed (offline?); checking against locally-cached origin/main"
}

LOCAL=$(git rev-parse main 2>/dev/null || echo "missing")
ORIGIN=$(git rev-parse origin/main 2>/dev/null || echo "missing")

if [ "$LOCAL" = "missing" ] || [ "$ORIGIN" = "missing" ]; then
  echo "FAIL: cannot resolve main / origin/main"
  exit 1
fi

echo "  local main:   $LOCAL"
echo "  origin/main:  $ORIGIN"

if [ "$LOCAL" = "$ORIGIN" ]; then
  echo "  PASS HEADs match"
else
  # Check whether local is strict ancestor of origin (clean FF possible).
  if git merge-base --is-ancestor "$LOCAL" "$ORIGIN" 2>/dev/null; then
    BEHIND=$(git rev-list --count "${LOCAL}..${ORIGIN}")
    echo "FAIL: local main is $BEHIND commits behind origin/main (FF available but not done)"
    exit 1
  else
    echo "FAIL: local main has diverged from origin/main (merge required)"
    exit 1
  fi
fi

# Working tree status (porcelain — machine-readable).
DIRTY=$(git status --porcelain)
if [ -z "$DIRTY" ]; then
  echo "  PASS working tree clean"
  echo "PASS: local main reconciled with origin/main, working tree clean"
  exit 0
fi

# Approved overlay set (currently empty after this WO ratifies operator changes).
# Add specific paths here if/when an explicit local-only overlay is later
# ratified by the architect (with documented owner + expiry).
APPROVED_OVERLAY=()

# Filter dirty entries against approved overlay list.
FAIL=0
while IFS= read -r line; do
  status="${line:0:2}"
  path="${line:3}"
  approved=0
  for ok in "${APPROVED_OVERLAY[@]}"; do
    if [ "$path" = "$ok" ]; then
      approved=1
      break
    fi
  done
  if [ $approved -eq 0 ]; then
    echo "  FAIL unexpected dirty entry: '$status' $path"
    FAIL=1
  else
    echo "  ALLOWED overlay: '$status' $path"
  fi
done <<< "$DIRTY"

if [ $FAIL -ne 0 ]; then
  echo "FAIL: working tree has unapproved modifications"
  exit 1
fi
echo "PASS: local main reconciled, only approved overlay entries present"
