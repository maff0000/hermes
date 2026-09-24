# HERMES GC Live Market Data — Purpose and Terminology

**Initiative:** `HERMES GC Historical + Live Market-Data Doctrine`
**Status:** DOCUMENTATION / ARCHITECTURE ONLY. Records doctrine for a **future, NOT-YET-AUTHORISED**
implementation phase (`HMT-LIVE-1`, see [`07-hmt-live-1-future-phase-not-authorised.md`](07-hmt-live-1-future-phase-not-authorised.md)).
This pack does not authorise, start, or imply the start of any code, infrastructure, or subscription change.
It is a companion to, and builds on, `docs/architecture/hmt0-market-truth-v2/` (the HMT-0 Market Truth v2
Architecture Closure pack) and does not restate or override any ruling made there.

## §1 — Purpose

There is **one governed GC acquisition authority** and **one durable GC market-truth corpus**. Live
delivery and historical replay delivery are **separate delivery paths over the same underlying market
truth** — they are not, and must never become, two independently-sourced views of GC.

Concretely:

- There is exactly one point in the architecture that acquires GC market data from the vendor (Databento)
  for a given delivery mode (live, or historical backfill/PAYG). That acquisition point is the single
  authority for what HERMES treats as GC market truth.
- There is exactly one durable, governed corpus of native GC market-truth records that both the live path
  and the historical/replay path are ultimately reconciled against.
- HERMES PROD, HERMES DEV, and DARWIN-research each consume GC market truth through a governed delivery
  path (live canonical path for PROD; replay/canonical from the durable corpus for DEV and DARWIN-research)
  — never through an independent GC feed of their own.

**Non-negotiable statement:** there must never be independent, unreconciled GC feeds serving HERMES PROD,
HERMES DEV, and DARWIN-research. If a consumer needs GC market data, it is served from the one governed
acquisition authority and the one durable corpus, via the appropriate delivery path — not from a new,
separately-sourced connection to the vendor.

This purpose statement is the test every other section in this pack is written against: any design that
would create a second GC acquisition point, a second durable corpus, or a delivery path that bypasses the
governed corpus/canonical semantics fails this test and is out of scope for `HMT-LIVE-1` as currently
recorded.

## §2 — Terminology

**Never call the durable GC store a "signal bucket."** The durable store is a **GC MARKET-TRUTH CORPUS**
(equivalently, the **GC NATIVE MARKET-DATA ARCHIVE**). Use one of those two terms, consistently, throughout
this pack and any future implementation work derived from it.

The reason this distinction is binding, not stylistic: GC MBP-1 events are **market observations / facts**
— they are Databento's record of what actually happened at the top of book on CME Globex for the GC
contract. They are not, and must never be described or implemented as, **trading signals**. A "signal" in
this programme's vocabulary implies a derived, interpreted, actionable artifact (the kind of thing DARWIN
discovers or HELIOS evaluates against). GC MBP-1 records are upstream of that: they are the raw market
truth that signals, features, and strategies are eventually built from, several architectural layers away
from and downstream of this corpus (see [`08-downstream-architecture-chain-correction.md`](08-downstream-architecture-chain-correction.md)
for the full chain).

Conflating "market-truth corpus" with "signal bucket" would misrepresent what is stored here and invite
downstream consumers to treat raw market observations as if they were already interpreted trading signals.
This pack, and any implementation that follows it, must not do that.

| Term | Meaning | Do not use instead |
|---|---|---|
| GC MARKET-TRUTH CORPUS / GC NATIVE MARKET-DATA ARCHIVE | The durable, governed store of native GC MBP-1 market-observation records (historical + progressively-accumulated live) | "signal bucket", "signal store", "signal archive" |
| GC MBP-1 event | A single native market observation/fact at the top of book for GC, as delivered by Databento | "signal", "trading signal" |
| Live canonical path | The low-latency delivery path from the single acquisition authority to HERMES PROD | — |
| Replay/canonical path | The delivery path from the durable corpus to HERMES DEV / DARWIN-research | — |
