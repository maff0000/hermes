"""HERMES deterministic indicator contract + publisher foundation + control-plane health self-listing fix.
CODE-ONLY, zero I/O, no Redis. WO-HELM-HERMES-INDICATOR-PUBLISHER-AND-CONTROL-PLANE-FIX-0001.
"""
import json
from datetime import datetime, timezone

import pytest

import utils.hermes_indicators_v1 as ind
import utils.hermes_control_plane_v1 as cp

UTC = timezone.utc
_NOW = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)
_OPEN = datetime(2026, 6, 30, 11, 0, tzinfo=UTC)
_SAMPLE = {"ema_12": 4080.5, "ema_26": 4075.25, "rsi_14": 56.3, "atr_14": 3.42}


# ============================ Part A — control-plane health self-listing fix ============================
def test_health_self_listing_fixed_when_control_plane_active():
    h = cp.build_health_summary(generated_at_utc=_NOW, control_plane_active=True)
    pf = h["per_family_health"]
    for fam in ("contract_manifest", "publisher_heartbeat", "candle_catalog", "health_summary"):
        assert pf[fam] == "ACTIVE"
        assert fam not in h["missing_but_expected_families"]   # no longer self-listed as missing
    assert pf["control_plane"] == "ACTIVE" and h["control_plane_health"] == "ACTIVE"
    assert h["control_plane_active"] is True


def test_health_keeps_genuinely_missing_when_active():
    h = cp.build_health_summary(generated_at_utc=_NOW, control_plane_active=True)
    for fam in ("indicators", "candle_features", "feed_health", "instrument_catalog", "sessions",
                "candle_history_d1", "ticks"):
        assert fam in h["missing_but_expected_families"]
    assert h["per_family_health"]["candle_history_d1"] == "BLOCKED_UNTIL_D1_LATEST_GREEN"


def test_health_default_inactive_still_lists_control_plane():
    h = cp.build_health_summary(generated_at_utc=_NOW)        # default control_plane_active=False
    assert h["control_plane_health"] == "PENDING"
    for fam in ("contract_manifest", "publisher_heartbeat", "candle_catalog", "health_summary"):
        assert fam in h["missing_but_expected_families"]


def test_indicators_built_not_active_never_active():
    h = cp.build_health_summary(generated_at_utc=_NOW, control_plane_active=True, indicators_built=True)
    assert h["per_family_health"]["indicators"] == "BUILT_NOT_ACTIVE"   # built but NOT falsely ACTIVE
    assert "indicators" in h["missing_but_expected_families"]
    assert cp.validate_health(h) is True


def test_heartbeat_ttl_policy_represented():
    p = cp.heartbeat_ttl_policy()
    assert p["heartbeat_ttl_seconds"] > p["heartbeat_refresh_seconds"]   # TTL > refresh (liveness)
    assert p["heartbeat_key"] == "hermes:publisher:heartbeat:v1"
    assert cp.KEY_CONTRACT_MANIFEST in p["persistent_keys_no_ttl"]
    assert cp.HEARTBEAT_TTL_SECONDS == 180 and cp.HEARTBEAT_REFRESH_SECONDS == 60


# ============================ Part D — indicator publisher gating ============================
def test_publisher_disabled_by_default(monkeypatch):
    monkeypatch.delenv(ind.ENABLED_ENV, raising=False)
    p = ind.build_indicator_publisher_from_env()
    assert isinstance(p, ind.DisabledIndicatorPublisher) and p.enabled is False
    assert p.status() == {"enabled": False}


def test_publisher_enabled_without_authorised_halts_101(monkeypatch):
    monkeypatch.setenv(ind.ENABLED_ENV, "true")
    monkeypatch.delenv(ind.AUTHORISED_ENV, raising=False)
    with pytest.raises(SystemExit) as e:
        ind.build_indicator_publisher_from_env()
    assert e.value.code == 101


def test_publisher_enabled_authorised_builds_no_redis(monkeypatch):
    monkeypatch.setenv(ind.ENABLED_ENV, "true")
    monkeypatch.setenv(ind.AUTHORISED_ENV, "true")
    monkeypatch.setenv(ind.INSTRUMENTS_ENV, "XAU_USD")
    monkeypatch.setenv(ind.TIMEFRAMES_ENV, "M1,M5,M15,H1,H4")
    p = ind.build_indicator_publisher_from_env()
    assert isinstance(p, ind.IndicatorPublisher) and p.enabled is True
    assert p.status()["instruments"] == ["XAU_USD"] and "D1" not in p.status()["timeframes"]
    pay = p.build(instrument="XAU_USD", timeframe="H4", generated_at_utc=_NOW, value_open_time_utc=_OPEN,
                  indicators=_SAMPLE)
    assert pay["publisher"] == "HERMES" and not hasattr(p, "redis_client")


