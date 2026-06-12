"""Read-only Stage F gate check for HERMES tick sequencing.
WO-HELM-HERMES-TICK-GAP-SEMANTIC-AND-LEDGER-0001.

Stage F (full tick seq backfill) MUST NOT resume until the accepted/unrecoverable tick gaps
for the 2026-06-10/11 outage are recorded and the stored-row seq semantic is bound to them.
This module ONLY reports gate state. It performs NO mutation and DOES NOT run Stage F.

Gate is OPEN (Stage F may proceed) only when ALL checks pass:
  1. stored-row seq semantic is present (ledger records carry the semantic_version);
  2. ACCEPTED/UNRECOVERABLE tick-gap records exist for the outage window, all 6 instruments;
  3. no tick-gap record for the window is OPEN or UNKNOWN (every window is governed);
  4. migration 017 UNIQUE(instrument,seq) is NOT yet present (it is a later stage);
  5. native writer inactive  (proxy: tick_stream_enabled=false);
  6. Redis tick stream inactive (proxy: tick_stream_enabled=false);
  7. retention inactive (proxy: tick_archive_enabled=false).

conn injected for testability; evaluate_gate() is pure.
"""
from __future__ import annotations
import json
import sys

SEMANTIC_VERSION = "hermes.tick.seq.stored_row.v1"
INSTRUMENTS = {"AUD_USD", "EUR_USD", "USD_JPY", "WTICO_USD", "XAG_USD", "XAU_USD"}
LEDGER = "hermes_tick_gap_ledger"
WINDOW_START = "2026-06-10 00:00:00"
WINDOW_END = "2026-06-11 23:59:59"


# ---------- pure, unit-testable ----------
def evaluate_gate(state: dict) -> dict:
    """state keys: semantic_versions(set), accepted_instruments(set), open_or_unknown_count(int),
       uq_017_present(bool), tick_stream_enabled(str), tick_archive_enabled(str)."""
    checks = {
        "semantic_present": SEMANTIC_VERSION in set(state.get("semantic_versions") or []),
        "accepted_all_instruments": INSTRUMENTS.issubset(set(state.get("accepted_instruments") or [])),
        "no_open_or_unknown": int(state.get("open_or_unknown_count", 1)) == 0,
        "mig_017_absent": not bool(state.get("uq_017_present", True)),
        "native_writer_inactive": str(state.get("tick_stream_enabled", "true")).lower() == "false",
        "redis_stream_inactive": str(state.get("tick_stream_enabled", "true")).lower() == "false",
        "retention_inactive": str(state.get("tick_archive_enabled", "true")).lower() == "false",
    }
    gate_open = all(checks.values())
    missing = [k for k, v in checks.items() if not v]
    return {"gate_open": gate_open, "checks": checks, "blocking": missing}


# ---------- read-only collection (injected conn) ----------
def collect_state(get_conn):
    state = {}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT DISTINCT semantic_version FROM {LEDGER} "
                "WHERE status='ACCEPTED' AND recoverability='UNRECOVERABLE'")
            state["semantic_versions"] = [r[0] for r in cur.fetchall() if r[0]]
            cur.execute(
                f"SELECT DISTINCT instrument FROM {LEDGER} WHERE status='ACCEPTED' "
                "AND recoverability='UNRECOVERABLE' AND gap_start_utc>=%s AND gap_end_utc<=%s",
                (WINDOW_START, WINDOW_END))
            state["accepted_instruments"] = [r[0] for r in cur.fetchall()]
            cur.execute(
                f"SELECT COUNT(*) FROM {LEDGER} "
                "WHERE (status='OPEN' OR recoverability='UNKNOWN') "
                "AND gap_start_utc>=%s AND gap_end_utc<=%s", (WINDOW_START, WINDOW_END))
            state["open_or_unknown_count"] = int(cur.fetchone()[0])
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema=DATABASE() "
                "AND table_name='ticks' AND INDEX_NAME='uq_ticks_instrument_seq'")
            state["uq_017_present"] = int(cur.fetchone()[0]) > 0
            cur.execute("SELECT config_key, config_value FROM hermes_config "
                        "WHERE config_key IN ('tick_stream_enabled','tick_archive_enabled')")
            cfg = {k: v for k, v in cur.fetchall()}
            state["tick_stream_enabled"] = cfg.get("tick_stream_enabled", "true")
            state["tick_archive_enabled"] = cfg.get("tick_archive_enabled", "true")
    return state


def check(get_conn, logger):
    result = evaluate_gate(collect_state(get_conn))
    logger.info("[STAGE_F_GATE] %s", json.dumps(result))
    return result


def main(argv=None):
    print("Read-only Stage F gate. Invoke check(get_conn, logger) from an authorised runner. "
          "Reports gate state only; never runs Stage F.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
