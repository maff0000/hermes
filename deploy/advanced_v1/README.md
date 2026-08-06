# Advanced-v1 deployed provenance, scope-aware readiness & reproducible dark configuration

**WO:** WO-HELM-HERMES-DEPLOYED-PROVENANCE-SCOPE-READINESS-AND-REPRODUCIBLE-DARK-CONFIG-0001. Corrects R2D2's four
post-deployment AMBER findings. **Source-only; production untouched; not deployed.**

## A. Immutable source provenance
- **Build:** `ops/build/build_production_candidate.sh` — the ONLY governed way to build a production candidate. It
  sets `SOURCE_SHA=$(git rev-parse HEAD)` (40-hex), refuses a dirty tree, passes `SOURCE_SHA`/`BUILD_UTC` as build args
  (compose already forwards them to the Dockerfile `ARG`→`LABEL org.opencontainers.image.revision`→`ENV`), then
  **verifies the built image's OCI revision == SOURCE_SHA** and emits a reproducible SHA→digest evidence JSON to
  `${HERMES_BUILD_EVIDENCE_DIR:-/srv/backup/build_evidence}` (Dockerfile/lock/compose checksums, image digest). A bare
  `docker compose build` (no `SOURCE_SHA`) yields `UNKNOWN_SOURCE_SHA` and MUST NOT be promoted.
- **/buildinfo** (`main.py`) returns `build_identity()` (`utils/hermes_build_identity_v1.py`): `source_sha`,
  `build_utc`, `image_ref`, `build_classification`, `build_identity_valid` + reasons (`BI-SHA-MISSING`,
  `BI-SHA-NOT-40-HEX`). The OCI revision label and `/buildinfo.source_sha` are the same value; a mismatch is a
  readiness fault. The immutable image **digest** is the runtime identity; the Git SHA identifies the source.

## B. External scope-aware readiness — `GET /readiness`
`utils/hermes_readiness_surface_v1.build_readiness_report(...)` assembles a versioned (`v1`), read-only, deterministic
contract from authoritative constituents (build identity, live `registry_effective_summary`, effective master/mode,
pilot scope, calendar provenance, stream/consumer/order/backfill state, core health). Blocks: `identity`, `core`,
`registry`, `pilot`, `expansion`, `calendar`, `boundaries`, `readiness` (separate `liveness` / `core_health` /
`deployment_authority_compliance` / `expansion_readiness` / `trading_execution_readiness` + `overall` + `fault_codes`).
The endpoint is **GREEN for the authorised DARK deployment** while reporting `expansion_readiness=NOT_READY_DARK` and
`trading_execution_readiness=ABSENT`; it returns **503/RED** on any authority breach (source missing/mismatch/malformed,
registry load fail, master true, mode not DISABLED, seven-new/inactive published, stream≠1, calendar drift, consumer on,
order present, backfill on). No secrets are exposed; state is derived from configuration/runtime, not Redis key patterns
(key counts are observability only).

## C. Reproducible dark deployment configuration
Replaces reliance on the untracked project-local override with **version-controlled** artefacts:
- `deploy/advanced_v1/docker-compose.dark.yml` — governed overlay that sets `HERMES_ADVANCED_V1_MASTER_ENABLED=false`
  and `HERMES_ADVANCED_V1_PUBLISHER_MODE=DISABLED` **explicitly**, pins the image by `${HERMES_IMAGE_REF}`, preserves
  the XAU pilot family controls via `${VAR}`, and hard-sets `CONSUMER_LIVE=false` / backfill execution off. No secrets.
- `deploy/advanced_v1/dark.env.template` — placeholders only; copy to a governed access-controlled env file (never
  commit the filled version); real secrets come from the governed secret mechanism.
- `deploy/advanced_v1/dark_config_preflight.py` — `validate_dark_config(env)` fails closed on master≠false, mode≠DISABLED,
  missing pilot scope, missing/malformed SOURCE_SHA, unpinned image, consumer enabled, backfill execution, order
  authority, or a second-stream setting; prints safe effective controls (never secrets).

### Precedence (deterministic; lowest → highest)
container defaults < base `docker-compose.yml` < governed env file < **`docker-compose.dark.yml` explicit values**.
The overlay's explicit `master=false`/`mode=DISABLED` are the effective authority, so a lower-priority unsafe env value
cannot silently override the governed dark state.

### Governed deploy command
```
docker compose -p hermes-dev -f docker-compose.yml -f deploy/advanced_v1/docker-compose.dark.yml up -d hermes-signal
```

### Rollback / recovery (reproducible; survives reboot, checkout/worktree cleanup, loss of the untracked override)
1. Rebuild the prior image via the wrapper at the prior SHA (or reuse the retained prior digest `sha256:9171fb67…`).
2. Set `HERMES_IMAGE_REF`/`HERMES_IMAGE_TAG` to the prior digest/tag in the governed env file.
3. `docker compose -p hermes-dev -f docker-compose.yml -f deploy/advanced_v1/docker-compose.dark.yml up -d hermes-signal`.
4. Migration 025 stays applied (migration rollback is a separate authority). No `/etc`/systemd/cron dependency.

### Container/cloud absorption
All controls are project-owned, secret-free, and env-driven — portable to a future container/cloud deployment by
supplying the same governed env file + overlay; the SHA→digest evidence makes the running image independently auditable.
