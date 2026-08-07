#!/usr/bin/env python3
# HERMES-owned signal-truth canary.
# Relocated into HERMES source control under WO-HELM-HERMES-CANARY-RELOCATE-0001
# (previously an external platform canary; see WO evidence). Read-only DB, UTC-only, fail-loud. CANARY_DB_* env config.
"""
HERMES Signal Truth Canary — WO-HERMES-SIGNAL-TRUTH-CANARY-0001

Tiny, independent, read-only canary. One job:
  "Do we have fresh signal truth?"

If not, emits: we_dont_have_a_signal = true

No recovery. No inference. No trading logic. No side effects.
Read-only DB queries only. UTC-only time semantics.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

# ============================================================
# Config — no defaults, fail loud
# ============================================================

_REQUIRED_CONFIG = [
    "CANARY_ENABLED",
    "CANARY_INSTRUMENT",
    "CANARY_CHECK_INTERVAL_SECONDS",
    "CANARY_M1_STALE_THRESHOLD_SECONDS",
    "CANARY_SIGNAL_TRUTH_STALE_THRESHOLD_SECONDS",
    "CANARY_DB_HOST",
    "CANARY_DB_PORT",
    "CANARY_DB_USER",
    "CANARY_DB_PASSWORD",
    "CANARY_DB_NAME",
]


def load_config():
    """Load config STRICTLY from the process environment — external, application-specific, fail-closed.

    WO-HELM-HERMES-BUILD-CONTEXT-SECRET-LEAK-CONTAINMENT-AND-CLEAN-SOURCE-BUILD-HARDENING-0001:
    this NEVER reads a plaintext `.env` beside the script (that nearby-secret-file pattern was the build-context
    leak vector that baked a real shared credential into HERMES images), and NEVER falls back to a shared estate
    account or an embedded default. Each key is supplied externally via the HERMES-specific
    canonical name (`HERMES_CANARY_*`, preferred) or the legacy name (`CANARY_*`). Any missing key -> RED, exit 1."""
    config = {}
    for key in _REQUIRED_CONFIG:
        # HERMES-specific canonical name first, then the legacy name; external environment ONLY.
        val = os.environ.get("HERMES_" + key)
        if val is None:
            val = os.environ.get(key)
        if val is not None:
            config[key] = val

    # Validate all required keys present
    missing = [k for k in _REQUIRED_CONFIG if k not in config]
    if missing:
        result = {
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "we_dont_have_a_signal": True,
            "health_state": "RED",
            "reason_code": "HERMES_CANARY_CONFIG_MISSING",
            "missing_keys": missing,
        }
        print(json.dumps(result), flush=True)
        sys.exit(1)

    # Type conversions
    config["CANARY_DB_PORT"] = int(config["CANARY_DB_PORT"])
    config["CANARY_CHECK_INTERVAL_SECONDS"] = int(config["CANARY_CHECK_INTERVAL_SECONDS"])
    config["CANARY_M1_STALE_THRESHOLD_SECONDS"] = int(config["CANARY_M1_STALE_THRESHOLD_SECONDS"])
    config["CANARY_SIGNAL_TRUTH_STALE_THRESHOLD_SECONDS"] = int(config["CANARY_SIGNAL_TRUTH_STALE_THRESHOLD_SECONDS"])

    return config


# ============================================================
# DB — read-only, UTC-forced
# ============================================================

def get_connection(config):
    """Open a read-only connection with UTC session."""
    import pymysql
    return pymysql.connect(
        host=config["CANARY_DB_HOST"],
        port=config["CANARY_DB_PORT"],
        user=config["CANARY_DB_USER"],
        password=config["CANARY_DB_PASSWORD"],
        database=config["CANARY_DB_NAME"],
        cursorclass=pymysql.cursors.DictCursor,
        charset="utf8mb4",
        connect_timeout=10,
        read_timeout=10,
        init_command="SET time_zone = '+00:00'",
    )


# ============================================================
# Checks — UTC only
# ============================================================

def check_m1_freshness(conn, instrument):
    """Check latest M1 candle age. Returns (timestamp, age_seconds)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MAX(timestamp) as latest, UTC_TIMESTAMP() as utc_now "
            "FROM candles_M1 WHERE instrument = %s",
            (instrument,),
        )
        row = cur.fetchone()

    if row is None or row["latest"] is None:
        return None, None

    latest = row["latest"]
    utc_now = row["utc_now"]
    age = (utc_now - latest).total_seconds()
    return latest, age


