# HMT-3A — Derived Fact Spine + Executed Flow

**Initiative:** `HMT-3 — Post-HMT-2 HERMES Market Truth Roadmap`
**Status:** **NOT YET AUTHORISED FOR IMPLEMENTATION.** See
[`hmt3-roadmap-overview.md`](hmt3-roadmap-overview.md).

## Purpose

Establish the `DerivedFact` architecture — the common spine every later HMT-3 item's facts are built on —
and name the initial set of GC price-level executed-flow facts it must be able to carry, together with the
explicit constraints on how thresholds, intervals, and provider semantics may and may not be baked into
that spine.

## §1 — The `DerivedFact` architecture

HMT-3A is the item responsible for turning the existing HMT-1 derived-fact identity skeleton into the
governed common `DerivedFact` envelope described in full in
[`derived-fact-contract-doctrine.md`](derived-fact-contract-doctrine.md). Every fact produced by this item,
and by every later HMT-3 item that builds on it, must be expressed through that envelope — this item does
not define a parallel or bespoke identity model for executed-flow facts.

## §2 — GC price-level executed-flow facts

The initial fact list this item must support, at the GC price level:

- Buyer-aggressor executed volume.
- Seller-aggressor executed volume.
- Unknown-side volume.
- Total executed volume.
- Delta.
- Delta percentage.
- Continuous diagonal buy/sell ratios.
- Adjacent-price relationships.
- Interval aggregates.
- Source-event counts.
- Unknown-side/quality metrics.

## §3 — No hard-coded imbalance thresholds

No imbalance threshold (e.g. a 3:1 or 4:1 ratio) may be hard-coded into this item's derivation logic. Where
a thresholded or stacked-imbalance fact is implemented, it must declare its own explicit derivation
configuration — the threshold is a named, versioned parameter of that specific fact's derivation, never an
implicit constant baked into shared code. See [`parameter-doctrine.md`](parameter-doctrine.md) for the full
doctrine this rule sits under.

## §4 — Intervals are governed parameters

Interval aggregates must be computed over intervals that are themselves governed parameters, not a
hard-coded 5-minute assumption. Any interval length used by this item's facts must be explicit, named, and
versioned exactly as any other derivation parameter under
[`parameter-doctrine.md`](parameter-doctrine.md).

## §5 — Databento side/aggressor semantics must be re-verified before implementation

Before any implementation of this item, the exact Databento side/aggressor semantics must be re-verified
against the governed provider schema/version actually in use — not assumed from memory, from a prior
integration, or from general market-data convention. This item must **not** rely solely on research notes
for that semantic mapping; the provider's own governed schema/version documentation is the authority that
must be consulted and confirmed at implementation time.

## §6 — Non-authorisation

This document describes the intended shape and constraints of HMT-3A. It does not authorise beginning any
implementation of it. See [`hmt3-roadmap-overview.md`](hmt3-roadmap-overview.md) for the programme-wide
non-authorisation statement.
