"""FW-08 F2-R2 build/OCI producer-independence tests (§8).

WO-HELM-HERMES-FW08-F2-R2-BUILD-AND-OCI-PRODUCER-INDEPENDENCE-IMPLEMENTATION-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-22. Contract version: 1.

These tests build NO real image, run NO docker/SBOM/scan, publish NOTHING, deploy NOTHING, wire NO runtime,
enable NO shadow, mutate NO live state, and add NO third-party dependency (stdlib only). They prove the
F2-R2 invariant: the governed build-result producer and the dedicated OCI-inspection producer are GENUINELY
INDEPENDENT (BUILD_RESULT_PRODUCER != OCI_INSPECTION_PRODUCER, plus independent evidence provenance).
Independence is DERIVED from bound registrations + evidence, never a caller boolean. Dual-role is FORBIDDEN
and NOT activated (even a valid exception fails closed).

All tests are ADDITIVE. The existing F-2 positive fixtures (which share tool_digest + approval_authority but
DIFFER in tool_identity, evidence_ref and producer_id) MUST still pass — a regression control asserts it.

Run: cd <worktree> && python3 -m pytest tests/test_fw08_f2_r2_producer_independence_v1.py -q
"""
from __future__ import annotations

import copy
import inspect

import pytest

import design.hermes_fw08_producer_registry_v1 as pr
import design.hermes_fw08_producer_independence_v1 as pi
import design.hermes_fw08_image_identity_anchor_v1 as iia
import design.hermes_fw08_anchor_bundle_v1 as anb
import tests.test_fw08_f2_independent_anchors_v1 as f2


APPROVER = f2.APPROVER
REG_POLICY = f2.REG_POLICY
BUILD_PID = f2.BUILD_PID
OCI_PID = f2.OCI_PID
AI_PID = f2.AI_PID


# ============================================================================ registrations helper
def _build_reg(**over):
    return f2._reg(BUILD_PID, "GOVERNED_BUILD_RESULT", ["BUILD_RESULT"], ["STAGE_B_BUILD_RESULT"], **over)


def _oci_reg(**over):
    return f2._reg(OCI_PID, "OCI_IMAGE_INSPECTION", ["OCI_INSPECTION"], ["STAGE_B_OCI_INSPECTION"], **over)


def _dual_reg(producer_id):
    """A producer scoped for BOTH build and OCI roles (claims both roles)."""
    return f2._reg(producer_id, "GOVERNED_BUILD_RESULT",
                   ["BUILD_RESULT", "OCI_INSPECTION"],
                   ["STAGE_B_BUILD_RESULT", "STAGE_B_OCI_INSPECTION"])


def _ev_pair(**over_b):
    return f2._build_result(**over_b), f2._oci_inspection()


# ============================================================================ POSITIVE — genuine independence
def test_positive_independent_producers_pass():
    reasons = pi.evaluate_producer_independence(
        build_result=f2._build_result(), oci_inspection=f2._oci_inspection(),
        build_registration=_build_reg(), oci_registration=_oci_reg())
    assert reasons == (), reasons


def test_positive_shared_tool_digest_and_authority_but_distinct_identity_pass():
    """CONTROL: the existing F-2 fixtures SHARE tool_digest ('t'*64) and approval_authority (APPROVER) but
    DIFFER in tool_identity — they are NOT rejected. This is the node-set-preserving guarantee."""
    b, o = _build_reg(), _oci_reg()
    assert b.tool_digest == o.tool_digest and b.approval_authority == o.approval_authority
    assert b.tool_identity != o.tool_identity
    assert pi.evaluate_producer_independence(
        build_result=f2._build_result(), oci_inspection=f2._oci_inspection(),
        build_registration=b, oci_registration=o) == ()


def test_positive_convenience_independent_true():
    assert pi.build_oci_producers_independent(_build_reg(), _oci_reg()) is True
    assert pi.build_oci_producers_independent(_build_reg(), _build_reg()) is False


# ============================================================================ §8 REJECTIONS
def test_reject_same_producer_id():
    reasons = pi.evaluate_producer_independence(
        build_result=f2._build_result(), oci_inspection=f2._oci_inspection(),
        build_registration=_build_reg(), oci_registration=_build_reg())
    assert "F2R2-SAME-PRODUCER-ID" in reasons and "F2R2-SAME-REGISTRATION" in reasons


def test_reject_same_registration_object():
    reg = _build_reg()
    reasons = pi.evaluate_producer_independence(
        build_result=f2._build_result(), oci_inspection=f2._oci_inspection(),
        build_registration=reg, oci_registration=reg)
    assert "F2R2-SAME-REGISTRATION" in reasons


