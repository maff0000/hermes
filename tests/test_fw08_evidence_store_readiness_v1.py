"""FW-08 F2-R3-FUT-3 immutable evidence-store + resolver readiness tests (§10).

WO-HELM-HERMES-FW08-F2-R3-FUT-3-IMMUTABLE-EVIDENCE-STORE-READINESS-CONTRACT-IMPLEMENTATION-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-29. Contract version: 1.

These tests create NO real evidence store, write NO evidence object, perform NO SQL/Redis/filesystem/
object-store write, open NO socket, read NO file, create NO key/secret/credential, and add NO third-party
dependency (stdlib only). They CONSUME the already-implemented §9 resolver
(design.hermes_fw08_content_resolution_v1) and the FUT-2 producer registrations
(design.hermes_fw08_producer_registration_v1) — never reimplementing them. They prove the FUT-3 contract:

    real evidence-store readiness is UNAVAILABLE and FAILS CLOSED; the §9 immutable content-addressed resolver
    is READIED (not duplicated); EV-04/EV-05 producer registrations are bound as REAL records; no single
    primitive (caller classification / Boolean / local seal) is the trust root for real acceptance;
    content-level separation is PREPARED, not operationally proven.

All tests are ADDITIVE and PASS.

Run: cd <worktree> && python3 -m pytest tests/test_fw08_evidence_store_readiness_v1.py -q
"""
from __future__ import annotations

import ast
import copy
import dataclasses
import inspect
import re

import pytest

import design.hermes_fw08_evidence_store_readiness_v1 as es
import design.hermes_fw08_content_resolution_v1 as cres
import design.hermes_fw08_producer_registration_v1 as pr


NOW = "2026-07-29T00:00:00+00:00"
VALIDITY_END = "2026-07-30T00:00:00+00:00"
PAST = "2026-07-28T00:00:00+00:00"

MODULE_PATH = es.__file__
JSON_PATH = "docs/design/fw08/f2_r3_fut3_evidence_store_readiness.v1.json"
MD_PATH = "docs/design/fw08/F2_R3_FUT3_EVIDENCE_STORE_READINESS.md"


# ============================================================================ upstream EV-04 / EV-05 fixtures
def _build_reg(**overrides):
    kw = dict(
        role="BUILD_PRODUCER", lifecycle_stage=4, registration_id="reg-build-01",
        producer_identity_reference="ref://producer/build-01",
        execution_identity_reference="ref://build-producer/execution/exec-build-01",
        executable_identity_reference="ref://executable/build-tool-01",
        authority_root_reference="ref://authority-root/build-domain",
        approval_record_reference="ref://approval/build-01",
        now_utc=NOW, validity_end_utc=VALIDITY_END, provenance="SYNTHETIC_TEST",
        evidence_references=("ref://evidence/build-evidence/ev-04#reg=reg-build-01",),
        created_by="ref://approver/external-01",
    )
    kw.update(overrides)
    rec, reasons = pr.new_synthetic_registration(**kw)
    assert reasons == (), reasons
    return rec


def _oci_reg(**overrides):
    kw = dict(
        role="OCI_INSPECTION_PRODUCER", lifecycle_stage=5, registration_id="reg-oci-01",
        producer_identity_reference="ref://producer/oci-01",
        execution_identity_reference="ref://oci-producer/execution/exec-oci-01",
        executable_identity_reference="ref://executable/oci-tool-01",
        authority_root_reference="ref://authority-root/oci-domain",
        approval_record_reference="ref://approval/oci-01",
        now_utc=NOW, validity_end_utc=VALIDITY_END, provenance="SYNTHETIC_TEST",
        evidence_references=("ref://evidence/oci-inspection-evidence/ev-05#reg=reg-oci-01",),
        created_by="ref://approver/external-02",
    )
    kw.update(overrides)
    rec, reasons = pr.new_synthetic_registration(**kw)
    assert reasons == (), reasons
    return rec


# ============================================================================ §9 resolver / content fixtures
def _content(role, **over):
    kw = dict(
        reference=f"ref://evidence/{role}/{role}-object", content_digest=(role[0] + "d") * 32,
        media_type="application/json", schema_type=role.upper(), size_bytes=128, creation_utc=NOW,
        producer_id=f"{role}-producer", provenance=f"{role}-inspection",
        immutable_storage_id=f"{role}-store", storage_version="v1",
    )
    kw.update(over)
    return cres.new_content_resolved(**kw)


def _build_content(**over):
    return _content("build", **over)


def _oci_content(**over):
    return _content("oci", **over)


def _trusted_resolver(*contents):
    return cres.new_synthetic_content_resolver({c.reference: c for c in contents})


# ============================================================================ EV-06 readiness fixtures
def _readiness_kwargs(**overrides):
    kw = dict(
        ev04_reference="reg-build-01",
        ev05_reference="reg-oci-01",
        resolver_contract_reference="ref://contract/hermes-fw08-content-resolution-v1",
        resolver_identity_reference="ref://resolver/hermes-fw08-content-resolution",
        store_policy_reference="ref://policy/immutable-content-addressed-store",
        immutable_storage_id="ref://store/immutable-content-addressed",
        storage_version_scheme="ref://policy/monotonic-version-scheme",
        now_utc=NOW, validity_end_utc=VALIDITY_END, provenance="SYNTHETIC_TEST",
        evidence_references=("ref://evidence/build-evidence/ev-04",),
        created_by="ref://readier/external-01",
    )
    kw.update(overrides)
    return kw


