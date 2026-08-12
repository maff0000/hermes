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
