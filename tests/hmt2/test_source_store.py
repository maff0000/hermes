"""HMT-2B — tests for market_truth.acquisition.source_store.

Every `NativeArtefactRecord` and every byte payload used here is CLEARLY SYNTHETIC — this
checkpoint has no real acquired GC MBP-1 data (see source_store.py module docstring). Nothing
in this test file represents, or is claimed to represent, real GC data.
"""
from __future__ import annotations

import pytest

from market_truth.acquisition.source_store import (
    ImmutableArtefactError,
    NativeArtefactRecord,
    NativeSourceStore,
    SourceStoreError,
    artefact_relative_path,
    manifest_relative_path,
)

_SYNTHETIC_BYTES = b"__SYNTHETIC_HMT2B_PLACEHOLDER_NOT_REAL_GC_DATA__" * 4


def _make_record(**overrides) -> NativeArtefactRecord:
    defaults = dict(
        corpus_version="hmt2-corpus-v1",
        session_id="GC-2026-01-05",
        provider_id="databento",
        dataset_id="GLBX.MDP3",
        schema="mbp-1",
        request_identity="req-synthetic-0001",
        requested_start_utc="2026-01-04T22:00:00+00:00",
        requested_end_utc="2026-01-05T21:00:00+00:00",
        provider_raw_symbols=("GCZ26",),
        canonical_contract_mapping_ref="hmt1-gc-contract-mapping-v1:GCZ26",
        object_relative_path=artefact_relative_path("GC-2026-01-05", "source", "mbp1.bin"),
        byte_size=len(_SYNTHETIC_BYTES),
        sha256="0" * 64,
        source_condition="SYNTHETIC_PLACEHOLDER",
        acquisition_utc="2026-09-21T00:00:00+00:00",
        synthetic=True,
    )
    defaults.update(overrides)
    return NativeArtefactRecord(**defaults)


# ----------------------------------------------------------------------------------------------
# Path helpers
# ----------------------------------------------------------------------------------------------

def test_artefact_relative_path_uses_the_documented_layout():
    path = artefact_relative_path("GC-2026-01-05", "source", "mbp1.bin")
    assert path == "hmt2-gc-mbp1-v1/sessions/GC-2026-01-05/source/mbp1.bin"


def test_artefact_relative_path_rejects_unknown_category():
    with pytest.raises(SourceStoreError):
        artefact_relative_path("GC-2026-01-05", "not-a-real-category", "x.bin")


def test_manifest_relative_path():
    assert manifest_relative_path("corpus-manifest-v2.json") == "hmt2-gc-mbp1-v1/manifest/corpus-manifest-v2.json"


def test_artefact_relative_path_escapes_unsafe_segments():
    path = artefact_relative_path("GC 2026 01 05!", "source", "file name.bin")
    # Exactly 5 path components (root/sessions/session/category/filename) - no raw space or
    # extra separator survived inside an escaped segment.
    assert path.count("/") == 4
    assert " " not in path
    assert "!" not in path


# ----------------------------------------------------------------------------------------------
# NativeArtefactRecord — fails closed on missing evidence metadata
# ----------------------------------------------------------------------------------------------

def test_native_artefact_record_requires_object_path_and_hash():
    with pytest.raises(SourceStoreError):
        _make_record(object_relative_path="", sha256="")
    with pytest.raises(SourceStoreError):
        _make_record(sha256="")
    with pytest.raises(SourceStoreError):
        _make_record(object_relative_path="")


def test_native_artefact_record_requires_non_empty_session_id():
    with pytest.raises(SourceStoreError):
        _make_record(session_id="")


def test_native_artefact_record_rejects_negative_byte_size():
    with pytest.raises(SourceStoreError):
        _make_record(byte_size=-1)


def test_native_artefact_record_is_synthetic_by_default():
    record = _make_record()
    assert record.synthetic is True


def test_native_artefact_record_to_dict_round_trips_symbols_as_list():
    record = _make_record()
    d = record.to_dict()
    assert d["provider_raw_symbols"] == ["GCZ26"]
    assert d["synthetic"] is True


def test_native_artefact_record_content_sha256_is_deterministic_and_order_sensitive():
    a = _make_record()
    b = _make_record()
    assert a.content_sha256() == b.content_sha256()

    c = _make_record(byte_size=a.byte_size + 1)
    assert c.content_sha256() != a.content_sha256()


