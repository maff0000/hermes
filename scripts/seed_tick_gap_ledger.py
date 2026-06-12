"""Seed the HERMES tick-gap ledger with the 2026-06-10/11 ACCEPTED/UNRECOVERABLE outage gaps.
WO-HELM-HERMES-TICK-GAP-SEMANTIC-AND-LEDGER-0001.

Design (mirrors scripts/backfill_tick_seq.py governance):
  - DRY-RUN by default; mutation requires --execute AND --confirm.
  - Gap windows are DERIVED FROM DATA (read-only LAG scan of tradingSignals.ticks over the
    outage window), never hand-typed — so no timestamps are invented.
  - Per-instrument: any inter-tick interval > threshold_seconds inside the outage window is
    recorded as one ACCEPTED / UNRECOVERABLE gap (raw ticks are unrecoverable: OANDA has no
    historical tick endpoint, no fallback/archive — see the outage gap assessment).
  - Idempotent: scan_hash = sha256(instrument|gap_start_iso|gap_end_iso)[:16]; an existing
    scan_hash is skipped (append-only, never UPSERT).
  - This WO ships the script only (Phase 1). Execution is Phase 2, after R2D2 audit +
    Architect apply authorisation. conn/config injected (get_conn) for testability.

ALL timestamps UTC.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys

# --- governed constants for the 2026-06-10/11 systemic outage seed ---
OUTAGE_WINDOW_START = "2026-06-10 00:00:00"
OUTAGE_WINDOW_END = "2026-06-11 23:59:59"
DEFAULT_THRESHOLD_SECONDS = 600  # systemic dropout floor; smaller chop excluded from acceptance seed
INSTRUMENTS = ["AUD_USD", "EUR_USD", "USD_JPY", "WTICO_USD", "XAG_USD", "XAU_USD"]

SEMANTIC_VERSION = "hermes.tick.seq.stored_row.v1"
ACCEPTED_POLICY = "STORED_ROW_SEQUENCE"
SOURCE_CAUSE = "POWER_OUTAGE_INGEST_DROPOUT"
DETECTION_METHOD = "TICK_INTERVAL_SCAN"
R2D2_FINDING_KEY_V1 = "r2d2:finding:hermes:power_outage_tick_gap:v1"
R2D2_FINDING_KEY_V2 = "r2d2:finding:hermes:power_outage_tick_gap:v2"
# Default provenance records BOTH findings (v1 raised the risk; v2 confirmed unrecoverable).
# The primary (governed) r2d2_finding_key is the LATEST (v2); the full chain is preserved in
# diagnostic_json.r2d2_findings. Default must NEVER be v1-only (R2D2 binding requirement).
DEFAULT_R2D2_FINDING_KEYS = [R2D2_FINDING_KEY_V1, R2D2_FINDING_KEY_V2]
R2D2_PROVENANCE_NOTE = ("v1 raised the outage-gap risk; v2 confirmed unrecoverable raw ticks "
                        "and accepted stored-row sequencing.")
ARCHITECT_RULING = (
    "Raw tick gaps 2026-06-10/11 ACCEPTED as UNRECOVERABLE under current governed HERMES "
    "sources. HERMES tick seq = per-instrument monotonic sequence over STORED tick rows; "
    "it is NOT an assertion of complete market-time tick coverage. AUD_USD Stage E seq "
    "1..1,882,675 does not need reset."
)
CREATED_BY = "WO-HELM-HERMES-TICK-GAP-SEMANTIC-AND-LEDGER-0001"
TABLE = "hermes_tick_gap_ledger"


# ---------- pure, unit-testable ----------
def scan_hash(instrument: str, gap_start_iso: str, gap_end_iso: str) -> str:
    raw = f"{instrument}|{gap_start_iso}|{gap_end_iso}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def build_gap_record(instrument, gap_start_iso, gap_end_iso, duration_seconds,
                     prev_id=None, next_id=None, finding_keys=None):
    """Build one ACCEPTED/UNRECOVERABLE ledger record (pure dict). Fail loud on bad input.

    Provenance (R2D2 binding): finding_keys defaults to [v1, v2]. The governed primary
    r2d2_finding_key is the LATEST key (v2); the full chain is preserved in
    diagnostic_json.r2d2_findings. Fail loud if the resolved chain is v1-only.
    """
    if duration_seconds is None or int(duration_seconds) <= 0:
        raise ValueError(f"GOV-TICKGAP-001: non-positive duration for {instrument} (fail-loud)")
    keys = list(finding_keys) if finding_keys else list(DEFAULT_R2D2_FINDING_KEYS)
    if keys == [R2D2_FINDING_KEY_V1]:
        raise ValueError("GOV-TICKGAP-002: v1-only provenance is not permitted; "
                         "reference v2 (or both v1+v2) (fail-loud)")
    primary = keys[-1]  # latest finding governs the primary key
    return {
        "instrument": instrument,
        "gap_start_utc": gap_start_iso,
        "gap_end_utc": gap_end_iso,
        "duration_seconds": int(duration_seconds),
        "status": "ACCEPTED",
        "recoverability": "UNRECOVERABLE",
        "source_cause": SOURCE_CAUSE,
        "detection_method": DETECTION_METHOD,
        "accepted_policy": ACCEPTED_POLICY,
        "semantic_version": SEMANTIC_VERSION,
        "r2d2_finding_key": primary,
        "architect_ruling": ARCHITECT_RULING,
        "notes": "Systemic ingest dropout; raw ticks unrecoverable (no OANDA tick history). "
                 "Candle/signal layer recoverable separately with provenance.",
        "diagnostic_json": json.dumps({"prev_tick_id": prev_id, "next_tick_id": next_id,
                                       "window": "2026-06-10/11", "all_instruments_synchronized": True,
                                       "r2d2_findings": keys, "provenance_note": R2D2_PROVENANCE_NOTE}),
        "scan_hash": scan_hash(instrument, gap_start_iso, gap_end_iso),
        "created_by": CREATED_BY,
    }


# ---------- read-only derivation (injected conn) ----------
def derive_gaps(get_conn, threshold_seconds=DEFAULT_THRESHOLD_SECONDS,
                window_start=OUTAGE_WINDOW_START, window_end=OUTAGE_WINDOW_END,
                instruments=None, finding_keys=None):
    """Read-only: derive per-instrument tick gaps > threshold inside the outage window."""
    instruments = instruments or INSTRUMENTS
    records = []
    with get_conn() as conn:
        for inst in instruments:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT prev_ts, ts, TIMESTAMPDIFF(SECOND, prev_ts, ts) dur, prev_id, id "
                    "FROM (SELECT id, timestamp ts, "
                    "        LAG(timestamp) OVER (ORDER BY timestamp, id) prev_ts, "
                    "        LAG(id) OVER (ORDER BY timestamp, id) prev_id "
                    "      FROM ticks WHERE instrument=%s AND timestamp>=%s AND timestamp<=%s) g "
                    "WHERE TIMESTAMPDIFF(SECOND, prev_ts, ts) > %s ORDER BY ts",
                    (inst, window_start, window_end, int(threshold_seconds)),
                )
                for prev_ts, ts, dur, prev_id, next_id in cur.fetchall():
                    records.append(build_gap_record(
                        inst, prev_ts.isoformat(sep=" "), ts.isoformat(sep=" "),
                        dur, prev_id, next_id, finding_keys=finding_keys))
    return records


def insert_records(get_conn, logger, records, execute=False, confirm=False):
    """DRY-RUN unless execute AND confirm. Idempotent append: skip existing scan_hash."""
    summary = {"mode": "dry-run", "candidate": len(records), "inserted": 0, "skipped_existing": 0,
               "per_instrument": {}}
    for r in records:
        summary["per_instrument"].setdefault(r["instrument"], 0)
        summary["per_instrument"][r["instrument"]] += 1
    if not (execute and confirm):
        logger.info("[TICK_GAP_LEDGER_SEED_DRYRUN] %s", json.dumps(summary))
        return summary
    summary["mode"] = "execute"
    with get_conn() as conn:
        for r in records:
            with conn.cursor() as cur:
                cur.execute(f"SELECT 1 FROM {TABLE} WHERE scan_hash=%s", (r["scan_hash"],))
                if cur.fetchone():
                    summary["skipped_existing"] += 1
                    continue
                cur.execute(
                    f"INSERT INTO {TABLE} (instrument, gap_start_utc, gap_end_utc, duration_seconds, "
                    "status, recoverability, source_cause, detection_method, accepted_policy, "
                    "semantic_version, r2d2_finding_key, architect_ruling, notes, diagnostic_json, "
                    "scan_hash, created_at_utc, created_by) VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(3),%s)",
                    (r["instrument"], r["gap_start_utc"], r["gap_end_utc"], r["duration_seconds"],
                     r["status"], r["recoverability"], r["source_cause"], r["detection_method"],
                     r["accepted_policy"], r["semantic_version"], r["r2d2_finding_key"],
                     r["architect_ruling"], r["notes"], r["diagnostic_json"], r["scan_hash"],
                     r["created_by"]))
                summary["inserted"] += 1
        conn.commit()
    logger.info("[TICK_GAP_LEDGER_SEED] %s", json.dumps(summary))
    return summary


def run(get_conn, logger, execute=False, confirm=False, threshold_seconds=DEFAULT_THRESHOLD_SECONDS,
        finding_keys=None):
    records = derive_gaps(get_conn, threshold_seconds=threshold_seconds, finding_keys=finding_keys)
    return insert_records(get_conn, logger, records, execute=execute, confirm=confirm)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="actually insert (default dry-run)")
    ap.add_argument("--confirm", action="store_true", help="second guard; required with --execute")
    ap.add_argument("--threshold-seconds", type=int, default=DEFAULT_THRESHOLD_SECONDS)
    ap.add_argument("--r2d2-finding-key", action="append", default=None, dest="finding_keys",
                    help="R2D2 finding key(s); repeatable. Default records both v1 and v2 "
                         "(primary = latest). v1-only is rejected fail-loud.")
    args = ap.parse_args(argv)
    if args.execute and not args.confirm:
        print("REFUSING: --execute requires --confirm (governed, not auto-run)", file=sys.stderr)
        return 2
    keys = args.finding_keys or DEFAULT_R2D2_FINDING_KEYS
    print("Tick-gap ledger seed. Invoke run(get_conn, logger, finding_keys=...) from an authorised "
          "runner. Default mode is dry-run; mutation needs --execute --confirm. "
          f"Provenance default: {keys} (primary={keys[-1]}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
