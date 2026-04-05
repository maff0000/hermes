# WO-HERMES-CANARY-MARKET-HOURS-0002 — Evidence Summary

## Date: 2026-04-01
## Purpose: Add market-hours awareness to canary — eliminate false non-trading-hour alerts

## Design: 2-Layer Expected-Silence Model

### Layer 1 — Governed UTC Policy (authoritative)
- hermes_market_hours table: per-instrument, UTC-only, weekday open/close
- 12 instruments seeded (forex + metals: Sunday 22:00 → Friday 22:00 UTC)
- No inheritance between instruments
- Missing policy fails safe toward alerting (truth_expected=True)

### Layer 2 — Source-State Hint (augmenting, not authoritative)
- Canary optionally reads HERMES /health endpoint
- If /health reports market_open=False, canary emits AMBER/SOURCE_HALTED
- If /health is unreachable, Layer 1 UTC policy alone decides
- No new OANDA API calls from the canary
- Canary is NOT hostage to OANDA availability

### Decision Order
1. UTC policy says closed → AMBER/MARKET_CLOSED (authoritative)
2. UTC policy says open, /health says market_open=False → AMBER/SOURCE_HALTED (hint)
3. UTC policy says open, /health unavailable → normal freshness rules (fail safe)
4. UTC policy says open, /health says market_open=True → normal freshness rules

## What was created

### New table: hermes_market_hours
- 12 rows, one per instrument, no inheritance
- Columns: instrument, market_type, open_day_utc, open_time_utc, close_day_utc, close_time_utc, maintenance windows
- All with description + llm_reasoning

### New module: market_hours_policy.py
- MarketHoursPolicy class — reads hermes_market_hours, evaluates truth_expected
- Reusable by canary, watchdog, proof program
- UTC only, per-instrument, cached

### Modified: hermes_signal_truth_canary.py
- Added truth_expected field to output
- Layer 1: UTC policy check before freshness evaluation
- Layer 2: /health source-state hint (optional, non-blocking)
- Market closed → AMBER, no false RED, no we_dont_have_a_signal

## Output Contract

Market closed example:
  truth_expected: false, health_state: AMBER, reason_code: MARKET_CLOSED

Market open + fresh:
  truth_expected: true, health_state: GREEN

Market open + stale:
  truth_expected: true, health_state: RED, reason_code: HERMES_CANARY_M1_STALE

## Tests (8/8 PASS)

| # | Test | Result |
|---|------|--------|
| 1 | Monday midday → MARKET_OPEN | PASS |
| 2 | Saturday → MARKET_CLOSED, no warning | PASS |
| 3 | Sunday before 22:00 → MARKET_CLOSED | PASS |
| 4 | Sunday 22:00 → MARKET_OPEN | PASS |
| 5 | Friday 21:59 → MARKET_OPEN | PASS |
| 6 | Friday 22:00 → MARKET_CLOSED | PASS |
| 7 | Per-instrument isolation | PASS |
| 8 | No-policy fails safe (truth_expected=True) | PASS |

## Follow-on Note
- v2 may add holiday calendar if needed (Christmas, New Year closures)
- v2 may add per-instrument maintenance windows if OANDA/IBKR differ
- Current v1 covers the operational need: no false weekend/off-hours alerts
