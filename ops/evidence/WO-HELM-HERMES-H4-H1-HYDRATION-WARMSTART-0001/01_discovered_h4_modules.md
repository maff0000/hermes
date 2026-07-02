# 01 — Discovered H4 producer modules + buffer owner + H1 hook

| Module | Role |
|---|---|
| `utils/candle_h4_derivation_v1.py` | Pure H4-from-4×H1 derivation (no I/O). Owns the NY-5PM fixed-22:00-UTC H4 grid: `h4_bucket_open`, `H4_ANCHOR_HOURS_UTC=(22,2,6,10,14,18)`, `H4_EXPECTED_CHILDREN=4`, `H1_TIMEFRAME`. |
| `utils/candle_h4_publish_wire_v1.py` | **The live H4 producer** `CanonicalH4Producer` — owns the in-memory H1 buffer `self._buf` / `self._current`; **`on_h1_close`** is the H1→H4 hook; `_seal_and_publish` seals on bucket roll-over. **← the reset trap lives here.** |
| `utils/candle_history_v1.py` | Governed H1 history keyspace `hermes:candles:XAU_USD:H1:history:v1:{epoch}` + `:index` ZSET. **← the warm-start read source.** |
| `main.py` (lifespan ~L1182) | Builds `state.candle_h4_producer = _build_h4_producer()` (which builds + attaches the D1 producer). D1-producer init site. |

## Discovered facts
- **H1→H4 buffer owner:** `CanonicalH4Producer._buf` (instrument → {h4_bucket_open_epoch: [h1_child,...]}) + `_current`.
- **H1 close hook:** `CanonicalH4Producer.on_h1_close(h1_candle)` — buckets each H1, seals the previous bucket on roll-over.
- **Reset trap (root cause):** `_buf`/`_current` are volatile process memory. A restart mid-H4-bucket loses the
  bucket's already-closed H1 children → the bucket seals INCOMPLETE. This is exactly what sealed the 2026-07-01
  06:00 H4 at 2/4 (all 4 H1 existed in history), leaving the D1 day at 5/6 and correctly blocking D1.

## Fix surface (tight diff — 4 files)
- `candle_h4_publish_wire_v1.py` — `CanonicalH4Producer.hydrate()` + `h1_child_is_complete()` + `h1_hydration_reject_reason()` + warm-start metrics.
- `candle_h4_hydration_v1.py` (new) — gate + bounded H1-history reader + `HydratedH1Child` + `warmstart_h4_from_env`.
- `main.py` — `warmstart_h4_from_env(state.candle_h4_producer, …)` at H4-producer init (gated; before the D1 warm-start).
- `tests/test_candle_h4_hydration_v1.py` (new, 20 tests).
