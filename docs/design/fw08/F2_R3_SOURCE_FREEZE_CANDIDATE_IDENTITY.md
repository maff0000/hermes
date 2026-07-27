# FW-08 F2-R3 — Canonical-Source Freeze + Candidate Identity Allocation (INERT)

**WO:** WO-HELM-HERMES-FW08-REAL-PROOF-LIFECYCLE-CANONICAL-SOURCE-FREEZE-AND-CANDIDATE-IDENTITY-CONTRACT-IMPLEMENTATION-0001
**Owner:** HERMES (Helm). **Created (UTC):** 2026-07-27. **Contract version:** 1.
**Canonical base:** `4d6b346d39b6d4a401d57281cee9bd619894f090`.

This WO implements the **INERT contracts** for the first two stages of the merged F2-R3 real-proof execution
lifecycle (`docs/design/fw08/f2_r3_real_proof_lifecycle/f2_r3_real_proof_lifecycle.v1.json`, 22 stages):

- **Stage 1 — canonical-source freeze**
- **Stage 2 — candidate identity allocation**

It is the **first of an 8-WO serial sequence**. It **builds nothing, freezes nothing, allocates nothing,
creates no tag, no image, no key/secret, and touches no runtime.** Real source-freeze and real candidate
allocation **MUST fail closed** because `execution_authorised=false`. Only synthetic fixture evidence may
validate, in test mode.

## What is NOT done (fail-closed)

| Concern | Posture |
|---------|---------|
| Real canonical-source freeze | UNAVAILABLE — `production_real_freeze_available()` is `False`; `observe_source_freeze` in REAL mode ALWAYS returns `(None, ('REAL_PROOF_LIFECYCLE_REAL_FREEZE_NOT_AUTHORISED','SF-REAL-FREEZE-UNAVAILABLE'))` — no fallback, no downgrade. |
| Real candidate allocation | UNAVAILABLE — `allocate_candidate_identity` in REAL mode ALWAYS returns `(None, ('CI-REAL-ALLOCATION-UNAVAILABLE','REAL_PROOF_LIFECYCLE_REAL_ALLOCATION_NOT_AUTHORISED'))`. |
| Candidate tag / image | Not created / not built. |
| External authority / keys / secrets | Not configured / not created. |
| Real lifecycle-state transition | Impossible — any REAL state target fails closed (`LS-REAL-STATE-NOT-AUTHORISED` / `EXECUTION_NOT_AUTHORISED`). |
| Protected runtime | UNTOUCHED — source `71ea3bd4598d8af5628f78de10f8022088ad3f05` / image `c5fc2a62f424` / container `d80018037b7f`, `consumer_live=false`. |

## Models

### Stage 1 — `design/hermes_fw08_source_freeze_v1.py`

- **`SourceFreezeRequest`** (§4): binds the full request field set (application, repository, canonical branch,
  canonical source SHA, source-tree digest, request id, requesting authority, `requested_utc`,
  intended lifecycle id + candidate purpose, `expiry_utc`, prerequisite audit reference, prior canonical
  reference, configuration classification, `execution_authorised`, `request_digest`). `validate_source_freeze_request`
  rejects wrong type/digest tamper, `execution_authorised != False` (the **invariant**,
  `SFR-EXECUTION-AUTHORISED-FORBIDDEN`), missing/abbreviated SHA, non-canonical branch, missing tree digest,
  a **branch-only mutable identity** (`SFR-MUTABLE-BRANCH-IDENTITY`), missing audit reference, wrong
  application/repository, stale/expired, missing UTC (D-PR120-STAGE-FIELDS), and a caller-selected GREEN/frozen
  status field.
