"""Tests for the M5 manifest-driven peer-replay path — WO-HELM-HERMES-DEV-M5-GAP-REPAIR-AND-SIGNAL-
COLLAPSE-RCA-0001. Same integration-style convention as tests/test_hermes_m1_gap_peer_replay_v1.py
(real DEV DB, a dedicated fake instrument code, disposable source/target tables), but exercising
plan_m5_replay()/execute_m5_replay() specifically, including the property that motivated their
manifest-only design: rows OUTSIDE the declared missing-timestamp manifest must never be inspected,
compared, or allowed to block the plan — even when source and target disagree there.

Proves:
1. A clean NEW-only manifest reconstructs correctly and idempotently (second run: zero new writes).
2. CONFLICT is detected (never silently overwritten) when target already has an unexplained,
   disagreeing row at a manifest timestamp.
3. execute_m5_replay() refuses outright when the plan contains any CONFLICT.
4. SOURCE_DATA_MISSING is produced (not NEW, not blocking) when a manifest timestamp is absent from
   both sides.
5. Manifest-only scope: a timestamp NOT in missing_timestamps is never classified, compared, or able
   to block the plan — even when source and target genuinely disagree there (the Swiss-cheese/
   independent-stream scenario this design exists to handle safely).
"""
import sys
import os
from datetime import datetime, timedelta

import pymysql
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from env_config import get_db_config
from tools.hermes_m1_gap_peer_replay_v1 import (
    plan_m5_replay, execute_m5_replay, NEW, MATCH, CONFLICT, SOURCE_DATA_MISSING,
)

DB = get_db_config()
INSTRUMENT = "WO_M5_TEST_INSTR"  # fake, never a real traded instrument — safe to freely mutate
M5_TARGET_TABLE = "wo_m5_test_target_m5"
M5_SOURCE_TABLE = "wo_m5_test_source_m5"


def _conn():
    return pymysql.connect(**DB, autocommit=True, connect_timeout=5)


def _reset_tables():
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {M5_TARGET_TABLE}")
        cur.execute(f"DROP TABLE IF EXISTS {M5_SOURCE_TABLE}")
        cur.execute(f"CREATE TABLE {M5_TARGET_TABLE} LIKE candles_M5")
        cur.execute(f"CREATE TABLE {M5_SOURCE_TABLE} LIKE candles_M5")
    conn.close()


def _drop_tables():
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {M5_TARGET_TABLE}")
        cur.execute(f"DROP TABLE IF EXISTS {M5_SOURCE_TABLE}")
    conn.close()


def _insert(table, ts, o, h, l, c, v=100):
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


@pytest.fixture(autouse=True)
def clean_around_each_test():
    _reset_tables()
    yield
    _drop_tables()


T0 = datetime(2030, 1, 7, 12, 0, 0)  # far-future, never collides with any real market data


def _plan(missing):
    return plan_m5_replay(
        source_db_config=DB, target_db_config=DB, instrument=INSTRUMENT,
        missing_timestamps=missing, target_table=M5_TARGET_TABLE,
    )


def _mod():
    import tools.hermes_m1_gap_peer_replay_v1 as mod
    return mod


@pytest.fixture(autouse=True)
def point_m5_table_at_disposable_source():
    mod = _mod()
    old = mod.M5_TABLE
    mod.M5_TABLE = M5_SOURCE_TABLE
    yield
    mod.M5_TABLE = old


