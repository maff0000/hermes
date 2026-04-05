# WO-HERMES-PER-INSTRUMENT-HEALTH-0010 — Evidence Summary

## Date: 2026-04-01
## Purpose: Per-instrument health truth — eliminate false global GREEN

## What was built

### New table: hermes_instrument_health
One row per enabled instrument. Updated by watchdog every cycle.
Fields: instrument, truth_expected, last_tick_utc, last_m1_persisted_utc,
tick_age_seconds, m1_age_seconds, health_state, reason_code, updated_at_utc.
12 instruments seeded as RED (must be proven healthy).

### Watchdog enhancements
- Per-instrument in-memory tick/M1 tracking
- record_tick(tick_utc, instrument) — tracks per-instrument
- record_candle_m1(candle_utc, instrument) — tracks per-instrument
- _evaluate_instruments() — evaluates each instrument independently
- _persist_instrument_health() — writes to hermes_instrument_health
- Global health derived: GREEN only if ALL truth-expected instruments GREEN

### /health endpoint extended
instruments field in health snapshot shows per-instrument state:
  {instruments: {XAU_USD: {health_state: RED, reason_code: ...}, ...}}

### Fault codes
- HERMES_INSTRUMENT_TICK_STALE — per-instrument tick stale
- HERMES_INSTRUMENT_M1_STALE — per-instrument M1 DB stale

## Global Health Derivation Rule (v1)
- GREEN: all truth-expected instruments GREEN
- AMBER: one or more instruments degraded but not systemically failed
- RED: critical/systemic failure (existing watchdog logic)

If ANY truth-expected instrument is RED, global cannot be GREEN.

## Tests (6/6 PASS)

| # | Test | Key Result |
|---|------|-----------|
| 1 | All instruments healthy | All GREEN, global GREEN |
| 2 | XAU tick stale, EUR/JPY fresh | XAU RED, EUR GREEN, JPY GREEN, zero contamination |
| 3 | XAU M1 stale (ticks fresh) | XAU RED (INSTRUMENT_M1_STALE) — catches WO-0009 class |
| 4 | Market closed | AMBER/MARKET_CLOSED, not RED |
| 5 | Snapshot per-instrument | instruments field with state+reason per instrument |
| 6 | Global not GREEN when inst RED | Global AMBER when XAU RED |

## Production scenario this eliminates
Before: OANDA metals halt, forex continues → global GREEN, XAU dead
After: OANDA metals halt → XAU RED, EUR GREEN, global AMBER

## Can WO-H proof program proceed?
YES. This WO strengthens the proof foundation. The per-instrument isolation
proof (Gate 7) can now use the watchdog's native per-instrument health,
not just the canary.
