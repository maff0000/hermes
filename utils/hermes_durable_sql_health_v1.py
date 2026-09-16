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
candle_durable_sql_contract_v1.d1_durable_source_lineage_status) is NOT a fault: it is the guard working
exactly as designed and is expected to happen routinely through the day (normal honest missing-data/gap
semantics). It never downgrades health. A `source_lineage_conflict` (the durable H4 source set is fully
PRESENT but re-deriving D1 from it does NOT agree with what is being offered — round-2 Architect review
correction) IS a real data-integrity anomaly — the offered D1 is not provably derived from durable truth —
and downgrades to AMBER, same severity as `conflict_detected` (an existing durable row genuinely differs
from a freshly-sealed candle): the durable authority has NOT stopped advancing, one specific bucket needs a
human repair WO. A `connect_fail`/`write_fail` (the writer could not even attempt or complete a write)
downgrades to RED — the durable historical authority has genuinely stopped advancing.
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
    if writer_status.get("conflict_detected", 0) > 0 or writer_status.get("source_lineage_conflict", 0) > 0:
        return "AMBER"
    return None


def durable_sql_health(h4_status, d1_status):
    """Combine the H4 and D1 durable-SQL writer statuses into one /health block + downgrade.
    Returns (block: dict, downgrade: "RED"|"AMBER"|None)."""
    downgrade = worse_of(durable_sql_writer_health(h4_status), durable_sql_writer_health(d1_status))
    block = {"h4": h4_status or {"enabled": False}, "d1": d1_status or {"enabled": False}}
    return block, downgrade
