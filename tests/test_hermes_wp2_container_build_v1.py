"""
WP2 container-build & externalised-config tests — ADDITIVE, all PASS.
WO-HELM-HERMES-CONTAINER-MVP-WP2-CANONICAL-BUILD-AND-EXTERNALISED-CONFIGURATION-0001.

Pure helpers only — no running server, no real secrets (tmp files / fixtures only). Every secret assertion
proves the VALUE never leaks into the returned dict or the log records.
"""
import logging
import os
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).parent.parent.absolute()
sys.path.insert(0, str(BASE_DIR))

from utils.hermes_build_identity_v1 import build_identity, validate_build_identity  # noqa: E402
from utils.hermes_healthcheck_probe_v1 import healthcheck_decode  # noqa: E402
from utils.hermes_status_payload_v1 import build_status_payload  # noqa: E402
import env_config  # noqa: E402
from env_config import get_secret, load_secret_file  # noqa: E402


_VALID_SHA = "a" * 40
_SECRET_VALUE = "s3cr3t-VALUE-should-never-be-logged-1234567890"


# --------------------------------------------------------------------------- Build identity ---------

def test_validate_build_identity_valid_passes():
    d = {"source_sha": _VALID_SHA, "build_utc": "2026-07-29T00:00:00+00:00"}
    assert validate_build_identity(d) == []


def test_validate_build_identity_sha_missing():
    d = {"source_sha": "UNKNOWN_SOURCE_SHA", "build_utc": "2026-07-29T00:00:00+00:00"}
    assert "BI-SHA-MISSING" in validate_build_identity(d)


def test_validate_build_identity_sha_not_40_hex():
    d = {"source_sha": "abc123", "build_utc": "2026-07-29T00:00:00+00:00"}
    assert "BI-SHA-NOT-40-HEX" in validate_build_identity(d)


def test_validate_build_identity_sha_is_latest():
    d = {"source_sha": "latest", "build_utc": "2026-07-29T00:00:00+00:00"}
    assert "BI-SHA-IS-LATEST" in validate_build_identity(d)


def test_validate_build_identity_build_utc_missing():
    d = {"source_sha": _VALID_SHA}
    assert "BI-BUILD-UTC-MISSING" in validate_build_identity(d)


def test_build_identity_application_and_no_secrets(monkeypatch):
    # Even if secret-shaped env is present, build_identity must not surface any secret value.
    monkeypatch.setenv("OANDA_API_KEY", _SECRET_VALUE)
    monkeypatch.setenv("DB_PASSWORD", _SECRET_VALUE)
    monkeypatch.setenv("DISCORD_WEBHOOK_DEV", "https://discord.com/api/webhooks/1/xxxx")
    d = build_identity()
    assert d["application"] == "HERMES"
    # No secret-shaped keys.
    for k in d:
        assert "PASSWORD" not in k.upper()
        assert "TOKEN" not in k.upper()
        assert "WEBHOOK" not in k.upper()
        assert "OANDA" not in k.upper()
    # No secret VALUE anywhere in the dict.
    blob = repr(d)
    assert _SECRET_VALUE not in blob
    assert "webhooks/1/xxxx" not in blob


def test_build_identity_candidate_invalid_sha_marks_invalid(monkeypatch):
    monkeypatch.setenv("HERMES_BUILD_CLASSIFICATION", "NON_PROMOTED_ENGINEERING_CANDIDATE")
    monkeypatch.delenv("SOURCE_SHA", raising=False)
    monkeypatch.setenv("BUILD_UTC", "2026-07-29T00:00:00+00:00")
    d = build_identity()
    assert d["build_identity_valid"] is False
    assert "BI-SHA-MISSING" in d["build_identity_reasons"]


def test_build_identity_candidate_valid_sha(monkeypatch):
    monkeypatch.setenv("HERMES_BUILD_CLASSIFICATION", "NON_PROMOTED_ENGINEERING_CANDIDATE")
    monkeypatch.setenv("SOURCE_SHA", _VALID_SHA)
    monkeypatch.setenv("BUILD_UTC", "2026-07-29T00:00:00+00:00")
    d = build_identity()
    assert d["build_identity_valid"] is True
    assert d["source_sha"] == _VALID_SHA


# --------------------------------------------------------------------------- Secret loading ---------

def _clear_secret_env(monkeypatch, key):
    for name in (key, f"{key}_FILE", f"{env_config.ENV}_{key}", f"{env_config.ENV}_{key}_FILE"):
        monkeypatch.delenv(name, raising=False)


def test_get_secret_direct_env(monkeypatch):
    _clear_secret_env(monkeypatch, "MY_SECRET")
    monkeypatch.setenv("MY_SECRET", _SECRET_VALUE)
    assert get_secret("MY_SECRET") == _SECRET_VALUE


