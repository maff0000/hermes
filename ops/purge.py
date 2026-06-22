#!/usr/bin/env python3
"""HERMES-OPS-PURGE-001 — production-only, out-of-band database purge engine.

STANDALONE OPS SCRIPT. This module is deliberately NOT imported, initialised, or invoked by the
primary execution runtime (`main.py`) or anything on the hot-path market-data loop. It runs only as
`python3 ops/purge.py` (cron / manual / ops job), entirely out-of-band.

Doctrine honoured (HERMES):
  * Environment-blind + ringfenced — DEV/STAGING are NEVER purged (local ledger preserved). The only
    environment that can delete is RUN_ENV=PRODUCTION, and only with an explicit second toggle.
  * Fail-loud — production-without-explicit-enable refuses to run (GOV-PURGE-001) and exits non-zero.
  * No hidden destructive defaults — retention + target tables MUST be supplied explicitly; an
    unconfigured engine deletes nothing.
  * Non-blocking — deletes run in small, sliding-window chunks with a configurable micro-sleep so
    table locks never starve / latency-spike the concurrent hot-path market-data tables.
  * UTC everywhere — the retention cutoff is computed in UTC.
  * Telemetry out-of-band — PURGE_CYCLE_START / per-batch progress / PURGE_CYCLE_COMPLETE ship via
    GELF to the Graylog SIEM endpoint (SIEM_GRAYLOG_IP / legacy GRAYLOG_HOST).

NOTE: deletes outside PLUTUS are a guarded operation. This script is the *mechanism*; actually
running it against production is a separately-authorised act, gated here by RUN_ENV + HERMES_PURGE_ENABLED.
"""
from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

# Run as `python3 ops/purge.py`: ensure repo-root modules (hermes_logging, env_config) import
# regardless of CWD — otherwise sys.path[0] is ops/ and the telemetry/DB imports fail.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Reason codes
GOV_PURGE_DISABLED = "GOV-PURGE-001"       # prod but explicit enable toggle missing
GOV_PURGE_CONFIG = "GOV-PURGE-002"         # required config missing / invalid
GOV_PURGE_DB = "GOV-PURGE-003"             # DB connection / execution failure
GOV_PURGE_TABLE_DENY = "GOV-PURGE-004"     # target table outside the hardcoded allowlist

# Hardcoded destination allowlist — the engine may ONLY purge these canonical HERMES data tables. A
# config typo naming a critical downstream state table is rejected before any connection is opened.
# Deliberately narrow; extend ONLY with explicit owner sign-off (a wider blast radius needs authorisation).
ALLOWED_PURGE_TABLES = frozenset({"ticks", "candles_M5", "candles_H1"})

# Gate decisions
BYPASS = "BYPASS"     # non-production -> clean no-op exit 0
BLOCKED = "BLOCKED"   # production but not explicitly enabled -> fail-loud exit 1
PROCEED = "PROCEED"   # production + explicitly enabled -> run

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")  # SQL identifier allowlist (no injection from env)


def build_delete_sql(table, ts_col):
    """Single source of truth for the chunked-delete SQL. Used by BOTH the pure purge_table() and the
    live _real_purge_table() so the tested string and the destructive string can never drift apart."""
    return f"DELETE FROM `{table}` WHERE `{ts_col}` < %s ORDER BY `{ts_col}` ASC LIMIT %s"


# ----------------------------------------------------------------------------- pure helpers --------
def decide_gate(environ) -> tuple[str, str]:
    """Pure gate decision from an environment mapping. Returns (action, message).

    Order matters and mirrors the WO directive:
      1. RUN_ENV != PRODUCTION                  -> BYPASS  (informative stdout, exit 0)
      2. PRODUCTION but HERMES_PURGE_ENABLED!=TRUE -> BLOCKED (fail-loud GOV-PURGE-001, exit 1)
      3. PRODUCTION + HERMES_PURGE_ENABLED==TRUE   -> PROCEED
    """
    run_env = (environ.get("RUN_ENV") or "").strip().upper()
    if run_env != "PRODUCTION":
        return BYPASS, (f"[PURGE_BYPASS] RUN_ENV={environ.get('RUN_ENV', '<unset>')!r} != 'PRODUCTION' "
                        "— purge skipped, development/staging ledger preserved.")
    # Strict, case-sensitive: the destructive enable toggle must be exactly "TRUE" (whitespace-stripped).
    # No lenient "true"/"1"/"yes" — an ambiguous value must fail closed, not delete.
    enabled = (environ.get("HERMES_PURGE_ENABLED") or "").strip()
    if enabled != "TRUE":
        return BLOCKED, (f"{GOV_PURGE_DISABLED}: RUN_ENV=PRODUCTION but HERMES_PURGE_ENABLED!='TRUE' "
                         f"(got {environ.get('HERMES_PURGE_ENABLED', '<unset>')!r}) — refusing to purge (fail-loud).")
    return PROCEED, "production purge gate open"