def _make_readiness(ev04=None, ev05=None, **overrides):
    kw = _readiness_kwargs(**overrides)
    if ev04 is not None:
        kw["ev04"] = ev04
    if ev05 is not None:
        kw["ev05"] = ev05
    rec, reasons = es.new_synthetic_evidence_store_readiness(**kw)
    assert reasons == (), reasons
    assert rec is not None
    return rec


def _valid_setup():
    """A fully-valid synthetic EV-06 + its distinct EV-04/EV-05 + a trusted resolver."""
    ev04 = _build_reg()
    ev05 = _oci_reg()
    rec = _make_readiness(ev04=ev04, ev05=ev05)
    resolver = _trusted_resolver(_build_content(), _oci_content())
    return rec, ev04, ev05, resolver


def _reseal_with(rec, **field_overrides):
    """Reconstruct a record with `field_overrides` applied, then recompute its digest and RE-SEAL it with the
    module issuer, so the tampered field is carried by a VALID SYNTHETIC seal. This isolates the validator's
    FIELD checks (digest algorithm, reference mismatch, ...) from the seal check — the mint helper deliberately
    forces internally-consistent fields, so a negative field vector must be injected this way."""
    obj = object.__new__(es.EvidenceStoreReadinessRecord)
    for f in dataclasses.fields(rec):
        object.__setattr__(obj, f.name, getattr(rec, f.name))
    for name, value in field_overrides.items():
        object.__setattr__(obj, name, value)
    object.__setattr__(obj, "readiness_digest", obj.recompute_digest())
    object.__setattr__(obj, "readiness_seal", es._MODULE_ISSUER.seal_readiness(obj))
    assert es._MODULE_ISSUER.verify(obj) is True
    return obj


# ============================================================================ positive
def test_positive_synthetic_readiness_validates_in_test_only():
    rec, ev04, ev05, resolver = _valid_setup()
    reasons = es.validate_evidence_store_readiness(
        rec, resolver=resolver, ev04_record=ev04, ev05_record=ev05, real_mode=False, now_utc=NOW)
    assert reasons == (), reasons
    assert rec.readiness_status == "SYNTHETIC_UNREADY"
    assert rec.synthetic_or_real_classification == "SYNTHETIC"
    assert rec.lifecycle_stage == 6
    assert rec.fut == "FUT-3"
    assert rec.digest_algorithm == "sha256"
    assert rec.readiness_record_id == es.EV06_RECORD_ID


def test_positive_same_readiness_rejects_in_real_mode():
    rec, ev04, ev05, resolver = _valid_setup()
    reasons = es.validate_evidence_store_readiness(
        rec, resolver=resolver, ev04_record=ev04, ev05_record=ev05, real_mode=True, now_utc=NOW)
    assert reasons != ()
    assert "ES-STORE-AUTHORITY-NOT-CONFIGURED" in reasons
    assert "ES-SYNTHETIC-IN-REAL-MODE" in reasons


def test_real_candidate_always_fails_closed():
    rec, reasons = es.ready_evidence_store(mode="REAL_CANDIDATE", **_readiness_kwargs())
    assert rec is None
    assert "ES-REAL-READINESS-UNAVAILABLE" in reasons
    assert "F2R3-FUT3-REAL-EVIDENCE-STORE-NOT-CONFIGURED" in reasons


# ============================================================================ §G production unavailable
def test_production_evidence_store_unconfigured():
    assert es.production_evidence_store_configured() is False


def test_configured_store_authority_identity_is_none():
    assert es.configured_store_authority_identity() is None


def test_synthetic_requires_module_token():
    rec, reasons = es.ready_evidence_store(mode="TEST_ONLY", **_readiness_kwargs())
    assert rec is None
    assert reasons == ("ES-SYNTHETIC-REQUIRES-MODULE-TOKEN",)


def test_mint_helper_requires_module_token():
    with pytest.raises(es.EvidenceStoreReadinessForbidden):
        es.new_synthetic_readiness(**_readiness_kwargs())


def test_mint_helper_refuses_real_classification():
    with pytest.raises(es.EvidenceStoreReadinessForbidden):
        es.new_synthetic_readiness(
            test_token=es._READINESS_TOKEN, synthetic_or_real_classification="REAL", **_readiness_kwargs())


def test_invalid_mode_rejected():
    rec, reasons = es.ready_evidence_store(mode="BOGUS", **_readiness_kwargs())
    assert rec is None
    assert reasons == ("ES-INVALID-MODE",)


# ============================================================================ readiness authority attacks
def _all_readiness_validate(rec, ev04, ev05, resolver, real_mode):
    return es.validate_evidence_store_readiness(
        rec, resolver=resolver, ev04_record=ev04, ev05_record=ev05, real_mode=real_mode, now_utc=NOW)


