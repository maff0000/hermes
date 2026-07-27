"""FW-08 F2-R3 execution/content independence + real-image proof tests (§14,§15).

WO-HELM-HERMES-FW08-F2-R3-EXECUTION-CONTENT-INDEPENDENCE-AND-REAL-IMAGE-PROOF-IMPLEMENTATION-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-27. Contract version: 1.

These tests build NO real image, run NO real docker/SBOM/scan, publish NOTHING, deploy NOTHING, wire NO
runtime, enable NO shadow, mutate NO live state, create NO live key/secret/authority, and add NO third-party
dependency (stdlib only). They prove the F2-R3 invariant:

    REAL_IMAGE_PROOF => DISTINCT_EXECUTIONS + CONTENT_RESOLVED_EVIDENCE + GOVERNED_PRODUCER_AUTHORITY
                        + EXTERNALLY_VERIFIED_ACTIVE_IMPORT

No real proof may be asserted from fixture data — real proof ALWAYS fails closed here. Synthetic proof
validates ONLY in TEST_ONLY, sealed by the module issuer, mintable ONLY via the module helper. All tests are
ADDITIVE and PASS.

Run: cd <worktree> && python3 -m pytest tests/test_fw08_f2_r3_execution_content_proof_v1.py -q
"""
from __future__ import annotations

import dataclasses
import inspect

import pytest

import design.hermes_fw08_execution_identity_v1 as exid
import design.hermes_fw08_content_resolution_v1 as cres
import design.hermes_fw08_producer_independence_v1 as pi
import design.hermes_fw08_legacy_comparator_containment_v1 as lc
import design.hermes_fw08_real_image_proof_v1 as rip
import design.hermes_fw08_active_import_evidence_v1 as ai
import tools.hermes_stage_b_build_v1 as w
import tests.test_fw08_f2_independent_anchors_v1 as f2


NOW = "2026-07-27T00:00:00+00:00"
LATER = "2026-07-27T01:00:00+00:00"
EXPIRY = "2026-07-28T00:00:00+00:00"
SRC = "a" * 40
CAND = f"fw08cand@{SRC}"
IMG = "sha256:" + "b" * 64


# ============================================================================ execution-identity fixtures
def _exec(role, **over):
    kw = dict(
        application="hermes", execution_id=f"{role}-exec-1", producer_id=f"{role}-producer",
        producer_registration_ref=f"{role}-reg-ref", process_identity=f"{role}-process",
        runner_identity=f"{role}-runner", host_container_identity=f"{role}-host",
        tool_identity=f"{role}-tool", executable_digest=(role[0] + "x") * 32, invocation_digest=f"{role}-inv",
        start_utc=NOW, completion_utc=LATER, authority_reference=f"{role}-authority",
        evidence_output_reference=f"{role}-output",
    )
    kw.update(over)
    return exid.new_execution_identity(**kw)


def _build_exec(**over):
    return _exec("build", **over)


def _oci_exec(**over):
    return _exec("oci", **over)


# ============================================================================ content-resolved fixtures
def _content(role, **over):
    kw = dict(
        reference=f"{role}-content-ref", content_digest=(role[0] + "d") * 32, media_type="application/json",
        schema_type=role.upper(), size_bytes=128, creation_utc=NOW, producer_id=f"{role}-producer",
        provenance=f"{role}-inspection", immutable_storage_id=f"{role}-store", storage_version="v1",
    )
    kw.update(over)
    return cres.new_content_resolved(**kw)


def _build_content(**over):
    return _content("build", **over)


def _oci_content(**over):
    return _content("oci", **over)


class _FA:
    manifest_digest = "sha256:" + "d" * 64
    config_digest = "sha256:" + "e" * 64
    filesystem_digest = "sha256:" + "c" * 64
    layer_digests = ("sha256:" + "f" * 64,)


class _II:
    image_id = IMG


# ============================================================================ §4/§5 execution independence
def test_positive_distinct_executions_independent():
    assert exid.evaluate_execution_independence(
        build_execution=_build_exec(), oci_execution=_oci_exec(), candidate_mode="TEST_ONLY") == ()


def test_reject_same_execution_id():
    r = exid.evaluate_execution_independence(
        build_execution=_build_exec(execution_id="shared"), oci_execution=_oci_exec(execution_id="shared"),
        candidate_mode="TEST_ONLY")
    assert "EI-SAME-EXECUTION-ID" in r


