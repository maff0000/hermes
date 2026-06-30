# Candle-Feature Code Inventory (read-only)
| Source | Features | Det/Interp | Owner | Runtime | Redis | Status |
|--|--|--|--|--|--|--|
| utils/candle_features.py | candle_geometry (body_high/low, body_size, range_size, wick_high/low) + ratios (body_to_range, wick ratios, close-in-range) + threshold classifications (doji/full_body/long_wick/pin_bar) via governed CandleFeatureConfig | DETERMINISTIC (governed thresholds; docstring: no permission/confidence/signal/regime/session) | HERMES | not wired to v1 Redis | hermes_candle_feature_config (SQL thresholds) | present, NOT exposed as v1 candle_features |
| candle_contract_v1 geometry | body_high/low/size, range_size, wick_high/low, candle_direction | DETERMINISTIC | HERMES | embedded in candle latest/history payloads | (in candle keys) | present (in-candle), to expose as a clean feature surface |
| legacy hermes:signals:* | any deterministic geometry embedded | DETERMINISTIC values | HERMES (values) | legacy | hermes:signals:* | LEGACY co-resident, not mutated |
| level_engine (PDH/PDL/swings) | deterministic levels | DETERMINISTIC | HERMES | LevelEngine | hermes_levels SQL | separate future LEVELS family |
