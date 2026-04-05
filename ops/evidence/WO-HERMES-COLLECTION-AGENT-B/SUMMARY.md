# WO-HERMES-COLLECTION-AGENT-B — Evidence Summary

## Date: 2026-03-31
## Epic: EPIC-HERMES-SIGNAL-SOURCE-RESILIENCE-001
## Purpose: Collection agent contract — source adapter interface

## What was created

### utils/collection_contract.py
- **CandidateM1** data contract — one candidate per instrument per minute, OHLCV + provenance
- **AcceptResult** — engine response with status + fault code
- **CollectionAgent** abstract interface:
  - connect(), disconnect()
  - stream_candidates() → AsyncIterator[CandidateM1] (per-instrument, no batch)
  - fetch_m1_range(instrument, start, end) → List[CandidateM1] (per-instrument)
  - health() → Dict[str, InstrumentSourceHealth] (per-instrument, NOT monolith)
  - source_id, supported_instruments properties
- **InstrumentSourceHealth** — per-instrument health, not monolith
- **SourcePolicyReader** — reads hermes_source_policy, no inheritance

### Per-Instrument Isolation (enforced at contract level)
- CandidateM1 is single-instrument (no batch structure exists)
- stream_candidates() yields one candidate at a time
- health() returns Dict keyed by instrument, not single status
- fetch_m1_range() takes one instrument per call
- No transaction, lock, or queue groups instruments
- Contract docstrings explicitly forbid cross-instrument dependency

## Tests (6/6 PASS)

| # | Test | Result |
|---|------|--------|
| 1 | CandidateM1 validity (6 checks) | PASS |
| 2 | AcceptResult fault codes | PASS |
| 3 | Per-instrument health | PASS |
| 4 | Source policy reader (no inheritance) | PASS |
| 5 | Contract isolation (no cross-instrument) | PASS |
| 6 | Serialization | PASS |

## Acceptance Criteria
- [x] Contract implementable by OANDA adapter with minimal refactor
- [x] Contract generic for future IBKR/MT5 adapters
- [x] Per-instrument isolation enforced — no grouped barriers
- [x] Agent health per-instrument, not monolith
- [x] No cross-instrument success dependency
- [x] Dedupe handled by engine, not agent
- [x] Policy registry governs per-instrument source priority (12 rows, no inheritance)
