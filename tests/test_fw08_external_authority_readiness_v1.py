"""FW-08 F2-R3-FUT-1 external-authority + issuer-attestation readiness tests (§6).

WO-HELM-HERMES-FW08-F2-R3-FUT-1-EXTERNAL-AUTHORITY-AND-ISSUER-ATTESTATION-READINESS-CONTRACT-IMPLEMENTATION-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-27. Contract version: 1.

These tests configure NO external authority, create NO key/secret/credential, sign NOTHING, register NO
authority, wire NO runtime, and add NO third-party dependency (stdlib only). They prove the FUT-1 contract:

    real external authority is UNAVAILABLE and FAILS CLOSED; no signing capability is reachable; a caller can
    never own both the asserted authority and its verifier; synthetic readiness can never become real.

All tests are ADDITIVE and PASS.

Run: cd <worktree> && python3 -m pytest tests/test_fw08_external_authority_readiness_v1.py -q
"""
from __future__ import annotations

import ast
import dataclasses
import re

import pytest

import design.hermes_fw08_external_authority_readiness_v1 as ear


NOW = "2026-07-27T00:00:00+00:00"
VALIDITY_END = "2026-07-28T00:00:00+00:00"
PAST = "2026-07-26T00:00:00+00:00"

MODULE_PATH = ear.__file__
JSON_PATH = "docs/design/fw08/f2_r3_fut1_external_authority_readiness.v1.json"
MD_PATH = "docs/design/fw08/F2_R3_FUT1_EXTERNAL_AUTHORITY_READINESS.md"


def _synthetic_kwargs(**overrides):
    kw = dict(
        external_authority_reference="ref://authority-root/hermes-fw08",
        trust_anchor_reference="ref://trust-anchor/hermes-fw08",
        resolver_reference="ref://resolver/hermes-fw08",
        issuer_identity_reference="ref://issuer/hermes-fw08",
        attestation_policy_reference="ref://attestation-policy/hermes-fw08",
        evidence_references=("EV-03-AUTHORITY-READINESS",),
        now_utc=NOW,
        validity_end_utc=VALIDITY_END,
        provenance="SYNTHETIC_TEST",
    )
    kw.update(overrides)
    return kw


def _make_synthetic():
    readiness, reasons = ear.new_synthetic_test_readiness(**_synthetic_kwargs())
    assert reasons == ()
    assert readiness is not None
    return readiness


# ============================================================================ readiness-model
def test_synthetic_readiness_accepted_in_synthetic_mode():
    r = _make_synthetic()
    assert ear.validate_authority_readiness(r, now_utc=NOW, real_mode=False) == ()
    assert r.readiness_state == "AUTHORITY_READY"
    assert r.authority_class == "SYNTHETIC_TEST_AUTHORITY_READINESS"
    assert r.synthetic_or_real_classification == "SYNTHETIC"
    assert r.lifecycle_stage == 3


def test_real_candidate_always_fails_closed():
    r, reasons = ear.resolve_authority_readiness(mode="REAL_CANDIDATE", **_synthetic_kwargs())
    assert r is None
    assert "EAR-REAL-AUTHORITY-UNAVAILABLE" in reasons
    assert "F2R3-FUT1-REAL-EXTERNAL-AUTHORITY-NOT-CONFIGURED" in reasons


def test_production_external_authority_unavailable():
    assert ear.production_external_authority_available() is False


def test_synthetic_requires_module_token():
    r, reasons = ear.resolve_authority_readiness(mode="TEST_ONLY", **_synthetic_kwargs())
    assert r is None
    assert reasons == ("EAR-SYNTHETIC-REQUIRES-MODULE-TOKEN",)


@pytest.mark.parametrize("field,code", [
    ("external_authority_reference", "EAR-AUTHORITY-REF-MISSING"),
    ("trust_anchor_reference", "EAR-TRUST-ANCHOR-REF-MISSING"),
    ("issuer_identity_reference", "EAR-ISSUER-REF-MISSING"),
    ("attestation_policy_reference", "EAR-ATTESTATION-POLICY-REF-MISSING"),
])
def test_missing_reference_rejected(field, code):
    r = _make_synthetic()
    tampered = dataclasses.replace(r, **{field: ""})
    reasons = ear.validate_authority_readiness(tampered, now_utc=NOW, real_mode=False)
    assert code in reasons


def test_missing_evidence_reference_rejected():
    r, reasons = ear.new_synthetic_test_readiness(**_synthetic_kwargs(evidence_references=()))
    assert r is not None
    assert "EAR-EVIDENCE-REF-MISSING" in ear.validate_authority_readiness(r, now_utc=NOW, real_mode=False)


def test_non_utc_rejected():
    r, _ = ear.new_synthetic_test_readiness(**_synthetic_kwargs(now_utc="2026-07-27T00:00:00"))
    assert "EAR-UTC-INVALID" in ear.validate_authority_readiness(r, now_utc=NOW, real_mode=False)


def test_expired_rejected():
    r, _ = ear.new_synthetic_test_readiness(**_synthetic_kwargs(validity_end_utc=PAST))
    assert "EAR-EXPIRED" in ear.validate_authority_readiness(r, now_utc=NOW, real_mode=False)


def test_unsupported_contract_version_rejected():
    r = _make_synthetic()
    tampered = ear.dataclasses.replace(r, contract_version="99")
    reasons = ear.validate_authority_readiness(tampered, now_utc=NOW, real_mode=False)
    # tampering the body also breaks the digest/seal; the version check is still present.
    assert "EAR-CONTRACT-VERSION" in reasons


