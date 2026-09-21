# HMT-0 — Derived-fact taxonomy and ownership

**Initiative:** `HMT-0 — Market Truth v2 Architecture Closure`
**Status:** DOCUMENTATION / ARCHITECTURE ONLY. See [`hmt0-closure-report.md`](hmt0-closure-report.md).

## Purpose

Every fact class in `p0-microstructure-requirements-matrix.md` (volume-at-price, CVD, absorption, etc.),
every continuous-futures series in `gc-futures-identity-and-roll.md`, and every future derived indicator
in this programme is a **derived fact** — something HERMES computed from canonical raw input, not the raw
input itself. This document is the single, generalised contract every derived fact must satisfy,
regardless of which specific fact it is. It exists so that no future implementer has to reinvent
provenance discipline per fact class, and so that a derived fact can never be produced "just this once"
without the identity fields below.

## §1 — The hard rule

> **No unversioned semantic fact.**

This is stated as a hard rule, not a preference, per this pack's binding brief. A derived fact record
that is missing any of the fields in §2 below is not a valid derived fact — it is not published, not
promoted to the evidence vault (see `data-lifecycle-and-storage.md`), and not relied upon by any
downstream consumer.

## §2 — The minimum field set (every derived fact, no exceptions)

1. **Fact schema/version** — the identity of the fact's own record shape (field names/types/meaning),
   versioned independently of the algorithm that produced it. A schema change (even a rename) is a new
   schema version, not a silent overwrite of the old one's meaning.
2. **Algorithm version** — the identity of the specific computation logic that produced this fact
   (e.g. "absorption-detector v3"). Distinct from schema version: the same schema can be filled by
   different algorithm versions over time as detection logic improves.
3. **Parameter identity/hash** — every tunable parameter the algorithm used (window sizes, thresholds,
   smoothing constants, etc.), captured as an identity or hash rather than assumed-constant. Two runs of
   the "same" algorithm version with different parameters produce facts that must be distinguishable by
   this field, never silently merged. This is the field that ties directly into the deterministic-replay
   architecture (`deterministic-replay-and-evidence.md`) — parameter-transmission integrity means this
   identity must arrive at the point of computation unmodified and be recorded exactly as used, not as
   configured-and-possibly-drifted.
4. **Source event/corpus identity** — precisely which raw events (by source classification per
   `source-taxonomy.md`, and by the specific contract identity per `gc-futures-identity-and-roll.md`
   where GC-derived) fed this computation. Never a vague "the GC feed" — the actual contributing
   event range/contract set.
5. **Session/calendar identity** — which trading session/calendar definition was in force when this fact
   was computed (relevant to any fact that is session-bounded, e.g. a daily value area). Existing HERMES
   precedent for calendar/session-identity discipline exists in `ADR-0004-market-closure-authority.md`
   (deterministic schedule, `UNCLASSIFIED_MARKET_STATE` for unresolved exceptional periods, never guessed)
   — this field generalises that same discipline to derived microstructure facts.
6. **Roll policy** (where applicable) — for any fact computed over or across a continuous-futures series
   (`gc-futures-identity-and-roll.md`), which roll-policy version was in force. Absent/`N/A` for facts
   computed purely within a single actual contract's own history.
7. **Completeness/gap state** — whether the source data this fact was computed from was complete for its
   window, or degraded/partial, and how. Existing precedent: `canonical_candles_h4`/`canonical_candles_d1`
   already carry `source_count`, `expected_source_count`, `source_coverage`, and `gap_state` columns for
   exactly this purpose (migration 027) — this field generalises that pattern to every derived fact in
   this programme, not just candles.
8. **Evidence lineage** — a pointer back to the specific raw/canonical inputs and the historical
   provenance era(s) (per `time-order-sequence-model.md` §3) those inputs belonged to, sufficient to
   support the deterministic replay this fact must be reproducible under
   (`deterministic-replay-and-evidence.md`).

## §3 — Labelling discipline: `PROXY` vs `EXACT`

Per `p0-microstructure-requirements-matrix.md`, several fact classes (absorption, failed aggression,
balance/imbalance, squeeze/trapped-flow proxies) cannot be computed with genuine correctness from a
weaker feed level than MBP-1. Any such fact computed from a weaker level must carry an explicit
computation-fidelity flag — `PROXY` (an honest approximation, with its specific limitation named) or
`EXACT` (computed at or above the fact's genuine minimum-correct level) — as part of its algorithm-version
identity (§2 item 2). A `PROXY` fact is never silently promoted to look like an `EXACT` one by a later
schema revision that simply drops the flag; that would be exactly the kind of invented-plausibility error
this entire pack's evidence discipline (per its binding brief) exists to prevent.

## §4 — Ownership

- **HERMES owns** every derived fact's identity fields (§2) and the governed algorithm/schema that
  produced it — this is squarely inside the market-data fact-spine mandate
  (`docs/governance/PID-HERMES-MVP-001.md`).
- **HERMES does not own** any interpretation of what a derived fact *means* for a trading decision. A
  derived "absorption" fact is evidence of a market-data condition, exactly as
  `docs/governance/PID-HERMES-MVP-001.md` §3 already rules for gap/recovery facts: *"a published
  gap/recovery fact is evidence of a market-data condition, never authority to act."* This document
  extends that same binding correction to every P0 microstructure derived fact — none of them carry
  trading authority, and none of them are HERMES making a recommendation.

## §5 — What this document does not do

Does not specify storage schema (table/column shapes) — that is `data-lifecycle-and-storage.md`'s and
eventual HMT-1 implementation's concern. Does not specify which of the requirements-matrix fact classes
are actually built first, or at all — that is a future, separately-authorised scoping decision.
