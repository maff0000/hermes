# FW-08 F2-R2 — Build / OCI producer independence

WO-HELM-HERMES-FW08-F2-R2-BUILD-AND-OCI-PRODUCER-INDEPENDENCE-IMPLEMENTATION-0001
Owner: HERMES (Helm). Contract version: 1. Status: INERT (no image build/inspection).

## The invariant

    BUILD_RESULT_PRODUCER != OCI_INSPECTION_PRODUCER

The FW-08 F-2 anchor chain requires TWO governed producers: a governed build-result producer that
*creates* the image-identity claim, and a dedicated OCI/image-inspection producer that *independently
confirms* it. F2-R2 enforces that these are GENUINELY independent, so the same producer / process /
evidence object / evidence reference / provenance / registration cannot both create AND confirm the same
image.

Before F2-R2, `build_image_identity_anchor` composed `producer_identity = f"{build}+{oci}"` — which accepts
`"X+X"` — and `assemble_anchor_bundle` resolved all three producers without an independence check. F2-R2
closes that gap.

## Independence is DERIVED, never a boolean

`evaluate_producer_independence(...)` has **no `independent` parameter**. Independence is derived from the
bound registrations and the bound build-result / OCI-inspection evidence:

- distinct producer ids (`F2R2-SAME-PRODUCER-ID`) and distinct registration records
  (`F2R2-SAME-REGISTRATION`);
- distinct Python evidence objects (`F2R2-SAME-EVIDENCE-OBJECT`), distinct non-empty evidence refs
  (`F2R2-MISSING-EVIDENCE-REF` / `F2R2-SAME-EVIDENCE-REF`), and non-cloned payloads
  (`F2R2-COPIED-PAYLOAD`);
- not the SAME signed tool run under the SAME approver — rejected only when tool_identity **and**
  tool_digest **and** approval_authority ALL coincide (`F2R2-SAME-TOOL-AND-AUTHORITY`). Genuinely
  independent producers may share a signed tool_digest and approver while differing in tool_identity —
  those are NOT rejected;
- the OCI record must carry its OWN manifest/config/filesystem digests and must not be derived from the
  build ref (`F2R2-OCI-DERIVED-FROM-BUILD`) nor from the active-import path
  (`F2R2-OCI-DERIVED-FROM-ACTIVE-IMPORT`);
- neither record's provenance may point at the other's evidence ref (`F2R2-SHARED-PROVENANCE-REF`).

The function returns `()` iff independent (fail-closed: any doubt yields a reason).

## Dual role is FORBIDDEN and NOT activated

Default policy is `DUAL_ROLE_FORBIDDEN`. If either registration is scoped for BOTH the build and OCI
roles, that producer claims both roles: `F2R2-DUAL-ROLE-WITHOUT-EXCEPTION`.

A `DualRoleException` type is **MODELLED but INERT** in this WO. `validate_dual_role_exception` proves its
shape (distinct executions / evidence refs / approvals, distinct tool digests OR an explicit
independently-controlled-execution marker, a reason, a non-self approver, a matching digest). Even a
structurally-valid exception is rejected with `F2R2-DUAL-ROLE-EXCEPTION-NOT-ACTIVATED` — this WO does not
activate dual-role. Activation is deferred to a future governed WO.

## Integration

- `design/hermes_fw08_image_identity_anchor_v1.py` — after resolving both registrations and before minting,
  rejects `II-BUILD-OCI-SAME-PRODUCER` (same id) plus every `F2R2-*` reason.
- `design/hermes_fw08_anchor_bundle_v1.py` — enforces distinct build/oci ids + registrations and an ai
  producer distinct from both (`AB-BUILD-OCI-NOT-INDEPENDENT`).
- `tools/hermes_stage_b_build_v1.py` — an explicit independence gate runs BEFORE anchor construction when
  build-result / OCI-inspection artifacts are present; a failure rejects with
  `PRODUCER-INDEPENDENCE-FAILED:...`. When artifacts are absent (default runner) the block is skipped and
  behaviour is byte-identical to before.

## Status

INERT / TEST_ONLY. No image is built or inspected. Real-image proof and governed dual-role activation
remain outstanding (F2-R3).
