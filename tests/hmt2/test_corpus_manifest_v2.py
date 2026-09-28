"""HMT-2 (real-money checkpoint) — corpus_manifest_v2.py schema/hashing/round-trip tests.
Mirrors tests/hmt2/test_corpus_manifest.py's own pattern for the v1 schema."""
import json

import pytest

from market_truth.acquisition.corpus_manifest_v2 import (
    ManifestIntegrityErrorV2,
    ManifestMetadataV2,
    ManifestRowV2,
    build_manifest_document_v2,
    manifest_hash_v2,
    verify_manifest_integrity_v2,
    write_manifest_v2,
)


def _metadata():
    return ManifestMetadataV2(
        corpus_version="test-corpus-v1",
        manifest_schema_version="hmt2-corpus-selection-manifest-v2",
        selection_algorithm_version="test-algo-v2",
        calendar_version="test-cal-v1",
        session_universe_start="2017-05-21",
        session_universe_end="2026-09-18",
        seed_development_random_hex="a" * 64,
        seed_protected_holdout_hex="b" * 64,
        stratum_precedence_order=("PROTECTED_HOLDOUT", "SCHEDULED_EVENT"),
        superseded_manifest_relative_path="research/hmt2/corpus-selection-manifest-v1.json",
        superseded_manifest_sha256="f" * 64,
        event_snapshot_version="hmt2-scheduled-macro-event-snapshot-v2",
        reference_snapshot_version="hmt2-reference-series-v2",
        generated_by="test",
        base_sha="0" * 40,
        notes="test",
    )


def _rows():
    return [
        ManifestRowV2(
            corpus_version="test-corpus-v1",
            session_id="GC-2026-01-05",
            gc_trade_date="2026-01-05",
            request_start_utc="2026-01-04T22:00:00+00:00",
            request_end_utc="2026-01-05T21:00:00+00:00",
            primary_stratum="HIGH_VOL_NON_EVENT",
            protected_holdout=False,
            selection_algorithm_version="test-algo-v2",
            calendar_version="test-cal-v1",
            inclusion_reason="reference_series_top_20pct_within_year",
            session_log_range=0.0234,
            reference_quality="REFERENCE_COMPLETE",
            within_year_rank=3,
            within_year_eligible_count=200,
            within_year_percentile=0.985,
            continuous_underlying_instrument_ids=(12345,),
            reference_snapshot_version="hmt2-reference-series-v2",
        )
    ]


def test_manifest_v2_hash_is_deterministic_for_same_inputs():
    metadata, rows = _metadata(), _rows()
    h1 = manifest_hash_v2(metadata, rows)
    h2 = manifest_hash_v2(metadata, rows)
    assert h1 == h2
    assert len(h1) == 64


def test_manifest_v2_hash_changes_if_a_row_field_changes():
    metadata, rows = _metadata(), _rows()
    h1 = manifest_hash_v2(metadata, rows)
    mutated = [ManifestRowV2(**{**rows[0].to_dict(), "session_log_range": 9.999})]
    h2 = manifest_hash_v2(metadata, mutated)
    assert h1 != h2


def test_build_manifest_document_v2_includes_hash_and_rows():
    metadata, rows = _metadata(), _rows()
    doc = build_manifest_document_v2(metadata, rows)
    assert doc["manifest_sha256"] == manifest_hash_v2(metadata, rows)
    assert len(doc["rows"]) == 1
    assert doc["metadata"]["superseded_manifest_sha256"] == "f" * 64


def test_write_and_verify_round_trip(tmp_path):
    metadata, rows = _metadata(), _rows()
    out_path = tmp_path / "manifest_v2.json"
    digest = write_manifest_v2(str(out_path), metadata, rows)

    with open(out_path, encoding="utf-8") as f:
        document = json.load(f)
    assert document["manifest_sha256"] == digest
    verify_manifest_integrity_v2(document)  # must not raise


def test_verify_manifest_integrity_v2_fails_closed_on_tampering(tmp_path):
    metadata, rows = _metadata(), _rows()
    out_path = tmp_path / "manifest_v2.json"
    write_manifest_v2(str(out_path), metadata, rows)

    with open(out_path, encoding="utf-8") as f:
        document = json.load(f)
    document["rows"][0]["session_log_range"] = 123.456  # tamper after the fact
    with pytest.raises(ManifestIntegrityErrorV2):
        verify_manifest_integrity_v2(document)


def test_row_to_dict_serializes_instrument_ids_as_a_list_not_a_tuple():
    row = _rows()[0]
    d = row.to_dict()
    assert d["continuous_underlying_instrument_ids"] == [12345]
    assert isinstance(d["continuous_underlying_instrument_ids"], list)


def test_row_with_none_optional_fields_serializes_cleanly():
    row = ManifestRowV2(
        corpus_version="test-corpus-v1",
        session_id="GC-2026-02-02",
        gc_trade_date="2026-02-02",
        request_start_utc="2026-02-01T22:00:00+00:00",
        request_end_utc="2026-02-02T21:00:00+00:00",
        primary_stratum="RANDOM_DEVELOPMENT",
        protected_holdout=False,
        selection_algorithm_version="test-algo-v2",
        calendar_version="test-cal-v1",
        inclusion_reason="deterministic_random_draw",
    )
    d = row.to_dict()
    assert d["session_log_range"] is None
    assert d["continuous_underlying_instrument_ids"] is None
