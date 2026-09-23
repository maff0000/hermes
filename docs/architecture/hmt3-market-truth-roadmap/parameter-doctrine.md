# HMT-3 — Parameter Doctrine

**Initiative:** `HMT-3 — Post-HMT-2 HERMES Market Truth Roadmap`
**Status:** DOCUMENTATION / GOVERNANCE RECORD ONLY. Governs every HMT-3 item. See
[`hmt3-roadmap-overview.md`](hmt3-roadmap-overview.md) for the roadmap-wide non-authorisation statement.

## Purpose

Name, in full, the specific numeric parameters that recur across the HMT-3 fact families, and state
plainly that none of them is universal truth.

## §1 — The parameter list

None of the following is universal truth:

- 3:1 / 4:1 imbalance threshold.
- 5-minute interval.
- Value-area percentage.
- HVN/LVN threshold.
- Reaction-zone width.
- Touch distance.
- Maturity duration.
- Near-test distance.
- CVD reset.
- Large-trade percentile.
- OFI formula.

Every one of these appears in the specific per-item fact families defined elsewhere in this roadmap (e.g.
the imbalance threshold and interval in [HMT-3A](hmt3a-derived-fact-spine-and-executed-flow.md), the
value-area percentage and HVN/LVN threshold in [HMT-3C](hmt3c-auction-volume-profile.md), the
reaction-zone width in [HMT-3D](hmt3d-structural-price-geography.md), touch distance/maturity
duration/near-test distance in [HMT-3E](hmt3e-level-interaction-state.md), and the OFI formula in
[HMT-3F](hmt3f-bbo-microstructure.md)); this document is the single place their shared doctrine is stated
in full, rather than repeated piecemeal.

## §2 — Where implemented, parameters must be explicit and versioned

Wherever any of the above (or any other parameter of the same kind) is implemented, its derivation must
declare explicit, versioned derivation parameters. No such value may exist as an unnamed, unversioned
constant embedded in code or configuration.

## §3 — DARWIN tests parameter value and generalisation

Naming and versioning a parameter is not the same as validating it. Whether a given parameter value is
useful, and whether it generalises beyond the specific conditions it was chosen under, is not decided by
HERMES or by this document:

> DARWIN later tests parameter value and generalisation.

## §4 — Non-authorisation

This document states doctrine that governs every HMT-3 item's parameters. It does not itself authorise
any implementation. See [`hmt3-roadmap-overview.md`](hmt3-roadmap-overview.md).
