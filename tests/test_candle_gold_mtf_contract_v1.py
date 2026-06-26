"""GOLD MTF candle-contract extension — M1/M5/M15/H1 DIRECT-NATIVE grid + fixed wick semantics.
WO-HELM-HERMES-GOLD-MTF-CANDLE-CONTRACT-EXTEND-0001.

Shadow-first, code-only. No live Redis/SQL, no backfill, no activation. In-memory fake Redis only.

Proves:
  * the contract recognises and TTLs M1/M5/M15/H1 (and the deferred H4/D),
  * the deterministic geometry block (body/range/wick/direction) is present + correct,
  * wick semantics are FIXED: wick_high = high-body_high (NOT the high price), wick_low = body_low-low,
  * the validator recomputes the geometry and fails loud on aliasing / tampering,
  * the shadow seam writes ONLY versioned shadow keys for M1/M5/M15/H1 and skips D1/H4/D,
  * XAUUSD is canonicalised to XAU_USD with no dual-publish,
  * no interpretive/regime field can leak (forbidden-token scan).
"""
import json
from datetime import datetime, timedelta, timezone

from utils import candle_contract_v1 as cc
from utils import candle_runtime_seam_v1 as seam
from utils import candle_publisher_v1 as cp

_TS = datetime(2026, 6, 26, 8, 0, tzinfo=timezone.utc)
_DIRECT_GRID = ("M1", "M5", "M15", "H1")
_EXPECTED_TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600}
_EXPECTED_EX = {"M1": 90, "M5": 360, "M15": 1080, "H1": 3900}


def _direct(tf="M5", *, instrument="XAU_USD", o=2000.0, h=2010.0, low=1995.0, c=2005.0,
            is_closed=True, market_open=True):
    gen = _TS + timedelta(seconds=cc.TF_SECONDS[tf] + 0.5)
    return cc.build_candle_contract(
        instrument=instrument, timeframe=tf, timestamp_utc=_TS,
        ohlc={"open": o, "high": h, "low": low, "close": c, "volume": 100},
        is_closed=is_closed, generated_at_utc=gen, source_timeframe=tf,
        source_count=1, expected_source_count=1,
        derivation=cc.DERIVATION_DIRECT, derivation_policy=cc.DERIVATION_POLICY_DIRECT,
        source_policy_epoch="DIRECT_NATIVE_V1", market_open=market_open)


# ---------------- timeframe grid ----------------
def test_direct_native_grid_recognised_and_ttld():
    assert all(tf in cc.TIMEFRAMES for tf in _DIRECT_GRID)
    for tf in _DIRECT_GRID:
        assert cc.TF_SECONDS[tf] == _EXPECTED_TF_SECONDS[tf]
        assert cc.redis_ex_seconds(tf) == _EXPECTED_EX[tf]


def test_each_grid_tf_builds_and_validates():
    for tf in _DIRECT_GRID:
        p = _direct(tf)
        assert cc.validate_candle_contract(p) is True
        assert p["ttl_seconds"] == _EXPECTED_TF_SECONDS[tf]
        assert p["key"] == f"hermes:candles:XAU_USD:{tf}:latest:v1"
        assert p["data"]["timeframe"] == tf


# ---------------- geometry: correct + present ----------------
def test_geometry_block_present_and_correct_bullish():
    d = _direct("M5", o=2000.0, h=2010.0, low=1995.0, c=2005.0)["data"]
    assert d["body_high"] == 2005.0 and d["body_low"] == 2000.0      # max/min(open,close)
    assert d["body_size"] == 5.0                                     # abs(close-open)
    assert d["range_size"] == 15.0                                   # high-low
    assert d["wick_high"] == 5.0                                     # high-body_high (2010-2005)
    assert d["wick_low"] == 5.0                                      # body_low-low (2000-1995)
    assert d["candle_direction"] == "UP"


def test_geometry_bearish_and_flat_directions():
    down = _direct("M5", o=2005.0, h=2010.0, low=1995.0, c=2000.0)["data"]
    assert down["candle_direction"] == "DOWN"
    assert down["body_high"] == 2005.0 and down["body_low"] == 2000.0
    flat = _direct("M15", o=2000.0, h=2003.0, low=1998.0, c=2000.0)["data"]
    assert flat["candle_direction"] == "FLAT"
    assert flat["body_size"] == 0.0
    assert flat["wick_high"] == 3.0 and flat["wick_low"] == 2.0


# ---------------- the wick-semantics fix: NO high/low aliasing ----------------
def test_wick_high_is_not_the_high_price():
    d = _direct("H1", o=2000.0, h=2010.0, low=1995.0, c=2005.0)["data"]
    assert d["wick_high"] != d["high"]          # wick_high is a SIZE, never the high price
    assert d["wick_low"] != d["low"]
    assert d["wick_high"] == d["high"] - d["body_high"]
    assert d["wick_low"] == d["body_low"] - d["low"]


def test_marubozu_zero_wicks_are_zero_not_aliased():
    # open==low, close==high -> both wicks zero (NOT aliased to high/low)
    d = _direct("M1", o=1995.0, h=2005.0, low=1995.0, c=2005.0)["data"]
    assert d["wick_high"] == 0.0 and d["wick_low"] == 0.0
    assert d["range_size"] == d["body_size"] == 10.0


# ---------------- validator recomputes / fails loud on tampering ----------------
def _tamper(field, value):
    p = _direct("M5")
    p["data"][field] = value
    return p