def parse_table_specs(spec: str):
    """Parse 'table:ts_col,table2:ts_col2' into a validated list of (table, ts_column).

    Identifiers are allowlist-validated (no SQL injection from env-supplied config). Raises ValueError.
    """
    if not spec or not spec.strip():
        raise ValueError(f"{GOV_PURGE_CONFIG}: PURGE_TABLES is required (no default) — nothing to purge.")
    out = []
    for raw in spec.split(","):
        item = raw.strip()
        if not item:
            continue
        if ":" not in item:
            raise ValueError(f"{GOV_PURGE_CONFIG}: bad PURGE_TABLES entry {item!r} (expected 'table:timestamp_column').")
        table, ts_col = (p.strip() for p in item.split(":", 1))
        if not _IDENT_RE.match(table) or not _IDENT_RE.match(ts_col):
            raise ValueError(f"{GOV_PURGE_CONFIG}: illegal identifier in {item!r} — refusing (fail-loud).")
        if table not in ALLOWED_PURGE_TABLES:
            raise ValueError(f"{GOV_PURGE_TABLE_DENY}: table {table!r} is not in the purge allowlist "
                             f"{sorted(ALLOWED_PURGE_TABLES)} — refusing (fail-loud). A typo or a critical "
                             "downstream state table will NEVER be purged.")
        out.append((table, ts_col))
    if not out:
        raise ValueError(f"{GOV_PURGE_CONFIG}: PURGE_TABLES parsed to empty — nothing to purge.")
    return out


def compute_cutoff_utc(now_utc: datetime, retention_days: int) -> str:
    """Retention boundary in UTC. Rows strictly older than this are eligible for deletion."""
    if now_utc.tzinfo is None:
        raise ValueError(f"{GOV_PURGE_CONFIG}: now_utc must be timezone-aware UTC (fail-loud).")
    cutoff = now_utc.astimezone(timezone.utc) - timedelta(days=retention_days)
    return cutoff.strftime("%Y-%m-%d %H:%M:%S")


def load_config(environ):
    """Load + validate purge config from env. Fail-loud on missing/invalid required values."""
    def _req_int(name, minimum):
        val = environ.get(name)
        if val is None or str(val).strip() == "":
            raise ValueError(f"{GOV_PURGE_CONFIG}: {name} is required (no default) — fail-loud.")
        try:
            n = int(val)
        except (TypeError, ValueError):
            raise ValueError(f"{GOV_PURGE_CONFIG}: {name}={val!r} is not an integer — fail-loud.")
        if n < minimum:
            raise ValueError(f"{GOV_PURGE_CONFIG}: {name}={n} below minimum {minimum} — fail-loud.")
        return n

    def _opt_int(name, default, minimum):
        val = environ.get(name)
        if val is None or str(val).strip() == "":
            return default
        n = int(val)
        if n < minimum:
            raise ValueError(f"{GOV_PURGE_CONFIG}: {name}={n} below minimum {minimum} — fail-loud.")
        return n

    return {
        "retention_days": _req_int("PURGE_RETENTION_DAYS", 1),
        "tables": parse_table_specs(environ.get("PURGE_TABLES", "")),
        "batch_size": _opt_int("PURGE_BATCH_SIZE", 5000, 1),
        # Sleep FLOOR is 1ms (not 0): a non-zero micro-sleep structurally forces a context-switch/yield
        # between chunks so the hot-path market-data writers are never lock-starved. 0 -> fail-loud.
        "batch_sleep_ms": _opt_int("PURGE_BATCH_SLEEP_MS", 200, 1),
        # Safety backstop against a runaway loop; 0 = unlimited (still chunked + sleeping).
        "max_batches_per_table": _opt_int("PURGE_MAX_BATCHES_PER_TABLE", 100000, 0),
    }


def purge_table(execute_fn, table, ts_col, cutoff_utc, batch_size, sleep_fn, max_batches, emit_fn):
    """Chunked, sliding-window delete of one table. Pure of I/O specifics — all effects are injected.

    execute_fn(sql, params) -> affected_rowcount ; sleep_fn() called between batches ; emit_fn(event, **fields).
    Deletes the OLDEST rows first in LIMIT-bounded batches, committing per batch (via execute_fn), with
    a micro-sleep between batches so concurrent hot-path writers are never lock-starved. The sleep runs
    ONLY between batches (never after the final batch), so a single drained batch sleeps zero times.
    """
    sql = build_delete_sql(table, ts_col)  # shared generator — no drift vs tests
    total = 0
    batches = 0
    capped = False
    while True:
        affected = execute_fn(sql, (cutoff_utc, batch_size))
        batches += 1
        total += affected
        emit_fn("PURGE_BATCH", table=table, batch=batches, rows=affected, cumulative=total,
                cutoff_utc=cutoff_utc)
        if affected < batch_size:
            break  # drained the eligible rows for this table
        if max_batches and batches >= max_batches:
            capped = True
            emit_fn("PURGE_BATCH_CAP", table=table, batches=batches, cumulative=total,
                    note="max_batches_per_table reached — stopping this table (rerun to continue)")
            break
        sleep_fn()  # non-blocking micro-sleep between chunks: yield locks to hot-path writers
    return {"table": table, "rows": total, "batches": batches, "capped": capped}


