#!/usr/bin/env python3
"""HERMES FW-08 F2-R1 registry-activation flow v1 (PURE, fixture-backed).

WO-HELM-HERMES-FW08-F2-R1-EXTERNAL-PRODUCER-REGISTRY-ROOT-OF-TRUST-IMPLEMENTATION-0001 (§11).
Owner: HERMES (Helm). Created (UTC): 2026-07-21. Contract version: 1.

WHY THIS EXISTS (F2-R1). `activate_registry` is the SOLE minting path for a trusted `ActivatedRegistryHandle`.
It resolves the external authority root + manifest through a governed resolver, validates every binding, and
only then mints a sealed handle. A caller cannot mint a trusted handle any other way: without a working
resolver (ProductionAuthorityResolver raises) AND without the ActivationAuthority key, no trusted handle is
possible. This is the structural fix for the caller-mintable-registry defect.

PURE: no I/O, no subprocess, no network, no secrets, stdlib only, deterministic (given the injected
ActivationAuthority key). NOT imported by runtime.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import design.hermes_fw08_producer_registry_v1 as pr
import design.hermes_fw08_authority_root_v1 as ar
import design.hermes_fw08_authorised_registry_manifest_v1 as mf
import design.hermes_fw08_authority_resolver_v1 as rs
import design.hermes_fw08_activated_registry_handle_v1 as arh

CONTRACT_VERSION = "1"


def activate_registry(
    *,
    registry: object,
    resolver: object,
    activation_authority: object,
    authority_root_id: str,
    trust_domain_id: str,
    registry_id: str,
    revocation_set: object,
    now_utc: str,
    candidate_use_mode: str,
    application: str = "hermes",
) -> Tuple[Optional[arh.ActivatedRegistryHandle], Tuple[str, ...]]:
    """Mint a trusted ActivatedRegistryHandle, or (None, sorted reasons). Implements the 14-step order exactly;
    NO handle is emitted if any step fails."""
    reasons: List[str] = []
    real_candidate_mode = (candidate_use_mode == "REAL_CANDIDATE")

    # 1. registry must be a frozen governed ProducerRegistry.
    if not isinstance(registry, pr.ProducerRegistry):
        return (None, ("RA-REGISTRY-NOT-GOVERNED",))
    if not registry.frozen:
        return (None, ("RA-REGISTRY-NOT-FROZEN",))

    # 2. integrity of the frozen snapshot (tamper detectable BEFORE use).
    if not registry.integrity_ok():
        return (None, ("RA-REGISTRY-TAMPERED",))

    # 3. compute the registry digest INDEPENDENTLY.
    registry_digest = registry.canonical_digest()
    registry_policy_version = str(getattr(registry, "registry_policy_version", ""))

    # 4. resolver gate (fail closed on ANY RS-*).
    rs_reasons = rs.require_resolver(resolver, real_candidate_mode=real_candidate_mode)
    if rs_reasons:
        return (None, tuple(sorted(set(rs_reasons))))

    # 5. resolve + validate the authority root.
    try:
        root = resolver.resolve_authority_root(
            authority_root_id=authority_root_id, trust_domain_id=trust_domain_id, now_utc=now_utc)
    except rs.ResolverNotConfigured:
        return (None, ("RA-RESOLVER-NOT-CONFIGURED",))
    except rs.UntrustedAuthorityResolved:
        return (None, ("RA-UNTRUSTED-AUTHORITY",))
    root_reasons = ar.validate_authority_root(
        root, now_utc=now_utc, application=application, trust_domain_id=trust_domain_id)
    if root_reasons:
        return (None, tuple(sorted(set("RA-AUTHORITY:" + r for r in root_reasons))))
    classification = getattr(root, "authority_root_classification", "")
    if real_candidate_mode:
        if classification != "GOVERNED_EXTERNAL_AUTHORITY":
            return (None, ("RA-SYNTHETIC-AUTHORITY-IN-REAL-MODE",))
    else:
        # TEST_ONLY: SYNTHETIC_TEST_AUTHORITY is allowed (GOVERNED is too, but cannot arise here since the
        # only working resolver in this WO is the synthetic one).
        if classification not in ("SYNTHETIC_TEST_AUTHORITY", "GOVERNED_EXTERNAL_AUTHORITY"):
            return (None, ("RA-AUTHORITY-CLASSIFICATION-INVALID",))

    # 6. resolve the manifest.
    try:
        manifest = resolver.resolve_manifest(
            authority_root_id=authority_root_id, registry_id=registry_id,
            registry_version=registry_policy_version, registry_digest=registry_digest, now_utc=now_utc)
    except rs.ResolverNotConfigured:
        return (None, ("RA-RESOLVER-NOT-CONFIGURED",))
    except rs.UntrustedAuthorityResolved:
        return (None, ("RA-UNTRUSTED-AUTHORITY",))

    # 7. validate manifest against the registry + root (8. approvals validated inside).
    m_reasons = mf.validate_manifest_against_registry(
        manifest, registry=registry, authority_root=root, now_utc=now_utc, application=application)
    if m_reasons:
        return (None, tuple(sorted(set("RA-MANIFEST:" + r for r in m_reasons))))

    # 9. revocation: the set must be PRESENT and its integrity intact (else a governance revocation could be
    #    silently neutralised by tampering an entry — fail closed), then any effective revocation at now over
    #    root / manifest(registry_id) / producer / approver rejects.
    _integ = getattr(revocation_set, "integrity_ok", None)
    if revocation_set is None or not callable(_integ):
        return (None, ("RA-REVOCATION-SET-MISSING",))
    if not _integ():
        return (None, ("RA-REVOCATION-SET-TAMPERED",))
    if _revoked(revocation_set, registry=registry, root=root, manifest=manifest,
                registry_id=registry_id, now_utc=now_utc):
        return (None, ("RA-REVOKED",))

    # 10. exact registry id / version / policy / digest compare vs manifest.
    if manifest.registry_id != registry_id or manifest.registry_digest != registry_digest \
            or manifest.registry_policy_version != registry_policy_version:
        return (None, ("RA-DIGEST-MISMATCH",))

    # 11. producer-set digest compare.
    if manifest.producer_set_digest != registry.producer_set_digest():
        return (None, ("RA-PRODUCER-SET-MISMATCH",))

    # 12. application + gate scope check.
    if manifest.application != application or getattr(root, "application", "") != application:
        return (None, ("RA-APPLICATION-MISMATCH",))
    if not manifest.allowed_gate_ids:
        return (None, ("RA-GATE-SCOPE",))

    # 13. candidate-use policy. TEST_ONLY handle may only be minted in TEST_ONLY mode; REAL_CANDIDATE requires
    #     governed authority (already gated in step 5).
    if candidate_use_mode not in arh.CANDIDATE_USE_POLICIES:
        return (None, ("RA-CANDIDATE-USE-POLICY",))
    if not real_candidate_mode and classification == "GOVERNED_EXTERNAL_AUTHORITY":
        # A governed authority resolved in TEST_ONLY mode is permitted, but the minted handle is TEST_ONLY.
        pass

    # 14. mint the sealed handle.
    manifest_id = registry_id + "@" + registry_policy_version
    handle0 = arh.ActivatedRegistryHandle(
        contract_version=CONTRACT_VERSION,
        application=application,
        authority_root_id=authority_root_id,
        authority_root_digest=getattr(root, "authority_root_digest", ""),
        manifest_id=manifest_id,
        manifest_digest=getattr(manifest, "manifest_digest", ""),
        registry_id=registry_id,
        registry_version=registry_policy_version,
        registry_digest=registry_digest,
        producer_set_digest=registry.producer_set_digest(),
        activation_utc=now_utc,
        validity_end_utc=manifest.valid_until_utc,
        activation_reason="F2-R1 governed activation via resolver",
        resolver_identity=getattr(resolver, "resolver_identity", ""),
        resolver_version=getattr(resolver, "resolver_version", ""),
        resolver_evidence_reference=getattr(manifest, "provenance_reference", ""),
        candidate_use_policy=candidate_use_mode,
        handle_checksum="",
        lifecycle_state="ACTIVATED",
        seal="",
        _bound_registry=registry,
    )
    import dataclasses
    checksum = handle0.recompute_checksum()
    handle1 = dataclasses.replace(handle0, handle_checksum=checksum)
    seal = activation_authority._seal(arh._seal_message(handle1))
    handle = dataclasses.replace(handle1, seal=seal)
    return (handle, tuple())


# =========================================================== F2-R1-C §7/§8/§11 EXTERNALLY-ROOTED activation
# WO-HELM-HERMES-FW08-F2-R1-EXTERNAL-PRODUCER-REGISTRY-ROOT-OF-TRUST-IMPLEMENTATION-0001 (F2-R1-C, §C1/§C3).
# AUDIT AMBER: `activate_registry` (above) still trusted a CALLER-supplied resolver + a CALLER-supplied
# ActivationAuthority (used to both seal AND verify) and checked approvers only against the root's own named
# set. This variant introduces a MODULE-OWNED trust boundary (hermes_fw08_trust_anchor_provider_v1):
#   * C1 the resolver must be ANCHORED (exact identity + content-digest) in a module-owned TrustContext;
#   * C2 the handle is sealed by the MODULE verifier from the TrustContext, NOT a caller activation_authority;
#   * C3 EACH manifest approver must be anchored in the TrustContext, not merely named by the root;
#   * REAL_CANDIDATE fails closed here because the production trust-anchor provider is unavailable.
# The trust-anchor module is imported LAZILY to avoid any cycle; this function is add-only (the original
# `activate_registry` stays for TEST_ONLY backward-compat but is NO LONGER the real-candidate path).


def activate_registry_externally_rooted(
    *,
    registry: object,
    candidate_mode: str,
    resolver: object,
    authority_root_id: str,
    trust_domain_id: str,
    registry_id: str,
    revocation_set: object,
    now_utc: str,
    trust_context: object = None,
    application: str = "hermes",
) -> Tuple[Optional[arh.ActivatedRegistryHandle], Tuple[str, ...]]:
    """Mint a trusted ActivatedRegistryHandle via a MODULE-OWNED trust boundary, or (None, sorted reasons).
    Fail-closed. Reuses the existing 14-step validations (registry frozen/integrity, root validate, manifest
    validate incl. quorum, digest/producer-set/app/gate compares, revocation integrity+match) UNCHANGED, then
    adds the externally-rooted gates and seals with the MODULE verifier."""
    import design.hermes_fw08_candidate_mode_v1 as cm
    import design.hermes_fw08_trust_anchor_provider_v1 as tap

    # 1. typed mode.
    m_reasons = cm.require_mode(candidate_mode)
    if m_reasons:
        return (None, ("RA-C-INVALID-CANDIDATE-MODE",))

    # 2. obtain the module-owned trust context. For REAL_CANDIDATE the production provider is unavailable →
    #    fail closed (no fallback). An injected context is accepted ONLY if it is a genuine module context.
    if trust_context is None:
        trust_context, tc_reasons = tap.resolve_trust_context(
            candidate_mode=candidate_mode, revocation_source=revocation_set)
        if trust_context is None:
            return (None, ("RA-C-TRUST-CONTEXT-UNAVAILABLE",) + tuple(sorted(tc_reasons)))
    if not tap.is_module_trust_context(trust_context):
        # A caller-built fake TrustContext (or a real-mode request that somehow reached here) is rejected.
        return (None, ("RA-C-TRUST-CONTEXT-UNTRUSTED",))
    if cm.is_real(candidate_mode):
        # Defence in depth: a module context can only exist for non-real modes, but never proceed under real.
        return (None, ("RA-C-TRUST-CONTEXT-UNAVAILABLE", "TC-PRODUCTION-PROVIDER-UNAVAILABLE"))

    # 3. resolver anchoring (exact identity + content-digest in the trust context).
    rid_reasons = tap.verify_resolver_identity(resolver, trust_context=trust_context)
    if rid_reasons:
        return (None, ("RA-C-RESOLVER-NOT-ANCHORED",) + tuple(sorted(rid_reasons)))

    # 4. registry frozen + integrity (steps 1-3 of the original flow).
    if not isinstance(registry, pr.ProducerRegistry):
        return (None, ("RA-REGISTRY-NOT-GOVERNED",))
    if not registry.frozen:
        return (None, ("RA-REGISTRY-NOT-FROZEN",))
    if not registry.integrity_ok():
        return (None, ("RA-REGISTRY-TAMPERED",))
    registry_digest = registry.canonical_digest()
    registry_policy_version = str(getattr(registry, "registry_policy_version", ""))

    # 5. resolver gate (fail closed on ANY RS-*) — the anchored resolver must also be a usable interface.
    rs_reasons = rs.require_resolver(resolver, real_candidate_mode=cm.is_real(candidate_mode))
    if rs_reasons:
        return (None, tuple(sorted(set(rs_reasons))))

    # 6. resolve + validate the authority root.
    try:
        root = resolver.resolve_authority_root(
            authority_root_id=authority_root_id, trust_domain_id=trust_domain_id, now_utc=now_utc)
    except rs.ResolverNotConfigured:
        return (None, ("RA-RESOLVER-NOT-CONFIGURED",))
    except rs.UntrustedAuthorityResolved:
        return (None, ("RA-UNTRUSTED-AUTHORITY",))
    root_reasons = ar.validate_authority_root(
        root, now_utc=now_utc, application=application, trust_domain_id=trust_domain_id)
    if root_reasons:
        return (None, tuple(sorted(set("RA-AUTHORITY:" + r for r in root_reasons))))

    # 7. resolve + validate the manifest against the registry + root (quorum validated inside).
    try:
        manifest = resolver.resolve_manifest(
            authority_root_id=authority_root_id, registry_id=registry_id,
            registry_version=registry_policy_version, registry_digest=registry_digest, now_utc=now_utc)
    except rs.ResolverNotConfigured:
        return (None, ("RA-RESOLVER-NOT-CONFIGURED",))
    except rs.UntrustedAuthorityResolved:
        return (None, ("RA-UNTRUSTED-AUTHORITY",))
    m_reasons2 = mf.validate_manifest_against_registry(
        manifest, registry=registry, authority_root=root, now_utc=now_utc, application=application)
    if m_reasons2:
        return (None, tuple(sorted(set("RA-MANIFEST:" + r for r in m_reasons2))))

    # 8. C3 EXTERNAL approver anchoring — EACH manifest approver must be in the trust context anchor set, not
    #    merely named by the root.
    for rec in getattr(manifest, "approval_records", ()):
        aid = getattr(rec, "approver_id", None)
        if str(getattr(rec, "approval_decision", "")) != "APPROVE":
            continue
        ap_reasons = tap.verify_approver_anchored(aid, trust_context=trust_context)
        if ap_reasons:
            return (None, ("RA-C-APPROVER-NOT-ANCHORED",) + tuple(sorted(ap_reasons)))

    # 9. revocation: present + integrity intact + no effective match (unchanged semantics).
    _integ = getattr(revocation_set, "integrity_ok", None)
    if revocation_set is None or not callable(_integ):
        return (None, ("RA-REVOCATION-SET-MISSING",))
    if not _integ():
        return (None, ("RA-REVOCATION-SET-TAMPERED",))
    if _revoked(revocation_set, registry=registry, root=root, manifest=manifest,
                registry_id=registry_id, now_utc=now_utc):
        return (None, ("RA-REVOKED",))

    # 10. exact id/version/policy/digest compare vs manifest.
    if manifest.registry_id != registry_id or manifest.registry_digest != registry_digest \
            or manifest.registry_policy_version != registry_policy_version:
        return (None, ("RA-DIGEST-MISMATCH",))
    if manifest.producer_set_digest != registry.producer_set_digest():
        return (None, ("RA-PRODUCER-SET-MISMATCH",))
    if manifest.application != application or getattr(root, "application", "") != application:
        return (None, ("RA-APPLICATION-MISMATCH",))
    if not manifest.allowed_gate_ids:
        return (None, ("RA-GATE-SCOPE",))

    # 11. mint the sealed handle — sealed by the MODULE verifier from the trust context (NOT a caller
    #     activation_authority). candidate_use_policy carries the typed mode.
    manifest_id = registry_id + "@" + registry_policy_version
    handle0 = arh.ActivatedRegistryHandle(
        contract_version=CONTRACT_VERSION,
        application=application,
        authority_root_id=authority_root_id,
        authority_root_digest=getattr(root, "authority_root_digest", ""),
        manifest_id=manifest_id,
        manifest_digest=getattr(manifest, "manifest_digest", ""),
        registry_id=registry_id,
        registry_version=registry_policy_version,
        registry_digest=registry_digest,
        producer_set_digest=registry.producer_set_digest(),
        activation_utc=now_utc,
        validity_end_utc=manifest.valid_until_utc,
        activation_reason="F2-R1-C externally-rooted activation via module trust boundary",
        resolver_identity=getattr(resolver, "resolver_identity", ""),
        resolver_version=getattr(resolver, "resolver_version", ""),
        resolver_evidence_reference=getattr(manifest, "provenance_reference", ""),
        candidate_use_policy=candidate_mode,
        handle_checksum="",
        lifecycle_state="ACTIVATED",
        seal="",
        _bound_registry=registry,
    )
    import dataclasses
    checksum = handle0.recompute_checksum()
    handle1 = dataclasses.replace(handle0, handle_checksum=checksum)
    verifier = trust_context.verifier()
    seal = verifier._seal(arh._seal_message(handle1))
    handle = dataclasses.replace(handle1, seal=seal)
    return (handle, tuple())


def _revoked(revocation_set: object, *, registry, root, manifest, registry_id: str, now_utc: str) -> bool:
    """True iff any effective revocation at `now_utc` names the authority root, the registry manifest, any
    registered producer, or any approver."""
    if revocation_set is None or not hasattr(revocation_set, "is_revoked"):
        return False
    if revocation_set.is_revoked(
            revocation_type="AUTHORITY_ROOT",
            target_id=getattr(root, "authority_root_id", ""), at_utc=now_utc):
        return True
    if revocation_set.is_revoked(
            revocation_type="REGISTRY_MANIFEST", target_id=registry_id, at_utc=now_utc):
        return True
    active = getattr(registry, "_active_map", None)
    if callable(active):
        for pid in active().keys():
            if revocation_set.is_revoked(
                    revocation_type="PRODUCER_REGISTRATION", target_id=pid, at_utc=now_utc):
                return True
    for rec in getattr(manifest, "approval_records", ()):
        aid = getattr(rec, "approver_id", None)
        if aid is not None and revocation_set.is_revoked(
                revocation_type="APPROVER", target_id=aid, at_utc=now_utc):
            return True
    return False
