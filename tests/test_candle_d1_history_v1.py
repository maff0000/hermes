"""HERMES D1 candle HISTORY series v1 — code-only, no live Redis I/O (fake client only).
WO-HELM-HERMES-D1-CANDLE-HISTORY-SERIES-0001.

Proves the D1 history surface mirrors the existing M1-H4 history key convention, admits ONLY sealed 6/6 D1
candles, preserves UTC + canonical XAU_USD, fails-loud below the indicator depth threshold, and is DARK by
default. No interpretive (regime/risk/strategy/signal/trade) semantics.
"""
from datetime import datetime, timedelta, timezone

import pytest

import utils.candle_d1_history_v1 as d1h
import utils.candle_d1_derivation_v1 as d1d
import utils.candle_contract_v1 as cc
import utils.candle_history_v1 as chv

UTC = timezone.utc
_D1O = datetime(2026, 6, 25, 22, 0, tzinfo=UTC)          # a valid D1 open (22:00Z NY-5PM grid)


def _h4(open_dt, o, hi, lo, c, v):
    return {"timestamp": open_dt, "open": o, "high": hi, "low": lo, "close": c, "volume": v}


def _six_children(d1_open=_D1O):
    opens = d1d.d1_child_h4_opens(d1_open)
    specs = [(2000, 2010, 1990, 2005, 10), (2005, 2030, 1995, 2020, 11), (2020, 2080, 2010, 2050, 12),
             (2050, 2060, 2000, 2030, 13), (2030, 2040, 1900, 1950, 14), (1950, 1975, 1940, 1970, 15)]
    return [_h4(opens[i], *specs[i]) for i in range(6)]


def _sealed_d1(d1_open=_D1O):
    """A real, sealed, status-OK 6/6 D1 envelope via the approved derivation path (not fabricated)."""
    env, _ = d1d.derive_d1(instrument="XAU_USD", d1_open=d1_open, h4_children=_six_children(d1_open),
                           generated_at_utc=cc.normalise_utc(d1_open) + timedelta(seconds=d1d.D1_SECONDS))
    assert env["status"] == "OK" and env["data"]["source_count"] == 6
    return env


class _FakeRedis:
    """Minimal in-memory fake — records SET/ZADD only. Proves the writer path without any live Redis."""
    def __init__(self):
        self.kv = {}
        self.z = {}
        self.sets = []
        self.zadds = []

    def set(self, key, value, ex=None):
        self.kv[key] = value
        self.sets.append((key, ex))

    def zadd(self, key, mapping):
        self.z.setdefault(key, {}).update(mapping)
        self.zadds.append((key, dict(mapping)))


# --------------------------------------------------------------------------- key convention
def test_d1_history_key_follows_existing_convention():
    ep = int(_D1O.timestamp())
    assert d1h.d1_history_key("XAU_USD", ep) == f"hermes:candles:XAU_USD:D1:history:v1:{ep}"
    assert d1h.d1_history_index_key("XAU_USD") == "hermes:candles:XAU_USD:D1:history:v1:index"
    # identical shape to the M1-H4 convention (only the timeframe token differs)
    h4 = chv.history_key("XAU_USD", "H4", ep)
    assert d1h.d1_history_key("XAU_USD", ep).replace(":D1:", ":H4:") == h4
    assert d1h.d1_history_index_key("XAU_USD").replace(":D1:", ":H4:") == chv.history_index_key("XAU_USD", "H4")


def test_d1_history_target_guard_rejects_latest_and_non_d1_and_alias():
    d1h.assert_d1_history_target("hermes:candles:XAU_USD:D1:history:v1:index")
    d1h.assert_d1_history_target(f"hermes:candles:XAU_USD:D1:history:v1:{int(_D1O.timestamp())}")
    for bad in ("hermes:candles:XAU_USD:D1:latest:v1",                  # latest, never a history target
                "hermes:candles:XAU_USD:H4:history:v1:index",            # non-D1 timeframe
                "hermes:candles:XAUUSD:D1:history:v1:index",             # alias
                "hermes:quote:XAU_USD:v1"):                               # non-candle
        with pytest.raises(ValueError):
            d1h.assert_d1_history_target(bad)


def test_canonical_instrument_required_alias_denied():
    with pytest.raises(ValueError):
        d1h.d1_history_key("XAUUSD", int(_D1O.timestamp()))
    with pytest.raises(ValueError):
        d1h.d1_history_index_key("EUR_USD")


# --------------------------------------------------------------------------- sealed-only admission
def test_sealed_complete_d1_admitted():
    env = _sealed_d1()
    assert d1h.assert_sealed_complete_d1(env) is True
    hist = d1h.build_d1_history_envelope(env, backfill_run_id="D1_FORWARD_SEAL_V1",
                                         backfill_inserted_at_utc=_gen(), source_table="x", source_timestamp_utc=_D1O)
    # snapshot preserves derived D1 provenance (source_count=6), only adds the history block
    assert hist["data"]["source_count"] == 6 and hist["data"]["timeframe"] == "D1"
    assert set(hist["history"]) == set(chv._HISTORY_FIELDS)
    cc.validate_candle_contract(hist)


