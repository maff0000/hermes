"""FW-08 lifecycle stage-1 source-freeze + stage-2 candidate-identity contract tests (§11, INERT).

WO-HELM-HERMES-FW08-REAL-PROOF-LIFECYCLE-CANONICAL-SOURCE-FREEZE-AND-CANDIDATE-IDENTITY-CONTRACT-
IMPLEMENTATION-0001. Authority: HELM. Created (UTC): 2026-07-27. Contract version: 1.

These tests freeze NO real source, allocate NO real candidate, create NO tag/image/key/secret, configure NO
authority, and touch NO runtime. Real freeze and real allocation ALWAYS fail closed. Synthetic evidence
validates ONLY in test mode, module-token gated, classification set by the issuer never the caller. All tests
are ADDITIVE and PASS.

Run: cd <worktree> && python3 -m pytest tests/test_fw08_source_freeze_candidate_identity_v1.py -q
"""
from __future__ import annotations

import dataclasses
import inspect

import design.hermes_fw08_source_freeze_v1 as sf
import design.hermes_fw08_candidate_identity_v1 as ci
import design.hermes_fw08_lifecycle_state_v1 as ls


NOW = "2026-07-27T00:00:00+00:00"
EXPIRY = "2026-07-28T00:00:00+00:00"
STALE_REQ = "2026-07-20T00:00:00+00:00"   # >24h before NOW
PAST_EXPIRY = "2026-07-26T00:00:00+00:00"  # before NOW
SHA = "a" * 40
TREE = "deadbeef" * 8
REPO = "/srv/trading/hermes"


# ============================================================================ fixtures
def _freeze_request(**over):
    kw = dict(
        application="hermes", repository=REPO, canonical_branch="main",
        canonical_source_sha=SHA, source_tree_digest=TREE, request_id="req-1",
        requesting_authority="chief-architect", requested_utc=NOW,
        intended_lifecycle_id="LC-1", intended_candidate_purpose="real-proof",
        expiry_utc=EXPIRY, prerequisite_audit_reference="AUDIT-REF-1",
        prior_canonical_reference="prior-sha", configuration_classification="GOVERNED",
    )
    kw.update(over)
    return sf.new_source_freeze_request(**kw)


def _synthetic_evidence(req, **over):
    kw = dict(
        freeze_request=req, observed_sha=SHA, observed_tree_digest=TREE,
        agreement=True, clean_tree=True, github_identity="github/main@" + SHA,
        now_utc=NOW, expiry_utc=EXPIRY, evidence_reference="EV-01-REF-1",
        observer_identity="helm",
    )
    kw.update(over)
    ev, reasons = sf.new_synthetic_freeze_evidence(**kw)
    return ev, reasons


def _candidate(**over):
    kw = dict(
        lifecycle_id="LC-1", candidate_id=f"cand-{SHA[:12]}-1",
        canonical_source_sha=SHA, source_tree_digest=TREE,
        candidate_namespace=f"hermes-fw08-candidate/{SHA[:12]}",
        proposed_immutable_tag=f"cand-{SHA}", expected_image_repository="hermes-fw08-candidate/img",
        purpose="real-proof", allocation_request_utc=NOW, expiry_utc=EXPIRY,
        requesting_authority="chief-architect", prerequisite_freeze_evidence_digest="EV-01-DIGEST",
    )
    kw.update(over)
    return ci.new_candidate_identity(**kw)


# ============================================================================ §11 freeze-request rejections
def test_branch_without_exact_sha():
    req = _freeze_request(canonical_source_sha="abc123")
    r = sf.validate_source_freeze_request(req, now_utc=NOW)
    assert "SFR-MUTABLE-BRANCH-IDENTITY" in r
    assert "SFR-SHA-ABBREVIATED" in r


def test_wrong_repository():
    req = _freeze_request(repository="/srv/trading/argus")
    assert "SFR-REPOSITORY-MISMATCH" in sf.validate_source_freeze_request(req, now_utc=NOW)


