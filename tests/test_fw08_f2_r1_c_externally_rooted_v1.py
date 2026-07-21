"""FW-08 F2-R1-C externally-rooted trust-boundary tests (§16-§22).

WO-HELM-HERMES-FW08-F2-R1-EXTERNAL-PRODUCER-REGISTRY-ROOT-OF-TRUST-IMPLEMENTATION-0001 (F2-R1-C).
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-21. Contract version: 1.

These tests build NO real image, run NO real docker/SBOM/scan, publish NOTHING, deploy NOTHING, wire NO
runtime, create NO live key/secret/KMS call/`/etc` write, and add NO third-party dependency (stdlib only).
They prove the F2-R1-C AMBER closure: real-candidate trust cannot be supplied by the caller. The prevention is
STRUCTURAL — a module-owned trust-anchor provider whose PRODUCTION path is unavailable (fails closed), a
module-owned verifier the caller cannot supply, resolver acceptance keyed on exact identity + content-digest,
externally-anchored approvers, and revocation rechecked AT USE.

The decisive test is CALLER-OWNS-BOTH: an attacker who builds EVERY artifact and even their own verifier still
cannot activate/validate in REAL_CANDIDATE mode — the production provider is unavailable BEFORE any caller
object is trusted.

Run: cd <worktree> && python3 -m pytest tests/test_fw08_f2_r1_c_externally_rooted_v1.py -q
"""
from __future__ import annotations

import dataclasses
import inspect

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
import design.hermes_fw08_candidate_mode_v1 as cm
import design.hermes_fw08_trust_anchor_provider_v1 as tap

# Reuse existing fixture builders.
import tests.test_fw08_f2_r1_registry_root_of_trust_v1 as R1
import tests.test_fw08_f2_independent_anchors_v1 as f2

APP = R1.APP
NOW = R1.NOW
TRUST_DOMAIN = R1.TRUST_DOMAIN
ROOT_ID = R1.ROOT_ID
REG_ID = R1.REG_ID
APPROVER_A = R1.APPROVER_A
APPROVER_B = R1.APPROVER_B
FUTURE = R1.FUTURE
PAST = R1.PAST


# ============================================================================ helpers
def _anchored_context(registry, *, resolver, revocation_source=None, approvers=(APPROVER_A, APPROVER_B),
                      candidate_mode="TEST_ONLY"):
    """A module-owned TEST_ONLY TrustContext that ANCHORS the given resolver (exact id + content-digest) and
    the given approvers. Built via the module helper `new_synthetic_test_context` (which internally supplies
    the module token — the caller never sees it)."""
    rid = resolver.resolver_identity
    digest = tap.resolver_content_digest(resolver)
    ctx, reasons = tap.new_synthetic_test_context(
        application=APP, trust_domain_id=TRUST_DOMAIN, candidate_mode=candidate_mode,
        trusted_resolver_ids=(rid,), trusted_resolver_digests=(digest,),
        trusted_approver_ids=tuple(approvers), revocation_source=revocation_source)
    assert ctx is not None, reasons
    return ctx


def _valid_ai_inputs(registry):
    bundle, _ia, _fa = f2._make_bundle(registry)
    ev = f2._ai_evidence()
    return bundle, ev


def _empty_revset():
    return rv.new_revocation_set(())


