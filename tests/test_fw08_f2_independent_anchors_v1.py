"""FW-08 F-2 independent active-import anchors tests (§13-§16, §19).

WO-HELM-HERMES-FW08-F2-INDEPENDENT-ACTIVE-IMPORT-ANCHORS-IMPLEMENTATION-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-21. Contract version: 1.

These tests build NO real image, run NO real docker/SBOM/scan, publish NOTHING, deploy NOTHING, wire NO
runtime, enable NO shadow, mutate NO live state, and add NO third-party dependency (stdlib only). They prove
the F-2 correction: the EXPECTED anchors an active-import verdict compares against are derived from
INDEPENDENT governed producers (build result + OCI inspection + producer registry), NEVER from the
active-import evidence record itself. The prevention is STRUCTURAL (type system + absent parameters), not a
value check.

Run: cd <worktree> && python3 -m pytest tests/test_fw08_f2_independent_anchors_v1.py -q
"""
from __future__ import annotations

import dataclasses
import inspect

import pytest

import design.hermes_fw08_producer_registry_v1 as pr
import design.hermes_fw08_image_identity_anchor_v1 as iia
import design.hermes_fw08_image_filesystem_anchor_v1 as ifa
import design.hermes_fw08_anchor_bundle_v1 as anb
import design.hermes_fw08_fixture_classification_v1 as fc
import design.hermes_fw08_active_import_evidence_v1 as ai
import tools.hermes_stage_b_build_v1 as w
import tests.test_fw08_governed_build_v1 as gb


# ============================================================================ isolated typed fixtures
REG_POLICY = "fw08-f2-registry-policy-v1"
BUILD_PID = "governed-build-result-producer-v1"
OCI_PID = "oci-image-inspection-producer-v1"
AI_PID = "active-import-analysis-producer-v1"
APPROVER = "HELM-external-approver"

SRC = "a" * 40
CAND = f"fw08cand@{SRC}"
IMG = "sha256:" + "b" * 64
FS = "sha256:" + "c" * 64
MANIFEST = "sha256:" + "d" * 64
CONFIG = "sha256:" + "e" * 64
LAYER = "sha256:" + "f" * 64
NOW = "2026-07-21T00:00:00+00:00"
UTC = "2026-07-21T00:00:00+00:00"          # build / inspection / generated timestamps (fresh vs NOW)
P2_PREFIX = "utils/hermes_sss_"
P2_SUFFIX = "_v1.py"


def _reg(producer_id, producer_type, evidence_types, gate_ids, *, real=False, approval=APPROVER,
         approver=APPROVER, created_by="fw08-f2-setup", origin="GOVERNED_SETUP",
         expiry="2027-07-21T00:00:00+00:00", **over):
    kw = dict(
        registry_policy_version=REG_POLICY, producer_id=producer_id, producer_type=producer_type,
        application="hermes", allowed_evidence_types=tuple(evidence_types), allowed_gate_ids=tuple(gate_ids),
        tool_identity=f"{producer_id}-tool", tool_version="1", tool_digest="t" * 64, source_version=SRC,
        candidate_binding_policy="EXACT", image_binding_policy="EXACT", authority_scope="STAGE_B",
        creation_utc="2026-07-20T00:00:00+00:00", expiry_utc=expiry, permanent_policy_rationale="",
        approval_authority=approval, audit_reference="AUDIT-F2", real_evidence_authority=real,
        created_by=created_by, origin=origin,
    )
    kw.update(over)
    return pr.new_registration(**kw)


def _registry(*, real=False, freeze=True, include_oci=True):
    reg = pr.ProducerRegistry(registry_policy_version=REG_POLICY)
    assert reg.register(_reg(BUILD_PID, "GOVERNED_BUILD_RESULT", ["BUILD_RESULT"], ["STAGE_B_BUILD_RESULT"],
                             real=real), approver_authority=APPROVER) == ()
    if include_oci:
        assert reg.register(_reg(OCI_PID, "OCI_IMAGE_INSPECTION", ["OCI_INSPECTION"],
                                 ["STAGE_B_OCI_INSPECTION"], real=real), approver_authority=APPROVER) == ()
    assert reg.register(_reg(AI_PID, "ACTIVE_IMPORT_ANALYSIS", ["ACTIVE_IMPORT"], ["STAGE_B_ACTIVE_IMPORT"],
                             real=real), approver_authority=APPROVER) == ()
    if freeze:
        reg.freeze()
    return reg


def _build_result(*, image_id=IMG, candidate_id=CAND, source_sha=SRC, build_utc=UTC,
                  evidence_ref="build-result-evidence-1", **over):
    d = dict(image_id=image_id, candidate_id=candidate_id, source_sha=source_sha, build_utc=build_utc,
             evidence_ref=evidence_ref)
    d.update(over)
    return d


