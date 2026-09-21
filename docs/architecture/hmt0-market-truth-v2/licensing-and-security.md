# HMT-0 — Licensing and security

**Initiative:** `HMT-0 — Market Truth v2 Architecture Closure`
**Status:** DOCUMENTATION / ARCHITECTURE ONLY. See [`hmt0-closure-report.md`](hmt0-closure-report.md).

## Purpose

This document restates Central Architecture's binding build-v-rent ownership ruling for this programme's
GC P0 native corpus, and Central Architecture's separate, later ruling that licensing/commercial
correspondence is external programme administration — not an HMT-0 engineering or closure gate. It
preserves the useful commercial/licensing context (Databento's terms, and the standing caution against
inferring permission from marketing material) so that institutional knowledge is not lost, while making
explicit that none of it alters HERMES's engineering capability or gates this pack's closure.

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
chosen" — that ambiguity is now resolved) — is distinct from HERMES's own derived canonical records above,
and HERMES's engineering architecture is **fully capable** of acquiring, permanently retaining,
canonicalising, replaying, storing, and researching it. That engineering capability rests on empirical
size/cost genuinely supporting it (per `gc-data-volume-and-cost-study.md`), and **that condition is
satisfied**: the measurement in `gc-data-volume-and-cost-study.md` §2 shows the complete outright-only
native MBP-1 historical corpus is approximately **345.9 GB** (uncompressed/billable representation as
quoted by Databento) for the full measured ~16-year interval (`2010-06-06T00:00:00Z` →
`2026-09-19T00:00:00Z`), at an acquisition cost ($579.894677) below TBBO's cost for the same complete
history ($881.798589). This is sufficiently bounded that storage capacity does not by itself justify
discarding the P0 native corpus as a permanent-retention candidate.

**Licensing/commercial correspondence is a separate matter.** Per Central Architecture's later, binding
ruling on this point:

> Licensing/commercial matters are external programme administration. They do not alter HERMES technical
> capability, contracts, canonical semantics or HMT phase engineering acceptance.

HERMES's engineering architecture's capability to acquire/retain/canonicalise/replay/store/research the
native MBP-1 corpus is **not** conditioned on licensing correspondence — this document no longer treats
written licensing confirmation as a precondition for that engineering capability. **Actual
organisational/commercial use of that capability** — i.e., whether and when the programme actually
exercises this capability against a live, paid Databento (or other provider) account — **remains subject
to whatever commercial arrangements the programme separately administers.** That administration is
external to HERMES's technical semantics and to HMT-0 engineering closure; it is recorded here for
institutional memory, not as a condition this pack, or any future engineering-closure document, needs to
satisfy.

## §2 — Commercial/licensing context (recorded, non-gating)

This section preserves the commercial/licensing context useful to a future reader, explicitly reframed
per the ruling in §1 above as **non-gating**:

- No specific written confirmation from Databento (or any other provider) about permanent-retention terms
  has been sought or obtained by this pack. That remains true, and is recorded here as a factual
  statement about the current state of commercial correspondence — **not** as an engineering blocker.
- The general caution this document has always carried still applies as sound commercial practice: no
  inference from public marketing material, a general sales page, a typical-industry-practice assumption,
  or a verbal/informal confirmation should be treated as a substitute for whatever actual commercial
  agreement the programme separately enters into with a provider. This is good discipline for whoever
  administers that commercial relationship — it is simply no longer framed, in this document, as a
  condition on HERMES's engineering architecture or on HMT-0 closure.
- Whoever administers the programme's actual commercial relationship with Databento (or any other GC data
  provider) may document that administration wherever the programme keeps its commercial/legal records —
  that is explicitly outside this architecture pack's scope, per §1 above.

**This document previously stated a literal marker** —
`PERMANENT_NATIVE_CORPUS_RETENTION = PENDING_WRITTEN_PROVIDER/LICENSOR_CONFIRMATION`, with a synonym
`PENDING_WRITTEN_LICENCE_RETENTION_CONFIRMATION` — gating engineering closure on written licensing
confirmation. **Per Central Architecture's ruling in §1 above, that marker is retired.**
Licensing/commercial correspondence does not gate HMT-0 engineering closure, and no new engineering gate
replaces it.

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