def test_wrong_lifecycle_stage_rejected():
    r = _make_synthetic()
    tampered = dataclasses.replace(r, lifecycle_stage=4)
    assert "EAR-WRONG-STAGE" in ear.validate_authority_readiness(tampered, now_utc=NOW, real_mode=False)


def test_missing_provenance_rejected():
    r, _ = ear.new_synthetic_test_readiness(**_synthetic_kwargs(provenance=""))
    assert "EAR-PROVENANCE-MISSING" in ear.validate_authority_readiness(r, now_utc=NOW, real_mode=False)


def test_bad_state_and_class_rejected():
    r = _make_synthetic()
    bad_state = dataclasses.replace(r, readiness_state="TOTALLY_BOGUS")
    assert "EAR-BAD-STATE" in ear.validate_authority_readiness(bad_state, now_utc=NOW, real_mode=False)
    bad_class = dataclasses.replace(r, authority_class="TOTALLY_BOGUS")
    assert "EAR-BAD-CLASS" in ear.validate_authority_readiness(bad_class, now_utc=NOW, real_mode=False)


def test_revoked_and_invalid_state_rejected():
    r = _make_synthetic()
    rev = dataclasses.replace(r, readiness_state="AUTHORITY_REVOKED")
    assert "EAR-REVOKED" in ear.validate_authority_readiness(rev, now_utc=NOW, real_mode=False)
    inv = dataclasses.replace(r, readiness_state="AUTHORITY_INVALID")
    assert "EAR-INVALID" in ear.validate_authority_readiness(inv, now_utc=NOW, real_mode=False)


def test_wrong_type_rejected():
    assert ear.validate_authority_readiness(object(), now_utc=NOW, real_mode=False) == ("EAR-WRONG-TYPE",)


# ============================================================================ authority-boundary attacks
def test_same_synthetic_record_rejected_in_real_mode():
    r = _make_synthetic()
    assert ear.validate_authority_readiness(r, now_utc=NOW, real_mode=False) == ()
    # C-PR122-EAR-PROMOTION: real mode fails closed FIRST on production-authority unavailability (independent
    # of classification/seal) — the decisive gate, not merely a synthetic-classification check.
    reasons = ear.validate_authority_readiness(r, now_utc=NOW, real_mode=True)
    assert reasons == ("EAR-PRODUCTION-AUTHORITY-UNAVAILABLE",)


def test_caller_supplied_verifier_in_real_mode_is_trust_loop():
    r = _make_synthetic()
    caller_verifier = ear.new_synthetic_verifier()
    reasons = ear.verify_readiness_no_caller_trust_loop(
        readiness=r, caller_verifier=caller_verifier, mode="REAL_CANDIDATE")
    assert "EAR-CALLER-OWNED-TRUST-LOOP" in reasons
    # and the module boundary yields no real verifier -> fail closed.
    assert "EAR-PRODUCTION-VERIFIER-UNAVAILABLE" in reasons


def test_caller_supplies_authority_and_verifier_rejected():
    # The asserted authority (readiness) AND its verifier both come from the caller in real mode -> rejected.
    r = _make_synthetic()
    reasons = ear.verify_readiness_no_caller_trust_loop(
        readiness=r, caller_verifier=ear.new_synthetic_verifier(), mode="REAL_CANDIDATE")
    assert reasons != ()
    assert "EAR-CALLER-OWNED-TRUST-LOOP" in reasons


def test_caller_supplied_signing_object_rejected():
    class ForgedSigningReadiness:
        def sign(self, *a, **k):
            return "forged"
    reasons = ear.validate_authority_readiness(ForgedSigningReadiness(), now_utc=NOW, real_mode=False)
    # not even the right type; the forged shape never validates.
    assert reasons == ("EAR-WRONG-TYPE",)

    # a verifier-shaped object exposing signing is rejected by the verifier-only gate.
    class ForgedSigningVerifier(ear.ExternalAuthorityVerifier):
        def verify_authority_evidence(self, *, readiness, evidence_reference, now_utc):
            return (True, ())
        def sign(self, *a, **k):
            return "forged"
    assert "EAR-VERIFIER-EXPOSES-SIGNING" in ear.require_verifier_only(ForgedSigningVerifier())


def test_object_exposing_sign_issue_mint_rejected_by_verifier_gate():
    for cap in ("sign", "issue", "mint", "generate_key", "rotate_key", "self_register", "private_key"):
        obj = type("Cap", (), {cap: (lambda self, *a, **k: None) if cap != "private_key" else b"x"})()
        assert "EAR-VERIFIER-EXPOSES-SIGNING" in ear.require_verifier_only(obj)


def test_non_verifier_interface_rejected():
    assert "EAR-NOT-VERIFIER-INTERFACE" in ear.require_verifier_only(object())


def test_module_synthetic_cannot_become_real_relabel_breaks_seal():
    r = _make_synthetic()
    relabelled = dataclasses.replace(r, synthetic_or_real_classification="REAL")
    # the seal binds the classification -> relabel breaks the seal (proved in test mode, where the seal check
    # is the operative integrity gate and control A does not short-circuit).
    assert "EAR-SEAL-INVALID" in ear.validate_authority_readiness(relabelled, now_utc=NOW, real_mode=False)
    # C-PR122-EAR-PROMOTION: in real mode the unconditional production-authority gate fires FIRST — the
    # record never passes real mode regardless of seal state.
    assert ear.validate_authority_readiness(relabelled, now_utc=NOW, real_mode=True) \
        == ("EAR-PRODUCTION-AUTHORITY-UNAVAILABLE",)


