"""Design-only tests for the HERMES recovery-proposal publication contract.
WO-HELM-HERMES-PH2-RECOVERY-PROPOSAL-PUBLICATION-CONTRACT-DESIGN-0001.

These validate the DESIGN artefacts (JSON Schema <-> fixtures, and cross-artefact consistency of fault codes / states).
They exercise NO runtime, NO Redis, NO network. Pure file + schema validation.
"""
import json
import pathlib
import re

import pytest
from jsonschema import Draft7Validator

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas/recovery_proposal/recovery_proposal_publication.v1.schema.json"
FIX = ROOT / "fixtures/recovery_proposal"
DOCS = ROOT / "docs/design/recovery_proposal_publication"


@pytest.fixture(scope="module")
def validator():
    schema = json.loads(SCHEMA.read_text())
    Draft7Validator.check_schema(schema)
    return Draft7Validator(schema)


def _load(name):
    return json.loads((FIX / name).read_text())


# --------------------------------------------------------------------------- schema <-> valid fixtures
@pytest.mark.parametrize("name", ["eligible.json", "stale.json", "revocation_supersession.json", "superseding_gen2.json"])
def test_valid_fixtures_pass_schema(validator, name):
    errs = sorted(validator.iter_errors(_load(name)), key=str)
    assert not errs, f"{name} should validate; errors: {[e.message for e in errs]}"


@pytest.mark.parametrize("name", [
    "invalid_alias.json", "invalid_execution_true.json", "invalid_consumer_live.json",
    "invalid_missing_policy_digest.json", "invalid_reason_code.json", "invalid_extra_field.json",
    "invalid_no_expiry.json", "invalid_proposal_id.json", "invalid_oversized_segments.json",
])
def test_invalid_fixtures_fail_schema(validator, name):
    assert not validator.is_valid(_load(name)), f"{name} MUST be rejected by the schema (fail-closed)"


# --------------------------------------------------------------------------- safety flags are const-locked
def test_schema_locks_execution_and_publication_flags():
    props = json.loads(SCHEMA.read_text())["properties"]["safety"]["properties"]
    assert props["execution_authorised"] == {"const": False}
    assert props["execution_started"] == {"const": False}
    assert props["publication_only"] == {"const": True}
    assert props["executor_bound"] == {"const": False}
    assert props["consumer_live"] == {"const": False}
    assert props["backfill_executed"] == {"const": False}
    assert props["repair_executed"] == {"const": False}


def test_schema_pins_canonical_instrument_and_rejects_alias():
    s = json.loads(SCHEMA.read_text())["properties"]
    assert s["instrument"] == {"const": "XAU_USD"}
    assert s["canonical_instrument"] == {"const": "XAU_USD"}
    # eligible fixture has no XAUUSD anywhere
    assert "XAUUSD" not in (FIX / "eligible.json").read_text()


def test_schema_requires_distinct_utc_timestamps_and_ttl():
    req = set(json.loads(SCHEMA.read_text())["required"])
    for f in ("generated_at_utc", "validated_at_utc", "published_at_utc", "source_as_of_utc", "expires_at_utc"):
        assert f in req, f"envelope must require distinct timestamp {f}"


def test_schema_requires_all_provenance_digests():
    req = set(json.loads(SCHEMA.read_text())["required"])
    for f in ("policy_digest", "gaps_semantic_digest", "coverage_semantic_digest", "closure_digest",
              "planner_version", "policy_version", "gaps_contract_version", "coverage_contract_version"):
        assert f in req


# --------------------------------------------------------------------------- cross-artefact consistency
def _codes_in(path):
    return set(re.findall(r"PUB_[A-Z_]+", path.read_text()))


def test_matrix_refusal_codes_are_catalogued():
    matrix = _codes_in(DOCS / "eligibility_matrix.md")
    catalogue = _codes_in(DOCS / "fault_code_catalogue.md")
    missing = matrix - catalogue
    assert not missing, f"every eligibility-matrix refusal code must be in the fault catalogue; missing: {missing}"


def test_fault_codes_never_collapse_to_generic_invalid():
    catalogue = _codes_in(DOCS / "fault_code_catalogue.md")
    assert "PUB_INVALID" not in catalogue and len(catalogue) >= 25, "refusals must be specific, not a generic INVALID"


def test_state_machine_lists_all_required_states():
    txt = (DOCS / "state_transition_table.md").read_text()
    for st in ("ABSENT", "CANDIDATE", "VALIDATING", "ELIGIBLE", "PUBLISHED", "HELD_CURRENT",
               "SUPERSEDED", "STALE", "REVOKED", "BLOCKED", "FAILED", "EXPIRED", "WITHDRAWN"):
        assert st in txt, f"state machine must define {st}"


def test_schema_status_enum_matches_external_states():
    enum = set(json.loads(SCHEMA.read_text())["properties"]["status"]["enum"])
    assert {"PUBLISHED", "HELD_CURRENT", "SUPERSEDED", "STALE", "REVOKED", "BLOCKED", "FAILED", "EXPIRED", "WITHDRAWN"} == enum


def test_gate_and_ttl_docs_exist_and_fail_closed():
    gate = (DOCS / "gate_truth_table.md").read_text()
    assert "PUB_GATE_MISMATCH" in gate and "SystemExit" in gate and "fail closed" in gate.lower()
    ttl = (DOCS / "ttl_freshness.md").read_text()
    assert "proposal_ttl_seconds" in ttl and "never" in ttl.lower()


def test_no_secret_material_in_fixtures():
    for p in FIX.glob("*.json"):
        if p.name.startswith("invalid_"):
            continue  # invalid_extra_field intentionally carries a token-named field to prove the schema rejects it
        blob = p.read_text().lower()
        for tok in ("password", "secret", "api_key", "apikey", "private key", "authorization"):
            assert tok not in blob, f"{p.name} must contain no secret material ({tok})"


def test_no_cross_application_reference_in_design():
    # Design docs may NAME boundaries in prose (e.g. "imports no code from ARES / Falcon"). Forbid only actual
    # import-statement coupling, not English prose mentioning the boundary.
    import_pat = re.compile(r"(?m)^\s*(?:from\s+(?:ares|falcon|helios|neo|solo|plutus|argus)\b|import\s+(?:ares|falcon|helios)\b)",
                            re.IGNORECASE)
    for p in list(DOCS.glob("*.md")) + [SCHEMA]:
        txt = p.read_text()
        assert not import_pat.search(txt), f"{p.name} must not contain a cross-application import statement"
        low = txt.lower()
        for tok in ("recoverylibrary", "utils.recovery_executor", "utils.recovery_planner"):
            assert tok not in low, f"{p.name} must not couple to {tok}"
