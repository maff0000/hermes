# HMT-0 — Closure report

**Initiative:** `HMT-0 — Market Truth v2 Architecture Closure`
**Status:** DOCUMENTATION / ARCHITECTURE ONLY. Base commit
`3f90e640c9c9c1f4a22ba4ac586a1d478f35a997`. Branch `docs/hmt0-market-truth-v2-architecture`.

## What this closure pack is

This is a set of 16 new Markdown documents (this one plus the 15 listed below), all under
`docs/architecture/hmt0-market-truth-v2/`, produced as a documentation/architecture closure exercise for
the HMT-0 ("Market Truth v2") programme. No `.py` file, migration, schema, config, or CI workflow was
touched to produce this pack. No HMT-1 implementation, market-data integration, or product/runtime
behaviour change was performed. DARWIN remains completely untouched and out of scope — this is stated
explicitly here as a non-goal, not merely an omission: nothing in this pack authorises, implies, or
creates a pathway toward any DARWIN/ATHENA capability (see `hmt1-provisional-scope.md` §2 for the same
statement in HMT-1's own scope context).

## The pack index — what each document establishes

1. **[`current-state-reconciliation.md`](current-state-reconciliation.md)** — grounds the whole pack in
   the real, current HERMES schema/services (candles tables, migration 027's canonical H4/D1 + `darwin_ro`,
   the tick `seq` semantic, OANDA integration, Redis boundary, `market-map-dev.service`), and states
   explicitly which concepts in this pack (GC identity/roll, Databento/COMEX GC, P0 microstructure facts,
   the canonical event model, the three-tier storage model) are genuinely new versus already existing.
2. **[`source-taxonomy.md`](source-taxonomy.md)** — the three never-conflated sources: OANDA `XAU_USD`
   (existing), Vantage `XAUUSD` (`BROKER_OTC / EXECUTABLE_QUOTE_OBSERVATION`, new taxonomy slot, not yet
   integrated), COMEX GC actual futures (`CENTRALISED_EXCHANGE_FUTURES / GENUINE_ORDER_FLOW`, new, the
   real target of this programme). States IBKR is excluded as a HERMES market-data source for this
   programme. Flags a naming caution: the literal token "Vantage" is an existing forbidden-scan entry in
   `main.py`'s test suite, unrelated to but worth noting for HMT-1.
3. **[`p0-microstructure-requirements-matrix.md`](p0-microstructure-requirements-matrix.md)** — for every
   P0 fact class (volume-at-price, POC, VAH/VAL, LVN/HVN, aggressor volume, delta, CVD, trade-size
   distributions, aggression clusters, flow efficiency, absorption, failed aggression,
   balance/imbalance, acceptance/rejection, failed auctions, squeeze/trapped-flow proxies), the minimum
   feed level (Trades/TBBO/MBP-1; MBO never introduced) that computes it correctly, with honest
   `PROXY`-vs-`EXACT` labelling discipline where a weaker level is used.
4. **[`canonical-market-events.md`](canonical-market-events.md)** — `MarketQuoteEvent`/
   `MarketTradeEvent`/`TopOfBookEvent`. Rules that TBBO alone cannot satisfy genuine `TopOfBookEvent`
   semantics (trade-anchored sampling only); MBP-1 can. Defers the P0 native-corpus level choice to
   `PENDING_EMPIRICAL_TBBO_VS_MBP1_MEASUREMENT`.
5. **[`time-order-sequence-model.md`](time-order-sequence-model.md)** — six permanently-separate time/
   sequence fields; explicitly forbids reusing HERMES's existing stored-row `ticks.seq` (ratified by
   `docs/hermes_tick_seq_semantic.md`) as an exchange/provider sequence number; forbids fabricated
   cross-source global ordering; ties the three historical provenance eras into per-record quality
   propagation.
6. **[`gc-futures-identity-and-roll.md`](gc-futures-identity-and-roll.md)** — every GC event/fact
   permanently retains the actual traded-contract identity (e.g. `GCZ26`, never a bare `GC`); continuous
   futures are derived-only, versioned, retain contributing contract + roll reason/policy per bar, and are
   never produced by destructively rewriting actual-contract history.
