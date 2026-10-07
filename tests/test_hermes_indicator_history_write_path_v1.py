"""WO-HERMES-INDICATOR-HISTORY-CONTRACT-0001 — write-path integration: `indicator_step()` now ALSO writes
the governed indicator-history key/index at the exact moment it writes the mutable `latest` key, reusing the
already-computed `_compute_indicators()` output (never a second computation). Proves: M5 historical atr_14,
H4 historical ema_50/ema_200 exist; open_epoch alignment; restart/recovery continuity; determinism across
replay; existing `latest` contracts remain byte-compatible/unaffected; no legacy SQL/`hermes:signals:*`
reference; retention wiring (time-based for M1/M5/M15/H1, count-based for H4) is exercised on the real write
path, not merely asserted in isolation.
"""
import datetime
import json

import pytest

from utils import hermes_runtime_publisher_steps_v1 as steps
from utils import hermes_indicators_v1 as ind
from utils import indicator_history_v1 as indh
from utils import candle_history_v1 as chv

UTC = datetime.timezone.utc
INST = "XAU_USD"


@pytest.fixture(autouse=True)
def _registry_adoption(monkeypatch):
    from tests.test_hermes_instrument_registry_v1 import rollout_rows
    import utils.hermes_instrument_registry_v1 as reg
    recs = reg.load_registry(rollout_rows())
    monkeypatch.setattr(reg, "load_from_db", lambda fetch=None: recs)


def _candle(i, base=2000.0):
    p = base + (i % 9) * 0.7 - (i % 4) * 0.5 + i * 0.01
    return {"open": round(p, 5), "high": round(p + 1.3, 5), "low": round(p - 1.15, 5),
            "close": round(p + 0.35, 5), "timestamp_utc": "2026-07-15T09:%02d:00.000Z" % (i % 60),
            "candle_direction": "up"}


class FakeRedis:
    """In-memory fake supporting everything both `_read_history`/`indicator_step` AND the new history
    write-path (`_write_indicator_history`) need: kv + ZSET + zcard/zremrangebyrank/zremrangebyscore."""
    def __init__(self):
        self.kv = {}
        self.z = {}
        self.writes = []

    def zadd(self, key, mapping):
        self.z.setdefault(key, {}).update(mapping)

    def zrevrange(self, key, start, end):
        items = sorted(self.z.get(key, {}).items(), key=lambda kv: kv[1], reverse=True)
        sl = items[start:(end + 1 if end != -1 else None)]
        return [m for m, _ in sl]

    def zrange(self, key, start, end):
        items = sorted(self.z.get(key, {}).items(), key=lambda kv: kv[1])
        sl = items[start:(end + 1 if end != -1 else None)]
        return [m for m, _ in sl]

    def zcard(self, key):
        return len(self.z.get(key, {}))

    def zremrangebyrank(self, key, start, stop):
        members = sorted(self.z.get(key, {}).items(), key=lambda kv: kv[1])
        removed = members[start:stop + 1] if stop != -1 else members[start:]
        for m, _ in removed:
            self.z[key].pop(m, None)
        return len(removed)

    def zremrangebyscore(self, key, lo, hi):
        lo_v = float("-inf") if lo == "-inf" else float(lo)
        hi_v = float("inf") if hi == "+inf" else float(hi)
        members = [(m, s) for m, s in self.z.get(key, {}).items() if lo_v <= s <= hi_v]
        for m, _ in members:
            self.z[key].pop(m, None)
        return len(members)

    def get(self, key):
        return self.kv.get(key)

    def set(self, key, val, ex=None):
        self.kv[key] = val
        self.writes.append(key)

    def ttl(self, key):
        return -1 if key in self.kv else -2


def _seed_history(r, tf, n):
    idx = f"hermes:candles:{INST}:{tf}:history:v1:index"
    base = 1_700_000_000
    for i in range(n):
        ep = base + i * 3600
        r.zadd(idx, {str(ep): ep})
        r.kv[f"hermes:candles:{INST}:{tf}:history:v1:{ep}"] = json.dumps({"data": _candle(i)})
    r.kv[f"hermes:candles:{INST}:{tf}:latest:v1"] = json.dumps({"status": "OK"})


def _enable_env(monkeypatch, timeframes="M1,M5,M15,H1,H4"):
    monkeypatch.setenv(ind.ENABLED_ENV, "true")
    monkeypatch.setenv(ind.AUTHORISED_ENV, "true")
    monkeypatch.setenv(ind.INSTRUMENTS_ENV, INST)
    monkeypatch.setenv(ind.TIMEFRAMES_ENV, timeframes)
    monkeypatch.delenv(ind.D1_AUTHORISED_ENV, raising=False)


