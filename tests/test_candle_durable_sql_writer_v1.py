"""HERMES durable SQL LIVE persistence hook v1 — fault-isolation, idempotency, gating.
WO-HELM-HERMES-DEV-DARWIN-DURABLE-CANONICAL-HISTORICAL-AUTHORITY-0001.

Proves: dark by default, enabled-without-authorised fails loud at BOOT (never mid-flight), on_sealed
NEVER raises (DB-down / write-fault / conflict are all fault-isolated and reported via metrics), only
status==OK envelopes are ever persisted, idempotent re-seal is recognised as a no-op 'match' (never a
duplicate write, never breaks anything), and a genuine conflict is counted + returned visibly rather than
silently lost or allowed to halt the caller.
"""
from datetime import datetime, timedelta, timezone

import pytest

import utils.candle_durable_sql_writer_v1 as dsw
import utils.candle_durable_sql_contract_v1 as sqlc
import utils.candle_h4_derivation_v1 as h4d

UTC = timezone.utc
INST = "XAU_USD"


def _genuine_h4_env():
    h4_open = datetime(2026, 6, 1, 22, 0, tzinfo=UTC)
    kids = [{"timestamp": h4_open + timedelta(hours=i), "open": 2400.0 + i, "high": 2401.0 + i,
             "low": 2399.0 + i, "close": 2400.5 + i, "volume": 100} for i in range(4)]
    env, _meta = h4d.derive_h4(instrument=INST, h4_open=h4_open, h1_children=kids,
                               generated_at_utc=h4_open + timedelta(hours=4), is_closed=True)
    return env


class _FakeCursor:
    def __init__(self, store):
        self.store = store
        self._result = None

    def execute(self, sql, params=None):
        params = params or ()
        if sql.startswith("SELECT"):
            inst, tf, ot = params
            self._result = self.store.get((inst, tf, ot))
        elif sql.startswith("INSERT"):
            row = dict(zip(sqlc.INSERT_COLUMNS, params))
            key = (row["instrument"], row["timeframe"], row["open_time"])
            self.store[key] = row
        else:
            raise AssertionError(sql[:60])

    def fetchone(self):
        if self._result is None:
            return None
        return tuple(self._result[c] for c in sqlc.INSERT_COLUMNS)

    def close(self):
        pass


class _FakeConn:
    def __init__(self, store):
        self.store = store
        self.committed = False
        self.closed = False

    def cursor(self):
        return _FakeCursor(self.store)

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        self.closed = True


class _BrokenConnFactory:
    def __call__(self):
        raise ConnectionError("DB unreachable")


def test_disabled_writer_is_a_true_noop():
    w = dsw.DisabledDurableSqlWriter()
    assert w.enabled is False
    res = w.on_sealed(_genuine_h4_env())
    assert res == {"attempted": False, "reason": "DURABLE_SQL_PERSIST_DISABLED"}


def test_on_sealed_skips_non_ok_never_attempts_write():
    store = {}
    w = dsw.DurableSqlWriter(table=sqlc.TABLE_H4, run_id_marker="TEST", conn_factory=lambda: _FakeConn(store))
    res = w.on_sealed({"status": "SOURCE_INCOMPLETE", "data": {}})
    assert res["attempted"] is False
    assert store == {}


def test_on_sealed_inserts_new_then_idempotent_match_never_duplicates():
    store = {}
    w = dsw.DurableSqlWriter(table=sqlc.TABLE_H4, run_id_marker="LIVE", conn_factory=lambda: _FakeConn(store))
    env = _genuine_h4_env()

    res1 = w.on_sealed(env)
    assert res1 == {"attempted": True, "wrote": True, "status": "inserted", "table": sqlc.TABLE_H4}
    assert len(store) == 1
    assert w.metrics["written"] == 1

    res2 = w.on_sealed(env)     # restart / duplicate seal callback
    assert res2["wrote"] is False and res2["status"] == "match"
    assert len(store) == 1      # never duplicated
    assert w.metrics["match_skip"] == 1


def test_on_sealed_conflict_is_visible_never_raised_never_overwrites():
    store = {}
    w = dsw.DurableSqlWriter(table=sqlc.TABLE_H4, run_id_marker="LIVE", conn_factory=lambda: _FakeConn(store))
    env = _genuine_h4_env()
    w.on_sealed(env)
    key = next(iter(store))
    original_close = store[key]["close"]
    store[key]["close"] = original_close + 999      # simulate a differing existing row

    res = w.on_sealed(env)      # must NOT raise
    assert res["wrote"] is False and res["status"] == "conflict"
    assert w.metrics["conflict_detected"] == 1
    assert store[key]["close"] == original_close + 999     # never overwritten either direction


def test_on_sealed_db_connect_fail_is_fault_isolated_never_raises():
    w = dsw.DurableSqlWriter(table=sqlc.TABLE_H4, run_id_marker="LIVE", conn_factory=_BrokenConnFactory())
    res = w.on_sealed(_genuine_h4_env())        # must NOT raise despite the broken factory
    assert res["wrote"] is False and res["reason"] == "DB_CONNECT_FAIL"
    assert w.metrics["connect_fail"] == 1


def test_build_h4_writer_enabled_without_authorised_fails_loud(monkeypatch):
    monkeypatch.setenv("HERMES_CANDLE_H4_DURABLE_SQL_PERSIST_ENABLED", "true")
    monkeypatch.delenv("HERMES_CANDLE_H4_DURABLE_SQL_PERSIST_AUTHORISED", raising=False)
    with pytest.raises(ValueError):
        dsw.build_h4_durable_sql_writer_from_env()


def test_build_h4_writer_disabled_by_default(monkeypatch):
    monkeypatch.delenv("HERMES_CANDLE_H4_DURABLE_SQL_PERSIST_ENABLED", raising=False)
    w = dsw.build_h4_durable_sql_writer_from_env()
    assert isinstance(w, dsw.DisabledDurableSqlWriter)


def test_build_d1_writer_enabled_without_authorised_fails_loud(monkeypatch):
    monkeypatch.setenv("HERMES_CANDLE_D1_DURABLE_SQL_PERSIST_ENABLED", "true")
    monkeypatch.delenv("HERMES_CANDLE_D1_DURABLE_SQL_PERSIST_AUTHORISED", raising=False)
    with pytest.raises(ValueError):
        dsw.build_d1_durable_sql_writer_from_env()
