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
statement in HMT-1's own scope context). **DARWIN remains FROZEN.**

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
   `main.py`'s test suite, unrelated to but worth noting for HMT-1. Updated in this revision: §1.3's
   provider bullet is corrected — HERMES's engineering capability to acquire GC data is not
   licensing-gated per Central Architecture's de-gating ruling.
3. **[`p0-microstructure-requirements-matrix.md`](p0-microstructure-requirements-matrix.md)** — for every
   P0 fact class (volume-at-price, POC, VAH/VAL, LVN/HVN, aggressor volume, delta, CVD, trade-size
   distributions, aggression clusters, flow efficiency, absorption, failed aggression,
   balance/imbalance, acceptance/rejection, failed auctions, squeeze/trapped-flow proxies), the minimum
   feed level (Trades/TBBO/MBP-1; MBO never introduced) that computes it correctly, with honest
   `PROXY`-vs-`EXACT` labelling discipline where a weaker level is used. Updated in a prior revision: the
   primer note and "Open item" now point at the resolved MBP-1 ruling instead of the pending measurement;
   the fact-class-to-minimum-level mapping itself is unchanged. Updated in **this** revision: the
   "Resolved item" section's closing sentence is corrected — it previously described the licensing gate on
   permanent retention as "still open," which no longer reflects Central Architecture's non-gating ruling.
4. **[`canonical-market-events.md`](canonical-market-events.md)** — `MarketQuoteEvent`/
   `MarketTradeEvent`/`TopOfBookEvent`. Rules that TBBO alone cannot satisfy genuine `TopOfBookEvent`
   semantics (trade-anchored sampling only); MBP-1 can. Updated in a prior revision: §3 records Central
   Architecture's ruling on the previously-deferred P0 native-corpus choice — **MBP-1** — on the empirical
   basis in `gc-data-volume-and-cost-study.md`; the `PENDING_EMPIRICAL_TBBO_VS_MBP1_MEASUREMENT` marker was
   superseded there. Updated in **this** revision: §3's licensing-gate framing is corrected — licensing/
   commercial correspondence is external programme administration, not an engineering gate on MBP-1's
   status as the ruled P0 native corpus.
5. **[`time-order-sequence-model.md`](time-order-sequence-model.md)** — six permanently-separate time/
   sequence fields; explicitly forbids reusing HERMES's existing stored-row `ticks.seq` (ratified by
   `docs/hermes_tick_seq_semantic.md`) as an exchange/provider sequence number; forbids fabricated
   cross-source global ordering; ties the three historical provenance eras into per-record quality
   propagation. Updated in a prior revision: §3 notes the `MDP3_FROM_2017_05_21` era boundary is
   independently corroborated by Databento's own schema-availability boundary. Updated in **this**
   revision: §3's era table is rewritten in full — the `PRE_2015_11_20_LEGACY` `PENDING_EVIDENCE` cell is
   now resolved (per Databento's official CME GLBX.MDP3 documentation: millisecond resolution, no genuine
   provider capture timestamp, `ts_recv` synthetically equated to `ts_event`, `F_BAD_TS_RECV` flag
   propagates), and the `2015_11_20_TO_2017_05_20_LEGACY` era is corrected to show nanosecond timestamp
   *resolution* without genuine independent capture time — that only arrives at the 2017-05-21 MDP3
   boundary.
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
   in this repository). Updated in this revision: §1.2's reference to "the still-pending TBBO-vs-MBP-1
   decision" is corrected to state plainly that the native GC corpus is MBP-1.
9. **[`deterministic-replay-and-evidence.md`](deterministic-replay-and-evidence.md)** — the
   reproducibility guarantee tied to derived-fact identity fields, and the parameter-transmission
   integrity requirement (parameters captured at point of computation, not reconstructed from possibly-
   drifted configuration).
10. **[`provider-abstraction.md`](provider-abstraction.md)** — the boundary between provider wire
    format and canonical HERMES representation, generalising the existing `adapters/base.py` (OANDA)
    precedent to a GC provider (e.g. Databento) that must remain swappable. Updated in this revision: §2's
    provider-swap rationale no longer leans on the licensing/cost gates as "still open" justification — the
    swappability requirement stands on its own foreseeability grounds.
11. **[`gc-data-volume-and-cost-study.md`](gc-data-volume-and-cost-study.md)** — originally a measurement
    template, every cell `PENDING_EVIDENCE`. Updated in a prior revision: populated with the real,
    outright-only Databento measurement (120 verified GC outright futures contracts, 2010-06-06 →
    2026-09-19) — TBBO 422,689,297 records / 33,815,143,760 bytes / $881.798589; MBP-1 4,324,008,111
    records / 345,920,648,880 bytes / $579.894677; full era breakdown; a §2.5 retaining the prior
    spread-inclusive full-history figures as clearly-labelled superseded/historical evidence (+4.5–5.3%
    spread inflation). Updated in **this** revision: §1/§3/§4's licensing-gate framing is corrected —
    licensing/commercial correspondence is external programme administration, not an engineering
    condition this document supplies or withholds evidence for; the previously-unmeasured cells (Trades
    level, 1-day/1-month/1-year horizons, live-subscription monthly cost, storage-tier $/GB rate) are
    relabelled `NOT_MEASURED / NOT_REQUIRED_FOR_HMT0` rather than `PENDING_EVIDENCE`, since none were ever
    required for the MBP-1 ruling itself — no number is invented for any of them.
