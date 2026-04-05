# WO-HERMES-BACKFILL-ENGINE-0004 — Evidence Summary

## Date: 2026-03-31
## Author: Helm (Claude Code)

## What was built

1. **hermes_recovery_jobs + hermes_recovery_job_items tables** — full execution audit trail
2. **utils/recovery_executor.py** — deterministic recovery engine:
   - RecoveryExecutor: plans via WO-3 library, executes in dependency order
   - JobLedger: records every step with rows, timing, fault codes
   - BROKER_FETCH: candles from OANDA REST API with pagination
   - WINDOW_PLUS_LOOKBACK: signal recompute from SignalComputer
   - Post-repair validation via WO-2 gap scanner
   - Gap ledger reconciliation (marks gaps RESOLVED)
   - CLI: recover-window, recover-gap-id, verify-window

## Real incident repair proof (2026-03-31 outage)

### Job #2 — Candle recovery
| Step | Artifact | Strategy | Rows |
|------|----------|----------|------|
| 10 | CANDLE_M1 | BROKER_FETCH | 287 |
| 20 | CANDLE_M5 | BROKER_FETCH | 59 |
| 30 | CANDLE_M15 | BROKER_FETCH | 21 |
| 40 | CANDLE_H1 | BROKER_FETCH | 7 |

### Job #3 — Signal recovery
| Step | Artifact | Strategy | Rows |
|------|----------|----------|------|
| 20 | CANDLE_M5 | BROKER_FETCH | 59 (idempotent) |
| 30 | CANDLE_M15 | BROKER_FETCH | 21 (idempotent) |
| 50 | SIGNAL_M5 | WINDOW_PLUS_LOOKBACK | 58 |
| 60 | SIGNAL_M15 | WINDOW_PLUS_LOOKBACK | 19 |

### Post-repair
- verify-window: **PASS** — no gaps in repaired window
- Gap ledger: gap #1 (287 M1 missing) → **RESOLVED**
- Idempotent rerun (Job #4): 287 rows re-written, no corruption

## Job ledger audit trail
| Job | Status | Steps | Failed | Rows | Duration |
|-----|--------|-------|--------|------|----------|
| #1 | FAILED | 4 | 4 | 0 | 0.7s (OANDA param bug) |
| #2 | COMPLETED | 4 | 0 | 374 | 1.7s |
| #3 | COMPLETED | 4 | 0 | 157 | 2.1s |
| #4 | COMPLETED | 1 | 0 | 287 | (rerun) |

## Acceptance criteria
- [x] Controlled repair: candle window rebuilt from OANDA
- [x] Real incident repaired: 287 M1 + 59 M5 + 21 M15 + 7 H1 candles, 58 M5 + 19 M15 signals
- [x] Post-repair verification passes
- [x] Idempotent rerun safe (ON DUPLICATE KEY UPDATE)
- [x] Job ledger records all steps, outcomes, fault states
- [x] Failed job (#1) correctly recorded with fault code
- [x] Gap ledger reconciled (RESOLVED)
