"""FW-08 F2-R1 external producer-registry root-of-trust tests (§23).

WO-HELM-HERMES-FW08-F2-R1-EXTERNAL-PRODUCER-REGISTRY-ROOT-OF-TRUST-IMPLEMENTATION-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-21. Contract version: 1.

These tests build NO real image, run NO real docker/SBOM/scan, publish NOTHING, deploy NOTHING, wire NO
runtime, create NO live key/secret/KMS call/`/etc` write, and add NO third-party dependency (stdlib only).
They prove the F2-R1 correction: candidate-readiness accepts a producer registry ONLY via an ACTIVATED HANDLE
minted by resolution through a governed EXTERNAL authority boundary and sealed by an ActivationAuthority key
the caller does not hold. A registry checksum proves INTEGRITY, not external AUTHENTICITY.

The prevention is STRUCTURAL (unforgeable HMAC seal + a resolver that cannot return anything in real mode),
not a value check. The decisive test is the CALLER-MINTED-AUTHORITY ATTACK.

Run: cd <worktree> && python3 -m pytest tests/test_fw08_f2_r1_registry_root_of_trust_v1.py -q
"""
from __future__ import annotations

import dataclasses

import pytest

import design.hermes_fw08_producer_registry_v1 as pr
import design.hermes_fw08_authority_root_v1 as ar
import design.hermes_fw08_approval_record_v1 as apr
import design.hermes_fw08_revocation_v1 as rv
import design.hermes_fw08_authorised_registry_manifest_v1 as mf
import design.hermes_fw08_authority_resolver_v1 as rs
import design.hermes_fw08_activated_registry_handle_v1 as arh
import design.hermes_fw08_registry_activation_v1 as ract
import design.hermes_fw08_active_import_evidence_v1 as ai

# Reuse the F-2 fixture builders (frozen registry, anchors, bundle, ai_evidence) so the positive delegation
# path exercises real, valid F-2 artifacts.
import tests.test_fw08_f2_independent_anchors_v1 as f2


# ============================================================================ constants + fixtures
APP = "hermes"
NOW = "2026-07-21T12:00:00+00:00"
TRUST_DOMAIN = "hermes-fw08-trust-domain-v1"
ROOT_ID = "hermes-fw08-authority-root-v1"
APPROVER_A = "external-approver-alpha"
APPROVER_B = "external-approver-beta"
REG_ID = f2.REG_POLICY               # the registry's policy version is its id
AUTHOR_ID = "hermes-fw08-registry-author"
FUTURE = "2027-07-21T00:00:00+00:00"
PAST = "2026-07-20T00:00:00+00:00"


def _registry():
    """A frozen, well-formed governed registry (reuses the F-2 helper)."""
    return f2._registry(real=False, freeze=True, include_oci=True)


def _authority_root(*, classification="SYNTHETIC_TEST_AUTHORITY", threshold=2,
                    approvers=(APPROVER_A, APPROVER_B), application=APP, trust_domain=TRUST_DOMAIN,
                    valid_from=PAST, valid_until=FUTURE, root_id=ROOT_ID, **over):
    kw = dict(
        application=application, authority_root_id=root_id,
        authority_type="EXTERNAL_GOVERNANCE", trust_domain_id=trust_domain, policy_version="1",
        allowed_registry_contract_versions=("1",), allowed_registry_policy_versions=(REG_ID,),
        allowed_registry_ids=(REG_ID,), registry_id_derivation_rule="",
        allowed_producer_applications=(APP,), approval_threshold=threshold,
        authorised_approver_identities=tuple(approvers), approver_key_references=("kms-ref-alpha", "kms-ref-beta"),
        authority_valid_from_utc=valid_from, authority_valid_until_utc=valid_until,
        permanence_rationale="", revocation_set_reference="revset-ref-1",
        source_provenance="SYNTHETIC_TEST", source_version="1", audit_reference="AUDIT-F2R1",
        authority_root_classification=classification, activation_policy="GOVERNED",
    )
    kw.update(over)
    return ar.new_authority_root(**kw)


def _approval(approver_id, *, registry_digest, decision="APPROVE", expiry=FUTURE, **over):
    kw = dict(
        approver_id=approver_id, approver_role="GOVERNANCE_APPROVER", authority_root_id=ROOT_ID,
        registry_id=REG_ID, registry_digest=registry_digest, policy_version=REG_ID,
        approval_decision=decision, approval_utc=PAST, expiry_utc=expiry,
        approval_evidence_reference="approval-evidence-ref",
    )
    kw.update(over)
    return apr.new_approval(**kw)


