"""FW-08 F2-R3-FUT-2 build + OCI-inspection producer-registration tests (§6).

WO-HELM-HERMES-FW08-F2-R3-FUT-2-BUILD-AND-OCI-INSPECTION-PRODUCER-REGISTRATION-CONTRACT-IMPLEMENTATION-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-28. Contract version: 1.

These tests register NO real producer, activate NO registry, build/inspect NO image, wire NO runtime, create
NO key/secret/credential, and add NO third-party dependency (stdlib only). They prove the FUT-2 contract:

    real producer registration is UNAVAILABLE and FAILS CLOSED; a build producer and an OCI-inspection producer
    are structurally separated (distinct roles, distinct executions, disjoint evidence classes); no single
    primitive (caller classification / Boolean / local seal) is the trust root for real acceptance.

All tests are ADDITIVE and PASS.

Run: cd <worktree> && python3 -m pytest tests/test_fw08_producer_registration_v1.py -q
"""
from __future__ import annotations

import ast
import copy
import dataclasses
import re

import pytest

import design.hermes_fw08_producer_registration_v1 as pr
import design.hermes_fw08_execution_identity_v1 as ei
import design.hermes_fw08_approval_record_v1 as apr


NOW = "2026-07-28T00:00:00+00:00"
VALIDITY_END = "2026-07-29T00:00:00+00:00"
PAST = "2026-07-27T00:00:00+00:00"

MODULE_PATH = pr.__file__
JSON_PATH = "docs/design/fw08/f2_r3_fut2_producer_registration.v1.json"
MD_PATH = "docs/design/fw08/F2_R3_FUT2_PRODUCER_REGISTRATION.md"


# ============================================================================ fixtures
def _build_kwargs(**overrides):
    kw = dict(
        role="BUILD_PRODUCER",
        lifecycle_stage=4,
        registration_id="reg-build-01",
        producer_identity_reference="ref://producer/build-01",
        execution_identity_reference="ref://build-producer/execution/exec-build-01",
        executable_identity_reference="ref://executable/build-tool-01",
        authority_root_reference="ref://authority-root/build-domain",
        approval_record_reference="ref://approval/build-01",
        now_utc=NOW,
        validity_end_utc=VALIDITY_END,
        provenance="SYNTHETIC_TEST",
        evidence_references=("ref://evidence/build-evidence/ev-04#reg=reg-build-01",),
        created_by="ref://approver/external-01",
    )
    kw.update(overrides)
    return kw


def _oci_kwargs(**overrides):
    kw = dict(
        role="OCI_INSPECTION_PRODUCER",
        lifecycle_stage=5,
        registration_id="reg-oci-01",
        producer_identity_reference="ref://producer/oci-01",
        execution_identity_reference="ref://oci-producer/execution/exec-oci-01",
        executable_identity_reference="ref://executable/oci-tool-01",
        authority_root_reference="ref://authority-root/oci-domain",
        approval_record_reference="ref://approval/oci-01",
        now_utc=NOW,
        validity_end_utc=VALIDITY_END,
        provenance="SYNTHETIC_TEST",
        evidence_references=("ref://evidence/oci-inspection-evidence/ev-05#reg=reg-oci-01",),
        created_by="ref://approver/external-02",
    )
    kw.update(overrides)
    return kw


def _make_build(**overrides):
    rec, reasons = pr.new_synthetic_registration(**_build_kwargs(**overrides))
    assert reasons == (), reasons
    assert rec is not None
    return rec


def _make_oci(**overrides):
    rec, reasons = pr.new_synthetic_registration(**_oci_kwargs(**overrides))
    assert reasons == (), reasons
    assert rec is not None
    return rec


def _exec(**overrides):
    kw = dict(
        application="hermes",
        execution_id="exec-build-01",
        producer_id="build-01",
        producer_registration_ref="ref://reg/build-01",
        process_identity="proc-build-01",
        runner_identity="runner-build-01",
        host_container_identity="host-build-01",
        tool_identity="build-tool",
        executable_digest="sha256:buildexec",
        invocation_digest="sha256:buildinv",
        start_utc=NOW,
        completion_utc=NOW,
        authority_reference="ref://authority/build",
        evidence_output_reference="ref://evidence/build-evidence/ev-04",
    )
    kw.update(overrides)
    return ei.new_execution_identity(**kw)


def _build_exec():
    return _exec()


def _oci_exec():
    return _exec(
        execution_id="exec-oci-01", producer_id="oci-01",
        producer_registration_ref="ref://reg/oci-01", process_identity="proc-oci-01",
        runner_identity="runner-oci-01", host_container_identity="host-oci-01",
        tool_identity="oci-tool", executable_digest="sha256:ociexec",
        invocation_digest="sha256:ociinv", authority_reference="ref://authority/oci",
        evidence_output_reference="ref://evidence/oci-inspection-evidence/ev-05")


