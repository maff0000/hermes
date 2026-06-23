#!/usr/bin/env python3
"""Automated outage backfill-recovery engine (boot-time).
WO-HERMES-AUTOMATED-BACKFILL-RECOVERY.

Runs AFTER the container BOOT-GATE clears and BEFORE the real-time threads spawn, so a container that
restarts after an outage mends the candle telemetry hole before processing new ticks. This is a thin,
guardrailed WRAPPER around the existing audited recovery machinery (legacy `scripts/backfill_oanda.py`
/ `utils/recovery_executor.py`, WO-HERMES-BACKFILL-ENGINE-0004) — it does NOT reimplement ingest.

Behaviour: fail-SILENT during normal ops (disabled / no gap -> clean exit 0); fail-LOUD during execution
(errors raise a flag + non-zero). Three immutable guardrails:
  1. Idempotency & re-entry isolation — compute Δt = now_utc - t_last (destination MAX timestamp) and
     ingest ONLY the missing interval; the legacy insert is ON DUPLICATE KEY UPDATE, so re-entry is safe.
  2. Time-bounded recovery — Δt > MAX_WINDOW_HOURS (default 24h) HALTS the auto-backfill and raises a
     critical flag (operator override required); never triggers a massive cascade.
  3. Decoupled webhook — Discord dispatch is fail-silent and isolated; a notification failure NEVER
     aborts (or alters) the recovery decision.

Exit codes (the entrypoint treats non-zero as a warning and advances under AMBER):
  0  clean   — disabled, or no gap, or bounded backfill completed
  20 AMBER   — Δt > window or no baseline; backfill refused, critical flag raised (operator override)
  1  FAIL    — execution error (DB / delegate) during an active recovery; flag raised
"""
from __future__ import annotations

import math
import subprocess
import sys
from datetime import datetime, timezone

DEFAULT_WINDOW_HOURS = 24

# Reason codes
GOV_WINDOW_EXCEEDED = "GOV-BACKFILL-001"   # Δt beyond the bounded window
GOV_NO_BASELINE = "GOV-BACKFILL-002"       # no last-canonical timestamp to bound against
GOV_EXEC = "GOV-BACKFILL-003"              # DB / delegate execution failure
GOV_RECOVER = "GOV-BACKFILL-RECOVER"       # informational: a bounded backfill is being performed

# Decision states
FRESH = "FRESH"
PROCEED = "PROCEED"
AMBER_STOP = "AMBER_STOP"

ENABLE_ENV = "HERMES_BACKFILL_RECOVERY_ENABLED"
WINDOW_ENV = "HERMES_BACKFILL_MAX_WINDOW_HOURS"
WEBHOOK_ENV = "ROGUE_ALLIANCE_DISCORD_WEBHOOK"
SIGNATURE_ENV = "HERMES_ARCHITECT_RECOVERY_SIGNATURE"
RUN_ENV_VAR = "RUN_ENV"

# Purge-grade activation (Invariant B): strict, case-sensitive enable string (mirrors the purge engine).
ENABLE_VALUE = "TRUE"
# Architect-mandated reason code for a missing/incomplete activation matrix (before any delta compute).
GOV_MISSING_PARAMS = "GOV-STAGE-CAP-003"

# Activation states
INERT = "INERT"      # master switch off -> inert dead-code, clean no-op
ABORT = "ABORT"      # armed but matrix incomplete/malformed -> hard fail before delta compute
ACTIVE = "ACTIVE"    # full architect-signed activation matrix satisfied


def resolve_activation(environ):
    """Purge-grade activation matrix — ALL THREE required to arm (Invariant B). Returns (state, gov_code).
      INERT  : ENABLE_ENV != 'TRUE' (master switch off, case-sensitive) -> inert dead-code, no-op.
      ABORT  : armed (ENABLE_ENV=='TRUE') but RUN_ENV!=PRODUCTION or signature missing -> GOV-STAGE-CAP-003.
      ACTIVE : RUN_ENV==PRODUCTION AND ENABLE_ENV=='TRUE' AND HERMES_ARCHITECT_RECOVERY_SIGNATURE present.
    A configuration change to ENABLE_ENV alone can NEVER flip this active (Invariant C)."""
    if (environ.get(ENABLE_ENV) or "").strip() != ENABLE_VALUE:
        return (INERT, None)
    run_env = (environ.get(RUN_ENV_VAR) or "").strip()
    signature = (environ.get(SIGNATURE_ENV) or "").strip()
    if run_env != "PRODUCTION" or not signature:
        return (ABORT, GOV_MISSING_PARAMS)
    return (ACTIVE, None)


def _safe_alert(alert_fn, code, summary):
    """Fix 3 — absolute isolation: a raising alert/telemetry path can NEVER interrupt recovery state."""
    try:
        alert_fn(code, summary)
    except Exception as exc:  # noqa: BLE001 — out-of-band telemetry must not leak into the recovery loop
        print(f"[RECOVERY][ALERT-MUTE] alert dispatch error suppressed ({code}): {exc}", file=sys.stderr)


# ----------------------------------------------------------------------------- pure decision --------
def recovery_decision(now_utc, last_ts, window_hours=DEFAULT_WINDOW_HOURS):
    """Pure guardrail decision. Returns (state, gov_code_or_None, delta_or_None). UTC-aware required."""
    if now_utc.tzinfo is None:
        raise ValueError(f"{GOV_EXEC}: now_utc must be timezone-aware UTC")
    if last_ts is None:                                  # guardrail 1: no baseline to bound against
        return (AMBER_STOP, GOV_NO_BASELINE, None)
    if last_ts.tzinfo is None:
        raise ValueError(f"{GOV_EXEC}: last_ts must be timezone-aware UTC")
    delta = now_utc - last_ts
    secs = delta.total_seconds()
    if secs <= 0:                                        # destination already current
        return (FRESH, None, delta)
    if secs > window_hours * 3600:                       # guardrail 2: bounded window
        return (AMBER_STOP, GOV_WINDOW_EXCEEDED, delta)
    return (PROCEED, None, delta)                        # guardrail 1: ingest only the missing interval


