"""Activation preflight guards for the HERMES tick-contract path
(WO-HELM-HERMES-MIGRATION-AND-BACKFILL-HARDENING-0001).

New file (strictly necessary): the dual-writer tripwire is an ACTIVATION-time guard
that future activation WOs (native-writer enable, Stage K) must call. It does not
belong inside the backfill script semantically, so it lives here as a small reusable,
fail-loud module. NO runtime/activation is performed here — these are assertions only.
"""
from __future__ import annotations


def assert_no_dual_writer(structure_engine_authoritative: bool, hermes_writer_enabled: bool) -> bool:
    """Fail loud if BOTH the structure_engine sink is still the authoritative writer of
    tradingSignals.ticks AND the HERMES native tick writer is enabled. Two concurrent
    writers of the same table would collide on the idempotency / seq keys and corrupt
    HERMES tick truth. Returns True only when at most one writer is active."""
    if structure_engine_authoritative and hermes_writer_enabled:
        raise RuntimeError(
            "GOV-DUAL-WRITER-001: structure_engine sink is still authoritative AND the "
            "HERMES native tick writer is enabled — refusing. Exactly one writer of "
            "tradingSignals.ticks is permitted (no dual-writer window)."
        )
    return True
