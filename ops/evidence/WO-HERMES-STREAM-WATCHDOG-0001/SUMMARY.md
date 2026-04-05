# WO-HERMES-STREAM-WATCHDOG-0001 — Evidence Summary

## Date: 2026-03-31
## Author: Helm (Claude Code)

## What was built

1. **hermes_service_health table** — single authoritative health row per service/env
2. **hermes_incidents table** — persistent incident audit trail
3. **utils/watchdog.py** — runtime staleness watchdog with:
   - Tick staleness detection (market-hours gated)
   - M1 candle progression detection
   - Reconnect proof-window validation
   - Recovery exhaustion → fatal exit path
   - Incident lifecycle (open on fault, close on recovery)
4. **main.py modifications**:
   - Watchdog wired into lifespan() startup/shutdown
   - /health endpoint replaced: static stub → authoritative truth
   - oanda_stream_task() reconnect: proof window instead of false success
   - Tick/candle/signal events forwarded to watchdog
   - Fatal exit callback for recovery exhaustion
5. **Config keys** in zeusv4_config (governed, with llm_reasoning):
   - hermes_tick_staleness_threshold_sec (120)
   - hermes_candle_staleness_threshold_sec (180)
   - hermes_recovery_proof_window_sec (30)
   - hermes_max_recovery_attempts (5)
   - hermes_watchdog_interval_sec (15)

## Tests (7/7 PASS)

| # | Test | Fault Code | Result |
|---|------|-----------|--------|
| 1 | False reconnect detection | HERMES_RECOVERY_FALSE_CONNECT | PASS |
| 2 | Tick staleness during market hours | HERMES_STREAM_STALE_TICK | PASS |
| 3 | M1 candle stagnation | HERMES_STREAM_STALE_CANDLE | PASS |
| 4 | Recovery exhaustion → fatal exit | HERMES_RECOVERY_EXHAUSTED | PASS |
| 5 | Market-closed suppression | (none — correctly suppressed) | PASS |
| 6 | Data flow promotion after proof | (GREEN after tick) | PASS |
| 7 | Incident lifecycle (open → close) | (lifecycle verified) | PASS |

## Acceptance criteria met

- [x] A false reconnect cannot remain GREEN
- [x] A process-alive but data-dead service becomes RED
- [x] Incidents persist in DB
- [x] Stale flow triggers recovery state change
- [x] Exhausted recovery exits non-zero (fatal callback)
- [x] Market-closed quiet periods do not false-trigger
- [x] /health never lies — reports authoritative runtime truth

## Trigger incident

This WO was triggered by the 2026-03-31 zombie-stream incident:
- OANDA stream disconnected at 02:25 UTC
- False reconnect at 02:25 UTC
- 5-hour silence (02:55 → 08:41 UTC)
- Service appeared healthy while producing zero data

With this watchdog in place, that incident would have been detected within
~135 seconds (15s watchdog interval + 120s tick threshold) and escalated
through the recovery path.
