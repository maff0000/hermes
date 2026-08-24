# HERMES configuration contract (canonical)

WO-HELM-HERMES-DEV-PROD-SINGLE-CONFIG-CONTRACT-CONVERGENCE-0001.

## Principle
HERMES has ONE codebase. DEV and PROD run the SAME application logic. Every environment difference is supplied
through external configuration using ONE shared schema (see `config/hermes.env.example`). No environment behaviour
is encoded in source, Python constants, `if DEV/PROD` branches, Dockerfile, entrypoint, systemd, cron, `/etc`, Redis,
or SQL flags. Values differ per environment; the SCHEMA does not.

## Resolution
`env_config.get_env(KEY)` reads `{ENVIRONMENT}_{KEY}` (e.g. `PROD_DB_HOST`), with a non-prefixed fallback for shared
keys. `ENVIRONMENT` ∈ {DEV, PROD}. Required keys fail loud; enabled-but-misconfigured components fail loud; disabled
optional components are INERT.

## Disabled = inert (hard rule)
For every optional component, the disabled state performs NO socket creation, connection, timeout, retry, timer, task,
write, or health degradation. Verified for the shadow tick emitter (`DisabledShadowEmitter` holds no client, no I/O).

## Shadow tick emitter (DEV-only capability, config-governed)
`HERMES_SHADOW_TICK_PUBLISH_ENABLED` — DEV may enable; PROD=false. Disabled ⇒ inert. When ENABLED but the endpoint is
unavailable, the emitter is now bounded (§20 fix): the shadow redis client uses a 0.5 s connect/socket timeout and a
circuit breaker opens after 3 consecutive failures, skipping redis I/O for a 30 s cooldown — a down endpoint can never
block the async tick loop, and a shadow fault never disrupts the market-truth tick path (`emit_tick_observed` never raises).

## PROD fail-closed
With `ENVIRONMENT=PROD`, no optional DEV/shadow/debug output activates from a missing key. Missing optional enable
flags never default to DEV-active behaviour.

## OANDA coordination (see docs/architecture/oanda-coordination.md)
`OANDA_SCHEDULE_PHASE_OFFSET_SECONDS` (DEV=0, PROD≈20) and `OANDA_REST_MAX_RPS` (≤10) are external config, not code
constants. They govern REST *initiation* only and never distort market timestamps.

## Secrets
Behavioural config lives in the shared env file; secret VALUES live in separately-permissioned secret files
(referenced by the app), never in Git/Fabric/logs.

## Core candle-history contract (multi-instrument)
WO-HELM-HERMES-DEV-MULTI-INSTRUMENT-CORE-CANDLE-WICK-HISTORY-CONTRACT-0001.

Every enabled instrument (`INSTRUMENTS`) exposes the SAME core candle surface for the base clock timeframes
**M1, M5, M15, H1**: `hermes:candles:<INSTR>:<TF>:latest:v1` and `hermes:candles:<INSTR>:<TF>:history:v1:*`
(+ `:index`). Each candle record carries the generic OHLC + geometry from `candle_contract_v1`: `open/high/low/
close/volume`, `body_high/body_low/body_size`, `range_size` (== total_range), `wick_high` (== upper_wick_size),
`wick_low` (== lower_wick_size), `candle_direction`, completion state, `schema_version`, and freshness/provenance.
Geometry is pure OHLC math — identical for metals/FX/indices/oil.

Scope is CONFIG-driven (`HERMES_CANDLE_CANONICAL_INSTRUMENTS`, `HERMES_CANDLE_HISTORY_FORWARD_INSTRUMENTS` = the
enabled set), fail-closed (no default fan-out). **H4/D1 remain XAU_USD only** on the governed fixed-22:00-UTC
(NY-5PM) grid, decoupled via `HERMES_CANDLE_H4_INSTRUMENTS`/`HERMES_CANDLE_D1_INSTRUMENTS`; non-XAU H4/D1 are
`UNSUPPORTED_BY_CONTRACT_PENDING_SESSION_ANCHOR_RULING` (applying gold's daily boundary to FX/index/oil could
create false daily market truth). XAU additionally exposes richer specialist surfaces (features, levels,
sessions, gaps, quote/tick, D1). Instrument enablement authority = `INSTRUMENTS` config; consumers select subsets.

