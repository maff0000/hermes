"""Advanced-v1 migration-readiness guard tests (§21). Fail-closed; DB-free (injected)."""
import pytest

from utils.hermes_advanced_v1_readiness_v1 import (
    readiness, check_columns_present, assert_ready, ReadinessError, REQUIRED_COLUMNS,
)
from utils.hermes_instrument_registry_v1 import load_registry
from tests.test_hermes_instrument_registry_v1 import rollout_rows

BASE_COLS = ("symbol", "category", "enabled", "oanda_compatible", "name")


def test_ready_when_columns_and_metadata_present():
    r = readiness(fetch=lambda: rollout_rows(), column_names=list(BASE_COLS) + list(REQUIRED_COLUMNS))
    assert r == {"ready": True, "fault": None}


def test_missing_migration_columns_blocks_deployment():
    # deployed schema lacks migration 025 columns -> fail closed (this is the production-today state)
    r = readiness(fetch=lambda: rollout_rows(), column_names=list(BASE_COLS))
    assert r["ready"] is False and "ADV-V1-READINESS-COLUMNS-MISSING" in r["fault"]


def test_check_columns_present_raises_with_exact_missing():
    with pytest.raises(ReadinessError, match="tick_size"):
        check_columns_present(["symbol", "price_precision"])  # missing tick_size etc.


def test_registry_unavailable_is_not_ready():
    r = readiness(fetch=lambda: (_ for _ in ()).throw(RuntimeError("db down")),
                  column_names=list(BASE_COLS) + list(REQUIRED_COLUMNS))
    assert r["ready"] is False and r["fault"]


def test_category_authority_without_energy_fails():
    recs = load_registry(rollout_rows())
    with pytest.raises(ReadinessError, match="missing 'energy'"):
        assert_ready(recs, category_authority=["precious_metals", "forex_major", "indices"])


def test_unsupported_metadata_version_fails():
    rows = rollout_rows()
    rows[0]["metadata_version"] = "v2"
    recs = load_registry(rows)
    with pytest.raises(ReadinessError, match="metadata_version"):
        assert_ready(recs)
