"""HERMES D1 H4 warm-start hydration — code-only, in-memory fake Redis. Nothing activates, no real I/O.
WO-HELM-HERMES-D1-H4-HYDRATION-WARMSTART-0001.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import utils.candle_d1_hydration_v1 as hyd
import utils.candle_d1_publish_wire_v1 as wire
import utils.candle_d1_derivation_v1 as d1d
import utils.candle_publisher_v1 as cp
import utils.candle_history_v1 as hist
import utils.candle_contract_v1 as cc

UTC = timezone.utc
_D1O = datetime(2026, 6, 25, 22, 0, tzinfo=UTC)         # a D1 block open (22:00Z)
_NOW = _D1O + timedelta(hours=20)                       # inside the _D1O block (before next 22:00 rollover)


class FakeRedis:
    """In-memory fake: get/set + a single ZSET for the H4 history index. Records every write for no-write proofs."""
    def __init__(self):
        self.store = {}
        self.sets = []
        self.z = {}

    def set(self, k, v, ex=None):
        assert isinstance(v, (str, bytes))
        self.store[k] = v
        self.sets.append((k, v, ex))
        return True

    def get(self, k):
        return self.store.get(k)

    def zadd(self, k, mapping):
        self.z.setdefault(k, {}).update({m: float(s) for m, s in mapping.items()})
        return len(mapping)

    def zrangebyscore(self, k, mn, mx):
        items = sorted(self.z.get(k, {}).items(), key=lambda kv: kv[1])
        return [m for m, s in items if mn <= s <= mx]


class _H4:
    """A SEALED, complete status-OK H4 child view by default. Override to model incomplete/non-OK/off-grid."""
    timeframe = "H4"

    def __init__(self, open_dt, instrument="XAU_USD", status="OK", is_closed=True, source_count=4,
                 expected_source_count=4, source_coverage=1.0, gap_state="NONE", o=2000.0, hi=2010.0, lo=1990.0,
                 c=2005.0, v=100, tf="H4"):
        self.instrument = instrument
        self.timeframe = tf
        self.timestamp = open_dt
        self.open, self.high, self.low, self.close, self.volume = o, hi, lo, c, v
        self.status, self.is_closed = status, is_closed
        self.source_count, self.expected_source_count = source_count, expected_source_count
        self.source_coverage, self.gap_state = source_coverage, gap_state


def _cfg():
    return cp.CandlePublisherConfig(publish_enabled=True, publish_authorised=True, shadow_publish_enabled=False,
                                    shadow_authorised=False, namespace="hermes", contract_version="v1",
                                    redis_host="192.168.11.10", redis_port=6379, redis_db=0)


def _producer(client=None):
    w = cp.SerializingCandleCanonicalWriter(config=_cfg(), redis_client=client or FakeRedis())
    return wire.CanonicalD1Producer(w, allowed_instruments=("XAU_USD",), source_timeframe="H4")


def _block_opens(n):
    return d1d.d1_child_h4_opens(_D1O)[:n]                # first n of 22,02,06,10,14,18


def _h4_hist_env(open_dt, status="OK", is_closed=True, source_count=4, expected=4, coverage=1.0, gap="NONE",
                 o=2000.0, hi=2010.0, lo=1990.0, c=2005.0, v=100):
    return {"status": status, "data": {"instrument": "XAU_USD", "timeframe": "H4",
            "timestamp_utc": cc._fmt(open_dt), "open": o, "high": hi, "low": lo, "close": c, "volume": v,
            "is_closed": is_closed, "source_count": source_count, "expected_source_count": expected,
            "source_coverage": coverage, "gap_state": gap}}


def _seed_h4_history(client, open_dt, **kw):
    ep = int(open_dt.timestamp())
    client.store[hist.history_key("XAU_USD", "H4", ep)] = json.dumps(_h4_hist_env(open_dt, **kw))
    client.zadd(hist.history_index_key("XAU_USD", "H4"), {str(ep): ep})


class _Logger:
    def __init__(self):
        self.lines = []

    def info(self, *a):
        self.lines.append(a[0] % a[1:] if len(a) > 1 else a[0])


# ============================ producer.hydrate — direct ============================
def test_empty_history_boundary_buffer_zero():
    p = _producer()
    r = p.hydrate([], now=_NOW)
    assert r["succeeded"] is True and r["buffer_length"] == 0 and r["accepted_count"] == 0
    assert r["remaining_children_required"] == 6 and r["d1_remains_gated_amber"] is True
    assert p._current["XAU_USD"] == int(_D1O.timestamp())          # live tracking can continue
    assert r["d1_block_start_utc"].endswith("Z") and r["d1_block_start_utc"].startswith("2026-06-25T22:00")


def test_partial_day_hydration_seeds_three_in_order():
    opens = _block_opens(3)
    children = [_H4(o, c=2005.0 + i) for i, o in enumerate(opens)]
    p = _producer()
    r = p.hydrate(children, now=_NOW)
    assert r["buffer_length"] == 3 and r["remaining_children_required"] == 3
    buf = p._buf["XAU_USD"][int(_D1O.timestamp())]
    assert [cc.normalise_utc(c.timestamp) for c in buf] == [cc.normalise_utc(o) for o in opens]   # order preserved
    assert [c.close for c in buf] == [2005.0, 2006.0, 2007.0]                                     # values preserved


def test_cross_day_filter_discards_pre_anchor_h4():
    pre = _H4(_D1O - timedelta(hours=4))                  # 18:00 of the PREVIOUS block
    p = _producer()
    r = p.hydrate([pre, _H4(_block_opens(1)[0])], now=_NOW)
    assert r["buffer_length"] == 1
    assert any(x["reason"] == "OUTSIDE_ACTIVE_D1_BLOCK" for x in r["rejected"])


def test_wrong_boundary_off_grid_rejected():
    off = _H4(_D1O + timedelta(hours=1))                  # 23:00 — inside block but off the fixed grid
    p = _producer()
    r = p.hydrate([off], now=_NOW)
    assert r["buffer_length"] == 0
    assert r["rejected"][0]["reason"] == "WRONG_BOUNDARY_OFF_FIXED_GRID"


def test_non_ok_child_rejected():
    bad = _H4(_block_opens(1)[0], status="SOURCE_INCOMPLETE", source_count=3, source_coverage=0.75)
    p = _producer()
    r = p.hydrate([bad], now=_NOW)
    assert r["buffer_length"] == 0 and r["rejected"][0]["reason"] == "NOT_OK_OR_INCOMPLETE_H4"


def test_duplicate_child_deduplicated_with_evidence():
    o = _block_opens(1)[0]
    p = _producer()
    r = p.hydrate([_H4(o), _H4(o)], now=_NOW)
    assert r["buffer_length"] == 1 and r["accepted_count"] == 1
    assert any(x["reason"] == "DUPLICATE_CHILD" for x in r["rejected"])


def test_non_h4_and_non_xau_rejected():
    p = _producer()
    r = p.hydrate([_H4(_block_opens(1)[0], tf="H1"), _H4(_block_opens(2)[1], instrument="EUR_USD")], now=_NOW)
    reasons = {x["reason"] for x in r["rejected"]}
    assert "WRONG_TIMEFRAME" in reasons and "INSTRUMENT_NOT_ALLOWLISTED" in reasons and r["buffer_length"] == 0


def test_complete_six_ready_but_not_published_by_hydration():
    children = [_H4(o) for o in d1d.d1_child_h4_opens(_D1O)]
    p = _producer()
    r = p.hydrate(children, now=_NOW)
    assert r["buffer_length"] == 6 and r["d1_complete_6of6"] is True
    assert r["d1_published_by_hydration"] is False and r["d1_remains_gated_amber"] is True
    assert r["d1_status_after_hydration"] == "READY_PENDING_LIVE_ROLLOVER"


def test_warmstart_then_live_rollover_publishes_genuine_seal():
    # the fix end-to-end: hydrate the full current block, then a LIVE next-day H4 close seals+publishes it
    client = FakeRedis()
    p = _producer(client=client)
    p.hydrate([_H4(o) for o in d1d.d1_child_h4_opens(_D1O)], now=_NOW)
    assert not client.sets                               # hydration wrote NOTHING
    res = p.on_h4_close(_H4(_D1O + timedelta(hours=24)))  # next 22:00 -> rollover seals the hydrated block
    assert res["published"] is True and res["status"] == "OK" and res["source_count"] == 6
    assert len(client.sets) == 1                          # the live seal is the ONLY write


def test_hydrate_idempotent():
    children = [_H4(o) for o in _block_opens(3)]
    p = _producer()
    r1 = p.hydrate(children, now=_NOW)
    r2 = p.hydrate(children, now=_NOW)
    assert r1["accepted_child_open_epochs"] == r2["accepted_child_open_epochs"]
    assert r2["buffer_length"] == 3 and len(p._buf["XAU_USD"][int(_D1O.timestamp())]) == 3


def test_fixed_2200_anchor_no_dst():
    # anchor is fixed 22:00:00 UTC regardless of date (no DST shift): a winter and a summer 'now' both -> 22:00Z
    for now in (datetime(2026, 1, 15, 9, 0, tzinfo=UTC), datetime(2026, 7, 15, 9, 0, tzinfo=UTC)):
        bo = d1d.d1_bucket_open(now)
        assert (bo.hour, bo.minute, bo.second) == (22, 0, 0)
        assert d1d.assert_d1_open_anchor(bo) is True


# ============================ warmstart_d1_from_env — gate + source ============================
def test_gate_cold_start_safe_noop(monkeypatch):
    monkeypatch.delenv(hyd.WARMSTART_ENABLED_ENV, raising=False)
    lg = _Logger()
    r = hyd.warmstart_d1_from_env(_producer(), now=_NOW, logger=lg)
    assert r["skipped"] is True and r["reason"] == "COLD_START_STRATEGY_ACTIVE" and r["buffer_length"] == 0
    assert hyd.COLD_START_LOG in lg.lines


def test_gate_enabled_unauthorised_exits_103(monkeypatch):
    monkeypatch.setenv(hyd.WARMSTART_ENABLED_ENV, "true")
    monkeypatch.delenv(hyd.WARMSTART_AUTHORISED_ENV, raising=False)
    with pytest.raises(SystemExit) as e:
        hyd.warmstart_d1_from_env(_producer(), now=_NOW, logger=_Logger())
    assert e.value.code == 103


def test_warmstart_disabled_producer_is_noop(monkeypatch):
    monkeypatch.setenv(hyd.WARMSTART_ENABLED_ENV, "true")
    monkeypatch.setenv(hyd.WARMSTART_AUTHORISED_ENV, "true")
    r = hyd.warmstart_d1_from_env(wire.DisabledD1Producer(), now=_NOW, redis_client=FakeRedis(), logger=_Logger())
    assert r["attempted"] is False and r["reason"] == "D1_PRODUCER_DISABLED"


def test_warmstart_reads_redis_h4_history_no_writes(monkeypatch):
    monkeypatch.setenv(hyd.WARMSTART_ENABLED_ENV, "true")
    monkeypatch.setenv(hyd.WARMSTART_AUTHORISED_ENV, "true")
    client = FakeRedis()
    for o in _block_opens(3):
        _seed_h4_history(client, o)
    _seed_h4_history(client, _D1O - timedelta(hours=4))   # previous block's 18:00 -> out of range, not read
    p = _producer(client=FakeRedis())
    r = hyd.warmstart_d1_from_env(p, now=_NOW, redis_client=client, logger=_Logger())
    assert r["succeeded"] is True and r["source_selected"] == hyd.SOURCE_REDIS_H4_HISTORY
    assert r["buffer_length"] == 3 and r["candidate_count"] == 3   # only the in-block children were read
    assert client.sets == []                                       # NO Redis writes by hydration


def test_warmstart_rejects_non_ok_from_history(monkeypatch):
    monkeypatch.setenv(hyd.WARMSTART_ENABLED_ENV, "true")
    monkeypatch.setenv(hyd.WARMSTART_AUTHORISED_ENV, "true")
    client = FakeRedis()
    opens = _block_opens(2)
    _seed_h4_history(client, opens[0])
    _seed_h4_history(client, opens[1], status="STALE", source_count=2, coverage=0.5, gap="GAP_DETECTED")
    p = _producer(client=FakeRedis())
    r = hyd.warmstart_d1_from_env(p, now=_NOW, redis_client=client, logger=_Logger())
    assert r["buffer_length"] == 1 and r["rejected_count"] == 1
    assert r["rejected"][0]["reason"] == "NOT_OK_OR_INCOMPLETE_H4"


# ============================ import safety / no-IO / policy / gate preservation ============================
def test_no_redis_sql_socket_thread_at_import():
    # the module constructs NO client at import (redis imported nowhere; no SQL/socket/thread); the read client
    # is INJECTED or taken from the producer's writer. No-writes is proven behaviourally (client.sets == []).
    src = open(hyd.__file__).read()
    assert "\nimport redis" not in src and "redis.Redis(" not in src and "\nfrom redis" not in src
    assert "pymysql" not in src and "\nimport socket" not in src and "\nimport threading" not in src
    # re-import is side-effect-free (no connection at import time)
    import importlib
    importlib.reload(hyd)


def test_d1_policy_markers_preserved():
    # source is H4-only (never a direct candles_D1 table, never a 24xH1 shortcut): the reader keys off the H4
    # history index, the default source is the H4 history surface, and the derivation timeframe is H4.
    assert hyd.DEFAULT_SOURCE == hyd.SOURCE_REDIS_H4_HISTORY == "redis_h4_history"
    assert hyd.d1d.H4_TIMEFRAME == "H4"
    assert hist.history_index_key("XAU_USD", "H4").endswith("H4:history:v1:index")
    # fixed 22:00 anchor, no DST (a non-22:00 open is rejected loud)
    assert d1d.D1_ANCHOR_HOUR_UTC == 22
    with pytest.raises(ValueError):
        d1d.assert_d1_open_anchor(_D1O.replace(hour=0))


def test_no_hardcoded_redis_target_in_module():
    src = open(hyd.__file__).read()
    for marker in ("192.168.", "127.0.0.1", "localhost", "6379", "requirepass", "password=", "redis://"):
        assert marker not in src                            # read client is injected / from producer writer


def test_d1_gates_remain_amber_after_hydration():
    p = _producer()
    r = p.hydrate([_H4(o) for o in _block_opens(4)], now=_NOW)
    assert r["d1_remains_gated_amber"] is True and r["d1_published_by_hydration"] is False
    # hydration touches only the in-memory D1 buffer — never D1 history/indicators/features/levels keys
    assert "d1_status_after_hydration" in r and r["buffer_length"] == 4
