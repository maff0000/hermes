"""HERMES governed durable SQL LIVE persistence hook v1 — closes the historical/live seam for DARWIN.
WO-HELM-HERMES-DEV-DARWIN-DURABLE-CANONICAL-HISTORICAL-AUTHORITY-0001.

There must be no operational boundary such as "backfill ends here / live data lives somewhere else"
(Architect mandatory clarification). This module is the LIVE half of that guarantee: it is offered every
freshly-SEALED, status-OK H4/D1 candle by the EXISTING governed live derivation/seal path
(`CanonicalH4Producer._seal_and_publish`, `CanonicalD1Producer._seal_and_publish`) and persists it into the
SAME durable canonical table the historical backfill writes (`canonical_candles_h4` / `canonical_candles_d1`,
migration 027) via the SAME shared row-shape/idempotency contract (`candle_durable_sql_contract_v1`) — one
row shape, one idempotency rule, whether the row arrived via backfill or via live seal.

DARK BY DEFAULT (own gate, ON TOP OF the existing H4/D1 publish gates): deploying this code does not
auto-activate durable persistence. Gated ENABLED-without-AUTHORISED fails loud at boot (mirrors the D1
publish-wire precedent). FAULT-ISOLATED at the call site: a durable-SQL fault (DB down, transient network)
is counted + returned and NEVER raised into the caller — it must never break the H4/D1 latest publish
(already succeeded) or the Redis history-forward write (independent, already governed). A genuine CONFLICT
(existing row's content differs from the freshly-sealed candle — e.g. the same bucket got restated) is
likewise never raised here (the live path cannot halt trading infrastructure over it) but is counted +
returned loudly as `status: "conflict"` so it can never be silently lost; a human-run repair WO resolves it,
exactly like the deliberate fail-loud conflict rule in the backfill scripts (which DO raise, because a
one-shot batch script can safely stop and be re-run).

Restart/duplicate safety: idempotent INSERT-if-new via `candle_durable_sql_contract_v1.upsert_new_only` —
a restart that re-seals or a duplicate seal callback is recognised as `match` (no-op), never double-inserted
(the table's own UNIQUE KEY (instrument,timeframe,open_time) is a second line of defence).
"""
from __future__ import annotations

from utils import candle_durable_sql_contract_v1 as sqlc

H4_PERSIST_ENABLED_ENV = "HERMES_CANDLE_H4_DURABLE_SQL_PERSIST_ENABLED"
H4_PERSIST_AUTHORISED_ENV = "HERMES_CANDLE_H4_DURABLE_SQL_PERSIST_AUTHORISED"
D1_PERSIST_ENABLED_ENV = "HERMES_CANDLE_D1_DURABLE_SQL_PERSIST_ENABLED"
D1_PERSIST_AUTHORISED_ENV = "HERMES_CANDLE_D1_DURABLE_SQL_PERSIST_AUTHORISED"

H4_RUN_MARKER = "H4_LIVE_SEAL_DURABLE_SQL_V1"
D1_RUN_MARKER = "D1_LIVE_SEAL_DURABLE_SQL_V1"


def _real_conn_factory():
    """Lazy real DB connection builder — a NEW short-lived connection per seal event (H4 seals at most
    every 4h, D1 at most once/day; there is no benefit to a persistent pool here, and a fresh connection
    per event avoids ever holding a stale/broken connection across the long gaps between seals)."""
    import pymysql
    from env_config import get_db_config
    cfg = get_db_config()
    return pymysql.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"], password=cfg["password"],
                           database=cfg["database"])


