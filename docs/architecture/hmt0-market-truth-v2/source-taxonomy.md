# HMT-0 — Source taxonomy

**Initiative:** `HMT-0 — Market Truth v2 Architecture Closure`
**Status:** DOCUMENTATION / ARCHITECTURE ONLY. See [`hmt0-closure-report.md`](hmt0-closure-report.md).

## Purpose

This programme deliberately spans three market-data sources that must never be conflated, because they
carry fundamentally different market-truth semantics. Every downstream document in this pack (event
model, time/order model, derived-fact taxonomy, licensing) assumes this taxonomy and cites it by name.
Conflating any two of these sources — even informally, in a comment or a variable name — is a class of
error this document exists to prevent.

## §0 — Naming caution (flagged during repo reconciliation, not a scope violation)

While grounding this pack in the real repository (see `current-state-reconciliation.md` §3), an existing
static-scan test was found: `tests/test_stream_silent_stall_recovery.py::T11_NoForbiddenTokens` asserts
the literal token `"Vantage"` never appears in `main.py`, grouped with `MetaTrader5`, `import mt5`,
`order_send`, `place_order`, `dispatch_trade`, `Agent_Smith`. That test's evident purpose is guarding
HERMES's core file against execution-oriented, manual-trading-platform concepts leaking into the fact
spine — consistent with the governance PID's "no trading authority" mandate. This document's use of
"Vantage" is a market-data source **classification label only** — it never implies MT5, order routing, or
execution capability, and this pack introduces the word into zero `.py` files (this pack introduces zero
`.py` files, period). This is recorded here as a caution for whoever eventually authors HMT-1
implementation: choose source-identifier naming for this feed deliberately, aware that the literal string
"Vantage" already carries a guarded, execution-adjacent connotation elsewhere in this codebase's test
suite. This is not treated as a contradiction of this brief (documentation cannot violate a code-scan
test), but it is exactly the kind of ambiguity the governing brief for this pack asked to be surfaced
rather than silently resolved.

## §1 — The three sources

### 1.1 OANDA `XAU_USD` (existing HERMES integration)

- **Classification:** `RETAIL_CFD_STREAMING_QUOTE` (a retail CFD/spread-style broker streaming price feed;
  not an exchange, not a centralized order book).
- **Status:** existing, live, the entirety of HERMES's current market-data ingestion. See
  `docs/architecture/oanda-coordination.md` for the full existing coordination model (shared
  DEV+PROD credential, persistent streams, REST rate limits, reconnect discipline) — this document does
  not repeat or re-derive that model, only anchors it in the taxonomy.
- **Canonical output identity:** `XAU_USD` only. `XAUUSD` is accepted as an **inbound alias of this same
  OANDA feed's own alternate spelling** and is fail-closed rejected as an output key
  (`utils/hermes_instrument_catalog_v1.py`, `GOV-HERMES-IC-017`/`-021`; `utils/hermes_feed_health_v1.py`,
  `GOV-HERMES-FH-004`). This is unrelated to, and must never be confused with, §1.2 below — the alias
  logic exists purely inside the OANDA integration and has nothing to do with Vantage.
- **Semantics available:** bid/ask ticks, broker-side candle aggregation. No centralized volume, no
  order-flow, no order-book depth — OANDA CFD pricing carries no genuine exchange trade tape.

### 1.2 Vantage `XAUUSD` (new source this pack prepares a taxonomy slot for — not yet integrated)

- **Classification:** `BROKER_OTC / EXECUTABLE_QUOTE_OBSERVATION`.
- **Status:** does not exist in the codebase today (confirmed: zero market-data integration code; the
  only repository occurrence of the literal token is the forbidden-scan entry in §0). This taxonomy slot
  is forward-looking only.
- **What this classification means, precisely:** a Vantage `XAUUSD` observation is evidence that a
  particular OTC broker's own execution venue was willing to fill at a given price at a given moment. It
  is **not** a centralized-exchange trade print, carries **no** implied centralized volume, and must
  never be treated as, aggregated with, or presented as if it were exchange order-flow. Any derived fact
  computed from this source (see `derived-fact-taxonomy-and-ownership.md`) must carry a source-identity
  tag that makes this classification impossible to lose downstream.
- **Relationship to §1.1:** genuinely independent broker, independent venue, independent price formation
  process from OANDA `XAU_USD`, even though both nominally quote "gold in USD." They are never
  substitutable, never averaged together, never presented under one shared instrument key. HERMES's
  existing canonical-instrument discipline (one canonical key per real, distinct market) extends naturally
  to keeping Vantage's `XAUUSD` under its own distinct source-qualified identity — never the bare string
  `XAUUSD` unqualified, and never conflated with OANDA's inbound alias of the same spelling (§1.1).