# ============================================================================ positive synthetic
def test_synthetic_build_registration_accepted_in_test_only():
    rec = _make_build()
    assert pr.validate_producer_registration(rec, now_utc=NOW, real_mode=False) == ()
    assert rec.producer_role == "BUILD_PRODUCER"
    assert rec.permitted_evidence_class == "BUILD_EVIDENCE"
    assert rec.forbidden_evidence_classes == ("OCI_INSPECTION_EVIDENCE",)
    assert rec.synthetic_or_real_classification == "SYNTHETIC"
    assert rec.status == "SYNTHETIC_UNREGISTERED"
    assert rec.lifecycle_stage == 4


def test_synthetic_oci_registration_accepted_in_test_only():
    rec = _make_oci()
    assert pr.validate_producer_registration(rec, now_utc=NOW, real_mode=False) == ()
    assert rec.permitted_evidence_class == "OCI_INSPECTION_EVIDENCE"
    assert rec.forbidden_evidence_classes == ("BUILD_EVIDENCE",)
    assert rec.lifecycle_stage == 5


def test_positive_distinct_pair_validates_and_real_rejects():
    build = _make_build()
    oci = _make_oci()
    # each role correct, distinct executions, distinct evidence classes.
    assert pr.validate_producer_registration(build, now_utc=NOW, real_mode=False) == ()
    assert pr.validate_producer_registration(oci, now_utc=NOW, real_mode=False) == ()
    assert pr.evaluate_producer_registration_distinctness(
        build, oci, build_execution=_build_exec(), oci_execution=_oci_exec()) == ()
    # independent approver each.
    build_ap = {"approver_id": "ref://approver/external-01", "approver_execution": "ref://x/other-exec"}
    assert pr.validate_registration_approval(build, approval_record=build_ap, now_utc=NOW) == ()
    # the SAME registration under REAL_CANDIDATE rejects.
    rb, reasons_b = pr.register_producer(mode="REAL_CANDIDATE", activated_registry_handle=None,
                                         activation_authority=None, **_build_kwargs())
    assert rb is None
    assert "PR-FUT2-REAL-REGISTRATION-UNAVAILABLE" in reasons_b


# ============================================================================ §4G production unavailable
def test_production_producer_registry_unconfigured():
    assert pr.production_producer_registry_configured() is False


def test_configured_registry_authority_identity_is_none():
    assert pr.configured_registry_authority_identity() is None


def test_real_candidate_always_fails_closed():
    rec, reasons = pr.register_producer(mode="REAL_CANDIDATE", activated_registry_handle=None,
                                        activation_authority=None, **_build_kwargs())
    assert rec is None
    assert "PR-FUT2-REAL-REGISTRATION-UNAVAILABLE" in reasons
    assert "F2R3-FUT2-REAL-PRODUCER-REGISTRATION-NOT-CONFIGURED" in reasons


def test_synthetic_requires_module_token():
    rec, reasons = pr.register_producer(mode="TEST_ONLY", activated_registry_handle=None,
                                        activation_authority=None, **_build_kwargs())
    assert rec is None
    assert reasons == ("PR-FUT2-SYNTHETIC-REQUIRES-MODULE-TOKEN",)


def test_mint_helper_requires_module_token():
    with pytest.raises(pr.ProducerRegistrationForbidden):
        pr.new_synthetic_producer_registration(**_build_kwargs())


def test_mint_helper_refuses_real_classification():
    with pytest.raises(pr.ProducerRegistrationForbidden):
        pr.new_synthetic_producer_registration(
            test_token=pr._REGISTRATION_TOKEN, synthetic_or_real_classification="REAL", **_build_kwargs())


# ============================================================================ registration-authority attacks
def test_caller_created_registration_direct_dataclass_seal_invalid():
    # a caller directly constructs a record (no module seal) -> seal invalid, not accepted.
    ref = _make_build()
    fields = {f.name: getattr(ref, f.name) for f in dataclasses.fields(ref)}
    fields["registration_seal"] = "0" * 64
    forged = pr.ProducerRegistrationRecord(**fields)
    reasons = pr.validate_producer_registration(forged, now_utc=NOW, real_mode=False)
    assert "PR-FUT2-SEAL-INVALID" in reasons


def test_relabelled_role_breaks_seal():
    rec = _make_build()
    relabelled = dataclasses.replace(rec, producer_role="OCI_INSPECTION_PRODUCER")
    reasons = pr.validate_producer_registration(relabelled, now_utc=NOW, real_mode=False)
    # body tamper breaks digest, and evidence class no longer matches role.
    assert "PR-FUT2-DIGEST-TAMPER" in reasons or "PR-FUT2-SEAL-INVALID" in reasons


def test_copied_registration_still_synthetic_and_valid_but_real_rejects():
    rec = _make_build()
    for variant in (copy.copy(rec), copy.deepcopy(rec)):
        assert pr.validate_producer_registration(variant, now_utc=NOW, real_mode=False) == ()
        # real mode still fails on the registry-authority gate.
        assert "PR-FUT2-REGISTRY-AUTHORITY-NOT-CONFIGURED" in \
            pr.validate_producer_registration(variant, now_utc=NOW, real_mode=True)


