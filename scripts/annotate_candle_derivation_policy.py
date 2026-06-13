"""Governed historical derivation-policy annotation for candles_M30 / candles_H4.
WO-HELM-HERMES-CANDLE-DERIVATION-POLICY-ANNOTATION-0001.

Marks EXISTING (pre-strict) M30/H4 rows with their HISTORICAL policy so historical vs forward
`complete=1` semantics are machine-distinguishable (R2D2 B-DIV). It sets ONLY the policy columns
on rows where derivation_policy IS NULL — it does NOT touch open/high/low/close/volume/complete/
timestamp/id. DRY-RUN by default; mutation requires --execute AND --confirm.

This WO ships the runner only — NOT executed live here. Requires migration 023 columns to exist;
fails loud otherwise. conn injected for testability. ALL timestamps UTC.
"""
from __future__ import annotations
import argparse
import json
import sys

HISTORICAL = {
    "derivation_policy": "HISTORICAL_ALL_M1_COUNTED",
    "source_complete_policy": "ALL_M1_COUNTED",
    "source_policy_epoch": "PHASE2_BACKFILL_PRE_STRICT_COMPLETE_POLICY",
    "derivation_policy_note": ("Historical M30/H4 rows created by the Phase-2 backfill, which "
                               "counted ALL constituent M1 rows regardless of each M1's complete "
                               "flag. complete=1 here means count==expected over ALL M1, NOT strict "
                               "complete-M1-only. Forward rows use FORWARD_COMPLETE_M1_ONLY."),
}
TABLES = ("candles_M30", "candles_H4")
# columns the migration 023 must provide before annotation/forward write is allowed
REQUIRED_COLUMNS = ("derivation_policy", "source_complete_policy", "source_policy_epoch",
                    "derivation_policy_note")


def _assert_policy_columns(get_conn, table):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COLUMN_NAME FROM information_schema.columns "
                        "WHERE table_schema=DATABASE() AND table_name=%s", (table,))
            cols = {r[0] for r in cur.fetchall()}
    missing = [c for c in REQUIRED_COLUMNS if c not in cols]
    if missing:
        raise RuntimeError(f"GOV-ANNOT-001: {table} missing policy columns {missing} "
                           "(apply migration 023 first) (fail-loud)")


def count_unannotated(get_conn, table):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE derivation_policy IS NULL")
            return int(cur.fetchone()[0])


# annotation touches ONLY policy columns; never OHLC/complete/timestamp/id
ANNOTATE_SQL = (
    "UPDATE {table} SET derivation_policy=%s, source_complete_policy=%s, source_policy_epoch=%s, "
    "derivation_policy_note=%s, derivation_generated_at_utc=UTC_TIMESTAMP(3) "
    "WHERE derivation_policy IS NULL"
)


def run(get_conn, logger, execute=False, confirm=False):
    summary = {"mode": "dry-run", "tables": {}}
    for t in TABLES:
        _assert_policy_columns(get_conn, t)
        summary["tables"][t] = {"would_annotate": count_unannotated(get_conn, t), "annotated": 0}
    if not (execute and confirm):
        logger.info("[CANDLE_POLICY_ANNOTATE_DRYRUN] %s", json.dumps(summary))
        return summary
    summary["mode"] = "execute"
    with get_conn() as conn:
        for t in TABLES:
            with conn.cursor() as cur:
                cur.execute(ANNOTATE_SQL.format(table=t),
                            (HISTORICAL["derivation_policy"], HISTORICAL["source_complete_policy"],
                             HISTORICAL["source_policy_epoch"], HISTORICAL["derivation_policy_note"]))
                summary["tables"][t]["annotated"] = cur.rowcount
        conn.commit()
    logger.info("[CANDLE_POLICY_ANNOTATE] %s", json.dumps(summary))
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--confirm", action="store_true")
    args = ap.parse_args(argv)
    if args.execute and not args.confirm:
        print("REFUSING: --execute requires --confirm (governed, not auto-run)", file=sys.stderr)
        return 2
    print("Historical candle derivation-policy annotation. Dry-run default; mutation needs "
          "--execute --confirm. Sets ONLY policy columns where derivation_policy IS NULL; never "
          "touches OHLC/complete/timestamp. Requires migration 023 columns (fail-loud).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
