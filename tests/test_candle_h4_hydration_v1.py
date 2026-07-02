"""HERMES H4 H1 warm-start hydration — code-only, in-memory fake Redis. Nothing activates, no real I/O.
WO-HELM-HERMES-H4-H1-HYDRATION-WARMSTART-0001.
"""
import importlib
import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

import utils.candle_h4_hydration_v1 as hyd
import utils.candle_h4_publish_wire_v1 as wire
import utils.candle_h4_derivation_v1 as h4d
import utils.candle_publisher_v1 as cp
import utils.candle_history_v1 as hist
import utils.candle_contract_v1 as cc

UTC = timezone.utc
_H4O = datetime(2026, 7, 1, 2, 0, tzinfo=UTC)          # an H4 bucket open (02:00Z) -> block [02:00, 06:00)
_NOW = _H4O + timedelta(hours=2)                        # 04:00Z, inside the block


class FakeRedis:
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


class _H1:
    """A completed, status-OK H1 child by default. Override to model incomplete/non-OK/off-grid."""
    timeframe = "H1"

    def __init__(self, open_dt, instrument="XAU_USD", status="OK", is_closed=True, source_count=1,
                 expected_source_count=1, source_coverage=1.0, gap_state="NONE", o=2000.0, hi=2010.0, lo=1990.0,
                 c=2005.0, v=100, tf="H1"):
        self.instrument = instrument
        self.timeframe = tf
        self.timestamp = open_dt
        self.open, self.high, self.low, self.close, self.volume = o, hi, lo, c, v
        self.status, self.is_closed = status, is_closed
        self.source_count, self.expected_source_count = source_count, expected_source_count
        self.source_coverage, self.gap_state = source_coverage, gap_state


class _StubD1:
    enabled = True

    def __init__(self):
        self.received = []

    def on_h4_close(self, view):
        self.received.append({"status": getattr(view, "status", None),
                              "source_count": getattr(view, "source_count", None)})
        return {"published": False, "reason": "STUB"}


def _cfg():
    return cp.CandlePublisherConfig(publish_enabled=True, publish_authorised=True, shadow_publish_enabled=False,
                                    shadow_authorised=False, namespace="hermes", contract_version="v1",
                                    redis_host="192.168.11.10", redis_port=6379, redis_db=0)


def _producer(client=None, d1_producer=None):
    w = cp.SerializingCandleCanonicalWriter(config=_cfg(), redis_client=client or FakeRedis())
    return wire.CanonicalH4Producer(w, allowed_instruments=("XAU_USD",), d1_producer=d1_producer)


def _block_opens(n):
    return [_H4O + timedelta(hours=i) for i in range(n)]     # 02:00,03:00,04:00,05:00


def _h1_hist_env(open_dt, status="OK", is_closed=True, source_count=1, expected=1, coverage=1.0, gap="NONE",
                 o=2000.0, hi=2010.0, lo=1990.0, c=2005.0, v=100):
    return {"status": status, "data": {"instrument": "XAU_USD", "timeframe": "H1",
            "timestamp_utc": cc._fmt(open_dt), "open": o, "high": hi, "low": lo, "close": c, "volume": v,
            "is_closed": is_closed, "source_count": source_count, "expected_source_count": expected,
            "source_coverage": coverage, "gap_state": gap}}


def _seed_h1_history(client, open_dt, **kw):
    ep = int(open_dt.timestamp())
    client.store[hist.history_key("XAU_USD", "H1", ep)] = json.dumps(_h1_hist_env(open_dt, **kw))
    client.zadd(hist.history_index_key("XAU_USD", "H1"), {str(ep): ep})


class _Logger:
    def __init__(self):
        self.lines = []

    def info(self, *a):
        self.lines.append(a[0] % a[1:] if len(a) > 1 else a[0])


# ============================ producer.hydrate — direct ============================
def test_empty_history_boundary_buffer_zero():
    p = _producer()
    r = p.hydrate([], now=_NOW)
    assert r["succeeded"] is True and r["buffer_length"] == 0 and r["remaining_children_required"] == 4
    assert p._current["XAU_USD"] == int(_H4O.timestamp())          # live tracking can continue
    assert r["h4_block_start_utc"].startswith("2026-07-01T02:00")


def test_partial_hydration_seeds_two_in_order():
    opens = _block_opens(2)                                        # 02:00, 03:00
    children = [_H1(o, c=2005.0 + i) for i, o in enumerate(opens)]
    p = _producer()
    r = p.hydrate(children, now=_NOW)
    assert r["buffer_length"] == 2 and r["remaining_children_required"] == 2
    buf = p._buf["XAU_USD"][int(_H4O.timestamp())]
    assert [cc.normalise_utc(c.timestamp) for c in buf] == [cc.normalise_utc(o) for o in opens]
    assert [c.close for c in buf] == [2005.0, 2006.0]


