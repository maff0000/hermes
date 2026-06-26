"""GOLD MTF canonical candle writer + live wiring + M15 production.
WO-HELM-HERMES-GOLD-MTF-CANONICAL-WRITER-AND-WIRE-0001.

Code-only, PR-gated. NO live/dev Redis or SQL is touched — an in-memory fake Redis client is injected.
Nothing here activates canonical publication (no env flip, no real client).

Proves: the canonical writer writes ONLY versioned canonical keys for M1/M5/M15/H1; rejects H4/D1,
unversioned keys, XAUUSD alias keys, invalid payloads, and regime fields; sets TTL per contract;
fails loud on validation/Redis errors (and the seam surfaces them observably); the canonical sink
requires explicit governed config; M15 is produced by both aggregator paths with no extra D1/H4
publication; and naive/aware UTC handling stays safe.
"""
import json
from datetime import datetime, timedelta, timezone

from utils import candle_contract_v1 as cc
from utils import candle_publisher_v1 as cp
from utils import candle_runtime_seam_v1 as seam

_TS = datetime(2026, 6, 26, 8, 0, tzinfo=timezone.utc)
_GRID = ("M1", "M5", "M15", "H1")


# --------------------------------------------------------------------- fakes / helpers
class _TF:
    def __init__(self, name):
        self.name = name


class _Candle:
    """Mirrors the runtime candle shape the seam consumes (signal_builder.Candle / models.Candle)."""
    def __init__(self, instrument="XAU_USD", tf="M5", complete=True, ts=None,
                 o=2000.0, h=2010.0, low=1995.0, c=2005.0, volume=120):
        self.instrument = instrument
        self.timeframe = _TF(tf)
        self.timestamp = ts or _TS
        self.open, self.high, self.low, self.close = o, h, low, c
        self.volume = volume
        self.complete = complete


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.sets = []

    def set(self, key, value, ex=None):
        assert isinstance(value, (str, bytes)), "real client must never receive a dict"
        self.store[key] = (value, ex)
        self.sets.append((key, value, ex))
        return True


class ExplodingRedis:
    def set(self, *a, **k):
        raise ConnectionError("redis down")


def _canon_cfg(**over):
    base = dict(publish_enabled=True, publish_authorised=True,
                shadow_publish_enabled=False, shadow_authorised=False,
                namespace="hermes", contract_version="v1",
                redis_host="192.168.11.10", redis_port=6379, redis_db=0)
    base.update(over)
    return cp.CandlePublisherConfig(**base)


def _canon_seam(client=None, allowed=("XAU_USD",)):
    return seam.build_canonical_seam(config=_canon_cfg(), redis_client=client or FakeRedis(),
                                     allowed_instruments=allowed)


def _gen(tf):
    return _TS + timedelta(seconds=cc.TF_SECONDS[tf] + 0.5)


def _envelope(tf="M5", instrument="XAU_USD", **o):
    p = dict(open=2000.0, high=2010.0, low=1995.0, close=2005.0, volume=10)
    p.update(o)
    return cc.build_candle_contract(
        instrument=instrument, timeframe=tf, timestamp_utc=_TS,
        ohlc=p, is_closed=True, generated_at_utc=_gen(tf), source_timeframe=tf,
        source_count=1, expected_source_count=1, derivation=cc.DERIVATION_DIRECT,
        derivation_policy=cc.DERIVATION_POLICY_DIRECT, source_policy_epoch="DIRECT_NATIVE_V1")


# --------------------------------------------------------------------- canonical writer
def test_writer_writes_only_versioned_canonical_keys_for_grid():
    for tf in _GRID:
        r = FakeRedis()
        w = cp.SerializingCandleCanonicalWriter(config=_canon_cfg(), redis_client=r)
        res = w.publish(_envelope(tf))
        assert res["key"] == f"hermes:candles:XAU_USD:{tf}:latest:v1"
        assert res["write_mode"] == cp.WRITE_MODE_CANONICAL
        key, value, ex = r.sets[0]
        assert key.endswith(":latest:v1")
        assert isinstance(value, str) and ex == cc.redis_ex_seconds(tf)   # TTL per contract
        assert cc.validate_candle_contract(json.loads(value)) is True


def test_writer_requires_enabled_and_authorised():
    for cfg in (_canon_cfg(publish_enabled=False), _canon_cfg(publish_authorised=False)):
        try:
            cp.SerializingCandleCanonicalWriter(config=cfg, redis_client=FakeRedis())
            assert False, "must fail loud when not enabled+authorised"
        except ValueError as e:
            assert "GOV-CANDLE-PUB-CANON-001" in str(e)


def test_writer_rejects_h4_and_d1():
    for tf in ("H4", "D"):
        r = FakeRedis()
        w = cp.SerializingCandleCanonicalWriter(config=_canon_cfg(), redis_client=r)
        try:
            w.publish(_envelope(tf)); assert False, f"{tf} must not be published"
        except ValueError as e:
            assert "GOV-CANDLE-PUB-CANON-KEY-005" in str(e)
        assert r.sets == []