# ----------------------------------------------------------------------------------------------
# NativeSourceStore — write-once, immutable, integrity-checked
# ----------------------------------------------------------------------------------------------

def test_write_and_read_artefact_round_trips(tmp_path):
    store = NativeSourceStore(tmp_path)
    rel_path = artefact_relative_path("GC-2026-01-05", "source", "mbp1.bin")
    digest = store.write_artefact(rel_path, _SYNTHETIC_BYTES)
    assert len(digest) == 64
    assert store.artefact_exists(rel_path)
    assert store.read_artefact(rel_path) == _SYNTHETIC_BYTES


def test_second_write_to_same_path_is_refused_even_with_identical_bytes(tmp_path):
    store = NativeSourceStore(tmp_path)
    rel_path = artefact_relative_path("GC-2026-01-05", "source", "mbp1.bin")
    store.write_artefact(rel_path, _SYNTHETIC_BYTES)
    with pytest.raises(ImmutableArtefactError):
        store.write_artefact(rel_path, _SYNTHETIC_BYTES)


def test_second_write_to_same_path_is_refused_even_with_different_bytes(tmp_path):
    store = NativeSourceStore(tmp_path)
    rel_path = artefact_relative_path("GC-2026-01-05", "source", "mbp1.bin")
    store.write_artefact(rel_path, _SYNTHETIC_BYTES)
    with pytest.raises(ImmutableArtefactError):
        store.write_artefact(rel_path, _SYNTHETIC_BYTES + b"more")


def test_write_refused_if_file_already_exists_on_disk_from_outside_this_store_instance(tmp_path):
    """A fresh NativeSourceStore instance (no in-memory history) must still refuse to overwrite
    a file that is already present on disk — immutability is a filesystem-level guarantee, not
    merely an in-memory one."""
    rel_path = artefact_relative_path("GC-2026-01-05", "source", "mbp1.bin")
    full_path = tmp_path / rel_path
    full_path.parent.mkdir(parents=True)
    full_path.write_bytes(_SYNTHETIC_BYTES)

    fresh_store = NativeSourceStore(tmp_path)
    with pytest.raises(ImmutableArtefactError):
        fresh_store.write_artefact(rel_path, _SYNTHETIC_BYTES)


def test_read_missing_artefact_fails_closed(tmp_path):
    store = NativeSourceStore(tmp_path)
    with pytest.raises(SourceStoreError):
        store.read_artefact(artefact_relative_path("GC-2026-01-05", "source", "missing.bin"))


def test_verify_artefact_integrity_passes_for_matching_hash(tmp_path):
    store = NativeSourceStore(tmp_path)
    rel_path = artefact_relative_path("GC-2026-01-05", "source", "mbp1.bin")
    digest = store.write_artefact(rel_path, _SYNTHETIC_BYTES)
    store.verify_artefact_integrity(rel_path, digest)  # must not raise


def test_verify_artefact_integrity_fails_closed_on_mismatch(tmp_path):
    store = NativeSourceStore(tmp_path)
    rel_path = artefact_relative_path("GC-2026-01-05", "source", "mbp1.bin")
    store.write_artefact(rel_path, _SYNTHETIC_BYTES)
    with pytest.raises(SourceStoreError):
        store.verify_artefact_integrity(rel_path, "0" * 64)


def test_multiple_sessions_and_categories_do_not_collide(tmp_path):
    store = NativeSourceStore(tmp_path)
    p1 = artefact_relative_path("GC-2026-01-05", "source", "mbp1.bin")
    p2 = artefact_relative_path("GC-2026-01-06", "source", "mbp1.bin")
    p3 = artefact_relative_path("GC-2026-01-05", "definitions", "def.json")
    store.write_artefact(p1, _SYNTHETIC_BYTES)
    store.write_artefact(p2, _SYNTHETIC_BYTES + b"x")
    store.write_artefact(p3, b"synthetic-definition-payload")
    assert store.read_artefact(p1) == _SYNTHETIC_BYTES
    assert store.read_artefact(p2) == _SYNTHETIC_BYTES + b"x"
    assert store.read_artefact(p3) == b"synthetic-definition-payload"
