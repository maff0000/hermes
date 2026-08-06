#!/usr/bin/env bash
# ============================================================================
# Governed production-candidate build wrapper — WO-HELM-HERMES-DEPLOYED-PROVENANCE-...-0001
#
# Deterministically stamps the immutable Git SOURCE_SHA into the image (OCI revision label + ENV consumed by
# build_identity()/ /buildinfo). Fail-closed: refuses to build a dirty/ambiguous/mismatched source. Verifies the built
# image's OCI revision == SOURCE_SHA and emits a reproducible SHA->digest evidence file. This is the ONLY governed way
# to build a production candidate; a bare `docker compose build` (no SOURCE_SHA) yields UNKNOWN_SOURCE_SHA and MUST NOT
# be promoted. This wrapper does NOT deploy or promote.
#
# Usage: ops/build/build_production_candidate.sh [<checkout_dir>]   (default: git top-level of CWD)
# ============================================================================
set -euo pipefail
CHECKOUT="${1:-$(git rev-parse --show-toplevel)}"
cd "$CHECKOUT"

# 1) source identity — fail closed on dirty tree / invalid SHA
DIRTY=$(git status --porcelain --untracked-files=no | wc -l)
if [ "$DIRTY" -ne 0 ]; then echo "FAIL: working tree has $DIRTY tracked change(s); refuse to build ambiguous source" >&2; exit 2; fi
SOURCE_SHA=$(git rev-parse HEAD)
if ! printf '%s' "$SOURCE_SHA" | grep -Eq '^[0-9a-f]{40}$'; then echo "FAIL: SOURCE_SHA not 40-hex: $SOURCE_SHA" >&2; exit 2; fi
BUILD_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)
IMAGE_TAG="prod-${SOURCE_SHA:0:12}"
export SOURCE_SHA BUILD_UTC HERMES_IMAGE_TAG="$IMAGE_TAG" HERMES_IMAGE_REF="hermes-signal:${IMAGE_TAG}"

echo "=== governed build: SOURCE_SHA=$SOURCE_SHA BUILD_UTC=$BUILD_UTC tag=$IMAGE_TAG ==="

# 2) build (compose passes SOURCE_SHA/BUILD_UTC/HERMES_IMAGE_REF as build args -> Dockerfile ARG -> LABEL + ENV)
docker compose build hermes-signal

# 3) verify OCI revision label == SOURCE_SHA (fail-closed)
IMG="hermes-signal:${IMAGE_TAG}"
REV=$(docker inspect "$IMG" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
DIGEST=$(docker inspect "$IMG" --format '{{.Id}}')
if [ "$REV" != "$SOURCE_SHA" ]; then echo "FAIL: OCI revision '$REV' != SOURCE_SHA '$SOURCE_SHA'" >&2; exit 3; fi

# 4) emit reproducible SHA->digest evidence (governed off-git evidence location; NO secrets)
EVID_DIR="${HERMES_BUILD_EVIDENCE_DIR:-/srv/backup/build_evidence}"
mkdir -p "$EVID_DIR"
DF_SHA=$(sha256sum Dockerfile | cut -d' ' -f1)
LOCK_SHA=$( [ -f requirements.txt ] && sha256sum requirements.txt | cut -d' ' -f1 || echo none )
COMPOSE_SHA=$(sha256sum docker-compose.yml | cut -d' ' -f1)
EVID="$EVID_DIR/hermes_build_${IMAGE_TAG}_$(date -u +%Y%m%dT%H%M%SZ).json"
cat > "$EVID" <<JSON
{"application":"HERMES","source_sha":"$SOURCE_SHA","build_utc":"$BUILD_UTC","image_tag":"$IMAGE_TAG",
 "image_digest":"$DIGEST","oci_revision":"$REV","dockerfile_sha256":"$DF_SHA","dependency_lock_sha256":"$LOCK_SHA",
 "compose_sha256":"$COMPOSE_SHA","build_contract_version":"v1"}
JSON
echo "SOURCE_SHA==OCI_REVISION verified. digest=$DIGEST"
echo "evidence: $EVID"
echo "BUILD_OK tag=$IMAGE_TAG digest=$DIGEST source_sha=$SOURCE_SHA"