## Startup dependency gate + startup/recovery resilience
WO-HELM-HERMES-DEV-STARTUP-RECOVERY-RESILIENCE-AND-HEALTH-TRUTHFIX-0001 (root cause: 2026-08-20 Dell reboot).
Full semantics: `docs/architecture/startup-recovery-resilience.md`.

| Key | Default | Meaning |
|-----|---------|---------|
| `DB_STARTUP_WAIT_TIMEOUT_SECONDS` | 180 | Total budget for the bounded wait-for-DB gate at lifespan start. Exhaustion ⇒ visible `DbStartupTimeout` exit (restart policy owns the next boot). |
| `DB_STARTUP_RETRY_INITIAL_DELAY` | 1 | First DB retry delay (seconds). |
| `DB_STARTUP_RETRY_MAX_DELAY` | 15 | DB retry backoff ceiling (seconds). |
| `DB_STARTUP_RETRY_BACKOFF_MULTIPLIER` | 2 | DB retry exponential multiplier. |

One shared schema: identical keys in DEV and PROD; only externally supplied VALUES may differ. The initial OANDA
connect is governed by the EXISTING `STREAM_RETRY_*` keys — a failed initial connect enters the same
`oanda_stream_task` reconnect loop (bounded exponential backoff, watchdog `NEVER_CONNECTED` → `RECOVERING` →
`CONNECTED_UNPROVEN` → `FLOWING`), never a second reconnect mechanism and never a silent streamless start.

## Deployment identity + host/config binding
WO-HELM-HERMES-DEV-DEPLOYMENT-IDENTITY-AND-HOST-CONFIG-BINDING-0001. Full doctrine:
`docs/architecture/deployment-identity-and-config-binding.md`.

| Key | Default | Meaning |
|-----|---------|---------|
| `EXPECTED_HOSTNAME` | *(required)* | Environment-owned declared host. Must equal the host machine's `/etc/hostname`; mismatch/missing ⇒ startup FAILS CLOSED (`IDENT-*`). |
| `HOST_HOSTNAME_PATH` | `/etc/host-hostname` | Where the deployment mounts the host's `/etc/hostname` (read-only). |

Canonical identity: `ENVIRONMENT` (DEV|PROD) paired with governed `RUN_ENV` (DEV→STAGING, PROD→PRODUCTION);
build identity from baked `SOURCE_SHA` (== OCI revision == `/buildinfo`). Redis manifest/heartbeat identity
derives from these same sources — the retired `HERMES_ENVIRONMENT`/`HERMES_RUN_ENV`/`HERMES_DEPLOYED_SHA`
fallbacks must not be reintroduced.

## Redis writer authority + consumer boundary
WO-HELM-HERMES-DEV-REDIS-CONSUMER-INTEGRITY-READONLY-BOUNDARY-0001. Doctrine:
`docs/architecture/redis-consumer-integrity-boundary.md`.

| Key | Default | Meaning |
|-----|---------|---------|
| `REDIS_USERNAME` | *(empty = legacy open model)* | Dedicated ACL writer user for ALL HERMES runtime redis clients (single seam `utils/hermes_redis_auth_v1`). |
| `REDIS_PASSWORD` | *(existing key)* | Writer secret when `REDIS_USERNAME` is set — REQUIRED then (fail-loud, no silent fallback to default authority). Secret lives only in the environment-owned 0600 env file. |

Consumers need no credentials where the deployment applies the read-only consumer ruleset to the Redis `default` user; consumer authority is `~hermes:* &hermes:* +@read +@connection +subscribe +psubscribe -@dangerous +info`.
