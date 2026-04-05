# WO-HERMES-PROOF-H — Proof Measurement Sheet

## Epic: EPIC-HERMES-SIGNAL-SOURCE-RESILIENCE-001
## Purpose: Objective cutover readiness scorecard

This sheet defines every gate that must be met before cutover.
Each gate has an explicit measurement method, threshold, and evidence requirement.
No vibes. No judgment calls. Pass or fail.

---

## Gate 1: M1 OHLC Equivalence

| Metric | Threshold | Measurement Method |
|--------|-----------|-------------------|
| canonical_m1 vs candles_M1 OHLC match | ≥99.9% of minute buckets | SQL comparison: JOIN on (instrument, minute_bucket_utc=timestamp), check ABS(diff) < 0.001 for each of O/H/L/C |
| Sample window | Full soak period (5 trading days minimum) | All instruments, all minutes during market hours |
| Mismatch tolerance | 0.001 (mid-price rounding) | Any mismatch > 0.001 on any OHLC field = counted as mismatch |

**Evidence:** SQL query output showing match count, mismatch count, percentage, and first 10 mismatches if any.

---

## Gate 2: Higher-TF Derivation Equivalence

| Metric | Threshold | Measurement Method |
|--------|-----------|-------------------|
| Derived M5 vs legacy candles_M5 OHLC match | ≥99.5% of buckets | M1DerivationEngine.compare_with_legacy() over soak window |
| Derived M15 vs legacy candles_M15 OHLC match | ≥99.5% of buckets | Same method |
| Derived H1 vs legacy candles_H1 OHLC match | ≥99.5% of buckets | Same method |
| D1 derivation correctness | Manual spot-check (3+ days) | Compare derived D1 OHLC vs OANDA chart for forex day boundaries |
| Sample window | Full soak period | At minimum XAU_USD; ideally 2-3 instruments |

**Evidence:** Comparison report per timeframe per instrument showing match %, mismatch details, volume note.

**Volume comparison:** Explicitly SKIPPED. Volume semantics differ (tick count vs broker volume). Documented in WO-F.

---

## Gate 3: Indicator/Signal Tolerance

| Metric | Threshold | Measurement Method |
|--------|-----------|-------------------|
| RSI-14 difference | < 0.5 | Compare signals computed from derived candles vs legacy signals for same timestamps |
| EMA values difference | < 0.01 (relative to price) | Same comparison |
| ATR-14 difference | < 0.01 (relative to price) | Same comparison |
| ADX/DI difference | < 0.5 | Same comparison |
| Bollinger Band difference | < 0.01 (relative to price) | Same comparison |
| Regime match | ≥99% agreement | Same timestamp, same instrument |
| Sample window | 1+ trading days minimum | At minimum XAU_USD M5 signals |

**Measurement method:** Compute signals from derived candle history using SignalComputer, compare against existing signals table for the same timestamps. Differences are FP rounding from candle derivation path.

**Evidence:** Comparison script output with per-indicator stats.

---

## Gate 4: Soak Period

| Metric | Threshold |
|--------|-----------|
| Consecutive trading days | ≥5 (Monday open → Friday close) |
| Watchdog GREEN during market hours | ≥99% of market-open minutes |
| Unresolved incidents | 0 related to canonical M1 path |
| Unresolved gaps (canonical M1 path) | 0 |
| Soak monitor snapshots captured | Every 5 minutes via cron |

**Evidence:** Soak log showing health state over full period. Summary stats.

---

## Gate 5: Canary

| Metric | Threshold |
|--------|-----------|
| Canary reports GREEN under canonical M1 path | For duration of soak |
| Canary detects stale during deliberate fault drill | Verified |
| Canary not fooled by legacy residual data | Verified |

**Evidence:** Canary output logs during soak + drill output.

---

## Gate 6: Provenance / Freshness

| Metric | Threshold |
|--------|-----------|
| canonical_m1 rows with explicit source_id | 100% |
| canonical_m1 rows with explicit ingest_mode | 100% |
| Unexplained overwrites (LIVE_FIRST_ACCEPTED overwritten without REPAIR marking) | 0 |
| Arrival latency (arrival_utc - minute_bucket_utc) within acceptance window | ≥99.9% |

**Evidence:** SQL query output.

---

## Gate 7: Per-Instrument Isolation

| Metric | Threshold |
|--------|-----------|
| Deliberate single-instrument failure | Tested |
| Other instruments continue canonical M1 acceptance during fault | Verified |
| Faulted instrument detected independently (health RED for that instrument) | Verified |
| Recovery of faulted instrument does not disrupt others | Verified |

**Evidence:** Drill transcript showing fault injection, per-instrument health, and recovery.

---

## Gate 8: Recovery Under New Architecture

| Metric | Threshold |
|--------|-----------|
| recover-window using M1-only BROKER_FETCH + derivation | Tested |
| verify-window passes after recovery | Verified |
| Idempotent rerun safe | Verified |
| Job ledger records correct strategies (BROKER_FETCH for M1, DERIVE_FROM_CANONICAL_M1 for higher TFs) | Verified |

**Evidence:** Job ledger queries + verify output.

---

## Gate 9: Authorization

| Metric | Requirement |
|--------|-------------|
| All Gates 1-8 passed | Yes |
| Evidence pack committed to ops/evidence/ | Yes |
| Matt explicit written approval | Required |

---

## Scorecard Template

| Gate | Status | Date Verified | Evidence Location |
|------|--------|---------------|-------------------|
| 1. M1 OHLC equivalence | PENDING | | |
| 2. Higher-TF equivalence | PENDING | | |
| 3. Indicator/signal tolerance | PENDING | | |
| 4. Soak period (5 days) | PENDING | | |
| 5. Canary green + drill | PENDING | | |
| 6. Provenance clean | PENDING | | |
| 7. Per-instrument isolation | PENDING | | |
| 8. Recovery under new arch | PENDING | | |
| 9. Matt authorization | PENDING | | |

**Cutover may proceed only when ALL gates show PASS.**