def test_wrong_application():
    req = _freeze_request(application="argus")
    assert "SFR-APPLICATION-MISMATCH" in sf.validate_source_freeze_request(req, now_utc=NOW)


def test_non_canonical_branch():
    req = _freeze_request(canonical_branch="feature/x")
    assert "SFR-NON-CANONICAL-BRANCH" in sf.validate_source_freeze_request(req, now_utc=NOW)


def test_stale_request():
    req = _freeze_request(requested_utc=STALE_REQ)
    assert "SFR-STALE" in sf.validate_source_freeze_request(req, now_utc=NOW)


def test_expired_request():
    req = _freeze_request(expiry_utc=PAST_EXPIRY)
    assert "SFR-EXPIRED" in sf.validate_source_freeze_request(req, now_utc=NOW)


def test_missing_utc_on_request():
    req = _freeze_request(requested_utc="", expiry_utc="")
    assert "SFR-UTC-MISSING" in sf.validate_source_freeze_request(req, now_utc=NOW)


def test_missing_audit_ref():
    req = _freeze_request(prerequisite_audit_reference="")
    assert "SFR-AUDIT-REF-MISSING" in sf.validate_source_freeze_request(req, now_utc=NOW)


def test_missing_tree_digest():
    req = _freeze_request(source_tree_digest="")
    assert "SFR-TREE-DIGEST-MISSING" in sf.validate_source_freeze_request(req, now_utc=NOW)


def test_execution_authorised_true_on_request():
    # A caller-forged execution_authorised=True is rejected — the INVARIANT.
    req = _freeze_request(execution_authorised=True)
    assert "SFR-EXECUTION-AUTHORISED-FORBIDDEN" in sf.validate_source_freeze_request(req, now_utc=NOW)


def test_caller_status_forbidden():
    req = _freeze_request(configuration_classification="SOURCE_FROZEN")
    assert "SFR-CALLER-STATUS-FORBIDDEN" in sf.validate_source_freeze_request(req, now_utc=NOW)


def test_request_digest_tamper():
    req = _freeze_request()
    tampered = dataclasses.replace(req, requesting_authority="attacker")
    assert "SFR-DIGEST-TAMPER" in sf.validate_source_freeze_request(tampered, now_utc=NOW)


def test_request_wrong_type():
    assert sf.validate_source_freeze_request(object(), now_utc=NOW) == ("SFR-WRONG-TYPE",)


# ============================================================================ §11 freeze-evidence rejections
def test_real_freeze_unavailable():
    req = _freeze_request()
    ev, reasons = sf.observe_source_freeze(
        freeze_request=req, mode="REAL_CANDIDATE", observed_sha=SHA, observed_tree_digest=TREE,
        agreement=True, clean_tree=True, github_identity="gh", now_utc=NOW, expiry_utc=EXPIRY,
        evidence_reference="EV-01-REF", observer_identity="helm")
    assert ev is None
    assert reasons == ("REAL_PROOF_LIFECYCLE_REAL_FREEZE_NOT_AUTHORISED", "SF-REAL-FREEZE-UNAVAILABLE")


def test_real_freeze_via_real_flag():
    req = _freeze_request()
    ev, reasons = sf.observe_source_freeze(
        freeze_request=req, mode="TEST_ONLY", observed_sha=SHA, observed_tree_digest=TREE,
        agreement=True, clean_tree=True, github_identity="gh", now_utc=NOW, expiry_utc=EXPIRY,
        evidence_reference="EV-01-REF", observer_identity="helm", real_freeze=True)
    assert ev is None
    assert "SF-REAL-FREEZE-UNAVAILABLE" in reasons


def test_synthetic_requires_module_token():
    # A caller calling observe_source_freeze directly (no token) cannot mint synthetic evidence.
    req = _freeze_request()
    ev, reasons = sf.observe_source_freeze(
        freeze_request=req, mode="TEST_ONLY", observed_sha=SHA, observed_tree_digest=TREE,
        agreement=True, clean_tree=True, github_identity="gh", now_utc=NOW, expiry_utc=EXPIRY,
        evidence_reference="EV-01-REF", observer_identity="helm")
    assert ev is None
    assert reasons == ("SF-SYNTHETIC-REQUIRES-MODULE-TOKEN",)


