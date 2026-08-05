"""Reusable, registry-driven Advanced-v1 instrument SELECTION + generic Redis key factory.

WO-HELM-HERMES-ADVANCED-V1-XAU-REGISTRY-REWIRE-0001.

This is the single authority the Advanced-v1 pipeline consults to decide WHICH instruments a capability
publishes for — replacing the per-module hard-coded XAU authority constant and env allowlists. It is
purely registry-capability driven: currently returns {XAU_USD} (the existing active pilot) and NEVER the 7
new instruments (they are NOT_ENABLED). There is NO ticker-name branch and NO hard-coded rollout list here.

Byte-parity: the generic key builders below emit exactly the existing XAU key strings, so a module adopting
them produces identical Redis keys. Fail-closed: an unavailable/invalid registry raises RegistryError.
"""
from __future__ import annotations

from typing import Callable, Mapping, Optional, Sequence, Tuple

from utils.hermes_instrument_registry_v1 import (
    InstrumentRecord, RegistryError, capability_instruments, load_from_db,
)

# Advanced-v1 capability -> registry capability flag column. The single mapping; no ticker logic.
CAPABILITY_FLAG = {
    "tick": "tick_contract_enabled",
    "indicator": "indicator_contract_enabled",
    "gap": "gap_detection_enabled",
}
# backfill STATUS selection follows the gap capability (status projection is a function of gap detection);
# the backfill EXECUTOR is a separate, still-held gate and is NOT selected here.
BACKFILL_STATUS_FLAG = "gap_detection_enabled"


def selection_for(capability: str, records: Sequence[InstrumentRecord]) -> Tuple[str, ...]:
    """Registry-driven instrument set for an Advanced-v1 capability ('tick'|'indicator'|'gap')."""
    if capability not in CAPABILITY_FLAG:
        raise RegistryError(f"unknown advanced-v1 capability: {capability!r} (known: {sorted(CAPABILITY_FLAG)})")
    return capability_instruments(records, CAPABILITY_FLAG[capability])


def backfill_status_instruments(records: Sequence[InstrumentRecord]) -> Tuple[str, ...]:
    """Instruments whose backfill STATUS is projected (follows gap detection). Executor remains disabled."""
    return capability_instruments(records, BACKFILL_STATUS_FLAG)


def load_selection(fetch: Optional[Callable[[], Sequence[Mapping]]] = None) -> dict:
    """Fail-closed: resolve the full advanced-v1 selection from the registry (or an injected fetcher).
    Returns {capability: (instruments...)} plus backfill_status. Never falls back to a hard-coded set."""
    records = load_from_db(fetch)  # raises RegistryError if unavailable/invalid
    sel = {cap: selection_for(cap, records) for cap in CAPABILITY_FLAG}
    sel["backfill_status"] = backfill_status_instruments(records)
    return sel


# --- generic key factory (parameterised by instrument; byte-identical to the existing XAU key strings) ---
def tick_latest_key(instrument: str) -> str:
    return f"hermes:ticks:{instrument}:latest:v1"


def indicator_key(instrument: str, timeframe: str) -> str:
    return f"hermes:indicators:{instrument}:{timeframe}:v1"


def gaps_key(instrument: str) -> str:
    return f"hermes:gaps:{instrument}:v1"


def backfill_status_key(instrument: str) -> str:
    return f"hermes:backfill:status:{instrument}:v1"


def parity_ok(capability: str, records: Sequence[InstrumentRecord], legacy_allowlist: Sequence[str]) -> bool:
    """Regression guard: the registry-driven selection must equal the legacy allowlist for the active pilot."""
    return set(selection_for(capability, records)) == set(legacy_allowlist)
