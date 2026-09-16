"""HERMES governed H4 DURABLE SQL historical backfill v1 — full trustworthy range, dark/dry-run by default.
WO-HELM-HERMES-DEV-DARWIN-DURABLE-CANONICAL-HISTORICAL-AUTHORITY-0001.

Reconstructs the COMPLETE trustworthy XAU_USD H4 history into the durable canonical table
`canonical_candles_h4` (migration 027) from the durable MariaDB `candles_H1` source, using ONLY the
existing governed derivation (`candle_h4_derivation_v1.derive_h4`) — there is exactly ONE H4 construction
implementation; this module supplies it with the full historical H1 range instead of a bounded recent
window. This is DISTINCT from `candle_h4_history_seed_backfill_v1` (which seeds a BOUNDED, count-capped
Redis OPERATIONAL cache for EMA-200 warm-up); this module targets the UNBOUNDED-by-design durable SQL
historical AUTHORITY DARWIN consumes.

Full range, no arbitrary depth cap: every H4 bucket the source H1 data can honestly support is a candidate.
Only a genuine 4/4-child, status-OK bucket is ever inserted; an incomplete/gapped window is counted and
reported (rejection reason), never fabricated, never inserted. Idempotent by (instrument, timeframe,
open_time): re-running skips exact matches, and FAILS LOUD (raises, never overwrites) on a conflicting
existing row — mirroring the ratified Redis-backfill precedent. Bounded/deterministic/restart-safe: each
accepted candidate is committed individually, so a mid-run interruption leaves already-written rows durably
committed and a re-run naturally resumes (matches are skipped, only the remaining candidates are written).

NEVER `candles_H4` (legacy, UTC-00-anchored, stale since 2026-06-15) or `candles_D1` as a source — those
identifiers never appear as a FROM/JOIN target anywhere in this module.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

from utils import candle_h4_derivation_v1 as h4d
from utils import candle_durable_sql_contract_v1 as sqlc

CANONICAL_INSTRUMENT = sqlc.CANONICAL_INSTRUMENT
H1_SOURCE_TABLE = "candles_H1"          # the ONLY approved source table
HALT_CODE = 101

DERIVATION_RUN_MARKER = "H4_DURABLE_SQL_BACKFILL_V1"
_MAX_BUCKETS_HARD_CAP = 20000            # safety valve only — full XAU_USD H1 history (~2 yrs) is ~3-4k buckets

BACKFILL_ENABLED_ENV = "HERMES_H4_DURABLE_SQL_BACKFILL_ENABLED"
BACKFILL_AUTHORISED_ENV = "HERMES_H4_DURABLE_SQL_BACKFILL_AUTHORISED"
BACKFILL_DRY_RUN_ENV = "HERMES_H4_DURABLE_SQL_BACKFILL_DRY_RUN"       # default true (safe)


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


def _read_h1_source_rows(db_conn):
    """Full, unbounded read of every genuine complete H1 XAU_USD row (durable MariaDB source; read-only
    SELECT). The table itself bounds the result (~11k rows currently) — no artificial lookback window."""
    cur = db_conn.cursor()
    try:
        cur.execute(
            f"SELECT timestamp, open, high, low, close, volume FROM {H1_SOURCE_TABLE} "
            "WHERE instrument=%s AND complete=1 ORDER BY timestamp ASC",
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


def build_h4_candidates_full_range(h1_rows, *, max_buckets=_MAX_BUCKETS_HARD_CAP, now=None):
    """Walk EVERY H4 bucket across the full available H1 range, OLDEST-FIRST (so the durable table fills
    chronologically), using the exact governed selector (h4d.h1_children_in_bucket) and derivation
    (h4d.derive_h4) the live producer uses. Only an exact 4/4 genuine on-the-hour-grid bucket is accepted
    (status OK, source_count==4, gap_state NONE); anything short/gapped is REJECTED (never synthesised).
    Pure/read-only; no I/O of its own."""
    now = now or datetime.now(timezone.utc)
    if not h1_rows:
        return []
    start_bucket = h4d.h4_bucket_open(h1_rows[0]["timestamp"])
    end_bucket = h4d.h4_bucket_open(h1_rows[-1]["timestamp"])
    results = []
    b = start_bucket
    while b <= end_bucket and len(results) < max_buckets:
        kids = h4d.h1_children_in_bucket(b, h1_rows)
        bucket_epoch = int(b.timestamp())
        if len(kids) != h4d.H4_EXPECTED_CHILDREN:
            results.append({"bucket_open_epoch": bucket_epoch, "accepted": False,
                            "reason": "H4_INCOMPLETE_CHILDREN", "child_count": len(kids)})
            b = b + timedelta(seconds=h4d.H4_SECONDS)
            continue
        try:
            env, _meta = h4d.derive_h4(instrument=CANONICAL_INSTRUMENT, h4_open=b, h1_children=kids,
                                       generated_at_utc=b + timedelta(seconds=h4d.H4_SECONDS), is_closed=True)
        except Exception as exc:  # noqa: BLE001 - a non-derivable bucket is a REJECT, never a fabricated candle
            results.append({"bucket_open_epoch": bucket_epoch, "accepted": False,
                            "reason": "H4_DERIVE_FAILED", "error": repr(exc)[:160]})
            b = b + timedelta(seconds=h4d.H4_SECONDS)
            continue
        d = env["data"]
        if env["status"] != "OK" or d["source_count"] != h4d.H4_EXPECTED_CHILDREN \
                or d["source_coverage"] != 1.0 or d["gap_state"] not in (None, "NONE"):
            results.append({"bucket_open_epoch": bucket_epoch, "accepted": False,
                            "reason": "H4_DERIVED_NOT_OK", "derived_status": env["status"]})
            b = b + timedelta(seconds=h4d.H4_SECONDS)
            continue
        results.append({"bucket_open_epoch": bucket_epoch, "accepted": True, "reason": None, "env": env})
        b = b + timedelta(seconds=h4d.H4_SECONDS)
    return results


def dry_run_plan(db_conn, *, config=None, now=None):
    """Bounded, read-only dry-run against the LIVE durable table (SELECT existing rows to classify
    idempotency; NO INSERT statement is ever issued here). Returns full accepted/rejected/idempotency
    counts, matching the acceptance-proof reporting requirement."""
    now = now or datetime.now(timezone.utc)
    config = config or parse_backfill_config_from_env()
    h1_rows = _read_h1_source_rows(db_conn)
    cands = build_h4_candidates_full_range(h1_rows, now=now)
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
            existing = sqlc.fetch_existing(cur, sqlc.TABLE_H4, row["instrument"], row["timeframe"], row["open_time"])
            status = sqlc.classify_against_existing(existing, row)
            idem[status] += 1
            if status == "conflict":
                conflicts.append(env["data"]["timestamp_utc"])
    finally:
        cur.close()
    return {
        "mode": "DRY_RUN", "no_write_proof": True, "source_table": H1_SOURCE_TABLE,
        "target_table": sqlc.TABLE_H4, "source_forbidden_h4_table": True, "source_forbidden_d1_table": True,
        "anchor": "22:00Z NY-5PM (h4d.h4_bucket_open)", "config": config.as_dict(),
        "scanned_h1_rows": len(h1_rows), "candidate_bucket_count": len(cands),
        "accepted_count": len(accepted), "rejected_count": len(rejected), "rejection_reasons": reason_counts,
        "earliest_candidate_utc": min(ts_all) if ts_all else None,
        "latest_candidate_utc": max(ts_all) if ts_all else None,
        "idempotency": idem, "conflicts": conflicts,
    }


def execute_backfill(db_conn, *, config=None, now=None):
    """LIVE execution — refuses unless enabled+authorised+dry_run=false (fail-loud). For each accepted
    candidate: idempotency/conflict check is UNGUARDED (a conflict is a deliberate fail-loud refusal, never
    an incidental fault) — only the INSERT mechanics are wrapped so one candidate's incidental write fault
    doesn't abort the whole batch. Commits per-row (restart-safe: re-running after an interruption resumes
    cleanly via match-skip on already-written rows)."""
    now = now or datetime.now(timezone.utc)
    config = config or parse_backfill_config_from_env()
    if not config.live_write_allowed:
        raise ValueError("GOV-CANDLE-H4-DURABLE-SQL-BF-010: live H4 durable backfill refused — requires "
                         f"{BACKFILL_ENABLED_ENV}=true AND {BACKFILL_AUTHORISED_ENV}=true AND "
                         f"{BACKFILL_DRY_RUN_ENV}=false (dark/dry-run by default)")
    h1_rows = _read_h1_source_rows(db_conn)
    cands = build_h4_candidates_full_range(h1_rows, now=now)
    written, skipped_match, failed_writes = 0, 0, []
    cur = db_conn.cursor()
    try:
        for c in (x for x in cands if x["accepted"]):
            env = c["env"]
            row = sqlc.row_from_envelope(env, derivation_run_id=DERIVATION_RUN_MARKER)
            existing = sqlc.fetch_existing(cur, sqlc.TABLE_H4, row["instrument"], row["timeframe"], row["open_time"])
            status = sqlc.classify_against_existing(existing, row)
            if status == "conflict":
                raise ValueError("GOV-CANDLE-H4-DURABLE-SQL-BF-011: existing canonical H4 row conflicts "
                                 f"with candidate at {env['data']['timestamp_utc']} — refusing to "
                                 "overwrite (needs a separate repair WO)")
            if status == "match":
                skipped_match += 1
                continue
            try:
                sqlc.insert_row(cur, sqlc.TABLE_H4, row)
                db_conn.commit()
                written += 1
            except Exception as exc:  # noqa: BLE001 - one candidate's write fault must not abort the bounded batch
                db_conn.rollback()
                failed_writes.append({"timestamp_utc": env["data"]["timestamp_utc"], "error": repr(exc)[:200]})
    finally:
        cur.close()
    return {"written": written, "skipped_match": skipped_match, "failed": len(failed_writes),
            "failed_detail": failed_writes, "mode": "LIVE_WRITE", "target_table": sqlc.TABLE_H4}
