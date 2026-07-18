"""Bounded design-validation tests for the PR103-PR111 deployment-readiness design.

WO-HELM-HERMES-CUMULATIVE-PR103-PR111-DEPLOYMENT-READINESS-AND-PHASE2-RUNTIME-WIRING-DESIGN-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-18. DESIGN-ONLY.

These tests validate ONLY the design artifacts added by this WO. They implement nothing, wire nothing,
build nothing, deploy nothing, enable nothing. They assert the design's own invariants hold so the
future governed programme cannot silently drift (e.g. a stage that implicitly enables shadow, a stage
that introduces Redis/SQL, a Phase-3 cutover sneaking into scope).
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

_REPO = Path(__file__).resolve().parents[1]
_DR = _REPO / "docs" / "design" / "deployment_readiness"
_MODELS = _DR / "models"
_SCHEMAS = _REPO / "schemas" / "deployment_readiness"


def _load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- fixtures / loaders
def _stage_model() -> dict:
    return _load(_MODELS / "deployment_stage_model.v1.json")


def _pr_inventory() -> dict:
    return _load(_MODELS / "nine_pr_inventory.v1.json")


def _stages() -> list:
    return _stage_model()["stages"]


def _first_shadow_enabled_index(stages: list) -> int:
    for i, s in enumerate(stages):
        if s["shadow_enabled"] is True:
            return i
    raise AssertionError("no shadow_enabled stage found")


# --------------------------------------------------------------------------- §25.1 schema validity
def test_config_schema_is_valid_draft7():
    schema = _load(_SCHEMAS / "phase2_config.v1.schema.json")
    jsonschema.Draft7Validator.check_schema(schema)


def test_jsonl_record_schema_is_valid_draft7():
    schema = _load(_SCHEMAS / "phase2_jsonl_record.v1.schema.json")
    jsonschema.Draft7Validator.check_schema(schema)


def test_disabled_config_example_validates_against_schema():
    schema = _load(_SCHEMAS / "phase2_config.v1.schema.json")
    example = _load(_DR / "examples" / "phase2_config.disabled.example.json")
    jsonschema.validate(instance=example, schema=schema)


def test_disabled_config_example_is_actually_disabled():
    example = _load(_DR / "examples" / "phase2_config.disabled.example.json")
    assert example["shadow_enabled"] is False
    assert example["shadow_consumer_live"] is False


def test_schema_forbids_string_coercion_of_shadow_enabled():
    """A string 'true' must be rejected -> no env string coercion can enable."""
    schema = _load(_SCHEMAS / "phase2_config.v1.schema.json")
    example = _load(_DR / "examples" / "phase2_config.disabled.example.json")
    example["shadow_enabled"] = "true"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=example, schema=schema)


def test_schema_forbids_consumer_live_true():
    schema = _load(_SCHEMAS / "phase2_config.v1.schema.json")
    example = _load(_DR / "examples" / "phase2_config.disabled.example.json")
    example["shadow_consumer_live"] = True
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=example, schema=schema)


# --------------------------------------------------------------------------- §25.2 nine PRs present
def test_pr_inventory_has_all_nine_prs():
    inv = _pr_inventory()
    numbers = sorted(p["number"] for p in inv["prs"])
    assert numbers == [103, 104, 105, 106, 107, 108, 109, 110, 111]


def test_only_pr103_has_automatic_runtime_effect():
    inv = _pr_inventory()
    auto = [p["number"] for p in inv["prs"] if p.get("automatic_runtime_effect_on_rebuild") is True]
    assert auto == [103]


def test_no_pr_changes_dependencies():
    inv = _pr_inventory()
    for p in inv["prs"]:
        assert p["changes_dependencies"] is False, p["number"]


# --------------------------------------------------------------------------- §25.3 stage shadow flags
def test_every_stage_has_explicit_boolean_shadow_enabled():
    for s in _stages():
        assert isinstance(s["shadow_enabled"], bool), s["id"]


def test_only_stage_F_or_later_may_enable_shadow():
    stages = _stages()
    ids = [s["id"] for s in stages]
    f_index = ids.index("F")
    for i, s in enumerate(stages):
        if s["shadow_enabled"] is True:
            assert i >= f_index, f"stage {s['id']} enables shadow before F"


def test_stages_before_F_are_all_disabled():
    stages = _stages()
    ids = [s["id"] for s in stages]
    f_index = ids.index("F")
    for s in stages[:f_index]:
        assert s["shadow_enabled"] is False, s["id"]


# --------------------------------------------------------------------------- §25.4 deploy != activate
def test_deployment_and_activation_are_separate_stages():
    stages = _stages()
    deploy = [s for s in stages if s.get("is_deployment") is True]
    activate = [s for s in stages if s.get("is_activation") is True]
    assert len(deploy) == 1
    assert len(activate) == 1
    assert deploy[0]["id"] != activate[0]["id"]
    # the deploy stage must NOT enable shadow; the activate stage MUST.
    assert deploy[0]["shadow_enabled"] is False
    assert activate[0]["shadow_enabled"] is True


# --------------------------------------------------------------------------- §25.5 every stage rollback
def test_every_stage_has_a_rollback():
    for s in _stages():
        assert "rollback" in s and s["rollback"].get("action"), s["id"]


# --------------------------------------------------------------------------- §25.6 N-1/N-2 before shadow
def test_n1_and_n2_resolved_before_any_shadow_enabled_stage():
    stages = _stages()
    first_shadow = _first_shadow_enabled_index(stages)
    resolved_before = set()
    for s in stages[:first_shadow]:
        resolved_before.update(s.get("resolves_hardening", []))
    assert "N-1" in resolved_before
    assert "N-2" in resolved_before


# --------------------------------------------------------------------------- §25.7 no Redis/SQL
def test_no_stage_introduces_redis_or_sql():
    for s in _stages():
        assert s.get("introduces_redis_or_sql") is False, s["id"]


# --------------------------------------------------------------------------- §25.8 consumer_live false
def test_consumer_live_false_in_all_stages():
    for s in _stages():
        assert s.get("consumer_live") is False, s["id"]


# --------------------------------------------------------------------------- §25.9 Phase-3 out of scope
def test_phase3_out_of_scope():
    model = _stage_model()
    assert model.get("phase3_in_scope") is False
    blob = json.dumps(model).lower()
    assert "phase-3" not in blob or "out of scope" in blob
    # no stage id beyond H; no stage kind that is a cutover/authority promotion
    ids = [s["id"] for s in model["stages"]]
    assert ids == ["A", "B", "C", "D", "E", "F", "G", "H"]
    allowed_kinds = {
        "verify", "build", "audit", "deploy", "post-deploy-audit",
        "activate", "observe", "post-observe-audit",
    }
    for s in model["stages"]:
        # stage_kind is an enumerated, non-cutover kind; the shadow-enable stage is a bounded
        # activation, never a Phase-3 authority promotion.
        assert s["stage_kind"] in allowed_kinds, s["id"]
        assert "cutover" not in s["stage_kind"]
        assert s.get("is_activation") is not None  # activation is bounded, not a Phase-3 authority flip


# --------------------------------------------------------------------------- cross-model coherence
def test_stage_model_and_rollback_model_agree_on_prior_image():
    stage = _stage_model()
    roll = _load(_MODELS / "rollback_state_machine.v1.json")
    assert stage["prior_image_digest"] == "c5fc2a62f424"
    assert stage["prior_source_sha"] == "71ea3bd"
    assert roll["requirements"]["prior_image_digest"] == "c5fc2a62f424"
    assert roll["requirements"]["prior_source_sha"] == "71ea3bd"
    assert roll["requirements"]["no_redis"] is True
    assert roll["requirements"]["no_sql"] is True


def test_dependency_matrix_reports_cumulative_deploy_safe():
    dep = _load(_MODELS / "pr_dependency_matrix.v1.json")
    assert dep["cumulative_deploy_safe"] is True
    assert dep["blocker"] is None
