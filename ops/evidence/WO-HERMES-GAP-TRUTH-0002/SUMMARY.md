# WO-HERMES-GAP-TRUTH-0002 — Evidence Summary

## Date: 2026-03-31
## Author: Helm (Claude Code)

## What was built

1. **hermes_data_gaps table** — persistent gap ledger with:
   - Status transitions: DETECTED → CONFIRMED → IN_REPAIR → RESOLVED → INVALIDATED
   - Idempotent via scan_hash (SHA256 of artifact+instrument+timeframe+window)
   - description + llm_reasoning on every row
2. **utils/gap_scanner.py** — deterministic gap detection engine:
   - GapScanner: compares expected timestamp sequences against actual DB rows
   - GapLedger: persists/reconciles gap records idempotently
   - Market-hours aware (forex Sunday 22:00 → Friday 22:00 UTC)
   - Supports M1/M5/M15/H1 candles and M5/M15 signals
   - CLI with scan + verify commands
3. **Health integration** — watchdog health snapshot includes:
   - unresolved_gap_count
   - latest_gap_summary
4. **Verify command** — non-zero exit on unresolved gaps

## Tests (8/8 PASS)

| # | Test | Result |
|---|------|--------|
| 1 | Market hours logic (6 checks) | PASS |
| 2 | Expected timestamp generation | PASS |
| 3 | Real incident M1 gap (287 missing, 02:55→07:41) | PASS |
| 4 | Higher TF gaps (M15: 19 missing, H1: 6 missing) | PASS |
| 5 | Market-closed suppression (Saturday = 0 gaps) | PASS |
| 6 | Signal gap logic (M15 detected, M1 correctly ignored) | PASS |
| 7 | Idempotent persistence (0 new on rescan) | PASS |
| 8 | Gap count for health integration | PASS |

## Real incident detection proof

Scanner found the 2026-03-31 zombie-stream outage precisely:
- M1: 02:55 → 07:41 UTC, 287 missing candles
- M15: 02:45 → 07:15 UTC, 19 missing candles + 19 missing signals
- H1: 02:00 → 06:00 UTC, 5 missing candles
- M5: no gaps (startup backfill recovered these)

## Key design decisions

- Gap truth based on expected sequence continuity, not vibes
- Expected sequences computed from timeframe cadence + market hours
- Signal gaps only detected where HERMES structurally produces them (M5/M15)
- Idempotent via scan_hash — rescans reconcile, never duplicate
- Market closed = zero expected timestamps = zero false gaps
