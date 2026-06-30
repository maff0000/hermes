# 01 — Discovered D1 producer files/modules

No assumption of `utils/d1_publisher.py`. Actual discovered modules:

| Module | Role |
|---|---|
| `utils/candle_d1_derivation_v1.py` | **Pure** D1-from-6×H4 derivation (no I/O). Owns the fixed 22:00 UTC anchor (`d1_bucket_open`, `d1_child_h4_opens`, `assert_d1_open_anchor`, `D1_CHILD_H4_OPEN_HOURS_UTC`, `D1_EXPECTED_CHILDREN=6`). |
| `utils/candle_d1_publish_wire_v1.py` | **The live D1 producer** `CanonicalD1Producer` — holds the in-memory accumulation buffer `self._buf` / `self._current` and the `on_h4_close` seal-on-rollover loop. `h4_child_is_complete` is the ratified child-eligibility rule. `build_d1_producer_from_env` is the gate. **← the reset trap lives here.** |
| `utils/candle_h4_publish_wire_v1.py` | Builds the D1 producer inside `build_h4_producer_from_env()` and feeds it sealed H4 via `_offer_to_d1 → dp.on_h4_close(_SealedH4View(env))`. Exposes `.d1_producer`. |
| `utils/candle_history_v1.py` | Governed H4 history keyspace: `hermes:candles:XAU_USD:H4:history:v1:{epoch}` + `:index` ZSET. **← the warm-start read source.** |
| `main.py` (lifespan ~L1182) | Builds `state.candle_h4_producer = _build_h4_producer()` → the D1 producer init site. |

## Reset trap (root cause)
`CanonicalD1Producer._buf`/`_current` are volatile process memory, and `_seal_and_publish` only fires on a
bucket **roll-over** (when the next D1 day's first H4 arrives). A restart mid-day flushes the buffer, so the
day's already-sealed H4 children are lost and the first clean 6/6 D1 seal is pushed forward every restart.

## Fix surface (tight diff — 4 files)
- `utils/candle_d1_publish_wire_v1.py` — add `CanonicalD1Producer.hydrate()` + `hydration_reject_reason()` + warm-start metrics.
- `utils/candle_d1_hydration_v1.py` — **new**: gate + bounded H4-history reader + child adapter + `warmstart_d1_from_env`.
- `main.py` — call `warmstart_d1_from_env(state.candle_h4_producer.d1_producer, …)` at D1-producer init (gated).
- `tests/test_candle_d1_hydration_v1.py` — **new**: 20 tests.