def test_writer_guard_rejects_unversioned_key():
    # the writer-level guard rejects an unversioned canonical key outright...
    try:
        cp.assert_canonical_key("hermes:candles:XAU_USD:M5:latest"); assert False
    except ValueError as e:
        assert "GOV-CANDLE-PUB-CANON-KEY-002" in str(e)
    # ...and the full plan path also fails loud (the contract validator catches key inconsistency first)
    env = _envelope("M5")
    env["key"] = "hermes:candles:XAU_USD:M5:latest"
    try:
        cp.build_canonical_write_plan(env); assert False
    except ValueError as e:
        assert "GOV-CANDLE-CONTRACT-019" in str(e) or "GOV-CANDLE-PUB-CANON-KEY-002" in str(e)


def test_writer_rejects_xauusd_alias_key():
    try:
        cp.assert_canonical_key("hermes:candles:XAUUSD:M5:latest:v1"); assert False
    except ValueError as e:
        assert "GOV-CANDLE-PUB-CANON-KEY-004" in str(e)


def test_validation_failure_prevents_write():
    env = _envelope("M5")
    env["data"]["wick_high"] = env["data"]["high"]      # alias tamper -> validation fails
    r = FakeRedis()
    w = cp.SerializingCandleCanonicalWriter(config=_canon_cfg(), redis_client=r)
    try:
        w.publish(env); assert False
    except ValueError as e:
        assert "GOV-CANDLE-CONTRACT-035" in str(e)
    assert r.sets == []                                  # nothing written


def test_regime_field_rejected_before_write():
    env = _envelope("M5")
    env["data"]["regime"] = "TREND"
    r = FakeRedis()
    w = cp.SerializingCandleCanonicalWriter(config=_canon_cfg(), redis_client=r)
    try:
        w.publish(env); assert False
    except ValueError as e:
        assert "GOV-CANDLE-CONTRACT-029" in str(e)
    assert r.sets == []


def test_redis_failure_propagates_at_writer():
    w = cp.SerializingCandleCanonicalWriter(config=_canon_cfg(), redis_client=ExplodingRedis())
    try:
        w.publish(_envelope("M5")); assert False
    except ConnectionError:
        pass


# --------------------------------------------------------------------- canonical seam
def test_seam_publishes_grid_and_skips_deferred():
    for tf in _GRID:
        sh = _canon_seam()
        res = sh.emit(_Candle(tf=tf), generated_at_utc=_gen(tf))
        assert res["emitted"] is True and res["wrote"] is True
        assert res["key"] == f"hermes:candles:XAU_USD:{tf}:latest:v1"
        assert sh.metrics["candles_canonical_published"][tf] == 1
    for tf in ("D1", "H4", "D"):
        sh = _canon_seam()
        res = sh.emit(_Candle(tf=tf), generated_at_utc=_gen("M5"))
        assert res["emitted"] is False and res["reason"] == "UNSUPPORTED_TIMEFRAME"
        assert sh.writer.redis_client.sets == []


def test_seam_xauusd_canonicalised_no_dual_publish():
    sh = _canon_seam()
    res = sh.emit(_Candle(instrument="XAUUSD", tf="M5"), generated_at_utc=_gen("M5"))
    assert res["key"] == "hermes:candles:XAU_USD:M5:latest:v1"
    assert all("XAUUSD" not in k for k in sh.writer.redis_client.store)
    assert len(sh.writer.redis_client.store) == 1


def test_seam_redis_failure_is_observable_not_silent():
    sh = _canon_seam(client=ExplodingRedis())
    res = sh.emit(_Candle(tf="M5"), generated_at_utc=_gen("M5"))
    assert res["emitted"] is False and res["reason"] == "CANDLE_EMIT_FAIL"
    assert sh.metrics["candle_emit_fail"] == 1           # surfaced, not swallowed


def test_seam_payload_has_geometry_and_wick_sizes():
    sh = _canon_seam()
    sh.emit(_Candle(tf="H1", o=2000.0, h=2010.0, low=1995.0, c=2005.0), generated_at_utc=_gen("H1"))
    env = json.loads(sh.writer.redis_client.store["hermes:candles:XAU_USD:H1:latest:v1"][0])
    d = env["data"]
    for f in ("open", "high", "low", "close", "volume", "body_high", "body_low", "body_size",
              "range_size", "wick_high", "wick_low", "candle_direction", "gap_state",
              "source_count", "expected_source_count", "source_coverage"):
        assert f in d, f"missing data field {f}"
    assert "freshness_state" in env and "provenance" in env and "valid_until_utc" in env
    assert d["wick_high"] == d["high"] - d["body_high"] and d["wick_high"] != d["high"]
    assert d["wick_low"] == d["body_low"] - d["low"] and d["wick_low"] != d["low"]
    assert d["candle_direction"] in ("UP", "DOWN", "FLAT")


