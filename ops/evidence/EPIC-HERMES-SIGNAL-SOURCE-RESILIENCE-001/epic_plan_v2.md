# EPIC-HERMES-SIGNAL-SOURCE-RESILIENCE-001 (v2)

## Epic Definition and Implementation Plan — Revised

**Author:** Helm
**Date:** 2026-03-31
**Revision:** v2 — incorporates Matt's three corrections
**Status:** PLANNING COMPLETE — AWAITING FINAL REVIEW

### Revision Summary

| Correction | What Changed |
|------------|-------------|
| 1. WO-G canary | Replaced net-new canary with alignment/migration validation of existing WO-HERMES-SIGNAL-TRUTH-CANARY-0001 |
| 2. Per-instrument isolation | Added hard isolation criteria to WO-B, WO-C, WO-H. No grouped barriers. One-instrument failure proof required. |
| 3. Cutover criteria | Added explicit dual-truth finish line with equivalence checks, soak period, canary green, authorization gate |

---

## A. Executive Summary

### Problem

HERMES aggregates all candle timeframes independently from OANDA ticks and recovers each timeframe independently from OANDA REST. This creates single-source fragility, recovery complexity, no provenance truth, and no path to multi-source resilience.

### Agreed Target Architecture

**Canonical collected timeframe = M1 only.** Everything else derived internally.

- Collection agents produce candidate M1 candles from any source
- Canonical M1 engine accepts first valid arrival per instrument/minute bucket
- Higher timeframes derived internally from canonical M1
- Signal/indicator generator is a separate governed process consuming canonical M1
- Existing signal-truth canary validates freshness independently
- Provenance is explicit on every canonical row
- Per-instrument isolation is hard — one instrument's failure never blocks another

### Why This Simplifies

| Current | Target |
|---------|--------|
| 5 TFs × N sources = 5N collection paths | 1 TF × N sources = N collection paths |
| Recovery fetches M1+M5+M15+H1 independently | Recovery fetches M1 only, derives the rest |
| Adding a source = implementing 5-TF aggregation | Adding a source = producing M1 only |
| Source failure = all TFs lost | Source failure = failover at M1, rest derived automatically |

---

## B. Target Architecture

```
                    ┌─────────────────────────────┐
                    │     COLLECTION AGENTS        │
                    │  (Layer 1)                   │
                    │                              │
                    │  ┌─────────┐  ┌──────────┐  │
                    │  │  OANDA  │  │  Future:  │  │
                    │  │  Agent  │  │ IBKR/MT5  │  │
                    │  └────┬────┘  └─────┬─────┘  │
                    │       │             │        │
                    │       ▼             ▼        │
                    │   candidate M1   candidate   │
                    │   (per instrument,  M1       │
                    │    independent)              │
                    └───────┬─────────────┘────────┘
                            │
                            ▼
                    ┌─────────────────────────────┐
                    │   CANONICAL M1 ENGINE        │
                    │  (Layer 2)                   │
                    │                              │
                    │  PER-INSTRUMENT acceptance   │
                    │  first valid arrival wins    │
                    │  no cross-instrument deps    │
                    │  provenance recorded         │
                    │  repair/backfill marked      │
                    │                              │
                    │  → canonical_m1 table        │
                    └───────────┬──────────────────┘
                                │
                    ┌───────────┼──────────────────┐
                    │           │                  │
                    ▼           ▼                  ▼
          ┌─────────────┐ ┌──────────┐  ┌─────────────────┐
          │  SIGNAL /   │ │ EXISTING │  │  RECOVERY       │
          │  INDICATOR  │ │ CANARY   │  │  ENGINE          │
          │  GENERATOR  │ │ (WO-0001)│  │  (updated)       │
          │ (Layer 4)   │ │          │  │                  │
          │             │ │  already │  │  M1 BROKER_FETCH│
          │  M1 → M5    │ │  live    │  │  M5+ DERIVE     │
          │  M1 → M15   │ └──────────┘  └─────────────────┘
          │  M1 → H1    │
          │  M1 → D1    │
          │             │
          │  indicators │
          │  signals    │
          │  levels     │
          └─────────────┘
```

### V1 Ingestion Rule

**FIRST VALID ARRIVAL WINS. PER INSTRUMENT. INDEPENDENTLY.**

