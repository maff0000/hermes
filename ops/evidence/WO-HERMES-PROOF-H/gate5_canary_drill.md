# Gate 5 — Canary Green + Drill Evidence

## Date: 2026-04-01
## Method: iptables block on OANDA traffic (stream + API)

### Timeline

| Time (UTC) | Phase | Canary | Watchdog |
|------------|-------|--------|----------|
| 07:38:37 | Baseline | GREEN, M1 age 98s | GREEN/FLOWING |
| 07:38:40 | Traffic blocked | — | — |
| 07:40:09 | Stale detected | — | CRITICAL STALE_CANDLE (189s), incident #10, STALE |
| 07:42:13 | Canary check | RED, M1_STALE, M1 age 313s, we_dont_have_a_signal=true | STALE |
| 07:42:20 | Traffic restored | — | — |
| 07:45:10 | Recovery | GREEN, M1 age 70s | GREEN/FLOWING, 0 incidents |

### Result: PASS
- Canary correctly tripped during stale condition
- Watchdog independently detected and opened incident
- Both recovered to GREEN after traffic restored
- truth_expected=true throughout (market open)
- we_dont_have_a_signal correctly set during stale, cleared on recovery
