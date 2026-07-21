# FW-08 F-2 — Independent Active-Import Anchors

**WO:** WO-HELM-HERMES-FW08-F2-INDEPENDENT-ACTIVE-IMPORT-ANCHORS-IMPLEMENTATION-0001
**Owner:** HERMES (Helm). **Contract version:** 1. **Generated (UTC):** 2026-07-21.

## The defect (F-2)

The active-import evidence validator
`design/hermes_fw08_active_import_evidence_v1.py::validate_image_bound_active_import` is a PURE comparator:
it compares the evidence's declared anchors against caller-supplied *expected* values. That is correct and
unchanged.

The defect lived in the Stage-B wrapper (`tools/hermes_stage_b_build_v1.py`, lines ~1519-1542). It sourced
the expected filesystem digest and the trusted producer reference **from the active-import evidence record
itself** and fed them straight back into the comparator:

```
fs_digest    = ai_evidence.get("image_filesystem_digest") or ai_evidence.get("manifest_digest")
producer_ref = ai_evidence.get("producer_trust_reference")
```

A record proving its own truth by repeating a value is a **tautology**. F-2 requires the expected anchors —
image id, filesystem/manifest digest, and producer authority — to come from **independent governed sources**,
structurally impossible to supply from the active-import evidence.

## The correction

Expected anchors are now derived from an `IndependentAnchorBundle`, assembled from:

- an **image-identity anchor** (`build_image_identity_anchor`) whose image id comes from the **governed build
  result** and is independently confirmed by an **OCI inspection** — the two must match;
- an **image-filesystem anchor** (`build_image_filesystem_anchor`) whose manifest/config/filesystem digests
  come only from the **dedicated OCI inspection producer**;
- a **governed producer registry** (`ProducerRegistry`) whose `resolve()` consults only the registry object,
  never an evidence record, and which rejects self-registration, inline-from-evidence origin, wildcard scope,
  checksum tamper, and mutation after freeze.

The wrapper calls `validate_active_import_against_bundle`, which derives every `expected_*` value from the
bundle and delegates to the unchanged pure comparator. There is **no parameter** on any anchor/bundle builder
through which active-import evidence can flow in — the prevention is structural (type system + absent
parameter), not a value check.

## INERT until a real authorised image exists

- This WO builds **NO real image**, runs **NO Docker/SBOM/scan**, publishes and deploys **NOTHING**, wires
  **NO runtime**, and mutates **NO live state**. All anchors and producers are **FIXTURE** producers whose
  `real_evidence_authority` is `False`.
- **Model-level completion does NOT prove a real candidate.** Passing tests prove the structural prevention
  and the anchor/bundle/registry contracts — not that a real, authorised HERMES image exists.
- A **real Stage-B WO** must independently produce the anchors: a governed build producer (real image-id
  digest), a dedicated OCI/image-inspection producer (real manifest/config/filesystem digests), and a
  separate active-import-analysis producer (real non-running import graph), all registered by an external
  approver with `real_evidence_authority=True`.
- **R2D2 must audit the exact real-image evidence before candidate readiness is asserted.** Realness is a
  governed property of the producer registration, never a self-declared field on the evidence.
