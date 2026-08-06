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
The route lives in `utils/hermes_readiness_router_v1.py` as a dedicated `APIRouter` (`main.py` does
`app.include_router(...)` and binds `readiness_sources` to the live service state via FastAPI DI). Its `assemble()`
runs the authoritative observers and `utils/hermes_readiness_surface_v1.build_readiness_report(...)` to produce a
versioned (`v1`), read-only, deterministic contract. Blocks: `identity`, `core`, `registry`, `pilot`, `expansion`,
`calendar`, `boundaries`, `readiness` (separate `liveness` / `core_health` / `deployment_authority_compliance` /
`expansion_readiness` / `trading_execution_readiness` + `overall` + `fault_codes`). The endpoint is **GREEN for the
authorised DARK deployment** while reporting `expansion_readiness=NOT_READY_DARK` and `trading_execution_readiness=ABSENT`;
it returns **503/RED** on any authority breach. No secrets are exposed; state is derived from configuration/runtime, not
from Redis key-pattern scans.

### Authoritative field-to-source table (every safety field is DERIVED, never a constant)
| Field | Authoritative source | Observation | Freshness | Failure behaviour | Fault code | Privileged? |
|-------|----------------------|-------------|-----------|-------------------|-----------|-------------|
| `identity.source_sha` | `build_identity()` ENV (Dockerfile-stamped) | read env | build-time | missing/malformed → RED | `RDY-SOURCE-IDENTITY-MISSING` / `-MALFORMED` | no |
| `registry.*` | `registry_effective_summary(load_from_db())` | live DB read | per request | load fail → RED | `RDY-REGISTRY-LOAD-FAILED`, `RDY-ACTIVE-INCOMPLETE-ROW`, `RDY-MALFORMED-CAPABILITY` | DB |
| `pilot.xau_pilot_state` | registry active set + `load_pilot_scope()` | derived | per request | empty/inactive → RED | `RDY-PILOT-SCOPE-INVALID`, `RDY-XAU-PILOT-INACTIVE` | DB |
| `expansion.master/mode` | `load_master_enabled()` / `load_publisher_mode()` | config | per request | true / not-DISABLED → RED | `RDY-EXPANSION-MASTER-TRUE-UNDER-DARK`, `RDY-PUBLISHER-MODE-NOT-DISABLED` | no |
| `core.stream_count` | `observe_stream` over OANDA+IBKR adapters (`adapter.health`, task) | read-only | last tick heartbeat (30 s) | active≠1 → RED; indeterminate → RED | `RDY-STREAM-COUNT-NOT-ONE`, `RDY-STREAM-STATE-UNKNOWN` | no |
| `boundaries.order_path_state` | `observe_order_path` (routes + state components + order ENV flags) | read-only | per request | present/enabled → RED; unobservable → RED | `RDY-ORDER-PATH-PRESENT`, `RDY-ORDER-PATH-UNKNOWN` | no |
| `expansion.inactive_row_published_count` | `observe_inactive_publication` (exact canonical keys for NOT_ENABLED rows × 5 families) | bounded `exists()` | key TTL (existing key = current) | published>0 → RED; unobservable → RED | `RDY-INACTIVE-ROW-PUBLISHED`, `RDY-INACTIVE-PUBLICATION-UNOBSERVED` | Redis |
| `expansion.seven_new_published_count` | bounded exact-key `exists()` for the 7 new instruments | bounded `exists()` | key TTL | published>0 → RED | `RDY-SEVEN-NEW-PUBLISHED` | Redis |
| `calendar.*` | `load_calendar_provenance()` | config | per request | production_approved drift → RED | `RDY-CALENDAR-APPROVAL-DRIFT` | no |
| `boundaries.consumer_state` / `backfill_execution_state` | `CONSUMER_LIVE` / `HERMES_BACKFILL_EXECUTION_ENABLED` ENV | read env | per request | on → RED | `RDY-CONSUMER-ENABLED`, `RDY-BACKFILL-EXECUTION-ACTIVE` | no |

The three corrected fields — `stream_count`, `order_path_state`, `inactive_row_published_count` — are **observed, not
assumed**: an observation that cannot complete fails readiness (never reports the safe 1 / ABSENT / 0). **19** reachable
readiness fault codes are declared (the earlier `RDY-SOURCE-IDENTITY-MISMATCH` was removed — it is a build-time
wrapper guarantee, not runtime-derivable; a dead-fault reachability test enforces this). Two route-level operational
sentinels exist outside the authority set: `RDY-PROVIDER-UNWIRED` (unwired app) and `RDY-EVALUATION-ERROR` (bounded
error contract, never a stack trace).

### HTTP contract
| Aspect | Behaviour |
|--------|-----------|
| Route / method | `GET /readiness` only |
| `POST/PUT/PATCH/DELETE` | `405 Method Not Allowed` |
| `HEAD` | not supported → `405` (use GET) |
| Content type | `application/json` |
| Status | `200` GREEN or AMBER · `503` RED / unwired / evaluation error |
| Mutation | none — read-only DB/registry reads + bounded exact-key `exists()` only; no write, no key creation, no stream/OANDA/order side effect |
| Evaluation timing | synchronous per request; bounded (≤ inactive_rows × (4 + timeframes) exact key checks; no scan) |
| Dependency failure | fail-closed to RED with a deterministic fault; no traceback leaked |
| Distinction from `/ready` | `/ready` = tick-freshness liveness (existing); `/readiness` = scope-aware Advanced-v1 deployment-authority surface |
| Distinction from `/health` | `/health` = watchdog health_state for the container probe |
| Relation to `/buildinfo` | `/buildinfo` returns the same `build_identity()` the readiness `identity` block is derived from |

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
