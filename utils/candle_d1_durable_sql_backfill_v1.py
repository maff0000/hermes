"""HERMES governed D1 DURABLE SQL historical backfill v1 — full canonical-H4-backed range, dark/dry-run by
default. WO-HELM-HERMES-DEV-DARWIN-DURABLE-CANONICAL-HISTORICAL-AUTHORITY-0001.

Reconstructs the COMPLETE D1 history the durable canonical H4 table can honestly support into
`canonical_candles_d1` (migration 027), reading EXCLUSIVELY from `canonical_candles_h4` (never the legacy
midnight-anchored `candles_D1`, never a 24xH1 shortcut) and using ONLY the existing governed derivation
(`candle_d1_derivation_v1.derive_d1`) — there is exactly ONE D1 construction implementation. This module
must run AFTER the H4 durable backfill has populated `canonical_candles_h4` (it is its only source).

Only a genuine 6/6-child, status-OK D1 bucket is ever inserted; a short/gapped day is counted and reported
(rejection reason), never fabricated, never inserted. Idempotent by (instrument, timeframe, open_time):
re-running skips exact matches, FAILS LOUD (raises, never overwrites) on a conflicting existing row.
Restart-safe: each accepted candidate is committed individually.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

from utils import candle_d1_derivation_v1 as d1d
from utils import candle_durable_sql_contract_v1 as sqlc

CANONICAL_INSTRUMENT = sqlc.CANONICAL_INSTRUMENT
HALT_CODE = 101

DERIVATION_RUN_MARKER = "D1_DURABLE_SQL_BACKFILL_V1"
_MAX_BUCKETS_HARD_CAP = 5000

BACKFILL_ENABLED_ENV = "HERMES_D1_DURABLE_SQL_BACKFILL_ENABLED"
BACKFILL_AUTHORISED_ENV = "HERMES_D1_DURABLE_SQL_BACKFILL_AUTHORISED"
BACKFILL_DRY_RUN_ENV = "HERMES_D1_DURABLE_SQL_BACKFILL_DRY_RUN"       # default true (safe)


class BackfillConfig:
    def __init__(self, *, enabled, authorised, dry_run):
        self.enabled = bool(enabled)
        self.authorised = bool(authorised)
        self.dry_run = bool(dry_run)

    @property
    def live_write_allowed(self):
        return self.enabled and self.authorised and not self.dry_run

    def as_dict(self):
        return {"enabled": self.enabled, "authorised": self.authorised, "dry_run": self.dry_run,
                "live_write_allowed": self.live_write_allowed}


def parse_backfill_config_from_env():
    from env_config import get_env_bool
    enabled = get_env_bool(BACKFILL_ENABLED_ENV, False)
    if not enabled:
        return BackfillConfig(enabled=False, authorised=False, dry_run=True)
    if not get_env_bool(BACKFILL_AUTHORISED_ENV, False):
        raise SystemExit(HALT_CODE)
    dry_run = get_env_bool(BACKFILL_DRY_RUN_ENV, True)
    return BackfillConfig(enabled=True, authorised=True, dry_run=dry_run)


def _read_h4_source_rows(db_conn):
    """Full, unbounded read of every canonical durable H4 row (status is always OK by construction — the
    backfill/live writer never inserts a non-OK row — but the filter is kept as defence-in-depth)."""
    cur = db_conn.cursor()
    try:
        cur.execute(
            f"SELECT open_time, open, high, low, close, volume FROM {sqlc.TABLE_H4} "
            "WHERE instrument=%s AND timeframe='H4' AND status='OK' ORDER BY open_time ASC",
            (CANONICAL_INSTRUMENT,))
        rows = cur.fetchall()
    finally:
        cur.close()
    out = []
    for ts, o, h, lo, c, v in rows:
        open_dt = ts if getattr(ts, "tzinfo", None) is not None else ts.replace(tzinfo=timezone.utc)
        out.append({"timestamp": open_dt, "open": float(o), "high": float(h), "low": float(lo),
                    "close": float(c), "volume": int(v or 0)})
    return out


def build_d1_candidates_full_range(h4_rows, *, max_buckets=_MAX_BUCKETS_HARD_CAP, now=None):
    """Walk EVERY D1 (22:00Z-anchored) bucket across the full available canonical-H4 range, OLDEST-FIRST,
    using the exact governed selector (d1d.h4_children_in_bucket) and derivation (d1d.derive_d1). Only an
    exact 6/6 genuine fixed-grid bucket is accepted; anything short is REJECTED (never synthesised)."""
    now = now or datetime.now(timezone.utc)
    if not h4_rows:
        return []
    start_bucket = d1d.d1_bucket_open(h4_rows[0]["timestamp"])
    end_bucket = d1d.d1_bucket_open(h4_rows[-1]["timestamp"])
    results = []
    b = start_bucket
    while b <= end_bucket and len(results) < max_buckets:
        kids = d1d.h4_children_in_bucket(b, h4_rows)
        bucket_epoch = int(b.timestamp())
        if len(kids) != d1d.D1_EXPECTED_CHILDREN:
            results.append({"bucket_open_epoch": bucket_epoch, "accepted": False,
                            "reason": "D1_INCOMPLETE_CHILDREN", "child_count": len(kids)})
            b = b + timedelta(seconds=d1d.D1_SECONDS)
            continue
        try:
            env, _meta = d1d.derive_d1(instrument=CANONICAL_INSTRUMENT, d1_open=b, h4_children=kids,
                                       generated_at_utc=b + timedelta(seconds=d1d.D1_SECONDS), is_closed=True)
        except Exception as exc:  # noqa: BLE001 - a non-derivable bucket is a REJECT, never a fabricated candle
            results.append({"bucket_open_epoch": bucket_epoch, "accepted": False,
                            "reason": "D1_DERIVE_FAILED", "error": repr(exc)[:160]})
            b = b + timedelta(seconds=d1d.D1_SECONDS)
            continue
        d = env["data"]
        if env["status"] != "OK" or d["source_count"] != d1d.D1_EXPECTED_CHILDREN \
                or d["source_coverage"] != 1.0 or d["gap_state"] not in (None, "NONE"):
            results.append({"bucket_open_epoch": bucket_epoch, "accepted": False,
                            "reason": "D1_DERIVED_NOT_OK", "derived_status": env["status"]})
            b = b + timedelta(seconds=d1d.D1_SECONDS)
            continue
        results.append({"bucket_open_epoch": bucket_epoch, "accepted": True, "reason": None, "env": env})
        b = b + timedelta(seconds=d1d.D1_SECONDS)
    return results


def dry_run_plan(db_conn, *, config=None, now=None):
    now = now or datetime.now(timezone.utc)
    config = config or parse_backfill_config_from_env()
    h4_rows = _read_h4_source_rows(db_conn)
    cands = build_d1_candidates_full_range(h4_rows, now=now)
    accepted = [c for c in cands if c["accepted"]]
    rejected = [c for c in cands if not c["accepted"]]
    reason_counts = {}
    for c in rejected:
        reason_counts[c["reason"]] = reason_counts.get(c["reason"], 0) + 1
    cur = db_conn.cursor()
    idem = {"new": 0, "match": 0, "conflict": 0}
    conflicts, ts_all = [], []
    try:
        for c in accepted:
            env = c["env"]
            row = sqlc.row_from_envelope(env, derivation_run_id=DERIVATION_RUN_MARKER)
            ts_all.append(env["data"]["timestamp_utc"])
            existing = sqlc.fetch_existing(cur, sqlc.TABLE_D1, row["instrument"], row["timeframe"], row["open_time"])
            status = sqlc.classify_against_existing(existing, row)
            idem[status] += 1
            if status == "conflict":
                conflicts.append(env["data"]["timestamp_utc"])
    finally:
        cur.close()
    return {
        "mode": "DRY_RUN", "no_write_proof": True, "source_table": sqlc.TABLE_H4,
        "target_table": sqlc.TABLE_D1, "source_forbidden_legacy_d1_table": True,
        "source_forbidden_24xh1_shortcut": True, "anchor": "22:00Z NY-5PM (d1d.d1_bucket_open)",
        "config": config.as_dict(), "scanned_h4_rows": len(h4_rows), "candidate_bucket_count": len(cands),
        "accepted_count": len(accepted), "rejected_count": len(rejected), "rejection_reasons": reason_counts,
        "earliest_candidate_utc": min(ts_all) if ts_all else None,
        "latest_candidate_utc": max(ts_all) if ts_all else None,
        "idempotency": idem, "conflicts": conflicts,
    }


def execute_backfill(db_conn, *, config=None, now=None):
    now = now or datetime.now(timezone.utc)
    config = config or parse_backfill_config_from_env()
    if not config.live_write_allowed:
        raise ValueError("GOV-CANDLE-D1-DURABLE-SQL-BF-010: live D1 durable backfill refused — requires "
                         f"{BACKFILL_ENABLED_ENV}=true AND {BACKFILL_AUTHORISED_ENV}=true AND "
                         f"{BACKFILL_DRY_RUN_ENV}=false (dark/dry-run by default)")
    h4_rows = _read_h4_source_rows(db_conn)
    cands = build_d1_candidates_full_range(h4_rows, now=now)
    written, skipped_match, failed_writes = 0, 0, []
    cur = db_conn.cursor()
    try:
        for c in (x for x in cands if x["accepted"]):
            env = c["env"]
            row = sqlc.row_from_envelope(env, derivation_run_id=DERIVATION_RUN_MARKER)
            existing = sqlc.fetch_existing(cur, sqlc.TABLE_D1, row["instrument"], row["timeframe"], row["open_time"])
            status = sqlc.classify_against_existing(existing, row)
            if status == "conflict":
                raise ValueError("GOV-CANDLE-D1-DURABLE-SQL-BF-011: existing canonical D1 row conflicts "
                                 f"with candidate at {env['data']['timestamp_utc']} — refusing to "
                                 "overwrite (needs a separate repair WO)")
            if status == "match":
                skipped_match += 1
                continue
            try:
                sqlc.insert_row(cur, sqlc.TABLE_D1, row)
                db_conn.commit()
                written += 1
            except Exception as exc:  # noqa: BLE001
                db_conn.rollback()
                failed_writes.append({"timestamp_utc": env["data"]["timestamp_utc"], "error": repr(exc)[:200]})
    finally:
        cur.close()
    return {"written": written, "skipped_match": skipped_match, "failed": len(failed_writes),
            "failed_detail": failed_writes, "mode": "LIVE_WRITE", "target_table": sqlc.TABLE_D1}