def test_no_redis_io_at_import():
    src = open(ind.__file__).read()
    assert "import redis" not in src and "redis.Redis(" not in src
    assert ".zadd(" not in src and ".setex(" not in src
    assert "requests" not in src and "import socket" not in src and "urllib" not in src and "pymysql" not in src
    assert "open(" not in src


def test_no_auth_added():
    # no AUTHENTICATION IMPLEMENTATION (the module's forbidden-token DENYLIST that rejects auth fields is defense,
    # not auth). Assert no redis auth args / ACL / requirepass / .auth() calls are introduced.
    src = open(ind.__file__).read().lower()
    for marker in ("password=", "requirepass", ".auth(", "acl setuser", "username=", "ssl="):
        assert marker not in src


# ============================ Part C — indicator contract ============================
def test_indicator_key_versioned_xau_only():
    assert ind.indicator_key("XAU_USD", "H4") == "hermes:indicators:XAU_USD:H4:v1"
    for bad in ("XAUUSD", "EUR_USD", "XAG_USD"):
        with pytest.raises(ValueError) as e:
            ind.indicator_key(bad, "H4")
        assert "GOV-HERMES-IND-004" in str(e.value)


def test_indicator_contract_validates_deterministic():
    p = ind.build_indicator_contract(instrument="XAU_USD", timeframe="H1", generated_at_utc=_NOW,
                                     value_open_time_utc=_OPEN, indicators=_SAMPLE)
    assert p["deterministic_only"] is True and p["instrument"] == "XAU_USD"
    assert p["source_candle_contract"] == "v1" and p["source_timeframes"] == ["H1"]
    assert p["indicator_set_version"] == "v1" and p["indicators"] == _SAMPLE
    assert p["generated_at_utc"].endswith("Z") and p["value_open_time_utc"].endswith("Z")
    assert ind.validate_indicator_contract(p) is True


def test_indicator_contract_xauusd_and_nonxau_rejected():
    for bad in ("XAUUSD", "EUR_USD"):
        with pytest.raises(ValueError) as e:
            ind.build_indicator_contract(instrument=bad, timeframe="H4", generated_at_utc=_NOW,
                                         value_open_time_utc=_OPEN, indicators=_SAMPLE)
        assert "GOV-HERMES-IND-004" in str(e.value)


def test_d1_indicators_gated_until_d1_green():
    with pytest.raises(ValueError) as e:
        ind.parse_indicator_timeframes("M1,H4,D1")              # default allow_d1=False
    assert "GOV-HERMES-IND-D1-001" in str(e.value)
    assert ind.parse_indicator_timeframes("M1,H4,D1", allow_d1=True) == ("M1", "H4", "D1")


def test_d1_indicator_publisher_gated_from_env(monkeypatch):
    monkeypatch.setenv(ind.ENABLED_ENV, "true"); monkeypatch.setenv(ind.AUTHORISED_ENV, "true")
    monkeypatch.setenv(ind.INSTRUMENTS_ENV, "XAU_USD")
    monkeypatch.setenv(ind.TIMEFRAMES_ENV, "M1,H4,D1")
    monkeypatch.delenv(ind.D1_AUTHORISED_ENV, raising=False)    # D1 not authorised
    with pytest.raises(ValueError) as e:
        ind.build_indicator_publisher_from_env()
    assert "GOV-HERMES-IND-D1-001" in str(e.value)


# ============================ no regime / risk / decision ; deterministic only ============================
def test_indicator_payload_no_regime_risk_decision_fields():
    p = ind.build_indicator_contract(instrument="XAU_USD", timeframe="M5", generated_at_utc=_NOW,
                                     value_open_time_utc=_OPEN, indicators=_SAMPLE)
    def keys(o, acc):
        if isinstance(o, dict):
            for k, v in o.items(): acc.append(str(k).lower()); keys(v, acc)
        elif isinstance(o, (list, tuple)):
            for x in o: keys(x, acc)
        return acc
    for k in keys(p, []):
        for tok in ("regime", "regime_confidence", "risk", "order_block", "liquidity", "decision", "trade"):
            assert tok not in k


def test_forbidden_indicator_field_rejected():
    with pytest.raises(ValueError) as e:
        ind.build_indicator_contract(instrument="XAU_USD", timeframe="M5", generated_at_utc=_NOW,
                                     value_open_time_utc=_OPEN, indicators={"regime_confidence": 0.9})
    assert "GOV-HERMES-IND-003" in str(e.value)


def test_indicators_module_does_not_pull_in_regime_or_signals():
    src = open(ind.__file__).read()
    assert "regime_detector" not in src and "regime_classifications" not in src   # ARES-owned not pulled in
    assert "hermes:signals" not in src and "market_map" not in src                # legacy not touched/mutated
    # deterministic indicator set is HERMES-owned
    assert "ema" in ind.HERMES_DETERMINISTIC_INDICATORS and "atr" in ind.HERMES_DETERMINISTIC_INDICATORS
