# HERMES Redis Contract Manifest / Catalog

HERMES-owned `hermes:*` Redis contracts (market truth only). Consumers (e.g. Falcon) read these
**read-only**; HERMES is the sole writer. **All timestamps UTC.** This manifest is documentation;
no keys are published by this WO.

## Registered contracts

| Key | Contract | Ver | Type | TTL (s) | Redis EX (s) | Truth source | Role | Status |
|---|---|---|---|---|---|---|---|---|
| `hermes:ticks:{instrument}:latest:v1` | `hermes.ticks.latest` | v1 | string (JSON envelope) | 5 | 10 | OANDA raw tick (HERMES_TICK_SOURCE_V1) | **canonical hot-path** per-instrument tick | DESIGNED (publisher not built) |
| `hermes:ticks:latest:v1` | `hermes.ticks.latest` | v1 | string (JSON catalog) | 5 | 10 | instrument registry v1 | discovery/dashboard/cockpit catalog only | DESIGNED (publisher not built) |

## Rules

- **Writer:** HERMES only. **Readers:** standalone consumers (Falcon/Falcon-structure) read-only —
  never HERMES SQL/internals/tradingProteus/structure_engine.
- **Envelope:** universal HERMES envelope (`schema_version, service=HERMES, domain, contract,
  contract_version, key, generated_at_utc, valid_until_utc, ttl_seconds, freshness_state, status,
  reason_codes, provenance, derivation, data`).
- **Derivation:** `NONE_RAW_TICK` (raw market truth; no interpretation).
- **seq:** `null` in v1 — not a completeness proof.
- **No interpretive fields:** no structure/regime/signal/risk/cockpit/support-resistance fields
  (HERMES does not own those).
- **Freshness:** FRESH ≤5s, DEGRADED ≤15s/warning, STALE >15s, UNAVAILABLE on no/bad source.

## Schema reference + fixtures

- Builder/validator: `utils/tick_contract_v1.py`.
- Fixtures: `tests/fixtures/tick_contract/{per_instrument_fresh,aggregate_fresh,stale,degraded,unavailable,malformed_invalid}.json`.
- Tests: `tests/test_tick_contract_v1.py`.

## Not in this contract (consumer-owned, elsewhere)

`hermes:candle_features:*`, `hermes:candle_context:*`, `hermes:indicators:*` are separately
designed HERMES candle/indicator contracts (different WOs). Structure events / regime / strategy /
cockpit are **Falcon/HELIOS/ARES-owned** and never appear under `hermes:*`.
