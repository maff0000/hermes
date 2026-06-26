# Candle Geometry & Direction Fields

HERMES owns **raw market truth only** (architect "Option A"). The contract carries deterministic
candle-state/geometry — NOT regime, NOT structure, NOT interpretation.

## Fields added to `data`
| field | definition | type |
|-------|-----------|------|
| `body_high` | `max(open, close)` | float / None |
| `body_low` | `min(open, close)` | float / None |
| `body_size` | `abs(close - open)` | float ≥ 0 / None |
| `range_size` | `high - low` | float ≥ 0 / None |
| `wick_high` | `high - body_high` (UPPER wick **size**) | float ≥ 0 / None |
| `wick_low` | `body_low - low` (LOWER wick **size**) | float ≥ 0 / None |
| `candle_direction` | `UP` if close>open, `DOWN` if close<open, else `FLAT` | enum / None |

All seven are `None` together when the candle is unpriced (`NO_SOURCE_DATA` / `MARKET_CLOSED`) — present
keys, never fabricated values. Proof: `test_market_closed_geometry_is_none_and_valid`.

## Why `UP/DOWN/FLAT` (not BULLISH/BEARISH)
`candle_direction` is the **pure sign** of `close - open` — fully deterministic, no thresholds, no config.
This deliberately avoids implying the config-governed classification in `utils/candle_features.classify`
(which uses doji thresholds and is consumer/interpretation territory). Worked examples:
`test_geometry_block_present_and_correct_bullish`, `test_geometry_bearish_and_flat_directions`.

## Deferred geometry (not added)
`wick_profile`, `range_state`, `volatility_state` were **not** added — they need the governed classifier +
DB config and are out of this PR's deterministic-basics scope. They can be added by a later WO that wires
the governed config. See `14_deferred_scope.md`.
