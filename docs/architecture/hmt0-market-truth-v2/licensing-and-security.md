# HMT-0 — Licensing and security

**Initiative:** `HMT-0 — Market Truth v2 Architecture Closure`
**Status:** DOCUMENTATION / ARCHITECTURE ONLY. See [`hmt0-closure-report.md`](hmt0-closure-report.md).

## Purpose

This document states, literally and without softening, the licensing gate this entire programme's
permanent-retention ambition sits behind, and the standing evidentiary rule for how that gate may ever be
cleared.

## §1 — Build-v-rent: the binding hybrid ruling (Central Architecture, `C — HYBRID`)

Central Architecture's ruling for this programme's ownership model is **`C — HYBRID`**, and this document
reproduces it plainly rather than reinterpreting or softening it:

HERMES **permanently owns**:
- Complete canonical GC P0 history (the canonical, HERMES-derived record — not necessarily the raw native
  provider corpus itself; see the distinction below).
- Permanent derived facts (per `derived-fact-taxonomy-and-ownership.md`).
- Definitions/symbology (the canonical event model, source taxonomy, contract-identity scheme).
- Calendar/session/roll history (which calendar/session definitions and roll-policy decisions were in
  force when, per `derived-fact-taxonomy-and-ownership.md` §2 items 5–6).
- Derivation/schema identity (fact-schema and algorithm-version identity, per the same document §2
  items 1–2).
- Immutable promoted-research evidence (the evidence vault, `data-lifecycle-and-storage.md` §1.3).

**The native GC P0 source corpus itself** — now that Central Architecture has ruled the P0 native corpus
is **MBP-1** (see `canonical-market-events.md` §3, decided on the empirical measurement in
`gc-data-volume-and-cost-study.md`; previously this document referred to "whichever of TBBO/MBP-1 is
chosen" — that ambiguity is now resolved) — distinct from HERMES's own derived canonical records above,
**may be permanently retained ONLY if**:

1. Empirical size/cost (per `gc-data-volume-and-cost-study.md`) genuinely supports permanent retention,
   **AND**
2. Written licensing explicitly permits it.

**Both conditions, not either.** A favourable cost study does not, by itself, authorise permanent
retention if licensing has not separately and explicitly confirmed it in writing. Written licensing
permission does not, by itself, authorise permanent retention if the cost/volume is genuinely
prohibitive. This document does not treat either condition as a formality that the other can substitute
for.

**Condition (1) is now satisfied.** The empirical measurement in `gc-data-volume-and-cost-study.md` §2
shows the complete outright-only native MBP-1 historical corpus is approximately **345.9 GB**
(uncompressed/billable representation as quoted by Databento) for the full measured ~16-year interval
(`2010-06-06T00:00:00Z` → `2026-09-19T00:00:00Z`). This is sufficiently bounded that storage capacity does
not by itself justify discarding the P0 native corpus as a permanent-retention candidate; historical
acquisition cost for MBP-1 ($579.894677) is also below TBBO's cost for the same complete history
($881.798589). **This favourable measurement does NOT clear condition (2).** Licensing remains open — see
§2 below — and permanent retention is not authorised until it is.

## §2 — The literal marker, stated exactly as required by this pack's binding brief

> `PERMANENT_NATIVE_CORPUS_RETENTION = PENDING_WRITTEN_PROVIDER/LICENSOR_CONFIRMATION`
>
> Equivalently, and also stated literally so a future grep for either string finds the same open
> conclusion: `PENDING_WRITTEN_LICENCE_RETENTION_CONFIRMATION`

This marker remains in force **until written evidence exists** — a specific, retrievable communication
from Databento (or whichever provider is ultimately used) explicitly confirming that permanent retention
of the licensed data is permitted under the terms actually purchased. No inference from public marketing
material, a general sales page, a typical-industry-practice assumption, or a verbal/informal confirmation
satisfies this requirement. This is stated here as a **standing rule for this document and for any future
revision of it** — a future editor of this document may update the marker's value once real written
confirmation exists, but may not relax the *standard* of evidence required to do so.

**This gate is not cleared by the favourable empirical measurement now available** (§1 above,
`gc-data-volume-and-cost-study.md`). The P0 native corpus is now known to be MBP-1, its full outright-only
history is known to be ~345.9 GB at an acceptable acquisition cost, and empirical support (condition 1)
is satisfied — but licensing (condition 2) is a wholly independent requirement, still open, and remains
so until the specific written confirmation described above exists.

## §3 — Narrowing: this hybrid model does not broaden to other data classes

Per this pack's binding brief, stated explicitly here to prevent scope creep by a future reader: the
`C — HYBRID` ownership model above applies **only** to GC P0 (Trades/TBBO/MBP-1-level data for COMEX GC).
It is explicitly **not** broadened to:

- MBO (full order-by-order book) — out of scope for this entire pack; nothing in this document implies an
  MBO retention posture, permanent or otherwise.
- Deeper order books beyond top-of-book (MBP-1) — same reasoning.
- All CME products generally — this ruling is scoped to GC specifically, not a blanket CME Group
  licensing posture.
- Unrelated provider datasets (OANDA, Vantage, or any other source in `source-taxonomy.md`) — those
  sources have their own existing or future licensing postures, entirely independent of this GC-specific
  ruling.

## §4 — Security note

No credential, API key, connection string, or secret material of any kind for Databento (or any other
provider) exists in this document, this pack, or anywhere touched by this pack. No provider account has
been created, tested, or connected to as part of producing this documentation. Any future credential
provisioning for a GC data provider is implementation work, explicitly out of scope for this
documentation-only pack, and would follow the same secrets-discipline already binding on HERMES generally
(external configuration only, secrets never in Git/image/logs — per
`docs/architecture/redis-consumer-integrity-boundary.md`'s existing config-contract precedent, generalised
here as the expected standard for any future provider credential, not yet exercised for this programme).

## §5 — What this document does not do

Does not claim any licensing confirmation exists — none does. Does not choose or recommend a specific
Databento plan/tier/subscription — that is a commercial/implementation decision outside this pack's
documentation-only scope. Does not authorise beginning acquisition of any GC data, temporary or
permanent — see `hmt1-provisional-scope.md` for the explicit non-authorisation of implementation.
