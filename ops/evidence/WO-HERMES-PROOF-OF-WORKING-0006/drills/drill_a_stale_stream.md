# Drill A — Forced Stale Stream

## Date: 2026-03-31
## Method: iptables block on OANDA traffic

### Timeline

| Time | Event | Health | Stream | Fault |
|------|-------|--------|--------|-------|
| 11:52:48 | Baseline | GREEN | FLOWING | null |
| 11:52:57 | Traffic blocked (iptables DROP) | — | — | — |
| 11:54:14 | Watchdog detects stale candle | RED | STALE | HERMES_STREAM_STALE_CANDLE |
| 11:54:14 | Incident #1 opened (CRITICAL) | — | — | — |
| ~11:55:33 | Health confirmed RED | RED | STALE | HERMES_STREAM_STALE_CANDLE |
| ~11:55:45 | Traffic restored (iptables flush) | — | — | — |
| 11:56:34 | OANDA disconnect detected | — | RECOVERING | — |
| ~11:57:14 | Reconnect + proof window passes | GREEN | FLOWING | null |
| ~11:57:14 | Incident closed | — | — | — |

### Result: PASS
- Stale detected within threshold (120s tick + 180s candle)
- No zombie state
- Incident opened and closed
- Full GREEN recovery after traffic restored
