"""HERMES D1 H4-seal runtime hook — code-only, in-memory fake Redis. Nothing activates, no live I/O.
WO-HELM-HERMES-GOLD-D1-H4-SEAL-RUNTIME-HOOK-0001.

Proves the H4 seal path offers each sealed H4 to the governed D1 producer, default-disabled is a no-op,
a D1 fault never breaks H4 latest, and D1 publishes only a complete 6/6 day. H4 latest / forward-history
behaviour is unchanged.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import utils.candle_h4_publish_wire_v1 as wire
import utils.candle_d1_publish_wire_v1 as d1w
import utils.candle_publisher_v1 as cp
import utils.candle_contract_v1 as cc

UTC = timezone.utc
_D1O = datetime(2026, 6, 25, 22, 0, tzinfo=UTC)        # a D1 open (22:00Z) == first H4 bucket of the day


class FakeRedis:
    def __init__(self): self.store = {}; self.sets = []
    def set(self, k, v, ex=None):
        assert isinstance(v, (str, bytes)); self.store[k] = (v, ex); self.sets.append((k, v, ex)); return True


class _H1:
    def __init__(self, open_dt, o=2000.0, hi=2010.0, lo=1990.0, c=2005.0, v=10, instrument="XAU_USD"):
        self.instrument = instrument; self.timeframe = "H1"; self.timestamp = open_dt
        self.open, self.high, self.low, self.close, self.volume = o, hi, lo, c, v


class SpyD1:
    enabled = True
    def __init__(self, published=False):
        self.calls = []; self._published = published
    def on_h4_close(self, h4_view):
        self.calls.append(h4_view)
        return {"published": self._published, "reason": "BUFFERED"}


class DisabledLikeD1:
    enabled = False
    def on_h4_close(self, *a, **k): return {"published": False, "reason": "D1_PUBLISH_DISABLED"}


class RaisingD1:
    enabled = True
    def on_h4_close(self, *a, **k): raise ValueError("GOV-CANDLE-D1-WIRE-XXX: simulated D1 fault")


def _cfg():
    return cp.CandlePublisherConfig(publish_enabled=True, publish_authorised=True, shadow_publish_enabled=False,
                                    shadow_authorised=False, namespace="hermes", contract_version="v1",
                                    redis_host="192.168.11.10", redis_port=6379, redis_db=0)


def _writer(client): return cp.SerializingCandleCanonicalWriter(config=_cfg(), redis_client=client)


def _h4_producer(client, d1_producer=None, allowed=("XAU_USD",)):
    return wire.CanonicalH4Producer(_writer(client), allowed_instruments=allowed, d1_producer=d1_producer)


def _seal_one_h4(h4p, base=_D1O, instrument="XAU_USD"):
    """Feed 4 H1 in the `base` H4 bucket + 1 H1 in the next bucket -> seals the `base` H4."""
    last = None
    for i in range(5):                      # 22,23,00,01 (base bucket) + 02 (next bucket triggers seal)
        last = h4p.on_h1_close(_H1(base + timedelta(hours=i), instrument=instrument))
    return last


def _feed_h1_hours(h4p, start, n, instrument="XAU_USD"):
    last = None
    for i in range(n):
        last = h4p.on_h1_close(_H1(start + timedelta(hours=i), instrument=instrument))
    return last


# ============================ disabled hook = no-op ============================
def test_disabled_d1_hook_no_op_when_none():
    r = FakeRedis(); h4p = _h4_producer(r, d1_producer=None)
    res = _seal_one_h4(h4p)
    assert res["published"] is True                          # H4 still published
    assert res["d1_hook"]["reason"] == "D1_HOOK_DISABLED"
    assert h4p.status()["d1_hook_enabled"] is False
    assert all(":D1:" not in k for k in r.store)


def test_disabled_d1_hook_no_op_when_disabled_producer():
    r = FakeRedis(); h4p = _h4_producer(r, d1_producer=DisabledLikeD1())
    res = _seal_one_h4(h4p)
    assert res["d1_hook"]["reason"] == "D1_HOOK_DISABLED"
    assert h4p.metrics["d1_hook_offered"] == 0              # never offered to a disabled producer


# ============================ from_env fail-loud through the hooked path ============================
def _canon_env(monkeypatch):
    monkeypatch.setenv("HERMES_CANDLE_FORWARD_ENABLED", "true")
    monkeypatch.setenv("HERMES_CANDLE_FORWARD_SINK", "canonical")
    monkeypatch.setenv("HERMES_CANDLE_H4_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("HERMES_CANDLE_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("HERMES_CANDLE_PUBLISH_AUTHORISED", "true")
    monkeypatch.setenv("HERMES_CANDLE_CANONICAL_REDIS_HOST", "192.168.11.10")
    monkeypatch.setenv("HERMES_CANDLE_CANONICAL_REDIS_PORT", "6379")
    monkeypatch.setenv("HERMES_CANDLE_CANONICAL_REDIS_DB", "0")
    monkeypatch.setenv("HERMES_CANDLE_CANONICAL_INSTRUMENTS", "XAU_USD")


def test_hook_d1_enabled_unauthorised_fails_loud(monkeypatch):
    _canon_env(monkeypatch)
    monkeypatch.setenv(d1w.D1_PUBLISH_ENABLED_ENV, "true")
    monkeypatch.delenv(d1w.D1_PUBLISH_AUTHORISED_ENV, raising=False)
    monkeypatch.setenv(d1w.D1_INSTRUMENTS_ENV, "XAU_USD")
    with pytest.raises(ValueError) as e:
        wire.build_h4_producer_from_env()
    assert "GOV-CANDLE-D1-WIRE-004" in str(e.value)


def test_hook_d1_missing_allowlist_fails_loud(monkeypatch):
    _canon_env(monkeypatch)
    monkeypatch.setenv(d1w.D1_PUBLISH_ENABLED_ENV, "true")
    monkeypatch.setenv(d1w.D1_PUBLISH_AUTHORISED_ENV, "true")
    monkeypatch.delenv(d1w.D1_INSTRUMENTS_ENV, raising=False)
    with pytest.raises(ValueError) as e:
        wire.build_h4_producer_from_env()
    assert "GOV-CANDLE-D1-WIRE-005" in str(e.value)


def test_hook_d1_source_tf_not_h4_fails_loud(monkeypatch):
    _canon_env(monkeypatch)
    monkeypatch.setenv(d1w.D1_PUBLISH_ENABLED_ENV, "true")
    monkeypatch.setenv(d1w.D1_PUBLISH_AUTHORISED_ENV, "true")
    monkeypatch.setenv(d1w.D1_INSTRUMENTS_ENV, "XAU_USD")
    monkeypatch.setenv(d1w.D1_SOURCE_TIMEFRAME_ENV, "H1")    # 24xH1 shortcut forbidden
    with pytest.raises(ValueError) as e:
        wire.build_h4_producer_from_env()
    assert "GOV-CANDLE-D1-WIRE-003" in str(e.value)


def test_hook_d1_xauusd_and_nonxau_rejected(monkeypatch):
    for bad in ("XAUUSD", "EUR_USD"):
        _canon_env(monkeypatch)
        monkeypatch.setenv(d1w.D1_PUBLISH_ENABLED_ENV, "true")
        monkeypatch.setenv(d1w.D1_PUBLISH_AUTHORISED_ENV, "true")
        monkeypatch.setenv(d1w.D1_INSTRUMENTS_ENV, bad)
        with pytest.raises(ValueError) as e:
            wire.build_h4_producer_from_env()
        assert "GOV-CANDLE-D1-WIRE-006" in str(e.value)


def test_hook_d1_disabled_default_builds_no_op(monkeypatch):
    _canon_env(monkeypatch)
    for k in (d1w.D1_PUBLISH_ENABLED_ENV, d1w.D1_PUBLISH_AUTHORISED_ENV, d1w.D1_INSTRUMENTS_ENV):
        monkeypatch.delenv(k, raising=False)
    p = wire.build_h4_producer_from_env()
    assert isinstance(p, wire.CanonicalH4Producer)
    assert p.status()["d1_hook_enabled"] is False           # H4 hooked but D1 dark


# ============================ offer mechanics ============================
def test_h4_seal_offers_sealed_h4_to_d1():
    r = FakeRedis(); spy = SpyD1(); h4p = _h4_producer(r, d1_producer=spy)
    _seal_one_h4(h4p)
    assert len(spy.calls) == 1
    view = spy.calls[0]
    assert view.timeframe == "H4" and view.instrument == "XAU_USD"
    assert view.timestamp == _D1O                            # the sealed 22:00 H4 bucket open
    # the offered view matches the published H4 envelope's OHLCV
    env = json.loads(r.store["hermes:candles:XAU_USD:H4:latest:v1"][0])
    d = env["data"]
    assert (view.open, view.high, view.low, view.close, view.volume) == (d["open"], d["high"], d["low"], d["close"], d["volume"])
    assert h4p.metrics["d1_hook_offered"] == 1


def test_h4_seal_does_not_offer_when_disabled():
    r = FakeRedis(); h4p = _h4_producer(r, d1_producer=None)
    _seal_one_h4(h4p)
    assert h4p.metrics["d1_hook_offered"] == 0


# ============================ integration: 6/6 only ============================
def test_d1_does_not_publish_until_six_h4():
    r = FakeRedis(); d1p = d1w.CanonicalD1Producer(_writer(r), allowed_instruments=("XAU_USD",))
    h4p = _h4_producer(r, d1_producer=d1p)
    _feed_h1_hours(h4p, _D1O, 12)                            # only ~2-3 H4 sealed within the day
    assert all(":D1:" not in k for k in r.store)             # no D1 published yet
    assert h4p.metrics["d1_hook_published"] == 0


def test_full_day_six_h4_publishes_complete_d1_via_hook():
    r = FakeRedis(); d1p = d1w.CanonicalD1Producer(_writer(r), allowed_instruments=("XAU_USD",))
    h4p = _h4_producer(r, d1_producer=d1p)
    _feed_h1_hours(h4p, _D1O, 30)                            # day1 22:00 .. day2 ~04:00 -> rolls D1 day1
    key = "hermes:candles:XAU_USD:D1:latest:v1"
    assert key in r.store
    env = json.loads(r.store[key][0]); d = env["data"]
    assert int(d["timestamp_utc"][11:13]) == 22             # 22:00 anchor (never midnight)
    assert d["timeframe"] == "D1" and d["source_timeframe"] == "H4"
    assert d["source_count"] == 6 and d["source_coverage"] == 1.0 and env["status"] == "OK"
    assert h4p.metrics["d1_hook_published"] >= 1
    assert cc.validate_candle_contract(env) is True


# ============================ fault isolation ============================
def test_d1_fault_does_not_break_h4_latest():
    r = FakeRedis(); h4p = _h4_producer(r, d1_producer=RaisingD1())
    res = _seal_one_h4(h4p)
    assert res["published"] is True and res["key"] == "hermes:candles:XAU_USD:H4:latest:v1"   # H4 STILL published
    assert "hermes:candles:XAU_USD:H4:latest:v1" in r.store
    assert res["d1_hook"]["reason"] == "D1_HOOK_FAIL"
    assert h4p.metrics["d1_hook_fail"] == 1                  # fault counted + visible


def test_d1_fault_visible_in_status():
    r = FakeRedis(); h4p = _h4_producer(r, d1_producer=RaisingD1())
    _seal_one_h4(h4p)
    st = h4p.status()
    assert st["d1_hook_enabled"] is True and st["d1_hook_fail"] == 1


# ============================ safety: no D1 history, no direct-D1, no 24xH1, no regime ============================
def test_no_d1_history_written_by_hook():
    r = FakeRedis(); d1p = d1w.CanonicalD1Producer(_writer(r), allowed_instruments=("XAU_USD",))
    h4p = _h4_producer(r, d1_producer=d1p)
    _feed_h1_hours(h4p, _D1O, 30)
    assert all(":history:" not in k or ":D1:" not in k for k in r.store)   # no D1 history key written


def test_hook_module_no_direct_d1_no_24xh1_no_regime_no_shadow():
    src = open(wire.__file__).read()
    assert '"candles_D1"' not in src and "'candles_D1'" not in src         # no midnight direct table
    assert '"candles_H1"' not in src and "'candles_H1'" not in src         # no H1 source path for D1
    # the D1 hook adapts a published H4 envelope (source H4) — _SealedH4View.timeframe == "H4"
    assert wire._SealedH4View.timeframe == "H4"
    assert "regime_confidence" not in src and '"regime"' not in src and "'regime'" not in src
    assert "shadow" not in src.lower()


def test_published_payloads_carry_no_forbidden_fields():
    r = FakeRedis(); d1p = d1w.CanonicalD1Producer(_writer(r), allowed_instruments=("XAU_USD",))
    h4p = _h4_producer(r, d1_producer=d1p)
    _feed_h1_hours(h4p, _D1O, 30)
    blob = json.dumps(r.store).lower()
    for tok in ("regime", "structure", "choch", "order_block", "shadow"):
        assert tok not in blob
