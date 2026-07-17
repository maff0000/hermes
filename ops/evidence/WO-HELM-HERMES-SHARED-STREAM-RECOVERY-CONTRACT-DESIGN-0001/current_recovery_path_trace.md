# Current recovery path trace (defect) — grounded in deployed 71ea3bd (== files at 8de293de)

## Call/state chain
```
utils/watchdog.py:519  _run() loop every watchdog_interval_sec
utils/watchdog.py:535  _evaluate_instruments(tick_threshold, candle_threshold)
utils/watchdog.py:560    is_truth_expected(instrument, now)  [DstAwareMarketHours Mode-C seam]
                          XAU metals-break -> False -> AMBER/MARKET_CLOSED -> continue (NOT red)   [CORRECT]
                          SPX500_USD/WTICO_USD schedule None -> True -> tick/M1 freshness -> stale -> RED, red_count++
utils/watchdog.py:613    _evaluate_per_instrument_recovery(now, red_count)
utils/watchdog.py:643      red_count>0 & FLOWING -> StreamState.PARTIAL_FLOWING
utils/watchdog.py:662      sustained = [inst age>=per_instrument_sustained_red_threshold_sec(=300)]
utils/watchdog.py:671      cooldown per_instrument_recovery_cooldown_sec(=600)
utils/watchdog.py:681      rate-limit per_instrument_max_recovery_attempts_per_hour(=3)
utils/watchdog.py:691      *** inst,age = sustained[0]  ->  AUTHORITY = ONE instrument's freshness ***
utils/watchdog.py:696      _recovery_request_pending = True; reason="sustained_red instrument=SPX500_USD ..."
main.py:731            consume_recovery_request()  -> StreamSilentStallError("Per-instrument recovery: ...")
main.py:912            outer except -> set_stream_state(RECOVERING); record_recovery_attempt();
main.py:936..960          exponential backoff; oanda_adapter.disconnect(); connect(); enter_proof_window(); backfill_gap()
```

## The coupling to correct
`instrument RED (freshness) -> shared transport mutation`, with NO independent transport-fault evidence.
There is no socket / heartbeat / shared-progress condition anywhere in `_evaluate_per_instrument_recovery`.

## Transport-alive signal that WAS available and ignored
`adapters/oanda.py:141` bumps `AdapterHealth.last_tick_at` on HEARTBEAT (and PRICE). Heartbeat freshness is a
shared, instrument-independent transport-alive proof. `main.py:707` already detects a genuine full-stream stall
(no line at all) via `asyncio.TimeoutError`. Neither was consulted before pulling the reconnect lever.

## OANDA v20 constraint
No per-instrument resubscribe (adapter code comments, watchdog.py:621-625). Only recovery action = full-stream
reconnect. The fix makes that lever require transport authority; it does not invent a per-instrument lever.