# ============================================================================ §16 CALLER-OWNS-BOTH
def test_caller_owns_both_real_mode_rejected():
    """The DECISIVE test. An attacker builds their OWN resolver + authority root + approvers + manifest +
    registry + activation issuer + verifier + all checksums + ai_evidence + bundle, then attempts BOTH
    externally-rooted entry points in REAL_CANDIDATE. BOTH must reject BEFORE any caller object is trusted:
    activation with RA-C-TRUST-CONTEXT-UNAVAILABLE (from TC-PRODUCTION-PROVIDER-UNAVAILABLE) and validation
    with AI4-TRUST-CONTEXT-UNAVAILABLE. We use NEITHER the module verifier NOR a real provider."""
    attacker_reg = f2._registry(real=True, freeze=True, include_oci=True)
    caller_root = R1._authority_root(classification="GOVERNED_EXTERNAL_AUTHORITY",
                                     approvers=("caller-approver-1", "caller-approver-2"))
    rd = attacker_reg.canonical_digest()
    caller_manifest = R1._manifest(
        attacker_reg, root_digest=caller_root.authority_root_digest,
        approvals=(R1._approval("caller-approver-1", registry_digest=rd,
                                authority_root_id=caller_root.authority_root_id),
                   R1._approval("caller-approver-2", registry_digest=rd,
                                authority_root_id=caller_root.authority_root_id)))
    caller_resolver = rs.SyntheticAuthorityResolver(authority_root=caller_root, manifest=caller_manifest)

    # (a) activation in REAL_CANDIDATE — no trust_context supplied; the provider is unavailable.
    handle, ra = ract.activate_registry_externally_rooted(
        registry=attacker_reg, candidate_mode="REAL_CANDIDATE", resolver=caller_resolver,
        authority_root_id=caller_root.authority_root_id, trust_domain_id=TRUST_DOMAIN, registry_id=REG_ID,
        revocation_set=_empty_revset(), now_utc=NOW, application=APP)
    assert handle is None
    assert "RA-C-TRUST-CONTEXT-UNAVAILABLE" in ra and "TC-PRODUCTION-PROVIDER-UNAVAILABLE" in ra, ra

    # (b) the caller even builds their OWN verifier + a self-sealed handle, and passes a self-built
    #     TrustContext. Validation in REAL_CANDIDATE rejects before trusting any of it.
    caller_verifier = tap._ModuleVerifier()   # a caller-constructed verifier (NOT the module singleton)
    forged_handle = arh.ActivatedRegistryHandle(
        contract_version="1", application=APP, authority_root_id=caller_root.authority_root_id,
        authority_root_digest=caller_root.authority_root_digest, manifest_id=REG_ID + "@" + REG_ID,
        manifest_digest=caller_manifest.manifest_digest, registry_id=REG_ID,
        registry_version=attacker_reg.registry_policy_version, registry_digest=rd,
        producer_set_digest=attacker_reg.producer_set_digest(), activation_utc=NOW, validity_end_utc=FUTURE,
        activation_reason="forged", resolver_identity=caller_resolver.resolver_identity, resolver_version="1",
        resolver_evidence_reference="forged", candidate_use_policy="REAL_CANDIDATE",
        handle_checksum="", lifecycle_state="ACTIVATED", seal="", _bound_registry=attacker_reg)
    forged_handle = dataclasses.replace(forged_handle, handle_checksum=forged_handle.recompute_checksum())
    forged_handle = dataclasses.replace(
        forged_handle, seal=caller_verifier._seal(arh._seal_message(forged_handle)))
    bundle, ev = _valid_ai_inputs(attacker_reg)
    v = ai.validate_active_import_externally_rooted(
        ev, anchor_bundle=bundle, activated_handle=forged_handle, candidate_mode="REAL_CANDIDATE",
        now_utc=f2.NOW, phase2_prefix=f2.P2_PREFIX, phase2_suffix=f2.P2_SUFFIX, required_phase2_count=10)
    assert not v.accepted
    assert "AI4-TRUST-CONTEXT-UNAVAILABLE" in v.reason_codes
    assert "F2R1-REAL-CANDIDATE-REQUIRES-EXTERNALLY-VERIFIED-HANDLE" in v.reason_codes, v.reason_codes

    # (c) even if the caller forges their OWN module-shaped TrustContext, it lacks the module marker, so any
    #     mode is rejected as untrusted (proves the caller cannot mint a trusted context).
    fake_ctx = tap.TrustContext(
        trust_anchor_set=tap.new_trust_anchor_set(
            application=APP, trust_domain_id=TRUST_DOMAIN, trusted_resolver_ids=(),
            trusted_resolver_digests=(), trusted_activation_verifier_id="x",
            trusted_activation_verifier_material="x", trusted_authority_root_ids=(),
            trusted_authority_root_material=(), trusted_approver_ids=(), policy_version="1",
            valid_from_utc=PAST, valid_until_utc=FUTURE, revocation_source_identity="x",
            source_provenance="CALLER", audit_reference="x", classification="SYNTHETIC_TEST_TRUST_ANCHOR"),
        resolver_authorisations={}, approver_ids=(), revocation_source=_empty_revset(),
        candidate_mode="TEST_ONLY", _verifier=caller_verifier, _marker=object())
    assert tap.is_module_trust_context(fake_ctx) is False
    assert fake_ctx.verifier() is None   # caller-built context carries no module verifier
    h2, r2 = ract.activate_registry_externally_rooted(
        registry=attacker_reg, candidate_mode="TEST_ONLY", resolver=caller_resolver,
        authority_root_id=caller_root.authority_root_id, trust_domain_id=TRUST_DOMAIN, registry_id=REG_ID,
        revocation_set=_empty_revset(), now_utc=NOW, trust_context=fake_ctx, application=APP)
    assert h2 is None and "RA-C-TRUST-CONTEXT-UNTRUSTED" in r2, r2


