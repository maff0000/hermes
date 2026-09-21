# HMT-0 — Deterministic replay and evidence

**Initiative:** `HMT-0 — Market Truth v2 Architecture Closure`
**Status:** DOCUMENTATION / ARCHITECTURE ONLY. See [`hmt0-closure-report.md`](hmt0-closure-report.md).

## Purpose

A derived fact is only as trustworthy as the confidence that recomputing it, from the same inputs, would
produce the same output. This document defines the architecture requirement for that guarantee — how
research evidence in this programme gets reproduced exactly, given the same inputs — and ties directly
into the derived-fact versioning contract (`derived-fact-taxonomy-and-ownership.md`) and the
parameter-transmission integrity requirement referenced there.

## §1 — The replay guarantee

> Given the same source event/corpus identity, the same algorithm version, and the same parameter
> identity/hash (the three identity fields from `derived-fact-taxonomy-and-ownership.md` §2 items 2–4),
> recomputing a derived fact must produce a bit-identical (or, where floating-point non-determinism is
> genuinely unavoidable, an explicitly bounded and documented-tolerance) result.

This is the property that lets a derived fact be promoted into the evidence vault
(`data-lifecycle-and-storage.md` §1.3): promotion is a claim that the fact is reproducible, not just that
it was computed once and looked reasonable.

## §2 — Parameter-transmission integrity

A derived fact's recorded parameter identity/hash (`derived-fact-taxonomy-and-ownership.md` §2 item 3) is
only meaningful if it reflects the parameters **actually used** at computation time, not the parameters
that were *configured* and may have drifted before or during the run. This document requires:

- The parameter identity is captured **at the point of computation**, from the values the algorithm
  actually consumed — never reconstructed after the fact from a separate configuration record that could
  have changed in between.
- A replay attempt that cannot obtain the exact parameter identity used originally cannot claim a valid
  replay — it can only claim a *new* computation under *newly chosen* parameters, and must be labelled as
  such (a fresh algorithm-version/parameter-identity combination), never presented as having reproduced
  the original.
- This mirrors, at the parameter layer, the same "canonical, deployed and operational states must always
  be recorded separately" discipline already binding on HERMES generally
  (`docs/governance/PID-HERMES-MVP-001.md` §5/§8) — a parameter's *configured* state and its *actually-used*
  state are not automatically the same thing, and this document requires the distinction be provable, not
  assumed.

## §3 — What "same inputs" means, precisely

"Same source event/corpus identity" (per `derived-fact-taxonomy-and-ownership.md` §2 item 4) requires the
input events themselves to be immutably addressable — which is exactly why the market research store
(`data-lifecycle-and-storage.md` §1.2) is append-oriented and the evidence vault (§1.3) is immutable.
A replay that reads from a mutable operational table whose rows could have been altered since the
original computation cannot claim the same guarantee — replay-grade reproducibility is a research-store/
evidence-vault-tier property, not an operational-tier one, precisely because the operational tier is
allowed to be live and mutable.

## §4 — Failure mode this architecture exists to prevent

Without this discipline, a future implementer could recompute a "CVD" figure for a historical window, get
a different number than before, and have no way to tell whether that's because (a) the underlying source
data genuinely changed (e.g. a late correction), (b) the algorithm version changed, (c) a parameter
drifted, or (d) there's a genuine non-determinism bug. This document's identity-and-lineage requirements
(inherited directly from `derived-fact-taxonomy-and-ownership.md`) exist so that distinction is always
answerable from the fact's own recorded metadata, never requiring an investigation to reconstruct after
the fact.

## §5 — What this document does not do

Does not specify a concrete replay-execution mechanism (a specific job runner, container image pinning
strategy, or test harness) — that is implementation, gated behind HMT-1 authorisation. Does not claim any
replay capability currently exists in HERMES; none does (per `current-state-reconciliation.md` §3, no
research store or evidence vault tier exists today, and no GC computation exists to replay).
