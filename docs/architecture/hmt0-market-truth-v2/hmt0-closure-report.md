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
   `PROXY`-vs-`EXACT` labelling discipline where a weaker level is used. Updated in this revision: the
   primer note and "Open item" now point at the resolved MBP-1 ruling instead of the pending measurement;
   the fact-class-to-minimum-level mapping itself is unchanged.
4. **[`canonical-market-events.md`](canonical-market-events.md)** — `MarketQuoteEvent`/
   `MarketTradeEvent`/`TopOfBookEvent`. Rules that TBBO alone cannot satisfy genuine `TopOfBookEvent`
   semantics (trade-anchored sampling only); MBP-1 can. Updated in this revision: §3 now records Central
   Architecture's ruling on the previously-deferred P0 native-corpus choice — **MBP-1** — on the empirical
   basis in `gc-data-volume-and-cost-study.md`; the `PENDING_EMPIRICAL_TBBO_VS_MBP1_MEASUREMENT` marker is
   superseded there.
5. **[`time-order-sequence-model.md`](time-order-sequence-model.md)** — six permanently-separate time/
   sequence fields; explicitly forbids reusing HERMES's existing stored-row `ticks.seq` (ratified by
   `docs/hermes_tick_seq_semantic.md`) as an exchange/provider sequence number; forbids fabricated
   cross-source global ordering; ties the three historical provenance eras into per-record quality
   propagation. Updated in this revision: §3 now notes the `MDP3_FROM_2017_05_21` era boundary is
   independently corroborated by Databento's own schema-availability boundary while the
   `PRE_2015_11_20_LEGACY`/`2015_11_20_TO_2017_05_20_LEGACY` boundary is not — the `PRE_2015_11_20_LEGACY`
   timestamp-confirmation `PENDING_EVIDENCE` cell itself remains open, unresolved by this note; §5's
   reference to the now-resolved native-corpus decision is updated.
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
11. **[`gc-data-volume-and-cost-study.md`](gc-data-volume-and-cost-study.md)** — originally a measurement
    template, every cell `PENDING_EVIDENCE`. Updated in this revision: now populated with the real,
    outright-only Databento measurement (120 verified GC outright futures contracts, 2010-06-06 →
    2026-09-19) — TBBO 422,689,297 records / 33,815,143,760 bytes / $881.798589; MBP-1 4,324,008,111
    records / 345,920,648,880 bytes / $579.894677; full era breakdown; a new §2.5 retaining the prior
    spread-inclusive full-history figures as clearly-labelled superseded/historical evidence (+4.5–5.3%
    spread inflation). Cells with no measurement (Trades level, 1-day/1-month/1-year horizons, live-
    subscription monthly cost, storage-tier $/GB rate) remain `PENDING_EVIDENCE`, unchanged.
12. **[`licensing-and-security.md`](licensing-and-security.md)** — the binding `C — HYBRID` build-v-rent
    ruling (HERMES permanently owns derived facts/definitions/calendar-roll-history/schema-identity/
    evidence vault; the native corpus itself only with BOTH empirical cost support AND written licensing);
    the literal `PERMANENT_NATIVE_CORPUS_RETENTION = PENDING_WRITTEN_PROVIDER/LICENSOR_CONFIRMATION`
    marker; explicit narrowing away from MBO/deeper books/all-CME/unrelated sources. Updated in this
    revision: records that the native corpus is now ruled MBP-1 and that empirical support (condition 1)
    is satisfied (~345.9 GB, acceptable cost); adds the synonym marker
    `PENDING_WRITTEN_LICENCE_RETENTION_CONFIRMATION` alongside the original, both denoting the same still-
    open licensing gate (condition 2) — the favourable cost measurement does not clear it.
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

