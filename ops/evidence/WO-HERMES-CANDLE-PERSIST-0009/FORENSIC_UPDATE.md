# WO-HERMES-CANDLE-PERSIST-0009 — Forensic Update

## Date: 2026-03-31 21:31 UTC

## Updated Root Cause: TWO causes, not one

### Cause 1: OANDA metals stream intermittently halts
OANDA stops sending tick updates for all 4 metals (XAU, XAG, XPT, XCU) while
forex pairs continue. This creates per-instrument staleness invisible to the
global-last-tick watchdog.

Evidence:
- Forex ticks: all 8 pairs current at 21:31:29 UTC
- Metal ticks: all 4 frozen at 21:27:37 UTC (service restart time, no new since)
- DB candles: forex instruments current (21:30), metals frozen (20:58)
- This is NOT a HERMES bug — it is a broker source behavior

### Cause 2: save_candle() defects (FIXED in this WO)
The three persistence defects (per-call connection, ignored return, watchdog
recording before confirmation) compound the problem by making it invisible.
These are now fixed. But they were NOT the primary cause of the missing candles.

## Architecture Gap Exposed
The watchdog tracks GLOBAL last-tick freshness. When one instrument goes stale,
if any other instrument is still flowing, the watchdog remains GREEN.

This proves the per-instrument isolation requirement from the resilience epic
is not theoretical — it is a live production need. The watchdog health model
needs per-instrument freshness, not just global.

## Per-Instrument Staleness (at 21:31 UTC)

| Instrument | Last Tick | Age | M1 Latest | Status |
|------------|-----------|-----|-----------|--------|
| EUR_USD | 21:31:29 | <1s | 21:30 | HEALTHY |
| GBP_USD | 21:31:24 | 6s | 21:30 | HEALTHY |
| USD_JPY | 21:31:29 | <1s | 21:30 | HEALTHY |
| AUD_USD | 21:31:29 | <1s | 21:30 | HEALTHY |
| NZD_USD | 21:31:29 | <1s | 21:30 | HEALTHY |
| USD_CAD | 21:31:28 | 2s | 21:30 | HEALTHY |
| USD_CHF | 21:31:29 | <1s | 21:30 | HEALTHY |
| EUR_GBP | 21:31:23 | 7s | 21:29 | HEALTHY |
| **XAU_USD** | **21:27:37** | **237s** | **20:58** | **STALE** |
| **XAG_USD** | **21:27:37** | **237s** | **20:58** | **STALE** |
| **XPT_USD** | **21:27:37** | **237s** | **20:58** | **STALE** |
| **XCU_USD** | **21:27:37** | **237s** | **20:58** | **STALE** |

## Recommendation
1. The save_candle() fix in this WO is correct and should stay deployed
2. A follow-on WO should add per-instrument health to the watchdog
3. The canary correctly detected per-instrument staleness (it checks XAU_USD specifically)
4. OANDA metals stream halts should be investigated separately