7. **[`derived-fact-taxonomy-and-ownership.md`](derived-fact-taxonomy-and-ownership.md)** — the
   8-field minimum contract every derived fact must carry (schema/version, algorithm version, parameter
   identity/hash, source event/corpus identity, session/calendar identity, roll policy, completeness/gap
   state, evidence lineage); the hard "no unversioned semantic fact" rule; `PROXY`/`EXACT` labelling.
8. **[`data-lifecycle-and-storage.md`](data-lifecycle-and-storage.md)** — OPERATIONAL TRUTH / MARKET
   RESEARCH STORE / EVIDENCE VAULT, kept distinct; explicit rule against deepening dependency on
   deprecated/shared Proteus architecture (grounded in real existing `tradingProteus` references already
   in this repository).
9. **[`deterministic-replay-and-evidence.md`](deterministic-replay-and-evidence.md)** — the
   reproducibility guarantee tied to derived-fact identity fields, and the parameter-transmission
   integrity requirement (parameters captured at point of computation, not reconstructed from possibly-
   drifted configuration).
10. **[`provider-abstraction.md`](provider-abstraction.md)** — the boundary between provider wire
    format and canonical HERMES representation, generalising the existing `adapters/base.py` (OANDA)
    precedent to a GC provider (e.g. Databento) that must remain swappable.
11. **[`gc-data-volume-and-cost-study.md`](gc-data-volume-and-cost-study.md)** — a measurement template,
    every cell `PENDING_EVIDENCE`, structured so real Databento numbers can be dropped in without
    restructuring.
12. **[`licensing-and-security.md`](licensing-and-security.md)** — the binding `C — HYBRID` build-v-rent
    ruling (HERMES permanently owns derived facts/definitions/calendar-roll-history/schema-identity/
    evidence vault; the native corpus itself only with BOTH empirical cost support AND written licensing);
    the literal `PERMANENT_NATIVE_CORPUS_RETENTION = PENDING_WRITTEN_PROVIDER/LICENSOR_CONFIRMATION`
    marker; explicit narrowing away from MBO/deeper books/all-CME/unrelated sources.