def test_subclass_proxy_cannot_relabel_synthetic_to_real():
    r = _make_synthetic()

    class ProxyReadiness(ear.ExternalAuthorityReadiness):
        pass

    # rebuild the same field values through a subclass, forcing classification REAL — seal must not verify.
    fields = {f.name: getattr(r, f.name) for f in dataclasses.fields(r)}
    fields["synthetic_or_real_classification"] = "REAL"
    proxy = ProxyReadiness(**fields)
    assert ear._MODULE_ISSUER.verify(proxy) is False
    # seal-invalid is the operative failure in test mode.
    assert "EAR-SEAL-INVALID" in ear.validate_authority_readiness(proxy, now_utc=NOW, real_mode=False)
    # C-PR122-EAR-PROMOTION: real mode fails closed FIRST on production-authority unavailability.
    assert ear.validate_authority_readiness(proxy, now_utc=NOW, real_mode=True) \
        == ("EAR-PRODUCTION-AUTHORITY-UNAVAILABLE",)


def test_monkeypatched_authority_cannot_bypass_classification(monkeypatch):
    # Even if a caller flips production_external_authority_available to True, REAL_CANDIDATE resolution still
    # fails closed (the code short-circuits real mode before any availability-based synthesis).
    monkeypatch.setattr(ear, "production_external_authority_available", lambda: True)
    r, reasons = ear.resolve_authority_readiness(mode="REAL_CANDIDATE", **_synthetic_kwargs())
    assert r is None
    assert "EAR-REAL-AUTHORITY-UNAVAILABLE" in reasons


def test_inline_payload_cannot_substitute_governed_reference():
    inline = "-----BEGIN CERTIFICATE-----\nMIIB...\n-----END CERTIFICATE-----"
    r, _ = ear.new_synthetic_test_readiness(**_synthetic_kwargs(external_authority_reference=inline))
    assert "EAR-REFERENCE-IS-INLINE-PAYLOAD" in ear.validate_authority_readiness(r, now_utc=NOW, real_mode=False)

    long_inline = "x" * 300
    r2, _ = ear.new_synthetic_test_readiness(**_synthetic_kwargs(trust_anchor_reference=long_inline))
    assert "EAR-REFERENCE-IS-INLINE-PAYLOAD" in ear.validate_authority_readiness(r2, now_utc=NOW, real_mode=False)


def test_changed_reference_invalidates_verification():
    r = _make_synthetic()
    # changing a bound reference breaks the digest recompute (the stored digest no longer matches the body).
    tampered = dataclasses.replace(r, trust_anchor_reference="ref://attacker/swapped")
    reasons = ear.validate_authority_readiness(tampered, now_utc=NOW, real_mode=False)
    assert "EAR-DIGEST-TAMPER" in reasons
    # and if the attacker ALSO recomputes the digest to hide the tamper, the seal (module-owned key) fails.
    forged = dataclasses.replace(tampered, readiness_digest=tampered.recompute_digest())
    forged_reasons = ear.validate_authority_readiness(forged, now_utc=NOW, real_mode=False)
    assert "EAR-DIGEST-TAMPER" not in forged_reasons
    assert "EAR-SEAL-INVALID" in forged_reasons


def test_revoked_or_unavailable_reference_fails_closed():
    r = _make_synthetic()
    rev = dataclasses.replace(r, readiness_state="AUTHORITY_REVOKED")
    assert ear.validate_authority_readiness(rev, now_utc=NOW, real_mode=False) != ()
    # verifier over a governed but non-listed evidence reference fails closed.
    v = ear.new_synthetic_verifier()
    ok, vreasons = v.verify_authority_evidence(
        readiness=r, evidence_reference="EV-99-NOT-GOVERNED", now_utc=NOW)
    assert ok is False
    assert "EAR-VERIFY-EVIDENCE-NOT-GOVERNED" in vreasons


# ============================================================================ production verifier
def test_production_verifier_raises_unavailable():
    v = ear.ProductionExternalAuthorityVerifier()
    with pytest.raises(ear.ExternalAuthorityUnavailable):
        v.verify_authority_evidence(readiness=_make_synthetic(), evidence_reference="EV", now_utc=NOW)


def test_select_production_verifier_unavailable():
    verifier, reasons = ear.select_production_verifier()
    assert verifier is None
    assert reasons == ("EAR-PRODUCTION-VERIFIER-UNAVAILABLE",)


def test_synthetic_verifier_is_module_token_gated():
    with pytest.raises(ear.ExternalAuthorityUnavailable):
        ear.SyntheticExternalAuthorityVerifier()  # no token
    v = ear.new_synthetic_verifier()
    assert isinstance(v, ear.ExternalAuthorityVerifier)
    assert ear.require_verifier_only(v) == ()


# ============================================================================ issuer-attestation boundary
def _make_ia_ref(**overrides):
    kw = dict(
        issuer_identity="ref://issuer/external-governed",
        authority_root_reference="ref://authority-root/external",
        attestation_evidence_reference="ref://attestation-evidence/ev-03",
        evidence_location_reference="ref://evidence-store/loc",
        verifier_result="VERIFIED_BY_EXTERNAL_AUTHORITY",
        lifecycle_readiness="AUTHORITY_READY",
    )
    kw.update(overrides)
    return ear.new_issuer_attestation_reference(**kw)


def test_issuer_attestation_reference_valid():
    ref = _make_ia_ref()
    assert ear.validate_issuer_attestation_reference(ref, now_utc=NOW) == ()


def test_issuer_attestation_self_issued_rejected():
    ref = _make_ia_ref(verifier_result="SIGNED_BY_HERMES")
    assert "EAR-IA-SELF-ISSUED" in ear.validate_issuer_attestation_reference(ref, now_utc=NOW)