def _manifest(registry, *, root_id=ROOT_ID, root_digest, approvals=None, valid_from=PAST, valid_until=FUTURE,
              revocation_state="NONE", registry_id=REG_ID, application=APP, **over):
    reg_digest = registry.canonical_digest()
    pset = registry.producer_set_digest()
    if approvals is None:
        approvals = (_approval(APPROVER_A, registry_digest=reg_digest),
                     _approval(APPROVER_B, registry_digest=reg_digest))
    kw = dict(
        authority_root_id=root_id, registry_id=registry_id, registry_contract_version="1",
        registry_policy_version=registry.registry_policy_version, registry_digest=reg_digest,
        producer_set_digest=pset, application=application,
        allowed_gate_ids=("STAGE_B_ACTIVE_IMPORT",), allowed_evidence_types=("ACTIVE_IMPORT",),
        valid_from_utc=valid_from, valid_until_utc=valid_until, manifest_generation_utc=PAST,
        approval_records=tuple(approvals), approval_threshold_result=True,
        revocation_state=revocation_state, provenance_reference=AUTHOR_ID,
        authority_root_digest=root_digest,
    )
    kw.update(over)
    return mf.new_manifest(**kw)


def _empty_revset():
    return rv.new_revocation_set(())


def _synthetic_resolver(registry, *, root=None, manifest=None):
    root = root or _authority_root()
    manifest = manifest or _manifest(registry, root_digest=root.authority_root_digest)
    return rs.SyntheticAuthorityResolver(authority_root=root, manifest=manifest), root, manifest


def _activate(registry=None, *, mode="TEST_ONLY", authority=None, root=None, manifest=None,
              revocation_set=None):
    registry = registry if registry is not None else _registry()
    resolver, root, manifest = _synthetic_resolver(registry, root=root, manifest=manifest)
    authority = authority or arh.ActivationAuthority()
    handle, reasons = ract.activate_registry(
        registry=registry, resolver=resolver, activation_authority=authority,
        authority_root_id=ROOT_ID, trust_domain_id=TRUST_DOMAIN, registry_id=REG_ID,
        revocation_set=revocation_set if revocation_set is not None else _empty_revset(),
        now_utc=NOW, candidate_use_mode=mode, application=APP,
    )
    return handle, reasons, registry, authority, root, manifest


# ============================================================================ §7 authority root
def test_authority_root_synthetic_accepted_in_test_mode():
    root = _authority_root()
    assert ar.validate_authority_root(root, now_utc=NOW, application=APP, trust_domain_id=TRUST_DOMAIN) == ()


def test_authority_root_rejections():
    # untrusted / revoked classification
    assert "AR-UNTRUSTED" in ar.validate_authority_root(
        _authority_root(classification="UNTRUSTED_AUTHORITY"),
        now_utc=NOW, application=APP, trust_domain_id=TRUST_DOMAIN)
    assert "AR-REVOKED" in ar.validate_authority_root(
        _authority_root(classification="REVOKED_AUTHORITY"),
        now_utc=NOW, application=APP, trust_domain_id=TRUST_DOMAIN)
    # bad classification
    assert "AR-BAD-CLASSIFICATION" in ar.validate_authority_root(
        _authority_root(classification="MADE_UP"),
        now_utc=NOW, application=APP, trust_domain_id=TRUST_DOMAIN)
    # expired
    assert "AR-EXPIRED" in ar.validate_authority_root(
        _authority_root(valid_until="2026-07-21T00:00:00+00:00"),
        now_utc=NOW, application=APP, trust_domain_id=TRUST_DOMAIN)
    # not yet valid
    assert "AR-NOT-YET-VALID" in ar.validate_authority_root(
        _authority_root(valid_from="2026-07-21T18:00:00+00:00"),
        now_utc=NOW, application=APP, trust_domain_id=TRUST_DOMAIN)
    # wrong application / trust domain
    assert "AR-APPLICATION-MISMATCH" in ar.validate_authority_root(
        _authority_root(), now_utc=NOW, application="other", trust_domain_id=TRUST_DOMAIN)
    assert "AR-TRUST-DOMAIN-MISMATCH" in ar.validate_authority_root(
        _authority_root(), now_utc=NOW, application=APP, trust_domain_id="other-domain")
    # wildcard approver
    assert "AR-WILDCARD-APPROVER" in ar.validate_authority_root(
        _authority_root(approvers=(APPROVER_A, "*")),
        now_utc=NOW, application=APP, trust_domain_id=TRUST_DOMAIN)
    # threshold invalid
    assert "AR-THRESHOLD-INVALID" in ar.validate_authority_root(
        _authority_root(threshold=0), now_utc=NOW, application=APP, trust_domain_id=TRUST_DOMAIN)
    # digest tamper
    root = _authority_root()
    tampered = dataclasses.replace(root, audit_reference="ALTERED")
    assert "AR-DIGEST-TAMPER" in ar.validate_authority_root(
        tampered, now_utc=NOW, application=APP, trust_domain_id=TRUST_DOMAIN)