def test_dirty_tree_evidence():
    req = _freeze_request()
    ev, _ = _synthetic_evidence(req, clean_tree=False)
    r = sf.validate_freeze_evidence(ev, freeze_request=req, now_utc=NOW, real_mode=False)
    assert "SF-DIRTY-TREE" in r


def test_local_main_origin_mismatch():
    req = _freeze_request()
    ev, _ = _synthetic_evidence(req, agreement=False)
    r = sf.validate_freeze_evidence(ev, freeze_request=req, now_utc=NOW, real_mode=False)
    assert "SF-LOCAL-MAIN-ORIGIN-MISMATCH" in r


def test_altered_tree_digest():
    req = _freeze_request()
    ev, _ = _synthetic_evidence(req, observed_tree_digest="0" * 64)
    r = sf.validate_freeze_evidence(ev, freeze_request=req, now_utc=NOW, real_mode=False)
    assert "SF-TREE-DIGEST-MISMATCH" in r


def test_sha_mismatch():
    req = _freeze_request()
    ev, _ = _synthetic_evidence(req, observed_sha="b" * 40)
    r = sf.validate_freeze_evidence(ev, freeze_request=req, now_utc=NOW, real_mode=False)
    assert "SF-SHA-MISMATCH" in r


def test_stale_expired_evidence():
    req = _freeze_request()
    ev_stale, _ = _synthetic_evidence(req, now_utc=STALE_REQ)
    r_stale = sf.validate_freeze_evidence(ev_stale, freeze_request=req, now_utc=NOW, real_mode=False)
    assert "SF-STALE" in r_stale
    ev_exp, _ = _synthetic_evidence(req, expiry_utc=PAST_EXPIRY)
    r_exp = sf.validate_freeze_evidence(ev_exp, freeze_request=req, now_utc=NOW, real_mode=False)
    assert "SF-EXPIRED" in r_exp


def test_caller_forged_classification_relabel_synthetic_to_real():
    # Relabel a module-minted synthetic record to a real classification. The seal (bound to classification)
    # breaks -> module-provenance fails -> also fails in real mode.
    req = _freeze_request()
    ev, _ = _synthetic_evidence(req)
    forged = dataclasses.replace(ev, freeze_classification="REAL_FREEZE_EVIDENCE")
    r = sf.validate_freeze_evidence(forged, freeze_request=req, now_utc=NOW, real_mode=True)
    assert "SF-SYNTHETIC-NOT-MODULE-MINTED" in r  # relabel breaks the seal
    # a real classification is not even in the permitted set here
    assert "SF-INVALID-CLASSIFICATION" in r


def test_synthetic_in_real_mode():
    # A genuine module-minted synthetic record never validates in real mode.
    req = _freeze_request()
    ev, _ = _synthetic_evidence(req)
    r = sf.validate_freeze_evidence(ev, freeze_request=req, now_utc=NOW, real_mode=True)
    assert "SF-SYNTHETIC-IN-REAL-MODE" in r


def test_copied_freeze_evidence():
    req = _freeze_request()
    ev1, _ = _synthetic_evidence(req, evidence_reference="EV-01-REF-A")
    # same observation content, different reference -> a copy.
    ev2, _ = _synthetic_evidence(req, evidence_reference="EV-01-REF-B")
    r = sf.validate_freeze_evidence(ev2, freeze_request=req, now_utc=NOW, real_mode=False,
                                    other_evidence=(ev1,))
    assert "SF-COPIED-EVIDENCE" in r


def test_reused_reference_is_copied():
    req = _freeze_request()
    ev1, _ = _synthetic_evidence(req, evidence_reference="EV-01-REF-X", observer_identity="a")
    ev2, _ = _synthetic_evidence(req, evidence_reference="EV-01-REF-X", observer_identity="b")
    r = sf.validate_freeze_evidence(ev2, freeze_request=req, now_utc=NOW, real_mode=False,
                                    other_evidence=(ev1,))
    assert "SF-COPIED-EVIDENCE" in r


