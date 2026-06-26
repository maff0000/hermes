"""GOLD MTF canonical instrument allowlist + geometry rounding fix.
WO-HELM-HERMES-GOLD-MTF-CANONICAL-INSTRUMENT-ALLOWLIST-AND-ROUNDING-FIX-0001.

Code-only. No live Redis/SQL — in-memory fake client only. Nothing activates.

Fix 1: fail-closed canonical instrument allowlist (only XAU_USD authorised here).
Fix 2: OHLC quantised to governed precision before bounds/geometry validation, so float-hair
       (high a few ulps under max(open,close)) can no longer trip GOV-CANDLE-CONTRACT-006/032.
"""
import json
from datetime import datetime, timedelta, timezone

from utils import candle_contract_v1 as cc
from utils import candle_publisher_v1 as cp
from utils import candle_runtime_seam_v1 as seam

_TS = datetime(2026, 6, 26, 9, 0, tzinfo=timezone.utc)


class _TF:
    def __init__(self, name): self.name = name


class _Candle:
    def __init__(self, instrument="XAU_USD", tf="M5", o=2000.0, h=2010.0, low=1995.0, c=2005.0):
        self.instrument = instrument
        self.timeframe = _TF(tf)
        self.timestamp = _TS
        self.open, self.high, self.low, self.close = o, h, low, c
        self.volume = 9
        self.complete = True


class FakeRedis:
    def __init__(self): self.store = {}; self.sets = []
    def set(self, k, v, ex=None):
        assert isinstance(v, (str, bytes)); self.store[k] = (v, ex); self.sets.append((k, v, ex)); return True


def _cfg(**o):
    base = dict(publish_enabled=True, publish_authorised=True, shadow_publish_enabled=False,
                shadow_authorised=False, namespace="hermes", contract_version="v1",
                redis_host="192.168.11.10", redis_port=6379, redis_db=0)
    base.update(o)
    return cp.CandlePublisherConfig(**base)


def _seam(allowed=("XAU_USD",), client=None):
    return seam.build_canonical_seam(config=_cfg(), redis_client=client or FakeRedis(),
                                     allowed_instruments=allowed)


def _gen(tf): return _TS + timedelta(seconds=cc.TF_SECONDS[tf] + 0.5)


# =========================== Fix 1 — instrument allowlist ===========================
def test_publishes_allowlisted_xau_usd():
    sh = _seam()
    for tf in ("M1", "M5", "M15", "H1"):
        r = sh.emit(_Candle(instrument="XAU_USD", tf=tf), generated_at_utc=_gen(tf))
        assert r["emitted"] is True and r["key"] == f"hermes:candles:XAU_USD:{tf}:latest:v1"


def test_skips_non_allowlisted_instruments_no_write():
    sh = _seam()
    for inst in ("AUD_USD", "EUR_USD", "GBP_USD", "USD_JPY", "XAG_USD"):
        r = sh.emit(_Candle(instrument=inst, tf="M5"), generated_at_utc=_gen("M5"))
        assert r["emitted"] is False and r["wrote"] is False
        assert r["reason"] == "INSTRUMENT_NOT_ALLOWLISTED" and r["instrument"] == inst
    assert sh.writer.redis_client.sets == []                      # nothing written
    assert sh.metrics["candles_skipped_not_allowlisted"]["AUD_USD"] == 1   # observable, no spam


def test_missing_allowlist_fails_loud():
    try:
        seam.parse_canonical_allowlist(None); assert False
    except ValueError as e:
        assert "GOV-CANDLE-FWD-SEAM-007" in str(e)


def test_empty_allowlist_fails_loud():
    for raw in ("", "   ", " , , "):
        try:
            seam.parse_canonical_allowlist(raw); assert False, raw
        except ValueError as e:
            assert "GOV-CANDLE-FWD-SEAM-007" in str(e)


def test_seam_requires_nonempty_allowlist():
    try:
        _seam(allowed=()); assert False
    except ValueError as e:
        assert "GOV-CANDLE-FWD-SEAM-007" in str(e)


def test_xauusd_input_canonicalises_but_never_publishes_alias_key():
    sh = _seam(allowed=("XAU_USD",))
    r = sh.emit(_Candle(instrument="XAUUSD", tf="M5"), generated_at_utc=_gen("M5"))
    assert r["emitted"] is True and r["key"] == "hermes:candles:XAU_USD:M5:latest:v1"
    assert all("XAUUSD" not in k for k in sh.writer.redis_client.store)


def test_allowlist_alias_entry_normalises_to_canonical_only():
    # an allowlist given as the alias XAUUSD must authorise XAU_USD (canonical), never emit XAUUSD keys
    al = seam.parse_canonical_allowlist("XAUUSD")
    assert al == frozenset({"XAU_USD"})
    sh = _seam(allowed=al)
    r = sh.emit(_Candle(instrument="XAU_USD", tf="M5"), generated_at_utc=_gen("M5"))
    assert r["key"] == "hermes:candles:XAU_USD:M5:latest:v1"
    assert all("XAUUSD" not in k for k in sh.writer.redis_client.store)


def test_direct_seam_refuses_h4_and_d1():
    sh = _seam()
    # H4 is derived-only: the DIRECT seam path must refuse it (never a stale direct candle).
    r = sh.emit(_Candle(instrument="XAU_USD", tf="H4"), generated_at_utc=_gen("M5"))
    assert r["emitted"] is False and r["reason"] == "H4_DERIVED_PATH_ONLY"
    # D1/D remain plain unsupported in the direct seam.
    for tf in ("D1", "D"):
        r = sh.emit(_Candle(instrument="XAU_USD", tf=tf), generated_at_utc=_gen("M5"))
        assert r["emitted"] is False and r["reason"] == "UNSUPPORTED_TIMEFRAME"
    assert sh.writer.redis_client.sets == []


