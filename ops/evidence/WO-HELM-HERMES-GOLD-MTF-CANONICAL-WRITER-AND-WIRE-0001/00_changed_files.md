# Changed Files

## Production code (3 files)
| File | Change |
|------|--------|
| `utils/candle_publisher_v1.py` | + `SerializingCandleCanonicalWriter`, `build_canonical_write_plan`, `assert_canonical_key`, `CANONICAL_PUBLISH_TIMEFRAMES`, `_CANONICAL_ALIAS_DENY` |
| `utils/candle_runtime_seam_v1.py` | + `CanonicalCandleForwardSeam`, `build_canonical_seam`, `_canonical_config_from_env`, `_real_canonical_redis_client`; factory `SINK=canonical` branch; `FAULT_CANONICAL_CLIENT`; `CANONICAL_SINK`/`FORBIDDEN_CANONICAL_ALIASES` |
| `models/candle.py` | + `Timeframe.M15` + M15 branch in `_get_candle_time` (15-min truncation) |

## NOT changed (live wiring already present)
- `main.py` — already emits `candle_forward_emitter.emit(candle=candle)` per completed candle (L796-798) and builds the emitter from the seam factory (L1154). No change required.
- `signal_builder.py` — live aggregator already M15-capable (TIMEFRAMES has M15; generic `_get_candle_start`); M15 is config-driven via `CANDLE_TIMEFRAMES`. No change required.

## Tests (4 files)
| File | Change |
|------|--------|
| `tests/test_candle_canonical_writer_and_wire_v1.py` | NEW — 20 cases (writer, seam, gating, M15, wiring) |
| `tests/test_candle_forward_runtime_seam_v1.py` | updated canonical-path tests (now a governed path, not blanket fail-loud) |
| `tests/test_candle_shadow_writer_wire_v1.py` | updated `canonical sink without config fails loud` |
| `tests/test_models.py` | `test_flush_all` now expects 5 candles incl. M15 |