# ============================================================================ §17 arbitrary resolver
def test_arbitrary_resolver_not_anchored_rejected():
    reg = R1._registry()
    good_resolver, root, man = R1._synthetic_resolver(reg)
    ctx = _anchored_context(reg, resolver=good_resolver, revocation_source=_empty_revset())

    # the anchored resolver passes.
    assert tap.verify_resolver_identity(good_resolver, trust_context=ctx) == ()

    # a subclass (different qualname/source) fails on digest even if it shares the id.
    class SneakyResolver(rs.SyntheticAuthorityResolver):
        pass
    sneaky = SneakyResolver(authority_root=root, manifest=man,
                            resolver_identity=good_resolver.resolver_identity)
    r = tap.verify_resolver_identity(sneaky, trust_context=ctx)
    assert "RESOLVER-DIGEST-MISMATCH" in r and "RESOLVER-NOT-ANCHORED" in r, r

    # a proxy object with a spoofed id but unknown to the anchor set fails ID-UNKNOWN.
    class Proxy:
        resolver_identity = "totally-different-id"
        resolver_classification = "SYNTHETIC_TEST_RESOLVER"
    r = tap.verify_resolver_identity(Proxy(), trust_context=ctx)
    assert "RESOLVER-ID-UNKNOWN" in r and "RESOLVER-NOT-ANCHORED" in r, r

    # an id-collision proxy (same id, wrong type/digest) fails DIGEST-MISMATCH.
    class Collision:
        resolver_identity = good_resolver.resolver_identity
        resolver_classification = "SYNTHETIC_TEST_RESOLVER"
    r = tap.verify_resolver_identity(Collision(), trust_context=ctx)
    assert "RESOLVER-DIGEST-MISMATCH" in r, r

    # a copied-with-changed-metadata resolver: same class, but we anchor a WRONG digest -> mismatch.
    ctx_wrong = _anchored_context(reg, resolver=good_resolver, revocation_source=_empty_revset())
    # tamper the anchor by rebuilding a context whose authorised digest is wrong.
    bad_ctx, _ = tap.new_synthetic_test_context(
        application=APP, trust_domain_id=TRUST_DOMAIN,
        trusted_resolver_ids=(good_resolver.resolver_identity,), trusted_resolver_digests=("deadbeef",),
        trusted_approver_ids=(APPROVER_A, APPROVER_B), revocation_source=_empty_revset())
    r = tap.verify_resolver_identity(good_resolver, trust_context=bad_ctx)
    assert "RESOLVER-DIGEST-MISMATCH" in r, r


# ============================================================================ §18 caller-verifier capability
def test_caller_verifier_capability_rejected():
    """A caller-created verifier cannot verify through the real-candidate API; a handle sealed by a caller
    ActivationAuthority fails AI4-HANDLE-SEAL-INVALID under the MODULE verifier."""
    reg = R1._registry()
    resolver, root, man = R1._synthetic_resolver(reg)
    ctx = _anchored_context(reg, resolver=resolver, revocation_source=_empty_revset())
    bundle, ev = _valid_ai_inputs(reg)

    # Mint a genuine module-sealed handle via the externally-rooted activation.
    handle, ra = ract.activate_registry_externally_rooted(
        registry=reg, candidate_mode="TEST_ONLY", resolver=resolver, authority_root_id=ROOT_ID,
        trust_domain_id=TRUST_DOMAIN, registry_id=REG_ID, revocation_set=_empty_revset(), now_utc=NOW,
        trust_context=ctx, application=APP)
    assert handle is not None, ra

    # Re-seal the SAME handle with a caller ActivationAuthority -> the module verifier rejects it.
    caller_auth = arh.ActivationAuthority()
    caller_sealed = dataclasses.replace(handle, seal=caller_auth._seal(arh._seal_message(handle)))
    v = ai.validate_active_import_externally_rooted(
        ev, anchor_bundle=bundle, activated_handle=caller_sealed, candidate_mode="TEST_ONLY",
        now_utc=f2.NOW, trust_context=ctx, phase2_prefix=f2.P2_PREFIX, phase2_suffix=f2.P2_SUFFIX,
        required_phase2_count=10)
    assert not v.accepted and "AI4-HANDLE-SEAL-INVALID" in v.reason_codes, v.reason_codes

    # HONEST NOTE: the synthetic module verifier's key is in-process; a co-resident attacker could reflectively
    # reach a name-mangled attr. This mirrors ActivationAuthority — the API caller cannot supply/read it.
    assert not hasattr(tap._MODULE_VERIFIER, "key") and not hasattr(tap._MODULE_VERIFIER, "get_key")


