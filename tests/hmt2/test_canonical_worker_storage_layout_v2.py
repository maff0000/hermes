"""HMT-2 governed canonical storage-layout-defect remediation — tests for the session-scoped
promotion fix in `market_truth.acquisition.canonical_worker`.

CONFIRMED DEFECT (see `canonical_worker.py`'s module docstring for the full mechanism, and the
fix's own dispatch report for the real, quantified forensic detail against the 122-session
corpus): `market_truth.partition.partition_relative_dir()` computes a canonical Parquet file's
LOGICAL path from ONLY `(contract_id, event.source_event_time.date(), event_family)` — never
`session_id`. The ORIGINAL promotion path promoted every session's staged output into ONE SHARED
physical tree keyed by that same session-blind logical path, so two different sessions whose
events land in the same `(contract, date, family)` bucket silently collided (whichever session
promoted last won; the earlier session's own lineage row was left pointing at a physical file it
no longer owns).

Uses ONLY the same zero-vendor-dependency fixtures already committed for HMT-1
(`tests/fixtures/hmt1/gc_contract_mapping_v1.json`, `tests/fixtures/hmt1/gc_mbp1_equivalent_v1.
jsonl`, replayed via `market_truth.providers.fixture.FixtureMarketDataProvider`) that
`test_canonical_worker.py` already uses — no real retained MBP-1 bytes and no vendor SDK
anywhere in this file. Two sessions given the SAME fixture records deterministically produce
events with the SAME `(contract, date, family)` coordinates, which is exactly the collision
shape this fix must now make physically impossible.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from market_truth.acquisition import canonical_worker as cw  # noqa: E402
from market_truth.acquisition.canonical_quality_record import SessionQualityCounters  # noqa: E402
from market_truth.acquisition.lineage import (  # noqa: E402
    CANONICAL_STORAGE_LAYOUT_VERSION,
    read_lineage_record,
)
from market_truth.futures import ContractMappingTable  # noqa: E402
from market_truth.providers.fixture import FixtureMarketDataProvider  # noqa: E402

FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "hmt1"
MAPPING_PATH = FIXTURES_DIR / "gc_contract_mapping_v1.json"
GC_FIXTURE = FIXTURES_DIR / "gc_mbp1_equivalent_v1.jsonl"


def _fixture_records():
    provider = FixtureMarketDataProvider(GC_FIXTURE, provider_id="hmt1-fixture-provider", dataset_id="hmt1-test")
    return list(provider.iter_records()), provider.content_sha256()


def _mapping_table():
    return ContractMappingTable.from_json_file(MAPPING_PATH)


def _canonicalise(canonical_store_root, session_id, *, records=None, native_sha256=None):
    if records is None:
        records, native_sha256 = _fixture_records()
    quality_counters = SessionQualityCounters(session_id=session_id)
    return cw.canonicalise_records(
        session_id=session_id,
        records=records,
        mapping_table=_mapping_table(),
        canonical_store_root=canonical_store_root,
        native_artefact_relative_path="hmt2-gc-mbp1-v1/sessions/%s/source/fixture.jsonl" % session_id,
        native_artefact_sha256=native_sha256,
        provider_request_identity="test-request-identity-%s" % session_id,
        provider_definition_ref="tests/fixtures/hmt1/gc_contract_mapping_v1.json",
        provider_adapter_version="hmt1-fixture-provider-v1",
        fixture_schema_version="hmt1-fixture-line-schema-v1",
        corpus_manifest_ref="research/hmt2/corpus-selection-manifest-v2.json",
        quality_counters=quality_counters,
    )


# ------------------------------------------------------------------------------------------------
# 1. Two sessions producing the same HMT-1 relative partition coordinate now resolve to
#    different final physical paths (never the same one — the exact collision this fix closes).
# ------------------------------------------------------------------------------------------------

def test_two_colliding_sessions_resolve_to_different_physical_paths(tmp_path):
    records, native_sha256 = _fixture_records()

    result_a = _canonicalise(tmp_path, "GC-COLLIDE-A", records=records, native_sha256=native_sha256)
    result_b = _canonicalise(tmp_path, "GC-COLLIDE-B", records=records, native_sha256=native_sha256)

    assert result_a.partition_relative_paths
    assert result_b.partition_relative_paths
    # Same input -> HMT-1's own (session-blind) logical coordinates are identical for both --
    # this IS the collision shape. Physically, they must never share a path.
    assert set(result_a.partition_relative_paths).isdisjoint(set(result_b.partition_relative_paths))
    for rel in result_a.partition_relative_paths:
        assert rel.startswith("session_id=GC-COLLIDE-A/")
    for rel in result_b.partition_relative_paths:
        assert rel.startswith("session_id=GC-COLLIDE-B/")

    canonical_root = tmp_path / cw.CANONICAL_STORAGE_ROOT_DIRNAME
    for rel in result_a.partition_relative_paths:
        assert (canonical_root / rel).exists()
    for rel in result_b.partition_relative_paths:
        assert (canonical_root / rel).exists()
    # The SAME logical (HMT-1) suffix, stripped of each session's own namespace prefix, is
    # identical for both -- proving this really is the collision shape, not two unrelated paths.
    suffixes_a = {rel.split("/", 1)[1] for rel in result_a.partition_relative_paths}
    suffixes_b = {rel.split("/", 1)[1] for rel in result_b.partition_relative_paths}
    assert suffixes_a == suffixes_b


# ------------------------------------------------------------------------------------------------
# 2/3. Processing session B cannot mutate session A's already-promoted artifact, and A's lineage
#    still verifies (hash matches disk) after B completes.
# ------------------------------------------------------------------------------------------------

def test_session_b_cannot_mutate_session_as_already_promoted_artifact(tmp_path):
    records, native_sha256 = _fixture_records()

    result_a = _canonicalise(tmp_path, "GC-COLLIDE-A", records=records, native_sha256=native_sha256)
    canonical_root = tmp_path / cw.CANONICAL_STORAGE_ROOT_DIRNAME
    a_bytes_before = {
        rel: (canonical_root / rel).read_bytes() for rel in result_a.partition_relative_paths
    }

    result_b = _canonicalise(tmp_path, "GC-COLLIDE-B", records=records, native_sha256=native_sha256)

    for rel, before in a_bytes_before.items():
        assert (canonical_root / rel).read_bytes() == before, f"session A's own artifact {rel} was mutated by B"

    # A's lineage row is still real, self-verifying, and points at real, unmoved bytes.
    row_a = read_lineage_record(tmp_path, "GC-COLLIDE-A")
    assert row_a.canonical_event_set_hash == result_a.canonical_event_set_hash
    assert cw.verify_existing_completion(
        canonical_store_root=tmp_path, session_id="GC-COLLIDE-A",
        native_artefact_path=GC_FIXTURE, expected_native_sha256=native_sha256,
    ) is True
    # B is independently real and complete too.
    assert cw.verify_existing_completion(
        canonical_store_root=tmp_path, session_id="GC-COLLIDE-B",
        native_artefact_path=GC_FIXTURE, expected_native_sha256=native_sha256,
    ) is True
    assert result_b.canonical_event_set_hash == result_a.canonical_event_set_hash  # same input


# ------------------------------------------------------------------------------------------------
# 4. An unexpected pre-existing destination (a file exists at the target v2 path with content
#    that does NOT match what is about to be promoted, and no verified-complete lineage record
#    for this exact session) causes a fail-closed error, never a silent overwrite.
# ------------------------------------------------------------------------------------------------

def test_unexpected_pre_existing_destination_with_no_verified_lineage_fails_closed(tmp_path):
    session_id = "GC-FAILCLOSED"
    records, native_sha256 = _fixture_records()
    result = _canonicalise(tmp_path, session_id, records=records, native_sha256=native_sha256)

    canonical_root = tmp_path / cw.CANONICAL_STORAGE_ROOT_DIRNAME
    target_rel = sorted(result.partition_relative_paths)[0]
    target_path = canonical_root / target_rel

    # Simulate "no verified-complete lineage record exists for this session" (e.g. the lineage
    # file was never durably written, or this store is being inspected before any lineage
    # existed) while a file is still unexpectedly sitting at the v2 destination path, WITH
    # content that will not match a fresh, deterministic re-derivation.
    (tmp_path / result.lineage_record_relative_path).unlink()
    target_path.write_bytes(b"UNEXPECTED FOREIGN BYTES, NOT A REAL PARQUET FILE, NOT FROM THIS SESSION")

    with pytest.raises(cw.CanonicalWorkerError, match="unexpected pre-existing canonical artifact"):
        _canonicalise(tmp_path, session_id, records=records, native_sha256=native_sha256)

    # Fail-closed means fail-closed: the foreign bytes are untouched, never silently overwritten.
    assert target_path.read_bytes() == b"UNEXPECTED FOREIGN BYTES, NOT A REAL PARQUET FILE, NOT FROM THIS SESSION"


def test_content_identical_pre_existing_destination_is_treated_as_a_safe_retry(tmp_path):
    """The narrow, deliberate exception to the fail-closed guard above: if what's already at the
    destination is BYTE-IDENTICAL to what this session is about to (re)promote -- exactly what a
    crash-and-retry of THIS SAME session looks like (same retained bytes + governed code always
    reproduce the same output) -- promotion proceeds rather than refusing a legitimate retry."""
    session_id = "GC-SAFE-RETRY"
    records, native_sha256 = _fixture_records()
    result = _canonicalise(tmp_path, session_id, records=records, native_sha256=native_sha256)

    # Simulate "no verified-complete lineage yet" (e.g. a crash between promotion and the
    # lineage write) with the destination file's content EXACTLY as this session itself wrote
    # it -- never a foreign mismatch.
    (tmp_path / result.lineage_record_relative_path).unlink()

    result_retry = _canonicalise(tmp_path, session_id, records=records, native_sha256=native_sha256)
    assert result_retry.canonical_event_set_hash == result.canonical_event_set_hash
    assert cw.verify_existing_completion(
        canonical_store_root=tmp_path, session_id=session_id,
        native_artefact_path=GC_FIXTURE, expected_native_sha256=native_sha256,
    ) is True


# ------------------------------------------------------------------------------------------------
# New lineage records carry the current storage-layout version.
# ------------------------------------------------------------------------------------------------

def test_new_lineage_record_carries_the_current_storage_layout_version(tmp_path):
    session_id = "GC-STORAGE-VERSION-CHECK"
    result = _canonicalise(tmp_path, session_id)
    row = read_lineage_record(tmp_path, session_id)
    assert row.canonical_storage_layout_version == CANONICAL_STORAGE_LAYOUT_VERSION
    assert result.canonical_event_set_hash  # sanity: a genuine result was produced
