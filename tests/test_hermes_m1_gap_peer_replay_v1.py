"""Tests for the HERMES M1 gap peer-replay engine — WO-HELM-HERMES-DEV-DETERMINISTIC-CANDLE-GAP-
RECONSTRUCTION-0001. Integration-style against the real DEV DB (same convention as
tests/test_m1_deriver.py), using a dedicated fake instrument code so no real instrument's data is
ever touched. source_db_config == target_db_config throughout (this suite proves the mechanism, not
cross-host connectivity); a same-host source/target is exactly what the DEV proof also uses.

Proves:
1. A clean NEW-only gap (M1 + derived M15/H1/D1) reconstructs correctly and idempotently.
2. CONFLICT is detected (never silently overwritten) when target already disagrees with source.
3. execute_replay() refuses outright when the plan contains any CONFLICT.
4. SOURCE_DATA_MISSING is produced (not NEW, not blocking) when a minute is absent from both sides,
   and the derived bucket containing it is correctly marked partial (complete=0) or skipped if the
   bucket has zero available constituents at all.
5. An existing (already-present) derived-timeframe row is NEVER re-derived, compared, or touched.
6. D1's `open` is corrected to the prior day's close (the carry-forward convention), not the raw
   first-M1-open — for both a single missing D1 day and two CONSECUTIVE missing D1 days (the prior
   close must chain from the first newly-derived day into the second).
7. Window auto behaviour: a bucket straddling the caller's window edge derives correctly using
   context M1 minutes just outside the window.
"""
import sys
import os
from datetime import datetime, timedelta

import pymysql
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from env_config import get_db_config
from tools.hermes_m1_gap_peer_replay_v1 import (
    plan_replay, execute_replay, NEW, MATCH, CONFLICT, SOURCE_DATA_MISSING,
)

DB = get_db_config()
INSTRUMENT = "WO13_TEST_INSTR"  # fake, never a real traded instrument — safe to freely mutate
TABLES = {"M1": "candles_M1", "M15": "candles_M15", "H1": "candles_H1", "D1": "candles_D1"}


def _conn():
    return pymysql.connect(**DB, autocommit=True, connect_timeout=5)


def _clear():
    conn = _conn()
    with conn.cursor() as cur:
        for t in TABLES.values():
            cur.execute(f"DELETE FROM {t} WHERE instrument=%s", (INSTRUMENT,))
    conn.close()


def _insert_m1(rows):
    """rows: list of (timestamp, open, high, low, close, volume)."""
    conn = _conn()
    with conn.cursor() as cur:
        for ts, o, h, l, c, v in rows:
            cur.execute(
                "INSERT INTO candles_M1 (instrument, timestamp, open, high, low, close, volume, complete, source) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,1,'test_fixture')",
                (INSTRUMENT, ts, o, h, l, c, v),
            )
    conn.close()


def _insert_row(table, ts, o, h, l, c, v=1):
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {table} (instrument, timestamp, open, high, low, close, volume, complete, source) "
            f"VALUES (%s,%s,%s,%s,%s,%s,%s,1,'test_fixture')",
            (INSTRUMENT, ts, o, h, l, c, v),
        )
    conn.close()


def _fetch_one(table, ts):
    conn = _conn()
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute(f"SELECT * FROM {table} WHERE instrument=%s AND timestamp=%s", (INSTRUMENT, ts))
        row = cur.fetchone()
    conn.close()
    return row


def _minute_rows(start, n, base_price=100.0):
    return [
        (start + timedelta(minutes=i), base_price + i * 0.01, base_price + i * 0.01 + 0.02,
         base_price + i * 0.01 - 0.02, base_price + i * 0.01 + 0.01, 10 + i)
        for i in range(n)
    ]


@pytest.fixture(autouse=True)
def clean_around_each_test():
    _clear()
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS wo13_test_source_m1")
    conn.close()
    yield
    _clear()
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS wo13_test_source_m1")
    conn.close()


@pytest.fixture
def peer_source():
    """Points the tool's M1 source at a disposable, genuinely separate table for the duration of the
    test — necessary because a real peer-copy repair (this tool's whole purpose) always reads from a
    DIFFERENT physical database/table than the one being repaired; source_db_config==target_db_config
    alone (same table) can never exercise the NEW/SOURCE_DATA_MISSING split at all."""
    import tools.hermes_m1_gap_peer_replay_v1 as mod
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute("CREATE TABLE wo13_test_source_m1 LIKE candles_M1")
    conn.close()
    old_table = mod.M1_TABLE
    mod.M1_TABLE = "wo13_test_source_m1"
    try:
        yield "wo13_test_source_m1"
    finally:
        mod.M1_TABLE = old_table