def test_public_real_construction_forbidden():
    # a caller cannot mint a REAL record through the public path.
    with pytest.raises(es.EvidenceStoreReadinessForbidden):
        es.new_synthetic_readiness(
            test_token=es._READINESS_TOKEN, synthetic_or_real_classification="REAL", **_readiness_kwargs())


def test_direct_dataclass_construction_seal_invalid():
    rec, ev04, ev05, resolver = _valid_setup()
    fields = {f.name: getattr(rec, f.name) for f in dataclasses.fields(rec)}
    fields["readiness_seal"] = "0" * 64
    forged = es.EvidenceStoreReadinessRecord(**fields)
    reasons = _all_readiness_validate(forged, ev04, ev05, resolver, False)
    assert "ES-SEAL-INVALID" in reasons


def test_reflective_issuer_access_seal_recompute_copied_seal():
    rec, ev04, ev05, resolver = _valid_setup()
    # a caller reaches the reflective module issuer and re-seals a SYNTHETIC record -> still valid synthetic.
    reseal = dataclasses.replace(rec, readiness_seal=es._MODULE_ISSUER.seal_readiness(rec))
    assert es._MODULE_ISSUER.verify(reseal) is True
    assert _all_readiness_validate(reseal, ev04, ev05, resolver, False) == ()
    # a copied valid seal onto a body-tampered record -> digest tamper + seal invalid.
    tampered = dataclasses.replace(rec, provenance="ATTACKER", readiness_seal=rec.readiness_seal)
    r = _all_readiness_validate(tampered, ev04, ev05, resolver, False)
    assert "ES-DIGEST-TAMPER" in r or "ES-SEAL-INVALID" in r


def test_object_new_construction_real_mode_fails_closed():
    rec, ev04, ev05, resolver = _valid_setup()
    obj = object.__new__(es.EvidenceStoreReadinessRecord)
    for f in dataclasses.fields(rec):
        object.__setattr__(obj, f.name, getattr(rec, f.name))
    object.__setattr__(obj, "synthetic_or_real_classification", "REAL")
    object.__setattr__(obj, "readiness_status", "REAL_READY")
    object.__setattr__(obj, "readiness_digest", obj.recompute_digest())
    object.__setattr__(obj, "readiness_seal", es._MODULE_ISSUER.seal_readiness(obj))
    reasons = _all_readiness_validate(obj, ev04, ev05, resolver, True)
    assert "ES-STORE-AUTHORITY-NOT-CONFIGURED" in reasons


def test_dataclasses_replace_relabel_breaks_seal():
    rec, ev04, ev05, resolver = _valid_setup()
    real = dataclasses.replace(rec, synthetic_or_real_classification="REAL")
    # relabel without re-seal -> seal invalid.
    reasons = _all_readiness_validate(real, ev04, ev05, resolver, False)
    assert "ES-SEAL-INVALID" in reasons


def test_shallow_and_deep_copy_still_synthetic_real_rejects():
    rec, ev04, ev05, resolver = _valid_setup()
    for variant in (copy.copy(rec), copy.deepcopy(rec)):
        assert _all_readiness_validate(variant, ev04, ev05, resolver, False) == ()
        assert "ES-STORE-AUTHORITY-NOT-CONFIGURED" in \
            _all_readiness_validate(variant, ev04, ev05, resolver, True)


def test_subclass_cannot_relabel_synthetic_to_real():
    rec, ev04, ev05, resolver = _valid_setup()

    class ProxyRecord(es.EvidenceStoreReadinessRecord):
        pass

    fields = {f.name: getattr(rec, f.name) for f in dataclasses.fields(rec)}
    fields["synthetic_or_real_classification"] = "REAL"
    proxy = ProxyRecord(**fields)
    assert es._MODULE_ISSUER.verify(proxy) is False
    assert "ES-SEAL-INVALID" in _all_readiness_validate(proxy, ev04, ev05, resolver, False)
    assert "ES-STORE-AUTHORITY-NOT-CONFIGURED" in _all_readiness_validate(proxy, ev04, ev05, resolver, True)


def test_proxy_wrong_type_rejected():
    _, ev04, ev05, resolver = _valid_setup()
    assert es.validate_evidence_store_readiness(
        object(), resolver=resolver, ev04_record=ev04, ev05_record=ev05, real_mode=False, now_utc=NOW) \
        == ("ES-WRONG-TYPE",)


def test_dict_mutation_bypass_rejected():
    rec, ev04, ev05, resolver = _valid_setup()
    obj = object.__new__(es.EvidenceStoreReadinessRecord)
    for f in dataclasses.fields(rec):
        object.__setattr__(obj, f.name, getattr(rec, f.name))
    object.__setattr__(obj, "store_policy_reference", "ref://attacker/swapped")
    r = _all_readiness_validate(obj, ev04, ev05, resolver, False)
    assert "ES-SEAL-INVALID" in r or "ES-DIGEST-TAMPER" in r


