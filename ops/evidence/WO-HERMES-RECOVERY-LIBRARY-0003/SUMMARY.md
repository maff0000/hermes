# WO-HERMES-RECOVERY-LIBRARY-0003 — Evidence Summary

## Date: 2026-03-31
## Author: Helm (Claude Code)

## What was built

1. **hermes_recovery_library table** — governed artifact registry:
   - 6 artifacts (CANDLE_M1/M5/M15/H1, SIGNAL_M5/M15)
   - rebuild_strategy, validation_strategy, lookback bars
   - description + llm_reasoning on every row
   - rebuild_order for deterministic execution sequencing
2. **hermes_recovery_dependencies table** — normalized dependency graph:
   - FK-enforced referential integrity
   - SIGNAL_M5 → CANDLE_M5, SIGNAL_M15 → CANDLE_M15
3. **utils/recovery_planner.py** — metadata-driven planner:
   - RecoveryLibrary: reads registry and dependency graph
   - LibraryValidator: checks metadata, references, cycles, order consistency
   - RecoveryPlanner: produces deterministic rebuild plans from metadata alone
   - CLI: validate, list, topo-sort, plan commands

## Artifact Registry

| Code | Type | Strategy | Order | Lookback | Depends On |
|------|------|----------|-------|----------|------------|
| CANDLE_M1 | CANDLE | BROKER_FETCH | 10 | 0 | (none) |
| CANDLE_M5 | CANDLE | BROKER_FETCH | 20 | 0 | (none) |
| CANDLE_M15 | CANDLE | BROKER_FETCH | 30 | 0 | (none) |
| CANDLE_H1 | CANDLE | BROKER_FETCH | 40 | 0 | (none) |
| SIGNAL_M5 | SIGNAL | WINDOW_PLUS_LOOKBACK | 50 | 250 | CANDLE_M5 |
| SIGNAL_M15 | SIGNAL | WINDOW_PLUS_LOOKBACK | 60 | 250 | CANDLE_M15 |

## Tests (7/7 PASS)

| # | Test | Result |
|---|------|--------|
| 1 | All enabled artifacts governed | PASS |
| 2 | Graph acyclic and sortable | PASS |
| 3 | Rebuild order consistency | PASS |
| 4 | Dependency references valid | PASS |
| 5 | Full validation passes | PASS |
| 6 | Real outage plan correct (6 steps, candles→signals) | PASS |
| 7 | Partial plan resolves dependencies | PASS |

## Real outage plan (2026-03-31 02:55 → 07:41 UTC)

1. CANDLE_M1 → candles_M1 (BROKER_FETCH)
2. CANDLE_M5 → candles_M5 (BROKER_FETCH)
3. CANDLE_M15 → candles_M15 (BROKER_FETCH)
4. CANDLE_H1 → candles_H1 (BROKER_FETCH)
5. SIGNAL_M5 → signals (WINDOW_PLUS_LOOKBACK, lookback from 2026-03-30 06:05)
6. SIGNAL_M15 → signals (WINDOW_PLUS_LOOKBACK, lookback from 2026-03-28 12:25)