def _oci_inspection(*, image_id=IMG, candidate_id=CAND, source_sha=SRC, inspection_utc=UTC,
                    generated_utc=UTC, manifest_digest=MANIFEST, config_digest=CONFIG,
                    filesystem_digest=FS, evidence_ref="oci-inspection-evidence-1", **over):
    d = dict(image_id=image_id, candidate_id=candidate_id, source_sha=source_sha,
             inspection_utc=inspection_utc, generated_utc=generated_utc, manifest_digest=manifest_digest,
             config_digest=config_digest, filesystem_digest=filesystem_digest, layer_digests=[LAYER],
             filesystem_correlation=True, tool_identity="oci-inspect-tool", tool_version="1",
             extraction_method="OCI_EXPORT", tool_digest="t" * 64, evidence_ref=evidence_ref)
    d.update(over)
    return d


def _p2_modules():
    return [f"{P2_PREFIX}{n}{P2_SUFFIX}" for n in range(10)]


def _ai_evidence(*, candidate_id=CAND, image_id=IMG, source_sha=SRC, fs=FS, producer=AI_PID,
                 generated_utc=NOW, **over):
    edges = {"main.py": ["config.py"], "config.py": []}
    modules = ["main.py", "config.py"] + _p2_modules()
    e = {
        "contract_version": "1", "application": "hermes", "candidate_id": candidate_id, "image_id": image_id,
        "source_sha": source_sha, "image_filesystem_digest": fs, "entrypoint": "/app/docker/entrypoint.sh",
        "command": "python main.py", "analysis_method": "STATIC_IMPORT_GRAPH",
        "analysis_tool": "hermes_img_import_scan", "analysis_tool_version": "1", "tool_digest": "t" * 64,
        "evidence_method": "STATIC_IMPORT_GRAPH", "entrypoint_module": "main.py", "modules": modules,
        "import_edges": edges, "runner_registrations": [], "callback_registrations": [],
        "dynamic_imports": [], "activation_env_defaults": {"HERMES_SSS_SHADOW_ENABLED": "0"},
        "plugin_discovery": [], "producer_trust_reference": producer, "result": "INERT",
        "generated_utc": generated_utc,
    }
    e.update(over)
    if "import_graph_checksum" not in over:
        e["import_graph_checksum"] = ai.import_graph_checksum(e["import_edges"])
    if "module_inventory_checksum" not in over:
        e["module_inventory_checksum"] = ai.module_inventory_checksum(e["modules"])
    return e


def _identity(reg, **over):
    return iia.build_image_identity_anchor(
        build_result=_build_result(**over.pop("build_result", {})),
        oci_inspection=_oci_inspection(**over.pop("oci_inspection", {})),
        candidate_id=over.pop("candidate_id", CAND), source_sha=over.pop("source_sha", SRC),
        producer_registry=reg, build_producer_id=over.pop("build_producer_id", BUILD_PID),
        oci_producer_id=over.pop("oci_producer_id", OCI_PID), now_utc=over.pop("now_utc", NOW), **over)


def _filesystem(reg, identity_anchor, **over):
    return ifa.build_image_filesystem_anchor(
        oci_inspection=_oci_inspection(**over.pop("oci_inspection", {})),
        image_identity_anchor=identity_anchor, producer_registry=reg,
        oci_producer_id=over.pop("oci_producer_id", OCI_PID), now_utc=over.pop("now_utc", NOW), **over)


def _make_bundle(reg):
    ia, ra = _identity(reg)
    assert ia is not None, ra
    fa, rf = _filesystem(reg, ia)
    assert fa is not None, rf
    b, rb = anb.assemble_anchor_bundle(
        image_identity_anchor=ia, image_filesystem_anchor=fa, producer_registry=reg,
        build_producer_id=BUILD_PID, oci_producer_id=OCI_PID, active_import_producer_id=AI_PID, now_utc=NOW)
    assert b is not None, rb
    return b, ia, fa


# ================================================================================ §7 image identity anchor
def test_identity_build_inspect_agree_accepted():
    reg = _registry()
    a, r = _identity(reg)
    assert a is not None and r == (), r
    assert a.image_id == IMG and a.lifecycle_state == "ANCHORED"
    assert a.image_id_algorithm == "sha256" and a.image_id_format == "OCI_IMAGE_ID_SHA256"


def test_identity_build_inspect_mismatch_rejected():
    reg = _registry()
    a, r = _identity(reg, oci_inspection={"image_id": "sha256:" + "9" * 64})
    assert a is None and "II-BUILD-INSPECT-IMAGE-ID-MISMATCH" in r


def test_identity_tag_or_label_image_id_rejected():
    reg = _registry()
    a, r = _identity(reg, build_result={"image_id": "fw08cand:latest"},
                     oci_inspection={"image_id": "fw08cand:latest"})
    assert a is None and "II-IMAGE-ID-IS-TAG-OR-LABEL" in r