def test_synthetic_passed_as_real_rejected():
    rec = _make_build()
    reasons = pr.validate_producer_registration(rec, now_utc=NOW, real_mode=True)
    assert "PR-FUT2-SYNTHETIC-IN-REAL-MODE" in reasons


def test_digest_tamper_then_reseal_seal_invalid():
    rec = _make_build()
    tampered = dataclasses.replace(rec, authority_root_reference="ref://attacker/swapped")
    reasons = pr.validate_producer_registration(tampered, now_utc=NOW, real_mode=False)
    assert "PR-FUT2-DIGEST-TAMPER" in reasons
    # attacker recomputes the digest to hide the body tamper -> the module seal (module key) still fails.
    forged = dataclasses.replace(tampered, registration_digest=tampered.recompute_digest())
    forged_reasons = pr.validate_producer_registration(forged, now_utc=NOW, real_mode=False)
    assert "PR-FUT2-DIGEST-TAMPER" not in forged_reasons
    assert "PR-FUT2-SEAL-INVALID" in forged_reasons


def test_subclass_cannot_relabel_synthetic_to_real():
    rec = _make_build()

    class ProxyRecord(pr.ProducerRegistrationRecord):
        pass

    fields = {f.name: getattr(rec, f.name) for f in dataclasses.fields(rec)}
    fields["synthetic_or_real_classification"] = "REAL"
    proxy = ProxyRecord(**fields)
    assert pr._MODULE_ISSUER.verify(proxy) is False
    assert "PR-FUT2-SEAL-INVALID" in pr.validate_producer_registration(proxy, now_utc=NOW, real_mode=False)
    # and the real-status/real-classification path is caught by the registry gate too.
    assert "PR-FUT2-REGISTRY-AUTHORITY-NOT-CONFIGURED" in \
        pr.validate_producer_registration(proxy, now_utc=NOW, real_mode=True)


def test_object_new_direct_construction_real_mode_fails_closed():
    base = _make_build()
    obj = object.__new__(pr.ProducerRegistrationRecord)
    for f in dataclasses.fields(base):
        object.__setattr__(obj, f.name, getattr(base, f.name))
    object.__setattr__(obj, "synthetic_or_real_classification", "REAL")
    object.__setattr__(obj, "status", "REAL_REGISTERED")
    object.__setattr__(obj, "registration_digest", obj.recompute_digest())
    object.__setattr__(obj, "registration_seal", pr._MODULE_ISSUER.seal_registration(obj))
    reasons = pr.validate_producer_registration(obj, now_utc=NOW, real_mode=True)
    assert "PR-FUT2-REGISTRY-AUTHORITY-NOT-CONFIGURED" in reasons


def test_vars_mutation_rejected():
    rec = _make_build()
    # frozen dataclass: object.__setattr__ bypass then validate -> broken seal.
    obj = object.__new__(pr.ProducerRegistrationRecord)
    for f in dataclasses.fields(rec):
        object.__setattr__(obj, f.name, getattr(rec, f.name))
    object.__setattr__(obj, "producer_identity_reference", "ref://producer/swapped")
    assert "PR-FUT2-SEAL-INVALID" in pr.validate_producer_registration(obj, now_utc=NOW, real_mode=False) \
        or "PR-FUT2-DIGEST-TAMPER" in pr.validate_producer_registration(obj, now_utc=NOW, real_mode=False)


def test_reflective_issuer_reseal_still_fails_real_on_registry_gate():
    # a caller reaches the reflective module issuer and mints a STRUCTURALLY VALID, CORRECTLY SEALED 'REAL'
    # record. It must STILL fail real-mode validation — because the production registry authority is
    # unavailable, NOT because the seal is invalid.
    base = _make_build()
    real = dataclasses.replace(base, synthetic_or_real_classification="REAL", status="SYNTHETIC_UNREGISTERED")
    real = dataclasses.replace(real, registration_digest=real.recompute_digest())
    real = dataclasses.replace(real, registration_seal=pr._MODULE_ISSUER.seal_registration(real))
    assert pr._MODULE_ISSUER.verify(real) is True            # the seal IS valid (correctly minted)
    reasons = pr.validate_producer_registration(real, now_utc=NOW, real_mode=True)
    assert "PR-FUT2-REGISTRY-AUTHORITY-NOT-CONFIGURED" in reasons
    assert "PR-FUT2-SEAL-INVALID" not in reasons


def test_raw_registry_without_activated_handle_rejected_real():
    # a real record with NO activated handle reference is not governed.
    base = _make_build()
    real = dataclasses.replace(base, synthetic_or_real_classification="REAL",
                               activated_registry_handle_reference="")
    real = dataclasses.replace(real, registration_digest=real.recompute_digest())
    real = dataclasses.replace(real, registration_seal=pr._MODULE_ISSUER.seal_registration(real))
    reasons = pr.validate_producer_registration(real, now_utc=NOW, real_mode=True)
    assert "PR-FUT2-REGISTRY-AUTHORITY-NOT-CONFIGURED" in reasons
    assert "PR-FUT2-AUTHORITY-NOT-GOVERNED" in reasons