For each `(instrument, minute_bucket_utc)`:
1. If no canonical row exists → accept candidate if valid
2. If canonical row exists → reject (log provenance of rejected candidate)
3. Validity = correct instrument + correct UTC minute + OHLC present + within acceptance window
4. Repair/backfill explicitly marked with `ingest_mode` ≠ `LIVE_FIRST_ACCEPTED`
5. **Acceptance is per-instrument. No grouped barriers. XAU_USD failing does not block EUR_USD.**

---

## C. WO Breakdown

### WO-A: Canonical M1 Architecture + Schema

**Purpose:** Define the truth foundation. Schema, provenance model, ingest modes, config, fault codes.

**Deliverables:**
1. `canonical_m1` table with provenance columns (`source_id`, `ingest_mode`, `arrival_utc`, `source_timestamp_utc`)
2. `hermes_source_policy` table (per-instrument source config)
3. Config keys in `hermes_config`
4. Fault codes defined
5. Migration script

**Acceptance criteria:**
- Schema exists with governance (description, llm_reasoning)
- Provenance model distinguishes live, repair, and backfill
- Per-instrument source policy is explicit — no instrument inherits silently from another
- Existing candles_M1 data can coexist during transition

---

### WO-B: Collection Agent Contract

**Purpose:** Define the interface any source adapter must implement.

**Deliverables:**
1. `CandidateM1` data contract
2. `SourceAdapter` abstract interface (`connect`, `stream_m1_candidates`, `fetch_m1_range`, `health`)
3. Source metadata requirements
4. Dedupe/idempotency rules (engine handles dedupe, not agent)
5. Source policy registry seed for OANDA

**Acceptance criteria:**
- Contract implementable by OANDA adapter with minimal refactor
- Contract generic enough for future IBKR/MT5 adapters
- **Per-instrument isolation enforced at contract level:**
  - Agent submits candidates per instrument independently
  - No batch/grouped submission that ties instrument fates together
  - Agent health is reported per instrument, not as monolith
  - Contract explicitly forbids cross-instrument success dependency
- Policy registry governs per-instrument source priority

---

### WO-C: Canonical M1 Engine Implementation

**Purpose:** Accept first valid M1, reject duplicates, record provenance. Per-instrument. Independently.

**Deliverables:**
1. `CanonicalM1Engine` class with `submit_candidate()` and `submit_repair()`
2. Provenance logging for rejected candidates
3. Watchdog integration — canonical M1 freshness as health truth
4. Repair mode with explicit marking

**Acceptance criteria:**
- First arrival for an instrument/minute bucket is accepted
- Second arrival for same bucket is rejected (not silently overwritten)
- Repair mode can overwrite with explicit marking
- Provenance is queryable for any canonical row
- **Per-instrument isolation enforced at engine level:**
  - Each `submit_candidate()` call operates on one instrument only
  - No transaction spans multiple instruments
  - No lock, mutex, or queue groups instruments together
  - One instrument's OANDA failure, DB write failure, or validation failure does NOT block or delay acceptance for any other instrument
  - Engine must continue accepting candidates for healthy instruments while one instrument is faulted
- **Proof required:** Deliberate single-instrument fault while others continue normally (tested in WO-H)

---

### WO-D: Signal/Indicator Generator Split

**Purpose:** Separate computation from collection. Generator consumes canonical M1 only.

**Deliverables:**
1. M1-to-higher-TF derivation module (M5, M15, H1, D1)
2. Generator process/task consuming canonical M1
3. Fail-loud on missing/stale canonical M1
4. Indicators/signals/levels remain unchanged

**Acceptance criteria:**
- Derived M5 matches OANDA native M5 within defined tolerance
- M15/H1/D1 derived correctly
- Signal computation produces identical results from derived candles
- Generator fails loudly on missing canonical M1
- D1 day boundary (22:00 UTC / 21:00 UTC during US DST) handled explicitly and governed

---

### WO-E: Recovery Migration

**Purpose:** Update recovery spine to M1-canonical.

**Deliverables:**
1. Recovery library changes: M1 = `BROKER_FETCH`, M5/M15/H1 = `DERIVE_FROM_CANONICAL_M1`, D1 added
2. Recovery executor updated for new strategy
3. Recovery flow: fetch M1 → derive higher TFs → recompute signals

**Acceptance criteria:**
- Recovery fetches M1 only from broker
- Higher TFs derived from recovered M1
- D1 is now recoverable
- Existing recovery jobs still work during migration
- Gap scanner correctly detects missing canonical M1 buckets

