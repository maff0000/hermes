"""XAU operational parity: the tracked bundle reproduces the current authorised live effective config.
WO-HELM-HERMES-COMPLETE-TRACKED-RUNTIME-CONFIG-AND-XAU-PARITY-0001."""
import json
import pathlib

import pytest

from deploy.advanced_v1.render_effective_config import render_effective, container_var
from deploy.advanced_v1 import runtime_config_schema_v1 as schema
from deploy.advanced_v1 import xau_parity_v1 as parity

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_COMPOSE = [str(_ROOT / "docker-compose.yml"),
            str(_ROOT / "deploy/advanced_v1/docker-compose.operational.yml"),
            str(_ROOT / "deploy/advanced_v1/docker-compose.dark.yml")]
LIVE = json.loads((_ROOT / "tests/fixtures/live_effective_config_v1.json").read_text())["fields"]


def _min_env(**over):
    e = {"SOURCE_SHA": "d489075e3b82f57694675f586b404009d304ff91", "BUILD_UTC": "2026-08-06T13:00:00Z",
         "HERMES_IMAGE_REF": "hermes-signal:prod-d489075e3b82", "HERMES_IMAGE_TAG": "prod-d489075e3b82",
         "DB_HOST": "192.168.11.10", "DB_PORT": "3307", "DB_NAME": "tradingSignals", "REDIS_HOST": "192.168.11.10",
         "HERMES_CANDLE_CANONICAL_REDIS_HOST": "192.168.11.10", "HERMES_CANDLE_CANONICAL_ACTIVATION_APPROVAL": "DUMMY",
         "ENVIRONMENT": "DEV", "RUN_ENV": "STAGING", "SIGNAL_PORT": "8211", "OANDA_ENVIRONMENT": "live"}
    e.update(over)
    return e


def test_full_parity_matches_live_effective():
    rendered = render_effective(_COMPOSE, _min_env())
    ok, results = parity.compare(LIVE, rendered)
    fails = [r for r in results if r["status"] != "OK"]
    assert ok, f"parity failures: {fails}"
    assert len(results) >= 70   # substantial parity-critical coverage


def test_every_parity_critical_field_is_load_bearing():
    """Removing ANY parity-critical non-secret field from the tracked render must make parity fail — no vacuous pass."""
    base = render_effective(_COMPOSE, _min_env())
    checked = 0
    for name in schema.PARITY_CRITICAL:
        rec = schema.BY_NAME[name]
        if rec["secret"] or rec["authority_bearing"]:
            continue
        cvar = container_var(name)
        if cvar not in base:
            continue
        broken = dict(base)
        broken.pop(cvar)
        ok, _ = parity.compare(LIVE, broken)
        assert not ok, f"{cvar} omission did not fail parity"
        checked += 1
    assert checked >= 60


def test_parity_detects_value_change():
    base = render_effective(_COMPOSE, _min_env())
    base["HERMES_INDICATOR_PUBLISH_TIMEFRAMES"] = "M1,M5"   # narrowed timeframe -> regression
    ok, results = parity.compare(LIVE, base)
    assert not ok
    assert any(r["field"] == "HERMES_INDICATOR_PUBLISH_TIMEFRAMES" and r["status"] == "FAIL" for r in results)


def test_db_port_is_3307_not_3306():
    rendered = render_effective(_COMPOSE, _min_env())
    assert rendered[container_var("DB_PORT")] == "3307"


def test_missing_from_tracked_is_empty_for_full_bundle():
    rendered = render_effective(_COMPOSE, _min_env())
    assert parity.missing_from_tracked(rendered) == []