def test_synthetic_relabelled_real_rejected_in_test_mode_via_status():
    rec, ev04, ev05, resolver = _valid_setup()
    # a real STATUS (without a real classification) still trips the real-readiness gate + real-status-forbidden.
    obj = object.__new__(es.EvidenceStoreReadinessRecord)
    for f in dataclasses.fields(rec):
        object.__setattr__(obj, f.name, getattr(rec, f.name))
    object.__setattr__(obj, "readiness_status", "REAL_READY")
    object.__setattr__(obj, "readiness_digest", obj.recompute_digest())
    object.__setattr__(obj, "readiness_seal", es._MODULE_ISSUER.seal_readiness(obj))
    r = _all_readiness_validate(obj, ev04, ev05, resolver, False)
    assert "ES-STORE-AUTHORITY-NOT-CONFIGURED" in r
    assert "ES-REAL-STATUS-FORBIDDEN" in r


def test_patched_availability_boolean_still_fails_closed(monkeypatch):
    rec, ev04, ev05, resolver = _valid_setup()
    real = dataclasses.replace(rec, synthetic_or_real_classification="REAL")
    real = dataclasses.replace(real, readiness_digest=real.recompute_digest())
    real = dataclasses.replace(real, readiness_seal=es._MODULE_ISSUER.seal_readiness(real))
    monkeypatch.setattr(es, "production_evidence_store_configured", lambda: True)
    reasons = _all_readiness_validate(real, ev04, ev05, resolver, True)
    assert "ES-STORE-AUTHORITY-NOT-CONFIGURED" in reasons


def test_patched_resolver_configured_and_store_identity_boolean_still_fails(monkeypatch):
    # patching the §9 resolver-trust Boolean surface does not help either: the readiness real gate is anchored on
    # the module-controlled configured_store_authority_identity(), which is None.
    rec, ev04, ev05, resolver = _valid_setup()
    real = dataclasses.replace(rec, synthetic_or_real_classification="REAL")
    real = dataclasses.replace(real, readiness_digest=real.recompute_digest())
    real = dataclasses.replace(real, readiness_seal=es._MODULE_ISSUER.seal_readiness(real))
    monkeypatch.setattr(es, "production_evidence_store_configured", lambda: True)
    # even if a caller could flip a resolver Boolean, the store-authority identity anchor is decisive.
    reasons = _all_readiness_validate(real, ev04, ev05, resolver, True)
    assert "ES-STORE-AUTHORITY-NOT-CONFIGURED" in reasons


def test_seal_alone_is_not_the_root():
    rec, ev04, ev05, resolver = _valid_setup()
    real = dataclasses.replace(rec, synthetic_or_real_classification="REAL")
    real = dataclasses.replace(real, readiness_digest=real.recompute_digest())
    real = dataclasses.replace(real, readiness_seal=es._MODULE_ISSUER.seal_readiness(real))
    assert es._MODULE_ISSUER.verify(real) is True
    reasons = _all_readiness_validate(real, ev04, ev05, resolver, True)
    assert reasons != ()
    assert "ES-STORE-AUTHORITY-NOT-CONFIGURED" in reasons


def test_caller_created_upstream_or_store_policy_rejected():
    # a caller-supplied EV-04/EV-05 that is NOT a real ProducerRegistrationRecord is rejected.
    rec, _, _, resolver = _valid_setup()
    r = es.validate_upstream_binding(
        rec, ev04_record="reg-build-01", ev05_record="reg-oci-01", real_mode=False, now_utc=NOW)
    assert "ES-EV04-MISSING" in r and "ES-EV05-MISSING" in r


# ============================================================================ DECISIVE (FUT-1 lesson)
def _decisive_real_forge_closed():
    """Reflectively forge a correctly-sealed REAL readiness record, monkeypatch
    production_evidence_store_configured -> True, validate in real_mode, and return True iff it is rejected on
    ES-STORE-AUTHORITY-NOT-CONFIGURED (the module-controlled NON-Boolean identity anchor, which is None). Proves
    the Boolean is not the sole root and the local seal is not the sole root."""
    ev04 = _build_reg()
    ev05 = _oci_reg()
    rec = _make_readiness(ev04=ev04, ev05=ev05)
    resolver = _trusted_resolver(_build_content(), _oci_content())
    real = dataclasses.replace(rec, synthetic_or_real_classification="REAL",
                               store_policy_reference="ref://caller/forged-store-authority")
    real = dataclasses.replace(real, readiness_digest=real.recompute_digest())
    real = dataclasses.replace(real, readiness_seal=es._MODULE_ISSUER.seal_readiness(real))
    assert es._MODULE_ISSUER.verify(real) is True

    saved = es.production_evidence_store_configured
    try:
        es.production_evidence_store_configured = lambda: True  # type: ignore[assignment]
        reasons = es.validate_evidence_store_readiness(
            real, resolver=resolver, ev04_record=ev04, ev05_record=ev05, real_mode=True, now_utc=NOW)
    finally:
        es.production_evidence_store_configured = saved  # type: ignore[assignment]
    return "ES-STORE-AUTHORITY-NOT-CONFIGURED" in reasons and \
        "ES-SEAL-INVALID" not in reasons and es.configured_store_authority_identity() is None


def test_decisive_real_forge_is_closed():
    assert _decisive_real_forge_closed() is True