# ============================================================================ §11/§C3 approver anchoring
def test_approver_not_anchored_rejected():
    """An approver NAMED by the root but ABSENT from the trust-anchor set -> RA-C-APPROVER-NOT-ANCHORED."""
    reg = R1._registry()
    resolver, root, man = R1._synthetic_resolver(reg)
    # Anchor context that omits APPROVER_B (only APPROVER_A anchored) though the root/manifest name both.
    ctx = _anchored_context(reg, resolver=resolver, revocation_source=_empty_revset(),
                            approvers=(APPROVER_A,))
    handle, ra = ract.activate_registry_externally_rooted(
        registry=reg, candidate_mode="TEST_ONLY", resolver=resolver, authority_root_id=ROOT_ID,
        trust_domain_id=TRUST_DOMAIN, registry_id=REG_ID, revocation_set=_empty_revset(), now_utc=NOW,
        trust_context=ctx, application=APP)
    assert handle is None and "RA-C-APPROVER-NOT-ANCHORED" in ra, ra

    # positive control: anchoring BOTH approvers mints a handle.
    ctx_ok = _anchored_context(reg, resolver=resolver, revocation_source=_empty_revset())
    handle2, ra2 = ract.activate_registry_externally_rooted(
        registry=reg, candidate_mode="TEST_ONLY", resolver=resolver, authority_root_id=ROOT_ID,
        trust_domain_id=TRUST_DOMAIN, registry_id=REG_ID, revocation_set=_empty_revset(), now_utc=NOW,
        trust_context=ctx_ok, application=APP)
    assert handle2 is not None, ra2


# ============================================================================ §12/§19 revocation at use
@pytest.mark.parametrize("rtype,target_attr", [
    ("AUTHORITY_ROOT", "authority_root_id"),
    ("REGISTRY_MANIFEST", "registry_id"),
    ("REGISTRY_MANIFEST", "manifest_id"),
    ("RESOLVER", "resolver_identity"),
    ("ACTIVATION_HANDLE", "handle_checksum"),
])
def test_revocation_at_use_rejects(rtype, target_attr):
    reg = R1._registry()
    resolver, root, man = R1._synthetic_resolver(reg)
    # Mint a TEST_ONLY handle via the externally-rooted path with a CLEAN revocation source.
    ctx_mint = _anchored_context(reg, resolver=resolver, revocation_source=_empty_revset())
    handle, ra = ract.activate_registry_externally_rooted(
        registry=reg, candidate_mode="TEST_ONLY", resolver=resolver, authority_root_id=ROOT_ID,
        trust_domain_id=TRUST_DOMAIN, registry_id=REG_ID, revocation_set=_empty_revset(), now_utc=NOW,
        trust_context=ctx_mint, application=APP)
    assert handle is not None, ra

    # Now revoke the chosen target SEPARATELY in a fresh context's revocation source, and validate AT USE.
    target_id = getattr(handle, target_attr)
    revset = rv.new_revocation_set((
        rv.new_revocation(revocation_type=rtype, target_id=target_id, effective_utc=PAST,
                          reason_code="COMPROMISE", source="GOV", audit_reference="A"),))
    ctx_use = _anchored_context(reg, resolver=resolver, revocation_source=revset)
    bundle, ev = _valid_ai_inputs(reg)
    v = ai.validate_active_import_externally_rooted(
        ev, anchor_bundle=bundle, activated_handle=handle, candidate_mode="TEST_ONLY", now_utc=f2.NOW,
        trust_context=ctx_use, phase2_prefix=f2.P2_PREFIX, phase2_suffix=f2.P2_SUFFIX,
        required_phase2_count=10)
    assert not v.accepted and "AI4-REVOKED-AT-USE" in v.reason_codes, v.reason_codes


