"""WO-HELM-HERMES-H4-CANONICAL-HISTORICAL-BOOTSTRAP-0001 — H4 count-based retention.

Proves H4 gets its own COUNT-based retention (mirroring D1's precedent) while M1/M5/M15/H1's generic
TIME-based retention is completely unchanged — the generic policy is never touched by this WO.
"""
import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

import utils.candle_history_v1 as chv
import utils.candle_history_forward_writer_v1 as fwd
import utils.candle_h4_derivation_v1 as h4d
import utils.candle_contract_v1 as cc

UTC = timezone.utc
INST = "XAU_USD"


# --------------------------------------------------------------------------- pure trim-plan / TTL selection
def test_h4_retention_constant_matches_ema200_plus_margin():
    assert chv.H4_HISTORY_RETAIN_COUNT >= 210          # EMA_TREND_WINDOW in hermes_runtime_publisher_steps_v1


def test_h4_history_retention_trim_plan_shape():
    plan = chv.h4_history_retention_trim_plan(300, keep=250)
    assert plan == {"operation": "ZREMRANGEBYRANK", "index_stub": "0..49", "would_trim": 50, "keep_newest": 250}
    plan2 = chv.h4_history_retention_trim_plan(100, keep=250)
    assert plan2["would_trim"] == 0 and plan2["index_stub"] == "none"


def test_history_ttl_seconds_for_h4_uses_dedicated_bound(monkeypatch):
    monkeypatch.setenv(chv.RETENTION_DAYS_ENV, "14")
    assert chv.history_ttl_seconds_for("H4") == chv.H4_HISTORY_TTL_SECONDS
    assert chv.history_ttl_seconds_for("H4") != chv.history_ttl_seconds()


def test_history_ttl_seconds_for_other_timeframes_unchanged(monkeypatch):
    monkeypatch.setenv(chv.RETENTION_DAYS_ENV, "14")
    for tf in ("M1", "M5", "M15", "H1"):
        assert chv.history_ttl_seconds_for(tf) == chv.history_ttl_seconds()


def test_build_history_write_plan_h4_gets_dedicated_ttl(monkeypatch):
    monkeypatch.setenv(chv.RETENTION_DAYS_ENV, "14")
    env = chv.build_history_envelope(instrument=INST, timeframe="H4", timestamp_utc=datetime(2026, 6, 2, 2, tzinfo=UTC),
                                     ohlc={"open": 1, "high": 2, "low": 0, "close": 1.5, "volume": 1},
                                     backfill_run_id="x", backfill_inserted_at_utc=datetime(2026, 6, 2, 6, tzinfo=UTC),
                                     source_table="t", source_timestamp_utc=datetime(2026, 6, 2, 2, tzinfo=UTC))
    plan = chv.build_history_write_plan(env)
    assert plan["ttl_seconds"] == chv.H4_HISTORY_TTL_SECONDS


def test_build_history_write_plan_h1_ttl_unchanged(monkeypatch):
    monkeypatch.setenv(chv.RETENTION_DAYS_ENV, "14")
    env = chv.build_history_envelope(instrument=INST, timeframe="H1", timestamp_utc=datetime(2026, 6, 2, 2, tzinfo=UTC),
                                     ohlc={"open": 1, "high": 2, "low": 0, "close": 1.5, "volume": 1},
                                     backfill_run_id="x", backfill_inserted_at_utc=datetime(2026, 6, 2, 3, tzinfo=UTC),
                                     source_table="t", source_timestamp_utc=datetime(2026, 6, 2, 2, tzinfo=UTC))
    plan = chv.build_history_write_plan(env)
    assert plan["ttl_seconds"] == 14 * 86400


