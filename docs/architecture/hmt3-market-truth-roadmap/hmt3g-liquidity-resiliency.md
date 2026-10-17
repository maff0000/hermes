# HMT-3G — Liquidity Resiliency

**Initiative:** `HMT-3 — Post-HMT-2 HERMES Market Truth Roadmap`
**Status:** **NOT YET AUTHORISED FOR IMPLEMENTATION.** See
[`hmt3-roadmap-overview.md`](hmt3-roadmap-overview.md).

## Purpose

Name the liquidity-resiliency fact family and state, without qualification, the honesty limits this item
must observe given that its native corpus is MBP-1, not order-level (MBO/L3) data.

## §1 — Fact family

- Visible top-of-book liquidity removed.
- Visible liquidity restored.
- Replenishment time.
- Repeated execution at a price.
- BBO depletion.
- Spread response.
- Quote-update intensity.
- Price progress during aggressive execution.

All of the above must be produced **within honest MBP-1 limits** — i.e. only measurements that MBP-1 data
genuinely supports.

## §2 — No claimed MBO/order-level cancellation or queue-position knowledge from MBP-1

This item must not claim MBO/order-level cancellation knowledge, or queue-position knowledge, derived from
MBP-1. MBP-1 shows the visible top-of-book state; it does not show individual order cancellations or where
in the queue any given order sits, and no fact in this family may be represented as though it does.

## §3 — MBO remains a future evidence-gated acquisition decision

MBO (market-by-order / L3) is not part of this item's scope, and its acquisition is not decided by this
document. See [`deferred-acquisition-families.md`](deferred-acquisition-families.md) — MBO/L3 remains a
future evidence-gated acquisition decision, to be justified by evidence, not adopted because it is
interesting or because it would make this item's facts more precise.

## §4 — Non-authorisation

This document describes the intended shape and constraints of HMT-3G. It does not authorise beginning any
implementation of it. See [`hmt3-roadmap-overview.md`](hmt3-roadmap-overview.md) for the programme-wide
non-authorisation statement.