def _hist_payloads(r):
    return {k: json.loads(v) for k, v in r.kv.items() if ":indicators:" in k and ":history:" in k}


def _latest_payloads(r):
    return {k: json.loads(v) for k, v in r.kv.items() if ":indicators:" in k and ":history:" not in k}


# --------------------------------------------------------------------------- required coverage (WO §13.2/.3/.4)
def test_m5_historical_atr14_exists_for_representative_window(monkeypatch):
    _enable_env(monkeypatch, timeframes="M5")
    r = FakeRedis()
    _seed_history(r, "M5", steps.EMA_TREND_WINDOW)
    res = steps.indicator_step(r)
    assert res["published"] == 1
    hist = _hist_payloads(r)
    recs = [p for k, p in hist.items() if f":{INST}:M5:history:v1:" in k]
    assert len(recs) == 1
    assert recs[0]["indicators"]["atr_14"] is not None


def test_h4_historical_ema50_and_ema200_exist_for_representative_window(monkeypatch):
    _enable_env(monkeypatch, timeframes="H4")
    r = FakeRedis()
    _seed_history(r, "H4", steps.EMA_TREND_WINDOW)   # 210 — enough for ema_200
    res = steps.indicator_step(r)
    assert res["published"] == 1
    hist = _hist_payloads(r)
    recs = [p for k, p in hist.items() if f":{INST}:H4:history:v1:" in k]
    assert len(recs) == 1
    assert recs[0]["indicators"]["ema_50"] is not None
    assert recs[0]["indicators"]["ema_200"] is not None


# --------------------------------------------------------------------------- same governed calc as latest
def test_historical_record_matches_latest_same_bar_same_governed_calculation(monkeypatch):
    _enable_env(monkeypatch, timeframes="H1")
    r = FakeRedis()
    _seed_history(r, "H1", steps.EMA_TREND_WINDOW)
    steps.indicator_step(r)
    latest = list(_latest_payloads(r).values())[0]
    hist = list(_hist_payloads(r).values())[0]
    assert latest["indicators"] == hist["indicators"]
    assert latest["methods"] == hist["methods"]


# --------------------------------------------------------------------------- open_epoch alignment
def test_open_epoch_alignment_to_correct_closed_bar(monkeypatch):
    _enable_env(monkeypatch, timeframes="H1")
    r = FakeRedis()
    _seed_history(r, "H1", steps.EMA_TREND_WINDOW)
    candles = steps._read_history(r, "H1", steps.EMA_TREND_WINDOW)
    expected_open = candles[-1]["timestamp_utc"]
    expected_epoch = int(datetime.datetime.strptime(expected_open[:-1], "%Y-%m-%dT%H:%M:%S.%f")
                         .replace(tzinfo=UTC).timestamp())
    steps.indicator_step(r)
    key = f"hermes:indicators:{INST}:H1:history:v1:{expected_epoch}"
    assert key in r.kv
    rec = json.loads(r.kv[key])
    assert rec["history"]["source_timestamp_utc"] == expected_open
    assert rec["value_open_time_utc"] == expected_open


# --------------------------------------------------------------------------- existing latest byte-compatible
def test_existing_latest_contract_unaffected_by_history_write(monkeypatch):
    _enable_env(monkeypatch, timeframes="M1,M5,M15,H1,H4")
    r = FakeRedis()
    for tf in ("M1", "M5", "M15", "H1"):
        _seed_history(r, tf, steps.EMA_TREND_WINDOW)
    _seed_history(r, "H4", 69)
    res = steps.indicator_step(r)
    assert res["published"] == 5
    for tf in ("M1", "M5", "M15", "H1", "H4"):
        key = f"hermes:indicators:{INST}:{tf}:v1"
        assert key in r.kv
        p = json.loads(r.kv[key])
        ind.validate_indicator_contract(p)   # exact same contract shape as before this WO
        assert "history" not in p            # latest key itself never carries a history block


# --------------------------------------------------------------------------- determinism / restart-recovery
def test_history_write_deterministic_across_two_observations(monkeypatch):
    _enable_env(monkeypatch, timeframes="H1")
    r = FakeRedis()
    _seed_history(r, "H1", steps.EMA_TREND_WINDOW)
    steps.indicator_step(r)
    hist1 = dict(_hist_payloads(r))
    steps.indicator_step(r)   # second observation, identical source history -> identical computed values
    hist2 = dict(_hist_payloads(r))
    k = list(hist1.keys())[0]
    assert hist1[k]["indicators"] == hist2[k]["indicators"]


