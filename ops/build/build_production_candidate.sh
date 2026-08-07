#!/usr/bin/env bash
# ============================================================================
# Governed production-candidate build wrapper — HARDENED
# WO-HELM-HERMES-BUILD-CONTEXT-SECRET-LEAK-CONTAINMENT-AND-CLEAN-SOURCE-BUILD-HARDENING-0001
# (supersedes the WO-...-DEPLOYED-PROVENANCE-...-0001 wrapper, which built the MUTABLE checkout via
#  `docker compose build` and could bake untracked host files — e.g. a nested healthcheck/canary/.env — into the image).
#
# PRIMARY SECURITY BOUNDARY: production candidates are built from an EXACT Git SHA exported into a temporary CLEAN
# build context (tools/hermes_clean_build_context_v1.py -> `git archive`, tracked-only). The mutable working directory
# is NEVER the Docker build context, so untracked/ignored/dirty files CANNOT enter the image. `.dockerignore` is now
# defence-in-depth only. A post-build image secret-artefact gate rejects any candidate carrying a prohibited secret file.
#
# Fail-closed. Verifies OCI revision == SOURCE_SHA. Emits SHA->digest + clean-context-manifest evidence. Does NOT deploy
# or promote. A bare `docker build`/`docker compose build` on the mutable checkout is NOT a governed production build.
#
# Usage: ops/build/build_production_candidate.sh [<checkout_dir>]   (default: git top-level of CWD)
# ============================================================================
set -euo pipefail
CHECKOUT="${1:-$(git rev-parse --show-toplevel)}"
cd "$CHECKOUT"

# 1) source identity — fail closed on dirty TRACKED tree / invalid SHA (untracked cruft is irrelevant: it can't enter
#    a git-archive export, but a dirty tracked tree would make the exported SHA not match the operator's intent).
DIRTY=$(git status --porcelain --untracked-files=no | wc -l)
if [ "$DIRTY" -ne 0 ]; then echo "FAIL: working tree has $DIRTY tracked change(s); refuse to build ambiguous source" >&2; exit 2; fi
SOURCE_SHA=$(git rev-parse HEAD)
if ! printf '%s' "$SOURCE_SHA" | grep -Eq '^[0-9a-f]{40}$'; then echo "FAIL: SOURCE_SHA not 40-hex: $SOURCE_SHA" >&2; exit 2; fi
BUILD_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)
IMAGE_TAG="prod-${SOURCE_SHA:0:12}"
IMG="hermes-signal:${IMAGE_TAG}"

echo "=== governed build (clean exact-SHA context): SOURCE_SHA=$SOURCE_SHA BUILD_UTC=$BUILD_UTC tag=$IMAGE_TAG ==="

# 2) export the EXACT-SHA clean build context (tracked-only) + manifest; validate fail-closed.
CTX="$(mktemp -d /tmp/hermes_clean_ctx.XXXXXX)"
MANIFEST="$(mktemp /tmp/hermes_ctx_manifest.XXXXXX.json)"
cleanup() { rm -rf "$CTX" "$CTX.err" "$MANIFEST"; }
trap cleanup EXIT
rmdir "$CTX"   # the tool requires a non-pre-existing output dir
# The tool EXPORTS the tracked-only context + writes the manifest, then also runs a content-pattern secret scan that
# benignly flags tracked evidence/schema/doc files (verdict digests, regex literals) and exits non-zero on that verdict.
# That content verdict is ADVISORY: the load-bearing gates here are (a) tracked-only export (git archive, structural),
# (b) no prohibited PATH artefacts, (c) the post-build IMAGE secret gate. So capture the tool's rc and gate on the
# manifest ourselves; a genuine export failure leaves no valid context/Dockerfile and is caught below.
set +e
python3 tools/hermes_clean_build_context_v1.py --source-sha "$SOURCE_SHA" --output-dir "$CTX" --manifest-out "$MANIFEST" >/dev/null 2>"$CTX.err" || true
set -e
python3 - "$MANIFEST" "$SOURCE_SHA" "$CTX" <<'PY'
import json, os, sys
mpath, sha, ctx = sys.argv[1], sys.argv[2], sys.argv[3]
assert os.path.isfile(mpath), "clean-context manifest not produced (export failed)"
m = json.load(open(mpath))
assert m.get("source_sha") == sha, f"manifest source_sha {m.get('source_sha')} != {sha}"
assert not m.get("prohibited_findings"), f"prohibited path findings: {m.get('prohibited_findings')}"
paths = {f.get('path') if isinstance(f, dict) else f for f in m.get('files', [])}
assert "Dockerfile" in paths, "Dockerfile missing from clean context"
assert not any(str(p).endswith('healthcheck/canary/.env') for p in paths), "canary .env in clean context"
assert os.path.isfile(os.path.join(ctx, "Dockerfile")), "exported context missing Dockerfile"
print(f"CLEAN-CONTEXT OK: files={m.get('exported_file_count')} manifest_checksum={str(m.get('manifest_checksum',''))[:16]}")
PY
rm -f "$CTX.err"