class DurableSqlWriter:
    """Live durable-SQL persistence for ONE governed timeframe (H4 or D1). `conn_factory` is a no-arg
    callable returning a new DB-API connection (injectable for tests; defaults to the real MariaDB)."""

    def __init__(self, *, table, run_id_marker, conn_factory=_real_conn_factory, source_guard=None):
        sqlc.assert_known_table(table)
        self.table = table
        self.run_id_marker = run_id_marker
        self.conn_factory = conn_factory
        # Architect review correction: optional `(cursor, row) -> bool` guard, checked BEFORE any write,
        # on the SAME connection/cursor as the write itself (so the check and the write are atomic w.r.t.
        # any concurrent writer). None (default, used for H4 — it has no durable prerequisite) -> always
        # allowed. D1's writer is built with a guard that verifies its 6 canonical durable H4 children
        # genuinely exist as status-OK rows FIRST — durable D1 must never get ahead of durable H4 truth.
        self.source_guard = source_guard
        self.enabled = True
        self.metrics = {"attempted": 0, "written": 0, "match_skip": 0, "conflict_detected": 0,
                        "connect_fail": 0, "write_fail": 0, "not_ok_skipped": 0,
                        "source_incomplete_refused": 0}

    def on_sealed(self, env):
        """Offer a freshly-sealed candle envelope. Returns a report dict; NEVER raises."""
        if env is None or env.get("status") != "OK":
            self.metrics["not_ok_skipped"] += 1
            return {"attempted": False, "reason": "NOT_OK_NOT_DURABLE"}
        self.metrics["attempted"] += 1
        try:
            row = sqlc.row_from_envelope(env, derivation_run_id=self.run_id_marker)
        except Exception as exc:  # noqa: BLE001 - malformed envelope must never break the caller
            self.metrics["write_fail"] += 1
            return {"attempted": True, "wrote": False, "reason": "ROW_MAP_FAIL", "error": repr(exc)[:200]}
        try:
            conn = self.conn_factory()
        except Exception as exc:  # noqa: BLE001 - DB unreachable is visible, never fatal to the seal path
            self.metrics["connect_fail"] += 1
            return {"attempted": True, "wrote": False, "reason": "DB_CONNECT_FAIL", "error": repr(exc)[:200]}
        status = None
        refused = False
        try:
            cur = conn.cursor()
            try:
                if self.source_guard is not None and not self.source_guard(cur, row):
                    refused = True
                else:
                    status = sqlc.upsert_new_only(cur, self.table, row)   # 'new' | 'match' | 'conflict'
                    if status == "new":
                        conn.commit()
            finally:
                cur.close()
        except Exception as exc:  # noqa: BLE001 - write fault visible via metrics, never raised
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            self.metrics["write_fail"] += 1
            return {"attempted": True, "wrote": False, "reason": "DURABLE_SQL_WRITE_FAIL", "error": repr(exc)[:200]}
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        if refused:
            self.metrics["source_incomplete_refused"] += 1
            return {"attempted": True, "wrote": False, "status": "refused",
                    "reason": "DURABLE_SOURCE_INCOMPLETE"}
        if status == "new":
            self.metrics["written"] += 1
            return {"attempted": True, "wrote": True, "status": "inserted", "table": self.table}
        if status == "match":
            self.metrics["match_skip"] += 1
            return {"attempted": True, "wrote": False, "status": "match", "reason": "ALREADY_PRESENT_MATCH"}
        self.metrics["conflict_detected"] += 1
        return {"attempted": True, "wrote": False, "status": "conflict",
                "reason": "DURABLE_SQL_CONFLICT_NEEDS_REPAIR_WO"}

    def status(self):
        return {"enabled": True, "table": self.table, **self.metrics}


class DisabledDurableSqlWriter:
    """No-op (default). Opens no DB connection, writes nothing."""
    enabled = False

    def on_sealed(self, *a, **k):
        return {"attempted": False, "reason": "DURABLE_SQL_PERSIST_DISABLED"}

    def status(self):
        return {"enabled": False}


def build_h4_durable_sql_writer_from_env():
    """Boot factory for the H4 durable-SQL live writer. Returns DisabledDurableSqlWriter (no-op) unless
    HERMES_CANDLE_H4_DURABLE_SQL_PERSIST_ENABLED=true, which then requires ..._AUTHORISED=true (fail-loud)."""
    from env_config import get_env_bool
    if not get_env_bool(H4_PERSIST_ENABLED_ENV, False):
        return DisabledDurableSqlWriter()
    if not get_env_bool(H4_PERSIST_AUTHORISED_ENV, False):
        raise ValueError(f"GOV-CANDLE-DURABLE-SQL-010: {H4_PERSIST_ENABLED_ENV}=true requires "
                         f"{H4_PERSIST_AUTHORISED_ENV}=true (refusing durable persistence without authorisation)")
    return DurableSqlWriter(table=sqlc.TABLE_H4, run_id_marker=H4_RUN_MARKER)


def build_d1_durable_sql_writer_from_env():
    """Boot factory for the D1 durable-SQL live writer. Returns DisabledDurableSqlWriter (no-op) unless
    HERMES_CANDLE_D1_DURABLE_SQL_PERSIST_ENABLED=true, which then requires ..._AUTHORISED=true (fail-loud)."""
    from env_config import get_env_bool
    if not get_env_bool(D1_PERSIST_ENABLED_ENV, False):
        return DisabledDurableSqlWriter()
    if not get_env_bool(D1_PERSIST_AUTHORISED_ENV, False):
        raise ValueError(f"GOV-CANDLE-DURABLE-SQL-011: {D1_PERSIST_ENABLED_ENV}=true requires "
                         f"{D1_PERSIST_AUTHORISED_ENV}=true (refusing durable persistence without authorisation)")
    return DurableSqlWriter(table=sqlc.TABLE_D1, run_id_marker=D1_RUN_MARKER, source_guard=_d1_source_guard)


def _d1_source_guard(cursor, row):
    """Architect review correction: D1 durable persistence must never get ahead of durable H4 truth.
    Reuses `candle_durable_sql_contract_v1.d1_durable_h4_source_complete` — the ONE 'are this D1's 6 H4
    children durably present' rule, never duplicated."""
    return sqlc.d1_durable_h4_source_complete(cursor, row["instrument"], row["open_time"])
