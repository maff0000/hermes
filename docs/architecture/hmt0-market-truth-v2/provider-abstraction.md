# HMT-0 — Provider abstraction

**Initiative:** `HMT-0 — Market Truth v2 Architecture Closure`
**Status:** DOCUMENTATION / ARCHITECTURE ONLY. See [`hmt0-closure-report.md`](hmt0-closure-report.md).

## Purpose

This document defines the boundary between "a specific data provider's wire format/API" and HERMES's own
canonical internal representation, so that a future provider swap (e.g. moving off Databento, or adding a
second GC data provider alongside it) does not require rewriting canonical semantics.

## §1 — The boundary, stated as a rule

> Canonical logic (the event model in `canonical-market-events.md`, the derived-fact contract in
> `derived-fact-taxonomy-and-ownership.md`, the storage tiers in `data-lifecycle-and-storage.md`) never
> reads a provider's native wire format directly. A thin, provider-specific adapter translates that
> provider's format into the canonical `MarketQuoteEvent` / `MarketTradeEvent` / `TopOfBookEvent` shapes,
> and everything downstream of that adapter is provider-agnostic.

## §2 — Existing HERMES precedent for this boundary

HERMES already has a real, existing precedent for exactly this shape of boundary: the OANDA integration
lives behind `adapters/base.py` (confirmed present in the repository), a single, provider-specific
adapter that the rest of HERMES's candle/indicator logic does not need to know the details of OANDA's own
v20 wire protocol to consume. This document generalises that same existing architectural pattern to the
GC programme's provider boundary — it does not invent a new pattern, it names and extends an existing
one.

The key generalisation this document adds over the existing single-provider precedent: the existing
OANDA adapter has never needed to prove it is swappable, because HERMES has only ever had one price
provider. The GC programme's provider-abstraction boundary must be designed **assuming** a provider swap
or a second concurrent provider is a real, foreseeable event (not a hypothetical) — a provider change
(commercial terms shifting, a better-suited vendor emerging, a service being discontinued) is a normal
risk for any single-vendor market-data dependency, and an architecture that could not tolerate that
decision changing later would be a genuine risk on that basis alone.

## §3 — What crosses the boundary, and what does not

**Crosses the boundary (provider-specific, lives in the adapter):**
- Wire protocol/format details (Databento's specific schema encoding, connection/auth mechanics, its own
  internal sequence-number domain before translation).
- Provider-specific error/retry/reconnect behaviour.
- Provider-specific rate limits and acquisition mechanics.

**Does not cross the boundary (canonical, provider-agnostic):**
- The `MarketQuoteEvent`/`MarketTradeEvent`/`TopOfBookEvent` shapes themselves.
- Source classification (`source-taxonomy.md`) — a canonical event's source classification
  (`CENTRALISED_EXCHANGE_FUTURES` for COMEX GC, etc.) is a property of *what the source is*, not of which
  specific provider's API happened to deliver it. Two different providers both delivering genuine COMEX
  GC MBP-1 data would produce events with the same source classification.
- Derived-fact identity fields (`derived-fact-taxonomy-and-ownership.md` §2) — an algorithm computing CVD
  from canonical `MarketTradeEvent`s does not need to know or care which provider adapter produced them.
- The GC actual-contract-identity discipline (`gc-futures-identity-and-roll.md`) — contract identity is a
  market fact, not a provider artifact, and must be represented identically regardless of which provider
  delivered the underlying event (subject to the adapter correctly translating that provider's own
  contract-identifier convention into HERMES's canonical one — a translation responsibility that itself
  lives inside the adapter, per §1).

## §4 — Provenance still crosses the boundary, honestly

The "sequence source/domain" and "provider receive/capture time" fields required by
`time-order-sequence-model.md` §1 are provider-specific facts that legitimately need to be recorded on the
canonical event — the abstraction boundary in this document is about *logic*, not about *erasing
provenance*. The adapter's job is to translate the provider's native representation of these fields into
the canonical provenance fields already defined there, tagged with which provider/domain they came from
— never to strip that information for the sake of a cleaner-looking canonical shape.

## §5 — What this document does not do

Does not specify the Databento adapter's actual code structure — no `.py` file is touched by this pack,
and no adapter code exists yet for this programme (confirmed: zero Databento references in the repo).
Does not commit to supporting a second concurrent GC provider — it only requires that the architecture
not make doing so structurally impossible later.