def test_issuer_attestation_missing_fields_rejected():
    assert "EAR-IA-ISSUER-MISSING" in ear.validate_issuer_attestation_reference(
        _make_ia_ref(issuer_identity=""), now_utc=NOW)
    assert "EAR-IA-AUTHORITY-ROOT-MISSING" in ear.validate_issuer_attestation_reference(
        _make_ia_ref(authority_root_reference=""), now_utc=NOW)
    assert "EAR-IA-ATTESTATION-EVIDENCE-MISSING" in ear.validate_issuer_attestation_reference(
        _make_ia_ref(attestation_evidence_reference=""), now_utc=NOW)


def test_issuer_attestation_inline_payload_rejected():
    ref = _make_ia_ref(attestation_evidence_reference="{\"inline\": \"payload\"}")
    assert "EAR-IA-REFERENCE-IS-INLINE-PAYLOAD" in ear.validate_issuer_attestation_reference(ref, now_utc=NOW)


def test_issuer_attestation_digest_tamper_rejected():
    ref = _make_ia_ref()
    tampered = dataclasses.replace(ref, authority_root_reference="ref://attacker")
    assert "EAR-IA-DIGEST-TAMPER" in ear.validate_issuer_attestation_reference(tampered, now_utc=NOW)


def test_eventual_evidence_record_ids():
    assert ear.eventual_evidence_record_ids() == {"authority_readiness": "EV-03-AUTHORITY-READINESS"}


# ============================================================================ secret-scan
def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def test_no_secrets_in_new_artifacts():
    secret_patterns = [
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        re.compile(r"(?i)\bAKIA[0-9A-Z]{16}\b"),                       # AWS access key id
        re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}"),
        re.compile(r"(?i)(password|passwd|secret|api[_-]?key)\s*[:=]\s*['\"][^'\"]{6,}['\"]"),
        re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"),                       # github token
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
    attr_accesses = set()   # e.g. os.environ / os.getenv
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

    # No I/O-capable modules imported (secrets/hashlib/hmac/json/re/ast/datetime/dataclasses/abc are pure).
    for bad in _FORBIDDEN_IMPORTS:
        assert bad not in imported, f"forbidden import: {bad}"
    # No I/O-capable call reachable in the AST (open/getenv/system/popen/socket/urlopen/run/Popen/...).
    assert not (_FORBIDDEN_CALLS & called_names), f"forbidden call(s): {_FORBIDDEN_CALLS & called_names}"
    # No os.environ / os.getenv style attribute access.
    for bad in ("os.environ", "os.getenv", "os.system", "os.popen"):
        assert bad not in attr_accesses, f"forbidden attribute access: {bad}"


def test_import_performs_no_io():
    # re-importing the module (already imported) triggers no I/O; a fresh import via importlib also must not.
    import importlib
    mod = importlib.reload(ear)
    assert mod.production_external_authority_available() is False


# ============================================================================ positive full flow
def test_full_synthetic_flow_accepts_in_test_only_and_byte_identical():
    r = _make_synthetic()
    before = r.to_dict()
    assert ear.validate_authority_readiness(r, now_utc=NOW, real_mode=False) == ()
    v = ear.new_synthetic_verifier()
    ok, reasons = v.verify_authority_evidence(
        readiness=r, evidence_reference="EV-03-AUTHORITY-READINESS", now_utc=NOW)
    assert ok is True
    assert reasons == ()
    # readiness values remain byte-identical after validation (no mutation / repair).
    assert r.to_dict() == before


def test_same_flow_under_real_candidate_rejects():
    r, reasons = ear.resolve_authority_readiness(mode="REAL_CANDIDATE", **_synthetic_kwargs())
    assert r is None
    assert "EAR-REAL-AUTHORITY-UNAVAILABLE" in reasons


def test_readiness_carries_no_signing_material():
    r = _make_synthetic()
    for cap in ear.FORBIDDEN_CAPABILITY_NAMES:
        assert not hasattr(r, cap), f"readiness must not expose {cap}"
    # and its own validator confirms no caller signing material is present.
    assert "EAR-CALLER-SIGNING-MATERIAL" not in ear.validate_authority_readiness(
        r, now_utc=NOW, real_mode=False)


# ============================================================================ C-PR122-EAR-PROMOTION fresh-mint attack tests
def _real_kwargs(**over):
    kw = dict(_synthetic_kwargs())
    kw.pop("mode", None)
    return kw


def test_public_constructor_rejects_real_classification():
    # §3B: the public constructor is SYNTHETIC-ONLY — a caller cannot confer a real classification (not repaired).
    with pytest.raises(ear.EARPromotionForbidden):
        ear.new_external_authority_readiness(
            synthetic_or_real_classification="REAL", readiness_state="AUTHORITY_READY",
            authority_class="GOVERNED_EXTERNAL_AUTHORITY_READINESS", provenance="p",
            external_authority_reference="ref://authority", trust_anchor_reference="ref://anchor",
            resolver_reference="ref://resolver", issuer_identity_reference="ref://issuer",
            attestation_policy_reference="ref://policy", evidence_references=("ref://ev",),
            issued_or_observed_utc=NOW, validity_end_utc=VALIDITY_END)


@pytest.mark.parametrize("cls", ["REAL", "real", "Real", " REAL ", "GOVERNED_EXTERNAL_AUTHORITY_READINESS", "PRODUCTION"])
def test_public_constructor_rejects_all_non_synthetic_classifications(cls):
    with pytest.raises(ear.EARPromotionForbidden):
        ear.new_external_authority_readiness(
            synthetic_or_real_classification=cls, readiness_state="AUTHORITY_READY",
            authority_class="GOVERNED_EXTERNAL_AUTHORITY_READINESS", provenance="p",
            external_authority_reference="ref://a", trust_anchor_reference="ref://t", resolver_reference="ref://r",
            issuer_identity_reference="ref://i", attestation_policy_reference="ref://p",
            evidence_references=("ref://e",), issued_or_observed_utc=NOW, validity_end_utc=VALIDITY_END)