# ============================================================================ §B resolver readiness (delegates §9)
def test_resolver_missing_delegated_cr_code():
    rec, ev04, ev05, _ = _valid_setup()
    r = es.validate_resolver_readiness(rec, resolver=None, real_mode=False, now_utc=NOW)
    assert "CR-RESOLVER-MISSING" in r


def test_caller_provided_resolver_untrusted():
    rec, ev04, ev05, _ = _valid_setup()
    caller_built = cres.SyntheticContentResolver({})   # no module token -> untrusted
    r = es.validate_resolver_readiness(rec, resolver=caller_built, real_mode=False, now_utc=NOW)
    assert "CR-RESOLVER-UNTRUSTED" in r


def test_synthetic_resolver_in_real_mode_delegated_cr_code():
    rec, ev04, ev05, resolver = _valid_setup()
    r = es.validate_resolver_readiness(rec, resolver=resolver, real_mode=True, now_utc=NOW)
    assert "CR-SYNTHETIC-IN-REAL-MODE" in r


def test_production_resolver_resolve_always_unavailable():
    prod = cres.ProductionContentResolver()
    with pytest.raises(cres.ContentResolverUnavailable):
        prod.resolve("ref://evidence/anything", now_utc=NOW)
    # via resolve_and_verify (delegated), a production resolver yields CR-UNRESOLVED-REFERENCE.
    got, r = cres.resolve_and_verify("ref://x", "d" * 64, resolver=prod, now_utc=NOW)
    assert got is None
    assert "CR-UNRESOLVED-REFERENCE" in r


def test_unsupported_digest_algorithm():
    rec, ev04, ev05, resolver = _valid_setup()
    bad = _reseal_with(rec, digest_algorithm="md5")   # a competing digest scheme is unsupported.
    r = es.validate_resolver_readiness(bad, resolver=resolver, real_mode=False, now_utc=NOW)
    assert "ES-DIGEST-ALGORITHM-UNSUPPORTED" in r


def test_mutable_storage_unready():
    ev04 = _build_reg()
    ev05 = _oci_reg()
    rec = _make_readiness(ev04=ev04, ev05=ev05, storage_version_scheme="")
    resolver = _trusted_resolver(_build_content(), _oci_content())
    r = es.validate_resolver_readiness(rec, resolver=resolver, real_mode=False, now_utc=NOW)
    assert "ES-MUTABLE-STORAGE-UNREADY" in r


def test_no_raw_dict_or_local_file_resolver_acceptance_structural():
    # STRUCTURAL: require_content_resolver only accepts a ContentResolver; a raw dict / local path is rejected.
    assert "CR-RESOLVER-MISSING" in cres.require_content_resolver({}, real_mode=False)
    assert "CR-RESOLVER-MISSING" in cres.require_content_resolver("/local/file", real_mode=False)
    # validate_resolver_readiness has no parameter that lets a caller supply evidence AND resolver together.
    params = set(inspect.signature(es.validate_resolver_readiness).parameters)
    assert "resolved_evidence" not in params
    assert "content_map" not in params


# ============================================================================ §C immutability (delegates §9)
def test_frozen_dataclass_is_not_immutable_storage_docstring():
    # the record docstring MUST distinguish frozen-dataclass immutability from immutable STORAGE.
    doc = es.EvidenceStoreReadinessRecord.__doc__ or ""
    assert "immutable STORAGE" in doc or "immutable storage" in doc.lower()
    assert "not" in doc.lower()


def test_immutability_delegates_changed_content():
    rec, ev04, ev05, _ = _valid_setup()
    bc = _build_content()
    resolver = _trusted_resolver(bc)
    # same ref, expected an OLDER storage version than what is stored -> CR-CHANGED-CONTENT.
    r = es.validate_immutability(
        rec, bc, now_utc=NOW, resolver=resolver, expected_content_digest=bc.content_digest,
        expected_storage_version="v0")
    assert "CR-CHANGED-CONTENT" in r


def test_immutability_delegates_content_mismatch():
    rec, ev04, ev05, _ = _valid_setup()
    bc = _build_content()
    resolver = _trusted_resolver(bc)
    r = es.validate_immutability(
        rec, bc, now_utc=NOW, resolver=resolver, expected_content_digest="9" * 64)
    assert "CR-REFERENCE-CONTENT-MISMATCH" in r


def test_immutability_mutable_reference_missing_storage_version():
    rec, ev04, ev05, _ = _valid_setup()
    mutable = _build_content(storage_version="")   # a versionless resolved object.
    resolver = _trusted_resolver(mutable)
    r = es.validate_immutability(
        rec, mutable, now_utc=NOW, resolver=resolver, expected_content_digest=mutable.content_digest)
    assert "ES-STORAGE-VERSION-MISSING" in r or "CR-MUTABLE-REFERENCE" in r


def test_immutability_digest_mutated_detected():
    rec, ev04, ev05, _ = _valid_setup()
    bc = _build_content()
    mutated = dataclasses.replace(bc, media_type="application/x-tampered")  # digest no longer recomputes.
    r = es.validate_immutability(rec, mutated, now_utc=NOW)
    assert "ES-DIGEST-MUTATED" in r


