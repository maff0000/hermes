"""HERMES durable-SQL canonical historical persistence READINESS TRUTH v1 — pure derivation, no I/O.
WO-HELM-HERMES-DEV-DARWIN-DURABLE-CANONICAL-HISTORICAL-AUTHORITY-0001 (Architect review correction).

A durable-SQL persistence fault (DB unreachable, write failure) must never take down HERMES's live
market-data/candle publication — that is already fault-isolated at the writer itself
(candle_durable_sql_writer_v1.DurableSqlWriter.on_sealed never raises). But it also must not go SILENT:
the whole point of this WO is that historical + live is ONE continuous surface, so if the live half of
that surface stops advancing, /health must say so — exactly the same LIVENESS-vs-READINESS correction
hermes_canonical_publisher_health_v1 already applied to the Redis canonical path after the 2026-09
incident (a healthy watchdog does not mean a healthy canonical/durable surface).

A `source_incomplete_refused` (D1 correctly refusing to persist ahead of its durable H4 source — see
candle_durable_sql_contract_v1.d1_durable_h4_source_complete) is NOT a fault: it is the new guard working
exactly as designed and is expected to happen routinely through the day. It never downgrades health. A
`conflict_detected` (an existing durable row genuinely differs from a freshly-sealed candle) IS a real
data-integrity anomaly needing a human repair WO — it downgrades to AMBER, not RED, because the durable
authority has NOT stopped advancing, one specific bucket needs attention. A `connect_fail`/`write_fail`
(the writer could not even attempt or complete a write) downgrades to RED — the durable historical
authority has genuinely stopped advancing.
"""
from __future__ import annotations

from utils.hermes_canonical_publisher_health_v1 import worse_of


def durable_sql_writer_health(writer_status):
    """Derive the /health downgrade ("RED"|"AMBER"|None) for ONE durable SQL writer's `.status()` dict.
    Disabled (the default, dark-by-default) -> no downgrade, no block noise."""
    if not writer_status or not writer_status.get("enabled"):
        return None
    if writer_status.get("connect_fail", 0) > 0 or writer_status.get("write_fail", 0) > 0:
        return "RED"
    if writer_status.get("conflict_detected", 0) > 0:
        return "AMBER"
    return None


def durable_sql_health(h4_status, d1_status):
    """Combine the H4 and D1 durable-SQL writer statuses into one /health block + downgrade.
    Returns (block: dict, downgrade: "RED"|"AMBER"|None)."""
    downgrade = worse_of(durable_sql_writer_health(h4_status), durable_sql_writer_health(d1_status))
    block = {"h4": h4_status or {"enabled": False}, "d1": d1_status or {"enabled": False}}
    return block, downgrade
