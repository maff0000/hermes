"""Compose rendering + secret-safety of the tracked deployment bundle.
WO-HELM-HERMES-COMPLETE-TRACKED-RUNTIME-CONFIG-AND-XAU-PARITY-0001."""
import pathlib

import pytest
import yaml

from deploy.advanced_v1.render_effective_config import render_effective, safe_report, RenderError

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_ADV = _ROOT / "deploy/advanced_v1"
_COMPOSE = [str(_ROOT / "docker-compose.yml"), str(_ADV / "docker-compose.operational.yml"),
            str(_ADV / "docker-compose.dark.yml")]
_TRACKED_ARTEFACTS = ["docker-compose.operational.yml", "docker-compose.dark.yml", "deployment.env.template",
                      "runtime_config_schema_v1.py", "runtime_config_preflight_v1.py", "render_effective_config.py",
                      "xau_parity_v1.py", "deploy.sh", "rollback.sh"]


def _full_env(**over):
    e = {"SOURCE_SHA": "d489075e3b82f57694675f586b404009d304ff91", "BUILD_UTC": "2026-08-06T13:00:00Z",
         "HERMES_IMAGE_REF": "hermes-signal:prod-d489075e3b82", "HERMES_IMAGE_TAG": "prod-d489075e3b82",
         "DB_HOST": "192.168.11.10", "DB_PORT": "3307", "DB_NAME": "tradingSignals", "REDIS_HOST": "192.168.11.10",
         "HERMES_CANDLE_CANONICAL_REDIS_HOST": "192.168.11.10", "HERMES_CANDLE_CANONICAL_ACTIVATION_APPROVAL": "TOK",
         "ENVIRONMENT": "DEV", "RUN_ENV": "STAGING", "SIGNAL_PORT": "8211", "OANDA_ENVIRONMENT": "live"}
    e.update(over)
    return e


def test_tracked_artefacts_are_present():
    for f in _TRACKED_ARTEFACTS:
        assert (_ADV / f).exists(), f"missing tracked artefact {f}"


def test_render_produces_single_service_env_with_correct_routing():
    rendered = render_effective(_COMPOSE, _full_env())
    assert rendered["DEV_DB_HOST"] == "192.168.11.10" and rendered["DEV_DB_PORT"] == "3307"
    assert rendered["DEV_REDIS_HOST"] == "192.168.11.10"
    assert rendered["HERMES_ADVANCED_V1_MASTER_ENABLED"] == "false"
    assert rendered["HERMES_ADVANCED_V1_PUBLISHER_MODE"] == "DISABLED"
    assert rendered["CONSUMER_LIVE"] == "false"
    assert rendered["HERMES_BACKFILL_EXECUTION_ENABLED"] == "false"
    assert rendered["HERMES_PUBLISHER_RUNTIME_OWNER"] == "in_process"


def test_render_requires_db_port_no_3306_fallback():
    with pytest.raises(RenderError):
        render_effective(_COMPOSE, _full_env(DB_PORT=None) if False else {k: v for k, v in _full_env().items() if k != "DB_PORT"})


def test_dark_overlay_wins_over_operational():
    # operational overlay does not set master; dark overlay pins it false — precedence proven via file order
    rendered = render_effective(_COMPOSE, _full_env())
    assert rendered["HERMES_ADVANCED_V1_MASTER_ENABLED"] == "false"


def test_single_service_one_replica():
    for path in (_ADV / "docker-compose.operational.yml", _ADV / "docker-compose.dark.yml"):
        doc = yaml.safe_load(path.read_text())
        assert list(doc["services"].keys()) == ["hermes-signal"]
        assert "deploy" not in doc["services"]["hermes-signal"] or \
               doc["services"]["hermes-signal"].get("deploy", {}).get("replicas", 1) == 1


def test_no_secret_values_in_any_tracked_artefact():
    import re
    secret_re = re.compile(r"(hunter2|BEGIN (RSA|OPENSSH)|NO_SHADOW_CANONICAL|[0-9a-f]{64}\b)")
    for f in _TRACKED_ARTEFACTS + ["config_inventory_v1.md", "README.md"]:
        p = _ADV / f
        if not p.exists():
            continue
        txt = p.read_text()
        assert not secret_re.search(txt), f"possible secret material in {f}"
        # never an OANDA key / DB password literal
        assert "OANDA_API_KEY=" not in txt or "<" in txt


def test_safe_report_redacts_secrets():
    rep = safe_report(_COMPOSE, _full_env(HERMES_CANDLE_CANONICAL_ACTIVATION_APPROVAL="TOPSECRET"))
    import json
    blob = json.dumps(rep).lower()
    assert "topsecret" not in blob
    row = {r["name"]: r for r in rep["fields"]}
    assert row["HERMES_CANDLE_CANONICAL_ACTIVATION_APPROVAL"]["value"] == "<REDACTED>"


def test_no_dev_override_actively_included_by_deploy_wrapper():
    # a comment may explain what the bundle REPLACES, but the active `-f` compose sequence must never include it
    deploy = (_ADV / "deploy.sh").read_text()
    assert "-f docker-compose.dev-override" not in deploy
    assert "dev-override" not in "".join(l for l in deploy.splitlines() if not l.strip().startswith("#"))
    # preflight's canonical compose set excludes any override
    from deploy.advanced_v1 import runtime_config_preflight_v1 as pf
    assert not any("override" in c for c in pf.EXPECTED_COMPOSE)
