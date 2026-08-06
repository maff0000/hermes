"""Full-contract fail-closed preflight matrix.
WO-HELM-HERMES-COMPLETE-TRACKED-RUNTIME-CONFIG-AND-XAU-PARITY-0001."""
import pathlib

import pytest

from deploy.advanced_v1 import runtime_config_preflight_v1 as pf

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_DIGEST = "hermes-signal@sha256:" + "a" * 64
_ROLLBACK = "hermes-signal@sha256:" + "d" * 64


def good(**over):
    e = {"SOURCE_SHA": "d489075e3b82f57694675f586b404009d304ff91", "BUILD_UTC": "2026-08-06T13:00:00Z",
         "HERMES_IMAGE_REF": _DIGEST, "HERMES_IMAGE_TAG": "prod-d489075e3b82",
         "DB_HOST": "192.168.11.10", "DB_PORT": "3307", "DB_NAME": "tradingSignals", "REDIS_HOST": "192.168.11.10",
         "HERMES_CANDLE_CANONICAL_REDIS_HOST": "192.168.11.10", "HERMES_CANDLE_CANONICAL_ACTIVATION_APPROVAL": "TOK",
         "ENVIRONMENT": "DEV", "RUN_ENV": "STAGING", "SIGNAL_PORT": "8211", "OANDA_ENVIRONMENT": "live",
         "HERMES_ROLLBACK_IMAGE_REF": _ROLLBACK}
    for k, v in over.items():
        if v is None:
            e.pop(k, None)
        else:
            e[k] = v
    return e


def _run(env=None, **kw):
    return pf.validate_runtime_config(env if env is not None else good(), **kw)


def test_valid_full_contract_passes():
    ok, faults, safe = _run()
    assert ok, faults
    assert safe["db"]["port"] == "3307" and safe["expansion_dark"]["master"] == "false"


@pytest.mark.parametrize("over,frag", [
    ({"DB_PORT": None}, "CFG-ENV-MISSING-DB_PORT"),
    ({"DB_PORT": "3306"}, "CFG-DB-PORT"),
    ({"REDIS_PORT": "70000"}, "CFG-RANGE-REDIS_PORT"),   # range fires on a non-must_equal int field
    ({"DEV_DB_PORT": "9999"}, "CFG-ALIAS-CONFLICT"),
    ({"DB_HOST": None, "DEV_DB_HOST": "10.0.0.9"}, "CFG-LEGACY-ONLY"),  # alias present, canonical absent
    ({"DB_NAME": None}, "CFG-ENV-MISSING-DB_NAME"),
    ({"REDIS_HOST": None}, "CFG-ENV-MISSING-REDIS_HOST"),
    ({"HERMES_CANDLE_CANONICAL_REDIS_HOST": None}, "CFG-ENV-MISSING-HERMES_CANDLE_CANONICAL_REDIS_HOST"),
    ({"HERMES_CANDLE_CANONICAL_ACTIVATION_APPROVAL": None}, "CFG-ENV-MISSING-HERMES_CANDLE_CANONICAL_ACTIVATION_APPROVAL"),
    ({"OANDA_ENVIRONMENT": None}, "CFG-ENV-MISSING-OANDA_ENVIRONMENT"),
    ({"OANDA_ENVIRONMENT": "demo"}, "CFG-ENUM-OANDA_ENVIRONMENT"),
    ({"SOURCE_SHA": "abc"}, "CFG-SOURCE-SHA-MALFORMED"),
    ({"HERMES_IMAGE_REF": "hermes-signal:latest"}, "CFG-IMAGE-NOT-PINNED"),
    ({"HERMES_ROLLBACK_IMAGE_REF": None}, "CFG-ROLLBACK-MISSING"),
])
def test_breach_fails_closed(over, frag):
    ok, faults, _ = _run(good(**over))
    assert not ok
    assert any(frag in f for f in faults), faults


def test_unsafe_redis_fallback_detected():
    # simulate a render where REDIS_HOST resolves to localhost by passing an env that yields it — force via ENVIRONMENT
    ok, faults, _ = _run(good(REDIS_HOST="127.0.0.1"))
    assert not ok and any("CFG-REDIS-UNSAFE-FALLBACK" in f for f in faults)


def test_forbidden_dev_override_in_compose_set():
    ok, faults, _ = _run(good(), compose_files=["docker-compose.yml", "docker-compose.dev-override.yml"])
    assert not ok
    assert any("CFG-FORBIDDEN-OVERRIDE" in f for f in faults) or any("CFG-COMPOSE-SET" in f for f in faults)


def test_extra_overlay_rejected():
    ok, faults, _ = _run(good(), compose_files=pf.EXPECTED_COMPOSE + ["deploy/advanced_v1/extra.yml"])
    assert not ok and any("CFG-COMPOSE-SET" in f for f in faults)


def test_master_true_cannot_flip_governed_dark_state():
    # even if the env carries master=true, the dark overlay's explicit false wins in the render -> still GREEN dark
    ok, faults, safe = _run(good(HERMES_ADVANCED_V1_MASTER_ENABLED="true"))
    assert safe["expansion_dark"]["master"] == "false"


def test_safe_summary_has_no_secret_value():
    _, _, safe = _run(good(DB_PASSWORD="hunter2", HERMES_CANDLE_CANONICAL_ACTIVATION_APPROVAL="TOPSECRET"))
    import json
    blob = json.dumps(safe).lower()
    assert "hunter2" not in blob and "topsecret" not in blob
    assert safe["canonical_redis"]["activation"] == "<PRESENT>"