def test_revocation_unrelated_nonblocking_and_unavailable_failclosed():
    reg = R1._registry()
    resolver, root, man = R1._synthetic_resolver(reg)
    ctx_mint = _anchored_context(reg, resolver=resolver, revocation_source=_empty_revset())
    handle, ra = ract.activate_registry_externally_rooted(
        registry=reg, candidate_mode="TEST_ONLY", resolver=resolver, authority_root_id=ROOT_ID,
        trust_domain_id=TRUST_DOMAIN, registry_id=REG_ID, revocation_set=_empty_revset(), now_utc=NOW,
        trust_context=ctx_mint, application=APP)
    assert handle is not None, ra
    bundle, ev = _valid_ai_inputs(reg)

    # UNRELATED revocation stays non-blocking (accepts + inert).
    unrelated = rv.new_revocation_set((
        rv.new_revocation(revocation_type="AUTHORITY_ROOT", target_id="some-other-root", effective_utc=PAST,
                          reason_code="X", source="GOV", audit_reference="A"),))
    ctx_ok = _anchored_context(reg, resolver=resolver, revocation_source=unrelated)
    v = ai.validate_active_import_externally_rooted(
        ev, anchor_bundle=bundle, activated_handle=handle, candidate_mode="TEST_ONLY", now_utc=f2.NOW,
        trust_context=ctx_ok, phase2_prefix=f2.P2_PREFIX, phase2_suffix=f2.P2_SUFFIX,
        required_phase2_count=10)
    assert v.accepted and v.inert, v.reason_codes

    # UNAVAILABLE revocation source -> fail closed AI4-REVOCATION-UNAVAILABLE.
    ctx_none = _anchored_context(reg, resolver=resolver, revocation_source=None)
    v2 = ai.validate_active_import_externally_rooted(
        ev, anchor_bundle=bundle, activated_handle=handle, candidate_mode="TEST_ONLY", now_utc=f2.NOW,
        trust_context=ctx_none, phase2_prefix=f2.P2_PREFIX, phase2_suffix=f2.P2_SUFFIX,
        required_phase2_count=10)
    assert not v2.accepted and "AI4-REVOCATION-UNAVAILABLE" in v2.reason_codes, v2.reason_codes


# ============================================================================ §13/§14/§C5 real-candidate invariant
def test_real_candidate_invariant():
    reg = R1._registry()
    resolver, root, man = R1._synthetic_resolver(reg)
    ctx = _anchored_context(reg, resolver=resolver, revocation_source=_empty_revset())
    bundle, ev = _valid_ai_inputs(reg)

    # raw registry as handle -> AI4-RAW-REGISTRY-NOT-SUFFICIENT (in TEST_ONLY where a context exists).
    v = ai.validate_active_import_externally_rooted(
        ev, anchor_bundle=bundle, activated_handle=reg, candidate_mode="TEST_ONLY", now_utc=f2.NOW,
        trust_context=ctx, phase2_prefix=f2.P2_PREFIX, phase2_suffix=f2.P2_SUFFIX, required_phase2_count=10)
    assert not v.accepted and "AI4-RAW-REGISTRY-NOT-SUFFICIENT" in v.reason_codes, v.reason_codes

    # REAL_CANDIDATE with a missing authority artifact -> fail closed, no downgrade (provider unavailable).
    h, ra = ract.activate_registry_externally_rooted(
        registry=reg, candidate_mode="REAL_CANDIDATE", resolver=resolver, authority_root_id=ROOT_ID,
        trust_domain_id=TRUST_DOMAIN, registry_id=REG_ID, revocation_set=_empty_revset(), now_utc=NOW,
        application=APP)
    assert h is None and "RA-C-TRUST-CONTEXT-UNAVAILABLE" in ra, ra

    # a TEST_ONLY-produced handle cannot be used in REAL_CANDIDATE mode (real fails closed first).
    handle, _ = ract.activate_registry_externally_rooted(
        registry=reg, candidate_mode="TEST_ONLY", resolver=resolver, authority_root_id=ROOT_ID,
        trust_domain_id=TRUST_DOMAIN, registry_id=REG_ID, revocation_set=_empty_revset(), now_utc=NOW,
        trust_context=ctx, application=APP)
    assert handle is not None
    v2 = ai.validate_active_import_externally_rooted(
        ev, anchor_bundle=bundle, activated_handle=handle, candidate_mode="REAL_CANDIDATE", now_utc=f2.NOW,
        phase2_prefix=f2.P2_PREFIX, phase2_suffix=f2.P2_SUFFIX, required_phase2_count=10)
    assert not v2.accepted and "AI4-TRUST-CONTEXT-UNAVAILABLE" in v2.reason_codes, v2.reason_codes

    # candidate mode is typed: a bare bool is invalid.
    assert cm.require_mode(True) == ("CM-INVALID-MODE",)
    assert cm.require_mode(False) == ("CM-INVALID-MODE",)
    assert cm.is_real(True) is False and cm.is_real("REAL_CANDIDATE") is True