def test_identity_missing_image_id_rejected():
    reg = _registry()
    a, r = _identity(reg, build_result={"image_id": ""})
    assert a is None and "II-IMAGE-ID-MISSING" in r


def test_identity_unauthorised_build_producer_rejected():
    reg = _registry()
    a, r = _identity(reg, build_producer_id="not-a-producer")
    assert a is None and "II-BUILD-PRODUCER-UNAUTHORISED" in r


def test_identity_has_no_expected_image_id_param():
    """STRUCTURAL: there is no `expected_image_id` (or any active-import) parameter; the image id can ONLY
    come from build_result + oci_inspection."""
    params = set(inspect.signature(iia.build_image_identity_anchor).parameters)
    assert "expected_image_id" not in params
    assert not any("active_import" in p or "evidence" in p and p not in ("build_result", "oci_inspection")
                   for p in params)
    assert "build_result" in params and "oci_inspection" in params
    # the anchor's image id is exactly the build-result image id (derived, not caller-supplied).
    reg = _registry()
    a, _ = _identity(reg)
    assert a.image_id == _build_result()["image_id"]


# ============================================================================= §8 image filesystem anchor
def test_filesystem_anchor_accepted():
    reg = _registry()
    ia, _ = _identity(reg)
    fa, r = _filesystem(reg, ia)
    assert fa is not None and r == (), r
    assert fa.filesystem_digest == FS and fa.manifest_digest == MANIFEST
    assert fa.digest_algorithms == ("sha256",)


def test_filesystem_wrong_digest_rejected():
    reg = _registry()
    ia, _ = _identity(reg)
    fa, r = _filesystem(reg, ia, oci_inspection={"filesystem_digest": "not-a-sha256"})
    assert fa is None and "IF-DIGEST-FORMAT" in r


def test_filesystem_image_mismatch_rejected():
    reg = _registry()
    ia, _ = _identity(reg)
    fa, r = _filesystem(reg, ia, oci_inspection={"image_id": "sha256:" + "9" * 64})
    assert fa is None and "IF-IMAGE-MISMATCH" in r


def test_filesystem_missing_oci_producer_rejected():
    reg = _registry()
    ia, _ = _identity(reg)
    fa, r = _filesystem(reg, ia, oci_producer_id="nope")
    assert fa is None and "IF-OCI-PRODUCER-UNAUTHORISED" in r


def test_filesystem_stale_rejected():
    reg = _registry()
    ia, _ = _identity(reg)
    fa, r = _filesystem(reg, ia, oci_inspection={"generated_utc": "2026-07-01T00:00:00+00:00"})
    assert fa is None and "IF-STALE" in r


def test_filesystem_correlation_failed_rejected():
    reg = _registry()
    ia, _ = _identity(reg)
    fa, r = _filesystem(reg, ia, oci_inspection={"filesystem_correlation": False})
    assert fa is None and "IF-CORRELATION-FAILED" in r


def test_filesystem_signature_has_no_active_import_param():
    """STRUCTURAL: the filesystem digest cannot be self-supplied — there is no active-import / expected-digest
    parameter on the builder."""
    params = set(inspect.signature(ifa.build_image_filesystem_anchor).parameters)
    assert "oci_inspection" in params
    for banned in ("active_import", "active_import_evidence", "expected_fs_digest", "expected_digest"):
        assert banned not in params


# ==================================================================================== §9 producer registry
def test_registry_class_limited_to_exact_evidence_type():
    reg = _registry()
    # build producer resolves for BUILD_RESULT ...
    r_ok, _ = reg.resolve(producer_id=BUILD_PID, evidence_type="BUILD_RESULT", gate_id="STAGE_B_BUILD_RESULT",
                          application="hermes", now_utc=NOW)
    assert r_ok is not None
    # ... but NOT for OCI evidence.
    r_bad, reasons = reg.resolve(producer_id=BUILD_PID, evidence_type="OCI_INSPECTION",
                                 gate_id="STAGE_B_OCI_INSPECTION", application="hermes", now_utc=NOW)
    assert r_bad is None and "PR-WRONG-EVIDENCE-TYPE" in reasons


