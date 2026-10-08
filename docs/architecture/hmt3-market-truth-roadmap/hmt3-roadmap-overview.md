# HMT-3 — Post-HMT-2 Market Truth Roadmap — Overview

**Initiative:** `HMT-3 — Post-HMT-2 HERMES Market Truth Roadmap`
**Status:** **DOCUMENTATION / GOVERNANCE RECORD ONLY. NOT YET AUTHORISED FOR IMPLEMENTATION.**

> **This pack does NOT authorise HMT-3 implementation.** Current HMT-2 acquisition/canonicalisation work
> remains the **only authorised implementation phase** of this programme. **DARWIN remains FROZEN** for
> this programme until Central Architecture changes that authority. Nothing in this pack — including the
> existence of a named, sequenced roadmap — constitutes or implies that authorisation. Every HMT-3A
> through HMT-3I item below carries its own explicit **NOT YET AUTHORISED FOR IMPLEMENTATION** marker,
> restated at its own heading, precisely so that no individual item document can be read in isolation and
> mistaken for a go-ahead.

## What this pack is

This is the governed post-HMT-2 HERMES Market Truth roadmap, recorded as a documentation/architecture
governance exercise under `docs/architecture/hmt3-market-truth-roadmap/`. It transcribes, faithfully and
without compression, the Architect's adjudication of recent GC/order-flow research into a named, sequenced
set of future work items (HMT-3A through HMT-3I), together with the doctrine that governs all of them. No
`.py` file, migration, schema, config, or CI workflow is touched to produce this pack. No HMT-3
implementation, market-data integration, or product/runtime behaviour change is performed or authorised by
it. The active HMT-2 branch/worktree (`hmt-2/governed-gc-mbp1-historical-corpus`) and its live corpus-drain
operation are untouched by this pack — this pack neither modifies nor depends on anything in that branch.

## Purpose

Record the governed post-HMT-2 HERMES Market Truth roadmap based on the Architect's adjudication of recent
GC/order-flow research.

## The governing system separation

This is the doctrine that every item in this roadmap is subordinate to, and that this pack restates in
full because it governs every downstream decision:

- **HERMES** measures reproducible market facts.
- **DARWIN** tests whether facts contain economic/predictive value.
- **HSA** formalises strategies supported by research.
- **HELIOS** evaluates authorised strategies deterministically.

> No trading strategy, threshold optimisation, trader-intent narrative or presenter folklore becomes
> HERMES truth.

## The pack index

### Future authorised sequence — NOT YET AUTHORISED FOR IMPLEMENTATION

Nine roadmap items, each in its own document, each individually and explicitly marked
**NOT YET AUTHORISED FOR IMPLEMENTATION** at its own heading:

1. **[HMT-3A — Derived Fact Spine + Executed Flow](hmt3a-derived-fact-spine-and-executed-flow.md)** —
   the `DerivedFact` architecture and the GC price-level executed-flow fact list; no hard-coded imbalance
   thresholds; governed intervals; re-verification of Databento side/aggressor semantics required before
   implementation.
2. **[HMT-3B — Flow Response / Efficiency](hmt3b-flow-response-efficiency.md)** — aggressive volumes,
   delta, displacement, and efficiency measures; `TRAPPED_TRADER` may never be published as market truth;
   no trader-intent assertions; contemporaneous-vs-ex-post measurement and `available_at` discipline.
3. **[HMT-3C — Auction / Volume Profile](hmt3c-auction-volume-profile.md)** — volume-by-price, POC/VPOC,
   configurable value-area calculations, HVN/LVN detector outputs, profile geometry, and developing state;
   material volume regions must preserve full geometry; every algorithm/parameter explicit and versioned.
4. **[HMT-3D — Structural Price Geography](hmt3d-structural-price-geography.md)** — prior-period
   extrema, developing/session extrema, governed swing extrema, and ATH semantics; strict
   `ACTUAL_CONTRACT`-vs-`CONTINUOUS_DERIVED` separation; deterministic, versioned reaction-zone and
   structural-clearance measurement.
5. **[HMT-3E — Level Interaction State](hmt3e-level-interaction-state.md)** — formation, departure, age,
   closest approach, touch ordinal, penetration, time/bars away, interaction history, excursions and
   reactions; no baked-in assumptions about first-touch superiority, near-miss consumption, or fixed
   maturity periods; classifications only as explicitly parameterised/versioned derivations, with DARWIN
   determining their value.
