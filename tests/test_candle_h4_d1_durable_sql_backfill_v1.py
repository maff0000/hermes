"""HERMES H4/D1 DURABLE SQL historical backfill v1 — code-only, in-memory fake MariaDB (no live I/O).
WO-HELM-HERMES-DEV-DARWIN-DURABLE-CANONICAL-HISTORICAL-AUTHORITY-0001.

Proves: dark/dry-run by default, no writes in dry-run, gated live write, full-range (oldest-first, not a
bounded recent window) reconstruction, genuine-complete-only acceptance (incomplete windows reported, never
fabricated), the ONE governed derivation (derive_h4/derive_d1 — no second implementation), idempotent
re-run (match, never duplicate), fail-loud conflict (raises, never overwrites), D1 sourced EXCLUSIVELY from
canonical_candles_h4 (never legacy candles_D1, never a 24xH1 shortcut), restart-safety (per-row commit).
"""
from datetime import datetime, timedelta, timezone

import pytest

import utils.candle_h4_durable_sql_backfill_v1 as h4bf
import utils.candle_d1_durable_sql_backfill_v1 as d1bf
import utils.candle_durable_sql_contract_v1 as sqlc

UTC = timezone.utc
INST = "XAU_USD"


# --------------------------------------------------------------------------- shared fake DB plumbing
class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._result = None

    def execute(self, sql, params=None):
        params = params or ()
        if sql.startswith("SELECT timestamp") or sql.startswith("SELECT open_time, open"):
            assert "candles_H4" not in sql or sql.count("candles_H4") <= (1 if "canonical_candles_h4" in sql else 0) \
                or "canonical_candles_h4" in sql
            assert "candles_D1" not in sql          # NEVER the legacy midnight table, anywhere
            self._result = self.conn.source_rows
        elif sql.startswith("SELECT ") and ("canonical_candles_h4" in sql or "canonical_candles_d1" in sql):
            table = "canonical_candles_h4" if "canonical_candles_h4" in sql else "canonical_candles_d1"
            inst, tf, ot = params
            self._result = self.conn.store.get((table, inst, tf, ot))
        elif sql.startswith("INSERT"):
            table = "canonical_candles_h4" if "canonical_candles_h4" in sql else "canonical_candles_d1"
            row = dict(zip(sqlc.INSERT_COLUMNS, params))
            key = (table, row["instrument"], row["timeframe"], row["open_time"])
            if key in self.conn.store:
                raise Exception("Duplicate entry for key 'uq_identity'")
            self.conn.store[key] = row
        else:
            raise AssertionError(f"unexpected SQL: {sql[:80]}")

    def fetchall(self):
        return self._result

    def fetchone(self):
        if self._result is None:
            return None
        return tuple(self._result[c] for c in sqlc.INSERT_COLUMNS)

    def close(self):
        pass


class _FakeConn:
    def __init__(self, source_rows):
        self.source_rows = source_rows      # raw tuples as pymysql would return them
        self.store = {}
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _h1_source_rows(n_hours, start=datetime(2026, 6, 1, 0, 0), gap_hours=()):
    rows = []
    for i in range(n_hours):
        if i in gap_hours:
            continue
        ts = start + timedelta(hours=i)
        p = 2400.0 + (i % 7) * 0.5
        rows.append((ts, p, p + 1, p - 1, p + 0.5, 100))
    return rows


# --------------------------------------------------------------------------- H4 backfill
def test_h4_dry_run_no_writes_full_range_genuine_only():
    conn = _FakeConn(_h1_source_rows(4 * 20, start=datetime(2026, 5, 31, 22, 0)))   # 20 clean, anchor-aligned 4h buckets
    plan = h4bf.dry_run_plan(conn, config=h4bf.BackfillConfig(enabled=True, authorised=True, dry_run=True))
    assert plan["no_write_proof"] is True
    assert plan["accepted_count"] == 20
    assert plan["rejected_count"] == 0
    assert plan["idempotency"] == {"new": 20, "match": 0, "conflict": 0}
    assert conn.store == {}                       # absolutely no write in dry-run