def test_get_secret_file_trailing_newline_trimmed(monkeypatch, tmp_path):
    _clear_secret_env(monkeypatch, "MY_SECRET")
    f = tmp_path / "secret.txt"
    f.write_text(_SECRET_VALUE + "\n")
    os.chmod(f, 0o600)
    monkeypatch.setenv("MY_SECRET_FILE", str(f))
    assert get_secret("MY_SECRET") == _SECRET_VALUE  # newline trimmed, internal content intact


def test_get_secret_missing_file_unreadable(monkeypatch, tmp_path):
    _clear_secret_env(monkeypatch, "MY_SECRET")
    monkeypatch.setenv("MY_SECRET_FILE", str(tmp_path / "does_not_exist.txt"))
    with pytest.raises(ValueError, match="SECRET-FILE-UNREADABLE"):
        get_secret("MY_SECRET")


def test_get_secret_empty_file(monkeypatch, tmp_path):
    _clear_secret_env(monkeypatch, "MY_SECRET")
    f = tmp_path / "empty.txt"
    f.write_text("   \n")  # whitespace only
    os.chmod(f, 0o600)
    monkeypatch.setenv("MY_SECRET_FILE", str(f))
    with pytest.raises(ValueError, match="SECRET-FILE-EMPTY"):
        get_secret("MY_SECRET")


def test_get_secret_source_conflict(monkeypatch, tmp_path):
    _clear_secret_env(monkeypatch, "MY_SECRET")
    f = tmp_path / "secret.txt"
    f.write_text(_SECRET_VALUE + "\n")
    os.chmod(f, 0o600)
    monkeypatch.setenv("MY_SECRET_FILE", str(f))
    monkeypatch.setenv("MY_SECRET", _SECRET_VALUE)
    with pytest.raises(ValueError, match="SECRET-SOURCE-CONFLICT"):
        get_secret("MY_SECRET")


def test_get_secret_unsafe_perms_warns_not_fails(monkeypatch, tmp_path, caplog):
    _clear_secret_env(monkeypatch, "MY_SECRET")
    f = tmp_path / "secret.txt"
    f.write_text(_SECRET_VALUE + "\n")
    os.chmod(f, 0o644)  # group/world readable
    monkeypatch.setenv("MY_SECRET_FILE", str(f))
    with caplog.at_level(logging.WARNING, logger="env_config"):
        val = get_secret("MY_SECRET")
    assert val == _SECRET_VALUE
    assert any("SECRET-FILE-PERMISSIONS" in r.getMessage() for r in caplog.records)
    # The secret VALUE never appears in any log record.
    for r in caplog.records:
        assert _SECRET_VALUE not in r.getMessage()


def test_get_secret_required_missing(monkeypatch):
    _clear_secret_env(monkeypatch, "MY_SECRET")
    with pytest.raises(ValueError, match="SECRET-REQUIRED-MISSING"):
        get_secret("MY_SECRET", required=True)


def test_get_secret_value_never_logged(monkeypatch, tmp_path, caplog):
    _clear_secret_env(monkeypatch, "MY_SECRET")
    f = tmp_path / "secret.txt"
    f.write_text(_SECRET_VALUE + "\n")
    os.chmod(f, 0o600)
    monkeypatch.setenv("MY_SECRET_FILE", str(f))
    with caplog.at_level(logging.DEBUG, logger="env_config"):
        get_secret("MY_SECRET")
    for r in caplog.records:
        assert _SECRET_VALUE not in r.getMessage()


# --------------------------------------------------------------------------- Discord ownership ------

def _clear_discord_env(monkeypatch):
    for k in ("DISCORD_WEBHOOK_DEV", "DISCORD_WEBHOOK_DEV_FILE",
              "DISCORD_WEBHOOK_PROD", "DISCORD_WEBHOOK_PROD_FILE"):
        for name in (k, f"{env_config.ENV}_{k}"):
            monkeypatch.delenv(name, raising=False)


def test_discord_rejects_ares_file_reference(monkeypatch, caplog):
    import utils.discord_alerts as da
    _clear_discord_env(monkeypatch)
    key = "DISCORD_WEBHOOK_PROD" if env_config.ENV == "PROD" else "DISCORD_WEBHOOK_DEV"
    monkeypatch.setenv(f"{key}_FILE", "/srv-dev/tradingProteus/ares/.env")
    with caplog.at_level(logging.ERROR, logger="utils.discord_alerts"):
        assert da.discord_configured() is False
    assert any("DISCORD-CROSS-APP-REFERENCE-REJECTED" in r.getMessage() for r in caplog.records)


