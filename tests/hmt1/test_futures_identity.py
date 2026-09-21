"""HMT-1 — GcContractIdentity: durable actual-contract identity, never a bare root."""
import pytest

from market_truth.futures import ContractMappingError, GcContractIdentity


def test_canonical_id_is_fully_qualified():
    contract = GcContractIdentity(delivery_year=2026, delivery_month=12)
    assert contract.canonical_id() == "COMEX:GC:2026-12"


def test_bare_root_is_never_a_valid_identity():
    contract = GcContractIdentity(delivery_year=2026, delivery_month=12)
    assert contract.canonical_id() != "GC"
    assert "GC" != contract.canonical_id()
    assert contract.canonical_id().startswith("COMEX:GC:")


def test_rejects_invalid_month():
    with pytest.raises(ContractMappingError):
        GcContractIdentity(delivery_year=2026, delivery_month=13)
    with pytest.raises(ContractMappingError):
        GcContractIdentity(delivery_year=2026, delivery_month=0)


def test_rejects_implausible_year():
    with pytest.raises(ContractMappingError):
        GcContractIdentity(delivery_year=1900, delivery_month=1)


def test_two_different_delivery_months_are_different_contracts():
    dec = GcContractIdentity(delivery_year=2026, delivery_month=12)
    jun = GcContractIdentity(delivery_year=2026, delivery_month=6)
    assert dec.canonical_id() != jun.canonical_id()
    assert dec != jun
