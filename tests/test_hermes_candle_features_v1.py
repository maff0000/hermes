"""HERMES candle-features contract + publisher foundation + indicator convention metadata + control-plane status.
CODE-ONLY, zero I/O, no Redis. WO-HELM-HERMES-CANDLE-FEATURES-PUBLISHER-AND-INDICATOR-CONVENTIONS-0001.
"""
import json
from datetime import datetime, timezone

import pytest

import utils.hermes_candle_features_v1 as feat
import utils.hermes_indicators_v1 as ind
import utils.hermes_control_plane_v1 as cp

UTC = timezone.utc
_NOW = datetime(2026, 6, 30, 13, 0, tzinfo=UTC)
_OPEN = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)
_SRC_KEY = "hermes:candles:XAU_USD:H1:latest:v1"
_FEATURES = {"body_size": 5.2, "range_size": 9.4, "wick_high": 2.1, "wick_low": 2.1, "candle_direction": "UP",
             "body_to_range_ratio": 0.553, "close_position_in_range": 0.72, "doji": False, "full_body": False,
             "long_wick": False, "pin_bar": False}
_METHOD_CFG = {"doji_body_to_range_max": 0.1, "full_body_min_body_to_range": 0.7, "long_wick_ratio_min": 2.0}


# ============================ Part A — indicator convention metadata ============================
def test_indicator_payload_declares_methods():
    p = ind.build_indicator_contract(instrument="XAU_USD", timeframe="H1", generated_at_utc=_NOW,
                                     value_open_time_utc=_OPEN,
                                     indicators={"ema_12": 1.0, "ema_26": 1.0, "rsi_14": 50.0, "atr_14": 2.0})
    m = p["methods"]
    assert m["rsi_method"] in ("CUTLER_SMA_14", "SMA_14") and "SMA_14" in m["rsi_method"]   # SMA/Cutler-style
    assert m["atr_method"] == "SMA_14"
    assert m["ema_method"] == ind.INDICATOR_METHODS["ema"]
    assert ind.validate_indicator_contract(p) is True


def test_indicator_methods_only_for_present_families():
    p = ind.build_indicator_contract(instrument="XAU_USD", timeframe="M5", generated_at_utc=_NOW,
                                     value_open_time_utc=_OPEN, indicators={"rsi_14": 50.0})
    assert set(p["methods"]) == {"rsi_method"} and "atr_method" not in p["methods"]


def test_indicator_validate_requires_method_declaration():
    p = ind.build_indicator_contract(instrument="XAU_USD", timeframe="M5", generated_at_utc=_NOW,
                                     value_open_time_utc=_OPEN, indicators={"atr_14": 2.0})
    p["methods"] = {}                                   # strip method declaration
    with pytest.raises(ValueError) as e:
        ind.validate_indicator_contract(p)
    assert "GOV-HERMES-IND-017" in str(e.value)


# ============================ Part C+D — candle-feature contract + publisher ============================
def test_feature_key_versioned_xau_only():
    assert feat.candle_features_key("XAU_USD", "H4") == "hermes:candle_features:XAU_USD:H4:v1"
    for bad in ("XAUUSD", "EUR_USD"):
        with pytest.raises(ValueError) as e:
            feat.candle_features_key(bad, "H4")
        assert "GOV-HERMES-FEAT-004" in str(e.value)


def test_feature_contract_validates_deterministic():
    p = feat.build_candle_feature_contract(instrument="XAU_USD", timeframe="H1", generated_at_utc=_NOW,
                                           source_candle_key=_SRC_KEY, source_candle_open_time_utc=_OPEN,
                                           features=_FEATURES, method_config=_METHOD_CFG)
    assert p["deterministic_only"] is True and p["instrument"] == "XAU_USD" and p["timeframe"] == "H1"
    assert p["source_candle_key"] == _SRC_KEY and p["feature_set_version"] == "v1"
    assert p["generated_at_utc"].endswith("Z") and p["source_candle_open_time_utc"].endswith("Z")
    assert p["features"] == _FEATURES and p["method_config"] == _METHOD_CFG
    assert feat.validate_candle_feature_contract(p) is True


def test_feature_contract_xauusd_and_nonxau_rejected():
    for bad in ("XAUUSD", "EUR_USD"):
        with pytest.raises(ValueError) as e:
            feat.build_candle_feature_contract(instrument=bad, timeframe="H4", generated_at_utc=_NOW,
                                               source_candle_key=_SRC_KEY, source_candle_open_time_utc=_OPEN,
                                               features=_FEATURES)
        assert "GOV-HERMES-FEAT-004" in str(e.value)


@pytest.mark.parametrize("bad_feature", ["regime_label", "risk_state", "liquidity_intent", "order_block_meaning",
                                         "smart_money_flag", "trade_signal", "go_no_go", "decision"])