# --------------------------------------------------------------------------- forward writer index pruning
class _FakeRedis:
    def __init__(self):
        self.kv = {}
        self.z = {}
        self.sets = []

    def get(self, k):
        v = self.kv.get(k)
        return v.encode() if isinstance(v, str) else v

    def set(self, k, v, ex=None):
        self.kv[k] = v
        self.sets.append((k, ex))

    def zadd(self, k, mapping):
        self.z.setdefault(k, {}).update({m: float(s) for m, s in mapping.items()})

    def zcard(self, k):
        return len(self.z.get(k, {}))

    def zremrangebyscore(self, k, mn, mx):
        before = len(self.z.get(k, {}))
        self.z[k] = {m: s for m, s in self.z.get(k, {}).items() if not (s <= mx)}
        return before - len(self.z[k])

    def zremrangebyrank(self, k, start, stop):
        members = sorted(self.z.get(k, {}).items(), key=lambda kv: kv[1])
        removed = members[start:stop + 1] if stop != -1 else members[start:]
        for m, _ in removed:
            self.z[k].pop(m, None)
        return len(removed)


def _h4_env(open_dt):
    return h4d.derive_h4(instrument=INST, h4_open=open_dt,
                         h1_children=[{"timestamp": open_dt + timedelta(hours=i), "open": 1, "high": 2,
                                      "low": 0, "close": 1.5, "volume": 1} for i in range(4)],
                         generated_at_utc=open_dt + timedelta(hours=4), is_closed=True)[0]


def test_forward_writer_prunes_h4_by_count_not_time(monkeypatch):
    monkeypatch.setenv(chv.RETENTION_DAYS_ENV, "1")     # a tiny time-based window that WOULD wipe H4 if used
    r = _FakeRedis()
    writer = fwd.CandleHistoryForwardWriter(redis_client=r, allowed_instruments=(INST,), timeframes=("H4",))
    base = datetime(2026, 1, 1, 2, tzinfo=UTC)          # deliberately ~150+ days before `now` below
    for i in range(260):
        env = _h4_env(base + timedelta(hours=4 * i))
        writer.on_h4_sealed(env, inserted_at_utc=datetime(2026, 9, 1, tzinfo=UTC))
    idx = chv.history_index_key(INST, "H4")
    # count-based retention kept ~H4_HISTORY_RETAIN_COUNT despite every candle being WAY outside a 1-day cutoff
    assert r.zcard(idx) <= chv.H4_HISTORY_RETAIN_COUNT
    assert r.zcard(idx) > 50                             # proves it did NOT fall back to the 1-day time cutoff


def test_forward_writer_prunes_h1_by_time_unchanged(monkeypatch):
    monkeypatch.setenv(chv.RETENTION_DAYS_ENV, "1")
    r = _FakeRedis()
    writer = fwd.CandleHistoryForwardWriter(redis_client=r, allowed_instruments=(INST,), timeframes=("H1",))
    now = datetime(2026, 9, 1, tzinfo=UTC)
    old = chv.build_history_envelope(instrument=INST, timeframe="H1", timestamp_utc=now - timedelta(days=10),
                                     ohlc={"open": 1, "high": 2, "low": 0, "close": 1.5, "volume": 1},
                                     backfill_run_id="x", backfill_inserted_at_utc=now - timedelta(days=10),
                                     source_table="t", source_timestamp_utc=now - timedelta(days=10))
    fresh = chv.build_history_envelope(instrument=INST, timeframe="H1", timestamp_utc=now - timedelta(hours=1),
                                       ohlc={"open": 1, "high": 2, "low": 0, "close": 1.5, "volume": 1},
                                       backfill_run_id="x", backfill_inserted_at_utc=now,
                                       source_table="t", source_timestamp_utc=now - timedelta(hours=1))
    writer.on_canonical_close(old, inserted_at_utc=now)
    writer.on_canonical_close(fresh, inserted_at_utc=now)
    idx = chv.history_index_key(INST, "H1")
    assert r.zcard(idx) == 1                             # the 10-day-old H1 candle WAS pruned by the 1-day cutoff
