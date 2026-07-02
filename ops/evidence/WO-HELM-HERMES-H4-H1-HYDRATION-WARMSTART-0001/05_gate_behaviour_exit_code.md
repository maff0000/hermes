# 05 — Gate behaviour + exit-code proof

Gates (externally-supplied config, default OFF): `HERMES_H4_WARMSTART_ENABLED=false`, `HERMES_H4_WARMSTART_AUTHORISED=false`.

| State | Behaviour | Test |
|---|---|---|
| `ENABLED` unset/false | Safe no-op cold-start: `{skipped:true, reason:COLD_START_STRATEGY_ACTIVE, buffer_length:0}`; logs `"H4 warmstart skipped: cold-start strategy active"`; legacy behaviour preserved. | `test_gate_cold_start_safe_noop` |
| `ENABLED=true` + `AUTHORISED=false` | **Fail loud** `SystemExit(104)` with stderr+log evidence. | `test_gate_enabled_unauthorised_exits_104` (asserts `code==104`) |
| both on | Bounded hydration of the current H4 block, then live hooks continue. | `test_warmstart_reads_redis_h1_history_no_writes` |
| both on, H4 producer disabled (default) | `{attempted:false, reason:H4_PRODUCER_DISABLED}` — warm-start never enables H4 publication. | `test_warmstart_disabled_producer_is_noop` |

`HALT_CODE=104`. In `main.py` the warm-start call re-raises `SystemExit` (boot abort) and safe-falls-back on any
other exception (empty buffer, service continues). Cold-start log literal: `H4 warmstart skipped: cold-start strategy active`.
