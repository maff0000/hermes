# HMT-0 — HMT-2 provisional acquisition roadmap

**Initiative:** `HMT-0 — Market Truth v2 Architecture Closure`
**Status:** DOCUMENTATION / ARCHITECTURE ONLY. See [`hmt0-closure-report.md`](hmt0-closure-report.md).

## Purpose

This document names and scopes the next-but-one programme step, `HMT-2`, at the planning/doctrine level
only — a research-acquisition-sequencing roadmap for the eventual native GC historical corpus. It is
*not* an implementation document, and it is *not* an authorisation. Its status is stated plainly and is
not softened anywhere in this document:

> **HMT-2 — GOVERNED GC HISTORICAL RESEARCH CORPUS**
> **RECORDED / NOT AUTHORISED.**

HMT-2 authorisation, like HMT-1's, requires a separate, explicit future decision. This document's
existence — describing HMT-2's acquisition-sequencing doctrine at the planning level — does not itself
constitute that authorisation, exactly as `hmt1-provisional-scope.md` §3 states for HMT-1's own scope
description. Nothing in this document, or in any other document in this HMT-0 pack, authorises HMT-1 or
HMT-2, and nothing in this document alters HMT-1's own separately-stated non-authorisation in any way.

## §1 — Budget doctrine

A total historical-data budget of **~$125** is currently available for this research line: a **~$100**
planning ceiling for the initial research corpus, plus **~$25** contingency. These are **ceilings and
planning envelopes, not spending targets** — the programme is not required to spend the full amount, and
underspending is not a shortfall.

Core principle, stated exactly:

> "The first $125 is experimental capital. Maximise information gained per dollar, not historical-data
> volume accumulated per dollar."

These figures are planning-document content only. **They must never be encoded into runtime, config,
schema, or health semantics** — no `.py`, config, or schema file may embed the $125/$100/$25 figures, the
session-count planning range in §5, or any other number from this document as a runtime constant, gate,
or check. This document's numbers exist to inform a future, separate acquisition decision — not to become
part of HERMES's technical semantics.

## §2 — Complete-history reference (retained, not purchased)

The complete outright-only MBP-1 history is already measured and its full figures are the authoritative
reference for that history's size and cost — see `gc-data-volume-and-cost-study.md` for the full figures
(2010-06-06 → 2026-09-19, 120 verified outright GC futures contracts, 4,324,008,111 records,
345,920,648,880 billable bytes, $579.894677); they are not restated in full here.

**Do NOT purchase the complete corpus at this stage.** Its technical feasibility is already proven —
HERMES's engineering capability to acquire, retain, canonicalise, replay, store, and research this corpus
is established (per the licensing de-gating already ruled elsewhere in this pack, see
`licensing-and-security.md` and `canonical-market-events.md` §3). Its purchase is deferred by **research
sequencing**, not by any engineering limitation: the programme chooses to spend a small experimental
budget on a stratified research panel first, and only later — if the evidence in §12 below supports it —
consider broader or complete acquisition.

## §3 — Initial HMT-2 corpus

The initial HMT-2 research corpus is: GC MBP-1, restricted to the **post-2017 modern provenance era**
(from `2017-05-21`, the MDP3 era per `time-order-sequence-model.md` §3), sampled as a **regime-stratified
session panel** (§4–§7).

This restriction to the modern era does **not** redefine or narrow HERMES's architectural ability to
ingest older history — the P0 native-corpus ruling (MBP-1, per `canonical-market-events.md` §3) is
unaffected and unnarrowed by this document. Restricting the initial HMT-2 research panel to the modern
era is a **research-budget sequencing choice only**, made because the modern era carries genuine
independent capture-time provenance (per `time-order-sequence-model.md` §3), which is the cleanest
starting corpus for research purposes — it is not an engineering or architectural limitation.

## §4 — Six required session strata

The panel must be stratified across all six of the following session categories. None may be omitted:

1. **Major scheduled-event sessions.** Sessions around major scheduled market events. Specific event
   names/catalysts are **not** to be encoded as canonical HERMES market semantics anywhere in this
   programme — event context belongs to external catalyst authority, ultimately **ARES** (see §8 below).
2. **Matched quiet/non-event control sessions.** Sessions selected to match the event sessions on
   ordinary session characteristics but without a scheduled catalyst, so event and non-event behaviour can
   be genuinely compared.
