# HMT-3C — Auction / Volume Profile

**Initiative:** `HMT-3 — Post-HMT-2 HERMES Market Truth Roadmap`
**Status:** **NOT YET AUTHORISED FOR IMPLEMENTATION.** See
[`hmt3-roadmap-overview.md`](hmt3-roadmap-overview.md).

## Purpose

Name the auction/volume-profile fact family and the geometry-preservation and parameter-explicitness
requirements that govern it.

## §1 — Fact family

- Volume by price.
- POC/VPOC.
- Configurable value-area calculations.
- HVN/LVN detector outputs.
- Profile geometry.
- Developing state.

## §2 — Material volume regions must preserve full geometry

Material volume regions (e.g. HVN/LVN regions) must preserve their geometry in full — a region is never
reduced to a single summary value. At minimum, each such region must retain:

- Low edge.
- High edge.
- POC.
- Width.
- Total volume.
- Rank.
- Formation interval.
- Profile identity.

## §3 — All parameters explicit and versioned

Every algorithm, percentage, tie-breaking rule, and region-detection parameter used by this item's facts
must be explicit and versioned — this includes (without limitation) the value-area percentage and any
HVN/LVN threshold used to derive a region. None of these may be an implicit constant; see
[`parameter-doctrine.md`](parameter-doctrine.md) for the doctrine governing parameters of this kind across
the whole roadmap.

## §4 — Non-authorisation

This document describes the intended shape and constraints of HMT-3C. It does not authorise beginning any
implementation of it. See [`hmt3-roadmap-overview.md`](hmt3-roadmap-overview.md) for the programme-wide
non-authorisation statement.