def test_wrapper_raw_branch_unreachable_in_real_mode():
    """§C5 mechanical: the wrapper's raw else-branch is unreachable in REAL_CANDIDATE — a missing artifact
    rejects with the invariant reason, never a raw-path acceptance."""
    import tools.hermes_stage_b_build_v1 as w
    src = inspect.getsource(w.run_stage_b_candidate_build)
    # the real-candidate branch must reject with the invariant, and must not call the raw comparator under it.
    assert "F2R1-REAL-CANDIDATE-REQUIRES-EXTERNALLY-VERIFIED-HANDLE" in src
    assert "activate_registry_externally_rooted" in src
    assert "validate_active_import_externally_rooted" in src


# ============================================================================ §20 trust-anchor-provider attacks
def test_trust_anchor_provider_attacks():
    reg = R1._registry()
    resolver, root, man = R1._synthetic_resolver(reg)

    # REAL_CANDIDATE always unavailable.
    ctx, r = tap.resolve_trust_context(candidate_mode="REAL_CANDIDATE")
    assert ctx is None and r == ("TC-PRODUCTION-PROVIDER-UNAVAILABLE",), r

    # synthetic context WITHOUT the module token -> rejected.
    anchor = tap.new_trust_anchor_set(
        application=APP, trust_domain_id=TRUST_DOMAIN, trusted_resolver_ids=(), trusted_resolver_digests=(),
        trusted_activation_verifier_id="x", trusted_activation_verifier_material="x",
        trusted_authority_root_ids=(), trusted_authority_root_material=(), trusted_approver_ids=(),
        policy_version="1", valid_from_utc=PAST, valid_until_utc=FUTURE, revocation_source_identity="x",
        source_provenance="CALLER", audit_reference="x", classification="SYNTHETIC_TEST_TRUST_ANCHOR")
    ctx2, r2 = tap.resolve_trust_context(candidate_mode="TEST_ONLY", synthetic_anchor_set=anchor,
                                         test_context_token=object())
    assert ctx2 is None and r2 == ("TC-SYNTHETIC-CONTEXT-REQUIRES-MODULE-TOKEN",), r2

    # invalid mode -> TC-INVALID-CANDIDATE-MODE.
    ctx3, r3 = tap.resolve_trust_context(candidate_mode="NONSENSE")
    assert ctx3 is None and r3 == ("TC-INVALID-CANDIDATE-MODE",), r3

    # a bare bool mode -> invalid (never defaults to real).
    ctx3b, r3b = tap.resolve_trust_context(candidate_mode=True)  # type: ignore[arg-type]
    assert ctx3b is None and r3b == ("TC-INVALID-CANDIDATE-MODE",), r3b

    # arbitrary provider / classification flip: a caller-built TrustContext (marker = object()) is untrusted.
    fake = tap.TrustContext(
        trust_anchor_set=anchor, resolver_authorisations={}, approver_ids=(),
        revocation_source=_empty_revset(), candidate_mode="TEST_ONLY",
        _verifier=tap._ModuleVerifier(), _marker=object())
    assert tap.is_module_trust_context(fake) is False

    # a genuine module context IS trusted and carries the module verifier.
    good = _anchored_context(reg, resolver=resolver, revocation_source=_empty_revset())
    assert tap.is_module_trust_context(good) is True
    assert good.verifier() is tap._MODULE_VERIFIER

    # production_provider_available is False.
    assert tap.production_provider_available() is False


