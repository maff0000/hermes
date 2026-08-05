# Inventory — XAU-specific code + duplicate instrument lists + generalisation plan (§24)

## A. XAU-specific pilot boundary — 15 modules (lift, don't fork)
Each defines `CANONICAL_INSTRUMENT = "XAU_USD"` and a `parse_*_instruments()` that rejects non-XAU
(`GOV-HERMES-*-021`) and returns `frozenset({"XAU_USD"})`. Keys are already `{instrument}[:{tf}]`.

| # | Module | Constant @line | Generalisation |
|---|--------|----------------|----------------|
| 1 | utils/hermes_indicators_v1.py | 26 | replace const w/ registry allowlist; parser accepts `enabled_instruments()`; key already generic |
| 2 | utils/hermes_levels_v1.py | 22 | same |
| 3 | utils/hermes_instrument_catalog_v1.py | 37 | same |
| 4 | utils/hermes_quote_tick_contract_v1.py | 29 | same |
| 5 | utils/hermes_proposal_validator_v1.py | 27 | same |
| 6 | utils/tick_live_emitter_v1.py | 27 | allowlist must accept registry set (was exactly {XAU}) |
| 7 | utils/candle_d1_history_v1.py | 29 | key currently literal `XAU_USD` -> parameterise by instrument |
| 8 | utils/candle_d1_history_seed_backfill_v1.py | 28 | same |
| 9 | utils/hermes_recovery_planner_v1.py | 26 | registry-driven |
| 10 | utils/candle_d1_hydration_v1.py | 38 | parameterise |
| 11 | utils/hermes_candle_features_v1.py | 25 | registry-driven; key already generic |
| 12 | utils/hermes_feed_health_v1.py | 25 | allowlist accepts registry set |
| 13 | utils/hermes_gaps_v1.py | 26 | key `hermes:gaps:XAU_USD:v1` literal -> `hermes:gaps:<INST>:v1` |
| 14 | utils/hermes_sessions_v1.py | 21 | registry-driven |
| 15 | utils/candle_h4_hydration_v1.py | 39 | parameterise |
Also: `candle_history_v1.py:HISTORY_INSTRUMENTS=("XAU_USD",)` + forward writer allowlist -> registry set.
**No `if instrument ==` scattered branches and no per-ticker function names exist** (confirmed) — the ONLY
per-ticker coupling is the constant + parser + a few literal keys. This is the whole delta.

## B. Per-instrument env flags (keep the gate semantics, widen the allowlist)
`HERMES_INDICATOR_PUBLISH_INSTRUMENTS`, `HERMES_LEVEL_PUBLISH_INSTRUMENTS`,
`HERMES_INSTRUMENT_CATALOG_PUBLISH_INSTRUMENTS`, `HERMES_QUOTE_PUBLISH_INSTRUMENTS`,
`HERMES_TICK_PUBLISH_INSTRUMENTS`, `HERMES_CANDLE_FEATURE_PUBLISH_INSTRUMENTS`,
`HERMES_FEED_HEALTH_PUBLISH_INSTRUMENTS`, `HERMES_SESSION_PUBLISH_INSTRUMENTS`,
`HERMES_CANDLE_D1_INSTRUMENTS`, `HERMES_CANDLE_HISTORY_FORWARD_INSTRUMENTS`.
Design: these remain the *activation* surface, but the parser validates against the **registry** (not the
XAU literal). Preferred future: activation moves onto the registry capability flags (per-row), collapsing
these env knobs to one governed source. No new per-instrument env branch may be introduced.

## C. Duplicate instrument lists — 17 (§17), with disposition
| # | Location | Disposition |
|---|----------|-------------|
| 1 | INSTRUMENTS env (config.py:152) | demote to optional override; loader is authority |
| 2 | config.py:182-189 legacy `load_instruments_from_db` fallback `['XAU..','XAU_USD']` | DELETE (dead legacy path) |
| 3 | adapters/oanda.py:260 fallback `['XAU..','XCU_USD']` | source from loader |
| 4 | scripts/backfill_oanda.py:210 `['XAU..','XCU_USD']` | source from loader |
| 5 | scripts/backfill_oanda.py:199 default `'XAU_USD'` | require explicit registry arg |
| 6 | scripts/seed_tick_gap_ledger.py:28 `INSTRUMENTS` const | registry-driven |
| 7 | scripts/stage_f_gate_check.py:24 set | registry-driven |
| 8-10 | mock/oanda_mock.py:56-87 BASE_PRICES/SPREADS/VOLATILITY | keyed from registry/test fixture |
| 11 | tests/test_predeploy_gate_v1.py:25 REAL14 | parameterise from registry |
| 12 | tests/test_predeploy_gate_v1.py:27 SCHEDULED12 | parameterise |
| 13 | tests/test_tick_live_emitter_v1.py:272 | parameterise |
| 14-16 | tests/test_tick_*_v1.py REGISTRY={XAU,EUR} | parameterise / shared fixture |
| 17 | tests/test_hermes_market_hours_readiness_v1.py:16 CONFIGURED12 | parameterise |
| + | main.py:996 level default `['XAU_USD']` | loader |
**End state: exactly one authority (`enabled_instruments()`); every other list deleted or derived.**

## D. Generalisation proof
The XAU pilot becomes generic by (1) one registry loader, (2) constant→allowlist lift in 15 modules,
(3) parser widening, (4) key-factory consolidation, (5) duplicate-list removal, (6) parameterised tests.
No copy of XAU is created. Diff surface is bounded and mechanical; behaviour for XAU is byte-identical.