def test_reject_same_evidence_object():
    shared = f2._build_result()
    reasons = pi.evaluate_producer_independence(
        build_result=shared, oci_inspection=shared,
        build_registration=_build_reg(), oci_registration=_oci_reg())
    assert "F2R2-SAME-EVIDENCE-OBJECT" in reasons


def test_reject_same_evidence_ref():
    reasons = pi.evaluate_producer_independence(
        build_result=f2._build_result(evidence_ref="shared-ref"),
        oci_inspection=f2._oci_inspection(evidence_ref="shared-ref"),
        build_registration=_build_reg(), oci_registration=_oci_reg())
    assert "F2R2-SAME-EVIDENCE-REF" in reasons


def test_reject_missing_evidence_ref():
    reasons = pi.evaluate_producer_independence(
        build_result=f2._build_result(evidence_ref=""), oci_inspection=f2._oci_inspection(),
        build_registration=_build_reg(), oci_registration=_oci_reg())
    assert "F2R2-MISSING-EVIDENCE-REF" in reasons


def test_reject_copied_payload():
    """A build_result deep-copied into oci_inspection under different registration ids is still a clone."""
    base = f2._build_result()
    clone = copy.deepcopy(base)
    reasons = pi.evaluate_producer_independence(
        build_result=base, oci_inspection=clone,
        build_registration=_build_reg(), oci_registration=_oci_reg())
    assert "F2R2-COPIED-PAYLOAD" in reasons
    # (evidence_ref is identical too, so SAME-EVIDENCE-REF also fires — both prove the clone.)
    assert "F2R2-SAME-EVIDENCE-REF" in reasons


def test_reject_subclass_proxy_disguising_same_producer():
    """Identity is by producer_id / registration, NOT class. A subclass registration carrying the SAME
    producer_id is still the same producer."""
    class _ProxyReg(pr.ProducerRegistration):
        pass
    base = _oci_reg()
    proxy = _ProxyReg(**{f.name: getattr(base, f.name) for f in base.__dataclass_fields__.values()})
    # proxy shares BUILD_PID with the build registration -> same producer id.
    proxy = pr.new_registration(
        registry_policy_version=REG_POLICY, producer_id=BUILD_PID, producer_type="OCI_IMAGE_INSPECTION",
        application="hermes", allowed_evidence_types=("OCI_INSPECTION",),
        allowed_gate_ids=("STAGE_B_OCI_INSPECTION",), tool_identity="oci-inspect-tool", tool_version="1",
        tool_digest="t" * 64, source_version=f2.SRC, candidate_binding_policy="EXACT",
        image_binding_policy="EXACT", authority_scope="STAGE_B", creation_utc="2026-07-20T00:00:00+00:00",
        expiry_utc="2027-07-21T00:00:00+00:00", permanent_policy_rationale="", approval_authority=APPROVER,
        audit_reference="AUDIT-F2", real_evidence_authority=False, created_by="fw08-f2-setup",
        origin="GOVERNED_SETUP")
    reasons = pi.evaluate_producer_independence(
        build_result=f2._build_result(), oci_inspection=f2._oci_inspection(),
        build_registration=_build_reg(), oci_registration=proxy)
    assert "F2R2-SAME-PRODUCER-ID" in reasons


def test_reject_dual_role_without_exception():
    """One producer registered for BUILD+OCI evidence types/gates claims both roles."""
    dual = _dual_reg(OCI_PID)
    reasons = pi.evaluate_producer_independence(
        build_result=f2._build_result(), oci_inspection=f2._oci_inspection(),
        build_registration=_build_reg(), oci_registration=dual)
    assert "F2R2-DUAL-ROLE-WITHOUT-EXCEPTION" in reasons


def test_reject_same_tool_and_authority_compound():
    """Compound trigger: reject ONLY when tool_identity AND tool_digest AND approval_authority ALL match.
    Here we FORCE identical tool_identity on both registrations."""
    b = _build_reg(tool_identity="shared-tool")
    o = _oci_reg(tool_identity="shared-tool")
    assert b.tool_digest == o.tool_digest and b.approval_authority == o.approval_authority
    reasons = pi.evaluate_producer_independence(
        build_result=f2._build_result(), oci_inspection=f2._oci_inspection(),
        build_registration=b, oci_registration=o)
    assert "F2R2-SAME-TOOL-AND-AUTHORITY" in reasons