def test_forged_activated_handle_reference_does_not_confer_authority(monkeypatch):
    # even if the caller stuffs the handle reference with a value they hope matches, the module-controlled
    # configured identity is None -> not-configured; and if we monkeypatch registry configured True, the
    # identity is STILL None so no caller-supplied handle value ever matches.
    base = _make_build()
    real = dataclasses.replace(base, synthetic_or_real_classification="REAL",
                               activated_registry_handle_reference="ref://caller/whatever-they-want")
    real = dataclasses.replace(real, registration_digest=real.recompute_digest())
    real = dataclasses.replace(real, registration_seal=pr._MODULE_ISSUER.seal_registration(real))
    monkeypatch.setattr(pr, "production_producer_registry_configured", lambda: True)
    reasons = pr.validate_producer_registration(real, now_utc=NOW, real_mode=True)
    assert "PR-FUT2-REGISTRY-AUTHORITY-NOT-CONFIGURED" in reasons


def test_expired_and_revoked_and_non_utc_rejected():
    exp = _make_build(validity_end_utc=PAST)
    assert "PR-FUT2-EXPIRED" in pr.validate_producer_registration(exp, now_utc=NOW, real_mode=False)
    nonutc = _make_build(now_utc="2026-07-28T00:00:00")
    assert "PR-FUT2-UTC-INVALID" in pr.validate_producer_registration(nonutc, now_utc=NOW, real_mode=False)
    rec = _make_build()
    rev = dataclasses.replace(rec, revocation_state="REVOKED")
    assert "PR-FUT2-REVOKED" in pr.validate_producer_registration(rev, now_utc=NOW, real_mode=False)


def test_missing_provenance_and_refs_rejected():
    nop = _make_build(provenance="")
    assert "PR-FUT2-PROVENANCE-MISSING" in pr.validate_producer_registration(nop, now_utc=NOW, real_mode=False)
    for field, code in (
        ("producer_identity_reference", "PR-FUT2-PRODUCER-REF-MISSING"),
        ("execution_identity_reference", "PR-FUT2-EXECUTION-REF-MISSING"),
        ("executable_identity_reference", "PR-FUT2-EXECUTABLE-REF-MISSING"),
        ("authority_root_reference", "PR-FUT2-AUTHORITY-REF-MISSING"),
        ("approval_record_reference", "PR-FUT2-APPROVAL-REF-MISSING"),
    ):
        rec = _make_build(**{field: ""})
        assert code in pr.validate_producer_registration(rec, now_utc=NOW, real_mode=False)


def test_wrong_type_rejected():
    assert pr.validate_producer_registration(object(), now_utc=NOW, real_mode=False) == ("PR-FUT2-WRONG-TYPE",)


def test_bad_role_rejected():
    rec = _make_build()
    bad = dataclasses.replace(rec, producer_role="TOTALLY_BOGUS")
    reasons = pr.validate_producer_registration(bad, now_utc=NOW, real_mode=False)
    assert "PR-FUT2-BAD-ROLE" in reasons or "PR-FUT2-SEAL-INVALID" in reasons


def test_wrong_stage_for_role_rejected():
    # build producer minted at stage 5 (wrong) — body tamper also breaks seal, but stage is checked.
    rec = _make_build()
    bad = dataclasses.replace(rec, lifecycle_stage=5)
    assert "PR-FUT2-WRONG-STAGE" in pr.validate_producer_registration(bad, now_utc=NOW, real_mode=False)


# ============================================================================ DECISIVE (FUT-1 lesson)
def _decisive_real_forge_closed():
    """Reflectively forge a correctly-sealed REAL registration record, monkeypatch
    production_producer_registry_configured -> True, validate in real_mode, and return True iff it is rejected
    on PR-FUT2-REGISTRY-AUTHORITY-NOT-CONFIGURED (the module-controlled NON-Boolean identity anchor, which is
    None). Proves the Boolean is not the sole root and the local seal is not the sole root."""
    base = _make_build()
    real = dataclasses.replace(base, synthetic_or_real_classification="REAL",
                               activated_registry_handle_reference="ref://caller/forged-handle")
    real = dataclasses.replace(real, registration_digest=real.recompute_digest())
    real = dataclasses.replace(real, registration_seal=pr._MODULE_ISSUER.seal_registration(real))
    assert pr._MODULE_ISSUER.verify(real) is True

    saved = pr.production_producer_registry_configured
    try:
        pr.production_producer_registry_configured = lambda: True  # type: ignore[assignment]
        reasons = pr.validate_producer_registration(real, now_utc=NOW, real_mode=True)
    finally:
        pr.production_producer_registry_configured = saved  # type: ignore[assignment]
    return "PR-FUT2-REGISTRY-AUTHORITY-NOT-CONFIGURED" in reasons and \
        "PR-FUT2-SEAL-INVALID" not in reasons and pr.configured_registry_authority_identity() is None