# ============================================================================ §8 manifest
def test_manifest_accept_and_rejections():
    reg = _registry()
    root = _authority_root()
    man = _manifest(reg, root_digest=root.authority_root_digest)
    assert mf.validate_manifest_against_registry(
        man, registry=reg, authority_root=root, now_utc=NOW, application=APP) == ()

    # registry-digest mismatch: mutate an independent second registry with fewer producers.
    other = f2._registry(freeze=True, include_oci=False)
    assert "MF-REGISTRY-DIGEST-MISMATCH" in mf.validate_manifest_against_registry(
        man, registry=other, authority_root=root, now_utc=NOW, application=APP)

    # wrong registry id
    man_badid = _manifest(reg, root_digest=root.authority_root_digest, registry_id="not-the-registry")
    assert "MF-REGISTRY-ID-MISMATCH" in mf.validate_manifest_against_registry(
        man_badid, registry=reg, authority_root=root, now_utc=NOW, application=APP)

    # wrong policy/contract version
    man_pol = _manifest(reg, root_digest=root.authority_root_digest, registry_contract_version="9")
    assert "MF-POLICY-MISMATCH" in mf.validate_manifest_against_registry(
        man_pol, registry=reg, authority_root=root, now_utc=NOW, application=APP)

    # expired / not-yet-valid
    man_exp = _manifest(reg, root_digest=root.authority_root_digest, valid_until=PAST)
    assert "MF-EXPIRED" in mf.validate_manifest_against_registry(
        man_exp, registry=reg, authority_root=root, now_utc=NOW, application=APP)
    man_nyv = _manifest(reg, root_digest=root.authority_root_digest,
                        valid_from="2026-07-21T18:00:00+00:00")
    assert "MF-NOT-YET-VALID" in mf.validate_manifest_against_registry(
        man_nyv, registry=reg, authority_root=root, now_utc=NOW, application=APP)

    # revoked state
    man_rev = _manifest(reg, root_digest=root.authority_root_digest, revocation_state="REVOKED")
    assert "MF-REVOKED" in mf.validate_manifest_against_registry(
        man_rev, registry=reg, authority_root=root, now_utc=NOW, application=APP)

    # digest tamper
    tampered = dataclasses.replace(man, provenance_reference="ALTERED")
    assert "MF-DIGEST-TAMPER" in mf.validate_manifest_against_registry(
        tampered, registry=reg, authority_root=root, now_utc=NOW, application=APP)

    # authority mismatch
    other_root = _authority_root(root_id="another-root")
    assert "MF-AUTHORITY-MISMATCH" in mf.validate_manifest_against_registry(
        man, registry=reg, authority_root=other_root, now_utc=NOW, application=APP)


# ============================================================================ §14 approvals
def test_approvals_quorum_and_rejections():
    reg = _registry()
    root = _authority_root()
    rd = reg.canonical_digest()
    pids = list(reg._active_map().keys())

    def _eval(records, **over):
        kw = dict(authority_root=root, registry_id=REG_ID, registry_digest=rd, policy_version=REG_ID,
                  registry_author_id=AUTHOR_ID, producer_ids=pids, now_utc=NOW)
        kw.update(over)
        return apr.evaluate_approvals(records, **kw)

    # threshold met
    ok, reasons = _eval((_approval(APPROVER_A, registry_digest=rd), _approval(APPROVER_B, registry_digest=rd)))
    assert ok and reasons == ()
    # threshold unmet
    ok, reasons = _eval((_approval(APPROVER_A, registry_digest=rd),))
    assert not ok and "AP-THRESHOLD-UNMET" in reasons
    # duplicate approver
    ok, reasons = _eval((_approval(APPROVER_A, registry_digest=rd), _approval(APPROVER_A, registry_digest=rd)))
    assert not ok and "AP-DUPLICATE-APPROVER" in reasons
    # producer self-approval
    a_prod = _approval(pids[0], registry_digest=rd)
    ok, reasons = _eval((a_prod, _approval(APPROVER_B, registry_digest=rd)),
                        authority_root=_authority_root(approvers=(pids[0], APPROVER_B)))
    assert not ok and "AP-PRODUCER-SELF-APPROVAL" in reasons
    # registry-author self-approval
    ok, reasons = _eval((_approval(AUTHOR_ID, registry_digest=rd), _approval(APPROVER_B, registry_digest=rd)),
                        authority_root=_authority_root(approvers=(AUTHOR_ID, APPROVER_B)))
    assert not ok and "AP-AUTHOR-SELF-APPROVAL" in reasons
    # wrong digest
    ok, reasons = _eval((_approval(APPROVER_A, registry_digest="deadbeef"),
                         _approval(APPROVER_B, registry_digest=rd)))
    assert not ok and "AP-WRONG-DIGEST" in reasons
    # expired
    ok, reasons = _eval((_approval(APPROVER_A, registry_digest=rd, expiry=PAST),
                         _approval(APPROVER_B, registry_digest=rd)))
    assert not ok and "AP-EXPIRED" in reasons
    # unauthorised approver
    ok, reasons = _eval((_approval("stranger", registry_digest=rd), _approval(APPROVER_B, registry_digest=rd)))
    assert not ok and "AP-APPROVER-UNAUTHORISED" in reasons
    # digest tamper
    tampered = dataclasses.replace(_approval(APPROVER_A, registry_digest=rd), approver_role="X")
    ok, reasons = _eval((tampered, _approval(APPROVER_B, registry_digest=rd)))
    assert not ok and "AP-DIGEST-TAMPER" in reasons


