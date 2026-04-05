# EPIC-HERMES-SIGNAL-SOURCE-RESILIENCE-001

## Epic Definition and Implementation Plan

**Author:** Helm
**Date:** 2026-03-31
**Status:** PLANNING COMPLETE — READY FOR REVIEW

---

## A. Executive Summary

### Problem

HERMES currently aggregates all candle timeframes (M1/M5/M15/H1/D1) independently from OANDA ticks, and recovers each timeframe independently from OANDA REST. This creates:

1. **Single-source fragility** — if OANDA fails, all timeframes are lost simultaneously with no fallback
2. **Recovery complexity** — each timeframe must be fetched independently from OANDA, coupling recovery to a single broker
3. **No provenance** — `source` column exists but is always `signal_service`, providing no operational truth about where data actually came from
4. **No path to multi-source** — adding IBKR/MT5 as fallback sources would require duplicating the full multi-timeframe aggregation stack per source

### Agreed Target Architecture

**Canonical collected timeframe = M1 only.**

- Collection agents produce candidate M1 candles from any source
- A canonical M1 engine accepts first valid arrival per instrument/minute bucket
- Higher timeframes (M5/M15/H1/D1) are derived internally from canonical M1
- Signal/indicator generator is a separate governed process consuming canonical M1
- A tiny canary independently monitors signal truth freshness
- Provenance is explicit on every canonical row

### Why This Simplifies

| Current | Target |
|---------|--------|
| 5 timeframes × N sources = 5N collection paths | 1 timeframe × N sources = N collection paths |
| Recovery fetches M1+M5+M15+H1 independently | Recovery fetches M1 only, derives the rest |
| Adding a source means implementing 5-TF aggregation | Adding a source means producing M1 only |
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
                    │   (with source   M1          │
                    │    provenance)               │
                    └───────┬─────────────┘────────┘
                            │
                            ▼
                    ┌─────────────────────────────┐
                    │   CANONICAL M1 ENGINE        │
                    │  (Layer 2)                   │
                    │                              │
                    │  first valid arrival wins    │
                    │  per instrument/minute bucket│
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
          │  SIGNAL /   │ │  M1/SIG  │  │  RECOVERY       │
          │  INDICATOR  │ │  CANARY  │  │  ENGINE          │
          │  GENERATOR  │ │ (Layer 3)│  │  (existing,      │
          │ (Layer 4)   │ │          │  │   updated)       │
          │             │ │  fresh?  │  │                  │
          │  M1 → M5    │ │  yes/no  │  │  M1 BROKER_FETCH│
          │  M1 → M15   │ │          │  │  M5+ DERIVE     │
          │  M1 → H1    │ └──────────┘  └─────────────────┘
          │  M1 → D1    │
          │             │
          │  indicators │
          │  signals    │
          │  levels     │
          └─────────────┘