def test_unversioned_key_still_rejected_by_writer_guard():
    try:
        cp.assert_canonical_key("hermes:candles:XAU_USD:M5:latest"); assert False
    except ValueError as e:
        assert "GOV-CANDLE-PUB-CANON-KEY-002" in str(e)


def test_from_env_canonical_requires_allowlist(monkeypatch):
    for k in ("HERMES_CANDLE_FORWARD_ENABLED", "HERMES_CANDLE_FORWARD_SINK",
              "HERMES_CANDLE_PUBLISH_ENABLED", "HERMES_CANDLE_PUBLISH_AUTHORISED",
              "HERMES_CANDLE_CANONICAL_REDIS_HOST", "HERMES_CANDLE_CANONICAL_REDIS_PORT",
              "HERMES_CANDLE_CANONICAL_REDIS_DB", "HERMES_CANDLE_CANONICAL_INSTRUMENTS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HERMES_CANDLE_FORWARD_ENABLED", "true")
    monkeypatch.setenv("HERMES_CANDLE_FORWARD_SINK", "canonical")
    monkeypatch.setenv("HERMES_CANDLE_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("HERMES_CANDLE_PUBLISH_AUTHORISED", "true")
    monkeypatch.setenv("HERMES_CANDLE_CANONICAL_REDIS_HOST", "192.168.11.10")
    monkeypatch.setenv("HERMES_CANDLE_CANONICAL_REDIS_PORT", "6379")
    monkeypatch.setenv("HERMES_CANDLE_CANONICAL_REDIS_DB", "0")
    # allowlist deliberately unset -> must fail loud (required), never default fan-out
    try:
        seam.build_candle_forward_seam_from_env(); assert False
    except ValueError as e:
        assert "required" in str(e).lower() or "GOV-CANDLE-FWD-SEAM-007" in str(e)


# =========================== Fix 2 — geometry rounding ===========================
def _build(instrument="XAU_USD", tf="M5", o=2000.0, h=2010.0, low=1995.0, c=2005.0):
    return cc.build_candle_contract(
        instrument=instrument, timeframe=tf, timestamp_utc=_TS,
        ohlc={"open": o, "high": h, "low": low, "close": c, "volume": 1}, is_closed=True,
        generated_at_utc=_gen(tf), source_timeframe=tf, source_count=1, expected_source_count=1,
        derivation=cc.DERIVATION_DIRECT, derivation_policy=cc.DERIVATION_POLICY_DIRECT,
        source_policy_epoch="DIRECT_NATIVE_V1")


def test_eur_gbp_float_hair_now_validates():
    # the exact activation failure: high a few ulps under close (which rounds up to 0.862685)
    p = _build(instrument="EUR_GBP", tf="M1", o=0.862680, h=0.8626849999999999, low=0.86263, c=0.862685)
    assert cc.validate_candle_contract(p) is True
    d = p["data"]
    assert d["body_high"] <= d["high"] and d["body_low"] >= d["low"]
    assert d["wick_high"] >= 0 and d["wick_low"] >= 0


def test_body_within_high_low_across_float_hair_fuzz():
    cases = []
    for base in (0.862685, 2000.123456, 150.123456, 1.234567, 5000.12, 19.995):
        for k in (-2, -1, 0, 1, 2):
            hv = base + k * 1e-16
            cases.append((base, max(base, hv), base - 0.001, hv))
    for o, h, lo, c in cases:
        p = _build(o=o, h=h, low=lo, c=c)
        assert cc.validate_candle_contract(p) is True
        d = p["data"]
        assert d["body_high"] <= d["high"], (d["body_high"], d["high"])
        assert d["body_low"] >= d["low"]
        assert d["range_size"] >= d["body_size"] - 1e-9


def test_wick_still_sizes_not_aliases_after_rounding():
    d = _build(o=2000.0, h=2010.0, low=1995.0, c=2005.0)["data"]
    assert d["wick_high"] == d["high"] - d["body_high"] and d["wick_high"] != d["high"]
    assert d["wick_low"] == d["body_low"] - d["low"] and d["wick_low"] != d["low"]


def test_wick_aliasing_still_fails_loud():
    p = _build()
    p["data"]["wick_high"] = p["data"]["high"]      # alias tamper
    try:
        cc.validate_candle_contract(p); assert False
    except ValueError as e:
        assert "GOV-CANDLE-CONTRACT-035" in str(e)


def test_direction_unchanged_after_rounding():
    assert _build(o=2000.0, c=2005.0)["data"]["candle_direction"] == "UP"
    assert _build(o=2005.0, c=2000.0, h=2010.0, low=1995.0)["data"]["candle_direction"] == "DOWN"
    assert _build(o=2000.0, c=2000.0, h=2003.0, low=1998.0)["data"]["candle_direction"] == "FLAT"


def test_no_validator_tolerance_masks_real_geometry_error():
    # a GENUINE (not float-hair) geometry violation must still fail loud
    p = _build()
    p["data"]["body_high"] = p["data"]["high"] + 0.5     # real overshoot
    try:
        cc.validate_candle_contract(p); assert False
    except ValueError as e:
        assert "GOV-CANDLE-CONTRACT-03" in str(e)   # 032/034 family, not silently tolerated


def test_serialised_payload_preserves_invariants():
    sh = _seam()
    sh.emit(_Candle(instrument="XAU_USD", tf="M1"), generated_at_utc=_gen("M1"))
    blob = sh.writer.redis_client.store["hermes:candles:XAU_USD:M1:latest:v1"][0]
    d = json.loads(blob)["data"]
    assert d["body_high"] <= d["high"] and d["body_low"] >= d["low"]
    assert cc.validate_candle_contract(json.loads(blob)) is True
