# WO-HERMES-CANONICAL-M1-SCHEMA-A — Evidence Summary

## Date: 2026-03-31
## Epic: EPIC-HERMES-SIGNAL-SOURCE-RESILIENCE-001
## Purpose: Foundation schema for canonical M1 truth with provenance

## What was created

### Tables

| Table | Purpose | Rows |
|-------|---------|------|
| canonical_m1 | Canonical M1 truth. One row per instrument/minute. Provenance explicit. | 0 (empty, parallel-run not started) |
| hermes_source_policy | Per-instrument source config. No inheritance between instruments. | 12 (all instruments seeded) |
| hermes_candidate_log | Audit log of accepted/rejected candidates. For future multi-source debugging. | 0 (empty) |

### Config Keys (hermes_config)

| Key | Value | Purpose |
|-----|-------|---------|
| canonical_m1_default_acceptance_window_sec | 120 | Safety fallback if instrument has no policy row |
| canonical_m1_reject_if_exists | true | V1: first valid arrival wins |
| canonical_m1_log_rejected_candidates | true | Audit rejected candidates |

### Fault Codes (for WO-C implementation)

| Code | Meaning |
|------|---------|
| HERMES_CANONICAL_M1_REJECTED_DUPLICATE | Candidate rejected — canonical row already exists for this instrument/minute |
| HERMES_CANONICAL_M1_REJECTED_LATE | Candidate rejected — arrived after acceptance window closed |
| HERMES_CANONICAL_M1_REJECTED_INVALID | Candidate rejected — missing OHLC, wrong instrument, or invalid minute bucket |
| HERMES_CANONICAL_M1_MISSING_BUCKET | Expected canonical M1 bucket is absent (gap) |
| HERMES_CANONICAL_M1_REPAIR_OVERWRITE | Canonical M1 row overwritten by explicit repair operation |

### Source Policy (V1)

All 12 instruments: oanda_stream primary, no fallback, 120s acceptance window.
Each instrument has its own explicit row — no silent inheritance.

### Schema Design Decisions

1. **New table (canonical_m1) not extending candles_M1** — clean provenance, safe parallel migration, no risk to existing consumers
2. **UNIQUE KEY on (instrument, minute_bucket_utc)** — enforces one canonical truth per bucket at DB level
3. **ingest_mode enum** — distinguishes live first-arrival from repair/backfill/manual. No silent replacement.
4. **arrival_utc** — records when candidate reached the engine, not when the candle closed. Enables latency analysis.
5. **source_timestamp_utc** — preserves original source timestamp for cross-source comparison in future phases.
6. **hermes_candidate_log** — separate from canonical truth. Audit trail only. Keeps canonical_m1 clean.
7. **Per-instrument policy with no inheritance** — XAU_USD policy cannot silently affect EUR_USD.

## Acceptance Criteria

- [x] Schema exists with governance (description + llm_reasoning on all rows)
- [x] Provenance model distinguishes live, repair, backfill, manual
- [x] Per-instrument source policy is explicit (12 rows, no inheritance)
- [x] Fault codes defined for WO-C
- [x] Config keys governed in hermes_config
- [x] candles_M1 untouched — coexistence during transition
- [x] Zero runtime behavior change