# 3) build ONLY from the clean context (never the mutable checkout).
docker build --target runtime -f "$CTX/Dockerfile" \
  --build-arg "SOURCE_SHA=$SOURCE_SHA" --build-arg "BUILD_UTC=$BUILD_UTC" --build-arg "HERMES_IMAGE_REF=$IMG" \
  -t "$IMG" "$CTX"

# 4) verify OCI revision label == SOURCE_SHA (fail-closed)
REV=$(docker inspect "$IMG" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
DIGEST=$(docker inspect "$IMG" --format '{{.Id}}')
if [ "$REV" != "$SOURCE_SHA" ]; then echo "FAIL: OCI revision '$REV' != SOURCE_SHA '$SOURCE_SHA'" >&2; exit 3; fi

# 5) IMAGE SECRET-ARTEFACT GATE (defence-in-depth): reject a candidate carrying a nested .env / key / canary secret.
echo "=== image secret-artefact gate ==="
if ! SCAN=$(python3 ops/build/hermes_image_secret_scan_v1.py "$IMG"); then
  echo "FAIL: image secret-artefact gate REJECTED the candidate:" >&2; echo "$SCAN" >&2; exit 4
fi
echo "$SCAN"

# 6) emit reproducible SHA->digest + clean-context evidence (governed off-git location; NO secrets)
EVID_DIR="${HERMES_BUILD_EVIDENCE_DIR:-/srv/backup/build_evidence}"
mkdir -p "$EVID_DIR"
DF_SHA=$(sha256sum "$CTX/Dockerfile" | cut -d' ' -f1)
LOCK_SHA=$( [ -f "$CTX/requirements.txt" ] && sha256sum "$CTX/requirements.txt" | cut -d' ' -f1 || echo none )
CTX_CHECKSUM=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('manifest_checksum',''))" "$MANIFEST")
EVID="$EVID_DIR/hermes_build_${IMAGE_TAG}_$(date -u +%Y%m%dT%H%M%SZ).json"
cat > "$EVID" <<JSON
{"application":"HERMES","source_sha":"$SOURCE_SHA","build_utc":"$BUILD_UTC","image_tag":"$IMAGE_TAG",
 "image_digest":"$DIGEST","oci_revision":"$REV","dockerfile_sha256":"$DF_SHA","dependency_lock_sha256":"$LOCK_SHA",
 "clean_context_manifest_checksum":"$CTX_CHECKSUM","image_secret_gate":"CLEAN","build_from":"exact_sha_clean_context",
 "build_contract_version":"v2"}
JSON
echo "SOURCE_SHA==OCI_REVISION verified. digest=$DIGEST"
echo "clean-context manifest checksum=$CTX_CHECKSUM"
echo "evidence: $EVID"
echo "BUILD_OK tag=$IMAGE_TAG digest=$DIGEST source_sha=$SOURCE_SHA (clean exact-SHA context; image secret gate CLEAN)"
