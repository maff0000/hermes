"""HERMES Redis control-plane v1 — manifest / heartbeat / catalog / health. CODE-ONLY, zero I/O, no Redis.
WO-HELM-HERMES-REDIS-CONTROL-PLANE-MANIFEST-HEALTH-0001.
"""
import json
from datetime import datetime, timezone

import pytest

import utils.hermes_control_plane_v1 as cp

UTC = timezone.utc
_NOW = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)


# ============================ gating ============================
def test_disabled_by_default_no_op(monkeypatch):
    monkeypatch.delenv(cp.ENABLED_ENV, raising=False)
    obj = cp.build_control_plane_from_env()
    assert isinstance(obj, cp.DisabledControlPlane) and obj.enabled is False
    assert obj.status() == {"enabled": False}


def test_enabled_without_authorised_terminal_halt_101(monkeypatch):
    monkeypatch.setenv(cp.ENABLED_ENV, "true")
    monkeypatch.delenv(cp.AUTHORISED_ENV, raising=False)
    with pytest.raises(SystemExit) as e:
        cp.build_control_plane_from_env()
    assert e.value.code == 101


def test_enabled_authorised_builds_payloads_no_redis(monkeypatch):
    monkeypatch.setenv(cp.ENABLED_ENV, "true")
    monkeypatch.setenv(cp.AUTHORISED_ENV, "true")
    b = cp.build_control_plane_from_env()
    assert isinstance(b, cp.ControlPlaneBuilder) and b.enabled is True
    # builds all four payloads with no Redis client / no I/O
    assert b.manifest(generated_at_utc=_NOW)["publisher"] == "HERMES"
    assert b.heartbeat(updated_at_utc=_NOW)["status"] == "OK"
    assert b.candle_catalog(generated_at_utc=_NOW)["contract_version"] == "v1"
    assert b.health(generated_at_utc=_NOW)["overall_status"] == "OK"
    assert not hasattr(b, "redis_client")


def test_no_redis_io_at_import():
    # importing the module must not create any redis client / connection / network / sql / file I/O
    import importlib
    mod = importlib.import_module("utils.hermes_control_plane_v1")
    src = open(mod.__file__).read()
    assert "import redis" not in src and "redis.Redis(" not in src      # no redis client
    assert ".zadd(" not in src and ".setex(" not in src                 # no redis write calls
    assert "requests" not in src and "import socket" not in src and "urllib" not in src  # no network
    assert "pymysql" not in src and "sqlite" not in src                 # no SQL
    assert "open(" not in src                                            # no file I/O in the module itself


# ============================ manifest ============================
def test_manifest_core_identity_and_instruments():
    m = cp.build_contract_manifest(generated_at_utc=_NOW, environment="dev", run_env="STAGING",
                                   deployed_sha="c9eb848", service_identity="hermes-signal")
    assert m["publisher"] == "HERMES" and m["contract_version"] == "v1"
    assert m["canonical_instruments"] == ["XAU_USD"]
    assert m["generated_at_utc"].endswith("Z")


def test_manifest_active_latest_and_history():
    m = cp.build_contract_manifest(generated_at_utc=_NOW)
    for tf in ("M1", "M5", "M15", "H1", "H4"):
        assert m["active_families"]["candle_latest"][tf] == "ACTIVE"
        assert m["active_families"]["candle_history"][tf] == "ACTIVE"


def test_manifest_d1_latest_pending_history_blocked():
    m = cp.build_contract_manifest(generated_at_utc=_NOW)
    assert m["gated_families"]["candle_latest_d1"]["status"] == "PENDING_FIRST_DAILY_SEAL"
    assert m["blocked_families"]["candle_history_d1"]["status"] == "BLOCKED_UNTIL_D1_LATEST_GREEN"


def test_manifest_not_implemented_and_legacy_and_ownership():
    m = cp.build_contract_manifest(generated_at_utc=_NOW)
    ni = m["not_implemented_families"]
    assert ni["indicators"] == "NOT_IMPLEMENTED" and ni["candle_features"] == "NOT_IMPLEMENTED"
    assert ni["sessions"] in ("NOT_IMPLEMENTED", "OWNERSHIP_PENDING")
    assert ni["feed_health"] in ("NOT_IMPLEMENTED", "INVENTORY_PENDING")
    leg = m["legacy_deprecated_families"]
    assert leg["hermes:signals:*"] == "FROZEN_PENDING_CONSUMER_CUTOVER"
    assert leg["hermes:market_map:*"] == "FROZEN_PENDING_CONSUMER_CUTOVER"
    # no regime ownership declared by HERMES
    assert "regime" in str(m["ownership_boundaries"]["ares_owns"]).lower()
    assert "NO regime ownership".lower() in m["interpretive_ownership_note"].lower()
    # source policies advertised
    assert m["source_policies"]["h4"] == "H4_FROM_H1" and m["source_policies"]["d1"] == "D1_FROM_6_OK_H4"


def test_manifest_validates_and_no_regime_data_field():
    m = cp.build_contract_manifest(generated_at_utc=_NOW)
    assert cp.validate_manifest(m) is True
    # no DATA field KEY names regime/risk (ownership TEXT values may name them)
    def keys(o):
        out = []
        if isinstance(o, dict):
            for k, v in o.items():
                out.append(str(k).lower()); out += keys(v)
        elif isinstance(o, (list, tuple)):
            for x in o: out += keys(x)
        return out
    for k in keys(m):
        assert "regime" not in k and "risk" not in k


