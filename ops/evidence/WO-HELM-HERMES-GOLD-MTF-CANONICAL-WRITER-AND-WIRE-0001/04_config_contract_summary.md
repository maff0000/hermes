# Config Contract Summary (governed, fail-loud, no hidden defaults)

Canonical publication is selected ONLY when ALL of the following are set via the approved external
env/config mechanism. Any missing piece → **fail loud** (no accidental canonical, no silent no-op).

| Env var | Required for canonical | Effect |
|---------|------------------------|--------|
| `HERMES_CANDLE_FORWARD_ENABLED` | `true` | master gate; false → `DisabledCandleEmitter` (no-op) |
| `HERMES_CANDLE_FORWARD_SINK` | `canonical` | selects the canonical seam (`inert`/`shadow` unchanged; `live`/`prod`/unknown → fail loud) |
| `HERMES_CANDLE_PUBLISH_ENABLED` | `true` | canonical master enable (`assert_canonical_allowed`) |
| `HERMES_CANDLE_PUBLISH_AUTHORISED` | `true` | canonical authorisation (`assert_canonical_allowed`) |
| `HERMES_CANDLE_CANONICAL_REDIS_HOST` | yes | explicit canonical bus host (no default) |
| `HERMES_CANDLE_CANONICAL_REDIS_PORT` | yes | explicit canonical bus port (no default) |
| `HERMES_CANDLE_CANONICAL_REDIS_DB` | yes | explicit canonical bus db (no default) |
| `CANDLE_TIMEFRAMES` | (for M15 production) | live aggregator grid, e.g. `M1,M5,M15,H1`; default `M5` |

**No config in code.** Timeframe grid, instrument, and bus target are all external. The publish grid the
writer accepts is fixed to `M1/M5/M15/H1` (H4/D1/D rejected) as a governed safety bound, not a runtime
default. **None of these are set by this PR** — the capability is dark until a separate activation WO.