- **`SourceFreezeEvidence`** (§5): the eventual freeze-evidence record. The classification is set by the
  **module-owned `_FreezeIssuer`** (ephemeral HMAC seal, mirroring `hermes_fw08_real_image_proof_v1`), never by
  the caller. `production_real_freeze_available()` is `False`, so a REAL classification is unmintable.
  `new_synthetic_freeze_evidence` supplies the private `_MODULE_TOKEN`; a bare `observe_source_freeze` caller
  gets `SF-SYNTHETIC-REQUIRES-MODULE-TOKEN`. `validate_freeze_evidence` rejects a **relabelled** synthetic
  (`SF-SYNTHETIC-NOT-MODULE-MINTED`, seal-bound to classification), `SF-SYNTHETIC-IN-REAL-MODE`, revoked/invalid,
  SHA/tree mismatch, dirty tree, local/main/origin disagreement, stale/expired, **copied evidence**
  (`SF-COPIED-EVIDENCE`), and missing UTC / evidence reference (D-PR120-STAGE-FIELDS).

### Stage 2 — `design/hermes_fw08_candidate_identity_v1.py`

- **`CandidateIdentity`** (§6): binds the full candidate field set including `candidate_namespace`,
  `proposed_immutable_tag`, `allocation_request_utc`, `expiry_utc`, `prerequisite_freeze_evidence_digest`,
  `candidate_status`, `execution_authorised`. `allocate_candidate_identity` real mode always fails closed;
  test mode is module-token gated and mints `SYNTHETIC_UNALLOCATED`, `execution_authorised=False`.
- **`validate_candidate_identity`** (§7 naming/isolation) rejects `execution_authorised != False`, a namespace
  not under `hermes-fw08-candidate` (`CI-NAMESPACE-NOT-ISOLATED`), a cross-application namespace, the `latest`
  tag (`CI-LATEST-FORBIDDEN`), any mutable/operational tag, **deployed-image reuse** (`c5fc2a62f424`) or a
  runtime service name, an unbound source SHA, missing lifecycle id, no expiry, an active deployment target, a
  non-`SYNTHETIC_UNALLOCATED` status, and missing UTC / freeze-evidence reference. `validate_candidate_uniqueness`
  rejects a reused `candidate_id` (`CI-CANDIDATE-ID-REUSE`).

### §8 — `design/hermes_fw08_lifecycle_state_v1.py`

`advance_lifecycle_state` permits **only** `LIFECYCLE_DESIGNED -> SOURCE_FREEZE_CONTRACT_READY`, and only when
the freeze request and candidate contract are valid **and** `execution_authorised` is `False`. There is
**no `force` / `advance` boolean** — progress is derived, never asserted. A REAL state target
(`SOURCE_FROZEN` / `CANDIDATE_ALLOCATED` / …) always fails closed with
`(EXECUTION_NOT_AUTHORISED, LS-REAL-STATE-NOT-AUTHORISED)`. `execution_authorised=True` →
`LS-EXECUTION-AUTHORISED-FORBIDDEN`.

## Design-note integration

- **D-PR120-EV-TAIL** — `eventual_evidence_record_ids()` defines the downstream evidence record ids
  (`source_freeze` → `EV-01-SOURCE-FREEZE`, `candidate_allocation` → `EV-02-CANDIDATE-ALLOCATION`) so later
  EV-20/EV-21/EV-22 decision/closure evidence can reference them unambiguously.
- **D-PR120-STAGE-FIELDS** — every new model carries explicit UTC fields and an explicit evidence-reference
  field, enforced by `SFR-UTC-MISSING`, `SF-UTC-MISSING`, `SF-EVIDENCE-REF-MISSING`, `CI-UTC-MISSING`,
  `CI-FREEZE-EV-REF-MISSING`.

## Purity

All three design modules are PURE (stdlib only, `from __future__ import annotations`, `CONTRACT_VERSION="1"`,
frozen dataclasses, fail-closed `(obj|None, sorted reason tuple)`, injected `now_utc`, no runtime import, no
cycle). The single permitted non-determinism is the module-owned `_FreezeIssuer` ephemeral seal key
(`secrets.token_bytes(32)`), mirroring the F2-R3 proof issuer. Tests are additive and pass. UTC-only. No
secrets anywhere.