def test_revoked_evidence():
    req = _freeze_request()
    ev, _ = _synthetic_evidence(req)
    revoked = dataclasses.replace(ev, revocation_status="REVOKED")
    r = sf.validate_freeze_evidence(revoked, freeze_request=req, now_utc=NOW, real_mode=False)
    assert "SF-REVOKED-EVIDENCE" in r


def test_content_digest_tamper():
    req = _freeze_request()
    ev, _ = _synthetic_evidence(req)
    tampered = dataclasses.replace(ev, observer_identity="attacker")
    r = sf.validate_freeze_evidence(tampered, freeze_request=req, now_utc=NOW, real_mode=False)
    assert "SF-CONTENT-DIGEST-TAMPER" in r


def test_missing_evidence_reference():
    req = _freeze_request()
    ev, _ = _synthetic_evidence(req, evidence_reference="")
    r = sf.validate_freeze_evidence(ev, freeze_request=req, now_utc=NOW, real_mode=False)
    assert "SF-EVIDENCE-REF-MISSING" in r


def test_caller_built_evidence_not_module_minted():
    # A caller who directly constructs an evidence object (no module seal) fails provenance.
    req = _freeze_request()
    forged = sf.SourceFreezeEvidence(
        contract_version="1", freeze_request_ref=req.request_digest, observed_canonical_sha=SHA,
        observed_source_tree_digest=TREE, local_main_origin_agreement=True, clean_tracked_tree=True,
        github_branch_identity="gh", observation_utc=NOW, observer_identity="attacker",
        evidence_reference="EV-01-REF", evidence_content_digest="", freeze_classification="SYNTHETIC_FREEZE_EVIDENCE",
        freeze_expiry_utc=EXPIRY, revocation_status="NONE", seal="deadbeef")
    forged = dataclasses.replace(forged, evidence_content_digest=forged.recompute_digest())
    r = sf.validate_freeze_evidence(forged, freeze_request=req, now_utc=NOW, real_mode=False)
    assert "SF-SYNTHETIC-NOT-MODULE-MINTED" in r


# ============================================================================ §11 candidate rejections
def test_real_allocation_unavailable():
    c, reasons = ci.allocate_candidate_identity(request={}, mode="REAL_CANDIDATE", now_utc=NOW)
    assert c is None
    assert reasons == ("CI-REAL-ALLOCATION-UNAVAILABLE", "REAL_PROOF_LIFECYCLE_REAL_ALLOCATION_NOT_AUTHORISED")


def test_candidate_synthetic_requires_token():
    c, reasons = ci.allocate_candidate_identity(request={}, mode="TEST_ONLY", now_utc=NOW)
    assert c is None
    assert reasons == ("CI-SYNTHETIC-REQUIRES-MODULE-TOKEN",)


def test_candidate_id_collision():
    c = _candidate()
    r = ci.validate_candidate_uniqueness(c, existing_candidate_ids=(c.candidate_id,))
    assert r == ("CI-CANDIDATE-ID-REUSE",)


def test_candidate_mutable_tag():
    c = _candidate(proposed_immutable_tag="stable")
    assert "CI-MUTABLE-TAG" in ci.validate_candidate_identity(c, now_utc=NOW)


def test_candidate_latest_forbidden():
    c = _candidate(proposed_immutable_tag="latest")
    assert "CI-LATEST-FORBIDDEN" in ci.validate_candidate_identity(c, now_utc=NOW)


def test_candidate_deployed_tag_reuse():
    c = _candidate(proposed_immutable_tag=f"c5fc2a62f424-{SHA}")
    assert "CI-DEPLOYED-TAG-REUSE" in ci.validate_candidate_identity(c, now_utc=NOW)


