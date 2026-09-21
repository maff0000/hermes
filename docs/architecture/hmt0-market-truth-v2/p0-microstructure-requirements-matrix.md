# HMT-0 — P0 microstructure requirements matrix

**Initiative:** `HMT-0 — Market Truth v2 Architecture Closure`
**Status:** DOCUMENTATION / ARCHITECTURE ONLY. See [`hmt0-closure-report.md`](hmt0-closure-report.md).

## Purpose

For every P0 microstructure fact class this programme cares about, this document states the **minimum
Databento feed level** genuinely required to compute it correctly, across four candidate levels: Trades,
TBBO, MBP-1, MBO. "Minimum" means the lowest level that produces a *correct* fact — not the lowest level
that produces something plausible-looking. Where a fact genuinely cannot be computed correctly from a
given level, this document says so plainly. The P0 native corpus level choice (TBBO vs MBP-1), previously
deferred here, is now resolved — Central Architecture has ruled the native corpus is **MBP-1** (see
`canonical-market-events.md` §3 and `gc-data-volume-and-cost-study.md` for the empirical basis). This
document's own fact-class-to-minimum-level mapping below is unaffected by that ruling — it was written to
hold regardless of which level was ultimately chosen; only the two "final choice" pointers below resolve.
This document introduces no MBO dependency anywhere (per this pack's binding scope: MBO is out of scope
for HMT-0/HMT-1 requirements).

## Feed-level primer (as referenced throughout this matrix)

- **Trades** — the raw trade print tape only: price, size, timestamp, trade-side flags where the venue
  publishes them. No book state.
- **TBBO** — trade prints, each one paired with the top-of-book bid/ask **immediately before that trade**.
  Provides trade + surrounding-quote context, but only at trade moments — it is not a continuous
  top-of-book stream (see `canonical-market-events.md` for why this matters to the event model).
  Databento's TBBO does carry a trade-side condition indicator; whether that indicator reaches full
  aggressor-side genuineness at the tick level was never itself directly measured by the empirical study
  that resolved the P0 native-corpus choice (that study measured volume/cost, not per-tick indicator
  genuineness) — this specific sub-question is now moot for the native-corpus decision, since the ruled
  corpus is MBP-1, not TBBO (see `canonical-market-events.md` §3), but it remains an honest, standalone
  gap in this document's own knowledge about TBBO specifically, not something this update resolves or
  claims to resolve.
- **MBP-1** — every top-of-book state change: every trade AND every quote update, with a resulting
  BBO price/quantity/order-count snapshot after each event. A continuous top-of-book stream.
- **MBO** — full order-by-order book (every add/modify/cancel at every price level, individually
  identified orders). Not required by, and not introduced into, any P0 fact class below.

## The matrix

For each fact class: minimum genuinely-correct level, and the honest caveat for any level below that.