# ----------------------------------------------------------------------------- runtime wiring ------
def _build_logger():
    """Telemetry logger -> GELF/Graylog SIEM. Falls back to console-only if SIEM is unconfigured so a
    fail-loud (GOV-PURGE-001) still surfaces rather than being swallowed by a logger-init crash."""
    from hermes_logging import get_logger
    try:
        from hermes_logging import resolve_gelf_target
        host, port = resolve_gelf_target()
        return get_logger(service="hermes-purge", enable_gelf=True, gelf_host=host, gelf_port=port)
    except Exception as exc:  # SIEM not configured — degrade to console, but keep running fail-loud path
        log = get_logger(service="hermes-purge", enable_gelf=False)
        log.warning("GELF/SIEM target unresolved (%r) — telemetry console-only for this run.", exc)
        return log


def _emit(logger):
    """Return an emit_fn(event, **fields) that ships a structured line to the SIEM."""
    def emit(event, **fields):
        kv = " ".join(f"{k}={v}" for k, v in fields.items())
        logger.info("%s %s", event, kv)
    return emit


def _real_purge_table(cursor, conn, table, ts_col, cutoff_utc, cfg, emit):
    """Bridge the pure purge_table() to a live pymysql cursor/connection with real per-batch commit
    and the configured micro-sleep between chunks."""
    sql = build_delete_sql(table, ts_col)  # shared generator — no drift vs tests
    sleep_s = cfg["batch_sleep_ms"] / 1000.0
    total = batches = 0
    capped = False
    while True:
        affected = cursor.execute(sql, (cutoff_utc, cfg["batch_size"]))
        conn.commit()
        batches += 1
        total += affected
        emit("PURGE_BATCH", table=table, batch=batches, rows=affected, cumulative=total,
             cutoff_utc=cutoff_utc)
        if affected < cfg["batch_size"]:
            break
        if cfg["max_batches_per_table"] and batches >= cfg["max_batches_per_table"]:
            capped = True
            emit("PURGE_BATCH_CAP", table=table, batches=batches, cumulative=total,
                 note="max_batches_per_table reached")
            break
        time.sleep(sleep_s)  # non-blocking micro-sleep: yield locks to hot-path writers
    return {"table": table, "rows": total, "batches": batches, "capped": capped}


def main(argv=None) -> int:
    environ = os.environ
    action, message = decide_gate(environ)

    # --- Gate 1: environment-blind bypass (DEV/STAGING never purge) ---
    if action == BYPASS:
        print(message)            # informative bypass message to stdout, per directive
        return 0

    logger = _build_logger()

    # --- Gate 2: production-but-not-explicitly-enabled -> fail-loud ---
    if action == BLOCKED:
        logger.error(message)     # GOV-PURGE-001 via the telemetry layer
        return 1

    # --- PROCEED: production + explicitly enabled ---
    emit = _emit(logger)
    try:
        cfg = load_config(environ)
    except ValueError as exc:
        logger.error("%s", exc)   # GOV-PURGE-002
        return 1

    now_utc = datetime.now(timezone.utc)
    cutoff_utc = compute_cutoff_utc(now_utc, cfg["retention_days"])

    try:
        import pymysql
        from env_config import get_db_config
        db = get_db_config()
        conn = pymysql.connect(host=db["host"], port=db["port"], user=db["user"],
                               password=db["password"], database=db["database"])
    except Exception as exc:
        logger.error("%s: DB connect failed: %r — aborting (fail-loud).", GOV_PURGE_DB, exc)
        return 1

    emit("PURGE_CYCLE_START", retention_days=cfg["retention_days"], cutoff_utc=cutoff_utc,
         batch_size=cfg["batch_size"], batch_sleep_ms=cfg["batch_sleep_ms"],
         tables=";".join(t for t, _ in cfg["tables"]))
    started = time.time()
    results = []
    rc = 0
    try:
        cursor = conn.cursor()
        for table, ts_col in cfg["tables"]:
            try:
                results.append(_real_purge_table(cursor, conn, table, ts_col, cutoff_utc, cfg, emit))
            except Exception as exc:
                rc = 1
                logger.error("%s: purge of `%s` failed: %r — continuing to next table.",
                             GOV_PURGE_DB, table, exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    grand_total = sum(r["rows"] for r in results)
    emit("PURGE_CYCLE_COMPLETE", tables_done=len(results), rows_deleted=grand_total,
         duration_s=round(time.time() - started, 3), partial_failure=(rc == 1),
         detail=";".join(f"{r['table']}:{r['rows']}b{r['batches']}{'CAP' if r['capped'] else ''}" for r in results))
    return rc


if __name__ == "__main__":
    sys.exit(main())
