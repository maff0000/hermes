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

---

# D. Complete tracked runtime configuration & XAU parity
**WO-HELM-HERMES-COMPLETE-TRACKED-RUNTIME-CONFIG-AND-XAU-PARITY-0001.** Completes the tracked bundle so it reproduces
the **entire** authorised XAU runtime (not just the Advanced-v1 dark controls), replacing the untracked
`docker-compose.dev-override.yml`. Source-only; production untouched.

## Artefact architecture
| Artefact | Role |
|----------|------|
| `runtime_config_schema_v1.py` | **Single source of truth** — one machine-readable record per deployment-contract field (type, required, enum/range, secret/authority/host classification, parity-critical flag, deprecated aliases, must-equal, consequence). Drives preflight, renderer, parity, template, tests. |
| `config_inventory_v1.md` | Secret-free inventory + classification (A–F) + reconciliation sets, generated from the schema. |
| `docker-compose.operational.yml` | **NEW** operational overlay — every non-secret XAU control (candle/indicator/level/session/feature/D1/warm-start/publisher-runtime/catalog/quote/canonical-Redis/control-plane), canonical-named, defaults matching the current authorised values. |
| `docker-compose.dark.yml` | Expansion-dark overlay (master=false, mode=DISABLED, 5 Advanced-v1 families, consumer/backfill-exec off). Applied **last** so dark controls win. |
| `deployment.env.template` | Complete governed env template — canonical names, per-field metadata, placeholders only. |
| `runtime_config_preflight_v1.py` | Full-contract fail-closed preflight (supersedes `dark_config_preflight.py`). |
| `render_effective_config.py` | Pure effective-config renderer (compose merge + interpolation) + secret-redacted report. |
| `xau_parity_v1.py` | Deterministic parity checker vs the live effective config. |
| `deploy.sh` / `rollback.sh` | Governed deployment / rollback wrappers. |

## Canonical naming (clean vs DEV_*)
The app (`env_config`, `ENVIRONMENT=DEV`) consumes `DEV_*` routing names. The **deployment contract** uses canonical,
environment-neutral names (`DB_HOST`, `DB_PORT`, `REDIS_HOST`, …); the operational overlay **maps** them onto the app's
`DEV_*` names. `DB_PORT=3307` is reproduced explicitly — the base compose's `${DB_PORT:-3306}` silent default is
eliminated (`${DB_PORT:?}` fail-loud). If both a canonical name and its legacy `DEV_*` alias are supplied with different
values, preflight **fails** (`CFG-ALIAS-CONFLICT`); a legacy alias without its canonical name fails (`CFG-LEGACY-ONLY`).

## Governed deploy command
```
docker compose -p hermes-dev \
  -f docker-compose.yml \
  -f deploy/advanced_v1/docker-compose.operational.yml \
  -f deploy/advanced_v1/docker-compose.dark.yml \
  --env-file <governed.env> up -d hermes-signal
```
Secrets (DB/Redis passwords, OANDA keys) come from the base `.env` env_file; the governed env file carries non-secret
routing/identity + the authority-bearing canonical activation token. Use `deploy/advanced_v1/deploy.sh --env-file <f>`.

## Backfill / history boundary (§15)
`HERMES_D1_HISTORY_BACKFILL_*`, `*_WARMSTART_*`, `HERMES_CANDLE_HISTORY_FORWARD_*` and `HERMES_BACKFILL_STATUS_*` are
authorised **history warm-start / status** controls and remain enabled for XAU. They are **distinct** from the prohibited
`HERMES_BACKFILL_EXECUTION_ENABLED` (kept `false`) and from autonomous repair (off). The Advanced-v1 backfill *executor*
stays absent.

## Old untracked override retirement plan (§37)
Executed under a later **deployment** WO (not here):
1. Preserve `docker-compose.dev-override.yml` checksum (`e967ce048c4f31d3…`) + restricted backup.
2. Generate the governed env file from `deployment.env.template` (chmod 600; never committed).
3. Render + parity-check (`render_effective_config` / `xau_parity_v1`) against the live effective config → 0 diffs.
4. `runtime_config_preflight_v1` GREEN on the exact deployment inputs.
5. Deploy the exact promoted digest with the tracked compose sequence (no `dev-override`).
6. Prove XAU continuity + `/buildinfo` + `/readiness`.
7. Remove `dev-override.yml` from the active compose invocation; archive it as rollback evidence.
8. Future deployment/rollback reconstruction relies only on: tracked overlays + governed env file + immutable digest +
   preflight + documented command. No dependence on the untracked override.

## Blueprint inputs for independent assurance (§38)
- **Authority map / canonical names / aliases:** `runtime_config_schema_v1.py` (`BY_NAME`, `ALIAS_TO_CANONICAL`, `PARITY_CRITICAL`, `MUST_EQUAL`).
- **DB/Redis routes:** `DB_HOST`/`DB_PORT=3307`/`DB_NAME`; `REDIS_HOST`/`REDIS_PORT`/`REDIS_DB`; canonical-Redis `HERMES_CANDLE_CANONICAL_REDIS_*`.
- **XAU operational groups:** `feature_group in {xau_operational, adv1_families, canonical_redis, process}`.
- **Secret references:** `SECRET_FIELDS` + `AUTHORITY_FIELDS` (values external; presence-checked).
- **Parity gate:** `xau_parity_v1.compare` vs `tests/fixtures/live_effective_config_v1.json`.
- **Preflight / commands:** `runtime_config_preflight_v1`, `deploy.sh`, `rollback.sh`.
- **Production unchanged:** container `c690a2ca25cb`, image `d42c1f4c`, restarts 0. **Exact next gate:** R2D2 exact-head assurance before merge/redeploy.
