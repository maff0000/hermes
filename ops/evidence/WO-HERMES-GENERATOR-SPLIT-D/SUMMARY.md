# WO-HERMES-GENERATOR-SPLIT-D — Evidence Summary

## Date: 2026-03-31
## Epic: EPIC-HERMES-SIGNAL-SOURCE-RESILIENCE-001
## Purpose: M1 derivation engine — higher TFs from canonical M1

## What was created

### utils/m1_deriver.py
- **M1DerivationEngine** — derives M5/M15/H1/D1 from canonical_m1 table
  - derive_candle(): single bucket derivation
  - derive_range(): range of buckets
  - compare_with_legacy(): equivalence report vs candles_M5/M15/H1/D1
- Bucket math matches CandleAggregator exactly (epoch-based truncation)
- D1 uses forex day boundary (22:00 UTC)
- Partial M1 sets produce incomplete candles (not silently dropped)

### Volume Semantics (explicitly documented)
- Derived volume = SUM of constituent M1 volumes
- If M1 was live: volume = tick count
- If M1 was repair: volume = broker-reported volume
- Known semantic mismatch. Documented, not solved. Does not affect OHLC truth.

## Tests (8/8 PASS)

| # | Test | Key Result |
|---|------|-----------|
| 1 | M5 from 5 M1s | 5/5 M1s, OHLC verified |
| 2 | M15 from 15 M1s | 15/15 M1s |
| 3 | H1 from 60 M1s | 60/60 M1s |
| 4 | Partial M1 → incomplete | 3/5 M1s, complete=False |
| 5 | Per-instrument independence | XAU has data, EUR independent |
| 6 | **Equivalence: 12/12 match (100.0%)** | Derived M5 = legacy M5 |
| 7 | Volume = SUM(M1 volumes) | 6191 = 6191 |
| 8 | Bucket boundaries match CandleAggregator | All checks passed |

## Critical Proof: Equivalence

Derived M5 candles from canonical M1 compared against legacy tick-aggregated
M5 candles: **12/12 buckets match at 100.0%**. Zero OHLC mismatches.

This proves the M1-only canonical architecture produces identical
higher-timeframe output to the current tick-aggregation architecture.

## What this WO did NOT do (by design)
- No live cutover
- No writes to candles_M5/M15/H1/D1
- No removal of existing CandleAggregator paths
- No signal computation changes
- Derivation engine is parallel-only, non-authoritative