# ============================================================================ §9 resolver
def test_resolver_gate():
    reg = _registry()
    # missing
    assert rs.require_resolver(None, real_candidate_mode=False) == ("RS-RESOLVER-MISSING",)
    # inline mapping (not the interface)
    assert rs.require_resolver({"resolve_authority_root": None}, real_candidate_mode=False) \
        == ("RS-RESOLVER-NOT-INTERFACE",)
    # synthetic in real mode
    resolver, _r, _m = _synthetic_resolver(reg)
    assert "RS-SYNTHETIC-IN-REAL-MODE" in rs.require_resolver(resolver, real_candidate_mode=True)
    # synthetic in test mode is fine
    assert rs.require_resolver(resolver, real_candidate_mode=False) == ()
    # ProductionAuthorityResolver raises ResolverNotConfigured -> activation RA-RESOLVER-NOT-CONFIGURED
    prod = rs.ProductionAuthorityResolver()
    with pytest.raises(rs.ResolverNotConfigured):
        prod.resolve_authority_root(authority_root_id=ROOT_ID, trust_domain_id=TRUST_DOMAIN, now_utc=NOW)
    handle, reasons = ract.activate_registry(
        registry=reg, resolver=prod, activation_authority=arh.ActivationAuthority(),
        authority_root_id=ROOT_ID, trust_domain_id=TRUST_DOMAIN, registry_id=REG_ID,
        revocation_set=_empty_revset(), now_utc=NOW, candidate_use_mode="TEST_ONLY", application=APP)
    assert handle is None and reasons == ("RA-RESOLVER-NOT-CONFIGURED",)


# ============================================================================ §10 activated handle
def test_activated_handle_trust_and_forgery():
    handle, reasons, reg, authority, root, man = _activate()
    assert handle is not None and reasons == ()
    ok, r = arh.is_trusted(handle, activation_authority=authority, now_utc=NOW)
    assert ok and r == ()

    # DIRECTLY constructed handle (no ActivationAuthority seal) -> AH-SEAL-INVALID.
    forged = dataclasses.replace(handle, seal="0" * 64)
    ok, r = arh.is_trusted(forged, activation_authority=authority, now_utc=NOW)
    assert not ok and "AH-SEAL-INVALID" in r

    # a handle sealed by a DIFFERENT ActivationAuthority also fails (key is per-instance).
    ok, r = arh.is_trusted(handle, activation_authority=arh.ActivationAuthority(), now_utc=NOW)
    assert not ok and "AH-SEAL-INVALID" in r

    # forged handle-shaped dict -> AH-WRONG-TYPE.
    ok, r = arh.is_trusted({"lifecycle_state": "ACTIVATED", "seal": "x"},
                           activation_authority=authority, now_utc=NOW)
    assert not ok and r == ("AH-WRONG-TYPE",)

    # expired handle -> AH-EXPIRED.
    ok, r = arh.is_trusted(handle, activation_authority=authority, now_utc="2028-01-01T00:00:00+00:00")
    assert not ok and "AH-EXPIRED" in r

    # checksum tamper -> AH-CHECKSUM-TAMPER (mutate a bound field without recomputing checksum/seal).
    dt = dataclasses.replace(handle, registry_digest="deadbeef")
    ok, r = arh.is_trusted(dt, activation_authority=authority, now_utc=NOW)
    assert not ok and "AH-CHECKSUM-TAMPER" in r

    # not-activated lifecycle -> AH-NOT-ACTIVATED (also re-seal so only lifecycle differs).
    revoked = dataclasses.replace(handle, lifecycle_state="REVOKED")
    revoked = dataclasses.replace(revoked, handle_checksum=revoked.recompute_checksum())
    revoked = dataclasses.replace(revoked, seal=authority._seal(arh._seal_message(revoked)))
    ok, r = arh.is_trusted(revoked, activation_authority=authority, now_utc=NOW)
    assert not ok and "AH-NOT-ACTIVATED" in r

    # get_bound_registry returns the registry only when trusted.
    br, brr = arh.get_bound_registry(handle, activation_authority=authority, now_utc=NOW)
    assert br is reg and brr == ()
    br, brr = arh.get_bound_registry(forged, activation_authority=authority, now_utc=NOW)
    assert br is None and "AH-SEAL-INVALID" in brr


