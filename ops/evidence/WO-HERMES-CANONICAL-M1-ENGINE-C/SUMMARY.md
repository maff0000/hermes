# WO-HERMES-CANONICAL-M1-ENGINE-C — Evidence Summary

## Date: 2026-03-31
## Epic: EPIC-HERMES-SIGNAL-SOURCE-RESILIENCE-001
## Purpose: First-valid-arrival-wins engine with per-instrument isolation

## What was created

### utils/canonical_engine.py
- **CanonicalM1Engine** — accepts/rejects candidate M1 candles
  - submit_candidate(): per-instrument, per-call connection, no shared state
  - submit_repair(): explicit overwrite with ingest_mode=REPAIR
  - get_canonical(), get_latest(): read canonical truth
- Validates: instrument, minute bucket, OHLC, acceptance window
- Rejects: duplicate, late, invalid — with explicit fault codes
- Logs: all rejections to hermes_candidate_log
- Race condition: IntegrityError caught on concurrent insert

### Per-Instrument Isolation (proven)
- Each submit_candidate() opens its own connection
- No transaction spans instruments
- No lock/mutex groups instruments
- ADVERSARIAL TEST: EUR/USD accepted while XAU/USD rejected — no contamination

## Tests (8/8 PASS)

| # | Test | Result |
|---|------|--------|
| 1 | First valid arrival accepted | PASS |
| 2 | Duplicate rejected (REJECTED_DUPLICATE/LATE) | PASS |
| 3 | Late candidate rejected | PASS |
| 4 | Invalid candidate rejected (bad OHLC, empty instrument) | PASS |
| 5 | Repair overwrites with explicit marking | PASS |
| 6 | Rejection logged to hermes_candidate_log | PASS |
| 7 | **ADVERSARIAL: EUR/USD accepted, XAU/USD rejected, no cross-contamination** | PASS |
| 8 | Concurrent duplicate handled via IntegrityError | PASS |

## Key Proofs
- First arrival wins: row created with source_id and ingest_mode=LIVE_FIRST_ACCEPTED
- Duplicate rejected: fault_code=HERMES_CANONICAL_M1_REJECTED_DUPLICATE
- Repair overwrite: close 3100.80 → 3105.00, ingest_mode=REPAIR, source_id=oanda_rest_repair
- Isolation: EUR/USD row exists with close=1.08520, XAU/USD row absent
