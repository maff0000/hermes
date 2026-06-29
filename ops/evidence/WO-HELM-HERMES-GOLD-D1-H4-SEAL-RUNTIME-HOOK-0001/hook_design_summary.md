# Hook Design Summary — D1 H4-Seal Runtime Hook
**WO-HELM-HERMES-GOLD-D1-H4-SEAL-RUNTIME-HOOK-0001 · code-only · HELM/HERMES**

## Wiring (single file: utils/candle_h4_publish_wire_v1.py)
`CanonicalH4Producer` gains an optional `d1_producer`. In `_seal_and_publish`, AFTER the H4 latest write and
the forward-history hook, `_offer_to_d1(env)` offers the freshly-sealed H4 to the D1 producer via a thin
`_SealedH4View` adapter (presents the published H4 envelope's data as an H4 candle-like object: timeframe=H4,
open time, instrument, OHLCV). The D1 producer groups six into a 22:00Z->22:00Z day and publishes a D1 latest
ONLY when a full 6xH4 day exists (it already enforces 6/6-OR-skip). EACH sealed H4 is offered.

## No import cycle / default disabled
`candle_d1_publish_wire_v1` does not import the H4 wire, so no cycle; the H4 producer accepts `d1_producer=None`
(treated as disabled -> no-op). `build_h4_producer_from_env` lazy-builds the D1 producer via
`build_d1_producer_from_env()` (default DisabledD1Producer). No Redis I/O at import.

## Unchanged behaviour
H4 latest publication and the forward-history writer are untouched (the offer runs after both, never alters
their results). Disabled D1 -> the seal hook is a pure no-op; H4 runtime is identical to before.
