# HMT-3B — Flow Response / Efficiency

**Initiative:** `HMT-3 — Post-HMT-2 HERMES Market Truth Roadmap`
**Status:** **NOT YET AUTHORISED FOR IMPLEMENTATION.** See
[`hmt3-roadmap-overview.md`](hmt3-roadmap-overview.md).

## Purpose

Name the flow-response/efficiency fact family — measurements of how price responds to executed flow — and
state, without qualification, the boundary between what this item may publish as HERMES fact and what it
must never publish, plus the temporal-measurement discipline it must obey.

## §1 — Fact family

- Aggressive volumes.
- Delta.
- Displacement.
- Favourable/adverse displacement.
- Progress per unit flow.
- Efficiency measures.

## §2 — `TRAPPED_TRADER` may never be published as market truth

> `TRAPPED_TRADER` must never be published as HERMES market truth.

This is stated without qualification. Whatever measurements this item produces (displacement, favourable
vs. adverse displacement, progress per unit flow) may be published as objective, versioned measurements;
a label asserting that a trader is "trapped" is a trader-intent narrative, not a fact, and is forbidden
here exactly as the general rule in [`terminology-doctrine.md`](terminology-doctrine.md) requires.

## §3 — No trader-intent assertions

More generally, this item must not assert trader intent of any kind. It measures price/flow response;
it does not infer, label, or publish why a market participant acted as they did.

## §4 — Contemporaneous vs. ex-post measurement

This item must distinguish, explicitly and at the fact level, between a measurement made contemporaneously
(using only information available at the time being measured) and a measurement made ex-post (using
information that only became available after that time, such as how price ultimately resolved). These are
not interchangeable, and a fact must state honestly which kind of measurement it is.

## §5 — `available_at` semantic requirement

Every fact this item produces must carry an honest `available_at` semantic (per
[`derived-fact-contract-doctrine.md`](derived-fact-contract-doctrine.md)) so that a consumer can determine,
without ambiguity, the earliest point in time at which the fact could genuinely have been known — this is
the structural mechanism that makes the contemporaneous-vs-ex-post distinction in §4 enforceable rather
than merely documented.

## §6 — Non-authorisation

This document describes the intended shape and constraints of HMT-3B. It does not authorise beginning any
implementation of it. See [`hmt3-roadmap-overview.md`](hmt3-roadmap-overview.md) for the programme-wide
non-authorisation statement.