# ----------------------------------------------------------------------------- orchestration --------
def run(*, enabled, now_fn, last_ts_fn, backfill_fn, alert_fn, window_hours=DEFAULT_WINDOW_HOURS):
    """Orchestrate the recovery lifecycle with injected effects (testable). alert_fn MUST be fail-silent."""
    if not enabled:
        print(f"[RECOVERY] {ENABLE_ENV} not set -> recovery disabled; no-op (fail-silent).")
        return 0

    try:
        now = now_fn()
        last = last_ts_fn()
    except Exception as exc:                             # fail-LOUD during execution
        _safe_alert(alert_fn, GOV_EXEC, f"recovery aborted before backfill: {exc}")
        print(f"[RECOVERY][FAIL] {GOV_EXEC}: could not read destination baseline: {exc}", file=sys.stderr)
        return 1

    state, code, delta = recovery_decision(now, last, window_hours)

    if state == FRESH:
        print(f"[RECOVERY] destination current (Δt={delta}); no telemetry gap — nothing to backfill.")
        return 0

    if state == AMBER_STOP:
        summary = (f"Outage gap Δt={delta} exceeds bounded window ({window_hours}h) or has no baseline "
                   f"({code}). Auto-backfill HALTED — operator override required; live engine proceeds.")
        _safe_alert(alert_fn, code, summary)             # decoupled, fail-silent
        print(f"[RECOVERY][AMBER] {code}: {summary}", file=sys.stderr)
        return 20

    # PROCEED — bounded, idempotent backfill of ONLY the missing interval
    try:
        _safe_alert(alert_fn, GOV_RECOVER, f"Bounded automated backfill of Δt={delta} (≤{window_hours}h).")
        backfill_fn(since=last, until=now)
    except Exception as exc:                             # fail-LOUD during execution
        _safe_alert(alert_fn, GOV_EXEC, f"bounded backfill failed: {exc}")
        print(f"[RECOVERY][FAIL] {GOV_EXEC}: bounded backfill failed: {exc}", file=sys.stderr)
        return 1

    print(f"[RECOVERY] bounded backfill complete (Δt={delta}).")
    return 0


# ----------------------------------------------------------------------------- runtime wiring -------
def _query_last_canonical_ts():
    """MAX(timestamp) across the supported SQL candle tables -> the destination baseline (aware UTC)."""
    import pymysql
    sys.path.insert(0, ".")
    from env_config import get_db_config
    db = get_db_config()
    conn = pymysql.connect(host=db["host"], port=db["port"], user=db["user"],
                           password=db["password"], database=db["database"])
    try:
        cur = conn.cursor()
        cur.execute("SELECT MAX(ts) FROM (SELECT MAX(timestamp) ts FROM candles_M5 "
                    "UNION ALL SELECT MAX(timestamp) FROM candles_H1) u")
        row = cur.fetchone()
    finally:
        conn.close()
    if not row or row[0] is None:
        return None
    return datetime.fromisoformat(str(row[0])).replace(tzinfo=timezone.utc)


def _delegate_backfill(*, since, until):
    """Delegate the ACTUAL ingest to the legacy backfill utility for the bounded window. Idempotent
    (ON DUPLICATE KEY UPDATE). Window is expressed in days (legacy CLI granularity); ≤24h -> 1 day."""
    hours = max(0.0, (until - since).total_seconds() / 3600.0)
    days = max(1, math.ceil(hours / 24.0))
    subprocess.run([sys.executable, "scripts/backfill_oanda.py",
                    "--days", str(days), "--all-instruments", "--all-timeframes"], check=True)


def main(argv=None):
    import os
    # Invariant B/C: purge-grade activation matrix evaluated BEFORE any delta computation / verification.
    state, gov = resolve_activation(os.environ)
    if state == INERT:
        print(f"[RECOVERY] inert — {ENABLE_ENV} != '{ENABLE_VALUE}'; recovery is dead-code (no-op).")
        return 0
    if state == ABORT:
        print(f"[RECOVERY][ABORT] {gov} (Missing Parameters): activation requires RUN_ENV=PRODUCTION + "
              f"{ENABLE_ENV}={ENABLE_VALUE} + {SIGNATURE_ENV} — incomplete/malformed. Refusing before delta "
              "computation (fail-loud).", file=sys.stderr)
        return 1

    # ACTIVE — full architect-signed activation matrix satisfied.
    try:
        window_hours = int(os.environ.get(WINDOW_ENV) or DEFAULT_WINDOW_HOURS)
    except (TypeError, ValueError):
        window_hours = DEFAULT_WINDOW_HOURS

    sys.path.insert(0, ".")
    from env_config import get_env
    webhook = get_env(WEBHOOK_ENV, default="") or ""

    from utils.alerts.discord import dispatch_rogue_alert

    def alert_fn(code, summary):                          # fail-silent by contract
        dispatch_rogue_alert(webhook, code, summary)

    return run(
        enabled=True,
        now_fn=lambda: datetime.now(timezone.utc),
        last_ts_fn=_query_last_canonical_ts,
        backfill_fn=_delegate_backfill,
        alert_fn=alert_fn,
        window_hours=window_hours,
    )


if __name__ == "__main__":
    sys.exit(main())
