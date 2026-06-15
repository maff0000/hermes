"""HERMES-owned structure-engine ingest boundary (DEP-2/DEP-3 severance).
WO-HELM-HERMES-STRUCTURE-ENGINE-SEVERANCE-0001.

HERMES is a standalone application that owns MARKET TRUTH only. `structure_engine` (CHOCH/BOS/
order-block/structure interpretation) is NOT HERMES-owned — it is downstream structure/strategy
domain and lives only in tradingProteus. HERMES must NOT import tradingProteus code, must NOT add
/srv-dev/tradingProteus to sys.path, and must NOT push ticks into structure_engine.

This module REPLACES the former `from structure_engine.ingest import build_publisher` (DEP-2) and
the `sys.path.insert(0, '/srv-dev/tradingProteus')` hack (DEP-3) with a HERMES-owned boundary:

  - HERMES persists its own market truth (ticks -> candles, and the tick contract) independently.
    It does NOT push into structure_engine. The structure_engine app must CONSUME the HERMES tick
    contract (SQL `tradingSignals.ticks` / the Redis tick stream) — producer never pushes into a
    consumer's code.
  - Default (STRUCTURE_ENGINE_ENABLED=false): a HERMES-owned NoopStructureIngestPublisher — the
    publish hook becomes a no-op; HERMES no longer drives structure_engine.
  - Enabled (STRUCTURE_ENGINE_ENABLED=true): FAIL LOUD (GOV-SE-SEVERED-001). The structure_engine
    push is severed and cannot be re-enabled from HERMES; reconfigure structure_engine to pull the
    HERMES tick contract and set STRUCTURE_ENGINE_ENABLED=false. No silent fallback, no wrapper,
    no tradingProteus import, no sys.path hack.

The publish contract shape (.ok / .error_tag / .error_detail) is preserved so the existing
main.py call sites are unchanged. ALL timestamps UTC. No DB/Redis I/O here.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class PublishResult:
    """Minimal HERMES-owned result contract (mirrors the shape main.py consumes)."""
    ok: bool = True
    error_tag: Optional[object] = None
    error_detail: Optional[str] = None


class NoopStructureIngestPublisher:
    """HERMES does NOT push into structure_engine. This no-op satisfies the publish hook contract;
    structure_engine consumes the HERMES tick contract (SQL/Redis) instead. Returns ok=True."""

    def publish(self, tick) -> PublishResult:  # noqa: ARG002 - tick intentionally unused
        return PublishResult(ok=True)


def build_publisher(enabled: Optional[bool] = None):
    """Return the HERMES structure-ingest boundary publisher.

    enabled=None -> resolve STRUCTURE_ENGINE_ENABLED via HERMES env_config (governed flag).
    enabled True -> fail loud (severed; not HERMES-owned). enabled False -> NoopStructureIngestPublisher.
    """
    if enabled is None:
        from env_config import get_env_bool  # HERMES-owned config at repo root
        enabled = get_env_bool("STRUCTURE_ENGINE_ENABLED", False)
    if enabled:
        raise RuntimeError(
            "GOV-SE-SEVERED-001: structure_engine ingest is SEVERED from HERMES (structure "
            "interpretation is NOT HERMES-owned market truth). HERMES no longer imports tradingProteus "
            "structure_engine and does not push ticks into it. The structure_engine app must CONSUME "
            "the HERMES tick contract (tradingSignals.ticks / Redis tick stream). Set "
            "STRUCTURE_ENGINE_ENABLED=false. (fail-loud)"
        )
    return NoopStructureIngestPublisher()
