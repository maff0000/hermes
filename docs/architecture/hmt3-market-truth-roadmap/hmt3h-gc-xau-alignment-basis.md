# HMT-3H — GC ↔ XAU Alignment / Basis

**Initiative:** `HMT-3 — Post-HMT-2 HERMES Market Truth Roadmap`
**Status:** **NOT YET AUTHORISED FOR IMPLEMENTATION.** See
[`hmt3-roadmap-overview.md`](hmt3-roadmap-overview.md).

## Purpose

Name the GC↔XAU alignment/basis fact family, state the identity-separation rule that governs it, and
record what belongs to DARWIN rather than to this item.

## §1 — What must be preserved separately

- The native GC contract and price (per the actual-contract identity discipline established elsewhere in
  HERMES's market-truth architecture).
- The native XAUUSD executable/reference quote.
- Source timestamps/precision for both.
- Contemporaneous alignment between the two.
- An explicitly-derived GC/XAU basis.

## §2 — GC price levels must never be projected directly onto XAUUSD

> GC price levels must never be projected directly onto XAUUSD as though the two instruments have
> identical prices.

GC (COMEX gold futures) and XAUUSD (spot/executable gold) are related but distinct instruments with
distinct pricing. Any relationship between the two that this item's facts expose must be represented
through the explicitly-derived basis in §1 — never by silently treating a GC level as if it were, or could
stand in for, an XAUUSD level.

## §3 — What belongs to DARWIN, not to this item

This item's job is to preserve the native facts and the derived basis honestly. It is DARWIN's job, later
and separately, to test:

- Lead/lag behaviour between GC and XAU.
- Basis behaviour more generally.
- Horizon/regime dependence of that behaviour.
- The usefulness of translating GC-derived structure into an executable XAUUSD context.

None of these DARWIN-side questions are answered, assumed, or pre-judged by this item's fact production.

## §4 — Non-authorisation

This document describes the intended shape and constraints of HMT-3H. It does not authorise beginning any
implementation of it. See [`hmt3-roadmap-overview.md`](hmt3-roadmap-overview.md) for the programme-wide
non-authorisation statement.
