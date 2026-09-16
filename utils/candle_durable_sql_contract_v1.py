"""HERMES governed durable SQL CANDLE contract v1 — shared row shape + idempotency for canonical H4/D1.
WO-HELM-HERMES-DEV-DARWIN-DURABLE-CANONICAL-HISTORICAL-AUTHORITY-0001.

Pure, DB-driver-agnostic helpers shared by BOTH the historical backfill scripts (candle_h4_durable_sql_
backfill_v1, candle_d1_durable_sql_backfill_v1) and the live durable-persistence hook
(candle_durable_sql_writer_v1) — there is exactly ONE row-shape / fingerprint / idempotency implementation,
never a second one duplicated between backfill and live paths. No DB connection is opened here; callers
supply a DB-API 2.0 cursor (pymysql-compatible: execute(sql, params) / fetchone() / fetchall()).

Target tables (migration 027): `canonical_candles_h4`, `canonical_candles_d1` — durable, versioned,
explicitly non-legacy (never `candles_H4` / `candles_D1`). Only COMPLETE, status-OK derived candles are
ever written here (an incomplete/short window is reported by the caller, never inserted) — mirrors the
existing live-history-forward rule (`candle_h4_publish_wire_v1._forward_history`: "ONLY a COMPLETE 4/4
sealed bucket... is appended to history").

Identity = (instrument, timeframe, open_time). Idempotent by identity: an identical existing row is a
silent match (skip); a DIFFERING existing row at the same identity is a CONFLICT — this module only
classifies (`upsert_new_only` returns the classification); whether a conflict raises (backfill, fail-loud)
or is fault-isolated (live writer, never breaks the seal path) is the CALLER's decision, matching the
existing Redis-history-backfill precedent (candle_h4_history_seed_backfill_v1.execute_backfill).
"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

TABLE_H4 = "canonical_candles_h4"
TABLE_D1 = "canonical_candles_d1"
KNOWN_TABLES = (TABLE_H4, TABLE_D1)
CANONICAL_INSTRUMENT = "XAU_USD"

# Row shape (DB-generated `id`/`created_at` excluded — never supplied by callers).
INSERT_COLUMNS = (
    "instrument", "timeframe", "open_time", "open", "high", "low", "close", "volume", "is_closed",
    "status", "source_timeframe", "derivation_policy", "source_policy_epoch", "source_count",
    "expected_source_count", "source_coverage", "gap_state", "derivation_run_id",
    "derivation_generated_at_utc",
)

# Fields that define genuine data identity/content — INCLUDING the governed semantics under which the
# candle was produced (`derivation_policy`, `source_policy_epoch`): a row with the same OHLCV but a
# DIFFERENT derivation policy or source policy epoch is NOT the same canonical fact and must be a
# `conflict`, never a silent `match` (Architect review correction, WO-HELM-HERMES-DEV-DARWIN-DURABLE-
# CANONICAL-HISTORICAL-AUTHORITY-0001). `derivation_run_id` and `derivation_generated_at_utc` are true
# EXECUTION PROVENANCE (which run/process produced the row, and when) and remain intentionally EXCLUDED:
# re-running a backfill after the live writer already persisted the same candle under the SAME policy (or
# vice versa) must still be recognised as the SAME genuine candle (match/skip), never a false conflict.
_FINGERPRINT_FIELDS = (
    "instrument", "timeframe", "open_time", "open", "high", "low", "close", "volume", "is_closed",
    "status", "source_timeframe", "derivation_policy", "source_policy_epoch",
    "source_count", "expected_source_count", "source_coverage", "gap_state",
)

_UTC_MS = "%Y-%m-%dT%H:%M:%S.%f"


def _parse_utc_naive(ts):
    """Parse a governed contract UTC-ms timestamp string ('...Z') to a NAIVE UTC datetime (matches the
    existing candles_H1/M1/etc DATETIME column convention — MariaDB DATETIME carries no tz of its own;
    every value in this table is UTC by contract, documented, never mixed)."""
    if isinstance(ts, datetime):
        return ts.replace(tzinfo=None) if ts.tzinfo is None else ts.astimezone(timezone.utc).replace(tzinfo=None)
    return datetime.strptime(ts[:-1], _UTC_MS)


def assert_known_table(table):
    if table not in KNOWN_TABLES:
        raise ValueError(f"GOV-CANDLE-DURABLE-SQL-001: unknown/unsupported table {table!r} "
                         f"(only {KNOWN_TABLES} are governed durable canonical targets)")
    return True


def row_from_envelope(env, *, derivation_run_id):
    """Build the governed row dict from an already-derived, already-validated H4/D1 envelope
    (candle_h4_derivation_v1.derive_h4 / candle_d1_derivation_v1.derive_d1 output). Never called for a
    non-OK envelope by any caller in this WO (backfill/live writer both gate on status=='OK' first) —
    this function itself does not gate; it only maps fields, so it stays a pure, reusable single mapping."""
    d = env["data"]
    if d["instrument"] != CANONICAL_INSTRUMENT:
        raise ValueError(f"GOV-CANDLE-DURABLE-SQL-002: instrument {d['instrument']!r} not allowed "
                         f"(durable canonical H4/D1 is {CANONICAL_INSTRUMENT} only)")
    return {
        "instrument": d["instrument"],
        "timeframe": d["timeframe"],
        "open_time": _parse_utc_naive(d["timestamp_utc"]),
        "open": d["open"], "high": d["high"], "low": d["low"], "close": d["close"],
        "volume": d["volume"],
        "is_closed": 1 if d["is_closed"] else 0,
        "status": env["status"],
        "source_timeframe": d["source_timeframe"],
        "derivation_policy": d["derivation_policy"],
        "source_policy_epoch": d["source_policy_epoch"],
        "source_count": d["source_count"],
        "expected_source_count": d["expected_source_count"],
        "source_coverage": d["source_coverage"],
        "gap_state": d["gap_state"] or "NONE",
        "derivation_run_id": derivation_run_id,
        "derivation_generated_at_utc": _parse_utc_naive(env["generated_at_utc"]),
    }


def fingerprint_row(row):
    """Stable fingerprint over the genuine-content fields only (never provenance). Two rows with the same
    fingerprint represent the SAME real candle regardless of which process/run inserted them. Normalises
    DECIMAL-column values read back from MariaDB (pymysql yields `decimal.Decimal`) against the Python
    `float` values a freshly-derived candidate carries — without this, a byte-identical price
    (Decimal('2412.50000') vs float(2412.5)) would fingerprint DIFFERENTLY and every genuine idempotent
    re-run would be misclassified as a false 'conflict' instead of 'match' (caught by a failing test)."""
    def _s(v):
        if isinstance(v, datetime):
            return v.isoformat()
        if isinstance(v, (float, Decimal)):
            return round(float(v), 8)
        return v
    key = json.dumps({k: _s(row[k]) for k in _FINGERPRINT_FIELDS}, sort_keys=True, default=str)
    return hashlib.sha256(key.encode()).hexdigest()


def classify_against_existing(existing_row, candidate_row):
    """'new' | 'match' | 'conflict'. `existing_row` is None (not present) or a dict shaped like
    INSERT_COLUMNS (as returned by `fetch_existing`)."""
    if existing_row is None:
        return "new"
    return "match" if fingerprint_row(existing_row) == fingerprint_row(candidate_row) else "conflict"


def select_existing_sql(table):
    assert_known_table(table)
    cols = ",".join(INSERT_COLUMNS)
    return f"SELECT {cols} FROM {table} WHERE instrument=%s AND timeframe=%s AND open_time=%s"


def insert_sql(table):
    assert_known_table(table)
    cols = ",".join(INSERT_COLUMNS)
    placeholders = ",".join(["%s"] * len(INSERT_COLUMNS))
    return f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"


def range_query_sql(table):
    """The Phase-7 supported consumer range-query shape, parameterised (instrument, timeframe unused for
    the per-TF physical tables but kept for API symmetry with the unified view), from, to."""
    assert_known_table(table)
    cols = ",".join(INSERT_COLUMNS)
    return (f"SELECT {cols} FROM {table} WHERE instrument=%s AND open_time>=%s AND open_time<%s "
            "ORDER BY open_time ASC")


def fetch_existing(cursor, table, instrument, timeframe, open_time):
    cursor.execute(select_existing_sql(table), (instrument, timeframe, open_time))
    row = cursor.fetchone()
    if row is None:
        return None
    return dict(zip(INSERT_COLUMNS, row))


def insert_row(cursor, table, row):
    cursor.execute(insert_sql(table), tuple(row[c] for c in INSERT_COLUMNS))


def upsert_new_only(cursor, table, row):
    """Returns 'new' (freshly inserted) | 'match' | 'conflict'. NEVER overwrites an existing row (no
    UPDATE statement exists anywhere in this module). A 'conflict' performs no write and leaves the
    existing row untouched; whether that classification raises or is fault-isolated is entirely the
    caller's responsibility."""
    existing = fetch_existing(cursor, table, row["instrument"], row["timeframe"], row["open_time"])
    status = classify_against_existing(existing, row)
    if status == "new":
        insert_row(cursor, table, row)
    return status


