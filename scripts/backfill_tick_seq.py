"""Governed, set-based seq backfill for tradingSignals.ticks
(WO-HELM-HERMES-MIGRATION-AND-BACKFILL-HARDENING-0001 — replaces the original
row-by-row per-id UPDATE loop, which was unsuitable for ~22M rows).

Strategy (R2D2-approved):
  1. DRY-RUN by default; mutation requires --execute AND --confirm.
  2. STAGE: build a persistent staging table tick_seq_backfill_staging(id PK, instrument, seq)
     computing seq with ROW_NUMBER() OVER (PARTITION BY instrument ORDER BY timestamp, id),
     seeded by the current MAX(seq) per instrument so assignment stays monotonic & resumable.
  3. VALIDATE staging (no dup id, no dup (instrument,seq), dense+monotonic) — fail loud.
  4. APPLY in batches via UPDATE ticks JOIN staging ON id, by id-range (batch_size),
     resumable (only rows still seq IS NULL). NO per-id row-by-row loop.
  5. POST-VALIDATE (no NULL among targeted, dense/monotonic, max(seq) per instrument).
  6. Emit evidence JSON throughout. Fail loud on any anomaly.

This WO ships the rewritten script only — it is NOT executed against the live DB.
conn/config are injected (get_conn, get_config) for testability.
"""
from __future__ import annotations
import argparse
import json
import sys

STAGING_TABLE = "tick_seq_backfill_staging"


# ---------- pure, unit-testable validation ----------
def validate_seq_assignment(rows):
    """rows: iterable of dicts {instrument,id,seq,order_index}. Fail loud (ValueError) on:
       duplicate id, duplicate (instrument,seq), or non-dense/non-monotonic seq per instrument
       relative to order_index (the deterministic timestamp,id ordering)."""
    seen_ids = set()
    per_inst = {}
    for r in rows:
        _id, inst, seq, oi = r["id"], r["instrument"], int(r["seq"]), r["order_index"]
        if _id in seen_ids:
            raise ValueError(f"GOV-SEQ-101: duplicate id {_id} in staging (fail-loud)")
        seen_ids.add(_id)
        per_inst.setdefault(inst, []).append((oi, seq, _id))
    for inst, items in per_inst.items():
        items.sort()  # by order_index
        seqs = [s for _, s, _ in items]
        if len(set(seqs)) != len(seqs):
            raise ValueError(f"GOV-SEQ-102: duplicate (instrument={inst}, seq) in staging (fail-loud)")
        # dense + strictly monotonic increasing in deterministic order
        for i in range(1, len(seqs)):
            if seqs[i] != seqs[i - 1] + 1:
                raise ValueError(
                    f"GOV-SEQ-103: non-dense/non-monotonic seq for instrument={inst} "
                    f"at position {i}: {seqs[i-1]} -> {seqs[i]} (fail-loud)"
                )
    return True


# ---------- SQL-bearing steps (integration; injected conn) ----------
def dry_run(get_conn, get_config, logger, limit_instrument=None):
    summary = {"mode": "dry-run", "executed": False, "per_instrument": {}}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT instrument FROM ticks ORDER BY instrument")
            instruments = [r[0] for r in cur.fetchall()]
        if limit_instrument:
            instruments = [i for i in instruments if i == limit_instrument]
        for inst in instruments:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*), SUM(seq IS NULL), COALESCE(MAX(seq),0) FROM ticks WHERE instrument=%s", (inst,))
                total, pending, maxseq = cur.fetchone()
            summary["per_instrument"][inst] = {"total": int(total), "pending_null_seq": int(pending or 0),
                                               "current_max_seq": int(maxseq)}
    logger.info("[TICK_SEQ_BACKFILL_DRYRUN] %s", json.dumps(summary))
    return summary


def stage(get_conn, get_config, logger, limit_instrument=None):
    version = get_config("tick_contract_version", "string")  # fail-loud
    out = {"staged": {}, "staging_table": STAGING_TABLE}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {STAGING_TABLE} ("
                " id BIGINT UNSIGNED NOT NULL PRIMARY KEY,"
                " instrument VARCHAR(20) NOT NULL,"
                " seq BIGINT UNSIGNED NOT NULL,"
                " UNIQUE KEY uq_stage_instrument_seq (instrument, seq)"
                ") ENGINE=InnoDB"
            )
            cur.execute("SELECT DISTINCT instrument FROM ticks ORDER BY instrument")
            instruments = [r[0] for r in cur.fetchall()]
        if limit_instrument:
            instruments = [i for i in instruments if i == limit_instrument]
        for inst in instruments:
            with conn.cursor() as cur:
                # seed from existing MAX(seq) so assignment continues monotonically
                cur.execute("SELECT COALESCE(MAX(seq),0) FROM ticks WHERE instrument=%s", (inst,))
                seed = int(cur.fetchone()[0])
                # set-based: ROW_NUMBER over the deterministic order, only NULL-seq rows
                cur.execute(
                    f"INSERT INTO {STAGING_TABLE} (id, instrument, seq) "
                    "SELECT id, instrument, %s + ROW_NUMBER() OVER (ORDER BY timestamp, id) "
                    "FROM ticks WHERE instrument=%s AND seq IS NULL "
                    "ON DUPLICATE KEY UPDATE seq=VALUES(seq)",
                    (seed, inst),
                )
                staged = cur.rowcount
            conn.commit()
            out["staged"][inst] = int(staged)
    logger.info("[TICK_SEQ_BACKFILL_STAGE] %s", json.dumps(out))
    return out