def test_control_shared_digest_authority_distinct_identity_not_rejected():
    """The existing shared-tool_digest-but-distinct-tool_identity fixture is NOT rejected for tool+authority."""
    reasons = pi.evaluate_producer_independence(
        build_result=f2._build_result(), oci_inspection=f2._oci_inspection(),
        build_registration=_build_reg(), oci_registration=_oci_reg())
    assert "F2R2-SAME-TOOL-AND-AUTHORITY" not in reasons


def test_reject_build_relabelled_as_oci_missing_digests():
    """OCI evidence with no manifest/config/filesystem digests of its own = build evidence relabelled."""
    fake_oci = f2._build_result(evidence_ref="oci-relabelled-1")  # a build record wearing an OCI ref
    reasons = pi.evaluate_producer_independence(
        build_result=f2._build_result(), oci_inspection=fake_oci,
        build_registration=_build_reg(), oci_registration=_oci_reg())
    assert "F2R2-OCI-DERIVED-FROM-BUILD" in reasons


def test_reject_oci_derived_from_build_ref():
    reasons = pi.evaluate_producer_independence(
        build_result=f2._build_result(),
        oci_inspection=f2._oci_inspection(derived_from="build-result-evidence-1"),
        build_registration=_build_reg(), oci_registration=_oci_reg())
    assert "F2R2-OCI-DERIVED-FROM-BUILD" in reasons


def test_reject_oci_derived_from_active_import():
    reasons = pi.evaluate_producer_independence(
        build_result=f2._build_result(),
        oci_inspection=f2._oci_inspection(evidence_ref="oci-active_import-tainted"),
        build_registration=_build_reg(), oci_registration=_oci_reg())
    assert "F2R2-OCI-DERIVED-FROM-ACTIVE-IMPORT" in reasons


def test_reject_shared_provenance_ref():
    reasons = pi.evaluate_producer_independence(
        build_result=f2._build_result(),
        oci_inspection=f2._oci_inspection(provenance_ref="build-result-evidence-1"),
        build_registration=_build_reg(), oci_registration=_oci_reg())
    assert "F2R2-SHARED-PROVENANCE-REF" in reasons


def test_reject_registration_wrong_type():
    reasons = pi.evaluate_producer_independence(
        build_result=f2._build_result(), oci_inspection=f2._oci_inspection(),
        build_registration={"not": "a registration"}, oci_registration=_oci_reg())
    assert reasons == ("F2R2-REGISTRATION-WRONG-TYPE",)


# ============================================================================ §8 structural: no boolean
def test_no_independent_boolean_parameter():
    """STRUCTURAL: a caller cannot assert independence — there is no `independent` parameter."""
    params = set(inspect.signature(pi.evaluate_producer_independence).parameters)
    assert "independent" not in params
    assert {"build_result", "oci_inspection", "build_registration", "oci_registration"} <= params


# ============================================================================ §4 dual-role exception (INERT)
def _valid_exception(**over):
    kw = dict(
        policy_record_id="POL-1", build_execution_identity="build-exec", oci_execution_identity="oci-exec",
        build_evidence_ref="build-result-evidence-1", oci_evidence_ref="oci-inspection-evidence-1",
        build_authority_approval="approver-build", oci_authority_approval="approver-oci",
        build_tool_digest="b" * 64, oci_tool_digest="o" * 64, exception_reason="governed dual-role trial",
        approval_authority="external-exception-approver", created_by="fw08-f2-r2-setup")
    kw.update(over)
    return pi.new_dual_role_exception(**kw)


def test_dual_role_exception_absent_forbidden_by_default():
    dual = _dual_reg(OCI_PID)
    reasons = pi.evaluate_producer_independence(
        build_result=f2._build_result(), oci_inspection=f2._oci_inspection(),
        build_registration=_build_reg(), oci_registration=dual, dual_role_exception=None)
    assert "F2R2-DUAL-ROLE-WITHOUT-EXCEPTION" in reasons


def test_dual_role_exception_malformed_propagates_dre():
    dual = _dual_reg(OCI_PID)
    bad = _valid_exception(oci_execution_identity="build-exec")  # same execution
    reasons = pi.evaluate_producer_independence(
        build_result=f2._build_result(), oci_inspection=f2._oci_inspection(),
        build_registration=_build_reg(), oci_registration=dual, dual_role_exception=bad)
    assert "DRE-SAME-EXECUTION" in reasons
    assert "F2R2-DUAL-ROLE-EXCEPTION-NOT-ACTIVATED" not in reasons