**Resolved/populated since the original pack revision:** `PENDING_EMPIRICAL_TBBO_VS_MBP1_MEASUREMENT`
(all 4 prior occurrences — `canonical-market-events.md` §3, `p0-microstructure-requirements-matrix.md`'s
primer note, `gc-data-volume-and-cost-study.md` §1/§4, and `hmt1-provisional-scope.md` §1/§4's reference
to it) is **superseded**: Central Architecture has ruled the P0 native corpus is **MBP-1**, on the
empirical measurement now in `gc-data-volume-and-cost-study.md` §2 (see `canonical-market-events.md` §3
for the ruling). `gc-data-volume-and-cost-study.md` §2.1/§2.2/§2.3/§2.4's previous all-cells-`PENDING_EVIDENCE`
rows are now populated with real, outright-only measurement data — a handful of individually-unmeasured
cells within that document (Trades-level figures, 1-day/1-month/1-year horizons, ongoing live-
subscription cost, storage-tier $/GB rate) remain `PENDING_EVIDENCE`, but these are no longer
all-cells-pending rows and are not separately enumerated here. Two items remain genuinely open:

| Marker | Document | Section |
|---|---|---|
| `PENDING_EVIDENCE` (era-specific timestamp/provenance confirmation, `PRE_2015_11_20_LEGACY`) | `time-order-sequence-model.md` | §3 table |
| `PENDING_WRITTEN_PROVIDER/LICENSOR_CONFIRMATION` / `PENDING_WRITTEN_LICENCE_RETENTION_CONFIRMATION` (synonyms, same open gate, as `PERMANENT_NATIVE_CORPUS_RETENTION = ...`) | `licensing-and-security.md` | §2 |

A future reader who needs to know "what is this whole programme still waiting on" can read this table
alone without hunting through all 15 documents.

## Status of this revision (empirical evidence + P0 corpus ruling)

- Archaeology: **GREEN**.
- Architecture: **GREEN**.
- P0 native corpus schema decision: **GREEN — MBP-1** (`canonical-market-events.md` §3).
- Empirical metadata/sizing measurement: **GREEN** (`gc-data-volume-and-cost-study.md` §2).
- Outright-only corpus measurement (120 verified GC outright contracts, spreads excluded, cross-validated
  against Databento's own `instrument_class=F` field): **GREEN**.
- `darwin_ro` provenance: **GREEN** (unchanged by this revision — see `darwin-ro-provenance-chronology.md`).
- Vantage RPyC containment: **GREEN** (unchanged by this revision, tracked in Fabric/Helm state, not a
  document in this pack).
- Licensing retention confirmation: **OPEN** — `licensing-and-security.md` §2, not cleared by this
  revision.
- **HMT-1 remains NOT AUTHORISED.** **DARWIN remains completely frozen and untouched.** Neither statement
  is weakened by this revision's P0 corpus ruling.

**Overall pack status: `AMBER / CLOSURE PENDING LICENSING`.** This revision resolves the P0 native-corpus
schema question and supplies the empirical size/cost evidence, but does not achieve full closure — the
licensing gate on permanent retention of the native corpus is still open, and closure is not claimed
until it clears.

## What this closure pack authorises

**Nothing beyond itself.** This pack authorises the existence of these 16 documents as a recorded
architecture direction. It does **not** authorise:

- Any HMT-1 implementation (see `hmt1-provisional-scope.md` — explicitly APPROVED IN PRINCIPLE / NOT
  AUTHORISED, gated behind a separate, explicit future authorisation).
- Any acquisition of GC market data, temporary or permanent. The P0 native-corpus schema choice is now
  resolved (MBP-1) and its empirical size/cost support is satisfied, but acquisition remains gated on
  `licensing-and-security.md`'s still-open licensing confirmation.
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

This revision's empirical figures (`gc-data-volume-and-cost-study.md` §2) were produced by Helm, delivered
via Fabric, and independently cross-validated by Rogue against the directive before being written into
this pack — the same evidence discipline as the rest of the pack applies: no figure beyond what was
explicitly measured is stated, and nothing here is estimated, rounded differently, or invented.
