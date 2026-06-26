# Implementation Summary — Canonical Candle Writer + Live Wiring + M15

**WO:** WO-HELM-HERMES-GOLD-MTF-CANONICAL-WRITER-AND-WIRE-0001 · **Owner:** HELM (HERMES lane)
**Type:** code-only, PR-gated · **Base:** `99c72e3` (merged main) · **Posture:** capability built, **NOT activated**

## What was missing (why the activation WO halted)
Merged main had no canonical writer: `build_candle_emitter_from_env` always returned a disabled no-op,
the seam fail-loud-rejected `SINK=canonical`, and there was no canonical writer class. This WO builds
exactly that capability — gated off by default.

## What this WO adds
### 1. Canonical writer (`utils/candle_publisher_v1.py`)
- `SerializingCandleCanonicalWriter` — real-client canonical write path. Requires
  `assert_canonical_allowed()` (publish_enabled AND publish_authorised). JSON-serialises before
  `client.set` (a real client is never handed a dict). Writes only versioned canonical keys. Redis errors
  **propagate** (never swallowed at the writer).
- `build_canonical_write_plan(envelope)` — validates the governed v1 contract (rejects bad
  OHLC/geometry/forbidden fields) then guards the key.
- `assert_canonical_key(key)` — last-line guard: must be `hermes:candles:{instr}:{tf}:latest:v1`, a
  publish-allowed timeframe (`CANONICAL_PUBLISH_TIMEFRAMES = M1/M5/M15/H1`), and a non-alias instrument
  (`_CANONICAL_ALIAS_DENY = XAUUSD`). Codes `GOV-CANDLE-PUB-CANON-KEY-001..005`, `-WR-001..003`.

### 2. Sink/config + seam (`utils/candle_runtime_seam_v1.py`)
- `CanonicalCandleForwardSeam` — mirrors the shadow seam: skips unsupported TFs (D1/H4/D →
  `UNSUPPORTED_TIMEFRAME`), canonicalises instrument (XAUUSD→XAU_USD, no dual-publish), surfaces
  validate/emit failures via metrics + rate-limited log (no silent failure).
- `build_canonical_seam`, `_canonical_config_from_env`, `_real_canonical_redis_client`.
- `build_candle_forward_seam_from_env`: `SINK=canonical` now builds the canonical seam, but **fails loud**
  unless `HERMES_CANDLE_PUBLISH_ENABLED` + `_AUTHORISED` + explicit `HERMES_CANDLE_CANONICAL_REDIS_HOST/PORT/DB`
  are all set. `live`/`prod`/unknown sinks still fail loud. Default (forward disabled) → `DisabledCandleEmitter`.

### 3. Live wiring — already present, unchanged
`main.py:796-798` already calls `state.candle_forward_emitter.emit(candle=candle)` per completed candle,
and builds the emitter from the seam factory (`main.py:1154`). **No `main.py`/`signal_builder.py` change
needed** — when canonical config is later set, the existing wiring routes completed candles to the new
canonical seam. Verified by `test_live_candle_object_flows_through_seam` (a `signal_builder.Candle`
publishes a canonical key).

### 4. M15 production (`models/candle.py`)
- `Timeframe.M15 = 15` + an M15 branch in `_get_candle_time` (15-min truncation).
- The live `signal_builder.CandleAggregator` was already M15-capable (its `TIMEFRAMES` dict has M15 and
  `_get_candle_start` is generic); M15 production there is **config-driven** via `CANDLE_TIMEFRAMES`
  (default unchanged). D1 still produced internally; **never published** by the canonical writer.

## Default behaviour preserved
Forward disabled by default → no-op. Existing M1/M5/H1/D1 behaviour preserved. No deploy/activation.