# ============================ heartbeat ============================
def test_heartbeat_required_fields_and_utc():
    hb = cp.build_publisher_heartbeat(updated_at_utc=_NOW, deployed_sha="c9eb848", service_identity="hermes-signal",
                                      redis_target=cp.redact_redis_target("192.168.11.10", 6379, 0),
                                      status="OK", active_timeframes=["M1", "M5", "M15", "H1", "H4"],
                                      last_publish_utc={"H4": cp._utc(_NOW)},
                                      fault_counters_summary={"h4_emit_fail": 0, "d1_hook_fail": 0},
                                      history_forward_state="ACTIVE", d1_state="PENDING_FIRST_DAILY_SEAL")
    assert hb["updated_at_utc"].endswith("Z") and hb["generated_at_utc"].endswith("Z")
    assert hb["deployed_sha"] == "c9eb848" and hb["service_identity"] == "hermes-signal"
    assert hb["fault_counters_summary"] == {"h4_emit_fail": 0, "d1_hook_fail": 0}
    assert hb["status"] == "OK" and "@" not in hb["redis_target"]
    assert cp.validate_heartbeat(hb) is True


def test_heartbeat_status_constrained():
    for bad in ("GREEN", "DOWN", "ok"):
        with pytest.raises(ValueError):
            cp.build_publisher_heartbeat(updated_at_utc=_NOW, status=bad)
    for good in ("OK", "WARN", "FAIL"):
        assert cp.build_publisher_heartbeat(updated_at_utc=_NOW, status=good)["status"] == good


def test_heartbeat_redis_target_redaction():
    with pytest.raises(ValueError):
        cp.redact_redis_target("user:password@host", 6379, 0)


# ============================ candle catalog ============================
def test_catalog_latest_history_patterns_and_policies():
    cat = cp.build_candle_catalog(generated_at_utc=_NOW)
    tfs = cat["timeframes"]
    for tf in ("M1", "M5", "M15", "H1", "H4", "D1"):
        assert tfs[tf]["latest_key"] == f"hermes:candles:XAU_USD:{tf}:latest:v1"
        assert tfs[tf]["history_key_pattern"] == f"hermes:candles:XAU_USD:{tf}:history:v1:{{open_epoch}}"
        assert tfs[tf]["history_index_key"] == f"hermes:candles:XAU_USD:{tf}:history:v1:index"
        assert tfs[tf]["ttl_seconds_history"] == 3024000
    assert tfs["H4"]["source_timeframe"] == "H1" and tfs["H4"]["derivation_policy"] == "DERIVED_H4_FROM_H1"
    assert tfs["D1"]["source_timeframe"] == "H4" and tfs["D1"]["expected_source_count"] == 6


def test_catalog_d1_pending_blocked_and_active_others():
    cat = cp.build_candle_catalog(generated_at_utc=_NOW)
    tfs = cat["timeframes"]
    for tf in ("M1", "M5", "M15", "H1", "H4"):
        assert tfs[tf]["latest_status"] == "ACTIVE" and tfs[tf]["history_status"] == "ACTIVE"
    assert tfs["D1"]["latest_status"] == "PENDING_FIRST_DAILY_SEAL"
    assert tfs["D1"]["history_status"] == "BLOCKED_UNTIL_D1_LATEST_GREEN"
    assert cp.validate_candle_catalog(cat) is True


# ============================ health ============================
def test_health_overall_and_per_family_and_absence_distinction():
    h = cp.build_health_summary(generated_at_utc=_NOW)
    assert h["overall_status"] in ("OK", "WARN", "FAIL")
    pf = h["per_family_health"]
    assert pf["candle_latest"] == "ACTIVE" and pf["candle_history"] == "ACTIVE"
    assert pf["candle_latest_d1"] == "PENDING_FIRST_DAILY_SEAL"
    assert pf["candle_history_d1"] == "BLOCKED_UNTIL_D1_LATEST_GREEN"
    assert pf["indicators"] == "NOT_IMPLEMENTED" and pf["candle_features"] == "NOT_IMPLEMENTED"
    # distinguishes missing-but-expected from intentionally-absent and legacy
    assert "indicators" in h["missing_but_expected_families"]
    assert any("regime" in s.lower() for s in h["intentionally_absent_families"])
    assert "hermes:signals:*" in h["legacy_deprecated_families"]


def test_health_no_regime_or_risk_fields():
    h = cp.build_health_summary(generated_at_utc=_NOW)
    assert cp.validate_health(h) is True
    def keys(o):
        out = []
        if isinstance(o, dict):
            for k, v in o.items():
                out.append(str(k).lower()); out += keys(v)
        elif isinstance(o, (list, tuple)):
            for x in o: out += keys(x)
        return out
    for k in keys(h):
        assert "regime" not in k and "risk" not in k and "order_block" not in k


def test_forbidden_field_key_rejected():
    h = cp.build_health_summary(generated_at_utc=_NOW)
    h["regime_label"] = "TRENDING"          # inject a forbidden DATA field key
    with pytest.raises(ValueError) as e:
        cp.validate_health(h)
    assert "GOV-HERMES-CP-003" in str(e.value)


def test_no_auth_tokens_anywhere():
    payloads = [cp.build_contract_manifest(generated_at_utc=_NOW),
                cp.build_publisher_heartbeat(updated_at_utc=_NOW),
                cp.build_candle_catalog(generated_at_utc=_NOW),
                cp.build_health_summary(generated_at_utc=_NOW)]
    blob = json.dumps(payloads).lower()
    for tok in ("password", "secret", "credential", "noauth", "acl", "api_key"):
        assert tok not in blob