# ============================================================================ §13 storage hardening
def test_storage_hardening_snapshot_and_integrity():
    reg = f2._registry(freeze=True, include_oci=True)
    # post-freeze ordinary mutation of the snapshot is blocked (MappingProxyType).
    with pytest.raises(TypeError):
        reg._frozen_by_id["x"] = None  # type: ignore[index]

    # a retained pre-freeze `_by_id` alias mutation does NOT change resolve()/canonical_digest().
    before = reg.canonical_digest()
    # forcibly mutate the stale pre-freeze dict.
    reg._by_id.pop(f2.BUILD_PID, None)
    reg._by_id["intruder"] = list(reg._frozen_by_id.values())[0]
    assert reg.canonical_digest() == before      # snapshot is the source of truth
    # the removed producer still resolves (snapshot retains it).
    got, _r = reg.resolve(
        producer_id=f2.BUILD_PID, evidence_type="BUILD_RESULT", gate_id="STAGE_B_BUILD_RESULT",
        application=APP, now_utc=NOW)
    assert got is not None
    assert reg.integrity_ok() is True            # snapshot unchanged, integrity holds

    # integrity_ok() detects a forced snapshot divergence BEFORE use (white-box poke on the frozen snapshot).
    reg2 = f2._registry(freeze=True, include_oci=True)
    snap = dict(reg2._frozen_by_id)
    victim = next(iter(snap))
    snap[victim] = dataclasses.replace(snap[victim], audit_reference="FORCED-DIVERGENCE")
    import types as _t
    reg2._frozen_by_id = _t.MappingProxyType(snap)   # bound digest no longer matches the new snapshot
    assert reg2.integrity_ok() is False
    # activation therefore fails RA-REGISTRY-TAMPERED.
    resolver, root, man = _synthetic_resolver(reg2)
    handle, reasons = ract.activate_registry(
        registry=reg2, resolver=resolver, activation_authority=arh.ActivationAuthority(),
        authority_root_id=ROOT_ID, trust_domain_id=TRUST_DOMAIN, registry_id=REG_ID,
        revocation_set=_empty_revset(), now_utc=NOW, candidate_use_mode="TEST_ONLY", application=APP)
    assert handle is None and reasons == ("RA-REGISTRY-TAMPERED",)

    # not-frozen registry -> RA-REGISTRY-NOT-FROZEN.
    unfrozen = f2._registry(freeze=False, include_oci=True)
    resolver2, _r2, _m2 = _synthetic_resolver(f2._registry(freeze=True))
    handle, reasons = ract.activate_registry(
        registry=unfrozen, resolver=resolver2, activation_authority=arh.ActivationAuthority(),
        authority_root_id=ROOT_ID, trust_domain_id=TRUST_DOMAIN, registry_id=REG_ID,
        revocation_set=_empty_revset(), now_utc=NOW, candidate_use_mode="TEST_ONLY", application=APP)
    assert handle is None and reasons == ("RA-REGISTRY-NOT-FROZEN",)


# ============================================================================ §16 candidate readiness API
def _valid_ai_inputs(registry):
    """Build a valid F-2 bundle + fresh ai_evidence that the delegated comparator accepts (reuses the F-2
    test's positive-path builders)."""
    bundle, _ia, _fa = f2._make_bundle(registry)
    ev = f2._ai_evidence()
    return bundle, ev


