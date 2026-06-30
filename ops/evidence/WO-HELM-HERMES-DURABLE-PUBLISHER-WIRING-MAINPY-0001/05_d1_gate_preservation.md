# 05 — D1 gate preservation (Part A / strict exclusions)

D1 latest is AMBER (pending first daily seal); D1 history blocked; D1-derived levels/indicators/features gated.
This WO preserves every D1 gate — it adds no D1 path.

## Preserved gates
- **Steps timeframe allowlist** — `steps.LATEST_TFS = ("M1","M5","M15","H1","H4")`; **D1 excluded**. The indicator
  and candle_feature steps iterate `LATEST_TFS` only → no D1 indicator/feature key is ever built.
  Tested: `test_runtime_families_and_d1_gate_preserved` asserts `"D1" not in steps.LATEST_TFS`.
- **Levels D1-derived scopes** — `sessions_levels_step` builds the level publisher via
  `lvl.build_level_publisher_from_env()`, which honours `HERMES_LEVEL_D1_AUTHORISED` (default false) and
  `parse_level_scopes(..., allow_d1=False)`. Daily/weekly scopes raise `GOV-HERMES-LVL-D1-001` unless D1 GREEN. The
  step only ever publishes `session` + `intraday` (closed-H1). Existing tests `test_daily_scope_d1_derived_gated_
  until_green`, `test_level_publisher_d1_scope_gated_from_env`, `test_level_scope_parse_d1_gated` still pass.
- **Control-plane D1 state** — `control_plane_step` reports `d1_state = PENDING_FIRST_DAILY_SEAL` until
  `hermes:candles:XAU_USD:D1:latest:v1` exists, and emits `gated_families.{indicators_d1,candle_features_d1,
  levels_d1} = GATED`. No D1-derived family is moved to active.
- **`level_semantics`** for daily/weekly correctly reports `level_source_granularity="D1"` (closed D1) — but those
  scopes remain gated, so the metadata is only reachable once D1 is GREEN + separately authorised.

## Not done
No D1 history. No D1-derived levels. No D1 indicators/features. No change to the in-memory D1 producer/observer.