def test_restart_recovery_reads_continue_working_off_whatever_is_present(monkeypatch):
    _enable_env(monkeypatch, timeframes="H4")
    r = FakeRedis()
    _seed_history(r, "H4", steps.EMA_TREND_WINDOW)
    steps.indicator_step(r)   # "process 1" writes history
    # "restart": fresh call against the SAME injected client, no in-process state carried
    res2 = steps.indicator_step(r)
    assert res2["published"] == 1
    hist = _hist_payloads(r)
    assert len(hist) == 1   # idempotent re-write of the SAME bar — no duplicate/conflicting record


# --------------------------------------------------------------------------- retention wiring on the real write path
def test_h4_history_index_count_based_retention_applied_on_write_path(monkeypatch):
    _enable_env(monkeypatch, timeframes="H4")
    r = FakeRedis()
    idx = indh.history_index_key(INST, "H4")
    base = 1_600_000_000
    # pre-seed the indicator-history index right up to the H4_HISTORY_RETAIN_COUNT cap with synthetic members
    for i in range(chv.H4_HISTORY_RETAIN_COUNT):
        ep = base + i * 14400
        r.zadd(idx, {str(ep): ep})
        r.kv[indh.history_key(INST, "H4", ep)] = json.dumps({"stub": True})
    _seed_history(r, "H4", steps.EMA_TREND_WINDOW)
    steps.indicator_step(r)
    assert r.zcard(idx) <= chv.H4_HISTORY_RETAIN_COUNT


def test_m5_history_index_time_based_retention_cutoff_applied_on_write_path(monkeypatch):
    monkeypatch.setenv("HERMES_REDIS_HISTORY_RETENTION_DAYS", "14")
    _enable_env(monkeypatch, timeframes="M5")
    r = FakeRedis()
    idx = indh.history_index_key(INST, "M5")
    # one member far in the past (outside the 14-day retention window relative to now)
    ancient_epoch = int((datetime.datetime.now(UTC) - datetime.timedelta(days=30)).timestamp())
    r.zadd(idx, {str(ancient_epoch): ancient_epoch})
    r.kv[indh.history_key(INST, "M5", ancient_epoch)] = json.dumps({"stub": True})
    _seed_history(r, "M5", steps.EMA_TREND_WINDOW)
    steps.indicator_step(r)
    assert str(ancient_epoch) not in r.z.get(idx, {})


# --------------------------------------------------------------------------- conflict guard
def test_conflicting_existing_history_record_fails_loud(monkeypatch):
    _enable_env(monkeypatch, timeframes="H1")
    r = FakeRedis()
    _seed_history(r, "H1", steps.EMA_TREND_WINDOW)
    candles = steps._read_history(r, "H1", steps.EMA_TREND_WINDOW)
    open_epoch = int(datetime.datetime.strptime(candles[-1]["timestamp_utc"][:-1], "%Y-%m-%dT%H:%M:%S.%f")
                     .replace(tzinfo=UTC).timestamp())
    key = indh.history_key(INST, "H1", open_epoch)
    # plant a conflicting record (different governed indicator values) at the exact key this run will target
    conflict_env = indh.build_history_envelope(
        instrument=INST, timeframe="H1", generated_at_utc=datetime.datetime(2020, 1, 1, tzinfo=UTC),
        value_open_time_utc=datetime.datetime.fromtimestamp(open_epoch, tz=UTC),
        indicators={"ema_12": 99999.0}, freshness_state="FRESH", publish_run_id="SOMETHING_ELSE",
        published_at_utc=datetime.datetime(2020, 1, 1, tzinfo=UTC),
        source_candle_history_key="bogus", source_timestamp_utc=datetime.datetime(2020, 1, 1, tzinfo=UTC))
    r.kv[key] = json.dumps(conflict_env)
    with pytest.raises(ValueError, match="GOV-HERMES-IND-HIST-020"):
        steps.indicator_step(r)


# --------------------------------------------------------------------------- no legacy SQL / hermes:signals:*
def test_no_legacy_sql_indicator_or_signals_reference_introduced():
    import inspect
    raw_module = inspect.getsource(indh)
    raw_steps = inspect.getsource(steps)
    for blob in (raw_module, raw_steps):
        assert "hermes:signals:" not in blob
        for tok in ("candles_H4", "candles_D1", "SELECT * FROM signals", "signals_table"):
            assert tok not in blob
