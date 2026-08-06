#!/usr/bin/env bash
# Governed HERMES deployment wrapper — the ONE authorised way to apply the tracked dark+operational bundle.
# WO-HELM-HERMES-COMPLETE-TRACKED-RUNTIME-CONFIG-AND-XAU-PARITY-0001.
#
# Runs the full-contract preflight, renders the safe effective config, verifies the immutable image, and applies the
# EXACT tracked compose sequence with ONE governed env file — never the untracked dev-override, never an extra overlay.
# This wrapper is NOT executed against production by the source WO that introduced it; a deployment WO invokes it.
#
# Usage: deploy.sh --env-file <governed.env> [--project hermes-dev] [--dry-run]
set -euo pipefail

PROJECT="hermes-dev"
ENV_FILE=""
DRY_RUN="false"
while [ $# -gt 0 ]; do
  case "$1" in
    --env-file) ENV_FILE="$2"; shift 2;;
    --project) PROJECT="$2"; shift 2;;
    --dry-run) DRY_RUN="true"; shift;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[ -n "$ENV_FILE" ] || { echo "FATAL: --env-file <governed.env> required" >&2; exit 2; }
[ -f "$ENV_FILE" ] || { echo "FATAL: env file not found: $ENV_FILE" >&2; exit 2; }

# Resolve project root (two levels up from this script: deploy/advanced_v1/ -> repo root).
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

COMPOSE=(-f docker-compose.yml
         -f deploy/advanced_v1/docker-compose.operational.yml
         -f deploy/advanced_v1/docker-compose.dark.yml)

echo "=== [1/4] full-contract preflight (fail-closed) ==="
python3 -c "
import sys, os
sys.path.insert(0, '$ROOT')
from deploy.advanced_v1 import runtime_config_preflight_v1 as pf
env = {}
for line in open('$ENV_FILE'):
    line=line.strip()
    if not line or line.startswith('#') or '=' not in line: continue
    k,v=line.split('=',1); env[k]=v
ok, faults, safe = pf.validate_runtime_config(env)
import json; print('SAFE EFFECTIVE CONFIG:', json.dumps(safe, indent=2))
if not ok:
    print('PREFLIGHT FAILED:'); [print('  -',f) for f in faults]; sys.exit(3)
print('PREFLIGHT OK')
"

echo "=== [2/4] verify immutable image reference ==="
IMG=$(grep -E '^HERMES_IMAGE_REF=' "$ENV_FILE" | head -1 | cut -d= -f2-)
case "$IMG" in
  *@sha256:*) echo "image pinned by digest: OK";;
  hermes-signal:prod-*) echo "governed prod- tag: OK";;
  *) echo "FATAL: image not immutably pinned ($IMG)" >&2; exit 3;;
esac

echo "=== [3/4] record artefact checksums ==="
sha256sum docker-compose.yml deploy/advanced_v1/docker-compose.operational.yml deploy/advanced_v1/docker-compose.dark.yml

echo "=== [4/4] apply (project=$PROJECT) ==="
if [ "$DRY_RUN" = "true" ]; then
  echo "DRY-RUN: docker compose -p $PROJECT ${COMPOSE[*]} --env-file $ENV_FILE config"
  docker compose -p "$PROJECT" "${COMPOSE[@]}" --env-file "$ENV_FILE" config >/dev/null && echo "compose config renders OK (dry-run)"
else
  docker compose -p "$PROJECT" "${COMPOSE[@]}" --env-file "$ENV_FILE" up -d hermes-signal
fi
