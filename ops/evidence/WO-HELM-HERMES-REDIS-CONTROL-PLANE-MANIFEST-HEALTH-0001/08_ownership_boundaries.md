# Ownership Boundaries (binding architect rulings, embedded in the manifest)
HERMES owns: deterministic market data, candles, candle history, deterministic indicators, deterministic candle
features, DETERMINISTIC LEVELS (H/L sweeps, mathematical pivots, deterministic midpoints, Fibonacci anchors,
candle-derived levels, indicator values), feed health, instrument/catalog metadata, Redis publication contracts.
ARES owns: regime, risk, event/calendar impact, liquidity/risk context, INTERPRETIVE LEVELS (liquidity blocks,
risk-gated order blocks), decision gating.
Falcon: consumes HERMES/ARES/HELIOS contracts later; does not define HERMES internals.
HERMES must NOT: publish/determine/imply market regime; publish risk conclusions; expose interpretive levels.
regime_detector.py + regime_* config = ARES-owned / pending extraction (inventory only; not expanded here).
Source policies advertised: H4_FROM_H1; D1_FROM_6_OK_H4; rejected: direct candles_H4 (stale), direct candles_D1
(UTC-midnight), 24xH1 D1 production shortcut.
