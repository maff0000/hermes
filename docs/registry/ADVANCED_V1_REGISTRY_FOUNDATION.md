# Advanced-v1 Registry Foundation
WO-HELM-HERMES-ADVANCED-V1-REGISTRY-FOUNDATION-0001

## Authority
`tradingSignals.instruments` is the SOLE runtime instrument authority. The reusable loader
`utils/hermes_instrument_registry_v1.py` derives discovery, metadata and per-capability policy from it.
Migration `025` adds reusable metadata columns (precision, tick_size, price_authority, market_hours_policy,
expected_freshness_sec, enabled_timeframes, indicator_profile, tick/indicator/gap capability flags,
backfill/retention policy, metadata_version, provenance), adds `energy` to the category enum, and corrects
`WTICO_USD` base_metals→energy. Onboarding = ONE registry record; the loader validates + fails closed and
NEVER falls back to a hard-coded list. Proven by `tests/test_hermes_instrument_registry_v1.py`
(`test_add_one_instrument_is_data_only`) + isolated ephemeral-DB apply/rollback (evidence).

## Environment `INSTRUMENTS` demotion
`INSTRUMENTS` env is no longer an authority. It survives ONLY as a fail-closed VALIDATOR:
`registry.consistency_check(records, env_instruments)` rejects any env-named instrument not enabled in the
SQL registry. Runtime consumers are rewired onto the loader in the NEXT (pipeline-generalisation) stage.

## Duplicate-list disposition (design inventory: 17 lists)
This foundation establishes the single authority + validator. Each live list is dispositioned as follows;
the actual per-consumer rewiring lands with the pipeline-generalisation stage (kept out of this bounded
foundation to avoid touching runtime behaviour):
- config.py INSTRUMENTS env (live)            -> DERIVE from loader; env kept as consistency VALIDATOR only.
- config.py legacy load_instruments_from_db   -> REMOVE (dead legacy fallback).
- adapters/oanda.py:260 fallback list         -> DERIVE from loader (no hard-coded fallback).
- scripts/backfill_oanda.py, seed_tick_gap_ledger.py, stage_f_gate_check.py -> DERIVE from loader.
- mock/oanda_mock.py price tables             -> keyed from registry/test fixture (isolated-test input).
- tests/* hard-coded lists (predeploy_gate, tick_*, market_hours_readiness) -> PARAMETERISE over
  `enabled_instruments()` (registry fixture); retain only as isolated-test inputs.
- main.py level default ['XAU_USD']           -> DERIVE from loader.
Interim guard: `consistency_check` is the fail-closed bridge so no live component silently diverges from the
registry before rewiring completes.

## Rollout scope + activation
8-instrument set seeded. `XAU_USD` = existing active pilot (capability flags 1). The 7 new instruments are
NOT_ENABLED (flags 0) and are NOT activated by this WO. `registry_component_state()` reports their status as
`NOT_ENABLED` for /health /ready /status /metrics — never falsely active.

## Held
No production migration applied (default HELD); no runtime rewiring; no Redis contract; no activation.
Production migration + activation are separately gated.
