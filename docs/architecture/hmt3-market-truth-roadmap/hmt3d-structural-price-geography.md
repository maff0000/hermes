# HMT-3D — Structural Price Geography

**Initiative:** `HMT-3 — Post-HMT-2 HERMES Market Truth Roadmap`
**Status:** **NOT YET AUTHORISED FOR IMPLEMENTATION.** See
[`hmt3-roadmap-overview.md`](hmt3-roadmap-overview.md).

## Purpose

Name the structural price-geography fact family (prior-period extrema through reaction zones and
structural clearance) and the identity-separation and versioning requirements that govern it.

## §1 — Fact family

- Prior day/week/month/year highs/lows.
- Developing period extrema.
- Session extrema.
- Governed swing extrema.
- ATH semantics, where legitimate.

## §2 — `ACTUAL_CONTRACT` vs. `CONTINUOUS_DERIVED` must never be silently combined

Every level in this item's fact family must distinguish, explicitly, whether it is an `ACTUAL_CONTRACT`
level or a `CONTINUOUS_DERIVED` level. These two identities must **never be silently combined** — a level
computed from the actual traded contract's own history and a level computed from a continuous derived
series are different facts with different provenance, and a consumer must always be able to tell which
one it is looking at.

## §3 — Reaction zones

Reaction-zone detection in this item must be deterministic and versioned. This item's facts also include
reaction density and structural-clearance measurements built on top of detected reaction zones.

> A reaction zone is never an unqualified truth object.

Its detector version, its parameters, and its constituent observations must all be preserved alongside the
zone itself — a reaction zone fact is only ever meaningful together with the record of exactly how it was
detected and from what.

## §4 — Structural clearance is always measured against an identified, versioned structural set

Any structural-clearance measurement produced by this item must be measured against an identified and
versioned structural set — never against an implicit or undeclared notion of "the nearby structure." The
structural set being measured against is itself part of the fact's identity.

## §5 — Non-authorisation

This document describes the intended shape and constraints of HMT-3D. It does not authorise beginning any
implementation of it. See [`hmt3-roadmap-overview.md`](hmt3-roadmap-overview.md) for the programme-wide
non-authorisation statement.