def test_fresh_mint_real_via_reflective_issuer_fails_on_unavailability_not_seal():
    # §3C.2/§3C.3: a caller reaches the reflective module issuer and mints a STRUCTURALLY VALID, CORRECTLY
    # SEALED 'REAL' record. It must STILL fail real-mode validation — because production authority is
    # unavailable, NOT because the seal is invalid.
    base = _make_synthetic()
    real = dataclasses.replace(base, synthetic_or_real_classification="REAL")
    real = dataclasses.replace(real, readiness_digest=real.recompute_digest())
    real = dataclasses.replace(real, seal=ear._MODULE_ISSUER.seal_readiness(real))
    assert ear._MODULE_ISSUER.verify(real) is True          # the seal IS valid (correctly minted)
    reasons = ear.validate_authority_readiness(real, now_utc=NOW, real_mode=True)
    assert reasons == ("EAR-PRODUCTION-AUTHORITY-UNAVAILABLE",)  # fails on availability, not seal
    assert "EAR-SEAL-INVALID" not in reasons


def test_object_new_direct_construction_then_real_mode_fails_closed():
    # §3C.5: bypass the constructor entirely via object.__new__ + a valid module seal.
    base = _make_synthetic()
    obj = object.__new__(ear.ExternalAuthorityReadiness)
    for f in dataclasses.fields(base):
        object.__setattr__(obj, f.name, getattr(base, f.name))
    object.__setattr__(obj, "synthetic_or_real_classification", "REAL")
    object.__setattr__(obj, "readiness_digest", obj.recompute_digest())
    object.__setattr__(obj, "seal", ear._MODULE_ISSUER.seal_readiness(obj))
    assert ear.validate_authority_readiness(obj, now_utc=NOW, real_mode=True) \
        == ("EAR-PRODUCTION-AUTHORITY-UNAVAILABLE",)


@pytest.mark.parametrize("copier", [lambda o: __import__("copy").copy(o), lambda o: __import__("copy").deepcopy(o)])
def test_copied_sealed_real_record_fails_closed(copier):
    # §3C.6: copy/deepcopy a valid-sealed REAL record -> still fails on production unavailability.
    base = _make_synthetic()
    real = dataclasses.replace(base, synthetic_or_real_classification="REAL")
    real = dataclasses.replace(real, readiness_digest=real.recompute_digest())
    real = dataclasses.replace(real, seal=ear._MODULE_ISSUER.seal_readiness(real))
    assert ear.validate_authority_readiness(copier(real), now_utc=NOW, real_mode=True) \
        == ("EAR-PRODUCTION-AUTHORITY-UNAVAILABLE",)


def test_monkeypatched_availability_true_without_resolver_still_blocked_by_resolve(monkeypatch):
    # §3C.4/§7: forcing production_external_authority_available()->True does NOT let a caller synthesise a real
    # record: resolve_authority_readiness(REAL) is unconditionally fail-closed BEFORE any availability check.
    monkeypatch.setattr(ear, "production_external_authority_available", lambda: True)
    r, reasons = ear.resolve_authority_readiness(mode="REAL_CANDIDATE", **_synthetic_kwargs())
    assert r is None and "EAR-REAL-AUTHORITY-UNAVAILABLE" in reasons


def test_real_mode_gate_is_first_and_independent_of_seal(monkeypatch):
    # §4 ordering: the production-availability gate precedes seal/classification/reference checks. Prove it by
    # feeding a record with a BROKEN seal AND bad refs in real mode -> the FIRST (and only) reason is the gate.
    base = _make_synthetic()
    broken = dataclasses.replace(base, seal="0" * 64, external_authority_reference="")
    assert ear.validate_authority_readiness(broken, now_utc=NOW, real_mode=True) \
        == ("EAR-PRODUCTION-AUTHORITY-UNAVAILABLE",)


# ==================================================== C-PR122-EAR-AVAILABILITY-SOLE-ROOT verification-result gate
# Helper: mint a SYNTHETIC verification result BOUND to a readiness record (module token supplied internally),
# then let tests mutate/relabel it to prove the binding rejects. It is SYNTHETIC — never real-mode-satisfying.
def _bound_synthetic_result(readiness, **overrides):
    kw = dict(
        readiness_record_digest=readiness.recompute_digest(),
        external_authority_reference=readiness.external_authority_reference,
        trust_anchor_reference=readiness.trust_anchor_reference,
        issuer_reference=readiness.issuer_identity_reference,
        attestation_policy_reference=readiness.attestation_policy_reference,
        evidence_references=tuple(readiness.evidence_references),
        verification_utc=NOW,
        validity_end_utc=VALIDITY_END,
        verification_outcome="VERIFIED",
        provenance="SYNTHETIC_TEST",
    )
    kw.update(overrides)
    kw["test_token"] = ear._READINESS_TOKEN
    return ear.new_synthetic_test_verification_result(**kw)


def _reseal_result(result):
    """Re-apply a VALID module receipt over a mutated result (module-owned key, reflective access) so binding
    checks — not the receipt — are the operative rejection."""
    r = dataclasses.replace(result, result_digest=result.recompute_digest())
    return dataclasses.replace(r, result_receipt=ear._seal_verification_result(r))