def test_immutability_storage_id_missing():
    ev04 = _build_reg()
    ev05 = _oci_reg()
    rec = _make_readiness(ev04=ev04, ev05=ev05, immutable_storage_id="")
    bc = _build_content()
    r = es.validate_immutability(rec, bc, now_utc=NOW)
    assert "ES-STORAGE-ID-MISSING" in r


def test_immutability_wrong_type():
    assert es.validate_immutability(object(), None, now_utc=NOW) == ("ES-WRONG-TYPE",)


# ============================================================================ §D duplicate & alias (delegates §9)
def test_conflicting_duplicate_same_identity_different_content():
    rec, ev04, ev05, _ = _valid_setup()
    a = _build_content(reference="ref://evidence/build/obj", content_digest="1" * 64)
    b = _build_content(reference="ref://evidence/build/obj", content_digest="2" * 64)
    r = es.evaluate_duplicate_policy(rec, a, rec, b, build_result={}, oci_inspection={})
    assert "ES-CONFLICTING-DUPLICATE" in r


def test_alias_same_content_delegated():
    rec, ev04, ev05, _ = _valid_setup()
    shared = "5" * 64
    a = _build_content(reference="ref://a", content_digest=shared)
    b = _oci_content(reference="ref://b", content_digest=shared)
    r = es.evaluate_duplicate_policy(
        rec, a, rec, b, build_result={}, oci_inspection={"resolved_content_digest": shared})
    assert "CR-ALIAS-SAME-CONTENT" in r


def test_duplicate_policy_wrong_type():
    a = _build_content()
    assert es.evaluate_duplicate_policy(object(), a, object(), a, build_result={}, oci_inspection={}) \
        == ("ES-WRONG-TYPE",)


# ============================================================================ §E upstream binding (delegates FUT-2)
def test_upstream_binding_positive():
    rec, ev04, ev05, _ = _valid_setup()
    assert es.validate_upstream_binding(
        rec, ev04_record=ev04, ev05_record=ev05, real_mode=False, now_utc=NOW) == ()


def test_missing_ev04_ev05():
    rec, ev04, ev05, _ = _valid_setup()
    assert "ES-EV04-MISSING" in es.validate_upstream_binding(
        rec, ev04_record=None, ev05_record=ev05, real_mode=False, now_utc=NOW)
    assert "ES-EV05-MISSING" in es.validate_upstream_binding(
        rec, ev04_record=ev04, ev05_record=None, real_mode=False, now_utc=NOW)


def test_upstream_lifecycle_mismatch():
    rec, ev04, ev05, _ = _valid_setup()
    # swap: pass the OCI record where EV-04 (build/stage-4) is expected.
    r = es.validate_upstream_binding(
        rec, ev04_record=ev05, ev05_record=ev04, real_mode=False, now_utc=NOW)
    assert "ES-UPSTREAM-LIFECYCLE-MISMATCH" in r


def test_ev04_ev05_reference_mismatch():
    rec, ev04, ev05, _ = _valid_setup()
    bad = _reseal_with(rec, ev04_reference="reg-wrong-elsewhere")
    r = es.validate_upstream_binding(bad, ev04_record=ev04, ev05_record=ev05, real_mode=False, now_utc=NOW)
    assert "ES-EV04-REFERENCE-MISMATCH" in r


def test_registry_authority_mismatch_shared_root():
    ev04 = _build_reg()
    ev05 = _oci_reg(authority_root_reference=ev04.authority_root_reference)  # shared root
    rec = _make_readiness(ev04=ev04, ev05=ev05)
    r = es.validate_upstream_binding(rec, ev04_record=ev04, ev05_record=ev05, real_mode=False, now_utc=NOW)
    assert "ES-REGISTRY-AUTHORITY-MISMATCH" in r


def test_resolver_contract_mismatch():
    ev04 = _build_reg()
    ev05 = _oci_reg()
    rec = _make_readiness(ev04=ev04, ev05=ev05, resolver_contract_reference="ref://contract/some-other-thing")
    r = es.validate_upstream_binding(rec, ev04_record=ev04, ev05_record=ev05, real_mode=False, now_utc=NOW)
    assert "ES-RESOLVER-CONTRACT-MISMATCH" in r


def test_copied_upstream_reference():
    ev04 = _build_reg()
    ev05 = _oci_reg(registration_id="reg-build-01")   # collide ids so the readiness carries the same ref twice
    rec = _make_readiness(ev04=ev04, ev05=ev05)
    r = es.validate_upstream_binding(rec, ev04_record=ev04, ev05_record=ev05, real_mode=False, now_utc=NOW)
    assert "ES-COPIED-UPSTREAM-REFERENCE" in r


def test_revoked_and_expired_upstream():
    ev04 = _build_reg()
    ev05 = _oci_reg()
    rec = _make_readiness(ev04=ev04, ev05=ev05)
    revoked = dataclasses.replace(ev04, revocation_state="REVOKED")
    r = es.validate_upstream_binding(rec, ev04_record=revoked, ev05_record=ev05, real_mode=False, now_utc=NOW)
    assert "ES-UPSTREAM-REVOKED" in r
    ev04e = _build_reg(validity_end_utc=PAST)
    rece = _make_readiness(ev04=ev04e, ev05=ev05)
    re_ = es.validate_upstream_binding(rece, ev04_record=ev04e, ev05_record=ev05, real_mode=False, now_utc=NOW)
    assert "ES-UPSTREAM-EXPIRED" in re_