12. **[`licensing-and-security.md`](licensing-and-security.md)** — the binding `C — HYBRID` build-v-rent
    ruling (HERMES permanently owns derived facts/definitions/calendar-roll-history/schema-identity/
    evidence vault; HERMES's engineering architecture is fully capable of acquiring/retaining/
    canonicalising/replaying/storing/researching the native MBP-1 corpus); explicit narrowing away from
    MBO/deeper books/all-CME/unrelated sources; security note confirming no credential material anywhere
    in this pack. Updated in a prior revision: recorded that the native corpus is now ruled MBP-1 and that
    empirical support is satisfied (~345.9 GB, acceptable cost). Updated in **this** revision, per Central
    Architecture's ruling that licensing/commercial matters are external programme administration: the
    literal `PERMANENT_NATIVE_CORPUS_RETENTION = PENDING_WRITTEN_PROVIDER/LICENSOR_CONFIRMATION` marker
    (and its synonym `PENDING_WRITTEN_LICENCE_RETENTION_CONFIRMATION`) is **removed** — not replaced by a
    new engineering gate; §2 is restructured to record commercial/licensing context as non-gating,
    external programme administration.
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
    implementation detail; explicit DARWIN/ATHENA non-goal statement. Updated in this revision: §2's
    permanent native-corpus-acquisition bullet is corrected — the engineering capability is not
    licensing-gated per Central Architecture's de-gating ruling; what HMT-1 authorisation does not itself
    satisfy is *implementation authorisation*, a completely separate and unrelated gate from licensing.
    **HMT-1 remains NOT AUTHORISED**, unaffected by this correction.
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