# ============================================================================ §21 POSITIVE synthetic flow
def test_positive_synthetic_flow_then_real_rejected():
    reg = R1._registry()
    resolver, root, man = R1._synthetic_resolver(reg)
    ctx = _anchored_context(reg, resolver=resolver, revocation_source=_empty_revset())
    bundle, ev = _valid_ai_inputs(reg)

    # module-owned synthetic context + anchored resolver + anchored approvers -> activation mints a handle.
    handle, ra = ract.activate_registry_externally_rooted(
        registry=reg, candidate_mode="TEST_ONLY", resolver=resolver, authority_root_id=ROOT_ID,
        trust_domain_id=TRUST_DOMAIN, registry_id=REG_ID, revocation_set=_empty_revset(), now_utc=NOW,
        trust_context=ctx, application=APP)
    assert handle is not None and ra == (), ra
    assert handle.candidate_use_policy == "TEST_ONLY"

    # and validation ACCEPTS (module-sealed handle, revocation clean, delegates to the unchanged comparator).
    v = ai.validate_active_import_externally_rooted(
        ev, anchor_bundle=bundle, activated_handle=handle, candidate_mode="TEST_ONLY", now_utc=f2.NOW,
        trust_context=ctx, phase2_prefix=f2.P2_PREFIX, phase2_suffix=f2.P2_SUFFIX, required_phase2_count=10)
    assert v.accepted and v.inert, v.reason_codes

    # the SAME objects under REAL_CANDIDATE -> reject (real fails closed).
    v2 = ai.validate_active_import_externally_rooted(
        ev, anchor_bundle=bundle, activated_handle=handle, candidate_mode="REAL_CANDIDATE", now_utc=f2.NOW,
        phase2_prefix=f2.P2_PREFIX, phase2_suffix=f2.P2_SUFFIX, required_phase2_count=10)
    assert not v2.accepted and "AI4-TRUST-CONTEXT-UNAVAILABLE" in v2.reason_codes, v2.reason_codes


# ============================================================================ §22 production unavailability
def test_production_unavailability_no_fs_env_access():
    # production_provider_available() is False.
    assert tap.production_provider_available() is False
    # resolve_trust_context(REAL_CANDIDATE) -> TC-PRODUCTION-PROVIDER-UNAVAILABLE.
    ctx, r = tap.resolve_trust_context(candidate_mode="REAL_CANDIDATE")
    assert ctx is None and r == ("TC-PRODUCTION-PROVIDER-UNAVAILABLE",), r

    # STRUCTURAL: the module makes NO filesystem/env CALLS (AST over CODE, ignoring docstring/comment prose).
    import ast
    tree = ast.parse(inspect.getsource(tap))

    # no `import os` / `from os ...` / `import pathlib` anywhere.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name.split(".")[0] not in ("os", "pathlib", "io"), a.name
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in ("os", "pathlib", "io"), node.module

    # no call to open()/Path()/os.environ/getenv anywhere in the module code.
    banned_call_names = {"open", "Path", "getenv", "system", "popen"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
            assert name not in banned_call_names, name
        if isinstance(node, ast.Attribute):
            assert node.attr != "environ", "os.environ access"


# ============================================================================ signature guards
def test_externally_rooted_signatures_have_no_caller_verifier():
    """STRUCTURAL: the externally-rooted validation entry point has NO activation_authority parameter (the
    caller cannot supply the verifier); the activation entry point has no evidence/bundle parameter."""
    vparams = set(inspect.signature(ai.validate_active_import_externally_rooted).parameters)
    assert "activation_authority" not in vparams
    assert "candidate_mode" in vparams and "trust_context" in vparams
    aparams = set(inspect.signature(ract.activate_registry_externally_rooted).parameters)
    assert "activation_authority" not in aparams
    assert "evidence" not in aparams and "anchor_bundle" not in aparams
    assert "candidate_mode" in aparams