def test_validator_rejects_wick_aliased_to_high():
    p = _tamper("wick_high", _direct("M5")["data"]["high"])   # alias wick_high -> high price
    try:
        cc.validate_candle_contract(p); assert False
    except ValueError as e:
        assert "GOV-CANDLE-CONTRACT-035" in str(e)


def test_validator_rejects_negative_wick():
    try:
        cc.validate_candle_contract(_tamper("wick_low", -1.0)); assert False
    except ValueError as e:
        assert "GOV-CANDLE-CONTRACT-031" in str(e)


def test_validator_rejects_body_bigger_than_range():
    try:
        cc.validate_candle_contract(_tamper("body_size", 999.0)); assert False
    except ValueError as e:
        assert "GOV-CANDLE-CONTRACT-033" in str(e)


def test_validator_rejects_bad_direction():
    try:
        cc.validate_candle_contract(_tamper("candle_direction", "BULLISH")); assert False
    except ValueError as e:
        assert "GOV-CANDLE-CONTRACT-038" in str(e) or "GOV-CANDLE-CONTRACT-037" in str(e)


def test_validator_rejects_missing_geometry_field():
    p = _direct("M5")
    del p["data"]["wick_high"]
    try:
        cc.validate_candle_contract(p); assert False
    except ValueError as e:
        assert "GOV-CANDLE-CONTRACT-012" in str(e)   # missing data field caught first


# ---------------- unpriced candle: geometry present-but-None, still valid ----------------
def test_market_closed_geometry_is_none_and_valid():
    p = _direct("H1", market_open=False)
    d = p["data"]
    assert p["status"] == "MARKET_CLOSED"
    for f in ("body_high", "body_low", "body_size", "range_size", "wick_high", "wick_low",
              "candle_direction"):
        assert d[f] is None
    assert cc.validate_candle_contract(p) is True


# ---------------- no interpretive/regime leakage ----------------
def test_no_forbidden_regime_field_can_leak():
    p = _direct("M5")
    p["data"]["regime"] = "TREND"
    try:
        cc.validate_candle_contract(p); assert False
    except ValueError as e:
        assert "GOV-CANDLE-CONTRACT-029" in str(e)


# ============================ shadow seam ============================
class _TF:
    def __init__(self, name):
        self.name = name


class _Candle:
    def __init__(self, instrument="XAU_USD", tf="M5", complete=True,
                 o=2000.0, h=2010.0, low=1995.0, c=2005.0, volume=120):
        self.instrument = instrument
        self.timeframe = _TF(tf)
        self.timestamp = _TS
        self.open, self.high, self.low, self.close = o, h, low, c
        self.volume = volume
        self.complete = complete


class StrictFakeRedis:
    def __init__(self):
        self.store = {}
        self.sets = []

    def set(self, key, value, ex=None):
        assert isinstance(value, (str, bytes)), "shadow writer must serialise (no dict to client)"
        self.store[key] = (value, ex)
        self.sets.append((key, value, ex))
        return True


def _shadow_cfg(**over):
    base = dict(publish_enabled=False, publish_authorised=False, shadow_publish_enabled=True,
                shadow_authorised=True, namespace="hermes", contract_version="v1",
                redis_host="192.168.11.10", redis_port=6380, redis_db=0,
                treat_as_production=False, dev_shadow=True)
    base.update(over)
    return cp.CandlePublisherConfig(**base)


def _seam(client=None):
    return seam.build_shadow_seam(config=_shadow_cfg(), redis_client=client or StrictFakeRedis())


def _gen(tf):
    return _TS + timedelta(seconds=cc.TF_SECONDS[tf] + 0.5)


def test_seam_supports_full_direct_grid():
    assert seam.SUPPORTED_TF == ("M1", "M5", "M15", "H1")
    for tf in _DIRECT_GRID:
        sh = _seam()
        res = sh.emit(_Candle(tf=tf), generated_at_utc=_gen(tf))
        assert res["emitted"] is True and res["wrote"] is True
        assert res["key"] == f"hermes:shadow:candles:XAU_USD:{tf}:latest:v1"
        key, value, ex = sh.writer.redis_client.sets[0]
        assert ex == cc.redis_ex_seconds(tf)
        assert cc.validate_candle_contract(json.loads(value)) is True


def test_seam_skips_deferred_timeframes():
    for tf in ("D1", "H4", "D"):
        sh = _seam()
        res = sh.emit(_Candle(tf=tf), generated_at_utc=_gen("M5"))
        assert res["emitted"] is False and res["reason"] == "UNSUPPORTED_TIMEFRAME"
        assert sh.writer.redis_client.sets == []


def test_every_shadow_key_is_versioned_v1():
    sh = _seam()
    for tf in _DIRECT_GRID:
        sh.emit(_Candle(tf=tf), generated_at_utc=_gen(tf))
    assert sh.writer.redis_client.store, "expected shadow writes"
    for k in sh.writer.redis_client.store:
        assert k.startswith("hermes:shadow:candles:") and k.endswith(":latest:v1")
        assert not (k.startswith("hermes:candles:") and not k.startswith("hermes:shadow:"))


def test_xauusd_alias_canonicalised_no_dual_publish():
    assert seam.canonical_instrument("XAUUSD") == "XAU_USD"
    assert seam.canonical_instrument("XAU_USD") == "XAU_USD"
    sh = _seam()
    res = sh.emit(_Candle(instrument="XAUUSD", tf="M5"), generated_at_utc=_gen("M5"))
    assert res["key"] == "hermes:shadow:candles:XAU_USD:M5:latest:v1"
    # the alias form is NEVER written (no dual-publish)
    assert all("XAUUSD" not in k for k in sh.writer.redis_client.store)
    assert len(sh.writer.redis_client.store) == 1
