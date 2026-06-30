# Indicator Code Inventory (read-only)
| Module / source | Indicators | Source TF | Source dep | Det/Interp | Owner | Runtime producer | Redis key | Containerised | Status |
|--|--|--|--|--|--|--|--|--|--|
| utils/indicators.py | EMA pairs (12/26 MACD-style), RSI(14), EMA crossover state, calculate_all_indicators | per-TF | candle prices | DETERMINISTIC | HERMES | signal_builder.SignalComputer (folds into legacy signals) | none (v1) — values in legacy hermes:signals:* | yes (module) | present, NOT exposed as v1 indicators |
| utils/atr_calculator.py | ATR = SMA(True Range, period) | per-TF | candle H/L/close | DETERMINISTIC | HERMES | backfill_atr_metrics.py (backfill) | none | yes | present, not live as v1 |
| utils/level_engine.py | PDH/PDL, Asia session range, M15 swing H/L | D1/H1/M15 | candles (reads candles_D1 for PDH/PDL) | DETERMINISTIC levels | HERMES (deterministic) | LevelEngine (main.py) -> hermes_levels SQL | none (Redis) | yes | present (SQL), separate future LEVELS family |
| signal_builder SignalComputer | RSI/EMA via indicators.py | per-TF | candles | DETERMINISTIC values | HERMES (values) | live -> legacy hermes:signals:* | hermes:signals:* (legacy) | yes | LEGACY co-resident; values trapped in legacy payload |
| utils/regime_detector.py | regime classification | — | — | INTERPRETIVE | ARES | (co-resident in HERMES) | regime tables | yes | ARES-OWNED — NOT pulled into HERMES indicators; pending extraction |
