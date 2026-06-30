# 05 — Gate behaviour + exit-code proof

Gates (externally-supplied config, default OFF):
- `HERMES_D1_WARMSTART_ENABLED=false`
- `HERMES_D1_WARMSTART_AUTHORISED=false`

| State | Behaviour | Test |
|---|---|---|
| `ENABLED` unset/false | **Safe no-op cold-start**: returns `{skipped:true, reason:COLD_START_STRATEGY_ACTIVE, buffer_length:0}`; logs `"D1 warmstart skipped: cold-start strategy active"`; legacy behaviour preserved (empty buffer, live tracking). | `test_gate_cold_start_safe_noop` |
| `ENABLED=true` + `AUTHORISED=false` | **Fail loud**: `raise SystemExit(103)` with stderr + log evidence ("enabled without authorisation"). | `test_gate_enabled_unauthorised_exits_103` (asserts `e.value.code == 103`) |
| `ENABLED=true` + `AUTHORISED=true` | Bounded hydration of the current block, then live hooks continue. | `test_warmstart_reads_redis_h4_history_no_writes` |
| both on, but D1 producer disabled (default) | `{attempted:false, reason:D1_PRODUCER_DISABLED}` — warm-start NEVER enables D1 publication (separate gate). | `test_warmstart_disabled_producer_is_noop` |

`HALT_CODE = 103` in `utils/candle_d1_hydration_v1.py`. In `main.py` the warm-start call re-raises `SystemExit`
(boot abort, process-fatal) and safe-falls-back (empty buffer, service continues) on any **other** exception.

Cold-start log line (literal): `D1 warmstart skipped: cold-start strategy active` (`COLD_START_LOG`).