def test_h4_rejects_incomplete_never_fabricates():
    conn = _FakeConn(_h1_source_rows(4 * 6, gap_hours=(5,)))    # one H1 hour missing -> 1 bucket incomplete
    plan = h4bf.dry_run_plan(conn, config=h4bf.BackfillConfig(enabled=True, authorised=True, dry_run=True))
    assert plan["rejected_count"] >= 1
    assert plan["rejection_reasons"].get("H4_INCOMPLETE_CHILDREN", 0) >= 1


def test_h4_execute_refuses_without_full_gate():
    conn = _FakeConn(_h1_source_rows(8))
    with pytest.raises(ValueError):
        h4bf.execute_backfill(conn, config=h4bf.BackfillConfig(enabled=True, authorised=True, dry_run=True))


def test_h4_execute_writes_then_idempotent_rerun_then_fail_loud_conflict():
    conn = _FakeConn(_h1_source_rows(4 * 3, start=datetime(2026, 5, 31, 22, 0)))    # 3 clean, anchor-aligned buckets
    cfg = h4bf.BackfillConfig(enabled=True, authorised=True, dry_run=False)
    res1 = h4bf.execute_backfill(conn, config=cfg)
    assert res1["written"] == 3 and res1["failed"] == 0
    assert conn.commits == 3

    res2 = h4bf.execute_backfill(conn, config=cfg)         # restart-safe re-run
    assert res2["written"] == 0
    assert res2["skipped_match"] == 3

    # Mutate one stored row to simulate a genuine conflicting existing record
    any_key = next(iter(conn.store))
    conn.store[any_key]["close"] = conn.store[any_key]["close"] + 999
    with pytest.raises(ValueError):
        h4bf.execute_backfill(conn, config=cfg)             # fail-loud, never silently overwrites


def test_h4_full_range_is_oldest_first_not_bounded_to_recent():
    conn = _FakeConn(_h1_source_rows(4 * 50))
    plan = h4bf.dry_run_plan(conn, config=h4bf.BackfillConfig(enabled=True, authorised=True, dry_run=True))
    assert plan["earliest_candidate_utc"] == "2026-06-01T02:00:00.000Z"    # first bucket, not the newest


# --------------------------------------------------------------------------- D1 backfill (sourced from canonical H4)
def _h4_source_rows_for_d1(n_days, start_d1_open=datetime(2026, 1, 6, 22, 0)):
    """Genuine 6-child H4 rows spanning n_days of complete D1 buckets (22:00Z-anchored)."""
    rows = []
    for day in range(n_days):
        base = start_d1_open + timedelta(days=day)
        for h in range(6):
            ts = base + timedelta(hours=4 * h)
            p = 2400.0 + day
            rows.append((ts, p, p + 1, p - 1, p + 0.5, 400))
    return rows


def test_d1_dry_run_sources_only_canonical_h4_never_legacy():
    conn = _FakeConn(_h4_source_rows_for_d1(5))
    plan = d1bf.dry_run_plan(conn, config=d1bf.BackfillConfig(enabled=True, authorised=True, dry_run=True))
    assert plan["source_table"] == sqlc.TABLE_H4
    assert plan["source_forbidden_legacy_d1_table"] is True
    assert plan["accepted_count"] == 5
    assert conn.store == {}


def test_d1_execute_writes_then_idempotent():
    conn = _FakeConn(_h4_source_rows_for_d1(3))
    cfg = d1bf.BackfillConfig(enabled=True, authorised=True, dry_run=False)
    res1 = d1bf.execute_backfill(conn, config=cfg)
    assert res1["written"] == 3
    res2 = d1bf.execute_backfill(conn, config=cfg)
    assert res2["written"] == 0 and res2["skipped_match"] == 3