# ----- THE decisive test: original bypass is now closed -----
def test_original_bypass_now_fails_verification_unavailable(monkeypatch):
    # ORIGINAL C-PR122-EAR-AVAILABILITY-SOLE-ROOT bypass: availability monkeypatched True + a reflectively
    # sealed REAL record + NO verification result -> must NOT be accepted; the decisive gate is the missing
    # module-boundary production verification result.
    monkeypatch.setattr(ear, "production_external_authority_available", lambda: True)
    base = _make_synthetic()
    real = dataclasses.replace(base, synthetic_or_real_classification="REAL")
    real = dataclasses.replace(real, readiness_digest=real.recompute_digest())
    real = dataclasses.replace(real, seal=ear._MODULE_ISSUER.seal_readiness(real))
    assert ear._MODULE_ISSUER.verify(real) is True   # the seal IS valid
    assert ear.validate_authority_readiness(real, now_utc=NOW, real_mode=True) \
        == ("EAR-PRODUCTION-VERIFICATION-UNAVAILABLE",)


def test_boolean_alone_is_insufficient(monkeypatch):
    # availability True ALONE -> fail; availability True + valid local seal -> fail; availability True + caller
    # assertion of classification -> fail. Each returns EAR-PRODUCTION-VERIFICATION-UNAVAILABLE.
    monkeypatch.setattr(ear, "production_external_authority_available", lambda: True)
    base = _make_synthetic()
    # (a) availability True alone, plain synthetic record.
    assert ear.validate_authority_readiness(base, now_utc=NOW, real_mode=True) \
        == ("EAR-PRODUCTION-VERIFICATION-UNAVAILABLE",)
    # (b) availability True + a valid module seal over a REAL-relabelled record.
    real = dataclasses.replace(base, synthetic_or_real_classification="REAL")
    real = dataclasses.replace(real, readiness_digest=real.recompute_digest())
    real = dataclasses.replace(real, seal=ear._MODULE_ISSUER.seal_readiness(real))
    assert ear.validate_authority_readiness(real, now_utc=NOW, real_mode=True) \
        == ("EAR-PRODUCTION-VERIFICATION-UNAVAILABLE",)


def test_production_verifier_configured_false():
    assert ear.production_verifier_configured() is False


def test_verify_external_authority_readiness_fails_closed():
    r = _make_synthetic()
    vr, reasons = ear.verify_external_authority_readiness(r)
    assert vr is None
    assert reasons == ("EAR-PRODUCTION-VERIFICATION-UNAVAILABLE",)


def test_verify_external_authority_readiness_fails_closed_even_if_available(monkeypatch):
    # Monkeypatching availability True must NOT make a verification result appear — the verifier boundary is a
    # SEPARATE flag and is not configured.
    monkeypatch.setattr(ear, "production_external_authority_available", lambda: True)
    vr, reasons = ear.verify_external_authority_readiness(_make_synthetic())
    assert vr is None
    assert reasons == ("EAR-PRODUCTION-VERIFICATION-UNAVAILABLE",)


def test_verify_external_authority_readiness_takes_no_caller_verifier():
    # A caller cannot inject a verifier: the module-controlled operation accepts only the readiness record.
    import inspect
    sig = inspect.signature(ear.verify_external_authority_readiness)
    assert list(sig.parameters) == ["readiness"]


# ----- verification-result CREATION attacks -----
def test_public_direct_result_construction_has_invalid_receipt():
    # A caller directly constructs a REAL, VERIFIED result (all fields set) — but cannot forge the module
    # receipt, so it never passes validate_production_verification_result.
    r = _make_synthetic()
    forged = ear.ProductionAuthorityVerificationResult(
        contract_version=ear.CONTRACT_VERSION, lifecycle_stage=ear.LIFECYCLE_STAGE,
        verification_mode="REAL", readiness_record_digest=r.recompute_digest(),
        external_authority_reference=r.external_authority_reference,
        trust_anchor_reference=r.trust_anchor_reference, issuer_reference=r.issuer_identity_reference,
        attestation_policy_reference=r.attestation_policy_reference,
        evidence_references=tuple(r.evidence_references), verification_utc=NOW, validity_end_utc=VALIDITY_END,
        verifier_identity_reference="ref://verifier/forged", verification_outcome="VERIFIED",
        provenance="FORGED", fault_code="", synthetic_or_real_classification="REAL",
        result_digest="", result_receipt="")
    forged = dataclasses.replace(forged, result_digest=forged.recompute_digest())
    forged = dataclasses.replace(forged, result_receipt="0" * 64)   # caller cannot mint a valid receipt
    reasons = ear.validate_production_verification_result(forged, readiness=r, now_utc=NOW)
    assert "EAR-VR-RECEIPT-INVALID" in reasons


def test_mint_helper_refuses_real_classification():
    # §4: the token-gated mint helper is SYNTHETIC-ONLY — a caller-selected real classification is refused,
    # mirroring EARPromotionForbidden. A caller cannot mint a REAL result.
    r = _make_synthetic()
    with pytest.raises(ear.EARPromotionForbidden):
        _bound_synthetic_result(r, synthetic_or_real_classification="REAL")


def test_mint_helper_requires_module_token():
    # Without the module token the caller cannot mint any verification result.
    r = _make_synthetic()
    with pytest.raises(ear.EARPromotionForbidden):
        ear.new_synthetic_test_verification_result(
            readiness_record_digest=r.recompute_digest(),
            external_authority_reference=r.external_authority_reference,
            trust_anchor_reference=r.trust_anchor_reference, issuer_reference=r.issuer_identity_reference,
            attestation_policy_reference=r.attestation_policy_reference,
            evidence_references=tuple(r.evidence_references), verification_utc=NOW, validity_end_utc=VALIDITY_END)