def test_new_only_manifest_reconstructs_and_is_idempotent():
    missing = [T0, T0 + timedelta(minutes=5), T0 + timedelta(minutes=10)]
    for i, ts in enumerate(missing):
        _insert(M5_SOURCE_TABLE, ts, 1.1000 + i * 0.0001, 1.1010 + i * 0.0001,
                1.0990 + i * 0.0001, 1.1005 + i * 0.0001, 50 + i)

    plan1 = _plan(missing)
    assert plan1.counts() == {NEW: 3, MATCH: 0, CONFLICT: 0, SOURCE_DATA_MISSING: 0}
    assert not plan1.has_conflicts()

    result = execute_m5_replay(target_db_config=DB, plan=plan1, target_table=M5_TARGET_TABLE)
    assert result["written"] == 3
    for ts in missing:
        row = _fetch_one(M5_TARGET_TABLE, ts)
        assert row is not None
        assert row["source"] == "M1_GAP_REPLAY_V1"

    # second run: now all three MATCH, zero new writes, fingerprint changes (status set differs)
    plan2 = _plan(missing)
    assert plan2.counts() == {NEW: 0, MATCH: 3, CONFLICT: 0, SOURCE_DATA_MISSING: 0}
    result2 = execute_m5_replay(target_db_config=DB, plan=plan2, target_table=M5_TARGET_TABLE)
    assert result2["written"] == 0
    assert plan1.fingerprint() != plan2.fingerprint()


def test_conflict_detected_and_never_overwritten():
    ts = T0
    _insert(M5_SOURCE_TABLE, ts, 1.2000, 1.2010, 1.1990, 1.2005, 100)
    _insert(M5_TARGET_TABLE, ts, 1.9000, 1.9010, 1.8990, 1.9005, 999)  # unexplained, disagreeing

    plan = _plan([ts])
    assert plan.counts()[CONFLICT] == 1
    assert plan.has_conflicts()

    with pytest.raises(ValueError, match="GOV-M5-REPLAY-CONFLICT"):
        execute_m5_replay(target_db_config=DB, plan=plan, target_table=M5_TARGET_TABLE)

    # target row must be completely untouched
    row = _fetch_one(M5_TARGET_TABLE, ts)
    assert float(row["close"]) == 1.9005
    assert row["volume"] == 999


def test_source_data_missing_is_not_new_and_does_not_block():
    present = T0
    missing_everywhere = T0 + timedelta(minutes=5)
    _insert(M5_SOURCE_TABLE, present, 1.3000, 1.3010, 1.2990, 1.3005, 100)

    plan = _plan([present, missing_everywhere])
    counts = plan.counts()
    assert counts[NEW] == 1
    assert counts[SOURCE_DATA_MISSING] == 1
    assert not plan.has_conflicts()

    result = execute_m5_replay(target_db_config=DB, plan=plan, target_table=M5_TARGET_TABLE)
    assert result["written"] == 1
    assert _fetch_one(M5_TARGET_TABLE, present) is not None
    assert _fetch_one(M5_TARGET_TABLE, missing_everywhere) is None


def test_rows_outside_manifest_are_never_inspected_or_allowed_to_block():
    """The whole point of manifest-driven planning: a timestamp genuinely present (and disagreeing)
    on both source and target, but NOT declared missing, must never be classified, must never appear
    in the plan, and must never block execution of the real manifest's NEW rows — this is exactly the
    Swiss-cheese/independent-stream scenario plan_replay()'s full-window scan cannot safely handle."""
    manifest_ts = T0
    untouched_ts = T0 + timedelta(minutes=5)

    _insert(M5_SOURCE_TABLE, manifest_ts, 1.4000, 1.4010, 1.3990, 1.4005, 100)
    # untouched_ts: present on both sides with genuinely different values (independent-stream noise)
    _insert(M5_SOURCE_TABLE, untouched_ts, 1.5000, 1.5010, 1.4990, 1.5005, 100)
    _insert(M5_TARGET_TABLE, untouched_ts, 1.5001, 1.5011, 1.4991, 1.5006, 97)

    plan = _plan([manifest_ts])  # untouched_ts deliberately NOT in the manifest

    assert len(plan.classifications) == 1
    assert plan.classifications[0].timestamp == manifest_ts
    assert not plan.has_conflicts()

    result = execute_m5_replay(target_db_config=DB, plan=plan, target_table=M5_TARGET_TABLE)
    assert result["written"] == 1

    # untouched_ts's target row must be byte-identical to what it was before — never compared, never written
    row = _fetch_one(M5_TARGET_TABLE, untouched_ts)
    assert float(row["close"]) == 1.5006
    assert row["volume"] == 97
