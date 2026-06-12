"""Tests for the durable M30/H4 candle pipeline.
WO-HELM-HERMES-CANDLE-H4-M30-PIPELINE-BUILD-0001.

Pure-logic tests — no live DB. Proves M30/H4 are registered, UTC-aligned bucketing is
correct, OHLCV aggregation is deterministic, partial windows are not silently completed,
and the upsert contract is idempotent + never downgrades a complete candle.
"""
import os
import sys
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from utils.m1_deriver import TIMEFRAME_SECONDS, get_bucket_start  # noqa: E402
import backfill_candles_h4_m30 as bf  # noqa: E402


# ---------- registration ----------
def test_m30_h4_registered():
    assert TIMEFRAME_SECONDS["M30"] == 1800
    assert TIMEFRAME_SECONDS["H4"] == 14400


def test_expected_m1_counts():
    assert bf.expected_m1("M30") == 30
    assert bf.expected_m1("H4") == 240


# ---------- UTC-aligned bucketing ----------
def test_m30_bucket_alignment():
    # 13:37 -> 13:30 ; 13:29 -> 13:00
    assert get_bucket_start(datetime(2026, 6, 12, 13, 37, 5), 1800) == datetime(2026, 6, 12, 13, 30)
    assert get_bucket_start(datetime(2026, 6, 12, 13, 29, 59), 1800) == datetime(2026, 6, 12, 13, 0)


def test_h4_bucket_alignment_utc_00_anchor():
    # UTC-aligned 4h buckets anchored at 00:00: 00,04,08,12,16,20
    cases = {2: 0, 5: 4, 11: 8, 13: 12, 17: 16, 23: 20}
    for hour, expect in cases.items():
        b = get_bucket_start(datetime(2026, 6, 12, hour, 30), 14400)
        assert b == datetime(2026, 6, 12, expect, 0), f"{hour}->{b}"


# ---------- deterministic OHLCV aggregation ----------
def _m1(o, h, l, c, v):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def test_aggregate_ohlcv_correct():
    rows = [_m1(10, 12, 9, 11, 5), _m1(11, 15, 10, 14, 7), _m1(14, 14, 8, 9, 3)]
    o, h, l, c, vol = bf.aggregate_ohlcv(rows)
    assert o == 10        # first open
    assert h == 15        # max high
    assert l == 8         # min low
    assert c == 9         # last close
    assert vol == 15      # sum volume


def test_aggregate_empty_fail_loud():
    try:
        bf.aggregate_ohlcv([])
        assert False, "expected GOV-CANDLE-001"
    except ValueError as e:
        assert "GOV-CANDLE-001" in str(e)


def test_is_complete_only_when_full():
    assert bf.is_complete(30, "M30") is True
    assert bf.is_complete(29, "M30") is False
    assert bf.is_complete(240, "H4") is True
    assert bf.is_complete(239, "H4") is False


def test_last_closed_bucket_excludes_current():
    now = datetime(2026, 6, 12, 13, 37)
    # current H4 bucket is 12:00-16:00; backfill end must be 12:00 (current forming bucket excluded)
    assert bf.last_closed_bucket_end(now, "H4") == datetime(2026, 6, 12, 12, 0)
    assert bf.last_closed_bucket_end(now, "M30") == datetime(2026, 6, 12, 13, 30)


# ---------- idempotent / no-downgrade upsert contract ----------
def test_upsert_is_idempotent_and_never_downgrades_complete():
    sql = bf.UPSERT
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "complete=GREATEST(complete, VALUES(complete))" in sql  # never downgrade complete=1
    assert "IF(VALUES(complete)=1, VALUES(open), open)" in sql      # partial never clobbers OHLC


def test_target_tables_are_hermes_owned_candle_tables():
    assert bf.TARGET_TABLE == {"M30": "candles_M30", "H4": "candles_H4"}
    assert bf.SOURCE == "m1_derive_backfill"


# ---------- dry-run never mutates (fake engine/conn) ----------
class _Candle:
    def __init__(self, ts, complete):
        from types import SimpleNamespace
        self.timestamp = ts
        self.complete = complete
        for k, v in dict(instrument="XAU_USD", open=1.0, high=2.0, low=0.5, close=1.5, volume=10).items():
            setattr(self, k, v)


class _Engine:
    def derive_range(self, inst, tf, start, end):
        return [_Candle(datetime(2026, 6, 10, 20, 0), True), _Candle(datetime(2026, 6, 10, 20, 30), False)]


class _Log:
    def info(self, *a, **k):
        pass


def test_dry_run_does_not_write():
    out = bf.backfill_timeframe(_Engine(), None, _Log(), "XAU_USD", "M30",
                                datetime(2026, 6, 10), datetime(2026, 6, 12),
                                execute=False, confirm=False)
    assert out["mode"] == "dry-run"
    assert out["written"] == 0
    assert out["complete"] == 1 and out["partial"] == 1   # partial surfaced, not hidden


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn(); passed += 1; print(f"PASS {fn.__name__}")
        except Exception:
            print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"\n{passed}/{len(fns)} passed")
    raise SystemExit(0 if passed == len(fns) else 1)