def test_candidate_readiness_handle_api():
    reg = _registry()
    bundle, ev = _valid_ai_inputs(reg)
    handle, reasons, reg2, authority, root, man = _activate(registry=reg)
    assert handle is not None, reasons

    # raw ProducerRegistry passed as the handle -> AI3-RAW-REGISTRY-NOT-SUFFICIENT.
    v = ai.validate_active_import_with_activated_handle(
        ev, anchor_bundle=bundle, activated_handle=reg, activation_authority=authority, now_utc=f2.NOW,
        phase2_prefix=f2.P2_PREFIX, phase2_suffix=f2.P2_SUFFIX, required_phase2_count=2)
    assert not v.accepted and v.reason_codes == ("AI3-RAW-REGISTRY-NOT-SUFFICIENT",)

    # trusted TEST_ONLY handle used in real_candidate_mode=True -> AI3-SYNTHETIC-HANDLE-IN-REAL-MODE.
    v = ai.validate_active_import_with_activated_handle(
        ev, anchor_bundle=bundle, activated_handle=handle, activation_authority=authority, now_utc=f2.NOW,
        phase2_prefix=f2.P2_PREFIX, phase2_suffix=f2.P2_SUFFIX, required_phase2_count=2,
        real_candidate_mode=True)
    assert not v.accepted and v.reason_codes == ("AI3-SYNTHETIC-HANDLE-IN-REAL-MODE",)

    # valid TEST_ONLY handle in test mode -> delegates and ACCEPTS.
    v = ai.validate_active_import_with_activated_handle(
        ev, anchor_bundle=bundle, activated_handle=handle, activation_authority=authority, now_utc=f2.NOW,
        phase2_prefix=f2.P2_PREFIX, phase2_suffix=f2.P2_SUFFIX, required_phase2_count=2)
    assert v.accepted and v.inert, v.reason_codes


# ============================================================================ §18 caller-minted-authority attack
def test_caller_minted_authority_attack_rejected():
    """The DECISIVE test. A caller builds a fresh registry, invents an approver + authority root + a matching
    manifest, recomputes ALL checksums, freezes, sets real_evidence_authority=True, and constructs a handle
    directly. Assert BOTH rejections:
      (a) the activated-handle path REJECTS a directly-built handle (AH-SEAL-INVALID — no ActivationAuthority
          key), and
      (b) with the ProductionAuthorityResolver, real activation is IMPOSSIBLE (RA-RESOLVER-NOT-CONFIGURED)."""
    # Caller mints a fresh registry with real_evidence_authority=True producers.
    attacker_reg = f2._registry(real=True, freeze=True, include_oci=True)
    attacker_authority_str = "CALLER-INVENTED-AUTHORITY"
    # Caller invents a well-formed-looking authority root + manifest, recomputing every digest.
    caller_root = _authority_root(classification="GOVERNED_EXTERNAL_AUTHORITY",
                                  approvers=("caller-approver-1", "caller-approver-2"))
    rd = attacker_reg.canonical_digest()
    caller_manifest = _manifest(
        attacker_reg, root_digest=caller_root.authority_root_digest,
        approvals=(_approval("caller-approver-1", registry_digest=rd, authority_root_id=caller_root.authority_root_id),
                   _approval("caller-approver-2", registry_digest=rd, authority_root_id=caller_root.authority_root_id)))

    # (a) caller constructs a handle DIRECTLY (frozen dataclass is publicly constructible) with a made-up seal.
    forged_handle = arh.ActivatedRegistryHandle(
        contract_version="1", application=APP, authority_root_id=caller_root.authority_root_id,
        authority_root_digest=caller_root.authority_root_digest, manifest_id=REG_ID + "@" + REG_ID,
        manifest_digest=caller_manifest.manifest_digest, registry_id=REG_ID,
        registry_version=attacker_reg.registry_policy_version, registry_digest=rd,
        producer_set_digest=attacker_reg.producer_set_digest(), activation_utc=NOW, validity_end_utc=FUTURE,
        activation_reason="forged", resolver_identity="forged", resolver_version="1",
        resolver_evidence_reference="forged", candidate_use_policy="REAL_CANDIDATE",
        handle_checksum="", lifecycle_state="ACTIVATED", seal="f" * 64, _bound_registry=attacker_reg)
    # even if the caller recomputes the checksum (integrity), they cannot compute the seal (authenticity).
    forged_handle = dataclasses.replace(forged_handle, handle_checksum=forged_handle.recompute_checksum())
    victim_authority = arh.ActivationAuthority()   # the real authority the caller does NOT hold
    ok, reasons = arh.is_trusted(forged_handle, activation_authority=victim_authority, now_utc=NOW)
    assert not ok and "AH-SEAL-INVALID" in reasons, reasons

    # and the §16 candidate-readiness API rejects it too.
    bundle, ev = _valid_ai_inputs(attacker_reg)
    v = ai.validate_active_import_with_activated_handle(
        ev, anchor_bundle=bundle, activated_handle=forged_handle, activation_authority=victim_authority,
        now_utc=f2.NOW, phase2_prefix=f2.P2_PREFIX, phase2_suffix=f2.P2_SUFFIX, required_phase2_count=2,
        real_candidate_mode=True)
    assert not v.accepted and "AI3-HANDLE-AH-SEAL-INVALID" in v.reason_codes, v.reason_codes

    # (b) with the ProductionAuthorityResolver, REAL activation is structurally impossible.
    prod = rs.ProductionAuthorityResolver()
    handle, ra_reasons = ract.activate_registry(
        registry=attacker_reg, resolver=prod, activation_authority=victim_authority,
        authority_root_id=caller_root.authority_root_id, trust_domain_id=TRUST_DOMAIN, registry_id=REG_ID,
        revocation_set=_empty_revset(), now_utc=NOW, candidate_use_mode="REAL_CANDIDATE", application=APP)
    assert handle is None and ra_reasons == ("RA-RESOLVER-NOT-CONFIGURED",), ra_reasons

    # structural: activation takes TYPED params, not evidence — an authority-root embedded in an anchor bundle
    # or a manifest embedded in active-import evidence is never consulted. activate_registry has no evidence
    # or bundle parameter at all.
    import inspect
    params = set(inspect.signature(ract.activate_registry).parameters)
    assert "evidence" not in params and "anchor_bundle" not in params and "ai_evidence" not in params