def test_decisive_real_forge_is_closed():
    assert _decisive_real_forge_closed() is True


def test_register_producer_real_candidate_decisive():
    rec, reasons = pr.register_producer(mode="REAL_CANDIDATE", activated_registry_handle=None,
                                        activation_authority=None, **_build_kwargs())
    assert rec is None
    assert "PR-FUT2-REAL-REGISTRATION-UNAVAILABLE" in reasons


def test_boolean_alone_is_not_the_root(monkeypatch):
    # registry configured True but the module-controlled identity is None -> still rejected.
    base = _make_build()
    real = dataclasses.replace(base, synthetic_or_real_classification="REAL")
    real = dataclasses.replace(real, registration_digest=real.recompute_digest())
    real = dataclasses.replace(real, registration_seal=pr._MODULE_ISSUER.seal_registration(real))
    monkeypatch.setattr(pr, "production_producer_registry_configured", lambda: True)
    reasons = pr.validate_producer_registration(real, now_utc=NOW, real_mode=True)
    assert "PR-FUT2-REGISTRY-AUTHORITY-NOT-CONFIGURED" in reasons


def test_seal_alone_is_not_the_root():
    # a perfectly valid seal over a REAL record does not grant real acceptance.
    base = _make_build()
    real = dataclasses.replace(base, synthetic_or_real_classification="REAL")
    real = dataclasses.replace(real, registration_digest=real.recompute_digest())
    real = dataclasses.replace(real, registration_seal=pr._MODULE_ISSUER.seal_registration(real))
    assert pr._MODULE_ISSUER.verify(real) is True
    reasons = pr.validate_producer_registration(real, now_utc=NOW, real_mode=True)
    assert reasons != ()
    assert "PR-FUT2-REGISTRY-AUTHORITY-NOT-CONFIGURED" in reasons


# ============================================================================ §4E approval
def test_producer_self_approval_rejected():
    rec = _make_build()
    ap = {"approver_id": rec.producer_identity_reference}
    assert "PR-FUT2-SELF-APPROVAL" in pr.validate_registration_approval(rec, approval_record=ap, now_utc=NOW)


def test_producer_approver_alias_self_approval_rejected():
    rec = _make_build(producer_identity_reference="ref://producer/build-alpha")
    ap = {"approver_id": "PROXY:ref://producer/build-alpha"}
    assert "PR-FUT2-SELF-APPROVAL" in pr.validate_registration_approval(rec, approval_record=ap, now_utc=NOW)


def test_approver_not_independent_shared_execution_executable_authority():
    rec = _make_build()
    for field in ("approver_execution", "approver_executable", "approver_authority"):
        shared = {
            "approver_execution": rec.execution_identity_reference,
            "approver_executable": rec.executable_identity_reference,
            "approver_authority": rec.authority_root_reference,
        }
        ap = {"approver_id": "ref://approver/x", field: shared[field]}
        assert "PR-FUT2-APPROVER-NOT-INDEPENDENT" in \
            pr.validate_registration_approval(rec, approval_record=ap, now_utc=NOW)


def test_approval_binding_to_another_producer_or_role_rejected():
    rec = _make_build()
    ap_prod = {"approver_id": "ref://approver/x", "bound_producer_identity": "ref://producer/OTHER"}
    assert "PR-FUT2-APPROVAL-BINDING" in pr.validate_registration_approval(rec, approval_record=ap_prod, now_utc=NOW)
    ap_role = {"approver_id": "ref://approver/x", "bound_role": "OCI_INSPECTION_PRODUCER"}
    assert "PR-FUT2-APPROVAL-BINDING" in pr.validate_registration_approval(rec, approval_record=ap_role, now_utc=NOW)


def test_approval_copied_relabelled_via_digest_tamper():
    rec = _make_build()
    record = apr.new_approval(
        approver_id="ref://approver/x", approver_role="external", authority_root_id="ar",
        registry_id="reg-build-01", registry_digest="d", policy_version="1",
        approval_decision="APPROVE", approval_utc=NOW, expiry_utc=VALIDITY_END,
        approval_evidence_reference="ref://ev")
    tampered = dataclasses.replace(record, approver_id="ref://approver/y")  # breaks digest
    assert "PR-FUT2-APPROVAL-COPIED" in \
        pr.validate_registration_approval(rec, approval_record=tampered, now_utc=NOW)


def test_approval_expired_and_revoked():
    rec = _make_build()
    exp = {"approver_id": "ref://approver/x", "expiry_utc": PAST}
    assert "PR-FUT2-APPROVAL-EXPIRED" in pr.validate_registration_approval(rec, approval_record=exp, now_utc=NOW)
    revd = {"approver_id": "ref://approver/x", "revoked": "TRUE"}
    assert "PR-FUT2-APPROVAL-REVOKED" in pr.validate_registration_approval(rec, approval_record=revd, now_utc=NOW)