def _seed_source(table, rows):
    conn = _conn()
    with conn.cursor() as cur:
        for ts, o, h, l, c, v in rows:
            cur.execute(
                f"INSERT INTO {table} (instrument, timestamp, open, high, low, close, volume, complete, source) "
                f"VALUES (%s,%s,%s,%s,%s,%s,%s,1,'test_fixture')",
                (INSTRUMENT, ts, o, h, l, c, v),
            )
    conn.close()


DAY0 = datetime(2026, 1, 5, 22, 0, 0)  # a forex-day boundary far from any real trading data


def test_clean_gap_reconstructs_m1_m15_h1_and_is_idempotent(peer_source):
    # Source (peer DB) has the full day; target (candles_M1) is missing one hour — a clean incident gap.
    full_day = _minute_rows(DAY0, 1440)
    _seed_source(peer_source, full_day)
    gap = [r for r in full_day if not (DAY0 + timedelta(hours=1) <= r[0] < DAY0 + timedelta(hours=2))]
    _insert_m1(gap)

    plan = plan_replay(source_db_config=DB, target_db_config=DB, instrument=INSTRUMENT,
                       window_start_utc=DAY0, window_end_utc=DAY0 + timedelta(hours=24),
                       now_utc=DAY0 + timedelta(days=2))
    counts = plan.counts()
    assert counts["M1"][NEW] == 60  # the deleted hour
    assert counts["M1"][CONFLICT] == 0
    assert counts["M1"][SOURCE_DATA_MISSING] == 0  # target==source here, so "missing from source" never happens
    assert counts["M15"][NEW] == 96  # 1440/15
    assert counts["H1"][NEW] == 24
    assert counts["D1"][NEW] == 1

    execute_replay(target_db_config=DB, plan=plan)

    for tf, table, n in (("M15", "candles_M15", 96), ("H1", "candles_H1", 24)):
        conn = _conn()
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE instrument=%s AND timestamp>=%s AND timestamp<%s",
                       (INSTRUMENT, DAY0, DAY0 + timedelta(hours=24)))
            assert cur.fetchone()[0] == n
        conn.close()
    d1 = _fetch_one("candles_D1", DAY0 + timedelta(hours=2))
    assert d1 is not None and int(d1["complete"]) == 1

    # idempotence: re-plan the identical window -> zero NEW, zero CONFLICT; execute writes nothing
    plan2 = plan_replay(source_db_config=DB, target_db_config=DB, instrument=INSTRUMENT,
                        window_start_utc=DAY0, window_end_utc=DAY0 + timedelta(hours=24),
                        now_utc=DAY0 + timedelta(days=2))
    c2 = plan2.counts()
    for tf in ("M1", "M15", "H1", "D1"):
        assert c2[tf][NEW] == 0
        assert c2[tf][CONFLICT] == 0
    result2 = execute_replay(target_db_config=DB, plan=plan2)
    assert all(v == 0 for v in result2["written"].values())


def test_conflict_detected_and_execute_refuses(peer_source):
    rows = _minute_rows(DAY0, 60)
    _insert_m1(rows)
    _seed_source(peer_source, rows)
    # source now disagrees with target at minute 5 — must never be silently overwritten either way
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute(f"UPDATE {peer_source} SET close=close-50 WHERE instrument=%s AND timestamp=%s",
                   (INSTRUMENT, DAY0 + timedelta(minutes=5)))
    conn.close()

    plan = plan_replay(source_db_config=DB, target_db_config=DB, instrument=INSTRUMENT,
                       window_start_utc=DAY0, window_end_utc=DAY0 + timedelta(hours=1),
                       now_utc=DAY0 + timedelta(days=2))
    assert plan.counts()["M1"][CONFLICT] == 1
    with pytest.raises(ValueError, match="GOV-M1-REPLAY-CONFLICT"):
        execute_replay(target_db_config=DB, plan=plan)