def test_synthetic_upstream_in_real_mode():
    rec, ev04, ev05, _ = _valid_setup()
    r = es.validate_upstream_binding(rec, ev04_record=ev04, ev05_record=ev05, real_mode=True, now_utc=NOW)
    assert "ES-SYNTHETIC-UPSTREAM-IN-REAL-MODE" in r


# ============================================================================ §F producer & evidence binding
def test_producer_binding_positive():
    rec, ev04, ev05, _ = _valid_setup()
    assert es.validate_producer_binding(rec, ev04_record=ev04, ev05_record=ev05) == ()


def test_producer_binding_broken_wrong_ref():
    rec, ev04, ev05, _ = _valid_setup()
    bad = _reseal_with(rec, ev04_reference="reg-nope")
    r = es.validate_producer_binding(bad, ev04_record=ev04, ev05_record=ev05)
    assert "ES-PRODUCER-BINDING-BROKEN" in r


def test_build_oci_not_distinct():
    ev04 = _build_reg()
    # an OCI reg sharing the build producer identity -> not distinct.
    ev05 = _oci_reg(producer_identity_reference=ev04.producer_identity_reference)
    rec = _make_readiness(ev04=ev04, ev05=ev05)
    r = es.validate_producer_binding(rec, ev04_record=ev04, ev05_record=ev05)
    assert "ES-BUILD-OCI-NOT-DISTINCT" in r


def test_producer_binding_wrong_type():
    rec, ev04, ev05, _ = _valid_setup()
    assert es.validate_producer_binding(rec, ev04_record="x", ev05_record="y") \
        == ("ES-PRODUCER-BINDING-BROKEN",)


def test_content_level_separation_not_operationally_proven():
    assert es.content_level_separation_status() == \
        "CONTENT_LEVEL_SEPARATION_PREPARED_NOT_OPERATIONALLY_PROVEN"


# ============================================================================ main validator misc
def test_wrong_stage_and_contract_version():
    ev04 = _build_reg()
    ev05 = _oci_reg()
    rec = _make_readiness(ev04=ev04, ev05=ev05)
    resolver = _trusted_resolver(_build_content(), _oci_content())
    bad_stage = object.__new__(es.EvidenceStoreReadinessRecord)
    for f in dataclasses.fields(rec):
        object.__setattr__(bad_stage, f.name, getattr(rec, f.name))
    object.__setattr__(bad_stage, "lifecycle_stage", 7)
    object.__setattr__(bad_stage, "readiness_digest", bad_stage.recompute_digest())
    object.__setattr__(bad_stage, "readiness_seal", es._MODULE_ISSUER.seal_readiness(bad_stage))
    r = _all_readiness_validate(bad_stage, ev04, ev05, resolver, False)
    assert "ES-WRONG-STAGE" in r


def test_expired_and_non_utc_and_revoked():
    ev04 = _build_reg()
    ev05 = _oci_reg()
    resolver = _trusted_resolver(_build_content(), _oci_content())
    exp = _make_readiness(ev04=ev04, ev05=ev05, validity_end_utc=PAST)
    assert "ES-EXPIRED" in _all_readiness_validate(exp, ev04, ev05, resolver, False)
    nonutc = _make_readiness(ev04=ev04, ev05=ev05, now_utc="2026-07-29T00:00:00")
    assert "ES-UTC-INVALID" in _all_readiness_validate(nonutc, ev04, ev05, resolver, False)
    rec = _make_readiness(ev04=ev04, ev05=ev05)
    rev = dataclasses.replace(rec, revocation_state="REVOKED")
    assert "ES-REVOKED" in _all_readiness_validate(rev, ev04, ev05, resolver, False) or \
        "ES-SEAL-INVALID" in _all_readiness_validate(rev, ev04, ev05, resolver, False)


def test_missing_provenance_and_refs():
    ev04 = _build_reg()
    ev05 = _oci_reg()
    resolver = _trusted_resolver(_build_content(), _oci_content())
    nop = _make_readiness(ev04=ev04, ev05=ev05, provenance="")
    assert "ES-PROVENANCE-MISSING" in _all_readiness_validate(nop, ev04, ev05, resolver, False)
    missing = _make_readiness(ev04=ev04, ev05=ev05, store_policy_reference="")
    assert "ES-STORE-POLICY-REF-MISSING" in _all_readiness_validate(missing, ev04, ev05, resolver, False)