def test_approval_record_delegation_valid():
    rec = _make_build(registration_id="reg-build-77")
    record = apr.new_approval(
        approver_id="ref://approver/external-independent", approver_role="external", authority_root_id="ar",
        registry_id="reg-build-77", registry_digest="d", policy_version="1",
        approval_decision="APPROVE", approval_utc=NOW, expiry_utc=VALIDITY_END,
        approval_evidence_reference="ref://ev")
    assert pr.validate_registration_approval(rec, approval_record=record, now_utc=NOW) == ()


def test_approval_wrong_type_rejected():
    rec = _make_build()
    assert pr.validate_registration_approval(rec, approval_record=object(), now_utc=NOW) \
        == ("PR-FUT2-APPROVAL-WRONG-TYPE",)


# ============================================================================ §4C distinctness
def test_distinct_pair_ok():
    assert pr.evaluate_producer_registration_distinctness(
        _make_build(), _make_oci(), build_execution=_build_exec(), oci_execution=_oci_exec()) == ()


def test_same_producer_identity_rejected():
    build = _make_build()
    oci = _make_oci(producer_identity_reference=build.producer_identity_reference)
    reasons = pr.evaluate_producer_registration_distinctness(
        build, oci, build_execution=_build_exec(), oci_execution=_oci_exec())
    assert "PR-FUT2-SAME-PRODUCER-IDENTITY" in reasons


def test_same_execution_executable_authority_registration_id_rejected():
    build = _make_build()
    for field, code in (
        ("execution_identity_reference", "PR-FUT2-SAME-EXECUTION-IDENTITY"),
        ("executable_identity_reference", "PR-FUT2-SAME-EXECUTABLE-IDENTITY"),
        ("authority_root_reference", "PR-FUT2-SAME-AUTHORITY-ROOT"),
        ("registration_id", "PR-FUT2-SAME-REGISTRATION-ID"),
    ):
        oci = _make_oci(**{field: getattr(build, field)})
        reasons = pr.evaluate_producer_registration_distinctness(
            build, oci, build_execution=_build_exec(), oci_execution=_oci_exec())
        assert code in reasons, (field, reasons)


def test_role_mismatch_rejected():
    # two build registrations (roles wrong for the distinctness contract).
    build = _make_build()
    build2 = _make_build(registration_id="reg-build-02", producer_identity_reference="ref://producer/build-02",
                         execution_identity_reference="ref://build-producer/execution/exec-build-02",
                         executable_identity_reference="ref://executable/build-tool-02",
                         evidence_references=("ref://evidence/build-evidence/ev-04b#reg=reg-build-02",))
    reasons = pr.evaluate_producer_registration_distinctness(
        build, build2, build_execution=_build_exec(),
        oci_execution=_exec(execution_id="exec-build-02", producer_id="build-02",
                            process_identity="proc-b2", runner_identity="runner-b2",
                            host_container_identity="host-b2", executable_digest="sha256:b2"))
    assert "PR-FUT2-ROLE-MISMATCH" in reasons


def test_common_executable_and_authority_surfaced():
    # build and OCI executions share executable_digest + authority + runner -> EI-COMMON-EXECUTABLE-AND-AUTHORITY
    build = _make_build()
    oci = _make_oci()
    shared_build_exec = _exec(executable_digest="sha256:shared", authority_reference="ref://authority/shared",
                              runner_identity="runner-shared")
    shared_oci_exec = _exec(execution_id="exec-oci-01", producer_id="oci-01",
                            process_identity="proc-oci-01", host_container_identity="host-oci-01",
                            executable_digest="sha256:shared", authority_reference="ref://authority/shared",
                            runner_identity="runner-shared",
                            evidence_output_reference="ref://evidence/oci-inspection-evidence/ev-05")
    reasons = pr.evaluate_producer_registration_distinctness(
        build, oci, build_execution=shared_build_exec, oci_execution=shared_oci_exec)
    assert "EI-COMMON-EXECUTABLE-AND-AUTHORITY" in reasons


def test_labels_differing_is_not_sufficient():
    # producer ids / tool names differ but executable+authority+runner coincide -> still rejected.
    build = _make_build()
    oci = _make_oci()
    b = _exec(tool_identity="build-tool-NAME", producer_id="build-XYZ",
              executable_digest="sha256:same", authority_reference="ref://authority/same",
              runner_identity="runner-same")
    o = _exec(execution_id="exec-oci-01", producer_id="oci-ABC", process_identity="proc-oci-01",
              host_container_identity="host-oci-01", tool_identity="oci-tool-NAME",
              executable_digest="sha256:same", authority_reference="ref://authority/same",
              runner_identity="runner-same",
              evidence_output_reference="ref://evidence/oci-inspection-evidence/ev-05")
    reasons = pr.evaluate_producer_registration_distinctness(build, oci, build_execution=b, oci_execution=o)
    assert "EI-COMMON-EXECUTABLE-AND-AUTHORITY" in reasons