def test_reflective_mint_of_real_result_still_rejected_synthetic():
    # A caller reaches the reflective module issuer and RESEALS a result relabelled to REAL. The receipt binds
    # the classification, so the reseal is genuinely valid — yet validate still rejects it: the module helper
    # only ever mints SYNTHETIC, and here the reseal keeps a valid receipt over classification REAL. Prove that
    # even with a valid receipt the classification path and binding hold: a REAL reseal passes the receipt but
    # nothing here makes it a genuine external verification — the record digest still binds.
    r = _make_synthetic()
    synth = _bound_synthetic_result(r)
    # reseal as REAL via reflective module issuer.
    real = dataclasses.replace(synth, synthetic_or_real_classification="REAL")
    real = _reseal_result(real)
    assert ear._verify_result_receipt(real) is True         # reflective reseal IS valid
    # but it is a fabricated result, not one obtained through verify_external_authority_readiness (which is
    # UNAVAILABLE). validate accepts it ONLY as far as its own checks go — prove binding still bites when refs
    # differ (record mismatch) even under a valid REAL receipt.
    other_r, _ = ear.new_synthetic_test_readiness(**_synthetic_kwargs(
        external_authority_reference="ref://authority-root/OTHER"))
    assert "EAR-VR-RECORD-MISMATCH" in ear.validate_production_verification_result(
        real, readiness=other_r, now_utc=NOW)
    # and a SYNTHETIC classification is rejected outright.
    assert "EAR-VR-SYNTHETIC" in ear.validate_production_verification_result(synth, readiness=r, now_utc=NOW)


def test_reflective_real_result_never_reaches_real_acceptance_via_validate(monkeypatch):
    # DECISIVE: even a reflectively-resealed, correctly-bound REAL result cannot enter real-mode acceptance,
    # because validate_authority_readiness obtains the result ONLY through the module-controlled boundary
    # (verify_external_authority_readiness), which fails closed. A caller CANNOT supply the result to validate.
    monkeypatch.setattr(ear, "production_external_authority_available", lambda: True)
    r = _make_synthetic()
    synth = _bound_synthetic_result(r)
    real = _reseal_result(dataclasses.replace(synth, synthetic_or_real_classification="REAL"))
    assert ear._verify_result_receipt(real) is True
    # validate takes NO verification_result / verifier parameter -> the fabricated result is simply never
    # consulted; real mode fails closed on the module boundary.
    import inspect
    params = list(inspect.signature(ear.validate_authority_readiness).parameters)
    assert "verification_result" not in params and "verifier" not in params
    assert ear.validate_authority_readiness(r, now_utc=NOW, real_mode=True) \
        == ("EAR-PRODUCTION-VERIFICATION-UNAVAILABLE",)


def test_copied_result_for_different_record_rejected():
    r = _make_synthetic()
    real = _reseal_result(dataclasses.replace(_bound_synthetic_result(r),
                                              synthetic_or_real_classification="REAL"))
    other_r, _ = ear.new_synthetic_test_readiness(**_synthetic_kwargs(
        evidence_references=("EV-03-AUTHORITY-READINESS", "EV-EXTRA")))
    reasons = ear.validate_production_verification_result(real, readiness=other_r, now_utc=NOW)
    assert "EAR-VR-RECORD-MISMATCH" in reasons


def test_duck_typed_and_object_new_result_rejected():
    r = _make_synthetic()

    class DuckResult:
        def recompute_digest(self):
            return "x"
    assert ear.validate_production_verification_result(DuckResult(), readiness=r, now_utc=NOW) \
        == ("EAR-VR-WRONG-TYPE",)
    assert ear.validate_production_verification_result(object(), readiness=r, now_utc=NOW) \
        == ("EAR-VR-WRONG-TYPE",)
    # object.__new__ of the real type but no valid receipt -> receipt invalid.
    obj = object.__new__(ear.ProductionAuthorityVerificationResult)
    synth = _bound_synthetic_result(r)
    for f in dataclasses.fields(synth):
        object.__setattr__(obj, f.name, getattr(synth, f.name))
    object.__setattr__(obj, "synthetic_or_real_classification", "REAL")
    object.__setattr__(obj, "result_digest", obj.recompute_digest())
    object.__setattr__(obj, "result_receipt", "0" * 64)
    assert "EAR-VR-RECEIPT-INVALID" in ear.validate_production_verification_result(obj, readiness=r, now_utc=NOW)


def test_caller_fake_verifier_never_consulted():
    # A caller-provided "verifier" is never consulted: verify_external_authority_readiness takes no caller
    # verifier, so a fake that would "attest True" has no path in.
    class FakeVerifier:
        def verify_authority_evidence(self, *, readiness, evidence_reference, now_utc):
            return (True, ())
    r = _make_synthetic()
    # there is no parameter to pass it through.
    vr, reasons = ear.verify_external_authority_readiness(r)
    assert vr is None and reasons == ("EAR-PRODUCTION-VERIFICATION-UNAVAILABLE",)


# ----- binding attacks: mutate a bound synthetic result, then RESEAL as REAL, prove each mismatch bites -----
def _real_bound(readiness, **overrides):
    """Bound REAL result with a VALID reflective receipt (so the receipt is NOT the operative rejection)."""
    synth = _bound_synthetic_result(readiness, **overrides)
    return _reseal_result(dataclasses.replace(synth, synthetic_or_real_classification="REAL"))


def test_binding_record_digest_mismatch():
    r = _make_synthetic()
    bad = _reseal_result(dataclasses.replace(_real_bound(r), readiness_record_digest="deadbeef"))
    assert "EAR-VR-RECORD-MISMATCH" in ear.validate_production_verification_result(bad, readiness=r, now_utc=NOW)


def test_binding_authority_ref_mismatch():
    r = _make_synthetic()
    bad = _reseal_result(dataclasses.replace(_real_bound(r), external_authority_reference="ref://attacker"))
    assert "EAR-VR-AUTHORITY-MISMATCH" in ear.validate_production_verification_result(bad, readiness=r, now_utc=NOW)


