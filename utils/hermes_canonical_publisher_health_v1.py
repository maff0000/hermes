"""HERMES canonical-publisher READINESS TRUTH v1 — pure derivation, no I/O.
WO-HELM-HERMES-INCIDENT-CANONICAL-PIPELINE-CONFIG-AND-READINESS-TRUTH-0001.

Root cause of the 2026-09-05..09-16 incident: HERMES's `/health` reported GREEN throughout an 11-day
outage of the canonical `hermes:candles:XAU_USD:*` publish path, because `/health`'s health_state is
derived ONLY from the legacy tick/M1 watchdog (utils/watchdog.py) — a different, unaffected pipeline.
The publisher heartbeat (`hermes:publisher:heartbeat:v1`, written every control-plane cycle by
hermes_runtime_publisher_steps_v1.control_plane_step via derive_publisher_status) correctly reported
status=FAIL / latest_key_freshness=UNKNOWN the entire time — nothing consumed it.

This module is the correct-semantics fix Matt required: LIVENESS (is the process alive) stays owned by
the watchdog; READINESS additionally requires the canonical publisher to be genuinely fresh. It performs
NO Redis I/O itself — the caller (main.py /health) reads the heartbeat key and hands the raw string (or
None) to `canonical_publisher_health()`, which is pure and directly unit-testable without a live Redis,
app, or TestClient.

Never fabricates freshness: a missing/unparseable/expired heartbeat is treated as NOT_READY (RED), the
same as an explicit status=FAIL — silence is never mistaken for health.
"""
from __future__ import annotations
import json

# Severity ordering so a caller can take the WORSE of two health states without hardcoding string compares.
_SEVERITY = {"GREEN": 0, "AMBER": 1, "RED": 2}


def worse_of(a, b):
    """The more severe of two health_state strings (GREEN < AMBER < RED). None is treated as GREEN
    (no downgrade requested)."""
    if a is None:
        return b
    if b is None:
        return a
    return a if _SEVERITY.get(a, 0) >= _SEVERITY.get(b, 0) else b


# Publisher-heartbeat status (OK/WARN/FAIL, from derive_publisher_status) -> the /health downgrade it
# requires. OK never downgrades; WARN degrades to AMBER (some timeframes stale, non-blocking); FAIL
# degrades to RED (NOT_READY — canonical facts cannot be trusted, downstream must mechanically refuse).
_STATUS_TO_DOWNGRADE = {"OK": None, "WARN": "AMBER", "FAIL": "RED"}


def canonical_publisher_health(raw_heartbeat):
    """Derive the /health `canonical_publisher` block + any required health_state downgrade from the
    raw `hermes:publisher:heartbeat:v1` value (a JSON string as read from Redis, or None/empty if the
    key is absent or expired — e.g. the supervisor thread died and the TTL lapsed).

    Returns (block: dict, downgrade: "RED"|"AMBER"|None).
    """
    if not raw_heartbeat:
        return (
            {"observed": False, "reason": "HEARTBEAT_KEY_MISSING",
             "detail": "hermes:publisher:heartbeat:v1 absent or expired — canonical publisher state unknown"},
            "RED",
        )
    try:
        hb = json.loads(raw_heartbeat)
    except (TypeError, ValueError) as exc:
        return (
            {"observed": False, "reason": "HEARTBEAT_KEY_UNPARSEABLE", "detail": repr(exc)[:200]},
            "RED",
        )
    status = hb.get("status")
    downgrade = _STATUS_TO_DOWNGRADE.get(status, "RED")   # an unrecognised status is never treated as OK
    return (
        {"observed": True, "status": status,
         "latest_key_freshness": hb.get("latest_key_freshness"),
         "generated_at_utc": hb.get("generated_at_utc"),
         "d1_state": hb.get("d1_state")},
        downgrade,
    )
