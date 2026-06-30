# Ownership Classification
HERMES-OWNED (deterministic, this WO's indicator contract scope): EMA/SMA moving averages, ATR, RSI, MACD,
Bollinger/Keltner deterministic bands, deterministic VWAP, deterministic pivots, deterministic high/low sweeps,
deterministic Fibonacci anchors, candle/body/wick features.
HERMES-OWNED but SEPARATE future LEVELS family (not this WO): PDH/PDL, Asia range, M15 swing (level_engine).
ARES-OWNED (NOT in HERMES indicators): regime detection (regime_detector.py), risk state, order-block
interpretation, liquidity context requiring risk judgement, event-risk interpretation, decision/gating.
The indicator module imports NO regime_detector and carries NO regime/risk/decision fields (test-asserted).
Legacy hermes:signals:*/market_map:* are NOT mutated by this WO.
