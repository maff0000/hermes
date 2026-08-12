"""Bounded MariaDB->Redis candle-history warm-start.
WO-HELM-HERMES-DEV-MULTI-INSTRUMENT-CANDLE-HISTORY-WARMSTART-0001."""
import json
from datetime import datetime, timezone, timedelta

import pytest

from utils import candle_history_warmstart_v1 as ws


class _Cur:
    def __init__(self, rows): self._rows = rows; self.q = None; self.p = None
    def execute(self, q, p=None): self.q = q; self.p = p
    def fetchall(self): return self._rows
    def close(self): pass


class _Conn:
    def __init__(self, rows): self._rows = rows
    def cursor(self): return _Cur(self._rows)


class _Pipe:
    def __init__(self, sink): self.sink = sink
    def set(self, k, v, ex=None): self.sink.append(("set", k, v, ex))
    def zadd(self, k, m): self.sink.append(("zadd", k, m))
    def execute(self): self.sink.append(("execute",))


class _Redis:
    def __init__(self): self.ops = []
    def pipeline(self, transaction=False): return _Pipe(self.ops)


def _rows(inst, tf, n, base=datetime(2026, 8, 12, 0, 0, tzinfo=timezone.utc)):
    step = ws.cc.TF_SECONDS[tf]
    out = []
    for i in range(n):
        t = base + timedelta(seconds=i * step)
        out.append((t.replace(tzinfo=None), 100.0 + i, 110.0 + i, 95.0 + i, 104.0 + i, 7 + i))
    return out


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv(ws.ENABLED_ENV, raising=False)
    w = ws.build_warmstart_from_env()
    assert w.enabled is False
    assert w.run(redis_client=None, db_conn=None)["seeded"] == {}


def test_enabled_unauthorised_fails_loud(monkeypatch):
    monkeypatch.setenv(ws.ENABLED_ENV, "true")
    monkeypatch.delenv(ws.AUTHORISED_ENV, raising=False)
    with pytest.raises(ValueError) as e:
        ws.build_warmstart_from_env()
    assert "GOV-CANDLE-HIST-WS-003" in str(e.value)


def test_hours_bounds():
    with pytest.raises(ValueError):
        ws.CandleHistoryWarmstart(instruments=("XAU_USD",), hours=0)
    with pytest.raises(ValueError):
        ws.CandleHistoryWarmstart(instruments=("XAU_USD",), hours=ws.MAX_HOURS + 1)
    with pytest.raises(ValueError):
        ws.CandleHistoryWarmstart(instruments=(), hours=24)


def test_seed_writes_canonical_keys_with_geometry():
    w = ws.CandleHistoryWarmstart(instruments=("XAG_USD",), hours=24, timeframes=("M5",))
    r = _Redis(); conn = _Conn(_rows("XAG_USD", "M5", 3))
    summ = w.run(redis_client=r, db_conn=conn, now=datetime(2026, 8, 12, 1, 0, tzinfo=timezone.utc))
    assert summ["seeded"]["XAG_USD"]["M5"] == 3 and summ["errors"] == []
    sets = [o for o in r.ops if o[0] == "set"]
    assert len(sets) == 3
    # canonical key + geometry survives SQL->Redis
    k, v = sets[0][1], json.loads(sets[0][2])
    assert k.startswith("hermes:candles:XAG_USD:M5:history:v1:")
    d = v["data"]
    for f in ("wick_high", "wick_low", "body_high", "body_low", "body_size", "range_size", "candle_direction"):
        assert f in d and d[f] is not None
    assert v["history"]["source_table"] == "candles_M5"


def test_only_base_timeframes_and_h4_d1_excluded():
    w = ws.CandleHistoryWarmstart(instruments=("XAU_USD",), hours=24, timeframes=("M1", "M5", "H4", "D1"))
    assert w.timeframes == ("M1", "M5")   # H4/D1 filtered out (governed grid)


def test_per_instrument_isolation():
    # a bad instrument (raises in SQL) does not stop the good one
    class _BoomConn:
        def __init__(self): self.calls = 0
        def cursor(self):
            self.calls += 1
            if self.calls == 1:
                class _C:
                    def execute(self, *a, **k): raise RuntimeError("db boom")
                    def close(self): pass
                return _C()
            return _Cur(_rows("XAU_USD", "M1", 2))
    w = ws.CandleHistoryWarmstart(instruments=("AAA_BAD", "XAU_USD"), hours=24, timeframes=("M1",))
    summ = w.run(redis_client=_Redis(), db_conn=_BoomConn())
    assert len(summ["errors"]) == 1 and summ["errors"][0]["instrument"] == "AAA_BAD"
    assert summ["seeded"]["XAU_USD"]["M1"] == 2