def test_cross_bucket_filter_before_and_after():
    before = _H1(_H4O - timedelta(hours=1))                        # 01:00 -> previous bucket
    after = _H1(_H4O + timedelta(hours=4))                         # 06:00 -> next bucket
    p = _producer()
    r = p.hydrate([before, _H1(_block_opens(1)[0]), after], now=_NOW)
    assert r["buffer_length"] == 1
    reasons = {x["reason"] for x in r["rejected"]}
    assert reasons == {"OUTSIDE_ACTIVE_H4_BLOCK"}


def test_wrong_boundary_off_grid_rejected():
    off = _H1(_H4O + timedelta(minutes=30))                        # 02:30 — inside block but off the hour
    p = _producer()
    r = p.hydrate([off], now=_NOW)
    assert r["buffer_length"] == 0 and r["rejected"][0]["reason"] == "WRONG_BOUNDARY_OFF_FIXED_GRID"


def test_non_ok_child_rejected():
    bad = _H1(_block_opens(1)[0], status="SOURCE_INCOMPLETE", source_coverage=0.5, gap_state="GAP_DETECTED")
    p = _producer()
    r = p.hydrate([bad], now=_NOW)
    assert r["buffer_length"] == 0 and r["rejected"][0]["reason"] == "NOT_OK_OR_INCOMPLETE_H1"


def test_duplicate_child_deduplicated_with_evidence():
    o = _block_opens(1)[0]
    p = _producer()
    r = p.hydrate([_H1(o), _H1(o)], now=_NOW)
    assert r["buffer_length"] == 1 and r["accepted_count"] == 1
    assert any(x["reason"] == "DUPLICATE_CHILD" for x in r["rejected"])


def test_non_h1_and_non_xau_rejected():
    p = _producer()
    r = p.hydrate([_H1(_block_opens(1)[0], tf="M15"), _H1(_block_opens(2)[1], instrument="EUR_USD")], now=_NOW)
    reasons = {x["reason"] for x in r["rejected"]}
    assert "WRONG_TIMEFRAME" in reasons and "INSTRUMENT_NOT_ALLOWLISTED" in reasons and r["buffer_length"] == 0


def test_complete_four_ready_but_not_published_by_hydration():
    children = [_H1(o) for o in _block_opens(4)]
    p = _producer()
    r = p.hydrate(children, now=_NOW)
    assert r["buffer_length"] == 4 and r["h4_complete_4of4"] is True
    assert r["h4_published_by_hydration"] is False
    assert r["h4_status_after_hydration"] == "READY_PENDING_LIVE_ROLLOVER"


def test_hydrate_idempotent():
    children = [_H1(o) for o in _block_opens(2)]
    p = _producer()
    r1 = p.hydrate(children, now=_NOW)
    r2 = p.hydrate(children, now=_NOW)
    assert r1["accepted_child_open_epochs"] == r2["accepted_child_open_epochs"]
    assert r2["buffer_length"] == 2 and len(p._buf["XAU_USD"][int(_H4O.timestamp())]) == 2


def test_fixed_h4_grid_no_dst():
    for now in (datetime(2026, 1, 15, 4, 30, tzinfo=UTC), datetime(2026, 7, 15, 4, 30, tzinfo=UTC)):
        bo = h4d.h4_bucket_open(now)
        assert bo.hour in h4d.H4_ANCHOR_HOURS_UTC and (bo.minute, bo.second) == (0, 0)


# ============================ RESTART-SPANNING scenario (test 13) ============================
def test_restart_spanning_h4_then_live_rollover_seals_complete_and_feeds_d1():
    # boot mid-bucket after 2 H1 already exist -> hydrate 2/4; live next two H1 close -> 4/4;
    # next bucket's first H1 triggers a GENUINE live rollover seal -> complete OK H4 offered to D1.
    d1 = _StubD1()
    client = FakeRedis()
    p = _producer(client=client, d1_producer=d1)
    hr = p.hydrate([_H1(_H4O), _H1(_H4O + timedelta(hours=1))], now=_NOW)   # 02:00, 03:00
    assert hr["buffer_length"] == 2 and not client.sets                     # hydration wrote NOTHING
    p.on_h1_close(_H1(_H4O + timedelta(hours=2)))                           # live 04:00
    p.on_h1_close(_H1(_H4O + timedelta(hours=3)))                           # live 05:00
    res = p.on_h1_close(_H1(_H4O + timedelta(hours=4)))                     # 06:00 -> next bucket -> seal 02:00
    assert res["published"] is True and res["status"] == "OK" and res["source_count"] == 4
    assert len(client.sets) == 1                                            # the live seal is the ONLY write
    assert d1.received and d1.received[-1] == {"status": "OK", "source_count": 4}   # complete OK H4 -> D1