def test_reject_same_process():
    r = exid.evaluate_execution_independence(
        build_execution=_build_exec(process_identity="shared-proc"),
        oci_execution=_oci_exec(process_identity="shared-proc"), candidate_mode="TEST_ONLY")
    assert "EI-SAME-PROCESS" in r


def test_reject_same_runner_and_host():
    r = exid.evaluate_execution_independence(
        build_execution=_build_exec(runner_identity="R", host_container_identity="H"),
        oci_execution=_oci_exec(runner_identity="R", host_container_identity="H"), candidate_mode="TEST_ONLY")
    assert "EI-SAME-PROCESS" in r


def test_reject_same_object():
    e = _build_exec()
    r = exid.evaluate_execution_independence(build_execution=e, oci_execution=e, candidate_mode="TEST_ONLY")
    assert "EI-SAME-OBJECT" in r


def test_reject_copied_relabelled_execution_evidence():
    """A relabel changes ONLY execution_id + producer_id; everything else identical -> a copy."""
    base = _build_exec()
    copy_relabelled = _build_exec(execution_id="oci-exec-9", producer_id="oci-producer")
    r = exid.evaluate_execution_independence(
        build_execution=base, oci_execution=copy_relabelled, candidate_mode="TEST_ONLY")
    assert "EI-COPIED-EXECUTION-EVIDENCE" in r


def test_reject_one_execution_both_roles_via_output_ref():
    r = exid.evaluate_execution_independence(
        build_execution=_build_exec(evidence_output_reference="shared-out"),
        oci_execution=_oci_exec(evidence_output_reference="shared-out"), candidate_mode="TEST_ONLY")
    assert "EI-ONE-EXECUTION-BOTH-ROLES" in r


def test_reject_one_execution_both_roles_via_provenance():
    """The build execution id appears in the oci execution's registration ref -> one execution emitted both."""
    r = exid.evaluate_execution_independence(
        build_execution=_build_exec(execution_id="EXEC-XYZ"),
        oci_execution=_oci_exec(producer_registration_ref="derived-from-EXEC-XYZ"),
        candidate_mode="TEST_ONLY")
    assert "EI-ONE-EXECUTION-BOTH-ROLES" in r


def test_changed_producer_labels_only_still_rejected():
    """Changing ONLY the producer id/label does NOT make two runs independent — same executable+authority+
    runner (and a copy) is caught by execution + executable/authority guards."""
    base = _build_exec()
    relabel = _build_exec(producer_id="different-producer-label")
    r = exid.evaluate_execution_independence(
        build_execution=base, oci_execution=relabel, candidate_mode="TEST_ONLY")
    assert "EI-COMMON-EXECUTABLE-AND-AUTHORITY" in r
    assert "EI-COPIED-EXECUTION-EVIDENCE" in r


def test_changed_tool_labels_only_still_rejected():
    """Changing ONLY the tool NAME does NOT bypass — independence is keyed on executable_digest + authority +
    runner, not the tool name."""
    base = _build_exec()
    relabel = _build_exec(execution_id="x2", producer_id="p2", tool_identity="renamed-tool")
    r = exid.evaluate_execution_independence(
        build_execution=base, oci_execution=relabel, candidate_mode="TEST_ONLY")
    assert "EI-COMMON-EXECUTABLE-AND-AUTHORITY" in r


def test_reject_common_executable_and_authority():
    """§5 same executable_digest + authority_reference + runner_identity (with distinct ids/process) is one
    executable run confirming its own output."""
    ex = "z" * 64
    r = exid.evaluate_execution_independence(
        build_execution=_build_exec(executable_digest=ex, authority_reference="A", runner_identity="R",
                                    process_identity="pb"),
        oci_execution=_oci_exec(executable_digest=ex, authority_reference="A", runner_identity="R",
                                process_identity="po"),
        candidate_mode="TEST_ONLY")
    assert "EI-COMMON-EXECUTABLE-AND-AUTHORITY" in r


def test_reject_wrong_type():
    r = exid.evaluate_execution_independence(
        build_execution={"not": "exec"}, oci_execution=_oci_exec(), candidate_mode="TEST_ONLY")
    assert r == ("EI-WRONG-TYPE",)


