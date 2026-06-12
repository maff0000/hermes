"""Idempotent historical backfill for durable candles_M30 + candles_H4 from canonical M1.
WO-HELM-HERMES-CANDLE-H4-M30-PIPELINE-BUILD-0001.

Design (mirrors the governed backfill pattern):
  - DRY-RUN by default; mutation requires --execute AND --confirm.
  - Derivation is delegated to utils.m1_deriver.M1DerivationEngine, which now registers
    M30 (1800s) and H4 (14400s) in TIMEFRAME_SECONDS and buckets them UTC-aligned
    (epoch-truncated): M30 -> :00/:30, H4 -> 00/04/08/12/16/20 UTC.
  - Aggregation is deterministic: open=first M1 open, high=max, low=min, close=last,
    volume=sum of constituent M1 volumes. complete=1 ONLY when all expected M1 present
    (M30=30, H4=240). A partial window (e.g. the 2026-06-10/11 outage) is recorded as
    complete=0 and surfaced in evidence — never silently published as complete.
  - Idempotent upsert on UNIQUE(instrument,timestamp): re-running is safe (deterministic
    values). A COMPLETE derivation refreshes the row; a PARTIAL derivation NEVER downgrades
    or clobbers an existing complete candle (no overwrite of valid data).
  - The still-forming current bucket is excluded (end is truncated to the last fully-closed
    bucket) so live aggregation is never raced.
  - This WO ships the script only — it is NOT executed against the live DB (Phase = PR).
  conn/engine injected for testability.

ALL timestamps UTC.
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import timedelta

sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])
from utils.m1_deriver import TIMEFRAME_SECONDS, get_bucket_start  # noqa: E402

TARGET_TABLE = {"M30": "candles_M30", "H4": "candles_H4"}
TIMEFRAMES = ["M30", "H4"]
SOURCE = "m1_derive_backfill"


# ---------- pure, unit-testable ----------
def expected_m1(timeframe: str) -> int:
    """Number of constituent M1 candles for one bucket of `timeframe`."""
    return TIMEFRAME_SECONDS[timeframe] // 60


def aggregate_ohlcv(rows):
    """rows: ordered list of dicts with open/high/low/close/volume (M1, ascending time).
       Returns (open, high, low, close, volume). Fail loud on empty."""
    if not rows:
        raise ValueError("GOV-CANDLE-001: cannot aggregate empty M1 set (fail-loud)")
    return (
        float(rows[0]["open"]),
        max(float(r["high"]) for r in rows),
        min(float(r["low"]) for r in rows),
        float(rows[-1]["close"]),
        sum(int(r["volume"]) for r in rows),
    )


def is_complete(m1_count: int, timeframe: str) -> bool:
    return m1_count == expected_m1(timeframe)


def last_closed_bucket_end(now_utc, timeframe: str):
    """Exclusive end = start of the current (still-forming) bucket — never backfill it."""
    return get_bucket_start(now_utc, TIMEFRAME_SECONDS[timeframe])


# ---------- SQL upsert (idempotent; never downgrades complete) ----------
UPSERT = (
    "INSERT INTO {table} (instrument, timestamp, open, high, low, close, volume, complete, source) "
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
    "ON DUPLICATE KEY UPDATE "
    "  open=IF(VALUES(complete)=1, VALUES(open), open), "
    "  high=IF(VALUES(complete)=1, VALUES(high), high), "
    "  low=IF(VALUES(complete)=1, VALUES(low), low), "
    "  close=IF(VALUES(complete)=1, VALUES(close), close), "
    "  volume=IF(VALUES(complete)=1, VALUES(volume), volume), "
    "  complete=GREATEST(complete, VALUES(complete))"
)


def backfill_timeframe(engine, get_conn, logger, instrument, timeframe, start_utc, now_utc,
                       execute=False, confirm=False):
    """Derive + (optionally) upsert one instrument/timeframe. Returns a summary dict."""
    end_utc = last_closed_bucket_end(now_utc, timeframe)
    candles = engine.derive_range(instrument, timeframe, start_utc, end_utc)
    complete = [c for c in candles if c.complete]
    partial = [c for c in candles if not c.complete]
    summary = {"instrument": instrument, "timeframe": timeframe,
               "derived": len(candles), "complete": len(complete), "partial": len(partial),
               "partial_buckets": [c.timestamp.isoformat() for c in partial[:20]],
               "written": 0, "mode": "dry-run"}
    if not (execute and confirm):
        logger.info("[CANDLE_BACKFILL_DRYRUN] %s", json.dumps(summary))
        return summary
    summary["mode"] = "execute"
    table = TARGET_TABLE[timeframe]
    with get_conn() as conn:
        with conn.cursor() as cur:
            for c in candles:  # write complete=1 refresh + complete=0 (won't clobber existing complete)
                cur.execute(UPSERT.format(table=table),
                            (c.instrument, c.timestamp, c.open, c.high, c.low, c.close,
                             c.volume, 1 if c.complete else 0, SOURCE))
                summary["written"] += 1
        conn.commit()
    logger.info("[CANDLE_BACKFILL] %s", json.dumps(summary))
    return summary


def run(engine, get_conn, logger, instruments, start_utc, now_utc,
        timeframes=None, execute=False, confirm=False):
    out = {"timeframes": timeframes or TIMEFRAMES, "results": []}
    for inst in instruments:
        for tf in (timeframes or TIMEFRAMES):
            out["results"].append(
                backfill_timeframe(engine, get_conn, logger, inst, tf, start_utc, now_utc,
                                   execute=execute, confirm=confirm))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="actually upsert (default dry-run)")
    ap.add_argument("--confirm", action="store_true", help="second guard; required with --execute")
    ap.add_argument("--timeframe", action="append", choices=["M30", "H4"], default=None)
    args = ap.parse_args(argv)
    if args.execute and not args.confirm:
        print("REFUSING: --execute requires --confirm (governed, not auto-run)", file=sys.stderr)
        return 2
    print("Durable M30/H4 candle backfill. Invoke run(engine, get_conn, logger, ...) from an "
          "authorised runner. Default mode is dry-run; mutation needs --execute --confirm. "
          "Derivation is UTC-aligned; partial windows are recorded complete=0, never silently completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
