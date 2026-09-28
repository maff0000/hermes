"""HMT-2A — corpus_manifest.py schema/hashing/round-trip tests."""
import json

import pytest

from market_truth.acquisition.corpus_manifest import (
    ManifestIntegrityError,
    ManifestMetadata,
    ManifestRow,
    build_manifest_document,
    manifest_hash,
    verify_manifest_integrity,
    write_manifest,
)


def _metadata():
    return ManifestMetadata(
        corpus_version="test-corpus-v1",
        manifest_schema_version="hmt2-corpus-selection-manifest-v1",
        selection_algorithm_version="test-algo-v1",
        calendar_version="test-cal-v1",
        session_universe_start="2024-01-01",
        session_universe_end="2024-01-31",
        seed_development_random_hex="a" * 64,
        seed_protected_holdout_hex="b" * 64,
        stratum_precedence_order=("PROTECTED_HOLDOUT", "SCHEDULED_EVENT"),
        pending_strata=("HIGH_VOL_NON_EVENT", "COMPRESSION"),
        generated_by="test",
        base_sha="0" * 40,
        notes="test",
    )


def _rows():
    return [
        ManifestRow(
            corpus_version="test-corpus-v1",
            session_id="GC-2024-01-02",
            gc_trade_date="2024-01-02",
            request_start_utc="2024-01-01T23:00:00+00:00",
            request_end_utc="2024-01-02T22:00:00+00:00",
            primary_stratum="SCHEDULED_EVENT",
            protected_holdout=False,
            selection_algorithm_version="test-algo-v1",
            calendar_version="test-cal-v1",
            inclusion_reason="test",
            event_class="CPI_RELEASE",
        )
    ]


def test_manifest_hash_is_deterministic_for_same_inputs():
    metadata, rows = _metadata(), _rows()
    h1 = manifest_hash(metadata, rows)
    h2 = manifest_hash(metadata, rows)
    assert h1 == h2
    assert len(h1) == 64


def test_manifest_hash_changes_if_a_row_changes():
    metadata, rows = _metadata(), _rows()
    h1 = manifest_hash(metadata, rows)
    mutated = [ManifestRow(**{**rows[0].to_dict(), "inclusion_reason": "different"})]
    h2 = manifest_hash(metadata, mutated)
    assert h1 != h2


def test_write_and_read_round_trip(tmp_path):
    metadata, rows = _metadata(), _rows()
    out = tmp_path / "manifest.json"
    digest = write_manifest(str(out), metadata, rows)
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["manifest_sha256"] == digest
    assert loaded["rows"][0]["session_id"] == "GC-2024-01-02"
    verify_manifest_integrity(loaded)  # must not raise


def test_verify_manifest_integrity_fails_closed_on_tamper(tmp_path):
    metadata, rows = _metadata(), _rows()
    out = tmp_path / "manifest.json"
    write_manifest(str(out), metadata, rows)
    document = json.loads(out.read_text(encoding="utf-8"))
    document["rows"][0]["inclusion_reason"] = "tampered"
    with pytest.raises(ManifestIntegrityError):
        verify_manifest_integrity(document)


def test_build_manifest_document_shape():
    metadata, rows = _metadata(), _rows()
    document = build_manifest_document(metadata, rows)
    assert set(document.keys()) == {"metadata", "manifest_sha256", "rows"}
    assert document["metadata"]["pending_strata"] == ["HIGH_VOL_NON_EVENT", "COMPRESSION"]