def test_registry_unknown_expired_and_wrong_scope_rejected():
    reg = pr.ProducerRegistry(registry_policy_version=REG_POLICY)
    assert reg.register(_reg("expired-prod", "GOVERNED_BUILD_RESULT", ["BUILD_RESULT"],
                             ["STAGE_B_BUILD_RESULT"], expiry="2020-01-01T00:00:00+00:00"),
                        approver_authority=APPROVER) == ()
    reg.freeze()
    _, unk = reg.resolve(producer_id="ghost", evidence_type="BUILD_RESULT", gate_id="STAGE_B_BUILD_RESULT",
                         application="hermes", now_utc=NOW)
    assert "PR-UNKNOWN-PRODUCER" in unk
    _, exp = reg.resolve(producer_id="expired-prod", evidence_type="BUILD_RESULT",
                         gate_id="STAGE_B_BUILD_RESULT", application="hermes", now_utc=NOW)
    assert "PR-PRODUCER-EXPIRED" in exp
    _, gate = reg.resolve(producer_id="expired-prod", evidence_type="BUILD_RESULT",
                          gate_id="STAGE_B_OCI_INSPECTION", application="hermes", now_utc=NOW)
    assert "PR-WRONG-GATE" in gate
    _, app = reg.resolve(producer_id="expired-prod", evidence_type="BUILD_RESULT",
                         gate_id="STAGE_B_BUILD_RESULT", application="argus", now_utc=NOW)
    assert "PR-APPLICATION-MISMATCH" in app


def test_registry_self_registration_rejected():
    reg = pr.ProducerRegistry(registry_policy_version=REG_POLICY)
    # producer approves itself.
    reasons = reg.register(_reg(BUILD_PID, "GOVERNED_BUILD_RESULT", ["BUILD_RESULT"],
                                ["STAGE_B_BUILD_RESULT"], approval=BUILD_PID), approver_authority=APPROVER)
    assert "PR-SELF-REGISTRATION" in reasons
    # active-import producer that created itself.
    reasons2 = reg.register(_reg(AI_PID, "ACTIVE_IMPORT_ANALYSIS", ["ACTIVE_IMPORT"],
                                 ["STAGE_B_ACTIVE_IMPORT"], created_by=AI_PID), approver_authority=APPROVER)
    assert "PR-SELF-REGISTRATION" in reasons2


def test_registry_inline_from_evidence_rejected():
    reg = pr.ProducerRegistry(registry_policy_version=REG_POLICY)
    reasons = reg.register(_reg(BUILD_PID, "GOVERNED_BUILD_RESULT", ["BUILD_RESULT"],
                                ["STAGE_B_BUILD_RESULT"], origin=pr.INLINE_ORIGIN),
                           approver_authority=APPROVER)
    assert "PR-INLINE-REGISTRY" in reasons


def test_registry_checksum_tamper_rejected():
    reg = pr.ProducerRegistry(registry_policy_version=REG_POLICY)
    good = _reg(BUILD_PID, "GOVERNED_BUILD_RESULT", ["BUILD_RESULT"], ["STAGE_B_BUILD_RESULT"])
    tampered = dataclasses.replace(good, application="argus")  # checksum no longer matches
    assert "PR-REGISTRY-CHECKSUM-TAMPER" in reg.register(tampered, approver_authority=APPROVER)
    # tamper detected at resolve time too (registered good, then swapped underneath).
    assert reg.register(good, approver_authority=APPROVER) == ()
    reg._by_id[BUILD_PID] = dataclasses.replace(reg._by_id[BUILD_PID], tool_version="9")
    _, reasons = reg.resolve(producer_id=BUILD_PID, evidence_type="BUILD_RESULT",
                             gate_id="STAGE_B_BUILD_RESULT", application="hermes", now_utc=NOW)
    assert "PR-REGISTRY-CHECKSUM-TAMPER" in reasons


def test_registry_wildcard_rejected():
    reg = pr.ProducerRegistry(registry_policy_version=REG_POLICY)
    ev = reg.register(_reg(BUILD_PID, "GOVERNED_BUILD_RESULT", ["*"], ["STAGE_B_BUILD_RESULT"]),
                      approver_authority=APPROVER)
    assert "PR-WILDCARD-EVIDENCE-TYPE" in ev
    ga = reg.register(_reg(BUILD_PID, "GOVERNED_BUILD_RESULT", ["BUILD_RESULT"], ["?"]),
                      approver_authority=APPROVER)
    assert "PR-WILDCARD-GATE" in ga


def test_registry_frozen_mutation_rejected():
    reg = _registry()  # already frozen
    reasons = reg.register(_reg("late-prod", "GOVERNED_BUILD_RESULT", ["BUILD_RESULT"],
                                ["STAGE_B_BUILD_RESULT"]), approver_authority=APPROVER)
    assert "PR-REGISTRY-FROZEN" in reasons


def test_registry_approval_missing_rejected():
    reg = pr.ProducerRegistry(registry_policy_version=REG_POLICY)
    reasons = reg.register(_reg(BUILD_PID, "GOVERNED_BUILD_RESULT", ["BUILD_RESULT"],
                                ["STAGE_B_BUILD_RESULT"], approval=""), approver_authority="")
    assert "PR-APPROVAL-MISSING" in reasons


def test_registry_resolve_consults_only_registry_not_evidence():
    """STRUCTURAL: resolve() has no `evidence` parameter — it cannot read an evidence record."""
    params = set(inspect.signature(pr.ProducerRegistry.resolve).parameters)
    assert "evidence" not in params
    assert {"producer_id", "evidence_type", "gate_id", "application", "now_utc"} <= params