def test_incomplete_or_unsealed_d1_rejected():
    env = _sealed_d1()
    # not closed
    e1 = _deep(env); e1["data"]["is_closed"] = False
    with pytest.raises(ValueError):
        d1h.assert_sealed_complete_d1(e1)
    # <6 source_count (short day)
    e2 = _deep(env); e2["data"]["source_count"] = 5
    with pytest.raises(ValueError):
        d1h.assert_sealed_complete_d1(e2)
    # non-OK status
    e3 = _deep(env); e3["status"] = "PARTIAL"
    with pytest.raises(ValueError):
        d1h.assert_sealed_complete_d1(e3)
    # a gap_state present
    e4 = _deep(env); e4["data"]["gap_state"] = "MISSING"
    with pytest.raises(ValueError):
        d1h.assert_sealed_complete_d1(e4)


def test_write_plan_is_utc_idempotent_and_targets_history_only():
    env = _sealed_d1()
    hist = d1h.build_d1_history_envelope(env, backfill_run_id="D1_FORWARD_SEAL_V1",
                                         backfill_inserted_at_utc=_gen(), source_table="x", source_timestamp_utc=_D1O)
    plan = d1h.build_d1_history_write_plan(hist)
    ep = int(_D1O.timestamp())
    assert plan["operation"] == "SET" and plan["key"].endswith(f":D1:history:v1:{ep}")
    assert plan["index_key"].endswith(":D1:history:v1:index")
    assert plan["index_score"] == ep and plan["index_member"] == str(ep)
    assert plan["idempotent"] is True and plan["write_mode"] == "HISTORY_INERT_NO_WRITE"
    assert hist["data"]["timestamp_utc"].endswith("Z")           # UTC preserved
    # same candle -> same plan (idempotent by construction)
    assert d1h.build_d1_history_write_plan(hist)["key"] == plan["key"]


# --------------------------------------------------------------------------- depth validator (downstream gate)
def test_depth_validator_blocks_below_26_passes_at_or_above():
    assert d1h.D1_MIN_DEPTH_FOR_INDICATORS == 26
    for shallow in (0, 1, 2, 14, 15, 25):
        assert d1h.d1_history_depth_sufficient(shallow) is False
        with pytest.raises(ValueError):
            d1h.assert_sufficient_d1_history_depth(shallow)
    for deep in (26, 35, 100):
        assert d1h.d1_history_depth_sufficient(deep) is True
        assert d1h.assert_sufficient_d1_history_depth(deep) is True


def test_retention_is_count_based_keep_newest():
    assert d1h.D1_HISTORY_RETAIN_COUNT == 35
    assert d1h.d1_history_retention_trim_plan(35)["would_trim"] == 0
    assert d1h.d1_history_retention_trim_plan(40)["would_trim"] == 5
    assert d1h.d1_history_retention_trim_plan(10)["would_trim"] == 0


# --------------------------------------------------------------------------- gated writer (dark by default)
def test_writer_disabled_by_default(monkeypatch):
    monkeypatch.delenv("HERMES_CANDLE_D1_HISTORY_ENABLED", raising=False)
    w = d1h.build_d1_history_writer_from_env()
    assert w.enabled is False
    assert w.on_d1_sealed(_sealed_d1())["written"] is False       # no-op, no client, never writes


def test_writer_enabled_without_authorised_halts(monkeypatch):
    monkeypatch.setenv("HERMES_CANDLE_D1_HISTORY_ENABLED", "true")
    monkeypatch.delenv("HERMES_CANDLE_D1_HISTORY_AUTHORISED", raising=False)
    with pytest.raises(SystemExit):
        d1h.build_d1_history_writer_from_env()


def test_enabled_writer_writes_only_history_keyspace_and_indexes():
    fake = _FakeRedis()
    w = d1h.D1HistoryWriter(redis_client=fake)
    res = w.on_d1_sealed(_sealed_d1())
    assert res["written"] is True
    ep = int(_D1O.timestamp())
    assert res["key"] == f"hermes:candles:XAU_USD:D1:history:v1:{ep}"
    # exactly one SET (history key, with TTL) + one ZADD (index) — never touches :latest:v1
    assert len(fake.sets) == 1 and fake.sets[0][1] == d1h.D1_HISTORY_TTL_SECONDS
    assert all(":D1:history:v1:" in k for k, _ in fake.sets)
    assert all(not k.endswith(":latest:v1") for k in fake.kv)
    assert fake.zadds == [("hermes:candles:XAU_USD:D1:history:v1:index", {str(ep): ep})]
    assert "XAUUSD" not in "".join(fake.kv)


def test_enabled_writer_faultisolated_never_raises():
    fake = _FakeRedis()
    w = d1h.D1HistoryWriter(redis_client=fake)
    # an unsealed env must be REJECTED, but on_d1_sealed must not raise (fault-isolated for the latest path)
    bad = _deep(_sealed_d1()); bad["data"]["source_count"] = 3
    res = w.on_d1_sealed(bad)
    assert res["written"] is False and res["reason"] == "D1_HISTORY_WRITE_FAIL"
    assert w.faults == 1 and not fake.sets