def test_reject_missing_execution_identity():
    bad = dataclasses.replace(_build_exec(), execution_id="")
    r = exid.evaluate_execution_independence(
        build_execution=bad, oci_execution=_oci_exec(), candidate_mode="TEST_ONLY")
    assert "EI-MISSING-EXECUTION-IDENTITY" in r


def test_reject_unverifiable_execution():
    tampered = dataclasses.replace(_build_exec(), tool_identity="mutated-after-digest")
    r = exid.evaluate_execution_independence(
        build_execution=tampered, oci_execution=_oci_exec(), candidate_mode="TEST_ONLY")
    assert "EI-UNVERIFIABLE-EXECUTION" in r


def test_reject_synthetic_execution_in_real_mode():
    r = exid.evaluate_execution_independence(
        build_execution=_build_exec(classification="SYNTHETIC_TEST_EXECUTION"),
        oci_execution=_oci_exec(classification="SYNTHETIC_TEST_EXECUTION"), candidate_mode="REAL_CANDIDATE")
    assert "EI-SYNTHETIC-EXECUTION-IN-REAL-MODE" in r


def test_dual_role_exception_supplied_but_not_activated():
    r = exid.evaluate_execution_independence(
        build_execution=_build_exec(), oci_execution=_oci_exec(), candidate_mode="TEST_ONLY",
        dual_role_exception=object())
    assert "EI-DUAL-ROLE-EXCEPTION-NOT-ACTIVATED" in r


def test_no_independent_boolean_parameter_execution():
    """STRUCTURAL: a caller cannot assert execution independence — there is no `independent` parameter."""
    params = set(inspect.signature(exid.evaluate_execution_independence).parameters)
    assert "independent" not in params
    assert {"build_execution", "oci_execution", "candidate_mode"} <= params


# ============================================================================ §6 content resolution
def test_resolve_and_verify_positive():
    bc = _build_content()
    resolver = cres.new_synthetic_content_resolver({bc.reference: bc})
    got, r = cres.resolve_and_verify(bc.reference, bc.content_digest, resolver=resolver, now_utc=NOW)
    assert got is bc and r == ()


def test_require_content_resolver_missing_and_untrusted():
    assert cres.require_content_resolver(None, real_mode=False) == ("CR-RESOLVER-MISSING",)
    caller_built = cres.SyntheticContentResolver({})   # no module token -> untrusted
    assert "CR-RESOLVER-UNTRUSTED" in cres.require_content_resolver(caller_built, real_mode=False)


def test_require_content_resolver_synthetic_in_real_mode():
    bc = _build_content()
    trusted = cres.new_synthetic_content_resolver({bc.reference: bc})
    r = cres.require_content_resolver(trusted, real_mode=True)
    assert "CR-SYNTHETIC-IN-REAL-MODE" in r


def test_production_content_resolver_unavailable():
    prod = cres.ProductionContentResolver()
    with pytest.raises(cres.ContentResolverUnavailable):
        prod.resolve("any-ref", now_utc=NOW)


def test_unresolved_reference():
    resolver = cres.new_synthetic_content_resolver({})
    got, r = cres.resolve_and_verify("ghost", "d" * 64, resolver=resolver, now_utc=NOW)
    assert got is None and "CR-UNRESOLVED-REFERENCE" in r


def test_mutable_reference():
    bc = _build_content(immutable_storage_id="", storage_version="")
    resolver = cres.new_synthetic_content_resolver({bc.reference: bc})
    got, r = cres.resolve_and_verify(bc.reference, bc.content_digest, resolver=resolver, now_utc=NOW)
    assert got is None and "CR-MUTABLE-REFERENCE" in r


def test_reference_content_mismatch():
    bc = _build_content()
    resolver = cres.new_synthetic_content_resolver({bc.reference: bc})
    got, r = cres.resolve_and_verify(bc.reference, "9" * 64, resolver=resolver, now_utc=NOW)
    assert got is None and "CR-REFERENCE-CONTENT-MISMATCH" in r


def test_changed_content_behind_reference():
    bc = _build_content(storage_version="v2")
    resolver = cres.new_synthetic_content_resolver({bc.reference: bc})
    got, r = cres.resolve_and_verify(
        bc.reference, bc.content_digest, resolver=resolver, now_utc=NOW, expected_storage_version="v1")
    assert got is None and "CR-CHANGED-CONTENT" in r


