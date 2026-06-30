"""HERMES deterministic session + level contracts/publishers (market_map containment design).
CODE-ONLY, zero I/O, no Redis. WO-HELM-HERMES-MARKET-MAP-CONTAINMENT-SESSION-LEVELS-INVENTORY-0001.
"""
from datetime import datetime, timezone

import pytest

import utils.hermes_sessions_v1 as sess
import utils.hermes_levels_v1 as lvl

UTC = timezone.utc
_NOW = datetime(2026, 6, 30, 14, 0, tzinfo=UTC)
_SESSIONS = {"asia": {"open_time_utc": "22:00", "close_time_utc": "07:00"},
             "london": {"open_time_utc": "07:00", "close_time_utc": "16:00"},
             "newyork": {"open_time_utc": "12:00", "close_time_utc": "21:00"}}
_SESSION_LEVELS = {"asia_high": 4082.5, "asia_low": 4070.1, "london_high": 4090.0, "london_low": 4075.2,
                   "range_midpoint": 4080.05}


# ============================ sessions ============================
def test_session_key_versioned_xau_only():
    assert sess.sessions_key("XAU_USD") == "hermes:sessions:XAU_USD:v1"
    for bad in ("XAUUSD", "EUR_USD"):
        with pytest.raises(ValueError) as e:
            sess.sessions_key(bad)
        assert "GOV-HERMES-SESS-004" in str(e.value)


def test_session_contract_validates_deterministic():
    p = sess.build_session_contract(instrument="XAU_USD", generated_at_utc=_NOW, current_session="london",
                                    sessions=_SESSIONS)
    assert p["deterministic_only"] is True and p["instrument"] == "XAU_USD"
    assert p["current_session"] == "london" and p["sessions"] == _SESSIONS
    assert p["generated_at_utc"].endswith("Z") and sess.validate_session_contract(p) is True


def test_session_publisher_gating(monkeypatch):
    monkeypatch.delenv(sess.ENABLED_ENV, raising=False)
    assert isinstance(sess.build_session_publisher_from_env(), sess.DisabledSessionPublisher)
    monkeypatch.setenv(sess.ENABLED_ENV, "true"); monkeypatch.delenv(sess.AUTHORISED_ENV, raising=False)
    with pytest.raises(SystemExit) as e:
        sess.build_session_publisher_from_env()
    assert e.value.code == 101


def test_session_no_regime_risk_decision_rejected():
    with pytest.raises(ValueError) as e:
        sess.build_session_contract(instrument="XAU_USD", generated_at_utc=_NOW, current_session="asia",
                                    sessions={**_SESSIONS, "regime_label": "TRENDING"})
    assert "GOV-HERMES-SESS-003" in str(e.value)


# ============================ levels ============================
def test_level_key_versioned_scoped_xau_only():
    assert lvl.levels_key("XAU_USD", "session") == "hermes:levels:XAU_USD:session:v1"
    for bad in ("XAUUSD", "EUR_USD"):
        with pytest.raises(ValueError) as e:
            lvl.levels_key(bad, "session")
        assert "GOV-HERMES-LVL-004" in str(e.value)


def test_session_scope_level_validates_deterministic():
    p = lvl.build_level_contract(instrument="XAU_USD", scope="session", generated_at_utc=_NOW, levels=_SESSION_LEVELS)
    assert p["deterministic_only"] is True and p["scope"] == "session" and p["d1_derived"] is False
    assert p["levels"] == _SESSION_LEVELS and lvl.validate_level_contract(p) is True


def test_daily_scope_d1_derived_gated_until_green():
    # D1-derived daily scope requires d1_latest_green
    with pytest.raises(ValueError) as e:
        lvl.build_level_contract(instrument="XAU_USD", scope="daily", generated_at_utc=_NOW,
                                 levels={"prior_day_high": 4090.0, "prior_day_low": 4060.0, "adr_20": 28.5})
    assert "GOV-HERMES-LVL-D1-001" in str(e.value)
    # allowed when D1 latest GREEN
    p = lvl.build_level_contract(instrument="XAU_USD", scope="daily", generated_at_utc=_NOW,
                                 levels={"prior_day_high": 4090.0, "prior_day_low": 4060.0, "adr_20": 28.5},
                                 d1_latest_green=True)
    assert p["d1_derived"] is True and lvl.validate_level_contract(p) is True


def test_level_scope_parse_d1_gated():
    with pytest.raises(ValueError) as e:
        lvl.parse_level_scopes("session,daily")          # daily D1-derived, default allow_d1=False
    assert "GOV-HERMES-LVL-D1-001" in str(e.value)
    assert lvl.parse_level_scopes("session,intraday") == ("session", "intraday")
    assert lvl.parse_level_scopes("session,daily,weekly", allow_d1=True) == ("session", "daily", "weekly")


def test_level_publisher_gating(monkeypatch):
    monkeypatch.delenv(lvl.ENABLED_ENV, raising=False)
    assert isinstance(lvl.build_level_publisher_from_env(), lvl.DisabledLevelPublisher)
    monkeypatch.setenv(lvl.ENABLED_ENV, "true"); monkeypatch.delenv(lvl.AUTHORISED_ENV, raising=False)
    with pytest.raises(SystemExit) as e:
        lvl.build_level_publisher_from_env()
    assert e.value.code == 101


def test_level_publisher_d1_scope_gated_from_env(monkeypatch):
    monkeypatch.setenv(lvl.ENABLED_ENV, "true"); monkeypatch.setenv(lvl.AUTHORISED_ENV, "true")
    monkeypatch.setenv(lvl.INSTRUMENTS_ENV, "XAU_USD"); monkeypatch.setenv(lvl.SCOPES_ENV, "session,daily")
    monkeypatch.delenv(lvl.D1_AUTHORISED_ENV, raising=False)
    with pytest.raises(ValueError) as e:
        lvl.build_level_publisher_from_env()
    assert "GOV-HERMES-LVL-D1-001" in str(e.value)


@pytest.mark.parametrize("bad", ["regime", "risk_state", "liquidity_block", "order_block_meaning",
                                 "smart_money_zone", "decision", "trade_bias", "setup_quality"])
def test_level_interpretive_fields_rejected(bad):
    with pytest.raises(ValueError) as e:
        lvl.build_level_contract(instrument="XAU_USD", scope="session", generated_at_utc=_NOW,
                                 levels={**_SESSION_LEVELS, bad: 1})
    assert "GOV-HERMES-LVL-003" in str(e.value)


# ============================ no I/O / no auth / module hygiene ============================
@pytest.mark.parametrize("mod", [sess, lvl])
def test_no_redis_io_no_auth_at_import(mod):
    src = open(mod.__file__).read()
    assert "import redis" not in src and "redis.Redis(" not in src and ".zadd(" not in src
    assert "requests" not in src and "import socket" not in src and "urllib" not in src and "pymysql" not in src
    assert "open(" not in src
    low = src.lower()
    for marker in ("password=", "requirepass", ".auth(", "acl setuser", "username=", "ssl="):
        assert marker not in low


@pytest.mark.parametrize("mod", [sess, lvl])
def test_no_regime_detector_or_legacy_mutation(mod):
    src = open(mod.__file__).read()
    # no ARES regime logic; no legacy mutation (docstrings may NAME market_map.py / the legacy keys as the
    # containment target; the no-Redis-I/O test proves there is no actual key write).
    assert "regime_detector" not in src and "hermes:signals" not in src
    assert "import market_map" not in src and "set_market_map" not in src and ".publish(" not in src