# ============================ warmstart_h4_from_env — gate + source ============================
def test_gate_cold_start_safe_noop(monkeypatch):
    monkeypatch.delenv(hyd.WARMSTART_ENABLED_ENV, raising=False)
    lg = _Logger()
    r = hyd.warmstart_h4_from_env(_producer(), now=_NOW, logger=lg)
    assert r["skipped"] is True and r["reason"] == "COLD_START_STRATEGY_ACTIVE" and r["buffer_length"] == 0
    assert hyd.COLD_START_LOG in lg.lines


def test_gate_enabled_unauthorised_exits_104(monkeypatch):
    monkeypatch.setenv(hyd.WARMSTART_ENABLED_ENV, "true")
    monkeypatch.delenv(hyd.WARMSTART_AUTHORISED_ENV, raising=False)
    with pytest.raises(SystemExit) as e:
        hyd.warmstart_h4_from_env(_producer(), now=_NOW, logger=_Logger())
    assert e.value.code == 104


def test_warmstart_disabled_producer_is_noop(monkeypatch):
    monkeypatch.setenv(hyd.WARMSTART_ENABLED_ENV, "true")
    monkeypatch.setenv(hyd.WARMSTART_AUTHORISED_ENV, "true")
    r = hyd.warmstart_h4_from_env(wire.DisabledH4Producer(), now=_NOW, redis_client=FakeRedis(), logger=_Logger())
    assert r["attempted"] is False and r["reason"] == "H4_PRODUCER_DISABLED"


def test_warmstart_reads_redis_h1_history_no_writes(monkeypatch):
    monkeypatch.setenv(hyd.WARMSTART_ENABLED_ENV, "true")
    monkeypatch.setenv(hyd.WARMSTART_AUTHORISED_ENV, "true")
    client = FakeRedis()
    for o in _block_opens(2):
        _seed_h1_history(client, o)
    _seed_h1_history(client, _H4O - timedelta(hours=1))            # previous bucket 01:00 -> out of range
    p = _producer(client=FakeRedis())
    r = hyd.warmstart_h4_from_env(p, now=_NOW, redis_client=client, logger=_Logger())
    assert r["succeeded"] is True and r["source_selected"] == hyd.SOURCE_REDIS_H1_HISTORY
    assert r["buffer_length"] == 2 and r["candidate_count"] == 2   # only in-block children read
    assert client.sets == []                                       # NO Redis writes by hydration


def test_warmstart_rejects_non_ok_from_history(monkeypatch):
    monkeypatch.setenv(hyd.WARMSTART_ENABLED_ENV, "true")
    monkeypatch.setenv(hyd.WARMSTART_AUTHORISED_ENV, "true")
    client = FakeRedis()
    opens = _block_opens(2)
    _seed_h1_history(client, opens[0])
    _seed_h1_history(client, opens[1], status="STALE", coverage=0.5, gap="GAP_DETECTED")
    p = _producer(client=FakeRedis())
    r = hyd.warmstart_h4_from_env(p, now=_NOW, redis_client=client, logger=_Logger())
    assert r["buffer_length"] == 1 and r["rejected_count"] == 1
    assert r["rejected"][0]["reason"] == "NOT_OK_OR_INCOMPLETE_H1"


# ============================ import safety / no-IO / policy / D1 protection ============================
def test_no_redis_sql_socket_thread_at_import():
    src = open(hyd.__file__).read()
    assert "\nimport redis" not in src and "redis.Redis(" not in src and "\nfrom redis" not in src
    assert "pymysql" not in src and "\nimport socket" not in src and "\nimport threading" not in src
    assert not any(t.name.startswith("hermes-h4-warmstart") for t in threading.enumerate())
    importlib.reload(hyd)                                          # re-import is side-effect-free


def test_no_hardcoded_redis_target_in_module():
    src = open(hyd.__file__).read()
    for marker in ("192.168.", "127.0.0.1", "localhost", "6379", "requirepass", "password=", "redis://"):
        assert marker not in src


def test_h4_policy_preserved():
    assert hyd.DEFAULT_SOURCE == hyd.SOURCE_REDIS_H1_HISTORY == "redis_h1_history"
    assert hyd.h4d.H1_TIMEFRAME == "H1" and h4d.H4_EXPECTED_CHILDREN == 4
    assert hist.history_index_key("XAU_USD", "H1").endswith("H1:history:v1:index")
    assert 22 in h4d.H4_ANCHOR_HOURS_UTC                           # fixed NY-5PM 22:00 grid


def test_d1_protection_hydration_does_not_force_d1_or_write():
    d1 = _StubD1()
    p = _producer(d1_producer=d1)
    r = p.hydrate([_H1(o) for o in _block_opens(4)], now=_NOW)     # even a complete 4/4
    assert r["h4_published_by_hydration"] is False
    assert d1.received == []                                       # hydration never offers to D1 / never seals