def test_caller_provided_content_digest_ignored_authority_is_resolution():
    """STRUCTURAL: resolve_and_verify has no parameter that lets the evidence declare its OWN authoritative
    digest — the digest comes from the RESOLVED content, and the expected digest is an INDEPENDENT input."""
    params = set(inspect.signature(cres.resolve_and_verify).parameters)
    assert {"reference", "expected_content_digest", "resolver", "now_utc"} <= params
    # the resolved content's OWN declared digest is authoritative; a reference cannot self-certify a digest it
    # does not actually resolve to.
    bc = _build_content()
    resolver = cres.new_synthetic_content_resolver({bc.reference: bc})
    got, r = cres.resolve_and_verify(bc.reference, "d" * 64, resolver=resolver, now_utc=NOW)
    assert got is None and "CR-REFERENCE-CONTENT-MISMATCH" in r


# ============================================================================ §7 near-copy / alias
def _oci_records_independent():
    """A build record + a genuinely-independent OCI record (own inspection digests + metadata)."""
    build = f2._build_result()
    oci = f2._oci_inspection()   # carries manifest/config/fs/layer + extraction_method + tool_identity
    return build, oci


def test_nearcopy_positive_independent_oci_passes():
    build, oci = _oci_records_independent()
    bc = _build_content()
    oc = _oci_content(content_digest="0" * 64)
    # embed the resolved OCI digest into the oci record so it is BACKED (not fabricated).
    oci = dict(oci, resolved_content_digest=oc.content_digest)
    r = cres.detect_alias_or_nearcopy(build_content=bc, oci_content=oc, build_result=build, oci_inspection=oci)
    assert r == (), r


def test_nearcopy_alias_same_content():
    build, oci = _oci_records_independent()
    shared = "5" * 64
    bc = _build_content(content_digest=shared, reference="ref-A")
    oc = _oci_content(content_digest=shared, reference="ref-B")
    oci = dict(oci, resolved_content_digest=shared)
    r = cres.detect_alias_or_nearcopy(build_content=bc, oci_content=oc, build_result=build, oci_inspection=oci)
    assert "CR-ALIAS-SAME-CONTENT" in r


def test_nearcopy_build_dressed_as_oci():
    """OCI record with NO independent inspection fields (only may-agree image/candidate/source) -> near-copy."""
    build = f2._build_result()
    dressed = {"image_id": build["image_id"], "candidate_id": build["candidate_id"],
               "source_sha": build["source_sha"], "evidence_ref": "oci-relabelled"}
    bc = _build_content()
    oc = _oci_content()
    r = cres.detect_alias_or_nearcopy(
        build_content=bc, oci_content=oc, build_result=build, oci_inspection=dressed)
    assert "CR-NEAR-COPY-FROM-BUILD" in r


def test_nearcopy_fabricated_oci_digest():
    """OCI digests declared, but the resolved OCI content does not back them."""
    build, oci = _oci_records_independent()
    bc = _build_content()
    oc = _oci_content(content_digest="7" * 64)   # not present in the oci record
    r = cres.detect_alias_or_nearcopy(build_content=bc, oci_content=oc, build_result=build, oci_inspection=oci)
    assert "CR-FABRICATED-OCI-DIGEST" in r


def test_nearcopy_oci_from_active_import():
    build, oci = _oci_records_independent()
    oci = dict(oci, evidence_ref="oci-active_import-tainted")
    bc = _build_content()
    oc = _oci_content(content_digest="0" * 64)
    oci["resolved_content_digest"] = oc.content_digest
    r = cres.detect_alias_or_nearcopy(build_content=bc, oci_content=oc, build_result=build, oci_inspection=oci)
    assert "CR-OCI-FROM-ACTIVE-IMPORT" in r


def test_nearcopy_embedded_evidence():
    build = f2._build_result()
    oci = dict(f2._oci_inspection(), nested_build=dict(build))
    bc = _build_content()
    oc = _oci_content(content_digest="0" * 64)
    oci["resolved_content_digest"] = oc.content_digest
    r = cres.detect_alias_or_nearcopy(build_content=bc, oci_content=oc, build_result=build, oci_inspection=oci)
    assert "CR-EMBEDDED-EVIDENCE" in r