def test_dual_role_exception_self_approved_dre():
    exc = _valid_exception(approval_authority="fw08-f2-r2-setup")  # == created_by
    assert "DRE-SELF-APPROVED" in pi.validate_dual_role_exception(exc)
    exc2 = _valid_exception(approval_authority="build-exec")  # == an execution identity
    assert "DRE-SELF-APPROVED" in pi.validate_dual_role_exception(exc2)


def test_dual_role_exception_digest_tamper_dre():
    exc = _valid_exception()
    import dataclasses
    tampered = dataclasses.replace(exc, exception_reason="mutated after digest")
    assert "DRE-DIGEST-TAMPER" in pi.validate_dual_role_exception(tampered)


def test_dual_role_exception_same_tool_without_marker_dre():
    exc = _valid_exception(build_tool_digest="z" * 64, oci_tool_digest="z" * 64)
    assert "DRE-SAME-TOOL" in pi.validate_dual_role_exception(exc)
    # ... unless explicitly marked independently-controlled.
    ok = _valid_exception(build_tool_digest="z" * 64, oci_tool_digest="z" * 64,
                          independently_controlled_execution="two-separately-approved-deployments")
    assert "DRE-SAME-TOOL" not in pi.validate_dual_role_exception(ok)


def test_valid_exception_still_not_activated():
    """A structurally-VALID exception is still rejected — this WO does not activate dual-role."""
    exc = _valid_exception()
    assert pi.validate_dual_role_exception(exc) == ()
    dual = _dual_reg(OCI_PID)
    reasons = pi.evaluate_producer_independence(
        build_result=f2._build_result(), oci_inspection=f2._oci_inspection(),
        build_registration=_build_reg(), oci_registration=dual, dual_role_exception=exc)
    assert "F2R2-DUAL-ROLE-EXCEPTION-NOT-ACTIVATED" in reasons
    assert not any(r.startswith("DRE-") for r in reasons)


# ============================================================================ §6 anchor + bundle integration
def test_anchor_rejects_same_producer():
    reg = f2._registry()
    a, r = f2._identity(reg, build_producer_id=OCI_PID, oci_producer_id=OCI_PID)
    # OCI_PID is not authorised for the build role -> build producer unauthorised; and same-producer fires.
    assert a is None
    assert "II-BUILD-OCI-SAME-PRODUCER" in r


def test_anchor_rejects_copied_payload():
    reg = f2._registry()
    # make the oci inspection a byte-clone of the build result (same evidence_ref + payload).
    a, r = f2._identity(
        reg,
        oci_inspection={"image_id": f2.IMG, "candidate_id": f2.CAND, "source_sha": f2.SRC,
                        "build_utc": f2.UTC, "evidence_ref": "build-result-evidence-1"})
    assert a is None
    assert "F2R2-COPIED-PAYLOAD" in r or "F2R2-SAME-EVIDENCE-REF" in r


def test_anchor_positive_still_succeeds():
    reg = f2._registry()
    a, r = f2._identity(reg)
    assert a is not None and r == (), r


def test_bundle_rejects_ai_reused_as_build():
    reg = f2._registry()
    ia, _ = f2._identity(reg)
    fa, _ = f2._filesystem(reg, ia)
    # register a producer id collision is impossible post-freeze; instead pass ai id == build id.
    b, r = anb.assemble_anchor_bundle(
        image_identity_anchor=ia, image_filesystem_anchor=fa, producer_registry=reg,
        build_producer_id=BUILD_PID, oci_producer_id=OCI_PID, active_import_producer_id=BUILD_PID,
        now_utc=f2.NOW)
    assert b is None and "AB-BUILD-OCI-NOT-INDEPENDENT" in r


def test_bundle_rejects_build_oci_same():
    reg = f2._registry()
    ia, _ = f2._identity(reg)
    fa, _ = f2._filesystem(reg, ia)
    b, r = anb.assemble_anchor_bundle(
        image_identity_anchor=ia, image_filesystem_anchor=fa, producer_registry=reg,
        build_producer_id=OCI_PID, oci_producer_id=OCI_PID, active_import_producer_id=AI_PID,
        now_utc=f2.NOW)
    assert b is None and "AB-BUILD-OCI-NOT-INDEPENDENT" in r


# ============================================================================ regression control
def test_regression_existing_f2_fixtures_still_build():
    """The existing F-2 positive fixtures (shared tool_digest + authority, distinct tool_identity) still
    build an identity anchor AND a full bundle — independence PASSES for them."""
    reg = f2._registry()
    ia, ra = f2._identity(reg)
    assert ia is not None and ra == (), ra
    b, ia2, fa2 = f2._make_bundle(reg)
    assert b is not None and b.provenance.derived_from_active_import is False