# ==================================================================================== §10 anchor bundle
def test_bundle_valid_accepted():
    reg = _registry()
    b, ia, fa = _make_bundle(reg)
    assert b.provenance.derived_from_active_import is False
    assert b.image_identity_anchor is ia and b.image_filesystem_anchor is fa
    assert b.candidate_id == CAND and b.source_sha == SRC


@pytest.mark.parametrize("mutate,code", [
    ("candidate", "AB-CANDIDATE-MISMATCH"),
    ("source", "AB-SOURCE-MISMATCH"),
    ("image", "AB-IMAGE-MISMATCH"),
    ("dup", "AB-DUPLICATE-CONFLICTING-ANCHOR"),
])
def test_bundle_mismatches_rejected(mutate, code):
    reg = _registry()
    ia, _ = _identity(reg)
    fa, _ = _filesystem(reg, ia)
    if mutate == "candidate":
        fa = dataclasses.replace(fa, candidate_id="other@x")
    elif mutate == "source":
        fa = dataclasses.replace(fa, source_sha="e" * 40)
    elif mutate == "image":
        fa = dataclasses.replace(fa, image_id="sha256:" + "9" * 64)
    elif mutate == "dup":
        fa = dataclasses.replace(fa, evidence_ref="oci-inspection-evidence-2")  # divergent OCI source
    b, r = anb.assemble_anchor_bundle(
        image_identity_anchor=ia, image_filesystem_anchor=fa, producer_registry=reg,
        build_producer_id=BUILD_PID, oci_producer_id=OCI_PID, active_import_producer_id=AI_PID, now_utc=NOW)
    assert b is None and code in r, r


def test_bundle_missing_anchor_wrong_type_rejected():
    """STRUCTURAL: passing the evidence Mapping (or any non-anchor) where a typed anchor is expected is
    rejected — this makes 'assemble a bundle out of active-import fields' impossible."""
    reg = _registry()
    ia, _ = _identity(reg)
    b, r = anb.assemble_anchor_bundle(
        image_identity_anchor=ia, image_filesystem_anchor=_ai_evidence(), producer_registry=reg,
        build_producer_id=BUILD_PID, oci_producer_id=OCI_PID, active_import_producer_id=AI_PID, now_utc=NOW)
    assert b is None and r == ("AB-ANCHOR-WRONG-TYPE",)


def test_bundle_cycle_rejected():
    reg = _registry()
    ia, _ = _identity(reg)
    fa, _ = _filesystem(reg, ia)
    fa_loop = dataclasses.replace(fa, evidence_ref=ia.evidence_checksum)  # fs derived from the identity anchor
    b, r = anb.assemble_anchor_bundle(
        image_identity_anchor=ia, image_filesystem_anchor=fa_loop, producer_registry=reg,
        build_producer_id=BUILD_PID, oci_producer_id=OCI_PID, active_import_producer_id=AI_PID, now_utc=NOW)
    assert b is None and "AB-ANCHOR-CYCLE" in r


def test_bundle_derived_from_active_import_rejected():
    reg = _registry()
    ia, _ = _identity(reg, build_result={"evidence_ref": "active_import-tainted-build-ref"})
    fa, _ = _filesystem(reg, ia)
    b, r = anb.assemble_anchor_bundle(
        image_identity_anchor=ia, image_filesystem_anchor=fa, producer_registry=reg,
        build_producer_id=BUILD_PID, oci_producer_id=OCI_PID, active_import_producer_id=AI_PID, now_utc=NOW)
    assert b is None and "AB-BUNDLE-SELF-DERIVED" in r


def test_bundle_producer_unregistered_rejected():
    reg = _registry()
    ia, _ = _identity(reg)
    fa, _ = _filesystem(reg, ia)
    b, r = anb.assemble_anchor_bundle(
        image_identity_anchor=ia, image_filesystem_anchor=fa, producer_registry=reg,
        build_producer_id=BUILD_PID, oci_producer_id=OCI_PID, active_import_producer_id="ghost", now_utc=NOW)
    assert b is None and "AB-PRODUCER-UNREGISTERED" in r


def test_bundle_has_no_active_import_evidence_param():
    """STRUCTURAL: assemble_anchor_bundle takes typed anchors + registry + producer ids only."""
    params = set(inspect.signature(anb.assemble_anchor_bundle).parameters)
    for banned in ("active_import_evidence", "evidence", "ai_evidence"):
        assert banned not in params
    assert {"image_identity_anchor", "image_filesystem_anchor", "producer_registry"} <= params


