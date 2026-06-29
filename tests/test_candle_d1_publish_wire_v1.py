"""HERMES D1 canonical publish wiring (derived from 6xH4) — code-only, in-memory fake Redis. Nothing activates.
WO-HELM-HERMES-GOLD-D1-CANONICAL-PUBLISH-WIRE-0001.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import utils.candle_d1_publish_wire_v1 as wire
import utils.candle_d1_derivation_v1 as d1d
import utils.candle_publisher_v1 as cp
import utils.candle_history_v1 as chv
import utils.candle_runtime_seam_v1 as seam
import utils.candle_contract_v1 as cc

UTC = timezone.utc
_D1O = datetime(2026, 6, 25, 22, 0, tzinfo=UTC)        # a D1 open (22:00Z)


class FakeRedis:
    def __init__(self): self.store = {}; self.sets = []
    def set(self, k, v, ex=None):
        assert isinstance(v, (str, bytes)); self.store[k] = (v, ex); self.sets.append((k, v, ex)); return True


class _H4:
    """A SEALED H4 candle view. Defaults are a complete, status-OK H4 (4/4 H1, coverage 1.0) — a D1-eligible
    child. Override status/source_count/source_coverage/gap_state/is_closed to model an incomplete child."""
    def __init__(self, open_dt, o=2000.0, hi=2010.0, lo=1990.0, c=2005.0, v=100, instrument="XAU_USD", tf="H4",
                 status="OK", is_closed=True, source_count=4, expected_source_count=4, source_coverage=1.0,
                 gap_state="NONE", source_timeframe="H1"):
        self.instrument = instrument; self.timeframe = tf; self.timestamp = open_dt
        self.open, self.high, self.low, self.close, self.volume = o, hi, lo, c, v
        self.status = status; self.is_closed = is_closed
        self.source_count = source_count; self.expected_source_count = expected_source_count
        self.source_coverage = source_coverage; self.gap_state = gap_state; self.source_timeframe = source_timeframe


def _cfg():
    return cp.CandlePublisherConfig(publish_enabled=True, publish_authorised=True, shadow_publish_enabled=False,
                                    shadow_authorised=False, namespace="hermes", contract_version="v1",
                                    redis_host="192.168.11.10", redis_port=6379, redis_db=0)


def _producer(client=None, allowed=("XAU_USD",), source_timeframe="H4"):
    w = cp.SerializingCandleCanonicalWriter(config=_cfg(), redis_client=client or FakeRedis())
    return wire.CanonicalD1Producer(w, allowed_instruments=allowed, source_timeframe=source_timeframe)


def _six_h4(d1_open=_D1O, instrument="XAU_USD"):
    opens = d1d.d1_child_h4_opens(d1_open)               # 22,02,06,10,14,18
    specs = [(2000, 2010, 1990, 2005, 100), (2005, 2030, 1995, 2020, 110), (2020, 2080, 2010, 2050, 120),
             (2050, 2060, 2000, 2030, 130), (2030, 2040, 1900, 1950, 140), (1950, 1975, 1940, 1970, 150)]
    return [_H4(opens[i], *specs[i], instrument=instrument) for i in range(6)]


def _feed(p, children, next_day_trigger=True):
    """Feed children then (optionally) the next D1 day's first H4 to trigger the seal of the prior bucket."""
    last = None
    for c in children:
        last = p.on_h4_close(c)
    if next_day_trigger:
        trigger = _H4(_D1O + timedelta(hours=24), instrument=children[0].instrument)  # next 22:00 -> new D1 day
        last = p.on_h4_close(trigger)
    return last


# ============================ disabled / gating ============================
def test_disabled_producer_is_noop():
    d = wire.DisabledD1Producer()
    assert d.enabled is False
    assert d.on_h4_close(_H4(_D1O))["reason"] == "D1_PUBLISH_DISABLED"
    assert d.status() == {"enabled": False}