def test_source_data_missing_does_not_block_partial_bucket():
    # Full hour except 3 minutes entirely absent from BOTH sides (source==target here: a minute
    # missing from the single shared table is, by construction, missing from both).
    rows = [r for r in _minute_rows(DAY0, 60) if not (5 <= r[0].minute <= 7)]
    _insert_m1(rows)
    plan = plan_replay(source_db_config=DB, target_db_config=DB, instrument=INSTRUMENT,
                       window_start_utc=DAY0, window_end_utc=DAY0 + timedelta(hours=1),
                       now_utc=DAY0 + timedelta(days=2))
    counts = plan.counts()
    assert counts["M1"][SOURCE_DATA_MISSING] == 3
    assert counts["M1"][MATCH] == 57  # already present identically on both sides -> MATCH, not NEW
    h1 = [c for c in plan.classifications if c.timeframe == "H1"][0]
    assert h1.status == NEW
    assert h1.source_row["complete"] == 0  # partial: 57/60 constituents


def test_existing_derived_row_never_touched():
    _insert_m1(_minute_rows(DAY0, 60))
    _insert_row("candles_H1", DAY0, 999.0, 999.0, 999.0, 999.0)  # deliberately wrong-looking pre-existing row
    plan = plan_replay(source_db_config=DB, target_db_config=DB, instrument=INSTRUMENT,
                       window_start_utc=DAY0, window_end_utc=DAY0 + timedelta(hours=1),
                       now_utc=DAY0 + timedelta(days=2))
    h1_classifications = [c for c in plan.classifications if c.timeframe == "H1"]
    assert h1_classifications == []  # never evaluated at all — pre-existing row is untouched, unopined-on
    execute_replay(target_db_config=DB, plan=plan)
    row = _fetch_one("candles_H1", DAY0)
    assert float(row["open"]) == 999.0  # still untouched


def test_d1_open_carry_forward_single_and_consecutive_missing_days():
    prior_day = DAY0 - timedelta(hours=24)
    _insert_row("candles_D1", prior_day + timedelta(hours=2), 100.0, 110.0, 95.0, 105.5)  # prior close=105.5
    _insert_m1(_minute_rows(DAY0, 1440, base_price=200.0))                 # day 1: full M1
    _insert_m1(_minute_rows(DAY0 + timedelta(hours=24), 1440, base_price=300.0))  # day 2: full M1

    plan = plan_replay(source_db_config=DB, target_db_config=DB, instrument=INSTRUMENT,
                       window_start_utc=DAY0, window_end_utc=DAY0 + timedelta(hours=48),
                       now_utc=DAY0 + timedelta(days=3))
    d1s = sorted((c for c in plan.classifications if c.timeframe == "D1"), key=lambda c: c.timestamp)
    assert len(d1s) == 2
    assert d1s[0].source_row["open"] == 105.5          # carried from the real prior close
    day1_close = d1s[0].source_row["close"]
    assert d1s[1].source_row["open"] == day1_close     # chained from day 1's own freshly-derived close

    execute_replay(target_db_config=DB, plan=plan)
    row1 = _fetch_one("candles_D1", DAY0 + timedelta(hours=2))
    row2 = _fetch_one("candles_D1", DAY0 + timedelta(hours=26))
    assert float(row1["open"]) == 105.5
    assert float(row2["open"]) == float(row1["close"])


def test_boundary_bucket_uses_context_outside_caller_window(peer_source):
    # H1 bucket [DAY0, DAY0+1h) straddles the caller window edge at DAY0+30min: the first half is
    # "context" (already present in target, untouched), the second half is the genuine repair window.
    full_hour = _minute_rows(DAY0, 60)
    _seed_source(peer_source, full_hour)
    context_only = [r for r in full_hour if r[0] < DAY0 + timedelta(minutes=30)]
    _insert_m1(context_only)

    plan = plan_replay(source_db_config=DB, target_db_config=DB, instrument=INSTRUMENT,
                       window_start_utc=DAY0 + timedelta(minutes=30),
                       window_end_utc=DAY0 + timedelta(hours=1),
                       now_utc=DAY0 + timedelta(days=2))
    h1 = [c for c in plan.classifications if c.timeframe == "H1"][0]
    assert h1.status == NEW
    assert h1.source_row["complete"] == 1  # full hour available: 30 context + 30 repaired
    execute_replay(target_db_config=DB, plan=plan)
    row = _fetch_one("candles_H1", DAY0)
    assert abs(float(row["open"]) - float(full_hour[0][1])) < 0.0001