def test_one_execution_both_roles_surfaced():
    build = _make_build()
    oci = _make_oci()
    shared_output = "ref://evidence/shared/ev"
    b = _exec(evidence_output_reference=shared_output)
    o = _exec(execution_id="exec-oci-01", producer_id="oci-01", process_identity="proc-oci-01",
              runner_identity="runner-oci-01", host_container_identity="host-oci-01",
              executable_digest="sha256:ociexec", authority_reference="ref://authority/oci",
              evidence_output_reference=shared_output)
    reasons = pr.evaluate_producer_registration_distinctness(build, oci, build_execution=b, oci_execution=o)
    assert "EI-ONE-EXECUTION-BOTH-ROLES" in reasons


def test_aliased_identity_rejected():
    build = _make_build(producer_identity_reference="ref://producer/shared-name")
    oci = _make_oci(producer_identity_reference="PROXY:ref://producer/shared-name")
    reasons = pr.evaluate_producer_registration_distinctness(
        build, oci, build_execution=_build_exec(), oci_execution=_oci_exec())
    assert "PR-FUT2-ALIASED-IDENTITY" in reasons


def test_dual_role_registration_rejected_in_distinctness():
    build = _make_build()
    # forge an OCI record whose forbidden set does NOT exclude the other class (claims both) — break seal but
    # distinctness still surfaces DUAL-ROLE from the structure.
    oci = _make_oci()
    dual = dataclasses.replace(oci, forbidden_evidence_classes=())
    reasons = pr.evaluate_producer_registration_distinctness(
        build, dual, build_execution=_build_exec(), oci_execution=_oci_exec())
    assert "PR-FUT2-DUAL-ROLE" in reasons


def test_distinctness_wrong_type():
    assert pr.evaluate_producer_registration_distinctness(
        object(), _make_oci(), build_execution=_build_exec(), oci_execution=_oci_exec()) \
        == ("PR-FUT2-WRONG-TYPE",)


# ============================================================================ §4B dual-role / evidence class
def test_dual_role_via_forbidden_set_tamper():
    rec = _make_build()
    dual = dataclasses.replace(rec, forbidden_evidence_classes=("OCI_INSPECTION_EVIDENCE", "BUILD_EVIDENCE"))
    reasons = pr.validate_producer_registration(dual, now_utc=NOW, real_mode=False)
    assert "PR-FUT2-DUAL-ROLE" in reasons or "PR-FUT2-SEAL-INVALID" in reasons


def test_evidence_class_mismatch():
    rec = _make_build()
    bad = dataclasses.replace(rec, permitted_evidence_class="OCI_INSPECTION_EVIDENCE")
    reasons = pr.validate_producer_registration(bad, now_utc=NOW, real_mode=False)
    assert "PR-FUT2-EVIDENCE-CLASS-MISMATCH" in reasons or "PR-FUT2-SEAL-INVALID" in reasons


def test_evidence_ref_wrong_class_violation():
    # a build producer carrying an OCI-class evidence reference.
    rec = _make_build(evidence_references=("ref://evidence/oci-inspection-evidence/ev-x#reg=reg-build-01",))
    assert "PR-FUT2-EVIDENCE-CLASS-VIOLATION" in pr.validate_producer_registration(rec, now_utc=NOW, real_mode=False)


def test_evidence_ref_bound_elsewhere():
    rec = _make_build(evidence_references=("ref://evidence/build-evidence/ev-x#reg=reg-OTHER",))
    assert "PR-FUT2-EVIDENCE-BOUND-ELSEWHERE" in pr.validate_producer_registration(rec, now_utc=NOW, real_mode=False)


def test_inline_payload_as_reference_rejected():
    rec = _make_build(evidence_references=("-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----",))
    assert "PR-FUT2-REFERENCE-IS-INLINE-PAYLOAD" in \
        pr.validate_producer_registration(rec, now_utc=NOW, real_mode=False)


def test_noncanonical_reference_rejected():
    for evref in ("ref://evidence/build-evidence/..%2f..%2fetc",
                  "ref://evidence/build-evidence/../../etc",
                  "ref://evidence/build-evidence//double"):
        rec = _make_build(evidence_references=(evref + "#reg=reg-build-01",))
        assert "PR-FUT2-REFERENCE-NONCANONICAL" in \
            pr.validate_producer_registration(rec, now_utc=NOW, real_mode=False), evref


# ============================================================================ §4F evidence separation
def test_build_producer_limited_to_build_evidence():
    build = _make_build()
    assert pr.validate_evidence_class(build, "BUILD_EVIDENCE") == ()
    assert "PR-FUT2-EVIDENCE-CLASS-VIOLATION" in pr.validate_evidence_class(build, "OCI_INSPECTION_EVIDENCE")


def test_oci_producer_limited_to_oci_evidence():
    oci = _make_oci()
    assert pr.validate_evidence_class(oci, "OCI_INSPECTION_EVIDENCE") == ()
    assert "PR-FUT2-EVIDENCE-CLASS-VIOLATION" in pr.validate_evidence_class(oci, "BUILD_EVIDENCE")


