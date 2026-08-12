"""HERMES forward-history runtime WIRING — code-only, in-memory fake Redis. Nothing activates, no live I/O.
WO-HELM-HERMES-GOLD-MTF-FORWARD-HISTORY-WIRE-RUNTIME-0001.

Proves the canonical M1/M5/M15/H1 seam and the derived-H4 producer call the governed forward-history writer
at the right points (closed / complete-4-of-4 only), default-disabled is a no-op, and a history fault never
breaks the latest publish. Latest publication behaviour is unchanged.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import utils.candle_runtime_seam_v1 as seam
import utils.candle_h4_publish_wire_v1 as wire
import utils.candle_history_forward_writer_v1 as fw
import utils.candle_history_v1 as chv
import utils.candle_publisher_v1 as cp
import utils.candle_contract_v1 as cc

UTC = timezone.utc
_TS = datetime(2026, 6, 2, 2, 0, tzinfo=UTC)        # clean grid point / H4 bucket open (02:00)


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.zsets = {}
        self.sets = []

    def get(self, k):
        v = self.store.get(k)
        return None if v is None else v[0]

    def set(self, k, v, ex=None):
        assert isinstance(v, (str, bytes))
        self.store[k] = (v, ex)
        self.sets.append((k, v, ex))
        return True

    def zadd(self, name, mapping):
        self.zsets.setdefault(name, {}).update(mapping)
        return len(mapping)

    def history_keys(self):
        return [k for k in self.store if ":history:v1:" in k and not k.endswith(":index")]

    def latest_keys(self):
        return [k for k in self.store if k.endswith(":latest:v1")]


class _TF:
    def __init__(self, name): self.name = name


class _Candle:
    def __init__(self, instrument="XAU_USD", tf="M5", o=2000.0, h=2010.0, low=1995.0, c=2005.0,
                 complete=True, ts=_TS):
        self.instrument = instrument
        self.timeframe = _TF(tf)
        self.timestamp = ts
        self.open, self.high, self.low, self.close = o, h, low, c
        self.volume = 9
        self.complete = complete


class _H1:
    def __init__(self, open_dt, o=2000.0, hi=2008.0, lo=1998.0, c=2004.0, v=10, instrument="XAU_USD"):
        self.instrument = instrument; self.timeframe = "H1"; self.timestamp = open_dt
        self.open, self.high, self.low, self.close, self.volume = o, hi, lo, c, v


class SpyHistoryWriter:
    enabled = True

    def __init__(self):
        self.canonical_calls = []
        self.h4_calls = []

    def on_canonical_close(self, env, *, inserted_at_utc):
        self.canonical_calls.append(env)
        return {"wrote": True, "key": "spy"}

    def on_h4_sealed(self, env, *, inserted_at_utc):
        self.h4_calls.append(env)
        return {"wrote": True, "key": "spy"}


class RaisingHistoryWriter:
    enabled = True

    def on_canonical_close(self, env, *, inserted_at_utc):
        raise ValueError("GOV-CANDLE-HIST-FWD-020: simulated conflict")

    def on_h4_sealed(self, env, *, inserted_at_utc):
        raise ValueError("boom")


def _cfg():
    return cp.CandlePublisherConfig(publish_enabled=True, publish_authorised=True, shadow_publish_enabled=False,
                                    shadow_authorised=False, namespace="hermes", contract_version="v1",
                                    redis_host="192.168.11.10", redis_port=6379, redis_db=0)


def _gen(tf):
    return _TS + timedelta(seconds=cc.TF_SECONDS[tf] + 0.5)


def _seam(client, history_writer=None, allowed=("XAU_USD",)):
    return seam.build_canonical_seam(config=_cfg(), redis_client=client, allowed_instruments=allowed,
                                     history_forward_writer=history_writer)


def _real_hw(client, tfs=("M1", "M5", "M15", "H1", "H4"), allowed=("XAU_USD",)):
    return fw.CandleHistoryForwardWriter(redis_client=client, allowed_instruments=allowed, timeframes=tfs)


def _h4_producer(client, history_writer=None, allowed=("XAU_USD",)):
    w = cp.SerializingCandleCanonicalWriter(config=_cfg(), redis_client=client)
    return wire.CanonicalH4Producer(w, allowed_instruments=allowed, history_forward_writer=history_writer)


# ====================== default-disabled: no history calls ======================
def test_seam_disabled_default_no_history_write():
    r = FakeRedis(); sh = _seam(r, history_writer=None)
    res = sh.emit(_Candle(tf="M1"), generated_at_utc=_gen("M1"))
    assert res["emitted"] is True and res["wrote"] is True            # latest unchanged
    assert r.history_keys() == []                                     # no history written
    assert sh.status()["history_forward_enabled"] is False


def test_h4_producer_disabled_default_no_history_write():
    r = FakeRedis(); p = _h4_producer(r, history_writer=None)
    for hh in range(4):
        p.on_h1_close(_H1(_TS + timedelta(hours=hh)))
    p.on_h1_close(_H1(_TS + timedelta(hours=4)))                      # seals 02:00 bucket (complete)
    assert r.history_keys() == []
    assert p.status()["history_forward_enabled"] is False


# ====================== closed candle -> writer called + history written ======================
@pytest.mark.parametrize("tf", ["M1", "M5", "M15", "H1"])
def test_closed_candle_calls_writer_and_writes_history(tf):
    r = FakeRedis(); hw = _real_hw(r); sh = _seam(r, history_writer=hw)
    res = sh.emit(_Candle(tf=tf), generated_at_utc=_gen(tf))
    assert res["emitted"] is True and res["history_forward"]["wrote"] is True
    open_epoch = int(_TS.timestamp())
    hkey = f"hermes:candles:XAU_USD:{tf}:history:v1:{open_epoch}"
    assert hkey in r.store                                            # history written
    assert f"hermes:candles:XAU_USD:{tf}:latest:v1" in r.store        # latest also written
    assert chv.assert_history_target(hkey) is True and ":latest:" not in hkey
    assert r.store[hkey][1] == chv.HISTORY_TTL_SECONDS               # TTL applied
    assert sh.status()["history_forward_written"] == 1


def test_forming_candle_does_not_call_writer():
    r = FakeRedis(); spy = SpyHistoryWriter(); sh = _seam(r, history_writer=spy)
    res = sh.emit(_Candle(tf="M5", complete=False), generated_at_utc=_gen("M5"))
    assert res["emitted"] is True                                     # forming latest still published
    assert res["history_forward"]["reason"] == "FORMING_NOT_HISTORY"
    assert spy.canonical_calls == []                                  # writer never called for forming
    assert r.history_keys() == []


def test_closed_candle_calls_spy_once():
    r = FakeRedis(); spy = SpyHistoryWriter(); sh = _seam(r, history_writer=spy)
    sh.emit(_Candle(tf="H1"), generated_at_utc=_gen("H1"))
    assert len(spy.canonical_calls) == 1
    assert spy.canonical_calls[0]["data"]["timeframe"] == "H1"


# ====================== H4 seal -> writer called only for complete 4/4 ======================
def test_h4_complete_seal_calls_writer_and_writes_history():
    r = FakeRedis(); hw = _real_hw(r); p = _h4_producer(r, history_writer=hw)
    for hh in range(4):
        p.on_h1_close(_H1(_TS + timedelta(hours=hh)))
    res = p.on_h1_close(_H1(_TS + timedelta(hours=4)))               # seals 02:00 with 4 children
    assert res["published"] is True and res["status"] == "OK"
    assert res["history_forward"]["wrote"] is True
    hkey = f"hermes:candles:XAU_USD:H4:history:v1:{int(_TS.timestamp())}"
    assert hkey in r.store
    env = json.loads(r.store[hkey][0])
    assert env["data"]["source_count"] == 4 and env["provenance"]["derivation"] == cc.DERIVATION_DERIVED
    assert p.status()["h4_history_forward_written"] == 1


def test_h4_partial_seal_does_not_write_ok():
    r = FakeRedis(); spy = SpyHistoryWriter(); p = _h4_producer(r, history_writer=spy)
    for hh in range(3):                                              # only 3 children
        p.on_h1_close(_H1(_TS + timedelta(hours=hh)))
    res = p.on_h1_close(_H1(_TS + timedelta(hours=4)))               # seals 02:00 with 3 -> SOURCE_INCOMPLETE
    assert res["published"] is True and res["status"] != "OK"
    assert spy.h4_calls == []                                        # incomplete never offered to history
    assert r.history_keys() == []


def test_h4_warmup_no_seal_no_history():
    r = FakeRedis(); hw = _real_hw(r); p = _h4_producer(r, history_writer=hw)
    for hh in range(4):
        assert p.on_h1_close(_H1(_TS + timedelta(hours=hh)))["published"] is False   # buffering, no seal yet
    assert r.history_keys() == []                                    # nothing sealed -> nothing in history


# ====================== fault isolation: history error never breaks latest ======================
def test_history_fault_does_not_break_latest():
    r = FakeRedis(); sh = _seam(r, history_writer=RaisingHistoryWriter())
    res = sh.emit(_Candle(tf="M1"), generated_at_utc=_gen("M1"))
    assert res["emitted"] is True and res["wrote"] is True           # latest STILL written
    assert "hermes:candles:XAU_USD:M1:latest:v1" in r.store
    assert res["history_forward"]["reason"] == "HISTORY_FORWARD_FAIL"
    assert sh.status()["history_forward_fail"] == 1


def test_h4_history_fault_does_not_break_latest():
    r = FakeRedis(); p = _h4_producer(r, history_writer=RaisingHistoryWriter())
    for hh in range(4):
        p.on_h1_close(_H1(_TS + timedelta(hours=hh)))
    res = p.on_h1_close(_H1(_TS + timedelta(hours=4)))
    assert res["published"] is True                                  # H4 latest STILL published
    assert res["history_forward"]["reason"] == "H4_HISTORY_FORWARD_FAIL"
    assert p.status()["h4_history_forward_fail"] == 1


# ====================== target/validate guards reached through the wiring ======================
def test_written_history_validates_and_targets_history_not_latest():
    r = FakeRedis(); hw = _real_hw(r); sh = _seam(r, history_writer=hw)
    sh.emit(_Candle(tf="M15"), generated_at_utc=_gen("M15"))
    hkey = r.history_keys()[0]
    env = json.loads(r.store[hkey][0])
    assert cc.validate_candle_contract(env) is True                  # validate ran (would have raised otherwise)
    assert chv.assert_history_target(hkey) is True                   # history target (not latest/unversioned/D1)
    assert "XAUUSD" not in hkey and ":latest:" not in hkey


def test_no_regime_or_d1_in_any_written_key_or_payload():
    r = FakeRedis(); hw = _real_hw(r); sh = _seam(r, history_writer=hw)
    sh.emit(_Candle(tf="H1"), generated_at_utc=_gen("H1"))
    blob = json.dumps(r.store).lower()
    for tok in ("regime", "structure", "choch", "order_block"):
        assert tok not in blob
    assert all(":d1:" not in k.lower() for k in r.store)


# ====================== factory fail-loud through the wired path ======================
def _canonical_env(monkeypatch):
    monkeypatch.setenv("HERMES_CANDLE_FORWARD_ENABLED", "true")
    monkeypatch.setenv("HERMES_CANDLE_FORWARD_SINK", "canonical")
    monkeypatch.setenv("HERMES_CANDLE_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("HERMES_CANDLE_PUBLISH_AUTHORISED", "true")
    monkeypatch.setenv("HERMES_CANDLE_CANONICAL_REDIS_HOST", "192.168.11.10")
    monkeypatch.setenv("HERMES_CANDLE_CANONICAL_REDIS_PORT", "6379")
    monkeypatch.setenv("HERMES_CANDLE_CANONICAL_REDIS_DB", "0")
    monkeypatch.setenv(seam.CANONICAL_ALLOWLIST_ENV, "XAU_USD")


def test_factory_history_enabled_unauthorised_fails_loud(monkeypatch):
    _canonical_env(monkeypatch)
    monkeypatch.setenv(fw.ENABLED_ENV, "true")
    monkeypatch.delenv(fw.AUTHORISED_ENV, raising=False)              # enabled but NOT authorised
    monkeypatch.setenv(fw.TIMEFRAMES_ENV, "M1,M5,M15,H1,H4")
    monkeypatch.setenv(fw.INSTRUMENTS_ENV, "XAU_USD")
    with pytest.raises(ValueError) as e:
        seam.build_candle_forward_seam_from_env()
    assert "GOV-CANDLE-HIST-FWD-002" in str(e.value)


def test_factory_history_d1_timeframe_fails_loud(monkeypatch):
    _canonical_env(monkeypatch)
    monkeypatch.setenv(fw.ENABLED_ENV, "true")
    monkeypatch.setenv(fw.AUTHORISED_ENV, "true")
    monkeypatch.setenv(fw.TIMEFRAMES_ENV, "M1,D1")                    # D1 forbidden
    monkeypatch.setenv(fw.INSTRUMENTS_ENV, "XAU_USD")
    with pytest.raises(ValueError) as e:
        seam.build_candle_forward_seam_from_env()
    assert "GOV-CANDLE-HIST-FWD-005" in str(e.value)


def test_factory_history_multi_instrument_ok(monkeypatch):
    # WO-...-CORE-CANDLE-WICK-HISTORY: the forward-history lane now serves the configured multi-instrument set.
    _canonical_env(monkeypatch)
    monkeypatch.setenv(fw.ENABLED_ENV, "true")
    monkeypatch.setenv(fw.AUTHORISED_ENV, "true")
    monkeypatch.setenv(fw.TIMEFRAMES_ENV, "M1,M5")
    monkeypatch.setenv(fw.INSTRUMENTS_ENV, "XAU_USD,EUR_USD,XAG_USD")   # multi-instrument, incl non-XAU
    sh = seam.build_candle_forward_seam_from_env()
    assert isinstance(sh, seam.CanonicalCandleForwardSeam)
    assert sh.status()["history_forward_enabled"] is True


def test_factory_history_alias_still_fails_loud(monkeypatch):
    _canonical_env(monkeypatch)
    monkeypatch.setenv(fw.ENABLED_ENV, "true")
    monkeypatch.setenv(fw.AUTHORISED_ENV, "true")
    monkeypatch.setenv(fw.TIMEFRAMES_ENV, "M1,M5")
    monkeypatch.setenv(fw.INSTRUMENTS_ENV, "XAUUSD")                    # alias output rejected
    with pytest.raises(ValueError) as e:
        seam.build_candle_forward_seam_from_env()
    assert "GOV-CANDLE-HIST-FWD-006" in str(e.value)


def test_factory_history_disabled_by_default_is_latest_only(monkeypatch):
    _canonical_env(monkeypatch)
    for k in (fw.ENABLED_ENV, fw.AUTHORISED_ENV, fw.TIMEFRAMES_ENV, fw.INSTRUMENTS_ENV):
        monkeypatch.delenv(k, raising=False)
    sh = seam.build_candle_forward_seam_from_env()                   # builds the canonical seam, history off
    assert isinstance(sh, seam.CanonicalCandleForwardSeam)
    assert sh.status()["history_forward_enabled"] is False
