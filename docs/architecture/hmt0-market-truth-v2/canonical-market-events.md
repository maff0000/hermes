# HMT-0 — Canonical market events

**Initiative:** `HMT-0 — Market Truth v2 Architecture Closure`
**Status:** DOCUMENTATION / ARCHITECTURE ONLY. See [`hmt0-closure-report.md`](hmt0-closure-report.md).

## Purpose

This document defines the P0 canonical event model that all derived facts in this programme are built
from: `MarketQuoteEvent`, `MarketTradeEvent`, `TopOfBookEvent`. These are provider-independent event
shapes (see `provider-abstraction.md` for the general boundary principle) — the wire format of whichever
provider/feed level is ultimately chosen is translated into these shapes; canonical logic never reads a
provider's native wire format directly.

## §1 — The three event types

### `MarketQuoteEvent`

A single observed quote (bid and/or ask) from a source, at a point in time, carrying that source's
classification (see `source-taxonomy.md`). This is the event shape OANDA and Vantage quotes map onto.
It does **not** assert a continuous top-of-book state — it is one observation.

### `MarketTradeEvent`

A single observed trade print: price, size, timestamp, and (where the source genuinely supports it —
see the requirements matrix) an aggressor-side classification carrying its own confidence/derivation
provenance (never fabricated where the source cannot support it).

### `TopOfBookEvent`

**Critical, non-negotiable distinction — the reason this document exists as its own document:**

> `TopOfBookEvent` means a genuine top-of-book *state transition* — every change to the best bid/ask
> price, size, or order count, in order, with no gaps.

This is a **continuous stream** semantic. A consumer of `TopOfBookEvent` must be able to reconstruct the
top-of-book state at any point in time between two events by simply holding the last event's state.

## §2 — Why TBBO does NOT satisfy `TopOfBookEvent` on its own

This is the single most important ruling in this document, and it is stated exactly as handed down by
Central Architecture review — it is not reinterpreted or softened here:

- **TBBO** provides "trade + BBO immediately before trade" **only**. It is a trade-anchored sampling of
  the book, not a continuous book stream. Between two trades, TBBO tells you nothing about whether the
  BBO changed, by how much, or how many times. A consumer trying to build `TopOfBookEvent` semantics from
  TBBO alone would silently miss every quote-only BBO change that happened without an accompanying trade
  — which, on a liquid futures book, is the majority of BBO changes.
- **MBP-1** provides "every top-level book update, including trade and BBO price/quantity/order-count
  changes." This is the level that genuinely satisfies the `TopOfBookEvent` continuous-state-transition
  definition above.

Consequently: **`MarketTradeEvent` can be built correctly from either TBBO or MBP-1. `TopOfBookEvent`
can only be built correctly from MBP-1** (or a stronger level; MBO is out of scope here per this pack's
binding rulings). A P0 native corpus choice of TBBO-only would mean HERMES cannot honestly produce a
`TopOfBookEvent` stream — that would need to be stated plainly wherever it matters, never quietly
papered over by degrading the event's semantics to match the weaker feed.

## §3 — The P0 native corpus decision is deferred, not resolved here

The final choice between acquiring TBBO or MBP-1 as HERMES's permanent P0 native corpus is:

> **`PENDING_EMPIRICAL_TBBO_VS_MBP1_MEASUREMENT`**

This is deliberately left unresolved by this document and by this entire pack. It depends on real
Databento metadata (volume, cost, and genuine feed-content verification) that Helm is expected to return
in a later, separately-scoped measurement exercise (see `gc-data-volume-and-cost-study.md`). This document
does not guess a plausible-sounding outcome, does not lean toward either choice, and does not introduce
MBO as a hedge against the ambiguity. Until that measurement lands:

- The canonical event model (this document) is defined at the semantic level (what a `TopOfBookEvent`
  *means*) independent of which feed eventually supplies it.
- Any HMT-1 implementation work (see `hmt1-provisional-scope.md`) that would require this decision to be
  made is blocked on it, explicitly, not silently assumed to default to one side.
- If the empirical measurement shows MBP-1's incremental cost/volume over TBBO is prohibitive, the correct
  next step is a **new, explicit architecture ruling** — documented here or in a superseding document —
  not a silent runtime fallback to TBBO while still claiming `TopOfBookEvent` semantics.

## §4 — Relationship to the requirements matrix

`p0-microstructure-requirements-matrix.md` already establishes, independent of this deferred choice, which
fact classes need TBBO-level aggressor information (delta, CVD, aggression clusters, flow efficiency) and
which need genuine MBP-1 continuity (absorption, failed aggression, balance/imbalance, squeeze proxies).
This document's `TopOfBookEvent` ruling is consistent with that matrix: everything in the matrix that
required MBP-1 for correctness is exactly the set of facts that depend on genuine `TopOfBookEvent`
semantics; everything the matrix could satisfy from Trades/TBBO alone maps onto `MarketTradeEvent` or
`MarketQuoteEvent` only, and never needs `TopOfBookEvent` at all.

## §5 — What this document explicitly does not do

- Does not introduce `MarketBookEvent`, `MarketOrderEvent`, or any MBO-level event shape. Out of scope for
  this pack, per its binding rulings.
- Does not specify wire-level schema, serialisation, storage table shape, or versioning mechanics for
  these three event types — that belongs to `derived-fact-taxonomy-and-ownership.md` (for facts derived
  from these events) and to HMT-1 implementation (if and when separately authorised), never to this
  architecture-only document.
- Does not apply these event shapes to OANDA or Vantage retroactively — the existing OANDA
  tick/candle pipeline is unchanged by this pack (see `current-state-reconciliation.md`). This event model
  is scoped to the new GC programme; whether/how it is later generalised to other sources is a separate,
  future decision.
