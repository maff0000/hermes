# HMT-3 — Determinism Standard

**Initiative:** `HMT-3 — Post-HMT-2 HERMES Market Truth Roadmap`
**Status:** DOCUMENTATION / GOVERNANCE RECORD ONLY. Governs every HMT-3 item. See
[`hmt3-roadmap-overview.md`](hmt3-roadmap-overview.md) for the roadmap-wide non-authorisation statement.

## Purpose

State the single reproducibility equation that every fact produced anywhere in the HMT-3 roadmap must
satisfy, and the "missing is never silently zero, unknown remains unknown" rule that goes with it.

## §1 — The determinism equation

> Same governed source bytes + same mapping/version + same derivation version + same complete parameters
> = same facts, same fact identities, same semantic hashes, same physical artefacts where persisted.

This equation is the reproducibility guarantee behind every fact envelope field named in
[`derived-fact-contract-doctrine.md`](derived-fact-contract-doctrine.md) §2 (derivation version, complete
parameter-set identity, semantic identity/hash, provenance to immutable evidence) — those fields exist
specifically so that this equation can be checked, not merely asserted.

## §2 — Missing is never silently zero; unknown remains unknown

Where a source event, a source partition, or an input needed to compute a fact is missing, the resulting
fact must never silently resolve to zero (or to any other value that could be mistaken for a genuine
measurement). Missing input produces an explicit missing/unknown state, not a numeric substitute. This is
consistent with, and extends to every HMT-3 fact, the unknown-side/quality metrics already named for
executed-flow facts in [HMT-3A](hmt3a-derived-fact-spine-and-executed-flow.md) §2 and the quality-state
field already required in every fact envelope by
[`derived-fact-contract-doctrine.md`](derived-fact-contract-doctrine.md) §2.

## §3 — Every fact remains traceable to immutable source evidence

Every fact produced under this roadmap must remain traceable to immutable source evidence — the
provenance chain from a published fact back to the ungoverned, immutable source bytes it was computed
from must never be broken, obscured, or made optional.

## §4 — Non-authorisation

This document states doctrine that governs the reproducibility of every HMT-3 item's facts. It does not
itself authorise any implementation. See [`hmt3-roadmap-overview.md`](hmt3-roadmap-overview.md).
