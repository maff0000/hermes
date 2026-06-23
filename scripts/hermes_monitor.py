#!/usr/bin/env python3
"""Out-of-band HERMES live telemetry monitor.
WO-HERMES-MONITOR-AND-CONSUMER-HANDOFF.

Verifies the 14-instrument keyspace on the shared dev/staging Redis bus and, on regression, dispatches
fail-loud notifications out-of-band to Graylog (GELF/UDP) and Discord. Designed for a 60s cron loop.

Hardened vs the original spec:
  * SCAN (non-blocking) instead of KEYS — this runs against the SHARED :6379 bus (NEO/Falcon/Solo);
    a blocking KEYS every 60s would stall the broker for the other consumers.
  * Self-loads .env via python-dotenv (no insecure `cat .env | xargs` in cron; secrets never hit argv).
  * Notification paths are fail-silent (a Graylog/Discord outage never changes the audit exit code).
"""
import json
import os
import socket
import sys

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
except Exception:
    pass  # dotenv optional; env may already be populated

GRAYLOG_HOST = os.getenv("MONITOR_GRAYLOG_HOST", os.getenv("GRAYLOG_HOST", "127.0.0.1"))
GRAYLOG_PORT = int(os.getenv("MONITOR_GRAYLOG_PORT", os.getenv("GRAYLOG_PORT", "12201")))
DISCORD_WEBHOOK_URL = os.getenv("MONITOR_DISCORD_WEBHOOK") or os.getenv("ROGUE_ALLIANCE_DISCORD_WEBHOOK")
REDIS_HOST = os.getenv("DEV_REDIS_HOST", "192.168.11.10")
REDIS_PORT = int(os.getenv("DEV_REDIS_PORT", "6379"))
EXPECTED_INSTRUMENTS = int(os.getenv("MONITOR_EXPECTED_INSTRUMENTS", "14"))


def log_to_graylog(message, level=3):
    payload = {"version": "1.1", "host": socket.gethostname(), "short_message": message,
               "level": level, "_facility": "hermes-monitor"}
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(json.dumps(payload).encode("utf-8"), (GRAYLOG_HOST, GRAYLOG_PORT))
    except Exception as e:  # fail-silent: telemetry never crashes the monitor
        print(f"Graylog Fail: {e}", file=sys.stderr)


def send_discord_alert(message):
    if not DISCORD_WEBHOOK_URL:
        print("[ALERT-MUTE] no MONITOR_DISCORD_WEBHOOK configured; Discord dispatch skipped.", file=sys.stderr)
        return
    try:
        import requests
        requests.post(DISCORD_WEBHOOK_URL, json={"content": f"🚨 **[HERMES FAILURE]** {message}"}, timeout=5)
    except Exception as e:  # fail-silent
        print(f"Discord Fail: {e}", file=sys.stderr)


def main():
    try:
        import redis
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        # Non-blocking SCAN (NOT KEYS) — safe against the shared platform bus.
        n = sum(1 for _ in r.scan_iter(match="hermes:instrument:*", count=100))
        if n != EXPECTED_INSTRUMENTS:
            raise ValueError(f"Keyspace mismatch on {REDIS_HOST}:{REDIS_PORT}. "
                             f"Expected {EXPECTED_INSTRUMENTS}, found {n}")
        print(f"HERMES Live Audit: PASS ({n}/{EXPECTED_INSTRUMENTS} instruments on {REDIS_HOST}:{REDIS_PORT})")
        return 0
    except Exception as e:
        err = f"Audit Critical Error: {e}"
        print(err, file=sys.stderr)
        log_to_graylog(err)
        send_discord_alert(err)
        return 1


if __name__ == "__main__":
    sys.exit(main())