# ============================================================================ §11 validator against bundle
def test_validator_valid_accepted_and_inert():
    reg = _registry()
    b, _, _ = _make_bundle(reg)
    v = ai.validate_active_import_against_bundle(
        _ai_evidence(), anchor_bundle=b, producer_registry=reg, now_utc=NOW,
        phase2_prefix=P2_PREFIX, phase2_suffix=P2_SUFFIX, required_phase2_count=10)
    assert v.accepted and v.inert, v.reason_codes


def test_validator_evidence_mapping_as_bundle_rejected():
    reg = _registry()
    v = ai.validate_active_import_against_bundle(
        _ai_evidence(), anchor_bundle=_ai_evidence(), producer_registry=reg, now_utc=NOW,
        phase2_prefix=P2_PREFIX, phase2_suffix=P2_SUFFIX, required_phase2_count=10)
    assert not v.accepted and "AI2-BUNDLE-NOT-INDEPENDENT" in v.reason_codes


def test_validator_inline_dict_registry_rejected():
    reg = _registry()
    b, _, _ = _make_bundle(reg)
    v = ai.validate_active_import_against_bundle(
        _ai_evidence(), anchor_bundle=b, producer_registry={"inline": "dict"}, now_utc=NOW,
        phase2_prefix=P2_PREFIX, phase2_suffix=P2_SUFFIX, required_phase2_count=10)
    assert not v.accepted and "AI2-REGISTRY-NOT-GOVERNED" in v.reason_codes


def test_validator_evidence_supplies_expected_rejected():
    reg = _registry()
    b, _, _ = _make_bundle(reg)
    v = ai.validate_active_import_against_bundle(
        _ai_evidence(expected_fs_digest=FS), anchor_bundle=b, producer_registry=reg, now_utc=NOW,
        phase2_prefix=P2_PREFIX, phase2_suffix=P2_SUFFIX, required_phase2_count=10)
    assert not v.accepted and "AI2-EVIDENCE-SUPPLIES-EXPECTED" in v.reason_codes


def test_validator_producer_not_independent_rejected():
    reg = _registry()
    b, _, _ = _make_bundle(reg)
    # forge a bundle whose build producer id equals the active-import producer id.
    prov = dataclasses.replace(b.provenance, build_producer_id=AI_PID)
    b2 = dataclasses.replace(b, provenance=prov)
    v = ai.validate_active_import_against_bundle(
        _ai_evidence(), anchor_bundle=b2, producer_registry=reg, now_utc=NOW,
        phase2_prefix=P2_PREFIX, phase2_suffix=P2_SUFFIX, required_phase2_count=10)
    assert not v.accepted and "AI2-PRODUCER-NOT-INDEPENDENT" in v.reason_codes


def test_validator_bundle_self_derived_rejected():
    reg = _registry()
    b, _, _ = _make_bundle(reg)
    prov = dataclasses.replace(b.provenance, derived_from_active_import=True)
    b2 = dataclasses.replace(b, provenance=prov)
    v = ai.validate_active_import_against_bundle(
        _ai_evidence(), anchor_bundle=b2, producer_registry=reg, now_utc=NOW,
        phase2_prefix=P2_PREFIX, phase2_suffix=P2_SUFFIX, required_phase2_count=10)
    assert not v.accepted and "AI2-BUNDLE-SELF-DERIVED" in v.reason_codes


def test_validator_producer_unauthorised_rejected():
    reg = _registry()
    b, _, _ = _make_bundle(reg)
    v = ai.validate_active_import_against_bundle(
        _ai_evidence(producer="unregistered-producer"), anchor_bundle=b, producer_registry=reg, now_utc=NOW,
        phase2_prefix=P2_PREFIX, phase2_suffix=P2_SUFFIX, required_phase2_count=10)
    assert not v.accepted and "AI2-PRODUCER-UNAUTHORISED" in v.reason_codes


def test_validator_tautology_copy_digest_rejected():
    """THE F-2 tautology: the evidence copies its OWN filesystem digest into an expected field. Rejected as a
    structural guard (an expected-authority field on the evidence), NOT by a value comparison."""
    reg = _registry()
    b, _, _ = _make_bundle(reg)
    e = _ai_evidence()
    e["expected_fs_digest"] = e["image_filesystem_digest"]   # the record repeats its own value
    v = ai.validate_active_import_against_bundle(
        e, anchor_bundle=b, producer_registry=reg, now_utc=NOW,
        phase2_prefix=P2_PREFIX, phase2_suffix=P2_SUFFIX, required_phase2_count=10)
    assert not v.accepted and "AI2-EVIDENCE-SUPPLIES-EXPECTED" in v.reason_codes