3. **High-volatility non-event sessions.** Required explicitly, so that later research can distinguish
   "high volatility" from "high volatility caused by a named scheduled catalyst" — volatility and event
   presence must not be allowed to collapse into the same variable in the panel.
4. **Compression/balanced sessions.** Required so the corpus is not biased toward directional-expansion-
   only conditions.
5. **Random baseline sessions.** An unconditional reference distribution, sampled without regard to any of
   the above categories, to give later research an honest baseline.
6. **Protected random holdout sessions.** See §6 below.

## §5 — Planning size and temporal coverage

**Planning size:** approximately **400–500 sessions**. This is explicitly **not a quota** — the actual
number acquired is subject to exact Databento metadata quotation before any purchase (§10). If a smaller,
well-balanced panel achieves the research goal, the programme spends less; the 400–500 figure is a
planning midpoint, not a target to be filled regardless of cost or need.

**Broad temporal coverage:** the panel should prefer sampling broadly across the full modern era
(`2017-05-21` → present) rather than concentrating in recent years only. It must span materially different
gold/rates/volatility environments, and the selection process must explicitly guard against accidental
recency clustering (e.g. a panel that is unintentionally mostly the last one or two years).

## §6 — Protected holdout

A random holdout subset must be selected and **frozen before any exploratory feature/model development
begins**. For the holdout, the programme must record:

- the selection method used;
- a deterministic seed, or an equivalent selection identity, where applicable;
- the exact session-list identity/hash of the frozen holdout; and
- the corpus version the holdout was drawn against.

The holdout must never be silently leaked into development — once frozen, it is held out, and any use of
it in development invalidates its protective purpose.

## §7 — Selection-bias doctrine

