"""HERMES durable SQL candle contract v1 — pure row-shape/fingerprint/idempotency tests.
WO-HELM-HERMES-DEV-DARWIN-DURABLE-CANONICAL-HISTORICAL-AUTHORITY-0001.

Proves: row mapping from a governed derived envelope, stable fingerprinting that survives the
pymysql Decimal-vs-float round-trip (the exact false-conflict bug caught and fixed while building this
WO — see the regression test below), new/match/conflict classification, never-overwrite (`upsert_new_only`
issues no UPDATE statement), and a fail-closed table allowlist.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest

import utils.candle_durable_sql_contract_v1 as sqlc
import utils.candle_h4_derivation_v1 as h4d
import utils.candle_d1_derivation_v1 as d1d

UTC = timezone.utc
INST = "XAU_USD"


def _h1_row(ts, price):
    return {"timestamp": ts, "open": price, "high": price + 1, "low": price - 1, "close": price + 0.5,
            "volume": 100}


def _genuine_h4_env():
    h4_open = datetime(2026, 6, 1, 22, 0, tzinfo=UTC)
    kids = [_h1_row(h4_open + timedelta(hours=i), 2400.0 + i) for i in range(4)]
    env, _meta = h4d.derive_h4(instrument=INST, h4_open=h4_open, h1_children=kids,
                               generated_at_utc=h4_open + timedelta(hours=4), is_closed=True)
    return env


class _FakeCursor:
    def __init__(self, store):
        self.store = store          # dict[(table,instrument,timeframe,open_time)] -> row dict
        self.last_result = None

    def execute(self, sql, params=None):
        params = params or ()
        if sql.startswith("SELECT"):
            table = _table_from_select(sql)
            inst, tf, ot = params
            self.last_result = self.store.get((table, inst, tf, ot))
        elif sql.startswith("INSERT"):
            table = _table_from_insert(sql)
            row = dict(zip(sqlc.INSERT_COLUMNS, params))
            key = (table, row["instrument"], row["timeframe"], row["open_time"])
            if key in self.store:
                raise Exception(f"Duplicate entry for key 'uq_identity' ({table})")
            self.store[key] = row
        else:
            raise AssertionError(f"unexpected SQL in fake cursor: {sql[:60]}")

    def fetchone(self):
        if self.last_result is None:
            return None
        return tuple(self.last_result[c] for c in sqlc.INSERT_COLUMNS)

    def close(self):
        pass


def _table_from_select(sql):
    for t in sqlc.KNOWN_TABLES:
        if f"FROM {t} " in sql:
            return t
    raise AssertionError("unknown table in SELECT")


def _table_from_insert(sql):
    for t in sqlc.KNOWN_TABLES:
        if f"INSERT INTO {t} " in sql:
            return t
    raise AssertionError("unknown table in INSERT")


class _FakeConn:
    def __init__(self):
        self.store = {}
        self.committed = 0
        self.rolled_back = 0

    def cursor(self):
        return _FakeCursor(self.store)

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1


def test_assert_known_table_fail_closed():
    sqlc.assert_known_table(sqlc.TABLE_H4)
    sqlc.assert_known_table(sqlc.TABLE_D1)
    with pytest.raises(ValueError):
        sqlc.assert_known_table("candles_H4")     # the LEGACY table must never be an accepted target
    with pytest.raises(ValueError):
        sqlc.assert_known_table("some_other_table")


def test_row_from_envelope_maps_fields_and_rejects_non_xau():
    env = _genuine_h4_env()
    row = sqlc.row_from_envelope(env, derivation_run_id="TEST_RUN")
    assert row["instrument"] == "XAU_USD"
    assert row["timeframe"] == "H4"
    assert row["open_time"] == datetime(2026, 6, 1, 22, 0)
    assert row["status"] == "OK"
    assert row["source_count"] == 4
    assert row["expected_source_count"] == 4
    assert row["source_coverage"] == 1.0
    assert row["gap_state"] in (None, "NONE")
    assert row["derivation_run_id"] == "TEST_RUN"

    bad = {"data": {**env["data"], "instrument": "EUR_USD"}, "generated_at_utc": env["generated_at_utc"],
          "status": env["status"]}
    with pytest.raises(ValueError):
        sqlc.row_from_envelope(bad, derivation_run_id="X")


def test_fingerprint_survives_decimal_vs_float_roundtrip():
    """REGRESSION for the exact bug caught live on HERMES DEV: pymysql returns DECIMAL columns as
    decimal.Decimal while a freshly-derived candidate carries Python float. Before the fix, a byte-identical
    price fingerprinted differently and every idempotent re-run was misclassified as a false 'conflict'."""
    env = _genuine_h4_env()
    row = sqlc.row_from_envelope(env, derivation_run_id="RUN_A")
    decimal_row = dict(row)
    decimal_row["open"] = Decimal(str(row["open"]))
    decimal_row["high"] = Decimal(str(row["high"]))
    decimal_row["low"] = Decimal(str(row["low"]))
    decimal_row["close"] = Decimal(str(row["close"]))
    decimal_row["source_coverage"] = Decimal(str(row["source_coverage"]))
    decimal_row["derivation_run_id"] = "RUN_B"    # provenance differs — must NOT affect the fingerprint

    assert sqlc.fingerprint_row(row) == sqlc.fingerprint_row(decimal_row)
    assert sqlc.classify_against_existing(decimal_row, row) == "match"


def test_classify_new_match_conflict():
    env = _genuine_h4_env()
    row = sqlc.row_from_envelope(env, derivation_run_id="R1")
    assert sqlc.classify_against_existing(None, row) == "new"
    assert sqlc.classify_against_existing(row, row) == "match"
    conflicting = dict(row)
    conflicting["close"] = row["close"] + 5.0
    assert sqlc.classify_against_existing(conflicting, row) == "conflict"


def test_upsert_new_only_never_issues_an_update_and_never_overwrites():
    conn = _FakeConn()
    cur = conn.cursor()
    env = _genuine_h4_env()
    row = sqlc.row_from_envelope(env, derivation_run_id="R1")

    status1 = sqlc.upsert_new_only(cur, sqlc.TABLE_H4, row)
    assert status1 == "new"
    assert len(conn.store) == 1

    status2 = sqlc.upsert_new_only(cur, sqlc.TABLE_H4, row)     # idempotent re-run of the SAME row
    assert status2 == "match"
    assert len(conn.store) == 1                                  # never duplicated

    conflicting = dict(row)
    conflicting["close"] = row["close"] + 5.0
    status3 = sqlc.upsert_new_only(cur, sqlc.TABLE_H4, conflicting)
    assert status3 == "conflict"
    assert len(conn.store) == 1                                  # never overwritten
    stored = next(iter(conn.store.values()))
    assert stored["close"] == row["close"]                        # original value untouched

    assert "UPDATE" not in sqlc.insert_sql(sqlc.TABLE_H4).upper()
    assert "UPDATE" not in sqlc.select_existing_sql(sqlc.TABLE_H4).upper()


# ---------------- Architect review correction: fingerprint MUST include governed semantic policy ----------------
def test_fingerprint_conflicts_on_different_derivation_policy_same_ohlcv():
    env = _genuine_h4_env()
    row = sqlc.row_from_envelope(env, derivation_run_id="R1")
    same_ohlcv_different_policy = dict(row)
    same_ohlcv_different_policy["derivation_policy"] = "SOME_OTHER_POLICY_V2"
    assert sqlc.classify_against_existing(same_ohlcv_different_policy, row) == "conflict"


def test_fingerprint_conflicts_on_different_source_policy_epoch_same_ohlcv():
    env = _genuine_h4_env()
    row = sqlc.row_from_envelope(env, derivation_run_id="R1")
    same_ohlcv_different_epoch = dict(row)
    same_ohlcv_different_epoch["source_policy_epoch"] = "H4_FROM_H1_NY1700_V2"
    assert sqlc.classify_against_existing(same_ohlcv_different_epoch, row) == "conflict"


def test_fingerprint_still_matches_on_run_id_and_generated_at_difference_only():
    env = _genuine_h4_env()
    row = sqlc.row_from_envelope(env, derivation_run_id="RUN_A")
    other_run = dict(row)
    other_run["derivation_run_id"] = "RUN_B_DIFFERENT_PROCESS"
    other_run["derivation_generated_at_utc"] = row["derivation_generated_at_utc"] + timedelta(days=90)
    assert sqlc.classify_against_existing(other_run, row) == "match"


# ---------------- Architect review correction: D1 durable must never get ahead of durable H4 truth ----------------
class _H4SourceCursor:
    """Minimal fake cursor for d1_durable_h4_source_complete: only answers the COUNT(*) ... IN (...) query
    this function issues, against a caller-supplied set of (instrument, open_time) rows already 'present'."""
    def __init__(self, present_open_times):
        self.present = set(present_open_times)   # naive-UTC datetimes considered present+OK
        self._result = None

    def execute(self, sql, params):
        assert sql.strip().startswith("SELECT COUNT(*)")
        assert "canonical_candles_h4" in sql
        assert "status='OK'" in sql
        instrument, *open_times = params
        assert instrument == INST
        self._result = (sum(1 for t in open_times if t in self.present),)

    def fetchone(self):
        return self._result


def test_d1_durable_h4_source_complete_true_only_when_all_six_present():
    d1_open = datetime(2026, 6, 25, 22, 0, tzinfo=UTC)
    expected = [t.replace(tzinfo=None) for t in d1d.d1_child_h4_opens(d1_open)]
    assert len(expected) == 6

    all_present = _H4SourceCursor(expected)
    assert sqlc.d1_durable_h4_source_complete(all_present, INST, d1_open) is True

    five_present = _H4SourceCursor(expected[:5])
    assert sqlc.d1_durable_h4_source_complete(five_present, INST, d1_open) is False

    none_present = _H4SourceCursor([])
    assert sqlc.d1_durable_h4_source_complete(none_present, INST, d1_open) is False


def test_d1_durable_h4_source_complete_accepts_naive_or_aware_d1_open():
    d1_open_aware = datetime(2026, 6, 25, 22, 0, tzinfo=UTC)
    d1_open_naive = datetime(2026, 6, 25, 22, 0)
    expected = [t.replace(tzinfo=None) for t in d1d.d1_child_h4_opens(d1_open_aware)]
    cur = _H4SourceCursor(expected)
    assert sqlc.d1_durable_h4_source_complete(cur, INST, d1_open_naive) is True
