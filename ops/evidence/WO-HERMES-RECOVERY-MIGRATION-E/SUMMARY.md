# WO-HERMES-RECOVERY-MIGRATION-E — Evidence Summary

## Date: 2026-03-31
## Epic: EPIC-HERMES-SIGNAL-SOURCE-RESILIENCE-001
## Purpose: Recovery library migration to M1-canonical

## What changed

### Recovery Library (migration 007)
| Artifact | Before | After | Depends On |
|----------|--------|-------|------------|
| CANDLE_M1 | BROKER_FETCH | BROKER_FETCH (unchanged) | (none) |
| CANDLE_M5 | BROKER_FETCH | DERIVE_FROM_CANONICAL_M1 | CANDLE_M1 |
| CANDLE_M15 | BROKER_FETCH | DERIVE_FROM_CANONICAL_M1 | CANDLE_M1 |
| CANDLE_H1 | BROKER_FETCH | DERIVE_FROM_CANONICAL_M1 | CANDLE_M1 |
| **CANDLE_D1** | **(missing)** | **DERIVE_FROM_CANONICAL_M1 (NEW)** | **CANDLE_M1** |
| SIGNAL_M5 | WINDOW_PLUS_LOOKBACK | WINDOW_PLUS_LOOKBACK (unchanged) | CANDLE_M5 |
| SIGNAL_M15 | WINDOW_PLUS_LOOKBACK | WINDOW_PLUS_LOOKBACK (unchanged) | CANDLE_M15 |

### Recovery Executor
- Added DERIVE_FROM_CANONICAL_M1 strategy handler
- Uses M1DerivationEngine from WO-D to derive higher TFs from canonical_m1

## Controlled Recovery Test (Job #6)

Setup: 60 canonical M1 rows seeded, M5/M15/H1 candles deleted for 03:00-04:00 window.

| Step | Artifact | Strategy | Rows | Result |
|------|----------|----------|------|--------|
| 10 | CANDLE_M1 | BROKER_FETCH | 61 | COMPLETED |
| 20 | CANDLE_M5 | DERIVE_FROM_CANONICAL_M1 | 12 | COMPLETED |
| 30 | CANDLE_M15 | DERIVE_FROM_CANONICAL_M1 | 4 | COMPLETED |
| 40 | CANDLE_H1 | DERIVE_FROM_CANONICAL_M1 | 1 | COMPLETED |

Post-repair validation: **all gaps closed**

## Key Achievements
- Only M1 is now fetched from broker during recovery
- All higher TFs derived from canonical M1
- D1 is now recoverable (was missing)
- Recovery flow: BROKER_FETCH M1 → DERIVE M5/M15/H1/D1 → RECOMPUTE signals
- Dependency chain fully governs rebuild order
- Existing job ledger, gap scanner, verification all work with new strategy

## What was NOT changed
- Live runtime path (tick aggregation) — untouched
- candles_M1 legacy table — still current live truth
- Signal computation — unchanged
- No cutover implied
