#!/usr/bin/env python3
"""Design-validation test for the FW-08 F2-R3 real-proof controlled-lifecycle DESIGN artifacts.

WO-HELM-HERMES-FW08-F2-R3-REAL-PROOF-LIFECYCLE-DESIGN. Contract version: 1.

PURE / ADDITIVE / DESIGN-ONLY. This test imports ONLY `json` (stdlib) + `os`/`re` for path + pattern work.
It asserts the DESIGN JSON contract and the design markdown are COMPLETE and CONSISTENT. It executes NOTHING,
authorises NOTHING, configures no authority, builds no image, and touches no runtime. It does not import any
FW-08 runtime/design module.
"""
import json
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_DESIGN_DIR = os.path.join(_ROOT, "docs", "design", "fw08", "f2_r3_real_proof_lifecycle")
_JSON_PATH = os.path.join(_DESIGN_DIR, "f2_r3_real_proof_lifecycle.v1.json")
_MD_PATH = os.path.join(_DESIGN_DIR, "LIFECYCLE_DESIGN.md")

_CANONICAL_BASE = "7c45d7a3ef73a1f60fe66d035d2ba7188be5ca66"
_R2D2_AUDIT_LANE = "R2D2"

_STAGE_KEYS = (
    "stage_number", "name", "owner", "mutation_authority", "required_input",
    "produced_evidence", "required_approval", "success_verdict", "fail_closed_verdict",
    "prohibited_side_effects", "rollback_or_containment", "next_gate",
)

_EXPECTED_LANES = {
    "Chief Architect", "HELM", "R2D2", "build producer", "OCI producer",
}

_EXPECTED_PROTECTED = {
    "source": "71ea3bd4598d8af5628f78de10f8022088ad3f05",
    "image": "c5fc2a62f424",
    "container": "d80018037b7f",
    "restarts": 0,
    "runners": 9,
    "config_version": "v3",
    "consumer_live": False,
}


