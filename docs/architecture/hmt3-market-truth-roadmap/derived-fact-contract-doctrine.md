# HMT-3 — Derived Fact Contract Doctrine

**Initiative:** `HMT-3 — Post-HMT-2 HERMES Market Truth Roadmap`
**Status:** DOCUMENTATION / GOVERNANCE RECORD ONLY. Governs
[HMT-3A](hmt3a-derived-fact-spine-and-executed-flow.md) and, through it, every later HMT-3 item. See
[`hmt3-roadmap-overview.md`](hmt3-roadmap-overview.md) for the roadmap-wide non-authorisation statement.

## Purpose

State the governed common fact envelope that every `DerivedFact` produced anywhere in the HMT-3 roadmap
must conform to, and the temporal-integrity requirement that envelope must satisfy.

## §1 — HMT-3A must produce a governed common fact envelope

HMT-3A must turn the existing HMT-1 derived-fact identity skeleton into a governed common fact envelope
that every subsequent HMT-3 item's facts are expressed through. This is not a per-item choice — the
envelope is common, and it is HMT-3A's responsibility to establish it once, for reuse everywhere else in
this roadmap.

## §2 — Minimum fields

At minimum, the governed common fact envelope must carry:

- Deterministic fact ID.
- Fact type.
- Instrument identity.
- Actual-contract vs. continuous-series identity.
- Observation start/end.
- `as_of`.
- `available_at`.
- Derivation version.
- Complete parameter-set identity.
- Source-event/source-partition references.
- Quality state.
- Semantic identity/hash.
- Provenance to immutable evidence.

## §3 — The temporal model must resist future-information leakage structurally

> The temporal model must make future-information leakage structurally difficult.

This is a structural requirement on the envelope's design, not merely a discipline asked of whoever
authors a given fact's derivation. The `as_of` / `available_at` pairing in §2 exists specifically to make
this enforceable rather than aspirational.

## §4 — A retrospective outcome can never masquerade as an available-at-start fact

> A retrospective outcome can never masquerade as a fact available at the beginning of its measurement
> horizon.

Any fact whose value depends on how a horizon ultimately resolved must carry an `available_at` that
reflects the true point at which that resolution became knowable — never the start of the horizon it
describes. This is the same principle HMT-3B §4 applies specifically to contemporaneous-vs-ex-post
flow-response measurement; here it is stated as the general envelope-level rule that HMT-3B's application
is an instance of.

## §5 — Non-authorisation

This document states doctrine that governs HMT-3A's design. It does not itself authorise any
implementation. See [`hmt3-roadmap-overview.md`](hmt3-roadmap-overview.md).