### 1.3 COMEX GC (actual futures contracts — the source this whole HMT-0 pack is preparing for)

- **Classification:** `CENTRALISED_EXCHANGE_FUTURES / GENUINE_ORDER_FLOW`.
- **Status:** does not exist in the codebase today (confirmed: zero Databento references anywhere in the
  repository). This is the actual new capability HMT-1 (if separately authorised — see
  `hmt1-provisional-scope.md`) would build toward.
- **What this classification means:** COMEX GC (gold futures, CME Group / COMEX) is a genuine centralized
  limit-order-book market with a real, provider-attested trade tape and (depending on feed level) genuine
  book depth. This is the **only** one of the three sources for which the P0 microstructure fact classes
  in `p0-microstructure-requirements-matrix.md` (volume-at-price, CVD, absorption, etc.) can be computed
  with genuine order-flow semantics rather than an approximation.
- **Contract identity:** every GC event/fact permanently retains the actual traded contract month/year
  (e.g. `GCZ26`), never a bare `GC` root — see `gc-futures-identity-and-roll.md` for the full identity and
  roll architecture; that document is entirely downstream of this taxonomy entry.
- **Historical provenance eras:** GC provenance quality is not uniform across history. Three eras are
  recognised and must propagate as a quality/condition flag into every canonical record and every derived
  fact sourced from this contract (cross-referenced in full in `time-order-sequence-model.md` §3):
  - `PRE_2015_11_20_LEGACY` — legacy CME historical records, millisecond timestamp quality.
  - `2015_11_20_TO_2017_05_20_LEGACY` — legacy FIX/FAST-era history, **no genuine provider capture
    timestamp** available (a materially weaker provenance guarantee than the modern era; any fact derived
    from this era must carry that weaker guarantee forward, never silently upgraded).
  - `MDP3_FROM_2017_05_21` — modern MDP 3.0 provenance, the strongest available guarantee.
  A fact computed from data spanning an era boundary must record which era(s) contributed to it; a fact
  cannot claim the strongest era's guarantee if any contributing input came from a weaker era.
- **Provider:** Databento is the presumed acquisition channel referenced throughout this pack for GC P0
  data (per this pack's brief). No integration work, connection, or credential handling has been performed
  by this pack. HERMES's engineering capability to acquire this data is not licensing-gated — per Central
  Architecture's ruling, licensing/commercial correspondence is external programme administration, not an
  HMT-0 engineering/closure gate (see `licensing-and-security.md`). Actual acquisition remains subject to
  whatever commercial arrangements the programme separately administers, and to HMT-1's own separate
  implementation-authorisation gate (`hmt1-provisional-scope.md`), neither of which this taxonomy document
  itself resolves.

## §2 — Exclusion, stated explicitly

**IBKR is excluded as a HERMES market-data source for this programme.** IBKR references exist elsewhere
in this repository (e.g. `adapters/base.py`, `main.py`, `models/tick.py` — confirmed present, not
inspected in depth as it is out of scope) in contexts unrelated to this GC/HMT-0 programme; nothing in
those existing references is disturbed, reinterpreted, or extended by this pack. This exclusion is a
scope boundary for HMT-0/HMT-1, not a statement about IBKR's role (if any) elsewhere in the wider estate.

## §3 — Taxonomy invariants (binding on every downstream document in this pack)

1. A fact's source classification (`RETAIL_CFD_STREAMING_QUOTE` / `BROKER_OTC_EXECUTABLE_QUOTE_OBSERVATION`
   / `CENTRALISED_EXCHANGE_FUTURES`) is permanent, immutable metadata on that fact — never inferred at
   query time, never defaulted.
2. No fact derived from §1.1 or §1.2 may claim genuine centralized order-flow semantics. Where a
   requirements matrix entry (see `p0-microstructure-requirements-matrix.md`) cannot be honestly computed
   from a given source, the matrix says so plainly rather than describing an approximation as exact.
3. The three sources are never merged into a single "gold price" series anywhere in canonical storage.
   Cross-source comparison (e.g. research into basis/spread behaviour between OANDA CFD pricing and COMEX
   futures) is a legitimate *derived research fact* with its own explicit multi-source provenance — never
   a replacement for keeping the three native corpora distinct.