13. **[`legacy-migration-roadmap.md`](legacy-migration-roadmap.md)** — governed, unstarted retirement
    roadmaps for (a) legacy `candles_D1` (00:00Z-anchored, `LEGACY / NON_AUTHORITATIVE / RETIRE_PENDING`,
    canonical authority is PR #166's 22:00Z `canonical_candles_d1`) and (b) `market-map-dev.service`
    (`LEGACY / RETIRE_PENDING_CONSUMER_CUTOVER`, in-process HERMES sessions/levels as intended future
    authority). No shutdown of either during HMT-0.
14. **[`darwin-ro-provenance-chronology.md`](darwin-ro-provenance-chronology.md)** — the sanitised,
    four-snapshot `darwin_ro` lifecycle (PR #166 unresolved-at-close → provisioned same day → rotated
    2026-09-17 → re-verified 2026-09-20, exactly 7 SELECT-only grants, no mutation authority), including
    the self-caught `SHOW GRANTS` password-hash exposure lesson, narrated without reproducing any
    credential material.
15. **[`hmt1-provisional-scope.md`](hmt1-provisional-scope.md)** — `HMT-1 — CANONICAL MARKET EVENT &
    REPLAY FOUNDATION`, stated as **APPROVED IN PRINCIPLE / NOT AUTHORISED**; scope description only; no
    implementation detail; explicit DARWIN/ATHENA non-goal statement.
16. **This document** — the pack index and closure statement.

## Directory structure choice

All 15 content documents (plus this index) live under a new subdirectory,
`docs/architecture/hmt0-market-truth-v2/`, rather than as 16 flat files directly under
`docs/architecture/`. Rationale (recorded here per this pack's own reporting requirement): the existing
`docs/architecture/` directory is small and flat (5 files, no subdirectories) — but `docs/design/` already
establishes a real, repeated precedent in this same repository for grouping a multi-document initiative
under its own subdirectory (`fw08/`, `market_hours_health/`, `container_mvp/`, `recovery_planner_wiring/`,
etc.). A 16-document pack is materially larger than anything currently flat in `docs/architecture/`, and
flattening it there would have made that directory harder to navigate and would have obscured which
documents belong to one coherent initiative versus which are independent, standalone architecture notes
(as the existing 5 are). This pack therefore follows `docs/design/`'s subdirectory-per-initiative
precedent, while keeping `docs/architecture/`'s own file-naming convention (lowercase-hyphenated, not the
SCREAMING_SNAKE_CASE `docs/design/` itself sometimes uses for individual WP/F2 documents) for every file
inside the new subdirectory — because these are architecture documents, governed by
`docs/architecture/`'s own conventions, merely grouped under a subdirectory for initiative cohesion.

## Collected `PENDING_EVIDENCE`-class markers — full list

| Marker | Document | Section |
|---|---|---|
| `PENDING_EMPIRICAL_TBBO_VS_MBP1_MEASUREMENT` | `canonical-market-events.md` | §3 |
| `PENDING_EMPIRICAL_TBBO_VS_MBP1_MEASUREMENT` (referenced) | `p0-microstructure-requirements-matrix.md` | primer note, "Databento's TBBO..." line |
| `PENDING_EMPIRICAL_TBBO_VS_MBP1_MEASUREMENT` (referenced) | `gc-data-volume-and-cost-study.md` | §1, §4 |
| `PENDING_EMPIRICAL_TBBO_VS_MBP1_MEASUREMENT` (referenced) | `hmt1-provisional-scope.md` | §1, §4 |
| `PENDING_EVIDENCE` (raw data volume matrix, all cells) | `gc-data-volume-and-cost-study.md` | §2.1 |
| `PENDING_EVIDENCE` (acquisition cost matrix, all cells) | `gc-data-volume-and-cost-study.md` | §2.2 |
| `PENDING_EVIDENCE` (volume/cost by provenance era, all cells) | `gc-data-volume-and-cost-study.md` | §2.3 |
| `PENDING_EVIDENCE` (storage-tier cost implications, all cells) | `gc-data-volume-and-cost-study.md` | §2.4 |
| `PENDING_EVIDENCE` (era-specific timestamp/provenance confirmation, `PRE_2015_11_20_LEGACY`) | `time-order-sequence-model.md` | §3 table |
| `PENDING_WRITTEN_PROVIDER/LICENSOR_CONFIRMATION` (as `PERMANENT_NATIVE_CORPUS_RETENTION = ...`) | `licensing-and-security.md` | §2 |

A future reader who needs to know "what is this whole programme still waiting on" can read this table
alone without hunting through all 15 documents.

## What this closure pack authorises

**Nothing beyond itself.** This pack authorises the existence of these 16 documents as a recorded
architecture direction. It does **not** authorise:

- Any HMT-1 implementation (see `hmt1-provisional-scope.md` — explicitly APPROVED IN PRINCIPLE / NOT
  AUTHORISED, gated behind a separate, explicit future authorisation).
- Any acquisition of GC market data, temporary or permanent (gated behind
  `gc-data-volume-and-cost-study.md` and `licensing-and-security.md`, both still open).
- Any retirement action against legacy `candles_D1` or `market-map-dev.service` (both roadmaps are
  recorded, neither is started — `legacy-migration-roadmap.md`).
- Any DARWIN/ATHENA work of any kind — stated explicitly as a non-goal, not merely an absence.

HMT-1 implementation remains separately gated, exactly as `hmt1-provisional-scope.md` §3 states.

## Honest disclosure

This pack was produced by reading the real repository (schema, migrations, existing architecture/ADR/
governance documents, and `ops/evidence/` history) rather than by assumption, per this pack's binding
brief. Every factual claim about "what already exists" in this pack is traceable to a specific file cited
in the relevant document. Every forward-looking architectural claim is stated as direction, not status.
Every genuinely unmeasured quantity is marked `PENDING_EVIDENCE` (or the more specific named markers
above) rather than estimated. No credential or secret material appears anywhere in this pack.