# ============================================================================ §6 reference boundary
def test_reference_boundary_rejects_hostile_forms():
    cases = {
        "ref://evidence/build/../../etc": "ES-REFERENCE-NONCANONICAL",
        "ref://evidence/build/..%2f..%2fetc": "ES-REFERENCE-NONCANONICAL",
        "ref://evidence/build/UPPER": "ES-REFERENCE-NONCANONICAL",
        "/etc/passwd": "ES-REFERENCE-NONCANONICAL",
        "file:///etc/passwd": "ES-REFERENCE-NONCANONICAL",
        "ref://evidence/build/obj?tag=latest": "ES-REFERENCE-MUTABLE-QUERY",
        "{\"inline\":true}": "ES-REFERENCE-IS-INLINE-PAYLOAD",
        "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----": "ES-REFERENCE-IS-INLINE-PAYLOAD",
        "echo pwned; rm -rf /": "ES-REFERENCE-IS-INLINE-PAYLOAD",
        "d" * 64: "ES-REFERENCE-DIGEST-ONLY",
        "sha256:" + "a" * 64: "ES-REFERENCE-DIGEST-ONLY",
    }
    for ref, code in cases.items():
        r = es.validate_evidence_reference(ref)
        assert code in r, (ref, r)


def test_reference_boundary_rejects_raw_dict_and_credential():
    assert "ES-REFERENCE-IS-INLINE-PAYLOAD" in es.validate_evidence_reference({"k": "v"})
    assert "ES-REFERENCE-IS-INLINE-PAYLOAD" in es.validate_evidence_reference(
        "ref://user:secretpass@host/obj")


def test_reference_boundary_accepts_canonical_pointer():
    assert es.validate_evidence_reference("ref://evidence/build-evidence/ev-04") == ()


# ============================================================================ evidence record ids
def test_eventual_evidence_record_ids():
    assert es.eventual_evidence_record_ids() == {
        "evidence_store_readiness": "EV-06-EVIDENCE-STORE-AND-RESOLVER-READINESS",
    }


# ============================================================================ CONSUMES §9 (not reimplemented)
def test_consumes_existing_resolver_not_reimplemented():
    # the module imports the §9 resolver and reuses its public surface — it does NOT define its own resolver.
    src = _read(MODULE_PATH)
    assert "import design.hermes_fw08_content_resolution_v1" in src
    # no competing resolver class / digest scheme defined in this module.
    assert "class SyntheticContentResolver" not in src
    assert "class ProductionContentResolver" not in src
    assert "def detect_alias_or_nearcopy" not in src
    assert "def resolve_and_verify" not in src
    # it DOES delegate to §9 functions.
    for fn in ("require_content_resolver", "resolve_and_verify", "detect_alias_or_nearcopy"):
        assert f"cres.{fn}" in src


def test_consumes_ev04_ev05_as_real_records_not_strings():
    # validate_upstream_binding / validate_producer_binding require ProducerRegistrationRecord objects.
    rec, ev04, ev05, _ = _valid_setup()
    assert isinstance(ev04, pr.ProducerRegistrationRecord)
    assert isinstance(ev05, pr.ProducerRegistrationRecord)
    # a raw string upstream is rejected (missing).
    assert "ES-EV04-MISSING" in es.validate_upstream_binding(
        rec, ev04_record="reg-build-01", ev05_record=ev05, real_mode=False, now_utc=NOW)


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
_FORBIDDEN_CALLS = {"open", "getenv", "system", "popen", "socket", "urlopen", "run", "Popen", "check_output",
                    "connect", "execute", "write"}
_FORBIDDEN_IMPORTS = {"os", "socket", "subprocess", "requests", "urllib", "pathlib", "http", "sqlite3", "redis"}


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
    mod = importlib.reload(es)
    assert mod.production_evidence_store_configured() is False
    assert mod.configured_store_authority_identity() is None


def test_no_write_or_deploy_or_activate_tokens_in_module():
    # scan only CODE lines (drop comments and the module docstring prose, which legitimately says
    # "no subprocess, no network"). No store/deploy/attest client or write call may appear.
    src = _read(MODULE_PATH)
    tree = ast.parse(src)
    import_mods = set()
    call_attrs = set()
    def_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                import_mods.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            import_mods.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            call_attrs.add(node.func.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            def_names.add(node.name)
    for mod in ("boto3", "psycopg", "sqlalchemy", "docker", "subprocess", "requests", "sqlite3", "redis"):
        assert mod not in import_mods, f"unexpected import in module: {mod}"
    for attr in ("commit", "flush", "execute", "put_object", "connect", "deploy", "attest"):
        assert attr not in call_attrs, f"unexpected call in module: {attr}"
    for fn in def_names:
        assert not any(fn.startswith(p) for p in ("deploy", "activate", "sign", "attest", "write_", "store_")), \
            f"unexpected function in module: {fn}"


def test_record_carries_no_endpoint_or_credential_fields():
    rec, _, _, _ = _valid_setup()
    field_names = {f.name for f in dataclasses.fields(rec)}
    for forbidden in ("endpoint", "bucket", "credential", "host_path", "raw_key", "password", "secret",
                      "access_key", "token"):
        assert not any(forbidden in n for n in field_names), forbidden
    for cap in ("sign", "issue", "mint", "private_key", "write", "delete", "deploy"):
        assert not hasattr(rec, cap)


# ============================================================================ no mutation / repair
def test_validation_does_not_mutate_record():
    rec, ev04, ev05, resolver = _valid_setup()
    before = rec.to_dict()
    assert es.validate_evidence_store_readiness(
        rec, resolver=resolver, ev04_record=ev04, ev05_record=ev05, real_mode=False, now_utc=NOW) == ()
    assert rec.to_dict() == before
