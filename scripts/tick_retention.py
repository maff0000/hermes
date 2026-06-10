"""HERMES-owned tick retention/archive (skeleton)
(WO-HELM-HERMES-TICK-CONTRACT-PUBLISH-0001).

D-RET: retention ownership moves WITH write ownership. It is FORBIDDEN for
HERMES to write tradingSignals.ticks while structure_engine.retention purges it
(split-brain). At cutover the structure_engine retention must be retired and
this HERMES-owned job becomes authoritative.

Behaviour (governed config; fail-loud if missing):
  - warm retention: rows older than tick_retention_warm_days are eligible;
  - if tick_archive_enabled: cold-archive eligible rows to tick_archive_path
    (parquet) BEFORE purge; never purge un-archived rows when archive enabled;
  - no ad-hoc deletes: purge only via this governed job, guarded by --execute
    AND --confirm; default dry-run; NOT auto-run.

This WO ships the skeleton only. It does NOT run against the live DB.
"""
from __future__ import annotations
import argparse
import json
import sys


def plan(get_conn, get_config, logger):
    warm_days = int(get_config("tick_retention_warm_days", "int"))     # fail-loud
    archive_enabled = bool(get_config("tick_archive_enabled", "bool"))
    archive_path = get_config("tick_archive_path", "string")
    out = {"warm_days": warm_days, "archive_enabled": archive_enabled,
           "archive_path": archive_path, "eligible_rows": {}, "executed": False}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT instrument, COUNT(*) FROM ticks "
                "WHERE timestamp < NOW() - INTERVAL %s DAY GROUP BY instrument", (warm_days,))
            for inst, n in cur.fetchall():
                out["eligible_rows"][inst] = int(n)
    logger.info("[TICK_RETENTION_PLAN] %s", json.dumps(out))
    return out


def execute(get_conn, get_config, confirm: bool, logger):
    if not confirm:
        raise RuntimeError("GOV-RET-001: retention execute requires explicit confirm (fail-loud)")
    archive_enabled = bool(get_config("tick_archive_enabled", "bool"))
    if archive_enabled:
        # Cold archive must succeed (parquet) BEFORE any purge. Implemented in a
        # later authorised WO; refuse to purge un-archived rows here.
        raise NotImplementedError("GOV-RET-002: cold archive path not yet implemented; refusing to purge")
    raise NotImplementedError("GOV-RET-003: destructive purge deferred to a separately authorised WO")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--confirm", action="store_true")
    args = ap.parse_args(argv)
    if args.execute and not args.confirm:
        print("REFUSING: --execute requires --confirm (governed, not auto-run)", file=sys.stderr)
        return 2
    print("HERMES tick retention skeleton. plan()/execute() require an authorised runner. "
          "Destructive purge is deferred to a separately authorised WO.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