# ============================================================================ §8 governed dual-role exception
def _valid_governed(**over):
    kw = dict(
        exception_id="GDRE-1", application="hermes", candidate_scope="fw08cand",
        build_execution_identity="build-exec", oci_execution_identity="oci-exec",
        build_evidence_ref="build-ref", oci_evidence_ref="oci-ref",
        build_content_digest="b" * 64, oci_content_digest="o" * 64,
        build_approval="approver-build", oci_approval="approver-oci",
        authority_root_binding="external-root-1", expiry_utc=EXPIRY,
        revocation_reference="revlist-1", audit_reason="governed dual-role trial",
        compensating_controls=("four-eyes", "segregated-runner"),
        approval_authority="external-exception-approver", created_by="fw08-f2-r3-setup",
    )
    kw.update(over)
    return pi.new_governed_dual_role_exception(**kw)


def test_governed_valid_but_not_activated():
    exc = _valid_governed()
    assert pi.validate_governed_dual_role_exception(exc, now_utc=NOW) == ()
    assert pi.governed_dual_role_active() is False


def test_governed_self_approved():
    exc = _valid_governed(approval_authority="fw08-f2-r3-setup")   # == created_by
    assert "GDRE-SELF-APPROVED" in pi.validate_governed_dual_role_exception(exc, now_utc=NOW)


def test_governed_wildcard_scope():
    exc = _valid_governed(candidate_scope="*")
    assert "GDRE-WILDCARD-SCOPE" in pi.validate_governed_dual_role_exception(exc, now_utc=NOW)


def test_governed_same_execution_and_content():
    exc = _valid_governed(oci_execution_identity="build-exec", oci_content_digest="b" * 64)
    r = pi.validate_governed_dual_role_exception(exc, now_utc=NOW)
    assert "GDRE-SAME-EXECUTION" in r and "GDRE-SAME-CONTENT-DIGEST" in r


def test_governed_missing_controls_and_binding_and_expired():
    exc = _valid_governed(compensating_controls=(), authority_root_binding="",
                          expiry_utc="2020-01-01T00:00:00+00:00")
    r = pi.validate_governed_dual_role_exception(exc, now_utc=NOW)
    assert "GDRE-NO-COMPENSATING-CONTROLS" in r and "GDRE-AUTHORITY-BINDING-MISSING" in r \
        and "GDRE-EXPIRED" in r


def test_governed_digest_tamper():
    exc = _valid_governed()
    tampered = dataclasses.replace(exc, audit_reason="mutated after digest")
    assert "GDRE-DIGEST-TAMPER" in pi.validate_governed_dual_role_exception(tampered, now_utc=NOW)


def test_f2r2_dual_role_exception_untouched():
    """The F2-R2 DualRoleException + validator still exist and behave (add-only guarantee)."""
    assert hasattr(pi, "DualRoleException") and hasattr(pi, "validate_dual_role_exception")


# ============================================================================ §9 legacy comparator containment
def test_comparator_not_readiness_authority():
    assert lc.comparator_is_readiness_authority() is False
    assert lc.LEGACY_COMPARATOR_NAME == "validate_image_bound_active_import"
    assert lc.LEGACY_COMPARATOR_STATUS == "CONTAINED_FIXTURE_COMPARISON_ONLY_NOT_READINESS_AUTHORITY"


def test_wrapper_source_has_no_readiness_bypass():
    """The REAL Stage-B wrapper routes readiness ONLY through the governed validators."""
    src = inspect.getsource(w)
    assert lc.assert_no_readiness_bypass(active_import_module=ai, wrapper_source=src) == ()


def test_direct_comparator_call_is_a_bypass():
    bad = "def readiness():\n    return validate_image_bound_active_import(ev)\n"
    r = lc.assert_no_readiness_bypass(active_import_module=ai, wrapper_source=bad)
    assert "LC-WRAPPER-DIRECT-COMPARATOR" in r


def test_dynamic_route_is_a_bypass():
    dyn = "def readiness():\n    fn = getattr(mod, computed_name)\n    return fn(ev)\n"
    r = lc.assert_no_readiness_bypass(active_import_module=ai, wrapper_source=dyn)
    assert "LC-DYNAMIC-ROUTE" in r