def test_binding_trust_anchor_mismatch():
    r = _make_synthetic()
    bad = _reseal_result(dataclasses.replace(_real_bound(r), trust_anchor_reference="ref://attacker"))
    assert "EAR-VR-TRUST-ANCHOR-MISMATCH" in ear.validate_production_verification_result(bad, readiness=r, now_utc=NOW)


def test_binding_issuer_mismatch():
    r = _make_synthetic()
    bad = _reseal_result(dataclasses.replace(_real_bound(r), issuer_reference="ref://attacker"))
    assert "EAR-VR-ISSUER-MISMATCH" in ear.validate_production_verification_result(bad, readiness=r, now_utc=NOW)


def test_binding_policy_mismatch():
    r = _make_synthetic()
    bad = _reseal_result(dataclasses.replace(_real_bound(r), attestation_policy_reference="ref://attacker"))
    assert "EAR-VR-POLICY-MISMATCH" in ear.validate_production_verification_result(bad, readiness=r, now_utc=NOW)


def test_binding_evidence_ref_mismatch():
    r = _make_synthetic()
    bad = _reseal_result(dataclasses.replace(_real_bound(r), evidence_references=("EV-DIFFERENT",)))
    assert "EAR-VR-EVIDENCE-MISMATCH" in ear.validate_production_verification_result(bad, readiness=r, now_utc=NOW)


def test_binding_classification_synthetic_rejected():
    r = _make_synthetic()
    synth = _bound_synthetic_result(r)   # classification SYNTHETIC, valid receipt
    assert "EAR-VR-SYNTHETIC" in ear.validate_production_verification_result(synth, readiness=r, now_utc=NOW)


def test_binding_stage_mismatch():
    r = _make_synthetic()
    bad = _reseal_result(dataclasses.replace(_real_bound(r), lifecycle_stage=4))
    assert "EAR-VR-STAGE-MISMATCH" in ear.validate_production_verification_result(bad, readiness=r, now_utc=NOW)


def test_binding_contract_version_mismatch():
    r = _make_synthetic()
    bad = _reseal_result(dataclasses.replace(_real_bound(r), contract_version="99"))
    assert "EAR-VR-CONTRACT-VERSION" in ear.validate_production_verification_result(bad, readiness=r, now_utc=NOW)


def test_binding_not_verified_rejected():
    r = _make_synthetic()
    bad = _reseal_result(dataclasses.replace(_real_bound(r), verification_outcome="FAILED"))
    assert "EAR-VR-NOT-VERIFIED" in ear.validate_production_verification_result(bad, readiness=r, now_utc=NOW)


def test_binding_revoked_rejected():
    r = _make_synthetic()
    bad = _reseal_result(dataclasses.replace(_real_bound(r), verification_outcome="REVOKED"))
    assert "EAR-VR-REVOKED" in ear.validate_production_verification_result(bad, readiness=r, now_utc=NOW)


def test_binding_expired_rejected():
    r = _make_synthetic()
    bad = _reseal_result(dataclasses.replace(_real_bound(r), validity_end_utc=PAST))
    assert "EAR-VR-EXPIRED" in ear.validate_production_verification_result(bad, readiness=r, now_utc=NOW)


def test_binding_non_utc_rejected():
    r = _make_synthetic()
    bad = _reseal_result(dataclasses.replace(_real_bound(r), verification_utc="2026-07-27T00:00:00"))
    assert "EAR-VR-UTC-INVALID" in ear.validate_production_verification_result(bad, readiness=r, now_utc=NOW)


def test_binding_digest_tamper_rejected():
    r = _make_synthetic()
    real = _real_bound(r)
    # tamper the digest without reselaing -> digest recompute mismatch AND receipt invalid.
    bad = dataclasses.replace(real, result_digest="0" * 64)
    reasons = ear.validate_production_verification_result(bad, readiness=r, now_utc=NOW)
    assert "EAR-VR-DIGEST-TAMPER" in reasons


# ----- verifier boundary / select_production_verifier fail closed -----
def test_select_production_verifier_still_fails_closed_under_availability(monkeypatch):
    monkeypatch.setattr(ear, "production_external_authority_available", lambda: True)
    verifier, reasons = ear.select_production_verifier()
    assert verifier is None
    assert reasons == ("EAR-PRODUCTION-VERIFIER-UNAVAILABLE",)


def test_no_verifier_exposing_signing_accepted():
    # A verification result carries NO forbidden capability; the verifier-only boundary still rejects signing.
    r = _make_synthetic()
    synth = _bound_synthetic_result(r)
    for cap in ear.FORBIDDEN_CAPABILITY_NAMES:
        assert not hasattr(synth, cap), f"verification result must not expose {cap}"


# ----- positive synthetic: explicitly non-real -----
def test_positive_synthetic_result_is_clearly_synthetic():
    r = _make_synthetic()
    synth = _bound_synthetic_result(r)
    assert synth.synthetic_or_real_classification == "SYNTHETIC"
    # a synthetic result is NEVER treated as real: it is rejected by validate_production_verification_result.
    assert "EAR-VR-SYNTHETIC" in ear.validate_production_verification_result(synth, readiness=r, now_utc=NOW)
    # and synthetic readiness still validates cleanly in synthetic mode.
    assert ear.validate_authority_readiness(r, now_utc=NOW, real_mode=False) == ()


def test_verification_result_carries_no_secrets():
    r = _make_synthetic()
    synth = _bound_synthetic_result(r)
    d = synth.to_dict()
    blob = repr(d)
    for pat in (re.compile(r"-----BEGIN"), re.compile(r"(?i)private[_-]?key"), re.compile(r"(?i)\bbearer\b")):
        assert pat.search(blob) is None