def test_seam_naive_timestamp_safe():
    sh = _canon_seam()
    res = sh.emit(_Candle(tf="M5", ts=datetime(2026, 6, 26, 8, 0)),   # naive
                  generated_at_utc=datetime(2026, 6, 26, 8, 5, 30))    # naive
    assert res["emitted"] is True and res["wrote"] is True
    env = json.loads(sh.writer.redis_client.store[res["key"]][0])
    assert env["generated_at_utc"].endswith("Z") and cc.validate_candle_contract(env) is True


# --------------------------------------------------------------------- from-env gating / wiring
def _clear(mp):
    for k in ("HERMES_CANDLE_FORWARD_ENABLED", "HERMES_CANDLE_FORWARD_SINK",
              "HERMES_CANDLE_PUBLISH_ENABLED", "HERMES_CANDLE_PUBLISH_AUTHORISED",
              "HERMES_CANDLE_CANONICAL_REDIS_HOST", "HERMES_CANDLE_CANONICAL_REDIS_PORT",
              "HERMES_CANDLE_CANONICAL_REDIS_DB"):
        mp.delenv(k, raising=False)


def test_disabled_by_default_writes_nothing(monkeypatch):
    _clear(monkeypatch)
    em = seam.build_candle_forward_seam_from_env()
    assert isinstance(em, cp.DisabledCandleEmitter)
    out = em.emit(candle=_Candle(tf="M5"))      # mirrors the live main.py call shape
    assert out["emitted"] is False and out["reason"] == "CANDLE_PUBLISH_DISABLED"


def test_canonical_requires_explicit_governed_config(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("HERMES_CANDLE_FORWARD_ENABLED", "true")
    monkeypatch.setenv("HERMES_CANDLE_FORWARD_SINK", "canonical")
    # enabled+authorised but NO bus target -> still fail loud (required config missing)
    monkeypatch.setenv("HERMES_CANDLE_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("HERMES_CANDLE_PUBLISH_AUTHORISED", "true")
    try:
        seam.build_candle_forward_seam_from_env(); assert False
    except ValueError as e:
        assert "required" in str(e).lower()


def test_invalid_sink_fails_loud(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("HERMES_CANDLE_FORWARD_ENABLED", "true")
    for bad in ("live", "prod", "bogus"):
        monkeypatch.setenv("HERMES_CANDLE_FORWARD_SINK", bad)
        try:
            seam.build_candle_forward_seam_from_env(); assert False
        except ValueError as e:
            assert seam.FAULT_WRITE_FORBIDDEN in str(e)


def test_live_candle_object_flows_through_seam():
    # the live emit call is emitter.emit(candle=candle) with a signal_builder.Candle (string tf).
    from signal_builder import Candle as SBCandle
    c = SBCandle(instrument="XAU_USD", timestamp=_TS, timeframe="M15",
                 open=2000.0, high=2010.0, low=1995.0, close=2005.0, volume=7, complete=True)
    sh = _canon_seam()
    res = sh.emit(candle=c, generated_at_utc=_gen("M15"))
    assert res["emitted"] is True and res["key"] == "hermes:candles:XAU_USD:M15:latest:v1"


# --------------------------------------------------------------------- M15 production
def test_models_aggregator_produces_m15_no_extra_h4():
    from models.candle import CandleAggregator, Timeframe
    from models.tick import SignalTick, TickSource
    assert Timeframe.M15 in list(Timeframe)
    agg = CandleAggregator(["XAU_USD"])
    agg.process_tick(SignalTick(instrument="XAU_USD", bid=2650.0, ask=2651.0,
                                timestamp=_TS.replace(tzinfo=None), source=TickSource.MOCK))
    names = {c.timeframe.name for c in agg.flush_all()}
    assert "M15" in names
    assert names == {"M1", "M5", "M15", "H1", "D1"}     # no H4; D1 internal only
    assert "H4" not in names


def test_models_m15_candle_time_truncates_to_15min():
    from models.candle import CandleAggregator, Timeframe
    agg = CandleAggregator(["XAU_USD"])
    t = agg._get_candle_time(datetime(2026, 6, 26, 8, 37, 41), Timeframe.M15)
    assert t == datetime(2026, 6, 26, 8, 30, 0)


def test_signal_builder_live_aggregator_produces_m15():
    from signal_builder import CandleAggregator as SBAgg
    agg = SBAgg(instruments=["XAU_USD"], timeframes=["M1", "M5", "M15", "H1"])
    # first tick opens candles; a tick 16 minutes later crosses the M15 boundary -> M15 completes
    agg.process_tick("XAU_USD", 2000.0, 2001.0, datetime(2026, 6, 26, 8, 1, 0))
    completed = agg.process_tick("XAU_USD", 2002.0, 2003.0, datetime(2026, 6, 26, 8, 17, 0))
    assert "M15" in {c.timeframe for c in completed}