def test_guard_not_proof_refuses_comparator_provenance():
    assert lc.guard_not_proof("validate_image_bound_active_import") == ("LC-COMPARATOR-NOT-PROOF-AUTHORITY",)
    assert lc.guard_not_proof("validate_active_import_against_bundle") == ()


def test_legacy_comparator_still_callable_fixture_comparison():
    """CONTAINMENT preserves fixture-level comparison: the comparator is unchanged and still callable."""
    kw = dict(expected_image_id=f2.IMG, expected_source_sha=f2.SRC, expected_fs_digest=f2.FS,
              expected_candidate_id=f2.CAND, trusted_producer_refs=(f2.AI_PID,), now_utc=f2.NOW,
              phase2_prefix=f2.P2_PREFIX, phase2_suffix=f2.P2_SUFFIX, required_phase2_count=10)
    v = ai.validate_image_bound_active_import(f2._ai_evidence(), **kw)
    assert v.accepted and v.inert


# ============================================================================ §10-§12 real-image proof
def _synth_proof(**over):
    kw = dict(
        source_sha=SRC, candidate_image_id=CAND, build_execution=_build_exec(), oci_execution=_oci_exec(),
        build_content=_build_content(), oci_content=_oci_content(), image_identity_anchor=_II(),
        filesystem_anchor=_FA(), activated_handle=None, active_import_verdict=None,
        sbom_reference="sbom-1", vulnerability_result_reference="vuln-1", trust_context=None,
        now_utc=NOW, expiry_utc=EXPIRY,
    )
    kw.update(over)
    return rip.new_synthetic_test_proof(**kw)


def test_real_candidate_assemble_fails_closed():
    p, r = rip.assemble_proof(
        candidate_mode="REAL_CANDIDATE", source_sha=SRC, candidate_image_id=CAND,
        build_execution=_build_exec(), oci_execution=_oci_exec(), build_content=_build_content(),
        oci_content=_oci_content(), image_identity_anchor=_II(), filesystem_anchor=_FA(),
        activated_handle=None, active_import_verdict=None, sbom_reference="s",
        vulnerability_result_reference="v", trust_context=None, now_utc=NOW, expiry_utc=EXPIRY)
    assert p is None
    assert "RIP-REAL-SOURCE-UNAVAILABLE" in r
    assert "F2R3-REAL-PROOF-REQUIRES-REAL-IMAGE-AND-AUTHORITY" in r
    assert rip.production_real_image_source_available() is False


def test_caller_cannot_mint_synthetic_without_module_token():
    p, r = rip.assemble_proof(
        candidate_mode="TEST_ONLY", source_sha=SRC, candidate_image_id=CAND,
        build_execution=_build_exec(), oci_execution=_oci_exec(), build_content=_build_content(),
        oci_content=_oci_content(), image_identity_anchor=_II(), filesystem_anchor=_FA(),
        activated_handle=None, active_import_verdict=None, sbom_reference="s",
        vulnerability_result_reference="v", trust_context=None, now_utc=NOW, expiry_utc=EXPIRY)
    assert p is None and r == ("RIP-SYNTHETIC-REQUIRES-MODULE-TOKEN",)


def test_module_helper_mints_synthetic_and_verifies():
    p, r = _synth_proof()
    assert p is not None and r == (), r
    assert p.proof_classification == "SYNTHETIC_TEST_PROOF"
    ok, vr = rip.verify_proof(p, now_utc=LATER, real_mode=False)
    assert ok and vr == (), vr


def test_assemble_rejects_non_independent_executions():
    e = _build_exec()
    p, r = _synth_proof(build_execution=e, oci_execution=e)
    assert p is None and "RIP-EXECUTIONS-NOT-INDEPENDENT" in r


def test_assemble_rejects_near_copy():
    build = f2._build_result()
    dressed = {"image_id": build["image_id"], "candidate_id": build["candidate_id"],
               "source_sha": build["source_sha"], "evidence_ref": "oci-relabelled"}
    p, r = _synth_proof(build_result=build, oci_inspection=dressed)
    assert p is None and "RIP-NEAR-COPY" in r


def test_verify_wrong_type():
    ok, r = rip.verify_proof({"not": "proof"}, now_utc=LATER, real_mode=False)
    assert not ok and r == ("RIP-WRONG-TYPE",)