```

### V1 Ingestion Rule

**FIRST VALID ARRIVAL WINS.**

For each `(instrument, minute_bucket_utc)`:
1. If no canonical row exists → accept candidate if valid
2. If canonical row exists → reject (log provenance of rejected candidate)
3. Validity = correct instrument + correct UTC minute + OHLC present + within acceptance window
4. Repair/backfill explicitly marked with `ingest_mode` ≠ `LIVE_FIRST_ACCEPTED`

No agreement, quorum, or coherence protocol in v1.

---

## C. WO Breakdown

### WO-A: Canonical M1 Architecture + Schema

**Purpose:** Define the truth foundation. Schema, provenance model, ingest modes, config, fault codes.

**Deliverables:**
1. `canonical_m1` table (or extend `candles_M1`) with:
   - `instrument` VARCHAR(20) NOT NULL
   - `minute_bucket_utc` DATETIME NOT NULL — the canonical minute (truncated to :00)
   - `open`, `high`, `low`, `close` DECIMAL(12,5) NOT NULL
   - `volume` INT UNSIGNED — tick count or broker volume
   - `source_id` VARCHAR(32) NOT NULL — which source produced this (e.g. `oanda_stream`, `ibkr_stream`, `oanda_rest_repair`)
   - `ingest_mode` ENUM('LIVE_FIRST_ACCEPTED', 'REPAIR', 'BACKFILL', 'MANUAL') NOT NULL
   - `arrival_utc` DATETIME(3) NOT NULL — when the candidate arrived at the engine
   - `source_timestamp_utc` DATETIME(3) NULL — original timestamp from source
   - `accepted` TINYINT(1) NOT NULL DEFAULT 1
   - `description` TEXT NOT NULL
   - `llm_reasoning` TEXT NOT NULL
   - UNIQUE KEY `(instrument, minute_bucket_utc)` — enforces one canonical truth per bucket
2. `hermes_source_policy` table:
   - `instrument` VARCHAR(20) NOT NULL
   - `primary_source` VARCHAR(32) NOT NULL
   - `fallback_sources_json` JSON NULL
   - `acceptance_window_sec` INT UNSIGNED NOT NULL — how late a candidate can arrive and still be accepted
   - `is_enabled` TINYINT(1) NOT NULL DEFAULT 1
   - `description` TEXT NOT NULL
   - `llm_reasoning` TEXT NOT NULL
3. Config keys in `hermes_config`:
   - `canonical_m1_acceptance_window_sec` (default for instruments without specific policy)
   - `canonical_m1_reject_if_exists` (boolean, default true for v1)
4. Fault codes:
   - `HERMES_CANONICAL_M1_REJECTED_DUPLICATE`
   - `HERMES_CANONICAL_M1_REJECTED_LATE`
   - `HERMES_CANONICAL_M1_REJECTED_INVALID`
   - `HERMES_CANONICAL_M1_MISSING_BUCKET`
   - `HERMES_CANONICAL_M1_REPAIR_OVERWRITE`
5. Migration script

**Acceptance criteria:**
- Schema exists and passes governance checks (description, llm_reasoning on all rows)
- Provenance model supports live, repair, and backfill modes distinctly
- Existing candles_M1 data can be migrated or coexist during transition
- Fault codes defined and documented

**Key design decision:** Whether to create a new `canonical_m1` table or extend existing `candles_M1`. Recommendation: **new table** — cleaner migration, no risk to existing consumers during transition. `candles_M1` becomes a legacy alias or is deprecated after migration.

---

### WO-B: Collection Agent Contract

**Purpose:** Define the interface that any source adapter must implement to submit candidate M1 candles.

**Deliverables:**
1. `CandidateM1` data contract:
   - instrument, minute_bucket_utc, open, high, low, close, volume
   - source_id, source_timestamp_utc
   - arrival_utc (set by engine, not agent)
2. `SourceAdapter` abstract interface:
   - `connect()` → bool
   - `stream_m1_candidates()` → AsyncIterator[CandidateM1]
   - `fetch_m1_range(instrument, start, end)` → List[CandidateM1] (for recovery)
   - `health()` → SourceHealth
3. Source metadata requirements:
   - source_id must be stable and unique
   - source must declare its latency characteristics
   - source must declare which instruments it covers
4. Dedupe/idempotency rules:
   - Engine handles dedupe (same instrument+minute = reject if canonical exists)
   - Agent does NOT need to dedupe — engine is authoritative
5. Source policy registry seed for current OANDA source

**Acceptance criteria:**
- Contract is implementable by OANDA adapter with minimal refactor
- Contract is generic enough for future IBKR/MT5 adapters
- Policy registry governs per-instrument source priority

---

### WO-C: Canonical M1 Engine Implementation

**Purpose:** The core — accept first valid M1, reject duplicates, record provenance.

**Deliverables:**
1. `CanonicalM1Engine` class:
   - `submit_candidate(candidate: CandidateM1) → AcceptResult`
   - Validates: instrument, minute bucket, OHLC present, within acceptance window
   - Checks: canonical row exists? → reject with `REJECTED_DUPLICATE`
   - Inserts with provenance if accepted
   - Returns: accepted/rejected with reason
2. Provenance logging for rejected candidates (separate table or structured log)
3. Integration with existing watchdog — canonical M1 freshness replaces raw tick freshness as health truth
4. Repair mode: `submit_repair(candidate, job_id)` — explicitly overwrites with `ingest_mode=REPAIR`

**Acceptance criteria:**
- First arrival for an instrument/minute bucket is accepted
- Second arrival for same bucket is rejected (not silently overwritten)
- Repair mode can overwrite with explicit marking
- Provenance is queryable for any canonical row
- Watchdog can evaluate canonical M1 freshness

---

### WO-D: Signal/Indicator Generator Split

**Purpose:** Separate computation from collection. Generator consumes canonical M1 only.

**Deliverables:**
1. M1-to-higher-TF derivation module:
   - `derive_m5(instrument, m1_candles[5])` → M5 candle
   - `derive_m15(instrument, m1_candles[15])` → M15 candle
   - `derive_h1(instrument, m1_candles[60])` → H1 candle
   - `derive_d1(instrument, m1_candles[...])` → D1 candle (day boundary aware)
2. Generator process/task:
   - On each new canonical M1 close: check if any higher TF boundary is complete
   - If complete: derive candle, write to candles_M5/M15/H1/D1
   - Then compute signals for M5/M15 as today
3. Fail-loud if canonical M1 is missing or stale when derivation expected
4. Indicators/signals/levels remain unchanged — they already consume from candle tables

**Acceptance criteria:**
- M5 derived from 5 consecutive canonical M1 candles matches OANDA M5 within acceptable tolerance
- M15/H1/D1 derived correctly
- Signal computation produces identical results whether candles came from tick aggregation or M1 derivation
- Generator fails loudly on missing canonical M1

**Validation approach:** Run derived candles alongside current tick-aggregated candles for a soak period. Compare OHLC values. Document acceptable tolerance (mid-price rounding, boundary edge cases).

---

### WO-E: Recovery Migration

**Purpose:** Update the recovery spine to M1-canonical.

**Deliverables:**
1. Recovery library changes:
   - `CANDLE_M1` stays `BROKER_FETCH` (from source adapter, not hardcoded OANDA)
   - `CANDLE_M5` changes from `BROKER_FETCH` → `DERIVE_FROM_CANONICAL_M1`
   - `CANDLE_M15` changes from `BROKER_FETCH` → `DERIVE_FROM_CANONICAL_M1`
   - `CANDLE_H1` changes from `BROKER_FETCH` → `DERIVE_FROM_CANONICAL_M1`
   - Add `CANDLE_D1` as `DERIVE_FROM_CANONICAL_M1` (currently missing from library)
   - Signals stay `WINDOW_PLUS_LOOKBACK` (unchanged)
2. Recovery executor updated to handle `DERIVE_FROM_CANONICAL_M1` strategy
3. Recovery flow: fetch M1 from source → derive higher TFs → recompute signals

**Acceptance criteria:**
- Recovery of a missing window only fetches M1 from broker
- Higher TFs are derived from recovered M1
- D1 is now recoverable (was missing)
- Existing recovery jobs still work during migration
- Gap scanner correctly detects missing canonical M1 buckets

---

### WO-F: Cleanup / De-duplication

**Purpose:** Remove technical debt exposed by the architecture change.

**Deliverables:**
1. Remove `main.py:fetch_oanda_candles()` (legacy M5-only function)
2. Consolidate to single OANDA REST path in collection agent
3. Resolve volume semantics:
   - Define: live M1 volume = tick count, recovery M1 volume = broker volume
   - Document the distinction in provenance (`ingest_mode` already distinguishes)
   - Decide if normalization is needed (recommendation: no — document, don't normalize)
4. Remove CandleAggregator's M5/M15/H1/D1 tick aggregation paths (replaced by derivation)
5. Deprecation/migration path for `candles_M1` → `canonical_m1` if separate table chosen

**Acceptance criteria:**
- No duplicate OANDA fetch functions
- Volume semantics documented and governed
- CandleAggregator only produces M1 (or is replaced by collection agent)
- No dead code paths remain

---

### WO-G: M1/Signal Truth Canary

**Purpose:** Independent freshness monitor outside the main service.

**Deliverables:**
1. Standalone script/cron job:
   - Queries `canonical_m1` for latest timestamp per active instrument
   - Queries `signals` for latest timestamp per signal timeframe
   - Compares against UTC now + market hours
   - Outputs machine-readable JSON: `{instrument: {m1_age_sec: N, signal_age_sec: N, status: OK/STALE}}`
   - Exits non-zero if any instrument is stale during expected market hours
2. No strategy reasoning, no recovery logic, no OANDA calls
3. Can run as cron or systemd timer
4. Distinct from the watchdog (which is inside the service) — this is an external verifier

**Acceptance criteria:**
- Canary detects stale M1 within configured threshold
- Canary does NOT depend on the HERMES service being up (reads DB directly)
- Canary exits non-zero on stale = usable in monitoring/alerting
- Canary ignores market-closed periods correctly

---

### WO-H: Proof / Validation

**Purpose:** Prove the full architecture works under real conditions.

**Deliverables:**
1. Parallel-run soak: derived candles alongside tick-aggregated candles, comparison report
2. Controlled source failure: block OANDA, verify no M1 = stale detected = correct
3. Controlled M1 gap: delete M1 buckets, verify derivation fails loudly, recovery repairs
4. Idempotent recovery rerun
5. Canary detects stale while service thinks GREEN (external vs internal)
6. Full evidence pack

**Acceptance criteria:**
- Derived candles match OANDA native candles within tolerance
- Source failure propagates correctly through the chain
- Recovery works end-to-end from M1 only
- Canary is independent and truthful

---

## D. Acceptance Criteria Summary

| WO | Must Prove |
|----|-----------|
| A | Schema governs, provenance explicit, fault codes defined |
| B | Contract implementable by OANDA adapter, generic for future sources |
| C | First arrival wins, duplicates rejected, provenance queryable, repair distinct |
| D | Derived candles match native, generator fails loud on missing M1 |
| E | Recovery fetches M1 only, derives rest, D1 recoverable |
| F | No dead code, volume documented, single fetch path |
| G | External canary detects stale independently |
| H | Full-stack proof under real conditions |

---

## E. Risks and Open Questions

### Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Derived candles differ from OANDA native | MEDIUM | WO-H parallel-run comparison. Tolerance defined in WO-D. Known: volume semantics differ (tick count vs broker). OHLC should match for mid-price. |
| Migration disrupts live trading | HIGH | Run new canonical path in parallel before cutover. Keep existing path as fallback until soak proves stability. |
| D1 boundary edge cases | LOW | Forex day boundary = 17:00 ET / 22:00 UTC. Must handle DST. Explicit in derivation module. |
| Existing recovery spine interaction | MEDIUM | WO-E must update recovery library atomically. Gap scanner and executor already read from library — they will adapt if library changes. |
| Watchdog health semantics change | MEDIUM | WO-C updates watchdog to evaluate canonical M1 freshness. Must not break during transition — keep tick-based health until M1-based health is proven. |

### Open Questions

1. **New table vs extend existing?** Recommendation: new `canonical_m1` table for clean provenance. But `candles_M1` is consumed by the Helios bridge and scanner — migration must account for those consumers.
2. **When does CandleAggregator stop producing M5+?** Only after WO-D derivation is proven to match. Not before.
3. **Should rejected candidates be persisted?** Recommendation: structured log only in v1. Full table in v2 if multi-source disagreement analysis is needed.
4. **D1 day boundary:** Forex uses 17:00 ET (22:00 UTC, 21:00 UTC during US DST). This must be explicitly governed, not hardcoded.

---

## F. Recommended Implementation Sequence

### Phase 1 — Foundation (safe, no runtime change)

**WO-A** first. Schema and contracts only. No runtime behavior change. This is the safest starting point — pure additive.

### Phase 2 — Engine + Collection (parallel path)

**WO-B** then **WO-C**. Build the canonical M1 engine and refactor OANDA adapter as a collection agent. Run in parallel with existing path — write to canonical_m1 AND candles_M1 simultaneously. Zero disruption.

### Phase 3 — Generator (prove equivalence)

**WO-D**. Build derivation module. Run derived candles alongside tick-aggregated candles. Compare. Only cut over after soak proves equivalence.

### Phase 4 — Recovery + Cleanup (cut over)

**WO-E** then **WO-F**. Update recovery to M1-only. Remove dead paths. This is the cutover — only after Phases 1-3 are proven.

### Phase 5 — Independent verification

**WO-G** then **WO-H**. Canary and full proof. This is the trust-building phase.

### Smallest Safe First WO

**WO-A** — schema and contracts only. No runtime change. No risk to live service. Creates the foundation everything else builds on.

---

## G. What Remains Unchanged

These completed systems continue to work as-is:

| System | Status | Impact |
|--------|--------|--------|
| Watchdog (WO-0001+0001A) | Stays. Health evaluation migrates from tick freshness to canonical M1 freshness in WO-C. | Updated, not replaced |
| Gap scanner (WO-0002) | Stays. Eventually scans canonical_m1 instead of candles_M1. | Table reference change |
| Recovery library (WO-0003) | Stays. Strategies updated in WO-E. | Metadata update |
| Recovery executor (WO-0004) | Stays. New strategy handler for DERIVE_FROM_CANONICAL_M1 added in WO-E. | Extended |
| Runbook (WO-0005) | Updated after WO-H with new commands/semantics. | Updated |
| Signal computation | Unchanged. Already reads from candle tables. Doesn't care how candles got there. | None |
| Indicators | Unchanged. Pure math on candle history. | None |
| Levels | Unchanged. Reads from candle tables. | None |

---

## H. Per-Instrument Source Policy Model (Design Only)

Not implemented in v1, but the governed model for future use:

```
hermes_source_policy
├── instrument (PK)
├── primary_source (e.g. 'oanda_stream')
├── fallback_sources_json (e.g. '["ibkr_stream", "mt5_stream"]')
├── acceptance_window_sec (per-instrument override)
├── is_enabled
├── description
├── llm_reasoning
```

**Example future state:**

| Instrument | Primary | Fallback | Notes |
|------------|---------|----------|-------|
| XAU_USD | mt5_stream | oanda_stream | MT5 primary for gold |
| EUR_USD | oanda_stream | ibkr_stream | OANDA primary for majors |
| USD_JPY | oanda_stream | ibkr_stream | |
| AUD_USD | oanda_stream | ibkr_stream | |

V1: all instruments use `oanda_stream` as primary with no fallback. The policy table exists and is read, but only has one source per instrument.

---

## I. Migration Safety

### Parallel-Run Strategy

The migration MUST NOT break live trading at any point. The strategy:

1. **Phase 1-2:** New canonical_m1 table populated IN PARALLEL with existing candles_M1. Both tables receive data. All existing consumers (Helios bridge, gap scanner, recovery) continue reading candles_M1.
2. **Phase 3:** Derivation module writes to candles_M5/M15/H1/D1 IN PARALLEL with existing tick aggregation. Compare outputs.
3. **Phase 4:** After soak proof, cut over consumers to canonical_m1. Deprecate tick-aggregated higher TFs. Update recovery.
4. **Phase 5:** After full proof, remove legacy paths.

At no point does a single WO break the existing working system. Each WO is additive until the cutover phase, and cutover only happens after evidence proves equivalence.