_D1_SOURCE_INCOMPLETE = "source_incomplete"
_D1_SOURCE_CONFLICT = "source_conflict"
_D1_SOURCE_VERIFIED = "source_verified"


def d1_durable_source_lineage_status(cursor, instrument, d1_open_time, candidate_row):
    """Architect review correction, round 2 (WO-HELM-HERMES-DEV-DARWIN-DURABLE-CANONICAL-HISTORICAL-
    AUTHORITY-0001): PRESENCE of 6 durable H4 rows is not enough — durable D1 must be PROVABLY DERIVED from
    the durable canonical H4 truth it claims as source, not merely co-located with 6 rows that happen to
    exist at the right identities. Without this, a durable H4 row A can sit in `canonical_candles_h4`
    while a live D1 seal is built from a DIFFERENT transient H4 fact B (e.g. a later live H4 re-seal that
    itself conflicted and never overwrote A) — the old presence-only guard would see 6/6 timestamps and
    wave the D1 through, even though the D1 being persisted does not actually agree with durable H4 truth.

    Loads the exact 6 expected durable H4 rows (by the EXISTING governed selector
    `candle_d1_derivation_v1.d1_child_h4_opens`), requiring `status='OK'` AND the current governed
    `derivation_policy`/`source_policy_epoch` (an H4 row present under an incompatible/legacy policy is
    correctly excluded — never a valid source, however genuinely it exists). If fewer than 6 durable H4
    rows satisfy that -> `'source_incomplete'` (the routine, expected case; never a fault). If all 6 are
    present, reconstructs a D1 envelope from THOSE EXACT durable rows using the EXISTING
    `candle_d1_derivation_v1.derive_d1()` (never a second D1 construction algorithm) and compares its
    canonical content fingerprint against `candidate_row` (the D1 actually being offered for persistence,
    already built from whatever the live pipeline had in memory). Fingerprints agree -> `'source_verified'`
    (durable D1 may proceed). Fingerprints disagree -> `'source_conflict'` — the offered D1 does NOT
    genuinely derive from durable H4 truth; durable persistence must refuse, visibly, without touching
    either the offered candidate or the existing durable H4 rows.

    `d1_open_time` may be naive or aware UTC (matches the DB's naive convention either way)."""
    from utils import candle_d1_derivation_v1 as d1d
    from utils import candle_h4_derivation_v1 as h4d
    from utils import candle_contract_v1 as cc
    aware = d1_open_time if getattr(d1_open_time, "tzinfo", None) is not None \
        else d1_open_time.replace(tzinfo=timezone.utc)
    expected_opens = [cc.normalise_utc(o).replace(tzinfo=None) for o in d1d.d1_child_h4_opens(aware)]
    placeholders = ",".join(["%s"] * len(expected_opens))
    cursor.execute(
        f"SELECT open_time, open, high, low, close, volume FROM {TABLE_H4} "
        f"WHERE instrument=%s AND timeframe='H4' AND status='OK' "
        f"AND derivation_policy=%s AND source_policy_epoch=%s "
        f"AND open_time IN ({placeholders}) ORDER BY open_time ASC",
        (instrument, cc.DERIVATION_POLICY_H4_FROM_H1, h4d.SOURCE_POLICY_EPOCH, *expected_opens))
    rows = cursor.fetchall()
    if len(rows) != d1d.D1_EXPECTED_CHILDREN:
        return _D1_SOURCE_INCOMPLETE
    h4_children = [{"timestamp": r[0].replace(tzinfo=timezone.utc) if r[0].tzinfo is None else r[0],
                    "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]),
                    "volume": int(r[5] or 0)} for r in rows]
    env, _meta = d1d.derive_d1(instrument=instrument, d1_open=aware, h4_children=h4_children,
                               generated_at_utc=aware + timedelta(seconds=d1d.D1_SECONDS), is_closed=True)
    if env["status"] != "OK":
        return _D1_SOURCE_INCOMPLETE   # defence-in-depth; unreachable given 6 already-OK durable children
    durable_source_row = row_from_envelope(env, derivation_run_id="DURABLE_SOURCE_LINEAGE_CHECK")
    return _D1_SOURCE_VERIFIED if fingerprint_row(durable_source_row) == fingerprint_row(candidate_row) \
        else _D1_SOURCE_CONFLICT
