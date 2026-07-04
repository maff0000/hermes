"""HERMES H4 prior-completed-block warm-start RESEAL — code-only, in-memory fake Redis, no real I/O.
WO-HELM-HERMES-H4-PRIOR-COMPLETED-BLOCK-WARMSTART-RESEAL-0001.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import utils.candle_h4_hydration_v1 as hyd
import utils.candle_h4_publish_wire_v1 as wire
import utils.candle_h4_derivation_v1 as h4d
import utils.candle_d1_derivation_v1 as d1d
import utils.candle_publisher_v1 as cp
import utils.candle_history_v1 as hist
import utils.candle_contract_v1 as cc

UTC = timezone.utc
_NOW = datetime(2026, 7, 3, 14, 30, tzinfo=UTC)          # forming block 14:00 -> prior completed block = 10:00
_PRIOR = datetime(2026, 7, 3, 10, 0, tzinfo=UTC)
_PRIOR_H1 = [_PRIOR + timedelta(hours=i) for i in range(4)]   # 10,11,12,13


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.z = {}
        self.sets = []

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v, ex=None):
        assert isinstance(v, (str, bytes))
        self.store[k] = v
        self.sets.append((k, v, ex))
        return True

    def zadd(self, k, mapping):
        self.z.setdefault(k, {}).update({m: float(s) for m, s in mapping.items()})
        return len(mapping)

    def zrangebyscore(self, k, mn, mx):
        items = sorted(self.z.get(k, {}).items(), key=lambda kv: kv[1])
        return [m for m, s in items if mn <= s <= mx]


class FakeHistoryWriter:
    """Governed-equivalent H4 history writer: idempotent snapshot of a sealed H4 envelope into its history key + index."""
    enabled = True

    def __init__(self, redis):
        self.redis = redis
        self.calls = []

    def on_h4_sealed(self, env, *, inserted_at_utc):
        self.calls.append(env["data"]["timestamp_utc"])
        d = env["data"]
        ep = int(datetime.strptime(d["timestamp_utc"][:-1], cc._UTC_MS).replace(tzinfo=UTC).timestamp())
        self.redis.store[hist.history_key("XAU_USD", "H4", ep)] = json.dumps({"status": env["status"], "data": d})
        self.redis.zadd(hist.history_index_key("XAU_USD", "H4"), {str(ep): ep})
        return {"wrote": True}


class _DisabledHistoryWriter:
    enabled = False

    def on_h4_sealed(self, *a, **k):
        return {"wrote": False, "reason": "DISABLED"}


class _H1:
    timeframe = "H1"

    def __init__(self, open_dt, status="OK", is_closed=True, source_count=1, expected_source_count=1,
                 source_coverage=1.0, gap_state="NONE", o=2000.0, hi=2010.0, lo=1990.0, c=2005.0, v=100):
        self.instrument = "XAU_USD"
        self.timestamp = open_dt
        self.open, self.high, self.low, self.close, self.volume = o, hi, lo, c, v
        self.status, self.is_closed = status, is_closed
        self.source_count, self.expected_source_count = source_count, expected_source_count
        self.source_coverage, self.gap_state = source_coverage, gap_state


def _cfg():
    return cp.CandlePublisherConfig(publish_enabled=True, publish_authorised=True, shadow_publish_enabled=False,
                                    shadow_authorised=False, namespace="hermes", contract_version="v1",
                                    redis_host="192.168.11.10", redis_port=6379, redis_db=0)


def _producer(fake, history_writer=None):
    w = cp.SerializingCandleCanonicalWriter(config=_cfg(), redis_client=fake)
    return wire.CanonicalH4Producer(w, allowed_instruments=("XAU_USD",),
                                    history_forward_writer=history_writer if history_writer is not None else FakeHistoryWriter(fake))


def _h1_env(open_dt, status="OK", is_closed=True, sc=1, esc=1, cov=1.0, gap="NONE", o=2000.0, hi=2010.0, lo=1990.0, c=2005.0, v=100):
    return {"status": status, "data": {"instrument": "XAU_USD", "timeframe": "H1", "timestamp_utc": cc._fmt(open_dt),
            "open": o, "high": hi, "low": lo, "close": c, "volume": v, "is_closed": is_closed,
            "source_count": sc, "expected_source_count": esc, "source_coverage": cov, "gap_state": gap}}


def _seed_h1(fake, open_dt, **kw):
    ep = int(open_dt.timestamp())
    fake.store[hist.history_key("XAU_USD", "H1", ep)] = json.dumps(_h1_env(open_dt, **kw))
    fake.zadd(hist.history_index_key("XAU_USD", "H1"), {str(ep): ep})


def _seed_full_prior(fake):
    for o in _PRIOR_H1:
        _seed_h1(fake, o)


# ============================ A. happy path ============================
def test_recover_prior_completed_h4_from_four_h1():
    fake = FakeRedis(); _seed_full_prior(fake); p = _producer(fake)
    r = hyd.reseal_prior_completed_h4(p, redis_client=fake, now=_NOW)
    assert r["status"] == "recovered"
    assert r["prior_h4_block_open_utc"].startswith("2026-07-03T10:00")
    assert r["source_count"] == 4 and r["source_coverage"] == 1.0 and r["gap_state"] in ("NONE", None)
    assert r["h4_status"] == "OK"
    # H4 history now holds the recovered 10:00 block (deterministic derive)
    hk = hist.history_key("XAU_USD", "H4", int(_PRIOR.timestamp()))
    assert hk in fake.store
    hd = json.loads(fake.store[hk])["data"]
    assert hd["source_count"] == 4 and hd["timestamp_utc"].startswith("2026-07-03T10:00")
    assert r["d1_relevant"] is True and r["h4_latest_updated"] is True


# ============================ B. fail-closed ============================
def test_missing_h1_child_fails_closed():
    fake = FakeRedis()
    for o in _PRIOR_H1[:3]:
        _seed_h1(fake, o)                                    # only 3 of 4
    r = hyd.reseal_prior_completed_h4(_producer(fake), redis_client=fake, now=_NOW)
    assert r["status"] == "failed_closed" and r["reason"] == "MISSING_H1_CHILD"


def test_non_ok_h1_child_fails_closed():
    fake = FakeRedis()
    _seed_h1(fake, _PRIOR_H1[0]); _seed_h1(fake, _PRIOR_H1[1]); _seed_h1(fake, _PRIOR_H1[2])
    _seed_h1(fake, _PRIOR_H1[3], status="STALE")
    r = hyd.reseal_prior_completed_h4(_producer(fake), redis_client=fake, now=_NOW)
    assert r["status"] == "failed_closed" and r["reason"] == "H1_CHILD_NOT_OK_OR_INCOMPLETE"


def test_incomplete_h1_child_fails_closed():
    fake = FakeRedis()
    for o in _PRIOR_H1[:3]:
        _seed_h1(fake, o)
    _seed_h1(fake, _PRIOR_H1[3], sc=1, esc=1, cov=0.5, gap="GAP_DETECTED")
    r = hyd.reseal_prior_completed_h4(_producer(fake), redis_client=fake, now=_NOW)
    assert r["status"] == "failed_closed" and r["reason"] == "H1_CHILD_NOT_OK_OR_INCOMPLETE"


def test_off_grid_h1_child_fails_closed():
    fake = FakeRedis(); _seed_full_prior(fake)
    _seed_h1(fake, _PRIOR + timedelta(minutes=30))           # 10:30 off the hourly grid, inside the block
    r = hyd.reseal_prior_completed_h4(_producer(fake), redis_client=fake, now=_NOW)
    assert r["status"] == "failed_closed" and r["reason"] == "H1_OFF_HOUR_GRID"


def test_malformed_h1_child_fails_closed():
    fake = FakeRedis()
    for o in _PRIOR_H1[:3]:
        _seed_h1(fake, o)
    ep = int(_PRIOR_H1[3].timestamp())
    fake.store[hist.history_key("XAU_USD", "H1", ep)] = "{not json"
    fake.zadd(hist.history_index_key("XAU_USD", "H1"), {str(ep): ep})
    r = hyd.reseal_prior_completed_h4(_producer(fake), redis_client=fake, now=_NOW)
    assert r["status"] == "failed_closed" and r["reason"] == "MALFORMED_H1_CHILD"


def test_existing_h4_mismatch_fails_closed_no_overwrite():
    fake = FakeRedis(); _seed_full_prior(fake)
    ep = int(_PRIOR.timestamp())
    # an existing H4 with a DIFFERENT close than the deterministic recompute
    fake.store[hist.history_key("XAU_USD", "H4", ep)] = json.dumps(
        {"status": "OK", "data": {"open": 2000.0, "high": 2010.0, "low": 1990.0, "close": 9999.0, "volume": 400, "source_count": 4}})
    before = fake.store[hist.history_key("XAU_USD", "H4", ep)]
    r = hyd.reseal_prior_completed_h4(_producer(fake), redis_client=fake, now=_NOW)
    assert r["status"] == "failed_closed" and r["reason"] == "EXISTING_H4_MISMATCH"
    assert fake.store[hist.history_key("XAU_USD", "H4", ep)] == before   # NOT overwritten


# ============================ C. idempotency ============================
def test_idempotent_already_present_no_rewrite():
    fake = FakeRedis(); _seed_full_prior(fake); p = _producer(fake)
    r1 = hyd.reseal_prior_completed_h4(p, redis_client=fake, now=_NOW)
    assert r1["status"] == "recovered"
    hw_calls_after_first = len(p.history_forward_writer.calls)
    r2 = hyd.reseal_prior_completed_h4(p, redis_client=fake, now=_NOW)   # second boot
    assert r2["status"] == "already_present" and r2["d1_accumulation_updated"] is False
    assert len(p.history_forward_writer.calls) == hw_calls_after_first    # no second history write
    # H4 history index has the 10:00 epoch exactly once
    idx = fake.z[hist.history_index_key("XAU_USD", "H4")]
    assert list(idx).count(str(int(_PRIOR.timestamp()))) == 1


# ============================ D. boundaries ============================
def test_d1_relevant_current_vs_prev_day_and_2200_anchor():
    # prior block in the CURRENT D1 day (now 14:30 -> prior 10:00 in [22:00 Jul2, 22:00 Jul3))
    fake = FakeRedis(); _seed_full_prior(fake)
    assert hyd.reseal_prior_completed_h4(_producer(fake), redis_client=fake, now=_NOW)["d1_relevant"] is True
    # prior block in the PREVIOUS D1 day (now 22:30 Jul3 -> forming 22:00 Jul3 -> prior 18:00 Jul3, in Jul2 D1 day)
    now2 = datetime(2026, 7, 3, 22, 30, tzinfo=UTC)
    prior2 = h4d.h4_bucket_open(now2) - timedelta(seconds=h4d.H4_SECONDS)
    fake2 = FakeRedis()
    for i in range(4):
        _seed_h1(fake2, prior2 + timedelta(hours=i))
    r2 = hyd.reseal_prior_completed_h4(_producer(fake2), redis_client=fake2, now=now2)
    assert r2["status"] == "recovered" and r2["d1_relevant"] is False
    # fixed 22:00 UTC D1 anchor (no DST): both D1 opens are 22:00
    assert d1d.d1_bucket_open(_NOW).hour == 22 and d1d.d1_bucket_open(now2).hour == 22


def test_reseal_never_seals_d1_or_derives_from_h1():
    # the reseal writes an H4 only; D1 receives it via the D1 warm-start reading H4 history (no direct feed).
    fake = FakeRedis(); _seed_full_prior(fake)
    r = hyd.reseal_prior_completed_h4(_producer(fake), redis_client=fake, now=_NOW)
    assert r["d1_accumulation_updated"] == "via_d1_warmstart_from_h4_history"
    assert not fake.exists("hermes:candles:XAU_USD:D1:latest:v1") if hasattr(fake, "exists") else True
    # no D1 key written by the reseal
    assert not any(":D1:" in k for k in fake.store)


# ============================ D1-receives-once integration ============================
def test_recovered_h4_feeds_d1_via_history_exactly_once():
    import utils.candle_d1_hydration_v1 as d1hyd
    fake = FakeRedis(); _seed_full_prior(fake)
    # seed the D1 day's OTHER three H4 (22:00 Jul2, 02:00, 06:00 Jul3) into H4 history
    for h4o in (datetime(2026, 7, 2, 22, 0, tzinfo=UTC), datetime(2026, 7, 3, 2, 0, tzinfo=UTC), datetime(2026, 7, 3, 6, 0, tzinfo=UTC)):
        ep = int(h4o.timestamp())
        fake.store[hist.history_key("XAU_USD", "H4", ep)] = json.dumps({"status": "OK", "data": {
            "instrument": "XAU_USD", "timeframe": "H4", "timestamp_utc": cc._fmt(h4o), "open": 2000.0, "high": 2010.0,
            "low": 1990.0, "close": 2005.0, "volume": 400, "is_closed": True, "source_count": 4,
            "expected_source_count": 4, "source_coverage": 1.0, "gap_state": "NONE"}})
        fake.zadd(hist.history_index_key("XAU_USD", "H4"), {str(ep): ep})
    hyd.reseal_prior_completed_h4(_producer(fake), redis_client=fake, now=_NOW)   # recovers 10:00 into history
    d1_open = d1d.d1_bucket_open(_NOW)
    kids, malformed, capped = d1hyd.read_block_h4_children_from_redis(fake, instrument="XAU_USD", d1_open=d1_open)
    opens = [cc.normalise_utc(c.timestamp).strftime("%H:%M") for c in kids]
    assert opens.count("10:00") == 1                         # recovered H4 seen exactly once by the D1 warm-start
    assert set(opens) == {"22:00", "02:00", "06:00", "10:00"} and malformed == []


# ============================ skip guards ============================
def test_skipped_when_producer_disabled():
    fake = FakeRedis()
    r = hyd.reseal_prior_completed_h4(wire.DisabledH4Producer(), redis_client=fake, now=_NOW)
    assert r["status"] == "skipped" and r["reason"] == "H4_PRODUCER_DISABLED"


def test_skipped_when_no_history_writer():
    fake = FakeRedis(); _seed_full_prior(fake)
    r = hyd.reseal_prior_completed_h4(_producer(fake, history_writer=_DisabledHistoryWriter()), redis_client=fake, now=_NOW)
    assert r["status"] == "skipped" and r["reason"] == "NO_HISTORY_FORWARD_WRITER"


# ============================ warmstart integration ============================
def test_warmstart_h4_runs_reseal_and_reports(monkeypatch):
    monkeypatch.setenv(hyd.WARMSTART_ENABLED_ENV, "true")
    monkeypatch.setenv(hyd.WARMSTART_AUTHORISED_ENV, "true")
    fake = FakeRedis(); _seed_full_prior(fake)
    # seed current 14:00 block with one H1 so hydrate has something
    _seed_h1(fake, datetime(2026, 7, 3, 14, 0, tzinfo=UTC))
    rep = hyd.warmstart_h4_from_env(_producer(fake), redis_client=fake, now=_NOW)
    assert rep["prior_block_reseal"]["status"] == "recovered"
    assert rep["prior_block_reseal"]["prior_h4_block_open_utc"].startswith("2026-07-03T10:00")


# add a lightweight exists() to FakeRedis used above
def _fake_exists(self, k):
    return k in self.store
FakeRedis.exists = _fake_exists