def test_validator_same_object_as_anchor_rejected():
    reg = _registry()
    b, ia, _ = _make_bundle(reg)
    v = ai.validate_active_import_against_bundle(
        ia, anchor_bundle=b, producer_registry=reg, now_utc=NOW,
        phase2_prefix=P2_PREFIX, phase2_suffix=P2_SUFFIX, required_phase2_count=10)
    assert not v.accepted and "AI2-EVIDENCE-IS-ANCHOR" in v.reason_codes
    v2 = ai.validate_active_import_against_bundle(
        b, anchor_bundle=b, producer_registry=reg, now_utc=NOW,
        phase2_prefix=P2_PREFIX, phase2_suffix=P2_SUFFIX, required_phase2_count=10)
    assert not v2.accepted and "AI2-EVIDENCE-IS-ANCHOR" in v2.reason_codes


def test_validator_digest_mismatch_still_rejected_via_bundle():
    """Even with all structural guards passed, if the evidence's own fs digest does not equal the bundle's
    INDEPENDENT digest, the delegated pure comparator rejects (IMG-FS-DIGEST-MISMATCH)."""
    reg = _registry()
    b, _, _ = _make_bundle(reg)
    v = ai.validate_active_import_against_bundle(
        _ai_evidence(fs="sha256:" + "9" * 64), anchor_bundle=b, producer_registry=reg, now_utc=NOW,
        phase2_prefix=P2_PREFIX, phase2_suffix=P2_SUFFIX, required_phase2_count=10)
    assert not v.accepted and "IMG-FS-DIGEST-MISMATCH" in v.reason_codes


# ============================================================================= §13 tautology attack matrix
def test_attack_no_builder_accepts_active_import_as_an_anchor_input():
    """STRUCTURAL sweep: NONE of the anchor/bundle builders accept an active-import evidence record as an
    input — none has a parameter that could carry it."""
    for fn in (iia.build_image_identity_anchor, ifa.build_image_filesystem_anchor, anb.assemble_anchor_bundle):
        params = set(inspect.signature(fn).parameters)
        for banned in ("active_import_evidence", "ai_evidence", "expected_image_id", "expected_fs_digest"):
            assert banned not in params, (fn.__name__, banned)


def test_attack_expected_image_id_cannot_be_caller_supplied():
    """STRUCTURAL: there is no way to inject an expected image id — it is derived from build+inspect."""
    assert "expected_image_id" not in inspect.signature(iia.build_image_identity_anchor).parameters


def test_attack_inline_registry_forged_from_evidence_rejected():
    """A registry inlined from the evidence (origin INLINE_FROM_EVIDENCE) can never grant authority."""
    reg = pr.ProducerRegistry(registry_policy_version=REG_POLICY)
    reasons = reg.register(_reg(AI_PID, "ACTIVE_IMPORT_ANALYSIS", ["ACTIVE_IMPORT"],
                                ["STAGE_B_ACTIVE_IMPORT"], origin=pr.INLINE_ORIGIN),
                           approver_authority=APPROVER)
    assert "PR-INLINE-REGISTRY" in reasons


def test_attack_frozen_registry_cannot_be_mutated_by_evidence():
    """STRUCTURAL: a frozen registry rejects any late registration — evidence cannot add its own producer."""
    reg = _registry()
    assert reg.frozen is True
    assert "PR-REGISTRY-FROZEN" in reg.register(
        _reg("evil", "ACTIVE_IMPORT_ANALYSIS", ["ACTIVE_IMPORT"], ["STAGE_B_ACTIVE_IMPORT"]),
        approver_authority=APPROVER)


# ==================================================================================== §16 fixture class
def test_fixture_synthetic_producer_not_real():
    reg = _registry(real=False)
    got, _ = reg.resolve(producer_id=AI_PID, evidence_type="ACTIVE_IMPORT", gate_id="STAGE_B_ACTIVE_IMPORT",
                         application="hermes", now_utc=NOW)
    assert got is not None
    assert fc.is_real_candidate(got) is False


def test_fixture_flipping_classification_insufficient():
    """Flipping evidence['classification'] to REAL_CANDIDATE_EVIDENCE does NOT make it real — realness is
    governed by the producer registration, not the evidence's declared string."""
    reg = _registry(real=False)
    got, _ = reg.resolve(producer_id=AI_PID, evidence_type="ACTIVE_IMPORT", gate_id="STAGE_B_ACTIVE_IMPORT",
                         application="hermes", now_utc=NOW)
    ev = _ai_evidence()
    ev["classification"] = fc.REAL_CLASS
    ev["fixture_class"] = fc.REAL_CLASS
    ok, reasons = fc.require_real_candidate_evidence(ev, resolved_producer_registration=got)
    assert ok is False and "FC-NOT-REAL-CANDIDATE" in reasons
    # a genuinely real (governed) registration WOULD pass — proving the authority is the registration.
    real_reg = _registry(real=True)
    real_got, _ = real_reg.resolve(producer_id=AI_PID, evidence_type="ACTIVE_IMPORT",
                                   gate_id="STAGE_B_ACTIVE_IMPORT", application="hermes", now_utc=NOW)
    ok2, _ = fc.require_real_candidate_evidence(_ai_evidence(), resolved_producer_registration=real_got)
    assert ok2 is True