# ============================================================================ §19 positive flow + source mutations
def test_positive_synthetic_flow_and_source_mutations():
    reg = _registry()
    bundle, ev = _valid_ai_inputs(reg)
    handle, reasons, reg2, authority, root, man = _activate(registry=reg)
    assert handle is not None, reasons
    # end-to-end accept in TEST_ONLY.
    v = ai.validate_active_import_with_activated_handle(
        ev, anchor_bundle=bundle, activated_handle=handle, activation_authority=authority, now_utc=f2.NOW,
        phase2_prefix=f2.P2_PREFIX, phase2_suffix=f2.P2_SUFFIX, required_phase2_count=2)
    assert v.accepted and v.inert, v.reason_codes

    # Mutate each INDEPENDENT source SEPARATELY and assert activation rejection each time.
    # 1) root digest tamper -> resolver returns a root that fails AR-DIGEST-TAMPER.
    bad_root = dataclasses.replace(root, audit_reference="TAMPER")   # digest no longer recomputes
    resolver_badroot = rs.SyntheticAuthorityResolver(authority_root=bad_root, manifest=man)
    h, r = ract.activate_registry(
        registry=reg, resolver=resolver_badroot, activation_authority=arh.ActivationAuthority(),
        authority_root_id=ROOT_ID, trust_domain_id=TRUST_DOMAIN, registry_id=REG_ID,
        revocation_set=_empty_revset(), now_utc=NOW, candidate_use_mode="TEST_ONLY", application=APP)
    assert h is None and any("AR-DIGEST-TAMPER" in x for x in r), r

    # 2) manifest digest tamper.
    bad_man = dataclasses.replace(man, provenance_reference="TAMPER")   # manifest digest no longer recomputes
    resolver_badman = rs.SyntheticAuthorityResolver(authority_root=root, manifest=bad_man)
    h, r = ract.activate_registry(
        registry=reg, resolver=resolver_badman, activation_authority=arh.ActivationAuthority(),
        authority_root_id=ROOT_ID, trust_domain_id=TRUST_DOMAIN, registry_id=REG_ID,
        revocation_set=_empty_revset(), now_utc=NOW, candidate_use_mode="TEST_ONLY", application=APP)
    assert h is None and any("MF-DIGEST-TAMPER" in x for x in r), r

    # 3) an approval mutated (breaks the quorum).
    rd = reg.canonical_digest()
    bad_appr_man = _manifest(
        reg, root_digest=root.authority_root_digest,
        approvals=(_approval(APPROVER_A, registry_digest="wrong"), _approval(APPROVER_B, registry_digest=rd)))
    resolver_badappr = rs.SyntheticAuthorityResolver(authority_root=root, manifest=bad_appr_man)
    h, r = ract.activate_registry(
        registry=reg, resolver=resolver_badappr, activation_authority=arh.ActivationAuthority(),
        authority_root_id=ROOT_ID, trust_domain_id=TRUST_DOMAIN, registry_id=REG_ID,
        revocation_set=_empty_revset(), now_utc=NOW, candidate_use_mode="TEST_ONLY", application=APP)
    assert h is None and any("APPROVAL-FAILED" in x or "AP-" in x for x in r), r

    # 4) a revocation of the authority root.
    revset = rv.new_revocation_set((
        rv.new_revocation(revocation_type="AUTHORITY_ROOT", target_id=ROOT_ID, effective_utc=PAST,
                          reason_code="COMPROMISE", source="GOV", audit_reference="A"),))
    resolver_ok, _r, _m = _synthetic_resolver(reg, root=root, manifest=man)
    h, r = ract.activate_registry(
        registry=reg, resolver=resolver_ok, activation_authority=arh.ActivationAuthority(),
        authority_root_id=ROOT_ID, trust_domain_id=TRUST_DOMAIN, registry_id=REG_ID,
        revocation_set=revset, now_utc=NOW, candidate_use_mode="TEST_ONLY", application=APP)
    assert h is None and r == ("RA-REVOKED",), r

    # 5) the registry mutated AFTER freeze (integrity divergence).
    reg_bad = f2._registry(freeze=True, include_oci=True)
    snap = dict(reg_bad._frozen_by_id)
    v0 = next(iter(snap))
    snap[v0] = dataclasses.replace(snap[v0], audit_reference="POST-FREEZE-TAMPER")
    import types as _t
    reg_bad._frozen_by_id = _t.MappingProxyType(snap)
    resolver_bad, root_b, man_b = _synthetic_resolver(reg_bad)   # manifest binds the ORIGINAL digest
    # but integrity_ok() is now False -> RA-REGISTRY-TAMPERED before any manifest compare.
    h, r = ract.activate_registry(
        registry=reg_bad, resolver=resolver_bad, activation_authority=arh.ActivationAuthority(),
        authority_root_id=ROOT_ID, trust_domain_id=TRUST_DOMAIN, registry_id=REG_ID,
        revocation_set=_empty_revset(), now_utc=NOW, candidate_use_mode="TEST_ONLY", application=APP)
    assert h is None and r == ("RA-REGISTRY-TAMPERED",), r