def test_build_emitting_oci_rejected():
    build = _make_build()
    assert "PR-FUT2-EVIDENCE-CLASS-VIOLATION" in pr.validate_evidence_class(build, "OCI_INSPECTION_EVIDENCE")


def test_oci_emitting_build_rejected():
    oci = _make_oci()
    assert "PR-FUT2-EVIDENCE-CLASS-VIOLATION" in pr.validate_evidence_class(oci, "BUILD_EVIDENCE")


def test_oci_repackages_build_execution_rejected():
    oci = _make_oci()
    reasons = pr.validate_evidence_class(
        oci, "OCI_INSPECTION_EVIDENCE",
        source_execution_ref="ref://build-producer/execution/exec-build-01")
    assert "PR-FUT2-OCI-REPACKAGES-BUILD" in reasons


def test_evidence_class_inline_and_noncanonical_and_wrongtype():
    oci = _make_oci()
    assert "PR-FUT2-REFERENCE-IS-INLINE-PAYLOAD" in \
        pr.validate_evidence_class(oci, "{\"inline\":true}")
    assert "PR-FUT2-REFERENCE-NONCANONICAL" in \
        pr.validate_evidence_class(oci, "ref://evidence/oci-inspection-evidence/..%2fetc")
    assert pr.validate_evidence_class(object(), "BUILD_EVIDENCE") == ("PR-FUT2-WRONG-TYPE",)


def test_evidence_class_bad_role():
    rec = _make_build()
    bad = object.__new__(pr.ProducerRegistrationRecord)
    for f in dataclasses.fields(rec):
        object.__setattr__(bad, f.name, getattr(rec, f.name))
    object.__setattr__(bad, "producer_role", "NOPE")
    assert pr.validate_evidence_class(bad, "BUILD_EVIDENCE") == ("PR-FUT2-BAD-ROLE",)


# ============================================================================ evidence record ids
def test_eventual_evidence_record_ids():
    assert pr.eventual_evidence_record_ids() == {
        "build_producer_registration": "EV-04-BUILD-PRODUCER-REGISTRATION",
        "oci_inspection_producer_registration": "EV-05-OCI-INSPECTION-PRODUCER-REGISTRATION",
    }


# ============================================================================ secret-scan
def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def test_no_secrets_in_new_artifacts():
    secret_patterns = [
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        re.compile(r"(?i)\bAKIA[0-9A-Z]{16}\b"),
        re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}"),
        re.compile(r"(?i)(password|passwd|secret|api[_-]?key)\s*[:=]\s*['\"][^'\"]{6,}['\"]"),
        re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"),
    ]
    for path in (MODULE_PATH, JSON_PATH, MD_PATH):
        text = _read(path)
        for pat in secret_patterns:
            assert pat.search(text) is None, f"secret-looking match in {path}: {pat.pattern}"


# ============================================================================ inertness
_FORBIDDEN_CALLS = {"open", "getenv", "system", "popen", "socket", "urlopen", "run", "Popen", "check_output"}
_FORBIDDEN_IMPORTS = {"os", "socket", "subprocess", "requests", "urllib", "pathlib", "http"}


def test_module_performs_no_io_static():
    tree = ast.parse(_read(MODULE_PATH))
    imported = set()
    called_names = set()
    attr_accesses = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                called_names.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                called_names.add(fn.attr)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            attr_accesses.add(f"{node.value.id}.{node.attr}")

    for bad in _FORBIDDEN_IMPORTS:
        assert bad not in imported, f"forbidden import: {bad}"
    assert not (_FORBIDDEN_CALLS & called_names), f"forbidden call(s): {_FORBIDDEN_CALLS & called_names}"
    for bad in ("os.environ", "os.getenv", "os.system", "os.popen"):
        assert bad not in attr_accesses, f"forbidden attribute access: {bad}"


def test_import_performs_no_io():
    import importlib
    mod = importlib.reload(pr)
    assert mod.production_producer_registry_configured() is False


# ============================================================================ carries no signing material
def test_record_carries_no_signing_material():
    rec = _make_build()
    for cap in ("sign", "issue", "mint", "private_key", "generate_key", "rotate_key", "self_register"):
        assert not hasattr(rec, cap)


def test_build_oci_full_flow_byte_identical_after_validation():
    build = _make_build()
    oci = _make_oci()
    before_b = build.to_dict()
    before_o = oci.to_dict()
    assert pr.validate_producer_registration(build, now_utc=NOW, real_mode=False) == ()
    assert pr.validate_producer_registration(oci, now_utc=NOW, real_mode=False) == ()
    assert pr.evaluate_producer_registration_distinctness(
        build, oci, build_execution=_build_exec(), oci_execution=_oci_exec()) == ()
    # no mutation / repair.
    assert build.to_dict() == before_b
    assert oci.to_dict() == before_o
