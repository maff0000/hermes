"""Tests for the SIEM/Graylog GELF target resolver.
WO-HELM-HERMES-ENVIRONMENT-BLIND-ISOLATION-0003.

Verifies: SIEM_GRAYLOG_IP is canonical, GRAYLOG_HOST is a backward-compatible fallback (no boot
landmine for existing deployments), port defaults to 12201, and missing target fails loud (GOV-LOG-012).
"""
import os

from hermes_logging import resolve_gelf_target
from hermes_logging.logger import DEFAULT_GELF_PORT, GOV_LOG_SIEM_FAILLOUD

_VARS = ("SIEM_GRAYLOG_IP", "SIEM_GRAYLOG_PORT", "GRAYLOG_HOST", "GRAYLOG_PORT")


def _clear(monkeypatch):
    for v in _VARS:
        monkeypatch.delenv(v, raising=False)


def test_siem_ip_is_canonical(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("SIEM_GRAYLOG_IP", "10.0.0.9")
    assert resolve_gelf_target() == ("10.0.0.9", DEFAULT_GELF_PORT)


def test_siem_ip_wins_over_legacy_host(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("SIEM_GRAYLOG_IP", "10.0.0.9")
    monkeypatch.setenv("GRAYLOG_HOST", "192.168.11.10")
    assert resolve_gelf_target()[0] == "10.0.0.9"


def test_legacy_host_is_backward_compatible_fallback(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("GRAYLOG_HOST", "192.168.11.10")  # existing deployment, no SIEM_GRAYLOG_IP
    assert resolve_gelf_target() == ("192.168.11.10", DEFAULT_GELF_PORT)


def test_port_default_is_12201(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("SIEM_GRAYLOG_IP", "10.0.0.9")
    assert resolve_gelf_target()[1] == 12201


def test_siem_port_override(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("SIEM_GRAYLOG_IP", "10.0.0.9")
    monkeypatch.setenv("SIEM_GRAYLOG_PORT", "5555")
    assert resolve_gelf_target()[1] == 5555


def test_legacy_port_fallback(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("GRAYLOG_HOST", "192.168.11.10")
    monkeypatch.setenv("GRAYLOG_PORT", "12202")
    assert resolve_gelf_target()[1] == 12202


def test_no_target_fails_loud(monkeypatch):
    _clear(monkeypatch)
    try:
        resolve_gelf_target()
        assert False, "expected fail-loud when no SIEM target configured"
    except RuntimeError as e:
        assert GOV_LOG_SIEM_FAILLOUD in str(e)


def test_invalid_port_fails_loud(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("SIEM_GRAYLOG_IP", "10.0.0.9")
    monkeypatch.setenv("SIEM_GRAYLOG_PORT", "not-a-port")
    try:
        resolve_gelf_target()
        assert False, "expected fail-loud on invalid port"
    except RuntimeError as e:
        assert GOV_LOG_SIEM_FAILLOUD in str(e)


if __name__ == "__main__":
    import traceback
    p = 0
    for k, fn in sorted(globals().items()):
        if k.startswith("test_") and callable(fn):
            try:
                fn(_Env())  # type: ignore  # noqa
            except Exception:
                traceback.print_exc()
    print("run via pytest")