6. **[HMT-3F — BBO Microstructure](hmt3f-bbo-microstructure.md)** — bid/ask, displayed size, spread,
   displayed-book imbalance, midpoint, microprice, microprice-midpoint displacement, and versioned OFI over
   governed genuine BBO state; no implied individual queue position from MBP-1.
7. **[HMT-3G — Liquidity Resiliency](hmt3g-liquidity-resiliency.md)** — visible top-of-book liquidity
   removed/restored, replenishment time, repeated execution at a price, BBO depletion, spread response,
   quote-update intensity, and price progress during aggressive execution, all within honest MBP-1 limits;
   no claimed MBO/order-level cancellation or queue-position knowledge; MBO remains a future
   evidence-gated acquisition decision.
8. **[HMT-3H — GC ↔ XAU Alignment / Basis](hmt3h-gc-xau-alignment-basis.md)** — native GC and native
   XAUUSD preserved separately with their own timestamps/precision, contemporaneous alignment, and an
   explicitly-derived basis; GC price levels are never projected directly onto XAUUSD as though the
   instruments share identical prices.
9. **[HMT-3I — Objective Gap Geometry](hmt3i-objective-gap-geometry.md)** — low priority; deterministic
   three-candle non-overlap geometry and lifecycle facts, with no institutional/"smart money" semantics;
   DARWIN determines whether the geometry adds incremental information.

### Governing doctrine — applies across every item above

6 doctrine documents, each binding on all nine roadmap items:

10. **[Derived Fact Contract Doctrine](derived-fact-contract-doctrine.md)** — the governed common fact
    envelope that HMT-3A must turn the existing HMT-1 derived-fact identity skeleton into, its minimum
    field set, and the temporal-leakage-resistance requirement.
11. **[Parameter Doctrine](parameter-doctrine.md)** — the full list of parameters (imbalance thresholds,
    intervals, value-area percentage, HVN/LVN threshold, reaction-zone width, touch distance, maturity
    duration, near-test distance, CVD reset, large-trade percentile, OFI formula) that are never universal
    truth, and the versioned-derivation-parameter requirement wherever they are implemented.
12. **[Terminology Doctrine](terminology-doctrine.md)** — the forbidden-as-unqualified-fact vocabulary
    (trapped traders, smart money, institutional buying/selling, manipulation, absorption-as-intent,
    unqualified maturity classifications, unqualified support/resistance) and what HERMES may publish
    instead.
13. **[Research-only / strategy-only concepts](research-only-strategy-concepts.md)** — the concepts
    (stop rules, catastrophic stop placement, first-touch preference, confluence assumptions, TP/SL and
    entry/exit decisions, horizon-specific strategy weighting) that belong to DARWIN and, only after
    evidence, HSA/HELIOS — never to HERMES.
14. **[Deferred acquisition families](deferred-acquisition-families.md)** — the data families (MBO/L3,
    detailed cancellation/queue-position data, GC options IV/skew/OI, EBS spot-gold, wider cross-market
    instruments, rates/DXY/silver/miner structures) that are not to be built or acquired yet solely because
    they are interesting.
15. **[Determinism Standard](determinism-standard.md)** — the reproducibility equation that governs every
    fact in this roadmap, and the "missing is never silently zero, unknown remains unknown" rule.

## Hand-off note

The roadmap content recorded in this pack is ready for hand-off to whoever owns recording it into Memory
Fabric or any other persona-specific system of record (e.g. R2D2's or Helm's own namespaces). This pack and
the agent that produced it have no access to, and have made no attempt to write to, any such system —
that hand-off step is explicitly out of scope here and must be performed separately by whoever holds that
authority.

## Relationship to HMT-0, HMT-1, and HMT-2

This roadmap is the direct successor, in the same documentation-governance tradition, to the HMT-0
("Market Truth v2") architecture-closure pack at
[`docs/architecture/hmt0-market-truth-v2/`](../hmt0-market-truth-v2/hmt0-closure-report.md), and in
particular to that pack's own `hmt2-provisional-acquisition-roadmap.md`. HMT-1 (canonical market event and
replay foundation) and HMT-2 (governed GC MBP-1 historical corpus acquisition/canonicalisation) are the
programme's authorised implementation phases; HMT-2 is, as of this pack, the active and only authorised
implementation phase. HMT-3 is deliberately recorded now, while HMT-2 is in flight, purely as a governance
record of what the Architect has adjudicated as the *next* research/derived-fact sequence — so that the
adjudication is not lost — without that recording being read as, or becoming, an authorisation to begin any
of it.
