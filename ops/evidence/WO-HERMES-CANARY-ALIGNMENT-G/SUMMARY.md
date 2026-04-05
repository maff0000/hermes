# WO-HERMES-CANARY-ALIGNMENT-G — Evidence Summary

## Date: 2026-03-31
## Epic: EPIC-HERMES-SIGNAL-SOURCE-RESILIENCE-001
## Purpose: Verify existing signal-truth canary remains valid during canonical M1 migration

## Existing Canary Assessment

### Location
/srv-dev/tradingProteus/helios/canary/hermes_signal_truth_canary.py
(WO-HERMES-SIGNAL-TRUTH-CANARY-0001, already live)

### What it checks
1. **M1 freshness**: MAX(timestamp) from candles_M1 vs UTC_TIMESTAMP()
2. **Signal truth freshness**: MAX(timestamp) from signals WHERE timeframe='M5' vs UTC_TIMESTAMP()

### Current behavior (verified 2026-03-31)
Canary correctly reports:
- GREEN when HERMES is flowing and data is fresh
- RED with HERMES_CANARY_M1_STALE when M1 candles are stale
- RED with HERMES_CANARY_SIGNAL_TRUTH_STALE when M5 signals are stale
- Uses UTC_TIMESTAMP() in DB queries (immune to BST/UTC mismatch)
- Exit code 1 on RED, 0 on GREEN

### Live proof during this session
At 21:01 UTC, HERMES experienced a brief stale event:
- Watchdog: detected at 124s, opened incident #4, stream → STALE, /health → 503
- Canary: reported RED, HERMES_CANARY_M1_STALE, M1 age 215s
- Both systems agreed: data stale. Truth aligned.

## Migration Impact Analysis

### During parallel-run (current phase)

| Canary Check | Source Table | Still Valid? | Why |
|-------------|-------------|-------------|-----|
| M1 freshness | candles_M1 | **YES** | candles_M1 still populated by live tick aggregation |
| Signal truth | signals (M5) | **YES** | Signals computed from candle tables — source doesn't matter |

**Canary requires NO changes during parallel-run phase.**

### After cutover (when candles_M1 stops being populated)

| Canary Check | Current Source | Required Change |
|-------------|---------------|-----------------|
| M1 freshness | candles_M1 | **Must switch to canonical_m1** |
| Signal truth | signals (M5) | **No change** — signals table unchanged |

### Required cutover change

One line in check_m1_freshness():


Or: add CANARY_M1_TABLE config key (default 'candles_M1', change to 'canonical_m1' at cutover).

### What would NOT change at cutover
- Signal truth check — reads signals table, unaffected
- UTC semantics — already correct
- Exit codes — unchanged
- Config structure — same .env pattern
- Reason codes — same fault codes

## Canary-vs-Watchdog Agreement

Both systems detected the same stale event independently:

| System | Detection Time | Fault | Action |
|--------|---------------|-------|--------|
| Watchdog | 21:01:09 UTC | HERMES_STREAM_STALE_TICK (124s) | Incident #4, stream→STALE, /health→503 |
| Canary | 21:01:35 UTC | HERMES_CANARY_M1_STALE (215s) | Exit code 1, RED output |

The canary is slower (checks on 60s interval, reports candle age not tick age) but independently confirms truth. This is the correct relationship: watchdog = fast internal alarm, canary = independent external verifier.

## Per-Instrument Isolation

Current canary checks one instrument only (CANARY_INSTRUMENT=XAU_USD from config). This is per-instrument by design — no cross-instrument dependency. To monitor additional instruments, deploy additional canary instances with different config.

## Acceptance Criteria

- [x] Existing canary validated against canonical M1 architecture
- [x] Canary remains valid during parallel-run (no changes needed)
- [x] Cutover change identified (one table reference, one config key)
- [x] Signal-truth proxy (M5 signals) unaffected by architecture change
- [x] Live proof: canary and watchdog independently agreed on stale event
- [x] UTC semantics correct (uses UTC_TIMESTAMP() in queries)
- [x] Per-instrument isolation maintained (one instrument per canary instance)
- [x] No false green from legacy table residual — canary reads live table, not stale cache
