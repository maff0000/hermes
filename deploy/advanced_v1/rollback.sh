#!/usr/bin/env bash
# Governed HERMES rollback wrapper — restore the previous immutable image with the SAME tracked bundle + env file.
# WO-HELM-HERMES-COMPLETE-TRACKED-RUNTIME-CONFIG-AND-XAU-PARITY-0001.
#
# Rollback preserves the FULL operational contract (DB :3307, canonical Redis routing, all XAU controls, one stream,
# dark expansion) — it only swaps HERMES_IMAGE_REF back to the previous digest (HERMES_ROLLBACK_IMAGE_REF). It does NOT
# roll back migration 025 (migration rollback is a separate, unauthorised authority). NOT executed against production by
# the source WO that introduced it.
#
# Usage: rollback.sh --env-file <governed.env> [--project hermes-dev]
set -euo pipefail
PROJECT="hermes-dev"; ENV_FILE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --env-file) ENV_FILE="$2"; shift 2;;
    --project) PROJECT="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[ -f "${ENV_FILE:-}" ] || { echo "FATAL: --env-file required" >&2; exit 2; }
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; cd "$ROOT"

PREV=$(grep -E '^HERMES_ROLLBACK_IMAGE_REF=' "$ENV_FILE" | head -1 | cut -d= -f2-)
[ -n "$PREV" ] || { echo "FATAL: HERMES_ROLLBACK_IMAGE_REF absent from env file" >&2; exit 3; }
case "$PREV" in *@sha256:*) ;; *) echo "FATAL: rollback target not digest-pinned ($PREV)" >&2; exit 3;; esac

echo "Rolling back HERMES_IMAGE_REF -> $PREV (full operational contract preserved; migration 025 retained)."
# Re-run preflight with the rollback image substituted, then apply the SAME tracked bundle.
TMP_ENV="$(mktemp)"; trap 'rm -f "$TMP_ENV"' EXIT
grep -vE '^HERMES_IMAGE_REF=' "$ENV_FILE" > "$TMP_ENV"
echo "HERMES_IMAGE_REF=$PREV" >> "$TMP_ENV"
bash "$ROOT/deploy/advanced_v1/deploy.sh" --env-file "$TMP_ENV" --project "$PROJECT"