def test_candidate_cross_application_namespace():
    c = _candidate(candidate_namespace="argus-fw08-candidate/xyz")
    r = ci.validate_candidate_identity(c, now_utc=NOW)
    assert "CI-CROSS-APPLICATION-NAMESPACE" in r
    assert "CI-NAMESPACE-NOT-ISOLATED" in r


def test_candidate_namespace_not_isolated():
    c = _candidate(candidate_namespace="somewhere-else")
    assert "CI-NAMESPACE-NOT-ISOLATED" in ci.validate_candidate_identity(c, now_utc=NOW)


def test_candidate_unbound_source_sha():
    c = _candidate(proposed_immutable_tag="floating-tag", candidate_id="cand-1",
                   candidate_namespace="hermes-fw08-candidate/x")
    assert "CI-UNBOUND-SOURCE-SHA" in ci.validate_candidate_identity(c, now_utc=NOW)


def test_candidate_no_expiry():
    c = _candidate(expiry_utc="")
    assert "CI-NO-EXPIRY" in ci.validate_candidate_identity(c, now_utc=NOW)


def test_candidate_runtime_service_tag():
    c = _candidate(proposed_immutable_tag="hermes-consumer")
    r = ci.validate_candidate_identity(c, now_utc=NOW)
    assert "CI-RUNTIME-SERVICE-TAG" in r


def test_candidate_execution_authorised_forbidden():
    c = _candidate(execution_authorised=True)
    assert "CI-EXECUTION-AUTHORISED-FORBIDDEN" in ci.validate_candidate_identity(c, now_utc=NOW)


def test_candidate_real_status_forbidden():
    c = _candidate(candidate_status="REAL_ALLOCATION_UNAVAILABLE")
    assert "CI-REAL-STATUS-FORBIDDEN" in ci.validate_candidate_identity(c, now_utc=NOW)


def test_candidate_missing_utc():
    c = _candidate(allocation_request_utc="")
    assert "CI-UTC-MISSING" in ci.validate_candidate_identity(c, now_utc=NOW)


def test_candidate_missing_freeze_ev_ref():
    c = _candidate(prerequisite_freeze_evidence_digest="")
    assert "CI-FREEZE-EV-REF-MISSING" in ci.validate_candidate_identity(c, now_utc=NOW)


def test_candidate_lifecycle_id_missing():
    c = _candidate(lifecycle_id="")
    assert "CI-LIFECYCLE-ID-MISSING" in ci.validate_candidate_identity(c, now_utc=NOW)


def test_candidate_digest_tamper():
    c = _candidate()
    tampered = dataclasses.replace(c, requesting_authority="attacker")
    assert "CI-DIGEST-TAMPER" in ci.validate_candidate_identity(tampered, now_utc=NOW)


# ============================================================================ §11 lifecycle-state rejections
def test_no_force_or_boolean_advance_parameter():
    sig = inspect.signature(ls.advance_lifecycle_state)
    names = set(sig.parameters)
    assert "force" not in names
    assert "advance" not in names
    # no bare boolean that lets a caller assert progress independent of validity.
    for banned in ("force_advance", "override", "allow_real", "authorise"):
        assert banned not in names


def test_source_frozen_transition_fails_closed():
    new, reasons = ls.advance_lifecycle_state(
        current_state="LIFECYCLE_DESIGNED", target_state="SOURCE_FROZEN",
        source_freeze_request_valid=True, candidate_contract_valid=True, execution_authorised=False)
    assert new is None
    assert reasons == ("EXECUTION_NOT_AUTHORISED", "LS-REAL-STATE-NOT-AUTHORISED")


def test_candidate_allocated_transition_fails_closed():
    new, reasons = ls.advance_lifecycle_state(
        current_state="SOURCE_FREEZE_CONTRACT_READY", target_state="CANDIDATE_ALLOCATED",
        source_freeze_request_valid=True, candidate_contract_valid=True, execution_authorised=False)
    assert new is None
    assert reasons == ("EXECUTION_NOT_AUTHORISED", "LS-REAL-STATE-NOT-AUTHORISED")