# --------------------------------------------------------------------------- no interpretive semantics
def test_no_forbidden_interpretive_tokens_in_module():
    import inspect
    # Scan CODE only: strip the module docstring (which carries an explanatory NEGATIVE declaration
    # "No regime/risk/strategy/signal/trade semantics") and pure-comment lines, so the test flags real
    # interpretive leakage into logic, not the honest negative declaration.
    raw = inspect.getsource(d1h)
    body = raw.replace(d1h.__doc__ or "", "")
    code = "\n".join(l for l in body.splitlines() if not l.strip().startswith("#")).lower()
    for tok in ("regime", "regime_confidence", "strategy", "signal", " buy ", " sell ", "no-go", "market_map"):
        assert tok not in code, f"forbidden interpretive token {tok!r} present in D1 history CODE"


# --------------------------------------------------------------------------- producer wiring (dark by default)
import utils.candle_d1_publish_wire_v1 as wire
import utils.candle_publisher_v1 as cp


class _H4:
    def __init__(self, open_dt, o=2000.0, hi=2010.0, lo=1990.0, c=2005.0, v=100, instrument="XAU_USD", tf="H4",
                 status="OK", is_closed=True, source_count=4, expected_source_count=4, source_coverage=1.0,
                 gap_state="NONE", source_timeframe="H1"):
        self.instrument, self.timeframe, self.timestamp = instrument, tf, open_dt
        self.open, self.high, self.low, self.close, self.volume = o, hi, lo, c, v
        self.status, self.is_closed = status, is_closed
        self.source_count, self.expected_source_count = source_count, expected_source_count
        self.source_coverage, self.gap_state, self.source_timeframe = source_coverage, gap_state, source_timeframe


def _cfg():
    return cp.CandlePublisherConfig(publish_enabled=True, publish_authorised=True, shadow_publish_enabled=False,
                                    shadow_authorised=False, namespace="hermes", contract_version="v1",
                                    redis_host="192.168.11.10", redis_port=6379, redis_db=0)


class _LatestFake:
    def __init__(self): self.store = {}
    def set(self, k, v, ex=None):
        assert isinstance(v, (str, bytes)); self.store[k] = (v, ex); return True


def _six_h4(d1_open=_D1O):
    opens = d1d.d1_child_h4_opens(d1_open)
    specs = [(2000, 2010, 1990, 2005, 100), (2005, 2030, 1995, 2020, 110), (2020, 2080, 2010, 2050, 120),
             (2050, 2060, 2000, 2030, 130), (2030, 2040, 1900, 1950, 140), (1950, 1975, 1940, 1970, 150)]
    return [_H4(opens[i], *specs[i]) for i in range(6)]


def _drive_seal(producer):
    for c in _six_h4():
        producer.on_h4_close(c)
    return producer.on_h4_close(_H4(_D1O + timedelta(hours=24)))    # next 22:00 -> seals prior D1 bucket


def test_producer_history_dark_by_default_latest_preserved():
    latest = _LatestFake()
    w = cp.SerializingCandleCanonicalWriter(config=_cfg(), redis_client=latest)
    producer = wire.CanonicalD1Producer(w, allowed_instruments=("XAU_USD",), source_timeframe="H4")
    # default history writer is the no-op DisabledD1HistoryWriter
    assert isinstance(producer._d1_history_writer, d1h.DisabledD1HistoryWriter)
    res = _drive_seal(producer)
    assert res["published"] is True                                     # D1 latest still published (preserved)
    assert any(":D1:latest:v1" in k for k in latest.store)             # latest key written
    assert not any(":D1:history:" in k for k in latest.store)          # NO history written (dark by default)


def test_producer_with_enabled_history_writer_appends_series_additively():
    latest, histfake = _LatestFake(), _FakeRedis()
    w = cp.SerializingCandleCanonicalWriter(config=_cfg(), redis_client=latest)
    producer = wire.CanonicalD1Producer(w, allowed_instruments=("XAU_USD",), source_timeframe="H4",
                                        d1_history_writer=d1h.D1HistoryWriter(redis_client=histfake))
    res = _drive_seal(producer)
    assert res["published"] is True                                     # latest preserved
    assert any(":D1:latest:v1" in k for k in latest.store)
    # history appended to the D1 history keyspace + index, additively (separate client)
    assert len(histfake.sets) == 1 and ":D1:history:v1:" in histfake.sets[0][0]
    assert histfake.zadds and histfake.zadds[0][0] == "hermes:candles:XAU_USD:D1:history:v1:index"


# --------------------------------------------------------------------------- helpers
def _gen(d1_open=_D1O):
    return cc.normalise_utc(d1_open) + timedelta(seconds=d1d.D1_SECONDS)


def _deep(obj):
    import copy
    return copy.deepcopy(obj)