---

### WO-F: Cleanup / De-duplication

**Purpose:** Remove technical debt.

**Deliverables:**
1. Remove `main.py:fetch_oanda_candles()` (legacy M5-only)
2. Consolidate to single OANDA fetch path in collection agent
3. Volume semantics documented and governed
4. CandleAggregator M5/M15/H1/D1 tick aggregation removed
5. `candles_M1` → `canonical_m1` migration/deprecation path

**Acceptance criteria:**
- No duplicate OANDA fetch functions
- Volume semantics explicitly governed (live = tick count, recovery = broker volume, documented in provenance)
- CandleAggregator only produces M1 or is replaced by collection agent
- No dead code paths

---

### WO-G: Canary Alignment / Migration Validation

**Purpose:** Verify existing signal-truth canary (WO-HERMES-SIGNAL-TRUTH-CANARY-0001, already live) remains valid during and after canonical M1 migration.

**This is NOT net-new canary creation.** The canary exists and is locally complete.

**Deliverables:**
1. Audit existing canary's signal-truth proxy against new canonical M1 schema
2. Determine if canary's freshness check needs to read `canonical_m1` instead of `candles_M1`
3. If generator split changes signal output timing or table paths, update canary accordingly
4. Prove canary behavior under new schema/path:
   - Canary green when canonical M1 is flowing and signals are fresh
   - Canary stale when canonical M1 stops (not fooled by legacy table still having old data)
   - Canary ignores market-closed periods correctly under new path

**Acceptance criteria:**
- Existing canary validated against new canonical M1 architecture
- Signal-truth proxy updated only if required by generator split
- Canary proven correct under new schema during WO-H soak
- No false green from legacy table residual data

---

### WO-H: Proof / Validation

**Purpose:** Prove the full architecture under real conditions, including per-instrument isolation.

**Deliverables:**
1. Parallel-run soak: derived candles alongside tick-aggregated candles, comparison report
2. Controlled source failure for single instrument while others continue
3. Controlled M1 gap: delete M1 buckets, verify derivation fails loudly, recovery repairs
4. Idempotent recovery rerun
5. Canary validation under new schema
6. Cutover equivalence report
7. Full evidence pack

**Acceptance criteria:**
- Derived candles match OANDA native within tolerance
- Source failure propagates correctly through the chain
- Recovery works end-to-end from M1 only
- Canary is valid under new path
- **Per-instrument isolation proven under adversarial conditions:**
  - Deliberately fail one instrument's source (block OANDA for XAU_USD only, or inject invalid M1 for one instrument)
  - Prove all other instruments continue receiving canonical M1, deriving higher TFs, and producing signals normally
  - Prove faulted instrument is detected independently (health RED for that instrument, GREEN for others)
  - Prove recovery of faulted instrument does not disrupt others
- Cutover criteria met (see section below)

---

## D. Cutover Criteria — Dual-Truth Finish Line

Parallel-run (legacy tick-aggregated path alongside canonical M1 path) continues until ALL of the following are met:

### Equivalence Checks

| Check | Requirement |
|-------|-------------|
| M1 OHLC match | Canonical M1 and legacy candles_M1 agree on OHLC within rounding tolerance for ≥99.9% of minute buckets over soak period |
| M5 derivation match | Derived M5 from canonical M1 matches legacy tick-aggregated M5 OHLC within tolerance for ≥99.5% of M5 buckets |
| M15 derivation match | Same standard as M5 |
| H1 derivation match | Same standard as M5 |
| Signal equivalence | Signals computed from derived candles produce identical indicator values (RSI, EMA, ATR) within floating-point tolerance |

### Soak Period

| Condition | Requirement |
|-----------|-------------|
| Minimum consecutive trading days | **5 full trading days** (Monday open → Friday close) |
| No unresolved incidents | Zero open incidents in `hermes_incidents` related to canonical M1 path |
| No unresolved gaps | Zero unresolved gaps in `hermes_data_gaps` for canonical M1 path during soak |
| Watchdog green | Canonical M1 health evaluated as GREEN for ≥99% of market-open minutes during soak |

### Canary

| Condition | Requirement |
|-----------|-------------|
| Canary green | Existing signal-truth canary reports GREEN under canonical M1 path for duration of soak |
| Canary tested | Canary correctly detects stale canonical M1 during deliberate fault drill |