| Fact class | Trades | TBBO | MBP-1 | MBO | Minimum correct level |
|---|---|---|---|---|---|
| **Volume-at-price** | Correct — trade price+size is exactly what this needs | Same as Trades (BBO context adds nothing here) | Same | Same | **Trades** |
| **POC (point of control)** | Correct — derived directly from volume-at-price | Same | Same | Same | **Trades** |
| **VAH / VAL (value area high/low)** | Correct — derived from the volume-at-price distribution | Same | Same | Same | **Trades** |
| **LVN / HVN (low/high volume nodes)** | Correct — same distribution, different read | Same | Same | Same | **Trades** |
| **Aggressor volume** (which side initiated) | **Cannot be determined correctly** — Trades alone has no reliable BBO-relative side classification; a price-only heuristic (uptick/downtick) is an approximation, not a genuine aggressor determination, and this matrix will not describe it as exact | Correct at trade moments — trade price vs. the immediately-preceding BBO gives a genuine Lee–Ready-style classification | Correct and continuous — same classification, available at every top-of-book update, not just trade moments | Correct, strongest — genuine order-level aggressor identity | **TBBO** (MBP-1 strictly better; ruled P0 native corpus is **MBP-1** — see `canonical-market-events.md` §3 — so the acquired corpus already exceeds this fact class's own minimum) |
| **Delta** (buy volume − sell volume) | **Cannot be determined correctly** — depends entirely on aggressor volume above | Correct | Correct, continuous | Correct | **TBBO** |
| **CVD (cumulative volume delta)** | **Cannot be determined correctly** | Correct | Correct, continuous | Correct | **TBBO** |
| **Trade-size distributions** | Correct — size is a native trade-print field | Same (no additional need) | Same | Same | **Trades** |
| **Aggression clusters** (bursts of same-side aggressive trades) | **Cannot be determined correctly** — needs aggressor side | Correct at trade granularity | Correct, continuous | Correct | **TBBO** |
| **Flow efficiency** (price move achieved per unit of aggressive volume) | **Cannot be determined correctly** — needs delta | Correct | Correct, continuous | Correct | **TBBO** |
| **Absorption** (aggressive volume absorbed without proportional price move) | **Cannot be determined correctly** — needs delta + a stable BBO reference | Partially correct — trade-moment BBO context supports a reasonable absorption read, but a BBO that only updates at trade moments cannot distinguish "price held because of absorption" from "price held because no quote update happened to occur between trades"; this is a genuine, honestly-stated limitation, not something this matrix will describe as fully correct | Correct — continuous BBO means a genuine "aggressive volume vs. book response" read | Correct, strongest | **MBP-1** for a fully honest absorption read; TBBO produces a materially weaker approximation that must be labelled as such wherever it is used |
| **Failed aggression** (aggressive push that reverses without holding) | Same limitation as absorption — needs a genuine, continuous BBO reaction, not just trade-moment context | Weak/approximate, same reasoning as absorption | Correct | Correct | **MBP-1** |
| **Balance / imbalance** (book-side pressure) | **Cannot be determined at all** — Trades has no quote-size information whatsoever | **Cannot be determined at all** — TBBO's BBO snapshot is only present at trade moments, and is one-sided context (pre-trade), not a genuine continuously-updated imbalance read | Correct — MBP-1 carries top-of-book size on both sides at every update | Correct, strongest (full depth) | **MBP-1** |
| **Acceptance / rejection** (price level held/rejected on retest) | Correct only for the price-action definition (does price return, does it hold) — this reading needs no order-flow input, only OHLC/candle-level price structure, and is already representable from existing candle data | N/A (no additional need) | N/A | N/A | **Trades / existing candle data** — this fact class does not require microstructure input at all; flagged here so it is not mistakenly gated behind the TBBO/MBP-1 decision |
| **Failed auctions** (auction-style rotation that fails to hold value area) | Needs volume-at-price (available from Trades) plus a genuine acceptance/rejection read (price-level, available from candles) — the *volume* component is correct from Trades; do not conflate with the order-flow-dependent facts above | Same | Same | Same | **Trades** (this fact class, specifically, does not need aggressor/delta — it is a value-area + price-structure fact, not an order-flow fact; documented explicitly here to prevent it being over-gated behind TBBO/MBP-1 by a future implementer) |
| **Squeeze / trapped-flow proxies** | **Cannot be determined correctly** — by definition this fact class is about aggressive flow failing to achieve its expected price outcome, which needs delta + a genuine BBO reaction read | Approximate, same absorption-class caveat above | Correct | Correct | **MBP-1** for a genuine read; any TBBO-based version is explicitly a **proxy**, and must be labelled `PROXY`, never `EXACT`, in any derived-fact record (see `derived-fact-taxonomy-and-ownership.md`) |

## Summary — minimum level by fact family

- **Price/volume-distribution facts** (volume-at-price, POC, VAH/VAL, LVN/HVN, trade-size distributions,
  failed auctions' volume component) — **Trades** is sufficient and correct. No microstructure escalation
  needed.
- **Aggressor-dependent flow facts** (aggressor volume, delta, CVD, aggression clusters, flow efficiency) —
  **TBBO** is the minimum correct level; MBP-1 is strictly better (continuous rather than trade-moment-only)
  but not required for correctness of these specific facts.
- **Book-reaction facts** (absorption, failed aggression, balance/imbalance, squeeze/trapped-flow) —
  **MBP-1** is the minimum genuinely correct level. Any TBBO-based version of these facts is an honestly
  weaker approximation and must be labelled as such, never presented as equivalent.
- **Pure price-structure facts** (acceptance/rejection) — need no microstructure feed at all; already
  representable from existing candle data.

## Explicit non-overclaim discipline

This matrix exists specifically to prevent a future implementer from quietly computing a "Delta" or
"CVD" field from Trades-only data (which cannot be done correctly) and shipping it as if it were the real
thing. Any derived fact whose correct computation requires a feed level HERMES has not yet acquired must
be either (a) not computed at all, or (b) computed as an explicitly labelled `PROXY`/`APPROXIMATION` with
its limitation stated in its own metadata (see `derived-fact-taxonomy-and-ownership.md`) — never silently
presented as the exact fact.

## Resolved item

The final choice of P0 native corpus level (TBBO vs. MBP-1) is **resolved**: Central Architecture has
ruled the native corpus is **MBP-1**, on the empirical basis measured in `gc-data-volume-and-cost-study.md`
— see `canonical-market-events.md` §3 for the ruling and its rationale. As anticipated when this matrix
was first written, the fact-class-to-minimum-level mapping above did not need to be restructured by this
resolution — only the "final choice" pointer resolved. Note this does not authorise HMT-1 implementation
(still `NOT AUTHORISED`, see `hmt1-provisional-scope.md`) and does not clear the separate licensing gate
on permanent retention of the corpus (`licensing-and-security.md` §2, still open).