# ==================================================================================== §19 wrapper single flow
class WrapperRunner(gb.FakeRunner):
    """A fake runner that ALSO exposes the F-2 independent producer artifacts + a governed registry. Never
    touches Docker (inherits gb.FakeRunner's fixture payloads)."""

    def __init__(self, sha, candidate_id, *, now, self_copy=False, **kw):
        super().__init__(sha, **kw)
        img = self.image_id
        self.producer_registry = _registry()
        self.build_producer_id = BUILD_PID
        self.oci_producer_id = OCI_PID
        self.active_import_producer_id = AI_PID
        self.build_result_evidence = _build_result(image_id=img, candidate_id=candidate_id, source_sha=sha,
                                                    build_utc=now)
        self.oci_inspection_evidence = _oci_inspection(image_id=img, candidate_id=candidate_id,
                                                       source_sha=sha, inspection_utc=now, generated_utc=now)
        over = {}
        if self_copy:
            over["expected_fs_digest"] = FS   # the record repeats its own digest as an expected field
        self._ai = _ai_evidence(candidate_id=candidate_id, image_id=img, source_sha=sha, generated_utc=now,
                                **over)

    def active_import_evidence(self, image_id, *, source_sha):
        return self._ai


def _guard_no_docker(monkeypatch):
    import subprocess
    real_run, real_popen = subprocess.run, subprocess.Popen

    def guard_run(cmd, *a, **k):
        assert not (cmd and "docker" in str(cmd[0])), "no real docker run"
        return real_run(cmd, *a, **k)

    def guard_popen(cmd, *a, **k):
        assert not (cmd and "docker" in str(cmd[0])), "no real docker popen"
        return real_popen(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", guard_run)
    monkeypatch.setattr(subprocess, "Popen", guard_popen)


def test_wrapper_single_flow_candidate_ready(monkeypatch, tmp_path):
    _guard_no_docker(monkeypatch)
    root = tmp_path / "gov"
    root.mkdir()
    sha = gb._make_governed_repo(root)
    candidate_id = w.make_candidate_id("fw08cand", sha)
    runner = WrapperRunner(sha, candidate_id, now=gb.NOW)
    rep = gb._run(root, sha, runner)
    assert rep.ready is True and rep.state == "CANDIDATE_READY", rep.reason_codes
    # the fake runner did the build — no real docker.
    assert runner.calls[0][0] == "build"
    # the default RefusingDockerRunner would have refused; we used the fake one.
    assert isinstance(runner, w.DockerRunner) and not isinstance(runner, w.RefusingDockerRunner)


def test_wrapper_self_copied_digest_not_accepted(monkeypatch, tmp_path):
    _guard_no_docker(monkeypatch)
    root = tmp_path / "gov2"
    root.mkdir()
    sha = gb._make_governed_repo(root)
    candidate_id = w.make_candidate_id("fw08cand", sha)
    runner = WrapperRunner(sha, candidate_id, now=gb.NOW, self_copy=True)
    rep = gb._run(root, sha, runner)
    assert rep.ready is False and rep.state == "REJECTED"
    assert any("IMAGE-BOUND-IMPORT-FAILED" in r and "AI2-EVIDENCE-SUPPLIES-EXPECTED" in r
               for r in rep.reason_codes), rep.reason_codes


def test_wrapper_no_active_import_artifacts_inert_unchanged(monkeypatch, tmp_path):
    """A runner with NO active-import artifacts leaves the flow unchanged (inert) — the F-2 path is skipped
    exactly as before, preserving every existing wrapper test."""
    _guard_no_docker(monkeypatch)
    root = tmp_path / "gov3"
    root.mkdir()
    sha = gb._make_governed_repo(root)
    rep = gb._run(root, sha, gb.FakeRunner(sha))
    assert rep.ready is True and rep.state == "CANDIDATE_READY", rep.reason_codes


# ==================================================================================== regression smoke
def test_regression_validate_image_bound_active_import_smoke():
    """The existing PURE comparator is unchanged: a valid image-bound record still accepts, a tampered one
    still rejects."""
    kw = dict(expected_image_id=IMG, expected_source_sha=SRC, expected_fs_digest=FS,
              expected_candidate_id=CAND, trusted_producer_refs=(AI_PID,), now_utc=NOW,
              phase2_prefix=P2_PREFIX, phase2_suffix=P2_SUFFIX, required_phase2_count=10)
    v = ai.validate_image_bound_active_import(_ai_evidence(), **kw)
    assert v.accepted and v.inert, v.reason_codes
    v2 = ai.validate_image_bound_active_import(_ai_evidence(image_id="sha256:" + "9" * 64), **kw)
    assert not v2.accepted and "IMG-IMAGE-ID-MISMATCH" in v2.reason_codes
