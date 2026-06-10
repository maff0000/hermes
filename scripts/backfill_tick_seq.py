"""Governed backfill: assign HERMES-owned per-instrument monotonic seq to the
existing ~22M rows of tradingSignals.ticks (WO-HELM-HERMES-TICK-CONTRACT-PUBLISH-0001).

Properties (per Architect D-SEQ/D-SCHEMA):
  - per-instrument monotonic; deterministic ORDER BY (timestamp, id);
  - resumable (resumes from MAX(seq) per instrument; only fills NULL seq rows);
  - batched (tick_seq_backfill_batch_size from governed config);
  - fail-loud (no silent skips; aborts on anomaly);
  - evidence-producing (writes a JSON summary);
  - NOT auto-run: requires explicit --execute AND --confirm; default is dry-run.

This WO ships the script only. It is NOT executed against the live DB here.
"""
from __future__ import annotations
import argparse
import json
import sys


def _stamp_version_marker():
    return "hermes.tick.v1"


def run(get_conn, get_config, execute: bool, confirm: bool, logger):
    batch = int(get_config("tick_seq_backfill_batch_size", "int"))  # fail-loud if missing
    version = get_config("tick_contract_version", "string")
    summary = {"batch_size": batch, "contract_version": version, "executed": False,
               "per_instrument": {}, "mode": "execute" if (execute and confirm) else "dry-run"}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT instrument FROM ticks ORDER BY instrument")
            instruments = [r[0] for r in cur.fetchall()]
        for inst in instruments:
            with conn.cursor() as cur:
                cur.execute("SELECT COALESCE(MAX(seq),0), COUNT(*) FROM ticks WHERE instrument=%s", (inst,))
                max_seq, total = cur.fetchone()
                cur.execute("SELECT COUNT(*) FROM ticks WHERE instrument=%s AND seq IS NULL", (inst,))
                pending = cur.fetchone()[0]
            summary["per_instrument"][inst] = {"max_seq": int(max_seq), "total": int(total), "pending_null_seq": int(pending)}
            if not (execute and confirm):
                continue
            # deterministic, resumable fill of NULL seq rows in (timestamp,id) order
            next_seq = int(max_seq) + 1
            while True:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM ticks WHERE instrument=%s AND seq IS NULL "
                        "ORDER BY timestamp, id LIMIT %s", (inst, batch))
                    ids = [r[0] for r in cur.fetchall()]
                if not ids:
                    break
                with conn.cursor() as cur:
                    for _id in ids:
                        cur.execute("UPDATE ticks SET seq=%s, contract_version=COALESCE(contract_version,%s) "
                                    "WHERE id=%s AND seq IS NULL", (next_seq, version, _id))
                        if cur.rowcount != 1:
                            raise RuntimeError(f"GOV-SEQ-002: backfill race/anomaly on id={_id} (fail-loud)")
                        next_seq += 1
                conn.commit()
            summary["per_instrument"][inst]["assigned_through_seq"] = next_seq - 1
        if execute and confirm:
            summary["executed"] = True
    logger.info("[TICK_SEQ_BACKFILL_SUMMARY] %s", json.dumps(summary))
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="actually write seq (default dry-run)")
    ap.add_argument("--confirm", action="store_true", help="second guard; required with --execute")
    args = ap.parse_args(argv)
    if args.execute and not args.confirm:
        print("REFUSING: --execute requires --confirm (governed, not auto-run)", file=sys.stderr)
        return 2
    print("This script does not self-wire DB/config. Invoke run(get_conn, get_config, ...) "
          "from an authorised runner. Default mode is dry-run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
