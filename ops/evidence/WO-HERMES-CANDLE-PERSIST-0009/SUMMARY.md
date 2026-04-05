# WO-HERMES-CANDLE-PERSISTENCE-DIVERGENCE-0009 — Evidence Summary

## Date: 2026-03-31
## Status: ROOT-CAUSED AND REMEDIATED

## Forensic Timeline (suspect window)

| Time (UTC) | Watchdog | DB M1 (XAU_USD) | DB Age | Canary |
|------------|----------|-----------------|--------|--------|
| ~20:58 | GREEN/FLOWING | 20:58:00 | 0s | GREEN |
| ~21:18 | GREEN/FLOWING | 20:58:00 | 1207s | RED/M1_STALE |
| ~21:25 | GREEN/FLOWING | 20:58:00 | 1627s | RED/M1_STALE |

Ticks flowing continuously (tick_age < 1s). Candle aggregation logs firing every second. DB M1 candles frozen at 20:58. Watchdog GREEN because in-memory M1 timestamp kept advancing.

## Root Cause: 3 compounding defects

### Defect 1: Per-call connection creation in save_candle()
save_candle() at main.py:361 created a new pymysql.connect() for EVERY candle write. With 12 instruments x 5 timeframes, this creates 60+ connections per minute. Under load, connections exhaust or timeout. pymysql.connect() failures were caught but logged through a logger that may not flush to journal.

### Defect 2: Return value ignored at call site
main.py:672 called save_candle(candle) but never checked the return value. Fire and forget. record_candle() was called unconditionally, counting candles that never reached the DB.

### Defect 3: Watchdog M1 truth updated before persistence confirmed
main.py:676 called state.watchdog.record_candle_m1(candle.timestamp) UNCONDITIONALLY after save_candle(), even if save failed. This made the watchdog believe M1 was fresh (in-memory) while the DB was stale. The watchdog's health model trusted completion events, not persistence success.

## Remediation

### Fix 1: Shared connection for candle writer
Replaced per-call pymysql.connect() with a shared _candle_writer_conn using ping/reconnect pattern. Same fix as WO-0001A applied to the watchdog — lesson #13 from the lessons inventory.

### Fix 2: Return value checked
Changed call site from fire-and-forget to candle_saved = save_candle(candle). On failure, logs HERMES_CANDLE_WRITE_FAILED warning.

### Fix 3: record_candle_m1 only on success
Watchdog's in-memory M1 timestamp is ONLY updated when save_candle() returns True. If the candle doesn't reach the DB, the watchdog doesn't pretend it did. This eliminates the truth divergence.

## Design Assessment

Q: Should HERMES health require BOTH tick flow freshness AND persisted candle freshness?
A: YES. The fix achieves this by making record_candle_m1() conditional on successful DB write. The watchdog's candle_staleness_threshold (180s) will now correctly fire if candles stop being persisted, even if ticks continue flowing.

## Fault Codes
- HERMES_CANDLE_WRITE_FAILED — candle DB write failed
- HERMES_CANDLE_WRITE_CONN_FAILED — candle writer connection failed

## Acceptance
- [x] Per-call connection churn eliminated (shared connection)
- [x] save_candle() failure is visible in logs
- [x] Watchdog M1 truth only updated on confirmed persistence
- [x] Truth divergence between in-memory and DB eliminated
- [x] Watchdog will go STALE/RED if candle persistence fails while ticks flow
