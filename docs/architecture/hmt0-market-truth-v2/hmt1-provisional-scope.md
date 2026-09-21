# HMT-0 — HMT-1 provisional scope

**Initiative:** `HMT-0 — Market Truth v2 Architecture Closure`
**Status:** DOCUMENTATION / ARCHITECTURE ONLY. See [`hmt0-closure-report.md`](hmt0-closure-report.md).

## Purpose

This document names and scopes the next programme step, `HMT-1`, at the description level only — no
implementation detail beyond what is needed to bound its scope. Its status is stated plainly and is not
softened anywhere in this document:

> **HMT-1 — CANONICAL MARKET EVENT & REPLAY FOUNDATION**
> **APPROVED IN PRINCIPLE / NOT AUTHORISED.**

Implementation requires a separate, explicit authorisation. Nothing in this document, or in any other
document in this HMT-0 pack, constitutes that authorisation.

## §1 — What HMT-1 would cover (scope description only)

Based on the architecture established across this pack, HMT-1 would be the first implementation
work order to actually build:

- The provider adapter for the eventually-chosen GC data source (per `provider-abstraction.md`), behind
  the canonical-event boundary.
- The canonical `MarketQuoteEvent` / `MarketTradeEvent` / `TopOfBookEvent` shapes (per
  `canonical-market-events.md`) as real, concrete schema/code, built against the now-ruled GC P0 native
  source corpus — **MBP-1** (per `canonical-market-events.md` §3): `TopOfBookEvent` means a genuine
  top-of-book state transition, which MBP-1 preserves and TBBO does not; empirical HMT-0 measurement
  (`gc-data-volume-and-cost-study.md`) found MBP-1 storage/cost manageable; MBO remains out of scope. This
  decision now being resolved does not itself authorise this work — see §3 below.
- The GC actual-contract-identity representation (per `gc-futures-identity-and-roll.md`) — at minimum the
  raw/canonical identity discipline; continuous-series/roll derivation may be a later, separate lane
  within or after HMT-1.
- The deterministic-replay foundation (per `deterministic-replay-and-evidence.md`) sufficient to prove the
  first canonical events are genuinely reproducible — this is explicitly named in HMT-1's own title
  ("& Replay Foundation") because replay-grade discipline from day one is cheaper than retrofitting it
  after facts already exist without it.

## §2 — What HMT-1 explicitly would NOT cover (even once authorised)

- **No P0 microstructure derived facts yet.** Computing volume-at-price, CVD, absorption, etc. (per
  `p0-microstructure-requirements-matrix.md`) is downstream of having a proven canonical event foundation
  — HMT-1 is the foundation, not the fact computation layer. That would be a later, separately-numbered
  work order.
- **No market research store or evidence vault build-out** beyond whatever minimum is needed to prove
  replay (§1) — the full three-tier storage architecture (`data-lifecycle-and-storage.md`) is a larger
  build than HMT-1's foundation scope.
- **No permanent native-corpus acquisition under HMT-1 itself.** HERMES's engineering capability for
  native-corpus acquisition/retention is not licensing-gated — per Central Architecture's ruling that
  licensing/commercial correspondence is external programme administration, not an engineering gate (see
  `licensing-and-security.md`). What HMT-1 authorisation does **not** itself satisfy is *implementation
  authorisation* — HMT-1 remains **NOT AUTHORISED** regardless of this licensing de-gating. These are two
  completely separate and unrelated gates: correcting the licensing framing does not authorise HMT-1, and
  does not authorise any native-corpus acquisition under it.
- **No DARWIN or ATHENA work of any kind.** DARWIN remains frozen (per this pack's own binding
  authority-and-scope constraints) and is explicitly out of scope for this entire pack, including for
  HMT-1's eventual scope. HMT-1 shares no implementation, table, or code path with anything DARWIN-named.
  This is stated here explicitly, as required, as a **non-goal** — HMT-1 does not authorise, imply, or
  create a pathway toward any DARWIN/ATHENA capability.

## §3 — Authorisation gate

HMT-1 implementation begins only when a separate, explicit authorisation is issued by whoever holds that
authority for this programme (outside the scope of this documentation-only pack to determine or name).
This document's existence — describing HMT-1's scope at the description level — is itself part of the
architecture-closure deliverable this pack was commissioned to produce; it is not, and must never be read
as, that authorisation.

## §4 — What this document does not do

A research-acquisition-sequencing roadmap for the eventual native GC corpus now exists as a separate
document, `hmt2-provisional-acquisition-roadmap.md` (RECORDED / NOT AUTHORISED) — HMT-1's own scope and
authorisation status in this document are unaffected by, and unrelated to, that roadmap's existence.

That document's §11 ("HMT-1 boundary") also states, from HMT-1's own side, the fixture doctrine that
governs HMT-1's data use: HMT-1, if and when separately authorised, uses synthetic governed fixtures by
default. A minimal real fixture is an exceptional, narrowly-scoped case only — it requires explicit prior
approval from **Central PO / Chief Architect** (the only approval authority for this exception; no
implicit approval, and no self-authorisation of it by any other party), it is validation evidence for
proving the HMT-1 replay chain only, and it can never become HMT-2 research-corpus acquisition or
authorise broader historical acquisition. See `hmt2-provisional-acquisition-roadmap.md` §11 for the full
doctrine and its seven governing conditions. This is a clarification of the fixture mechanism only — it
does not expand HMT-1's scope as described in §1 above.

Does not write a single line of implementation-relevant detail (no schema DDL, no code, no config). Does
not commit to a start date, an assignee, or a work-order number for HMT-1. Does not resolve any remaining
`PENDING_EVIDENCE`-class markers elsewhere in this pack (see `hmt0-closure-report.md`'s collected-markers
list) — HMT-1 authorisation and resolving those markers are related but distinct gates, and clearing one
does not imply the other has been cleared. Licensing/commercial correspondence is no longer one of these
markers at all, per Central Architecture's ruling that it is external, non-gating programme administration
(`licensing-and-security.md`) — but that de-gating is itself separate from, and does not touch, HMT-1's
own authorisation gate in §3 above. (The GC P0 native-corpus choice — MBP-1, per
`canonical-market-events.md` §3 — is resolved and is no longer one of these pending markers either, but
that resolution is itself a separate matter from HMT-1's own authorisation gate, addressed in §3 above.)
