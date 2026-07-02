# 02 — Instrument-catalog contract v1 schema (proposed)

**Key:** `hermes:instrument_catalog:XAU_USD:v1` (versioned; XAU_USD output only; XAUUSD = inbound alias only).
**Default behaviour:** builder PURE (payloads only from a governed snapshot). Publisher foundation DISABLED by
default (`HERMES_INSTRUMENT_CATALOG_PUBLISH_ENABLED` unset → `DisabledInstrumentCatalogPublisher`); enabled-without-
authorised → `SystemExit(101)`. **Nothing published in this WO.**

## Aggregate status: GREEN / AMBER_PARTIAL / GATED / BLOCKED / NOT_IMPLEMENTED / UNKNOWN_NEEDS_PROBE
GREEN when every EXPECTED-ACTIVE surface (candle latest+history M1-H4, indicators/features M1-H4, sessions, levels
session/intraday, control_plane) is ACTIVE. UNKNOWN anywhere → UNKNOWN_NEEDS_PROBE. Else AMBER_PARTIAL. D1
(gated/pending), quote/tick (not-impl), feed_health (dark), daily/weekly levels (gated) are EXPECTED non-active and
do NOT degrade GREEN.

## Per-surface status: ACTIVE / GATED / BLOCKED / NOT_IMPLEMENTED / CODE_PRESENT_DARK / LEGACY / LEGACY_OR_PARTIAL / DEPRECATED / PENDING_FIRST_DAILY_SEAL / UNKNOWN_NEEDS_PROBE

## Payload fields
schema_version, contract, instrument, canonical_instrument, aliases{inbound, output_publish_denied},
generated_at_utc, source, status, fault, supported_timeframes, active/gated/blocked/not_implemented_timeframes,
surfaces, contracts, contract_keys, timeframe_contracts, candle_contracts{tf:{latest_key,latest_status,
history_index_key,history_status}}, indicator_contracts{tf:{key,status}}, candle_feature_contracts, session_contracts,
level_contracts{scope}, feed_health_contract, quote_contract{key:null,NOT_IMPLEMENTED}, tick_contract{key:null,
NOT_IMPLEMENTED}, legacy_contracts[{pattern,status,note}], deprecated_contracts, d1_policy{anchor 22:00 fixed,
no DST, source 6xH4, latest_status, history BLOCKED, gated_derived}, source_dependencies, publisher, ttl_seconds,
provenance, notes, deterministic_only=true.

## Canonical/alias, D1, legacy, quote/tick representation
- Canonical XAU_USD; `XAUUSD` inbound alias only; validate rejects any output contract key containing `:XAUUSD:`.
- D1 latest PENDING_FIRST_DAILY_SEAL; D1 history BLOCKED; D1 indicators/features + daily/weekly levels GATED (never falsely ACTIVE).
- Legacy `hermes:signals:*` = LEGACY; `hermes:market_map:*` = LEGACY_OR_PARTIAL — preserved, not deleted/deprecated-away.
- quote/tick = NOT_IMPLEMENTED (key null) — not yet built, explicit.
