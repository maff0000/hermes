"""One-shot/periodic HERMES history-index pruning tool — in-memory fake Redis, no live I/O.
WO-HELM-HERMES-DEV-REDIS-CAPACITY-RETENTION-AND-PROD-INCIDENT-RECOVERY-DESIGN-0001.
"""
import fnmatch
from datetime import datetime, timezone

import pytest

import utils.candle_history_v1 as chv
import tools.hermes_history_index_prune_v1 as prune_tool

NOW = datetime(2026, 9, 5, 0, 0, tzinfo=timezone.utc)


class FakeRedis:
    def __init__(self):
        self.zsets = {}

    def scan_iter(self, match=None, count=None):
        for key in list(self.zsets):
            if match is None or fnmatch.fnmatchcase(key, match):
                yield key

    def zadd(self, name, mapping):
        self.zsets.setdefault(name, {}).update(mapping)

    def zcard(self, name):
        return len(self.zsets.get(name, {}))

    def zcount(self, name, lo, hi):
        lo = float("-inf") if lo == "-inf" else float(lo)
        hi = float("inf") if hi == "+inf" else float(hi)
        return sum(1 for s in self.zsets.get(name, {}).values() if lo <= s <= hi)

    def zremrangebyscore(self, name, lo, hi):
        lo = float("-inf") if lo == "-inf" else float(lo)
        hi = float("inf") if hi == "+inf" else float(hi)
        z = self.zsets.get(name, {})
        stale = [m for m, s in z.items() if lo <= s <= hi]
        for m in stale:
            del z[m]
        return len(stale)


@pytest.fixture(autouse=True)
def _retention(monkeypatch):
    monkeypatch.setenv("HERMES_REDIS_HISTORY_RETENTION_DAYS", "14")


def _seed(r, inst, tf, epochs):
    idx = chv.history_index_key(inst, tf)
    r.zadd(idx, {str(e): e for e in epochs})
    return idx


def test_scope_only_touches_history_index_keys():
    r = FakeRedis()
    idx = _seed(r, "XAU_USD", "M1", [1, 2])
    r.zsets["hermes:candles:XAU_USD:M1:latest:v1"] = {}     # not an index key — must never be touched
    r.zsets["some:unrelated:ares:key"] = {}
    results = prune_tool.prune_all_indexes(r, apply=False, now_utc=NOW)
    assert [x["index_key"] for x in results] == [idx]


def test_dry_run_reports_but_does_not_remove():
    r = FakeRedis()
    stale = int(NOW.timestamp()) - 20 * 86400
    fresh = int(NOW.timestamp()) - 1 * 86400
    idx = _seed(r, "XAU_USD", "M1", [stale, fresh])

    results = prune_tool.prune_all_indexes(r, apply=False, now_utc=NOW)

    assert results[0]["stale_members"] == 1
    assert results[0]["removed"] == 0                 # dry-run never mutates
    assert r.zcard(idx) == 2                          # nothing actually removed


def test_apply_removes_only_stale_members():
    r = FakeRedis()
    stale = int(NOW.timestamp()) - 20 * 86400
    fresh = int(NOW.timestamp()) - 1 * 86400
    idx = _seed(r, "XAU_USD", "M1", [stale, fresh])

    results = prune_tool.prune_all_indexes(r, apply=True, now_utc=NOW)

    assert results[0]["removed"] == 1
    assert r.zcard(idx) == 1
    assert str(fresh) in r.zsets[idx]
    assert str(stale) not in r.zsets[idx]


def test_idempotent_second_run_removes_nothing_more():
    r = FakeRedis()
    stale = int(NOW.timestamp()) - 20 * 86400
    _seed(r, "XAU_USD", "M1", [stale])

    first = prune_tool.prune_all_indexes(r, apply=True, now_utc=NOW)
    second = prune_tool.prune_all_indexes(r, apply=True, now_utc=NOW)

    assert first[0]["removed"] == 1
    assert second[0]["removed"] == 0


def test_multiple_instruments_and_timeframes_scoped_independently():
    r = FakeRedis()
    stale = int(NOW.timestamp()) - 20 * 86400
    fresh = int(NOW.timestamp()) - 1 * 86400
    _seed(r, "XAU_USD", "M1", [stale, fresh])
    _seed(r, "EUR_USD", "M5", [stale])

    results = prune_tool.prune_all_indexes(r, apply=True, now_utc=NOW)

    by_key = {x["index_key"]: x for x in results}
    assert by_key["hermes:candles:XAU_USD:M1:history:v1:index"]["removed"] == 1
    assert by_key["hermes:candles:XAU_USD:M1:history:v1:index"]["cardinality_before"] == 2
    assert by_key["hermes:candles:EUR_USD:M5:history:v1:index"]["removed"] == 1
    assert by_key["hermes:candles:EUR_USD:M5:history:v1:index"]["cardinality_before"] == 1