### Provenance / Freshness

| Condition | Requirement |
|-----------|-------------|
| No provenance gaps | Every canonical M1 row has explicit `source_id` and `ingest_mode` |
| No unexplained overwrites | Zero canonical M1 rows where `ingest_mode=LIVE_FIRST_ACCEPTED` was overwritten without `REPAIR` or `BACKFILL` marking |
| Freshness within threshold | Canonical M1 arrival latency (`arrival_utc - minute_bucket_utc`) is within configured acceptance window for ≥99.9% of buckets |

### Authorization

| Gate | Requirement |
|------|-------------|
| Matt approval | Explicit written approval from Matt to proceed with cutover |
| Evidence pack | Complete cutover evidence pack committed to `ops/evidence/` |

**Until ALL conditions are met, dual-truth operation continues. Legacy path is not removed.**

---

## E. Risks and Open Questions

### Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Derived candles differ from OANDA native | MEDIUM | WO-H parallel-run comparison. Tolerance defined in WO-D. Known: volume differs (tick count vs broker). OHLC should match for mid-price. |
| Migration disrupts live trading | HIGH | Parallel-run with explicit cutover criteria. Legacy path stays until evidence proves equivalence. |
| D1 boundary edge cases (DST) | LOW | 22:00 UTC / 21:00 UTC during US DST. Explicitly governed in derivation module, not hardcoded. |
| Existing recovery spine interaction | MEDIUM | WO-E updates recovery library atomically. Gap scanner/executor read from library — they adapt. |
| Per-instrument isolation regresses | MEDIUM | WO-H includes deliberate single-instrument failure proof. Acceptance criteria enforce no grouped barriers. |
| Watchdog health transition | MEDIUM | Keep tick-based health until canonical M1 health is proven in parallel. Only switch after WO-C soak. |

### Open Questions

1. **New table vs extend existing?** Recommendation: new `canonical_m1` table. But `candles_M1` is consumed by Helios bridge/scanner — migration must account for those consumers.
2. **When does CandleAggregator stop producing M5+?** Only after WO-D derivation proven to match AND cutover criteria met.
3. **Rejected candidate persistence:** Structured log in v1. Table in v2 if multi-source analysis needed.
4. **D1 day boundary:** Must be governed config, not hardcoded. Forex = 22:00 UTC (21:00 during US DST).

---

## F. Recommended Implementation Sequence

### Phase 1 — Foundation (safe, no runtime change)
**WO-A** — schema and contracts only

### Phase 2 — Engine + Collection (parallel path)
**WO-B** then **WO-C** — build canonical M1 engine, refactor OANDA adapter. Run in parallel. Zero disruption.

### Phase 3 — Generator (prove equivalence)
**WO-D** — derivation module. Run alongside tick aggregation. Compare.

### Phase 4 — Recovery + Cleanup (cutover — only after criteria met)
**WO-E** then **WO-F** — update recovery, remove dead paths. Only after Phase 3 soak.

### Phase 5 — Validation
**WO-G** then **WO-H** — canary alignment, full proof, cutover evidence.

### Smallest Safe First WO
**WO-A** — schema only, zero runtime change, zero risk.

---

## G. What Remains Unchanged

| System | Impact |
|--------|--------|
| Watchdog (WO-0001+0001A) | Updated in WO-C (M1 freshness), not replaced |
| Gap scanner (WO-0002) | Table reference change to canonical_m1 |
| Recovery library (WO-0003) | Strategies updated in WO-E |
| Recovery executor (WO-0004) | Extended with DERIVE_FROM_CANONICAL_M1 |
| Runbook (WO-0005) | Updated after WO-H |
| Signal-truth canary (WO-0001) | Validated in WO-G, updated only if needed |
| Signal computation | Unchanged — reads candle tables |
| Indicators | Unchanged — pure internal math |
| Levels | Unchanged — reads candle tables |

---

## H. Per-Instrument Source Policy Model (Design Only)

```
hermes_source_policy
├── instrument (PK)
├── primary_source
├── fallback_sources_json
├── acceptance_window_sec
├── is_enabled
├── description
├── llm_reasoning
```

V1: all instruments use `oanda_stream` as primary with no fallback. Policy table exists and is read. Future: per-instrument primary/fallback as sources are added.