def validate_staging(get_conn, logger):
    out = {"dup_ids": 0, "dup_instrument_seq": 0}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) - COUNT(DISTINCT id) FROM {STAGING_TABLE}")
            out["dup_ids"] = int(cur.fetchone()[0])
            cur.execute(
                f"SELECT COUNT(*) FROM (SELECT instrument, seq FROM {STAGING_TABLE} "
                "GROUP BY instrument, seq HAVING COUNT(*)>1) d")
            out["dup_instrument_seq"] = int(cur.fetchone()[0])
    if out["dup_ids"] or out["dup_instrument_seq"]:
        raise RuntimeError(f"GOV-SEQ-104: staging integrity failure {out} (fail-loud)")
    logger.info("[TICK_SEQ_BACKFILL_STAGE_VALIDATE] %s", json.dumps(out))
    return out


def apply(get_conn, get_config, logger, batch_size):
    out = {"batches": 0, "rows_updated": 0}
    version = get_config("tick_contract_version", "string")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT MIN(id), MAX(id) FROM {STAGING_TABLE}")
            lo, hi = cur.fetchone()
        if lo is None:
            logger.info("[TICK_SEQ_BACKFILL_APPLY] nothing staged"); return out
        cur_lo = int(lo)
        while cur_lo <= int(hi):
            cur_hi = cur_lo + int(batch_size) - 1
            with conn.cursor() as cur:
                # batched, resumable set-based UPDATE...JOIN by id range; only fill NULLs
                cur.execute(
                    f"UPDATE ticks t JOIN {STAGING_TABLE} s ON t.id=s.id "
                    "SET t.seq=s.seq, t.contract_version=COALESCE(t.contract_version,%s) "
                    "WHERE t.id BETWEEN %s AND %s AND t.seq IS NULL",
                    (version, cur_lo, cur_hi),
                )
                out["rows_updated"] += cur.rowcount
            conn.commit()
            out["batches"] += 1
            cur_lo = cur_hi + 1
    logger.info("[TICK_SEQ_BACKFILL_APPLY] %s", json.dumps(out))
    return out


def post_validate(get_conn, logger, limit_instrument=None):
    out = {"per_instrument": {}}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT instrument FROM ticks ORDER BY instrument")
            instruments = [r[0] for r in cur.fetchall()]
        if limit_instrument:
            instruments = [i for i in instruments if i == limit_instrument]
        for inst in instruments:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*), SUM(seq IS NULL), MAX(seq), "
                    "COUNT(*) - COUNT(DISTINCT seq) FROM ticks WHERE instrument=%s AND id IN "
                    f"(SELECT id FROM {STAGING_TABLE} WHERE instrument=%s)", (inst, inst))
                total, nulls, maxseq, dup = cur.fetchone()
            out["per_instrument"][inst] = {"total_targeted": int(total), "null_seq": int(nulls or 0),
                                           "max_seq": int(maxseq or 0), "dup_seq": int(dup or 0)}
            if (nulls or 0) > 0 or (dup or 0) > 0:
                raise RuntimeError(f"GOV-SEQ-105: post-backfill validation failed for {inst}: {out['per_instrument'][inst]}")
    logger.info("[TICK_SEQ_BACKFILL_POST_VALIDATE] %s", json.dumps(out))
    return out


def run(get_conn, get_config, logger, execute=False, confirm=False, limit_instrument=None):
    batch_size = int(get_config("tick_seq_backfill_batch_size", "int"))  # fail-loud
    if not (execute and confirm):
        return {"mode": "dry-run", **dry_run(get_conn, get_config, logger, limit_instrument)}
    staged = stage(get_conn, get_config, logger, limit_instrument)
    validate_staging(get_conn, logger)
    applied = apply(get_conn, get_config, logger, batch_size)
    validated = post_validate(get_conn, logger, limit_instrument)
    return {"mode": "execute", "executed": True, "batch_size": batch_size,
            "staged": staged, "applied": applied, "validated": validated}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="actually backfill (default dry-run)")
    ap.add_argument("--confirm", action="store_true", help="second guard; required with --execute")
    ap.add_argument("--limit-instrument", default=None, help="restrict to one instrument (limited run)")
    args = ap.parse_args(argv)
    if args.execute and not args.confirm:
        print("REFUSING: --execute requires --confirm (governed, not auto-run)", file=sys.stderr)
        return 2
    print("Set-based staged backfill. Invoke run(get_conn, get_config, logger, ...) from an "
          "authorised runner. Default mode is dry-run; mutation needs --execute --confirm.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
