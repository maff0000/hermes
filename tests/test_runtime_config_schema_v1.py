"""Configuration schema + inventory integrity + canonical naming.
WO-HELM-HERMES-COMPLETE-TRACKED-RUNTIME-CONFIG-AND-XAU-PARITY-0001."""
import json
import pathlib

import pytest

from deploy.advanced_v1 import runtime_config_schema_v1 as schema
from deploy.advanced_v1.render_effective_config import container_var

_ROOT = pathlib.Path(__file__).resolve().parents[1]
LIVE = json.loads((_ROOT / "tests/fixtures/live_effective_config_v1.json").read_text())["fields"]
_TYPES = {"bool", "int", "enum", "csv", "string", "sha40", "iso8601"}


def test_field_records_are_well_formed():
    seen = set()
    for r in schema.FIELDS:
        assert r["name"] not in seen, f"duplicate field {r['name']}"
        seen.add(r["name"])
        assert r["type"] in _TYPES, (r["name"], r["type"])
        if r["type"] == "enum":
            assert r["enum"], f"{r['name']} enum without values"
        if r["must_equal"] is not None:
            assert r["type"] in ("bool", "enum", "int", "string"), r["name"]


def test_canonical_names_are_not_dev_prefixed():
    for r in schema.FIELDS:
        assert not r["name"].startswith("DEV_"), f"{r['name']} is a legacy DEV_* name, not canonical"


def test_aliases_map_uniquely_and_are_legacy():
    for alias, canonical in schema.ALIAS_TO_CANONICAL.items():
        assert alias.startswith("DEV_"), alias
        assert canonical in schema.BY_NAME
        assert alias not in schema.BY_NAME, f"{alias} must not also be a canonical field"


def test_parity_critical_fields_have_a_consequence():
    for name in schema.PARITY_CRITICAL:
        assert schema.BY_NAME[name]["consequence"], f"{name} parity-critical without documented consequence"


def test_db_port_must_equal_3307():
    assert schema.BY_NAME["DB_PORT"]["must_equal"] == "3307"
    assert "DEV_DB_PORT" in schema.BY_NAME["DB_PORT"]["deprecated_aliases"]


def test_dark_invariants_fixed_in_schema():
    assert schema.MUST_EQUAL["HERMES_ADVANCED_V1_MASTER_ENABLED"] == "false"
    assert schema.MUST_EQUAL["HERMES_ADVANCED_V1_PUBLISHER_MODE"] == "DISABLED"
    assert schema.MUST_EQUAL["CONSUMER_LIVE"] == "false"
    assert schema.MUST_EQUAL["HERMES_BACKFILL_EXECUTION_ENABLED"] == "false"


def test_activation_token_is_authority_not_recordable():
    r = schema.BY_NAME["HERMES_CANDLE_CANONICAL_ACTIVATION_APPROVAL"]
    assert r["authority_bearing"] and r["current_effective"] == schema.EXTERNAL_REQUIRED


# ---- inventory coverage (§25): every live parity-critical container var maps to a schema field ----
def test_inventory_covers_every_live_parity_field():
    covered = {container_var(n) for n in schema.PARITY_CRITICAL}
    uncovered = [k for k in LIVE if k not in covered]
    assert not uncovered, f"live parity fields not covered by schema: {uncovered}"


def test_inventory_coverage_is_non_vacuous():
    """Removing a schema field must leave a live parity var uncovered — proves the coverage test has teeth."""
    victim = "HERMES_INDICATOR_PUBLISH_TIMEFRAMES"
    covered = {container_var(n) for n in schema.PARITY_CRITICAL if n != victim}
    assert container_var(victim) in LIVE and container_var(victim) not in covered


def test_schema_serialises_to_json():
    obj = json.loads(schema.to_json())
    assert obj["schema_version"] == "v1" and len(obj["fields"]) == len(schema.FIELDS)


def test_no_secret_value_in_schema_source():
    src = (_ROOT / "deploy/advanced_v1/runtime_config_schema_v1.py").read_text()
    for tok in ("hunter", "BEGIN RSA", "BEGIN OPENSSH", "NO_SHADOW_CANONICAL"):
        assert tok not in src
    # no field records a secret VALUE (only classification tokens)
    for r in schema.FIELDS:
        if r["secret"] or r["authority_bearing"]:
            assert r["current_effective"] in (schema.PRESENT_BY_SECRET_REFERENCE, schema.EXTERNAL_REQUIRED, None)