def test_verify_forged_seal_rejected():
    """A copied real-proof-SHAPED object built by a caller (no module seal) fails RIP-SEAL-INVALID."""
    p, _ = _synth_proof()
    forged = dataclasses.replace(p, seal="deadbeef" * 8)
    ok, r = rip.verify_proof(forged, now_utc=LATER, real_mode=False)
    assert not ok and r == ("RIP-SEAL-INVALID",)


def test_verify_caller_constructed_proof_object_rejected():
    """A caller who constructs a RealImageProof directly (no issuer seal) is rejected."""
    fake = rip.RealImageProof(
        contract_version="1", canonical_source_sha=SRC, candidate_image_id=CAND, immutable_image_digest=IMG,
        build_execution_identity_ref="b", oci_execution_identity_ref="o", build_producer_id="bp",
        oci_producer_id="op", resolved_build_evidence_digest="bd", resolved_oci_evidence_digest="od",
        manifest_digest="m", config_digest="c", layer_digests=("l",), filesystem_digest="f",
        sbom_reference="s", vulnerability_result_reference="v", trust_anchor_lineage_ref="t",
        resolver_lineage_ref="r", activated_registry_handle_ref="h", active_import_result_ref="a",
        proof_creation_utc=NOW, proof_expiry_utc=EXPIRY, proof_classification="REAL_IMAGE_PROOF",
        proof_digest="deadbeef", seal="deadbeef")
    ok, r = rip.verify_proof(fake, now_utc=LATER, real_mode=True)
    assert not ok and r == ("RIP-SEAL-INVALID",)


def test_verify_synthetic_relabelled_real_rejected():
    """A synthetic proof relabelled to REAL_IMAGE_PROOF breaks the seal (seal binds classification)."""
    p, _ = _synth_proof()
    relabelled = dataclasses.replace(p, proof_classification="REAL_IMAGE_PROOF")
    ok, r = rip.verify_proof(relabelled, now_utc=LATER, real_mode=True)
    assert not ok and "RIP-SEAL-INVALID" in r


def test_verify_synthetic_in_real_mode_rejected():
    """Even without relabelling, a genuine synthetic proof is rejected in real_mode."""
    p, _ = _synth_proof()
    ok, r = rip.verify_proof(p, now_utc=LATER, real_mode=True)
    assert not ok and "RIP-SYNTHETIC-IN-REAL-MODE" in r


def test_verify_digest_tamper_rejected():
    p, _ = _synth_proof()
    tampered = dataclasses.replace(p, canonical_source_sha="9" * 40)   # body changed, digest no longer matches
    ok, r = rip.verify_proof(tampered, now_utc=LATER, real_mode=False)
    # seal still verifies (covers digest+classification, both unchanged) but digest recompute fails.
    assert not ok and "RIP-DIGEST-TAMPER" in r


def test_verify_expired_rejected():
    p, _ = _synth_proof(expiry_utc=NOW)   # created and expires at NOW
    ok, r = rip.verify_proof(p, now_utc=LATER, real_mode=False)
    assert not ok and "RIP-EXPIRED" in r


def test_verify_replay_source_mismatch():
    p, _ = _synth_proof()
    ok, r = rip.verify_proof(p, now_utc=LATER, real_mode=False, expected_source_sha="9" * 40)
    assert not ok and "RIP-SOURCE-MISMATCH" in r


def test_verify_replay_image_mismatch():
    p, _ = _synth_proof()
    ok, r = rip.verify_proof(p, now_utc=LATER, real_mode=False, expected_image_id="sha256:" + "9" * 64)
    assert not ok and "RIP-IMAGE-MISMATCH" in r


def test_verify_candidate_tag_reuse():
    p, _ = _synth_proof()
    ok, r = rip.verify_proof(
        p, now_utc=LATER, real_mode=False, expected_candidate_image_id="other-candidate@x")
    assert not ok and "RIP-CANDIDATE-TAG-REUSE" in r


def test_verify_substituted_producer_and_sbom_and_vuln():
    p, _ = _synth_proof()
    ok, r = rip.verify_proof(
        p, now_utc=LATER, real_mode=False, expected_build_producer_id="other-bp",
        expected_sbom_reference="other-sbom", expected_vulnerability_result_reference="other-vuln")
    assert not ok
    assert "RIP-SUBSTITUTED-PRODUCER" in r and "RIP-SUBSTITUTED-SBOM" in r and "RIP-SUBSTITUTED-VULN" in r


