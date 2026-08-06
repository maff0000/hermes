"""Reproducible dark-deployment config preflight. WO-...-PROVENANCE-SCOPE-READINESS-...-0001. Pure, no I/O."""
import importlib.util
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("dark_pf", _ROOT / "deploy/advanced_v1/dark_config_preflight.py")
pf = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(pf)

GOOD_SHA = "cdbaabd0cc3a069da8357bd8a2183a5261e3b99c"


def _good_env(**over):
    e = {"SOURCE_SHA": GOOD_SHA, "HERMES_IMAGE_REF": "hermes-signal:prod-cdbaabd0", "HERMES_IMAGE_TAG": "prod-cdbaabd0",
         "HERMES_REQUIRE_DIGEST_PIN": "true", "HERMES_ADVANCED_V1_MASTER_ENABLED": "false",
         "HERMES_ADVANCED_V1_PUBLISHER_MODE": "DISABLED", "CONSUMER_LIVE": "false",
         "HERMES_BACKFILL_EXECUTION_ENABLED": "false", "HERMES_TICK_PUBLISH_ENABLED": "true"}
    e.update(over)
    return e


def test_valid_dark_config_passes():
    ok, faults, safe = pf.validate_dark_config(_good_env(), pilot_scope_present=True)
    assert ok and faults == []
    assert safe["HERMES_ADVANCED_V1_MASTER_ENABLED"] == "false" and safe["HERMES_ADVANCED_V1_PUBLISHER_MODE"] == "DISABLED"


@pytest.mark.parametrize("over,frag", [
    ({"HERMES_ADVANCED_V1_MASTER_ENABLED": "true"}, "DARK-MASTER-NOT-FALSE"),
    ({"HERMES_ADVANCED_V1_MASTER_ENABLED": None}, "DARK-MASTER-ABSENT"),
    ({"HERMES_ADVANCED_V1_PUBLISHER_MODE": "ACTIVE"}, "DARK-MODE-NOT-DISABLED"),
    ({"HERMES_ADVANCED_V1_PUBLISHER_MODE": "SHADOW"}, "DARK-MODE-NOT-DISABLED"),
    ({"HERMES_ADVANCED_V1_PUBLISHER_MODE": None}, "DARK-MODE-ABSENT"),
    ({"SOURCE_SHA": "UNKNOWN_SOURCE_SHA"}, "DARK-SOURCE-SHA"),
    ({"SOURCE_SHA": "short"}, "DARK-SOURCE-SHA"),
    ({"HERMES_IMAGE_REF": "", "HERMES_IMAGE_TAG": "local"}, "DARK-IMAGE-NOT-PINNED"),
    ({"CONSUMER_LIVE": "true"}, "DARK-CONSUMER-ENABLED"),
    ({"CONSUMER_LIVE": None}, "DARK-CONSUMER-STATE-ABSENT"),
    ({"HERMES_BACKFILL_EXECUTION_ENABLED": "true"}, "DARK-BACKFILL-EXECUTION-ENABLED"),
    ({"HERMES_ORDER_ENABLED": "true"}, "DARK-ORDER-AUTHORITY"),
    ({"HERMES_SECOND_STREAM": "true"}, "DARK-SECOND-STREAM"),
])
def test_unsafe_config_rejected(over, frag):
    ok, faults, safe = pf.validate_dark_config(_good_env(**over), pilot_scope_present=True)
    assert not ok and any(frag in f for f in faults), faults


def test_missing_pilot_scope_rejected():
    ok, faults, _ = pf.validate_dark_config(_good_env(), pilot_scope_present=False)
    assert not ok and "DARK-PILOT-SCOPE-ABSENT" in faults


def test_safe_summary_never_contains_secrets():
    env = _good_env(DB_PASSWORD="hunter2", OANDA_API_TOKEN="tok", REDIS_SECRET="x")
    ok, faults, safe = pf.validate_dark_config(env, pilot_scope_present=True)
    blob = str(safe).lower()
    for tok in ("hunter2", "password", "token", "secret", "credential", "oanda_api"):
        assert tok not in blob


def test_overlay_and_template_are_version_controlled_and_secret_free():
    import subprocess
    for f in ("deploy/advanced_v1/docker-compose.dark.yml", "deploy/advanced_v1/dark.env.template",
              "deploy/advanced_v1/dark_config_preflight.py"):
        out = subprocess.run(["git", "ls-files", f], cwd=_ROOT, capture_output=True, text=True).stdout.strip()
        assert out == f, f"{f} must be version-controlled"
    # the template carries only placeholders, never a real secret value
    tmpl = (_ROOT / "deploy/advanced_v1/dark.env.template").read_text()
    assert "DB_PASSWORD=<" in tmpl and "hunter2" not in tmpl
    # the overlay pins expansion dark controls explicitly
    overlay = (_ROOT / "deploy/advanced_v1/docker-compose.dark.yml").read_text()
    assert 'HERMES_ADVANCED_V1_MASTER_ENABLED: "false"' in overlay
    assert 'HERMES_ADVANCED_V1_PUBLISHER_MODE: "DISABLED"' in overlay