def test_execution_authorised_forbidden_on_transition():
    new, reasons = ls.advance_lifecycle_state(
        current_state="LIFECYCLE_DESIGNED", target_state="SOURCE_FREEZE_CONTRACT_READY",
        source_freeze_request_valid=True, candidate_contract_valid=True, execution_authorised=True)
    assert new is None
    assert "LS-EXECUTION-AUTHORISED-FORBIDDEN" in reasons


def test_invalid_transition():
    new, reasons = ls.advance_lifecycle_state(
        current_state="LIFECYCLE_DESIGNED", target_state="AUTHORITY_READY",
        source_freeze_request_valid=True, candidate_contract_valid=True, execution_authorised=False)
    assert new is None
    # AUTHORITY_READY is a real state -> fails closed as real-state-not-authorised.
    assert "LS-REAL-STATE-NOT-AUTHORISED" in reasons


def test_readiness_transition_requires_valid_contracts():
    new, reasons = ls.advance_lifecycle_state(
        current_state="LIFECYCLE_DESIGNED", target_state="SOURCE_FREEZE_CONTRACT_READY",
        source_freeze_request_valid=False, candidate_contract_valid=True, execution_authorised=False)
    assert new is None
    assert "LS-FREEZE-REQUEST-INVALID" in reasons


# ============================================================================ POSITIVE synthetic flow
def test_positive_valid_freeze_request():
    req = _freeze_request()
    assert sf.validate_source_freeze_request(req, now_utc=NOW) == ()


def test_positive_module_minted_evidence_validates():
    req = _freeze_request()
    ev, reasons = _synthetic_evidence(req)
    assert reasons == ()
    assert ev is not None
    assert ev.freeze_classification == "SYNTHETIC_FREEZE_EVIDENCE"
    assert sf.validate_freeze_evidence(ev, freeze_request=req, now_utc=NOW, real_mode=False) == ()


def test_positive_valid_candidate_identity():
    c = _candidate()
    assert c.candidate_status == "SYNTHETIC_UNALLOCATED"
    assert c.execution_authorised is False
    assert ci.validate_candidate_identity(c, now_utc=NOW) == ()
    assert ci.validate_candidate_uniqueness(c, existing_candidate_ids=()) == ()


def test_positive_module_minted_candidate_validates():
    req = dict(
        lifecycle_id="LC-1", candidate_id=f"cand-{SHA[:12]}-9", canonical_source_sha=SHA,
        source_tree_digest=TREE, candidate_namespace=f"hermes-fw08-candidate/{SHA[:12]}",
        proposed_immutable_tag=f"cand-{SHA}", expected_image_repository="hermes-fw08-candidate/img",
        purpose="real-proof", expiry_utc=EXPIRY, requesting_authority="chief-architect",
        prerequisite_freeze_evidence_digest="EV-01-DIGEST",
    )
    c, reasons = ci.new_synthetic_candidate_identity(request=req, now_utc=NOW)
    assert reasons == ()
    assert c is not None and c.candidate_status == "SYNTHETIC_UNALLOCATED" and c.execution_authorised is False
    assert ci.validate_candidate_identity(c, now_utc=NOW) == ()


def test_positive_readiness_transition_succeeds():
    new, reasons = ls.advance_lifecycle_state(
        current_state="LIFECYCLE_DESIGNED", target_state="SOURCE_FREEZE_CONTRACT_READY",
        source_freeze_request_valid=True, candidate_contract_valid=True, execution_authorised=False)
    assert reasons == ()
    assert new == "SOURCE_FREEZE_CONTRACT_READY"


# ============================================================================ D-PR120-EV-TAIL
def test_eventual_evidence_record_ids():
    ids = sf.eventual_evidence_record_ids()
    assert ids == {
        "source_freeze": "EV-01-SOURCE-FREEZE",
        "candidate_allocation": "EV-02-CANDIDATE-ALLOCATION",
    }


def test_real_state_transition_impossible():
    assert ls.real_state_transition_possible() is False
    assert sf.production_real_freeze_available() is False
