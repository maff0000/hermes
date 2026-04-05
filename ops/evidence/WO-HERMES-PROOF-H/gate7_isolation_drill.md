# Gate 7 — Per-Instrument Isolation Evidence

## Date: 2026-04-01

### Test Suite Evidence (6/6 PASS)
- XAU tick stale while EUR/JPY fresh → XAU RED, others GREEN, zero contamination
- XAU M1 stale (ticks fresh) → XAU RED, catches exact WO-0009 divergence class
- Market closed → AMBER/MARKET_CLOSED, not false RED
- Global health NOT falsely GREEN when any instrument RED

### Production Evidence (2026-03-31 ~21:00 UTC)
- OANDA metals (XAU/XAG/XPT/XCU) halted while forex continued
- Per-instrument health would have shown: metals RED, forex GREEN, global AMBER
- Documented in WO-0009 forensic update

### Live Verification (2026-04-01)
- 12/12 instruments GREEN in hermes_instrument_health table
- Per-instrument tick/M1 age tracked independently
- DB persistence confirmed

### Result: PASS