def test_verify_revoked_at_use():
    class _Rev:
        def is_revoked(self, *, revocation_type, target_id, at_utc):
            return target_id == SRC   # the source is revoked
    p, _ = _synth_proof()
    ok, r = rip.verify_proof(p, now_utc=LATER, real_mode=False, current_revocation=_Rev())
    assert not ok and "RIP-REVOKED-AT-USE" in r


def test_verify_no_independence_boolean_on_assemble():
    """STRUCTURAL: neither assemble_proof nor verify_proof exposes an `independent` classification flag the
    caller can set — classification is set by the issuer."""
    for fn in (rip.assemble_proof, rip.verify_proof):
        params = set(inspect.signature(fn).parameters)
        assert "independent" not in params
        assert "proof_classification" not in params
        assert "classification" not in params


# ============================================================================ §15 POSITIVE full synthetic flow
def test_positive_full_synthetic_flow_mints_and_the_same_under_real_rejects():
    """Full synthetic flow: distinct executions/producers/tools, independently resolved content, matching
    legit image identity, independent OCI digests, synthetic authority + activated registry + active-import +
    SBOM + vuln refs -> assemble_proof(TEST_ONLY) mints a SYNTHETIC_TEST_PROOF and verify_proof(test mode)
    accepts. The SAME objects under REAL_CANDIDATE reject."""
    b_exec, o_exec = _build_exec(), _oci_exec()
    assert exid.evaluate_execution_independence(
        build_execution=b_exec, oci_execution=o_exec, candidate_mode="TEST_ONLY") == ()

    bc, oc = _build_content(), _oci_content()
    resolver = cres.new_synthetic_content_resolver({bc.reference: bc, oc.reference: oc})
    got_b, rb = cres.resolve_and_verify(bc.reference, bc.content_digest, resolver=resolver, now_utc=NOW)
    got_o, ro = cres.resolve_and_verify(oc.reference, oc.content_digest, resolver=resolver, now_utc=NOW)
    assert got_b is bc and rb == () and got_o is oc and ro == ()

    build, oci = _oci_records_independent()
    oci = dict(oci, resolved_content_digest=oc.content_digest)
    assert cres.detect_alias_or_nearcopy(
        build_content=bc, oci_content=oc, build_result=build, oci_inspection=oci) == ()

    p, r = rip.new_synthetic_test_proof(
        source_sha=SRC, candidate_image_id=CAND, build_execution=b_exec, oci_execution=o_exec,
        build_content=bc, oci_content=oc, image_identity_anchor=_II(), filesystem_anchor=_FA(),
        activated_handle=None, active_import_verdict=None, sbom_reference="synthetic-sbom",
        vulnerability_result_reference="synthetic-vuln", trust_context=None, now_utc=NOW, expiry_utc=EXPIRY,
        build_result=build, oci_inspection=oci)
    assert p is not None and r == (), r
    assert p.proof_classification == "SYNTHETIC_TEST_PROOF"

    ok, vr = rip.verify_proof(p, now_utc=LATER, real_mode=False)
    assert ok and vr == (), vr

    # the SAME objects under REAL_CANDIDATE reject — no real proof can be minted here.
    p2, r2 = rip.assemble_proof(
        candidate_mode="REAL_CANDIDATE", source_sha=SRC, candidate_image_id=CAND, build_execution=b_exec,
        oci_execution=o_exec, build_content=bc, oci_content=oc, image_identity_anchor=_II(),
        filesystem_anchor=_FA(), activated_handle=None, active_import_verdict=None,
        sbom_reference="synthetic-sbom", vulnerability_result_reference="synthetic-vuln", trust_context=None,
        now_utc=NOW, expiry_utc=EXPIRY, build_result=build, oci_inspection=oci)
    assert p2 is None and "RIP-REAL-SOURCE-UNAVAILABLE" in r2

    # and verifying the (valid) synthetic proof in real mode also rejects.
    ok3, r3 = rip.verify_proof(p, now_utc=LATER, real_mode=True)
    assert not ok3 and "RIP-SYNTHETIC-IN-REAL-MODE" in r3
