# HMT-3 — Terminology Doctrine

**Initiative:** `HMT-3 — Post-HMT-2 HERMES Market Truth Roadmap`
**Status:** DOCUMENTATION / GOVERNANCE RECORD ONLY. Governs every HMT-3 item. See
[`hmt3-roadmap-overview.md`](hmt3-roadmap-overview.md) for the roadmap-wide non-authorisation statement.

## Purpose

Name, in full, the vocabulary that must never appear as an unqualified HERMES fact, and state what HERMES
may publish instead.

## §1 — Forbidden as unqualified fact

The following terms/concepts must never appear as an unqualified HERMES fact:

- Trapped traders.
- Smart money.
- Institutional buying/selling.
- Manipulation.
- Absorption, presented as trader intent.
- `CONSUMED` / `FRESH` / `MATURE` (or equivalent), without a defined derivation.
- Support/resistance, without provenance/formation semantics.

This list is the general rule that specific per-item prohibitions elsewhere in this roadmap are instances
of — for example, HMT-3B §2's prohibition on publishing `TRAPPED_TRADER` as market truth, HMT-3E §3's
requirement that `FRESH`/`MATURE`/`CONSUMED` exist only as explicitly parameterised and versioned derived
classifications, and HMT-3I §3's prohibition on institutional/"smart money" gap semantics are all specific
applications of this same terminology doctrine, not separate rules.

## §2 — What HERMES may publish instead

> HERMES may publish measurements from which research investigates these interpretations.

The rule in §1 is not a prohibition on measuring the underlying phenomena — it is a prohibition on
asserting the interpretive label itself as fact. HERMES publishes the objective, versioned measurement
(e.g. displacement, penetration, a defined-derivation classification, a provenance-carrying level); it is
DARWIN's job, separately and later, to investigate whether an interpretation such as "smart money" or
"absorption" is supported by that measurement.

## §3 — Non-authorisation

This document states doctrine that governs every HMT-3 item's terminology. It does not itself authorise
any implementation. See [`hmt3-roadmap-overview.md`](hmt3-roadmap-overview.md).