def test_ares_interpretive_features_rejected(bad_feature):
    with pytest.raises(ValueError) as e:
        feat.build_candle_feature_contract(instrument="XAU_USD", timeframe="M5", generated_at_utc=_NOW,
                                           source_candle_key=_SRC_KEY, source_candle_open_time_utc=_OPEN,
                                           features={**_FEATURES, bad_feature: 1})
    assert "GOV-HERMES-FEAT-003" in str(e.value)


def test_feature_publisher_disabled_by_default(monkeypatch):
    monkeypatch.delenv(feat.ENABLED_ENV, raising=False)
    p = feat.build_candle_feature_publisher_from_env()
    assert isinstance(p, feat.DisabledCandleFeaturePublisher) and p.enabled is False


def test_feature_publisher_enabled_without_authorised_halts_101(monkeypatch):
    monkeypatch.setenv(feat.ENABLED_ENV, "true")
    monkeypatch.delenv(feat.AUTHORISED_ENV, raising=False)
    with pytest.raises(SystemExit) as e:
        feat.build_candle_feature_publisher_from_env()
    assert e.value.code == 101


def test_feature_publisher_enabled_authorised_builds_no_redis(monkeypatch):
    monkeypatch.setenv(feat.ENABLED_ENV, "true"); monkeypatch.setenv(feat.AUTHORISED_ENV, "true")
    monkeypatch.setenv(feat.INSTRUMENTS_ENV, "XAU_USD"); monkeypatch.setenv(feat.TIMEFRAMES_ENV, "M1,M5,M15,H1,H4")
    p = feat.build_candle_feature_publisher_from_env()
    assert isinstance(p, feat.CandleFeaturePublisher) and "D1" not in p.status()["timeframes"]
    assert p.key("XAU_USD", "H4") == "hermes:candle_features:XAU_USD:H4:v1" and not hasattr(p, "redis_client")


def test_feature_d1_gated_until_d1_green(monkeypatch):
    with pytest.raises(ValueError) as e:
        feat.parse_feature_timeframes("M1,H4,D1")
    assert "GOV-HERMES-FEAT-D1-001" in str(e.value)
    assert feat.parse_feature_timeframes("M1,H4,D1", allow_d1=True) == ("M1", "H4", "D1")


def test_no_redis_io_at_import():
    src = open(feat.__file__).read()
    assert "import redis" not in src and "redis.Redis(" not in src
    assert ".zadd(" not in src and ".setex(" not in src
    assert "requests" not in src and "import socket" not in src and "urllib" not in src and "pymysql" not in src
    assert "open(" not in src


def test_no_auth_added():
    src = open(feat.__file__).read().lower()
    for marker in ("password=", "requirepass", ".auth(", "acl setuser", "username=", "ssl="):
        assert marker not in src


def test_feature_payload_no_regime_risk_decision_and_legacy_not_touched():
    p = feat.build_candle_feature_contract(instrument="XAU_USD", timeframe="M15", generated_at_utc=_NOW,
                                           source_candle_key=_SRC_KEY, source_candle_open_time_utc=_OPEN,
                                           features=_FEATURES, method_config=_METHOD_CFG)
    def keys(o, acc):
        if isinstance(o, dict):
            for k, v in o.items(): acc.append(str(k).lower()); keys(v, acc)
        elif isinstance(o, (list, tuple)):
            for x in o: keys(x, acc)
        return acc
    for k in keys(p, []):
        for tok in ("regime", "risk", "decision", "trade", "signal", "order_block", "liquidity", "smart_money"):
            assert tok not in k
    src = open(feat.__file__).read()
    assert "hermes:signals" not in src and "market_map" not in src and "regime_detector" not in src


# ============================ Part E — control-plane candle_features status ============================
def test_control_plane_candle_features_built_not_active():
    h = cp.build_health_summary(generated_at_utc=_NOW, control_plane_active=True, candle_features_built=True)
    assert h["per_family_health"]["candle_features"] == "BUILT_NOT_ACTIVE"
    assert "candle_features" in h["missing_but_expected_families"]
    assert cp.validate_health(h) is True


def test_control_plane_candle_features_active():
    h = cp.build_health_summary(generated_at_utc=_NOW, control_plane_active=True, candle_features_active=True)
    assert h["per_family_health"]["candle_features"] == "ACTIVE"
    assert "candle_features" not in h["missing_but_expected_families"]
    assert cp.validate_health(h) is True


def test_control_plane_candle_features_default_not_implemented():
    h = cp.build_health_summary(generated_at_utc=_NOW)
    assert h["per_family_health"]["candle_features"] == "NOT_IMPLEMENTED"
    assert "candle_features" in h["missing_but_expected_families"]
