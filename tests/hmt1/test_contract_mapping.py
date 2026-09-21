"""HMT-1 — ContractMappingTable: explicit, deterministic, hashable, versioned, evidence-bearing
provider->canonical GC symbology mapping. Fails closed on anything ambiguous/invalid/unknown."""
from pathlib import Path

import pytest

from market_truth.futures import ContractMappingError, ContractMappingTable, GcContractIdentity

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "hmt1"
MAPPING_PATH = FIXTURES_DIR / "gc_contract_mapping_v1.json"


def test_loads_from_json_file():
    table = ContractMappingTable.from_json_file(MAPPING_PATH)
    assert table.version == "hmt1-gc-contract-mapping-v1"
    assert table.resolve("GCZ26") == GcContractIdentity(delivery_year=2026, delivery_month=12)
    assert table.resolve("GCM26") == GcContractIdentity(delivery_year=2026, delivery_month=6)


def test_unknown_symbol_fails_closed():
    table = ContractMappingTable.from_json_file(MAPPING_PATH)
    with pytest.raises(ContractMappingError):
        table.resolve("GCZ99")


def test_bare_and_continuous_roots_fail_closed():
    table = ContractMappingTable.from_json_file(MAPPING_PATH)
    for bad_symbol in ("GC", "GC.FUT", "GC.V.0", ""):
        with pytest.raises(ContractMappingError):
            table.resolve(bad_symbol)


def test_ambiguous_single_digit_year_symbol_is_not_in_the_table_and_fails_closed():
    """A raw wire symbol like 'GCZ6' is genuinely ambiguous across decades with no further
    context — this module never guesses a decade; an unresolvable symbol simply is not in the
    governed table and resolution fails closed exactly like any other unknown symbol."""
    table = ContractMappingTable.from_json_file(MAPPING_PATH)
    with pytest.raises(ContractMappingError):
        table.resolve("GCZ6")


def test_content_hash_is_deterministic_and_order_independent():
    table_a = ContractMappingTable(
        version="v1",
        entries={
            "GCZ26": GcContractIdentity(delivery_year=2026, delivery_month=12),
            "GCM26": GcContractIdentity(delivery_year=2026, delivery_month=6),
        },
    )
    table_b = ContractMappingTable(
        version="v1",
        entries={
            "GCM26": GcContractIdentity(delivery_year=2026, delivery_month=6),
            "GCZ26": GcContractIdentity(delivery_year=2026, delivery_month=12),
        },
    )
    assert table_a.content_sha256() == table_b.content_sha256()


def test_content_hash_changes_when_a_mapping_changes():
    table_a = ContractMappingTable(version="v1", entries={"GCZ26": GcContractIdentity(delivery_year=2026, delivery_month=12)})
    table_b = ContractMappingTable(version="v1", entries={"GCZ26": GcContractIdentity(delivery_year=2027, delivery_month=12)})
    assert table_a.content_sha256() != table_b.content_sha256()


def test_conflicting_duplicate_entry_in_file_fails_closed(tmp_path):
    bad_file = tmp_path / "conflicting_mapping.json"
    bad_file.write_text(
        """
        {
          "version": "v1",
          "mappings": [
            {"provider_symbol": "GCZ26", "delivery_year": 2026, "delivery_month": 12},
            {"provider_symbol": "GCZ26", "delivery_year": 2027, "delivery_month": 12}
          ]
        }
        """
    )
    with pytest.raises(ContractMappingError):
        ContractMappingTable.from_json_file(bad_file)


def test_malformed_entry_fails_closed(tmp_path):
    bad_file = tmp_path / "malformed_mapping.json"
    bad_file.write_text('{"version": "v1", "mappings": [{"provider_symbol": "GCZ26"}]}')
    with pytest.raises(ContractMappingError):
        ContractMappingTable.from_json_file(bad_file)
