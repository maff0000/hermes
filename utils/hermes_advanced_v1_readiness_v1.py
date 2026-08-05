"""Advanced-v1 migration-readiness guard (§21).

WO-HELM-HERMES-ADVANCED-V1-XAU-MODULE-ADOPTION-0001.

Registry-adopting modules MUST NOT publish while the deployed database lacks migration 025's schema. This
guard proves the required registry columns + metadata version + capability flags + category authority exist
BEFORE any publication, and FAILS CLOSED (typed fault) otherwise. It never auto-runs a migration, never
mutates SQL, and never falls back to a hard-coded instrument set — so merging the adopted source is safe
even though production deployment (and migration 025) remain separately held.
"""
from __future__ import annotations

from typing import Callable, Mapping, Optional, Sequence

from utils.hermes_instrument_registry_v1 import (
    RegistryError, load_from_db, GOVERNED_CATEGORIES,
)

REQUIRED_COLUMNS = (
    "price_precision", "tick_size", "price_authority", "market_hours_policy", "expected_freshness_sec",
    "enabled_timeframes", "indicator_profile", "tick_contract_enabled", "indicator_contract_enabled",
    "gap_detection_enabled", "backfill_policy", "retention_policy", "metadata_version",
)
SUPPORTED_METADATA_VERSIONS = frozenset({"v1"})


class ReadinessError(RegistryError):
    """Fail-closed: the deployed registry schema is not ready for the adopted Advanced-v1 code."""


def check_columns_present(column_names: Sequence[str]) -> None:
    """Prove migration 025's required columns exist in the deployed schema (caller supplies the column list,
    e.g. from information_schema). Fail-closed with the exact missing set."""
    present = set(column_names)
    missing = [c for c in REQUIRED_COLUMNS if c not in present]
    if missing:
        raise ReadinessError(
            f"ADV-V1-READINESS-COLUMNS-MISSING: migration 025 not applied — missing registry columns {missing}; "
            f"deployment blocked (do not publish; do not auto-migrate)"
        )


def assert_ready(records, *, category_authority: Sequence[str] = tuple(GOVERNED_CATEGORIES)) -> None:
    """Validate the loaded registry records are ready for adoption: supported metadata_version + the category
    authority includes 'energy'. Fail-closed."""
    if "energy" not in set(category_authority):
        raise ReadinessError("ADV-V1-READINESS-CATEGORY: category authority missing 'energy' (migration 025 not applied)")
    for r in records:
        if r.metadata_version not in SUPPORTED_METADATA_VERSIONS:
            raise ReadinessError(
                f"ADV-V1-READINESS-METADATA-VERSION: {r.symbol} metadata_version {r.metadata_version!r} unsupported "
                f"(supported: {sorted(SUPPORTED_METADATA_VERSIONS)})"
            )


def readiness(fetch: Optional[Callable[[], Sequence[Mapping]]] = None,
              column_names: Optional[Sequence[str]] = None) -> dict:
    """Full readiness probe for the adopted pipeline. Returns {ready, fault}. Fail-closed on any gap. When
    column_names is provided (from information_schema), the column-presence check runs first."""
    try:
        if column_names is not None:
            check_columns_present(column_names)
        records = load_from_db(fetch)         # fail-closed if registry unavailable/invalid
        assert_ready(records)
    except RegistryError as e:
        return {"ready": False, "fault": str(e)}
    return {"ready": True, "fault": None}
