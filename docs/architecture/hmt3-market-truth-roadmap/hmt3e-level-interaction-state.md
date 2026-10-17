# HMT-3E — Level Interaction State

**Initiative:** `HMT-3 — Post-HMT-2 HERMES Market Truth Roadmap`
**Status:** **NOT YET AUTHORISED FOR IMPLEMENTATION.** See
[`hmt3-roadmap-overview.md`](hmt3-roadmap-overview.md).

## Purpose

Name the level-interaction-state fact family — how price behaves around a previously-established level
over time — and state the assumptions this item must not bake in, and the narrow conditions under which
any classification built on top of it may exist.

## §1 — Fact family

- Formation.
- Departure.
- Age / time since formation.
- Closest approach.
- Touch ordinal.
- Penetration.
- Time away.
- Bars away.
- Interaction history.
- Excursions and reactions.

## §2 — Assumptions this item must not bake in

This item must not bake in any of the following as if they were objective truth:

- That a first touch is superior to subsequent touches.
- That a near miss automatically consumes a level.
- That a level is mature after an arbitrary fixed period.

## §3 — Derived classifications (e.g. FRESH/MATURE/CONSUMED)

Classifications such as `FRESH`, `MATURE`, or `CONSUMED` may exist in this item's output only as
explicitly parameterised and versioned derived classifications computed over the objective measurements in
§1 — never as an unqualified state asserted directly. Whether such a classification, once produced this
way, actually carries value is not something this item decides:

> DARWIN determines whether such classifications have value.

## §4 — Non-authorisation

This document describes the intended shape and constraints of HMT-3E. It does not authorise beginning any
implementation of it. See [`hmt3-roadmap-overview.md`](hmt3-roadmap-overview.md) for the programme-wide
non-authorisation statement.