def check_signal_truth_freshness(conn, instrument):
    """
    Check latest signal truth age.
    Uses M5 signals as the signal truth proxy — HERMES computes
    indicators (RSI, ADX, ATR, regime, etc.) on M5 and M15 only.
    M5 is the primary timeframe for downstream strategy evaluation.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MAX(timestamp) as latest, UTC_TIMESTAMP() as utc_now "
            "FROM signals WHERE instrument = %s AND timeframe = 'M5'",
            (instrument,),
        )
        row = cur.fetchone()

    if row is None or row["latest"] is None:
        return None, None

    latest = row["latest"]
    utc_now = row["utc_now"]
    age = (utc_now - latest).total_seconds()
    return latest, age


# ============================================================
# Canary — one check, one output
# ============================================================

def run_check(config):
    """
    Run one canary check. Returns structured result dict.
    No side effects beyond the return value.
    """
    instrument = config["CANARY_INSTRUMENT"]
    m1_threshold = config["CANARY_M1_STALE_THRESHOLD_SECONDS"]
    signal_threshold = config["CANARY_SIGNAL_TRUTH_STALE_THRESHOLD_SECONDS"]
    now_utc = datetime.now(timezone.utc)

    result = {
        "checked_at_utc": now_utc.isoformat(),
        "instrument": instrument,
        "we_dont_have_a_signal": False,
        "health_state": "GREEN",
        "reason_code": None,
        "latest_m1_utc": None,
        "latest_signal_truth_utc": None,
        "m1_age_seconds": None,
        "signal_truth_age_seconds": None,
    }

    # Connect
    try:
        conn = get_connection(config)
    except Exception as e:
        result["we_dont_have_a_signal"] = True
        result["health_state"] = "RED"
        result["reason_code"] = "HERMES_CANARY_DB_QUERY_FAILED"
        result["error"] = str(e)
        return result

    try:
        # Check M1 candle freshness
        m1_ts, m1_age = check_m1_freshness(conn, instrument)
        if m1_ts is not None:
            result["latest_m1_utc"] = m1_ts.isoformat()
            result["m1_age_seconds"] = int(m1_age)
        else:
            result["we_dont_have_a_signal"] = True
            result["health_state"] = "RED"
            result["reason_code"] = "HERMES_CANARY_M1_STALE"
            return result

        if m1_age > m1_threshold:
            result["we_dont_have_a_signal"] = True
            result["health_state"] = "RED"
            result["reason_code"] = "HERMES_CANARY_M1_STALE"
            return result

        # Check signal truth freshness (M5 signals)
        sig_ts, sig_age = check_signal_truth_freshness(conn, instrument)
        if sig_ts is not None:
            result["latest_signal_truth_utc"] = sig_ts.isoformat()
            result["signal_truth_age_seconds"] = int(sig_age)
        else:
            result["we_dont_have_a_signal"] = True
            result["health_state"] = "RED"
            result["reason_code"] = "HERMES_CANARY_SIGNAL_TRUTH_STALE"
            return result

        if sig_age > signal_threshold:
            result["we_dont_have_a_signal"] = True
            result["health_state"] = "RED"
            result["reason_code"] = "HERMES_CANARY_SIGNAL_TRUTH_STALE"
            return result

        # Both fresh
        return result

    except Exception as e:
        result["we_dont_have_a_signal"] = True
        result["health_state"] = "RED"
        result["reason_code"] = "HERMES_CANARY_DB_QUERY_FAILED"
        result["error"] = str(e)
        return result
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ============================================================
# Main — loop or single check
# ============================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(description="HERMES Signal Truth Canary")
    parser.add_argument("--once", action="store_true", help="Single check then exit")
    args = parser.parse_args()

    config = load_config()

    if config.get("CANARY_ENABLED", "true").lower() != "true":
        print(json.dumps({
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "health_state": "DISABLED",
            "reason_code": "CANARY_DISABLED",
        }), flush=True)
        sys.exit(0)

    interval = config["CANARY_CHECK_INTERVAL_SECONDS"]

    while True:
        result = run_check(config)
        print(json.dumps(result, default=str), flush=True)

        if args.once:
            sys.exit(0 if not result["we_dont_have_a_signal"] else 1)

        time.sleep(interval)


if __name__ == "__main__":
    main()