The corpus must not consist mainly of spectacular news days. Later research needs the ability to
distinguish, at minimum: expansion/order-flow environments, mean-reverting environments, environments with
no demonstrated edge, event-driven behaviour, comparable non-event volatility, and balanced/compressed
states (this is why §4's six strata are all required, not optional).

Stated as a hard rule, verbatim:

> "Event presence is context, not a trade instruction."

HERMES must never encode "event day = trade opportunity" anywhere — not in this corpus's selection logic,
and not in any canonical HERMES semantics.

## §8 — System ownership boundary (three parties)

This roadmap is built on a precise three-way ownership boundary that must not be blurred:

- **HERMES owns observed market behaviour** — trades, top-of-book transitions, canonical market events,
  governed derived market facts, and replay/evidence. This is squarely the existing HMT-0 canonical-event
  and derived-fact scope; see `canonical-market-events.md` and `derived-fact-taxonomy-and-ownership.md` by
  name for that scope. HMT-2 does not expand or alter this ownership — it is a data-acquisition roadmap
  operating within it.
- **ARES ultimately owns catalyst/event context.** HERMES does not build or own event-calendar/catalyst
  semantics itself — "major scheduled-event session" in §4 is a session-selection label for this research
  panel, not a HERMES-owned market-truth concept.
- **DARWIN later tests the empirical relationship** between HERMES market truth and governed external
  (ARES) context. **DARWIN remains frozen now.** This is a description of a *future* relationship, not
  present work, and this document does not authorise, imply, or create any pathway toward DARWIN work —
  the same non-goal statement already made for HMT-1 in `hmt1-provisional-scope.md` §2 and in
  `hmt0-closure-report.md` applies identically here, unweakened, to HMT-2.

## §9 — Event-distance support

The corpus must preserve sufficient timestamp/session context for later research to study pre-event,
immediate-event, post-event, and subsequent-continuation behaviour by temporal distance from a catalyst.

This document explicitly does **not** hard-code any specific window (5m/15m/30m/60m or otherwise) as
HERMES truth. Any such window is a later, separately-versioned **research parameter**, layered on top of
HERMES's own precise canonical timestamps — it is not, and must never become, part of canonical HERMES
semantics itself.

## §10 — Pre-download quotation gate

No acquisition under HMT-2 may proceed except through the following ordered gate, in full, without
skipping or reordering any step:

1. Produce the final proposed session set.
2. Resolve actual GC contracts for each session.
3. Resolve exact requested MBP-1 intervals.
4. Obtain real Databento metadata (`record_count`, `billable_size`, `quoted_cost`) for the final candidate
   set.
5. Confirm the stratification distribution matches the six required strata (§4).
6. Confirm holdout allocation (§6).
7. Confirm total expected cost is within the approved experiment envelope (~$100 ceiling, ~$25
   contingency, §1).
8. Obtain the required programme approval.
9. Only then acquire.

**No blind download.**

## §11 — HMT-1 boundary

This is the sharpest boundary in this document, and it is stated plainly:

HMT-1 remains `CANONICAL MARKET EVENT & REPLAY FOUNDATION` (per `hmt1-provisional-scope.md`), and the
~$125 / HMT-2 stratified-research-corpus budget in §1 is **explicitly NOT authorised for use during
HMT-1**. HMT-1, if and when separately authorised, uses **synthetic governed fixtures by default**. If,
and only if, synthetic fixtures cannot adequately prove a required source-format/canonicalisation/replay
property, Central PO / Chief Architect may explicitly authorise acquisition/use of the minimum
technically sufficient real fixture solely for HMT-1 validation. Such a fixture is test evidence, not a
research corpus, and does not begin HMT-2 or authorise broader historical acquisition.

This exception is deliberately narrow. It applies only where ALL of the following hold simultaneously:

1. Synthetic input cannot adequately prove a required source-format/canonicalisation/replay property.
2. The fixture is the minimum technically sufficient sample.
3. Its sole purpose is proving the HMT-1 measuring instrument (the source-bytes → canonical-events →
   persisted-partition → deterministic-replay → identical-identities/counts/hashes chain).
4. It is NOT used for market research, feature discovery, regime analysis, strategy analysis, or model
   development.
5. It does NOT begin the HMT-2 research corpus.
6. It does NOT authorise broader historical acquisition.
7. It receives explicit prior approval from **Central PO / Chief Architect** — this is the ONLY approval
   authority for this exception. No implicit approval. No Rogue/Helm/FORGE self-authorisation of this
   exception, ever.

Outside this narrowly-scoped, Architect-approved exception, HMT-1 does **not** become historical-data
acquisition, and this document does not create, imply, or authorise any path by which it could.

This HMT-2 roadmap document does **not** itself authorise HMT-1, and it does not itself constitute the
Central PO / Chief Architect approval described above — that approval, if it is ever given, is a separate,
explicit, future decision, not something this document grants by describing the mechanism for it. HMT-1's
own non-authorisation, exactly as stated in `hmt1-provisional-scope.md`, is completely unaffected and
unaltered by anything in this document — the two gates are separate, and clearing or discussing one has no
bearing on the other.

## §12 — Expansion rule

Stated exactly:

> "Broader historical acquisition follows evidence, not available cash."

If initial HMT-2 research demonstrates that HERMES microstructure facts materially improve research
quality or measurable edge: the programme may progressively expand temporal coverage, sample density, and
regimes, and potentially move toward larger or full corpus ownership.

If the facts fail to improve research quality: the programme investigates methodology, data, and feature
definitions first. It does **not** automatically spend additional money on more data as a first response
to a null result.

The ~$580 complete-history corpus (§2) is an **option**, available if and when evidence supports it — it
is not a default next purchase, and nothing in this document schedules or commits to it.

## §13 — Authorisation gate

HMT-2 acquisition begins only when a separate, explicit authorisation is issued by whoever holds that
authority for this programme — exactly the same authorisation discipline `hmt1-provisional-scope.md` §3
states for HMT-1. This document's existence — recording HMT-2's acquisition-sequencing doctrine at the
planning level — is itself part of this pack's documentation-only deliverable; it is not, and must never
be read as, that authorisation. HMT-2's non-authorisation is independent of, and never conflated with,
HMT-1's own separate non-authorisation.

## §14 — What this document does not do

Does not authorise HMT-1 or HMT-2. Does not purchase, download, or acquire any market data. Does not
commit to a start date or a work-order number for HMT-2. Does not encode any budget figure, session-count
figure, or strata figure from this document into runtime, config, or schema. Does not alter any settled
HMT-0 canonical-event, timestamp, futures-identity, or storage architecture ruling elsewhere in this
pack — this document is a research-acquisition sequencing roadmap layered on top of those settled
rulings, not a revision of them.
