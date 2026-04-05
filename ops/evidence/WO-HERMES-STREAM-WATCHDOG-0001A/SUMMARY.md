# WO-HERMES-STREAM-WATCHDOG-0001A — Remediation Evidence

## Date: 2026-03-31
## Problem: WO-1 watchdog opened DB connection per tick, causing silent persistence failure under real load.

## Root cause
- record_tick() called update_health() which opened a new pymysql connection per tick
- Under real tick rates (multiple/sec), this caused connection churn and silent failures
- Watchdog logger used standalone logging.getLogger() not visible in service journal/GELF
- Health DB row went stale while service appeared to run normally

## Fix
1. record_tick() now updates IN-MEMORY state only (zero DB cost)
2. DB persistence occurs on:
   - State transitions (dirty flag)
   - Periodic heartbeat (watchdog interval)
   - Fault/incident events (immediate)
3. HealthPersistence reuses DB connection (ping+reconnect pattern)
4. Logger injected from service (same GELF/journal path)
5. get_health_snapshot() reads in-memory state (no DB read on hot path)

## Tests (9/9 PASS)

| # | Test | Key Metric | Result |
|---|------|-----------|--------|
| 1 | No per-tick DB writes | 1000 ticks → 0 DB writes | PASS |
| 2 | Cadence persistence | 1 persist per cycle | PASS |
| 3 | Snapshot from memory | 0.0s tick age (no DB) | PASS |
| 4 | Stale detection (memory) | STALE_TICK fires correctly | PASS |
| 5 | Burst stability | 10000 ticks in 6.7ms | PASS |
| 6 | Logger injection | State transitions in service logger | PASS |
| 7 | False reconnect (regression) | Still detects FALSE_CONNECT | PASS |
| 8 | Recovery exhaustion (regression) | Fatal callback still fires | PASS |
| 9 | Promotion persists | FLOWING written to DB immediately | PASS |
