# WO-HERMES-CLEANUP-F — Evidence Summary

## Date: 2026-03-31
## Epic: EPIC-HERMES-SIGNAL-SOURCE-RESILIENCE-001
## Purpose: Cleanup duplicate paths, govern volume semantics

## What was done

### 1. Legacy Path Deprecation

Two legacy functions in main.py marked DEPRECATED with explicit notices:

| Function | Location | Why Deprecated | When to Remove |
|----------|----------|----------------|----------------|
| fetch_oanda_candles() | main.py:433 | Hardcoded M5 only. Canonical path is recovery_executor.py (M1 BROKER_FETCH + derive) | After cutover criteria met |
| backfill_gap() | main.py:468 | Legacy M5-only startup backfill. Canonical path is recovery executor | After cutover criteria met |

**NOT removed yet** — both are still used for startup/reconnect backfill in the live service. Removal requires canonical M1 engine wired into live path (post-cutover). Deprecation notices prevent new callers.

### 2. Volume Semantics Governed

Volume semantic contract made explicit in hermes_config (migration 008):

| Ingest Mode | Volume Meaning | Source |
|-------------|---------------|--------|
| LIVE_FIRST_ACCEPTED | Tick count (number of ticks in the M1 minute) | CandleAggregator |
| REPAIR | Broker-reported volume (OANDA REST) | fetch_oanda_candles |
| BACKFILL | Broker-reported volume (OANDA REST) | fetch_oanda_candles |
| MANUAL | Operator-defined | Manual |

**Higher-TF derived volume** = SUM of constituent M1 volumes. Semantic follows the M1 ingest_mode.

**Decision: provenance-tagged, NOT normalized.**

Rationale:
- No trading logic uses absolute volume
- volume_ratio in signals uses relative 20-bar average (internally consistent)
- Normalizing would require arbitrary canonical definition
- ingest_mode already makes the semantic queryable

Alternatives considered and rejected: normalize_to_tick_count, normalize_to_broker_volume, drop_volume_entirely.

### 3. What Was NOT Removed (by design)

| Item | Why Preserved |
|------|---------------|
| main.py:fetch_oanda_candles() | Still used for startup backfill. Removal = live gap. |
| main.py:backfill_gap() | Still used for reconnect backfill. Same reason. |
| CandleAggregator M5/M15/H1/D1 tick paths | Still the live production path. Removal = cutover. |

**These are preserved until cutover criteria are met (WO-H).** Premature removal would break the live service.

## Acceptance Criteria
- [x] Duplicate OANDA fetch paths identified and deprecated (not removed — safety)
- [x] Volume semantics explicitly governed in hermes_config
- [x] Volume semantic contract: provenance-tagged, not normalized
- [x] Legacy paths have deprecation notices preventing new callers
- [x] No cutover, no premature deletion of safety paths
- [x] Live runtime unchanged
