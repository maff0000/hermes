"""HMT-2B — tests for market_truth.acquisition.lineage.

Every `LineageRow` here is CLEARLY SYNTHETIC (see lineage.py module docstring) — this checkpoint
has no real acquired GC MBP-1 data and no real canonical partitions built from it. Nothing in
this test file represents, or is claimed to represent, real GC data.
"""
from __future__ import annotations

import pytest

from market_truth.acquisition.lineage import (
    LINEAGE_CATALOGUE_VERSION,
    LineageCatalogue,
    LineageError,
    LineageRow,
)
from market_truth.futures import GcContractIdentity


def _contract(year=2026, month=12) -> GcContractIdentity:
    return GcContractIdentity(delivery_year=year, delivery_month=month)


def _make_row(**overrides) -> LineageRow:
    defaults = dict(
        corpus_session_id="GC-2026-01-05",
        provider_request_identity="req-synthetic-0001",
        native_artefact_relative_path="hmt2-gc-mbp1-v1/sessions/GC-2026-01-05/source/mbp1.bin",
        native_artefact_sha256="1" * 64,
        provider_definition_ref="synthetic-definition-ref-0001",
        gc_contract=_contract(),
        canonical_event_set_hash="2" * 64,
        research_partition_relative_paths=("schema=v1/.../part-00000.parquet",),
        evidence_manifest_ref="synthetic-evidence-ref-0001",
        synthetic=True,
    )
    defaults.update(overrides)
    return LineageRow(**defaults)


# ----------------------------------------------------------------------------------------------
# LineageRow — structural invariants
# ----------------------------------------------------------------------------------------------

def test_row_requires_session_id():
    with pytest.raises(LineageError):
        _make_row(corpus_session_id="")


def test_row_requires_provider_request_identity():
    with pytest.raises(LineageError):
        _make_row(provider_request_identity="")


def test_row_requires_native_artefact_path_and_hash_together():
    with pytest.raises(LineageError):
        _make_row(native_artefact_relative_path="")
    with pytest.raises(LineageError):
        _make_row(native_artefact_sha256="")


def test_row_requires_a_real_gc_contract_identity():
    with pytest.raises(LineageError):
        _make_row(gc_contract="COMEX:GC:2026-12")  # a bare string is not a GcContractIdentity


def test_row_rejects_orphan_partitions():
    """A row may not carry research-partition paths without a canonical_event_set_hash on the
    SAME row - WO Part 2's 'no orphan partitions' invariant, enforced at construction time."""
    with pytest.raises(LineageError):
        _make_row(canonical_event_set_hash=None, research_partition_relative_paths=("some/path.parquet",))


def test_row_permits_no_partitions_yet_with_no_event_set_hash():
    """This checkpoint's honest, expected state for every real (not-yet-acquired) session: no
    canonical events exist yet, so no partitions exist yet either - not an orphan, just empty."""
    row = _make_row(canonical_event_set_hash=None, research_partition_relative_paths=())
    assert row.research_partition_relative_paths == ()


def test_row_identity_sha256_is_deterministic():
    a = _make_row()
    b = _make_row()
    assert a.row_identity_sha256() == b.row_identity_sha256()


def test_row_identity_sha256_changes_with_any_field():
    a = _make_row()
    b = _make_row(evidence_manifest_ref="different-ref")
    assert a.row_identity_sha256() != b.row_identity_sha256()


def test_row_is_synthetic_by_default():
    assert _make_row().synthetic is True


# ----------------------------------------------------------------------------------------------
# LineageCatalogue — append-only, deterministic, orphan-free
# ----------------------------------------------------------------------------------------------

def test_catalogue_add_and_get_row():
    catalogue = LineageCatalogue()
    row = _make_row()
    catalogue.add_row(row)
    assert catalogue.get_row("GC-2026-01-05") is row


def test_catalogue_get_missing_row_fails_closed():
    catalogue = LineageCatalogue()
    with pytest.raises(LineageError):
        catalogue.get_row("GC-2026-01-05")


def test_catalogue_refuses_duplicate_session_id():
    catalogue = LineageCatalogue()
    catalogue.add_row(_make_row())
    with pytest.raises(LineageError):
        catalogue.add_row(_make_row())  # same corpus_session_id - append-only, never overwritten


def test_catalogue_all_rows_sorted_by_session_id_regardless_of_insertion_order():
    catalogue = LineageCatalogue()
    catalogue.add_row(_make_row(corpus_session_id="GC-2026-01-06", native_artefact_relative_path="p2", ))
    catalogue.add_row(_make_row(corpus_session_id="GC-2026-01-05"))
    ids = [row.corpus_session_id for row in catalogue.all_rows()]
    assert ids == ["GC-2026-01-05", "GC-2026-01-06"]


def test_catalogue_content_sha256_is_order_independent_and_deterministic():
    catalogue_a = LineageCatalogue()
    catalogue_a.add_row(_make_row(corpus_session_id="GC-2026-01-05"))
    catalogue_a.add_row(_make_row(corpus_session_id="GC-2026-01-06"))

    catalogue_b = LineageCatalogue()
    catalogue_b.add_row(_make_row(corpus_session_id="GC-2026-01-06"))
    catalogue_b.add_row(_make_row(corpus_session_id="GC-2026-01-05"))

    assert catalogue_a.catalogue_content_sha256() == catalogue_b.catalogue_content_sha256()


def test_catalogue_content_sha256_changes_when_a_row_changes():
    catalogue_a = LineageCatalogue()
    catalogue_a.add_row(_make_row())

    catalogue_b = LineageCatalogue()
    catalogue_b.add_row(_make_row(evidence_manifest_ref="different-ref"))

    assert catalogue_a.catalogue_content_sha256() != catalogue_b.catalogue_content_sha256()


def test_catalogue_assert_no_orphan_partitions_passes_for_a_clean_catalogue():
    catalogue = LineageCatalogue()
    catalogue.add_row(_make_row())
    catalogue.assert_no_orphan_partitions()  # must not raise


def test_lineage_catalogue_version_is_stable_string():
    assert LINEAGE_CATALOGUE_VERSION == "hmt2b-source-canonical-lineage-v1"