def test_discord_accepts_hermes_owned_env(monkeypatch):
    import utils.discord_alerts as da
    _clear_discord_env(monkeypatch)
    key = "DISCORD_WEBHOOK_PROD" if env_config.ENV == "PROD" else "DISCORD_WEBHOOK_DEV"
    url = "https://discord.com/api/webhooks/123/HERMES-owned-token"
    monkeypatch.setenv(key, url)
    assert da.discord_configured() is True
    assert da.get_webhook_url() == url


def test_discord_missing_optional_metadata(monkeypatch):
    import utils.discord_alerts as da
    _clear_discord_env(monkeypatch)
    assert da.discord_configured() is False
    meta = da.discord_destination_metadata()
    assert meta["configured"] is False
    assert meta["source"] == "none"


def test_discord_metadata_never_contains_url(monkeypatch):
    import utils.discord_alerts as da
    _clear_discord_env(monkeypatch)
    key = "DISCORD_WEBHOOK_PROD" if env_config.ENV == "PROD" else "DISCORD_WEBHOOK_DEV"
    url = "https://discord.com/api/webhooks/123/HERMES-owned-token"
    monkeypatch.setenv(key, url)
    meta = da.discord_destination_metadata()
    assert url not in repr(meta)
    assert "HERMES-owned-token" not in repr(meta)
    assert meta["configured"] is True
    assert meta["source"] == "env"


# --------------------------------------------------------------------------- /status payload --------

class _FakeHealth:
    def to_dict(self):
        return {
            "state": "disconnected",          # legacy misleading value
            "is_healthy": False,
            "last_tick_at": "2026-07-29T00:00:00+00:00",
            "tick_count": 42,
            "error_count": 7,                 # historical cumulative
            "last_error": "old transient reconnect",
        }


class _FakeAdapter:
    def __init__(self):
        self.health = _FakeHealth()


class _FakeWatchdog:
    def get_health_snapshot(self):
        return {
            "stream_state": "FLOWING",
            "health_state": "GREEN",
            "instruments": {"XAU_USD": {"last_tick_utc": "2026-07-29T00:00:00+00:00",
                                        "health_state": "GREEN"}},
        }


class _FakeConfig:
    instruments = ["XAU_USD"]


class _FakeState:
    def __init__(self):
        self.watchdog = _FakeWatchdog()
        self.oanda_adapter = _FakeAdapter()
        self.ibkr_adapter = None
        self.started_at = None
        self.config = _FakeConfig()
        self.latest_ticks = {}
        self.publisher_supervisor = None

        class _Src:
            value = "oanda"
        self.active_source = _Src()


def _build_status_payload(monkeypatch):
    return build_status_payload(_FakeState())


def test_status_flowing_not_disconnected(monkeypatch):
    payload = _build_status_payload(monkeypatch)
    oanda = payload["adapters"]["oanda"]
    # Authoritative current state must NOT be "disconnected" when FLOWING.
    assert oanda["state"] != "disconnected"
    assert oanda["is_flowing"] is True
    assert oanda["current_stream_state"] == "FLOWING"
    # Legacy demoted to historical.
    assert oanda["legacy_adapter_state"] == "disconnected"
    assert oanda["historical_cumulative_error_count"] == 7
    assert oanda["historical_last_error_is_current"] is False


def test_status_exposes_build_identity_and_authoritative(monkeypatch):
    payload = _build_status_payload(monkeypatch)
    assert payload["build_identity"]["application"] == "HERMES"
    assert payload["authoritative_stream_state"] == "FLOWING"
    assert payload["authoritative_health"] == "GREEN"
    assert "config_version" in payload
    # Backward-compat fields still present.
    for k in ("service", "version", "started_at", "active_source", "instruments",
              "tick_count", "adapters"):
        assert k in payload


def test_status_no_secret_values(monkeypatch):
    monkeypatch.setenv("OANDA_API_KEY", _SECRET_VALUE)
    monkeypatch.setenv("DB_PASSWORD", _SECRET_VALUE)
    payload = _build_status_payload(monkeypatch)
    assert _SECRET_VALUE not in repr(payload)


# --------------------------------------------------------------------------- Healthcheck decode -----

def test_healthcheck_200_green():
    assert healthcheck_decode(200, "GREEN") == 0


def test_healthcheck_200_amber_tolerant():
    assert healthcheck_decode(200, "AMBER") == 0


def test_healthcheck_503_red():
    assert healthcheck_decode(503, "RED") == 1


def test_healthcheck_connection_refused():
    assert healthcheck_decode(None, None) == 1


def test_healthcheck_listening_but_dead():
    # Got a 200 response but no health contract in the body -> reject.
    assert healthcheck_decode(200, None) == 1