def _load_json():
    with open(_JSON_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_md_text():
    with open(_MD_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


def _load_json_text():
    with open(_JSON_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------------------- top-level identity
def test_json_loads_and_identity():
    doc = _load_json()
    assert doc["contract_version"] == "1"
    assert doc["classification"] == "F2_R3_REAL_PROOF_EXECUTION_CONTROLLED_LIFECYCLE_DESIGN"
    assert doc["canonical_base"] == _CANONICAL_BASE


def test_flags_all_false_except_design_only():
    flags = _load_json()["flags"]
    for key in (
        "real_execution_authorised", "image_build_authorised", "authority_configured",
        "runtime_mutation", "redis", "sql", "secrets_present",
    ):
        assert flags[key] is False, key
    assert flags["design_only"] is True


# --------------------------------------------------------------------------- protected runtime
def test_protected_runtime_exact():
    pr = _load_json()["protected_runtime"]
    assert pr == _EXPECTED_PROTECTED


# --------------------------------------------------------------------------- 22 lifecycle stages
def test_lifecycle_stages_exactly_22_contiguous_and_complete():
    stages = _load_json()["lifecycle_stages"]
    assert isinstance(stages, list)
    assert len(stages) == 22
    numbers = [s["stage_number"] for s in stages]
    assert numbers == list(range(1, 23)), numbers
    for s in stages:
        for k in _STAGE_KEYS:
            assert k in s, (s["stage_number"], k)
            val = s[k]
            assert val not in (None, "", [], {}), (s["stage_number"], k)
        assert isinstance(s["prohibited_side_effects"], list)
        assert len(s["prohibited_side_effects"]) >= 1


def test_no_stage_mutator_is_the_r2d2_audit_lane():
    """No agent approves/audits its own mutation: the R2D2 audit lane owns ONLY audit stage(s); every
    non-R2D2 mutation stage has a non-R2D2 owner."""
    stages = _load_json()["lifecycle_stages"]
    r2d2_stages = [s for s in stages if s["owner"] == _R2D2_AUDIT_LANE]
    # R2D2 owns at least the independent audit stage.
    assert r2d2_stages, "R2D2 must own the independent audit stage"
    for s in r2d2_stages:
        # the R2D2-owned stage must be a read-only audit (its mutation authority must NOT mutate evidence)
        assert "audit" in s["name"].lower()
        assert "READ-ONLY" in s["mutation_authority"] or "read-only" in s["mutation_authority"].lower()
    # Every mutation stage owned by a producer/HELM/CA is NOT owned by R2D2.
    for s in stages:
        if "audit" not in s["name"].lower():
            assert s["owner"] != _R2D2_AUDIT_LANE, s["stage_number"]


# --------------------------------------------------------------------------- operator authority matrix
def test_operator_authority_matrix_five_lanes_and_distinctness():
    doc = _load_json()
    matrix = doc["operator_authority_matrix"]
    lanes = {row["lane"] for row in matrix}
    assert lanes == _EXPECTED_LANES, lanes
    for row in matrix:
        assert row["may"] and row["may_not"]
    # build and OCI producer lanes are distinct.
    assert "build producer" in lanes and "OCI producer" in lanes
    assert "build producer" != "OCI producer"
    dist = doc["build_oci_distinctness_assertion"]
    dims = dist["dimensions"]
    for expected_dim in (
        "execution identity", "registration", "runner",
        "executable boundary", "evidence reference", "provenance",
    ):
        assert expected_dim in dims, expected_dim
    assert len(dims) == 6
    assert dist["no_self_approval"] is True
    assert dist["no_self_audit"] is True


# --------------------------------------------------------------------------- contracts present
def test_six_contracts_present():
    doc = _load_json()
    for key in (
        "real_authority_contract", "build_producer_contract", "oci_producer_contract",
        "evidence_store_resolver_contract", "sbom_vulnerability_contract",
    ):
        assert doc[key], key
    ra = doc["real_authority_contract"]
    assert ra["authority_unavailable_behaviour"] == "FAIL_CLOSED"
    assert ra["no_etc_or_host_global"] is True
    assert set(ra["no_secret_values_in"]) >= {
        "code", "git", "evidence", "memory_fabric", "design_doc"}
    bp = doc["build_producer_contract"]
    assert set(bp["build_producer_must_not"]) >= {"perform_oci_inspection", "verify_proof"}
    op = doc["oci_producer_contract"]
    assert op["oci_producer_must_not_consume_build_payload_as_truth"] is True
    assert op["must_inspect_image_independently_by_digest"] is True
    es = doc["evidence_store_resolver_contract"]
    assert es["no_store_mutation_in_this_wo"] is True
    sv = doc["sbom_vulnerability_contract"]
    assert sv["produced_in_this_wo"] is False


# --------------------------------------------------------------------------- three closures
def test_closures_all_three_present_and_populated():
    closures = _load_json()["closures"]
    for key in ("L_F2R3_NEARCOPY", "L_F2R3_BYPASS_STATIC", "L_F2R3_REFLECTIVE"):
        assert key in closures
        assert closures[key], key
    nc = closures["L_F2R3_NEARCOPY"]
    mandatory = nc["mandatory_independently_inspected_values"]
    for v in ("manifest_digest", "config_digest", "ordered_layer_digests", "filesystem_digest"):
        assert v in mandatory, v
    assert set(nc["may_match_build"]) >= {"image_id", "candidate_id", "source_sha"}
    bs = closures["L_F2R3_BYPASS_STATIC"]
    assert len(bs["approach"]) >= 8
    rf = closures["L_F2R3_REFLECTIVE"]
    assert rf["not_configured_or_created_in_this_wo"] is True


# --------------------------------------------------------------------------- governed dual-role + acceptance
def test_governed_dual_role_policy():
    p = _load_json()["governed_dual_role_policy"]
    assert p["default"] == "DUAL_ROLE_FORBIDDEN"
    assert p["recommended_disposition"] == "PERMANENT_TOMBSTONE_UNLESS_A_CONCRETE_NEED_ARISES"
    assert p["activated_in_this_wo"] is False
    assert p["retained_exception_requirements"]


def test_real_proof_acceptance_matrix():
    m = _load_json()["real_proof_acceptance_matrix"]
    for verdict in ("GREEN", "AMBER", "RED"):
        assert verdict in m["outcomes"]
        assert m["outcomes"][verdict]
    assert m["required_inputs"]
    assert m["no_missing_prerequisite_may_downgrade_to_synthetic"] is True


# --------------------------------------------------------------------------- isolation + evidence
def test_candidate_isolation_requirements():
    reqs = _load_json()["candidate_isolation"]["requirements"]
    joined = " | ".join(reqs).lower()
    for token in ("separate namespace", "no latest tag", "no restart", "deterministic"):
        assert token in joined, token


def test_evidence_audit_sequence_eleven_packages():
    seq = _load_json()["evidence_audit_sequence"]
    assert len(seq) == 11
    for pkg in seq:
        assert pkg["required_contents"]
        assert pkg["no_secrets"] is True


# --------------------------------------------------------------------------- future WO sequence
def test_future_wo_sequence_authorises_no_execution():
    doc = _load_json()
    seq = doc["future_wo_sequence"]
    assert seq
    for entry in seq:
        assert entry["authorises_execution"] is False, entry.get("wo_title")
    note = doc["future_wo_sequence_note"]
    assert "no WO in this sequence authorises real Stage-B execution" in note


def test_next_gate_and_carry_forward():
    doc = _load_json()
    assert "no real Stage-B execution authorised" in doc["next_gate"]
    cf_items = {c["item"] for c in doc["carry_forward"]}
    for item in ("F-1", "F-3", "FW-16", "N-1", "N-2"):
        assert item in cf_items, item
    for c in doc["carry_forward"]:
        assert "OUTSTANDING" in c["status"]


# --------------------------------------------------------------------------- no secrets in either artifact
_SECRET_PATTERNS = (
    re.compile(r"BEGIN [A-Z ]*PRIVATE KEY"),
    re.compile(r"password\s*=\s*\S"),
    re.compile(r"api_key\s*=\s*\S", re.IGNORECASE),
    re.compile(r"\btoken\s*=\s*[A-Za-z0-9]"),
)


def test_no_secret_patterns_in_json_or_md():
    for text in (_load_json_text(), _load_md_text()):
        for pat in _SECRET_PATTERNS:
            m = pat.search(text)
            assert m is None, pat.pattern
