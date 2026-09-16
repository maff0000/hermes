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


# ---------------- Architect review correction (round 2): D1 durable must be PROVABLY DERIVED from durable
# H4 truth, not merely co-located with 6 present rows ----------------
class _H4SourceRowsCursor:
    """Fake cursor for d1_durable_source_lineage_status: answers the SELECT open_time,open,high,low,close,
    volume ... IN (...) query against a caller-supplied {naive_utc_open_time: (open,high,low,close,volume)}
    map of durable H4 rows considered present+OK. `present_policies` optionally overrides one or more
    entries' (derivation_policy, source_policy_epoch) away from the governed pair the query passes — this
    simulates the real WHERE derivation_policy=%s AND source_policy_epoch=%s clause correctly EXCLUDING a
    durable row that exists under an incompatible/legacy policy (Architect review correction round 2,
    requirement E)."""
    def __init__(self, present_rows, present_policies=None):
        self.present = dict(present_rows)
        self.policies = present_policies or {}
        self._result = None

    def execute(self, sql, params):
        assert sql.strip().startswith("SELECT open_time, open, high, low, close, volume")
        assert "canonical_candles_h4" in sql
        assert "status='OK'" in sql
        assert "derivation_policy=%s" in sql and "source_policy_epoch=%s" in sql
        instrument, policy, epoch, *open_times = params
        assert instrument == INST
        rows = []
        for t in sorted(t for t in open_times if t in self.present):
            row_policy, row_epoch = self.policies.get(t, (policy, epoch))   # default: correctly-policied
            if (row_policy, row_epoch) != (policy, epoch):
                continue   # excluded, exactly like the real SQL WHERE clause would exclude it
            o, h, lo, c, v = self.present[t]
            rows.append((t, o, h, lo, c, v))
        self._result = rows

    def fetchall(self):
        return self._result


def _six_h4_children_and_candidate_row(d1_open):
    """Build 6 genuine H4 children for `d1_open`, derive the D1 candidate row a live seal would offer
    (via the SAME derive_d1() the guard itself uses), and the {open_time: ohlcv} durable-presence map that,
    fed back through the guard unchanged, must reconstruct that EXACT same D1."""
    aware_opens = d1d.d1_child_h4_opens(d1_open)
    naive_opens = [t.replace(tzinfo=None) for t in aware_opens]
    specs = [(2000.0, 2010.0, 1990.0, 2005.0, 100), (2005.0, 2030.0, 1995.0, 2020.0, 110),
             (2020.0, 2080.0, 2010.0, 2050.0, 120), (2050.0, 2060.0, 2000.0, 2030.0, 130),
             (2030.0, 2040.0, 1900.0, 1950.0, 140), (1950.0, 1975.0, 1940.0, 1970.0, 150)]
    h4_children = [{"timestamp": aware_opens[i], "open": specs[i][0], "high": specs[i][1], "low": specs[i][2],
                    "close": specs[i][3], "volume": specs[i][4]} for i in range(6)]
    env, _meta = d1d.derive_d1(instrument=INST, d1_open=d1_open, h4_children=h4_children,
                               generated_at_utc=d1_open + timedelta(hours=24), is_closed=True)
    candidate_row = sqlc.row_from_envelope(env, derivation_run_id="LIVE_CANDIDATE")
    present_rows = {naive_opens[i]: specs[i] for i in range(6)}
    return naive_opens, present_rows, candidate_row


def test_d1_source_lineage_verified_when_durable_h4_exactly_matches_candidate():
    d1_open = datetime(2026, 6, 25, 22, 0, tzinfo=UTC)
    _opens, present_rows, candidate_row = _six_h4_children_and_candidate_row(d1_open)
    cur = _H4SourceRowsCursor(present_rows)
    assert sqlc.d1_durable_source_lineage_status(cur, INST, d1_open, candidate_row) == sqlc._D1_SOURCE_VERIFIED


def test_d1_source_lineage_incomplete_when_fewer_than_six_present():
    d1_open = datetime(2026, 6, 25, 22, 0, tzinfo=UTC)
    opens, present_rows, candidate_row = _six_h4_children_and_candidate_row(d1_open)
    del present_rows[opens[-1]]
    cur = _H4SourceRowsCursor(present_rows)
    assert sqlc.d1_durable_source_lineage_status(cur, INST, d1_open, candidate_row) == sqlc._D1_SOURCE_INCOMPLETE

    cur_none = _H4SourceRowsCursor({})
    assert sqlc.d1_durable_source_lineage_status(cur_none, INST, d1_open, candidate_row) == sqlc._D1_SOURCE_INCOMPLETE


def test_d1_source_lineage_conflict_when_one_durable_h4_disagrees_with_candidate():
    """THE critical case the round-2 Architect review flagged: 6/6 durable H4 rows present at the expected
    identities, but ONE durably-stored child DIFFERS from the H4 fact the live candidate D1 was actually
    built from -> the old presence-only guard would have allowed this through; this one MUST refuse."""
    d1_open = datetime(2026, 6, 25, 22, 0, tzinfo=UTC)
    opens, present_rows, candidate_row = _six_h4_children_and_candidate_row(d1_open)
    tampered = dict(present_rows)
    o, h, lo, c, v = tampered[opens[-1]]
    # a small, internally-consistent tamper (still within this child's own high/low bounds) — enough to
    # change the fingerprint without producing an invalid OHLC that the contract validator itself rejects
    tampered[opens[-1]] = (o, h, lo, c + 2.0, v)   # durable H4 differs from what candidate was built from
    cur = _H4SourceRowsCursor(tampered)
    assert sqlc.d1_durable_source_lineage_status(cur, INST, d1_open, candidate_row) == sqlc._D1_SOURCE_CONFLICT


def test_d1_source_lineage_accepts_naive_or_aware_d1_open():
    d1_open_aware = datetime(2026, 6, 25, 22, 0, tzinfo=UTC)
    d1_open_naive = datetime(2026, 6, 25, 22, 0)
    _opens, present_rows, candidate_row = _six_h4_children_and_candidate_row(d1_open_aware)
    cur = _H4SourceRowsCursor(present_rows)
    assert sqlc.d1_durable_source_lineage_status(cur, INST, d1_open_naive, candidate_row) \
        == sqlc._D1_SOURCE_VERIFIED


def test_d1_source_lineage_incomplete_when_one_durable_h4_has_incompatible_policy():
    """Requirement E: a durable H4 row present at the right identity with the right OHLCV but under an
    INCOMPATIBLE governed policy/epoch must never establish false lineage — it is correctly excluded by the
    same governed-policy filter that requirement C's completeness check already relies on, so it degrades
    to the ordinary 'source_incomplete' case (5/6 valid), never a silent VERIFIED."""
    d1_open = datetime(2026, 6, 25, 22, 0, tzinfo=UTC)
    opens, present_rows, candidate_row = _six_h4_children_and_candidate_row(d1_open)
    cur = _H4SourceRowsCursor(present_rows, present_policies={opens[-1]: ("LEGACY_POLICY_V0", "OLD_EPOCH")})
    assert sqlc.d1_durable_source_lineage_status(cur, INST, d1_open, candidate_row) == sqlc._D1_SOURCE_INCOMPLETE
