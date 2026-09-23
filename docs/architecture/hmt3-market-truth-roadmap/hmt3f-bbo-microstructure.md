# HMT-3F — BBO Microstructure

**Initiative:** `HMT-3 — Post-HMT-2 HERMES Market Truth Roadmap`
**Status:** **NOT YET AUTHORISED FOR IMPLEMENTATION.** See
[`hmt3-roadmap-overview.md`](hmt3-roadmap-overview.md).

## Purpose

Name the BBO-microstructure fact family built on governed genuine BBO state, and state the honesty
boundary this item must observe given MBP-1's actual information content.

## §1 — Fact family

- Bid/ask.
- Displayed size.
- Spread.
- Displayed-book imbalance.
- Midpoint.
- Microprice.
- Microprice-midpoint displacement.
- Versioned OFI.

All of the above are computed using governed genuine BBO state — this item does not invent a substitute
BBO representation; it consumes the BBO state that is already governed elsewhere in HERMES's market-truth
architecture.

## §2 — No implied individual queue position from MBP-1

This item must not imply individual queue position from MBP-1. MBP-1 (best-bid/best-offer market-by-price)
data does not carry order-level queue information, and no fact produced by this item may present a
measurement as if it did.

## §3 — Any OFI formulation must be explicitly versioned

Order-flow imbalance ("OFI") has more than one published formulation. Any OFI formulation implemented by
this item must have an explicit derivation version and formula recorded against it — "OFI" alone, with no
attached version/formula, is not a permitted fact identity.

## §4 — Non-authorisation

This document describes the intended shape and constraints of HMT-3F. It does not authorise beginning any
implementation of it. See [`hmt3-roadmap-overview.md`](hmt3-roadmap-overview.md) for the programme-wide
non-authorisation statement.