**Resolved since the original pack revision:**
- `PENDING_EMPIRICAL_TBBO_VS_MBP1_MEASUREMENT` (all 4 prior occurrences — `canonical-market-events.md` §3,
  `p0-microstructure-requirements-matrix.md`'s primer note, `gc-data-volume-and-cost-study.md` §1/§4, and
  `hmt1-provisional-scope.md` §1/§4's reference to it) — Central Architecture has ruled the P0 native
  corpus is **MBP-1**, on the empirical measurement in `gc-data-volume-and-cost-study.md` §2.
- `PRE_2015_11_20_LEGACY` timestamp/provenance confirmation (`time-order-sequence-model.md` §3) —
  resolved per Databento's official CME GLBX.MDP3 documentation: millisecond resolution, no genuine
  provider capture timestamp, `ts_recv` synthetically equated to `ts_event`, `F_BAD_TS_RECV` quality flag
  propagates. The `2015_11_20_TO_2017_05_20_LEGACY` era is likewise clarified: nanosecond timestamp
  *resolution* from 2015-11-20, but still no genuine independent capture time until the 2017-05-21 MDP3
  boundary.

**Retired this revision — no longer a `PENDING_EVIDENCE`-class marker, and not replaced by a new
engineering gate:**
- `PERMANENT_NATIVE_CORPUS_RETENTION = PENDING_WRITTEN_PROVIDER/LICENSOR_CONFIRMATION` (and its synonym
  `PENDING_WRITTEN_LICENCE_RETENTION_CONFIRMATION`), formerly in `licensing-and-security.md` §2. Central
  Architecture has ruled that licensing/commercial correspondence is external programme administration —
  it does not alter HERMES technical capability, contracts, canonical semantics, or HMT phase engineering
  acceptance. Actual organisational/commercial use of HERMES's acquisition/retention capability remains
  subject to whatever commercial arrangements the programme separately administers — external to this
  pack and to HMT-0 engineering closure.

**Relabelled this revision — non-gating, not required for HMT-0 closure (marker:
`NOT_MEASURED / NOT_REQUIRED_FOR_HMT0`):** all in `gc-data-volume-and-cost-study.md`, all
planning-granularity data superseded by the full-history MBP-1 measurement that actually grounds the P0
corpus ruling — none of these were ever required for that ruling:
- §2.1 — Trades-level volume figures (all horizons); TBBO/MBP-1 1-day/1-month/1-year horizon breakdowns.
- §2.2 — Trades-level cost figures; TBBO/MBP-1 ongoing live-subscription monthly cost.
- §2.4 — OPERATIONAL TRUTH and EVIDENCE VAULT tier cost estimates (depend on future bounded-depth/
  promoted-fact volumes, not the raw corpus this study measured); MARKET RESEARCH STORE's storage-medium
  $/GB infrastructure rate (distinct from, and not required to know, the Databento acquisition cost
  already measured in §2.2).

**Remaining genuinely open:** none identified in this pass. Every `PENDING_EVIDENCE`-class marker found
pack-wide in this revision's own full scan is either resolved or relabelled non-gating above — see this
document's own production record for the scan that confirmed this.

A future reader who needs to know "what is this whole programme still waiting on" can read this table
alone without hunting through all 15 documents: as of this revision, nothing engineering-relevant is.

## Status of this revision (empirical evidence + P0 corpus ruling + licensing de-gating + timestamp resolution)

- Archaeology: **GREEN**.
- Architecture: **GREEN**.
- P0 native corpus: **GREEN — MBP-1** (`canonical-market-events.md` §3).
- Empirical sizing/cost: **GREEN** (`gc-data-volume-and-cost-study.md` §2).
- Outright-only full-history measurement (120 verified GC outright contracts, spreads excluded,
  cross-validated against Databento's own `instrument_class=F` field): **GREEN**.
- Vantage containment: **GREEN** (unchanged by this revision, tracked in Fabric/Helm state, not a
  document in this pack).
- `darwin_ro`: **GREEN** (unchanged by this revision — see `darwin-ro-provenance-chronology.md`).
- Licensing/commercial correspondence: **EXTERNAL PROGRAMME ADMINISTRATION / NON-GATING.** Per Central
  Architecture's ruling, licensing/commercial matters do not alter HERMES technical capability, contracts,
  canonical semantics, or HMT phase engineering acceptance. This is **not** "OPEN" and is **not** a
  closure blocker — it is explicitly retired as an engineering/closure gate in this revision
  (`licensing-and-security.md`).
- **HMT-1: NOT AUTHORISED.** **DARWIN: FROZEN.** Neither statement is weakened by this revision's
  licensing de-gating or by the P0 corpus ruling — these are completely separate, unrelated gates.

**Recommended verdict:**

> **HMT-0: GREEN / READY FOR CENTRAL CLOSURE**

This is a recommendation only, made by this revision, subject to this revision's own Git/CI/security
evidence being confirmed GREEN by independent audit — it does not itself constitute Central Architecture's
closure of HMT-0. This revision resolves the P0 native-corpus schema question, supplies the empirical
size/cost evidence, corrects the licensing-gate framing per Central Architecture's ruling, and resolves
the `PRE_2015_11_20_LEGACY` timestamp-evidence question per Databento's official documentation. No
engineering/closure gate remains open in this pack as of this revision's own scan (see the
collected-markers list above).

## What this closure pack authorises

**Nothing beyond itself.** This pack authorises the existence of these 16 documents as a recorded
architecture direction. It does **not** authorise:

- Any HMT-1 implementation (see `hmt1-provisional-scope.md` — explicitly APPROVED IN PRINCIPLE / NOT
  AUTHORISED, gated behind a separate, explicit future authorisation). This gate is completely unrelated
  to, and unaffected by, this revision's licensing de-gating below.
- Any actual acquisition of GC market data, temporary or permanent, under this pack alone. The P0
  native-corpus schema choice is resolved (MBP-1), its empirical size/cost support is satisfied, and
  HERMES's engineering capability to acquire/retain it is not licensing-gated (per Central Architecture's
  ruling, `licensing-and-security.md`) — but actual acquisition still requires HMT-1 implementation
  authorisation (not granted here) and remains subject to whatever commercial arrangements the programme
  separately administers, external to this pack.
- Any retirement action against legacy `candles_D1` or `market-map-dev.service` (both roadmaps are
  recorded, neither is started — `legacy-migration-roadmap.md`).
- Any DARWIN/ATHENA work of any kind — stated explicitly as a non-goal, not merely an absence. **DARWIN
  remains FROZEN.**

HMT-1 implementation remains separately gated, exactly as `hmt1-provisional-scope.md` §3 states. **HMT-1
remains NOT AUTHORISED.**

## Honest disclosure

This pack was produced by reading the real repository (schema, migrations, existing architecture/ADR/
governance documents, and `ops/evidence/` history) rather than by assumption, per this pack's binding
brief. Every factual claim about "what already exists" in this pack is traceable to a specific file cited
in the relevant document. Every forward-looking architectural claim is stated as direction, not status.
Every genuinely unmeasured quantity is marked `PENDING_EVIDENCE`, or, where non-gating,
`NOT_MEASURED / NOT_REQUIRED_FOR_HMT0`, rather than estimated. No credential or secret material appears
anywhere in this pack.

This revision's empirical figures (`gc-data-volume-and-cost-study.md` §2) were produced by Helm, delivered
via Fabric, and independently cross-validated by Rogue against the directive before being written into
this pack — the same evidence discipline as the rest of the pack applies: no figure beyond what was
explicitly measured is stated, and nothing here is estimated, rounded differently, or invented. This
revision's timestamp/provenance facts for the legacy eras (`time-order-sequence-model.md` §3) are
attributed explicitly to Databento's official CME GLBX.MDP3 documentation, as supplied by Central
Architecture, and are not independently re-verified by this pack beyond that attribution — no stronger
precision claim is made than what that documentation states.