def _canonical_env(monkeypatch):
    monkeypatch.setenv("HERMES_CANDLE_FORWARD_ENABLED", "true")
    monkeypatch.setenv("HERMES_CANDLE_FORWARD_SINK", "canonical")
    monkeypatch.setenv("HERMES_CANDLE_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("HERMES_CANDLE_PUBLISH_AUTHORISED", "true")
    monkeypatch.setenv("HERMES_CANDLE_CANONICAL_REDIS_HOST", "192.168.11.10")
    monkeypatch.setenv("HERMES_CANDLE_CANONICAL_REDIS_PORT", "6379")
    monkeypatch.setenv("HERMES_CANDLE_CANONICAL_REDIS_DB", "0")


def test_from_env_disabled_by_default(monkeypatch):
    for k in ("HERMES_CANDLE_FORWARD_ENABLED", wire.D1_PUBLISH_ENABLED_ENV):
        monkeypatch.delenv(k, raising=False)
    assert isinstance(wire.build_d1_producer_from_env(), wire.DisabledD1Producer)


def test_from_env_enabled_unauthorised_fails_loud(monkeypatch):
    _canonical_env(monkeypatch)
    monkeypatch.setenv(wire.D1_PUBLISH_ENABLED_ENV, "true")
    monkeypatch.delenv(wire.D1_PUBLISH_AUTHORISED_ENV, raising=False)
    monkeypatch.setenv(wire.D1_INSTRUMENTS_ENV, "XAU_USD")
    with pytest.raises(ValueError) as e:
        wire.build_d1_producer_from_env()
    assert "GOV-CANDLE-D1-WIRE-004" in str(e.value)


def test_from_env_missing_allowlist_fails_loud(monkeypatch):
    _canonical_env(monkeypatch)
    monkeypatch.setenv(wire.D1_PUBLISH_ENABLED_ENV, "true")
    monkeypatch.setenv(wire.D1_PUBLISH_AUTHORISED_ENV, "true")
    monkeypatch.delenv(wire.D1_INSTRUMENTS_ENV, raising=False)
    with pytest.raises(ValueError) as e:
        wire.build_d1_producer_from_env()
    assert "GOV-CANDLE-D1-WIRE-005" in str(e.value)


def test_from_env_source_timeframe_not_h4_fails_loud(monkeypatch):
    _canonical_env(monkeypatch)
    monkeypatch.setenv(wire.D1_PUBLISH_ENABLED_ENV, "true")
    monkeypatch.setenv(wire.D1_PUBLISH_AUTHORISED_ENV, "true")
    monkeypatch.setenv(wire.D1_INSTRUMENTS_ENV, "XAU_USD")
    monkeypatch.setenv(wire.D1_SOURCE_TIMEFRAME_ENV, "H1")        # 24xH1 shortcut -> forbidden
    with pytest.raises(ValueError) as e:
        wire.build_d1_producer_from_env()
    assert "GOV-CANDLE-D1-WIRE-003" in str(e.value)


def test_allowlist_rejects_xauusd_and_nonxau():
    for raw in ("XAUUSD", "EUR_USD", "XAU_USD,GBP_USD"):
        with pytest.raises(ValueError) as e:
            wire.parse_d1_instruments(raw)
        assert "GOV-CANDLE-D1-WIRE-006" in str(e.value)
    assert wire.parse_d1_instruments("XAU_USD") == frozenset({"XAU_USD"})


def test_allowlist_missing_empty_fails_loud():
    for raw in (None, "", "  ", " , , "):
        with pytest.raises(ValueError) as e:
            wire.parse_d1_instruments(raw)
        assert "GOV-CANDLE-D1-WIRE-005" in str(e.value)


def test_source_timeframe_guard():
    assert wire.assert_d1_source_timeframe("H4") is True
    for bad in ("H1", "M15", "D1", "candles_D1"):
        with pytest.raises(ValueError) as e:
            wire.assert_d1_source_timeframe(bad)
        assert "GOV-CANDLE-D1-WIRE-003" in str(e.value)


def test_producer_requires_h4_source_and_nonempty_allowlist():
    with pytest.raises(ValueError) as e:
        _producer(allowed=("XAU_USD",), source_timeframe="H1")
    assert "GOV-CANDLE-D1-WIRE-003" in str(e.value)
    with pytest.raises(ValueError) as e:
        _producer(allowed=())
    assert "GOV-CANDLE-D1-WIRE-002" in str(e.value)


# ============================ publish path: 6/6 only ============================
def test_six_h4_children_publish_complete_d1():
    r = FakeRedis(); p = _producer(r)
    res = _feed(p, _six_h4())
    assert res["published"] is True and res["status"] == "OK"
    assert res["key"] == "hermes:candles:XAU_USD:D1:latest:v1"
    assert res["source_count"] == 6 and res["source_coverage"] == 1.0
    env = json.loads(r.store["hermes:candles:XAU_USD:D1:latest:v1"][0])
    assert cc.validate_candle_contract(env) is True
    d = env["data"]
    assert d["timeframe"] == "D1" and d["instrument"] == "XAU_USD"
    assert int(d["timestamp_utc"][11:13]) == 22                    # anchor: 22:00 UTC
    assert d["open"] == 2000.0 and d["high"] == 2080.0 and d["low"] == 1900.0 and d["close"] == 1970.0
    assert d["volume"] == 100 + 110 + 120 + 130 + 140 + 150
    assert env["provenance"]["derivation"] == cc.DERIVATION_DERIVED and d["source_timeframe"] == "H4"
    assert p.metrics["d1_published_ok"] == 1


def test_published_d1_anchor_is_2200_not_midnight():
    r = FakeRedis(); p = _producer(r)
    _feed(p, _six_h4())
    env = json.loads(r.store["hermes:candles:XAU_USD:D1:latest:v1"][0])
    hh = int(env["data"]["timestamp_utc"][11:13])
    assert hh == 22                                                # fixed NY-5PM, never UTC-midnight (00)


def test_fewer_than_six_never_published():
    r = FakeRedis(); p = _producer(r)
    res = _feed(p, _six_h4()[:5])                                  # only 5 H4 children
    assert res["published"] is False and res["reason"] == "D1_INCOMPLETE_NOT_PUBLISHED"
    assert res["complete_child_count"] == 5 and res["expected"] == 6   # short day -> never OK
    assert r.sets == []                                            # NOTHING written
    assert p.metrics["d1_skipped_incomplete"] == 1 and p.metrics["d1_published_ok"] == 0


def test_no_publish_until_day_rolls():
    r = FakeRedis(); p = _producer(r)
    for c in _six_h4():
        assert p.on_h4_close(c)["published"] is False             # buffering, no seal yet
    assert r.sets == []


def test_non_h4_candle_ignored():
    p = _producer()
    res = p.on_h4_close(_H4(_D1O, tf="H1"))                        # H1 must never drive D1 (no 24xH1)
    assert res["reason"] == "NOT_H4" and p.metrics["d1_skipped_non_h4"] == 1


def test_non_allowlisted_instrument_skipped():
    r = FakeRedis(); p = _producer(r, allowed=("XAU_USD",))
    res = p.on_h4_close(_H4(_D1O, instrument="EUR_USD"))
    assert res["reason"] == "INSTRUMENT_NOT_ALLOWLISTED" and r.sets == []


def test_xauusd_input_canonicalised_never_alias_key():
    r = FakeRedis(); p = _producer(r, allowed=("XAU_USD",))
    res = _feed(p, _six_h4(instrument="XAUUSD"))
    assert res["published"] is True and res["key"] == "hermes:candles:XAU_USD:D1:latest:v1"
    assert all("XAUUSD" not in k for k in r.store)


# ============================ key shape / guards / safety ============================
def test_d1_latest_key_accepted_history_rejected():
    assert cp.assert_canonical_key("hermes:candles:XAU_USD:D1:latest:v1") is True   # governed D1 latest
    with pytest.raises(ValueError) as e:                                            # D1 history still blocked
        chv.assert_history_target("hermes:candles:XAU_USD:D1:history:v1:1")
    assert "GOV-CANDLE-HIST-TGT-006" in str(e.value)


def test_direct_seam_still_refuses_d1():
    # generic/direct D1 publication is impossible: D1 is not in the seam's supported/derived grids
    assert "D1" not in seam.SUPPORTED_TF and "D1" not in seam.DERIVED_TF


def test_no_d1_history_written_by_producer():
    r = FakeRedis(); p = _producer(r)
    _feed(p, _six_h4())
    assert all(":history:" not in k for k in r.store)             # producer writes latest only, no history


def test_no_direct_candles_d1_no_24xh1_no_regime_no_shadow_no_redis_import_misuse():
    src = open(wire.__file__).read()
    assert '"candles_D1"' not in src and "'candles_D1'" not in src      # no midnight direct table
    assert '"candles_H1"' not in src and "'candles_H1'" not in src      # no H1 source
    assert wire.D1_REQUIRED_SOURCE_TIMEFRAME == "H4"                     # source is H4
    assert "regime_confidence" not in src and '"regime"' not in src and "'regime'" not in src
    assert "shadow" not in src.lower()
    # writer.publish performs the Redis SET; the producer itself constructs no client and does no direct .set/.zadd
    assert ".zadd(" not in src


def test_published_payload_no_forbidden_fields():
    r = FakeRedis(); p = _producer(r)
    _feed(p, _six_h4())
    blob = json.dumps(r.store).lower()
    for tok in ("regime", "structure", "choch", "order_block", "shadow"):
        assert tok not in blob