# ============================================================================ regression smoke
def test_regression_smoke_register_resolve_unchanged():
    reg = pr.ProducerRegistry(registry_policy_version=f2.REG_POLICY)
    assert reg.register(
        f2._reg(f2.BUILD_PID, "GOVERNED_BUILD_RESULT", ["BUILD_RESULT"], ["STAGE_B_BUILD_RESULT"]),
        approver_authority=f2.APPROVER) == ()
    reg.freeze()
    got, r = reg.resolve(producer_id=f2.BUILD_PID, evidence_type="BUILD_RESULT",
                         gate_id="STAGE_B_BUILD_RESULT", application="hermes", now_utc=f2.NOW)
    assert got is not None and r == ()
    # unknown producer still rejected identically.
    got, r = reg.resolve(producer_id="nope", evidence_type="BUILD_RESULT",
                         gate_id="STAGE_B_BUILD_RESULT", application="hermes", now_utc=f2.NOW)
    assert got is None and r == ("PR-UNKNOWN-PRODUCER",)
    # registering into a frozen registry still rejected.
    assert "PR-REGISTRY-FROZEN" in reg.register(
        f2._reg("late", "GOVERNED_BUILD_RESULT", ["BUILD_RESULT"], ["STAGE_B_BUILD_RESULT"]),
        approver_authority=f2.APPROVER)


# ============================================================================ §15 revocation fail-closed on tamper (HELM hardening)
def test_revocation_set_integrity_enforced_at_activation():
    """A governance revocation must not be neutralisable by tampering an entry: activation requires the
    revocation set be PRESENT and integrity-intact, and a still-effective revocation must still fire."""
    good = rv.new_revocation(revocation_type="AUTHORITY_ROOT", target_id=ROOT_ID, effective_utc=PAST,
                             reason_code="COMPROMISE", source="gov", audit_reference="A")
    # tampered entry: bytes altered so its own digest is stale, but the set_digest is copied from the good set.
    tampered = dataclasses.replace(good, reason_code="ALTERED")
    tampered_set = rv.RevocationSet(entries=(tampered,), set_digest=rv.new_revocation_set((good,)).set_digest)
    assert tampered_set.integrity_ok() is False
    h, r = _activate(revocation_set=tampered_set)[:2]
    assert h is None and "RA-REVOCATION-SET-TAMPERED" in r

    # a missing revocation set is not verifiable -> fail closed (call activation directly with None).
    registry = _registry()
    resolver, root, manifest = _synthetic_resolver(registry)
    h2, r2 = ract.activate_registry(
        registry=registry, resolver=resolver, activation_authority=arh.ActivationAuthority(),
        authority_root_id=ROOT_ID, trust_domain_id=TRUST_DOMAIN, registry_id=REG_ID,
        revocation_set=None, now_utc=NOW, candidate_use_mode="TEST_ONLY", application=APP)
    assert h2 is None and "RA-REVOCATION-SET-MISSING" in r2

    # a VALID revocation still revokes (fix does not over-block).
    h3, r3 = _activate(revocation_set=rv.new_revocation_set((good,)))[:2]
    assert h3 is None and "RA-REVOKED" in r3

    # a clean empty set still activates (positive control).
    h4, r4 = _activate(revocation_set=rv.new_revocation_set(()))[:2]
    assert h4 is not None and r4 == ()
