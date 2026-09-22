"""HMT-2 canonicalisation checkpoint — tests for
`market_truth.acquisition.canonical_quality_record`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from market_truth.acquisition.canonical_quality_record import (  # noqa: E402
    QualityRecordError,
    SessionQualityCounters,
    quality_record_exists,
    quality_record_relative_path,
    read_quality_record,
    write_quality_record_atomic,
)


class _FakeAdapterCounters:
    total_native_records = 100
    unmapped_symbol_records = 0
    crossed_book_skipped_records = 1
    incomplete_book_skipped_records = 2
    bad_receive_time_records = 3


def test_defaults_are_all_zero():
    counters = SessionQualityCounters(session_id="GC-2026-01-01")
    d = counters.to_dict()
    assert d["native_record_count"] == 0
    assert d["duplicate_count"] == 0
    assert d["conflict_count"] == 0
    assert d["source_sequence_anomaly_count"] == 0
    assert d["observed_contract_count"] == 0
    assert d["source_gap_completeness_status"] == "COMPLETE"


def test_source_gap_completeness_status_flips_on_any_anomaly():
    counters = SessionQualityCounters(session_id="GC-2026-01-01")
    counters.source_sequence_anomaly_count = 1
    assert counters.source_gap_completeness_status == "GAP_OR_ANOMALY_SUSPECTED"


def test_observed_contract_count_matches_set_size():
    counters = SessionQualityCounters(session_id="GC-2026-01-01")
    counters.observed_contract_ids = {"COMEX:GC:2026-12", "COMEX:GC:2027-02"}
    assert counters.observed_contract_count == 2
    assert counters.to_dict()["observed_contract_ids"] == ["COMEX:GC:2026-12", "COMEX:GC:2027-02"]


def test_apply_adapter_counters_reuses_pilot_adapter_fields():
    counters = SessionQualityCounters(session_id="GC-2026-01-01")
    counters.apply_adapter_counters(_FakeAdapterCounters())
    d = counters.to_dict()
    assert d["native_record_count"] == 100
    assert d["crossed_book_skipped_count"] == 1
    assert d["incomplete_book_skipped_count"] == 2
    assert d["bad_receive_time_count"] == 3


def test_write_then_read_round_trips(tmp_path):
    counters = SessionQualityCounters(session_id="GC-2026-01-01")
    counters.duplicate_count = 5
    written_path = write_quality_record_atomic(tmp_path, counters)
    assert written_path == tmp_path / quality_record_relative_path("GC-2026-01-01")
    assert quality_record_exists(tmp_path, "GC-2026-01-01") is True

    doc = read_quality_record(tmp_path, "GC-2026-01-01")
    assert doc["session_id"] == "GC-2026-01-01"
    assert doc["duplicate_count"] == 5


def test_write_never_leaves_a_tmp_file_behind(tmp_path):
    counters = SessionQualityCounters(session_id="GC-2026-01-01")
    write_quality_record_atomic(tmp_path, counters)
    tmp_files = list((tmp_path / "quality").glob("*.tmp"))
    assert tmp_files == []


def test_read_missing_record_fails_closed(tmp_path):
    with pytest.raises(QualityRecordError):
        read_quality_record(tmp_path, "GC-NEVER-WRITTEN")


def test_quality_record_relative_path_escapes_unsafe_characters():
    """The unsafe character that actually matters for path-traversal safety is `/` (it is what
    would let a session_id introduce an extra path segment) — `_safe_session_segment` escapes
    it, along with everything else that is not alphanumeric/`-_.:`. `.` itself is permitted
    (session ids are dotted-free in practice, but a literal `.` alone is not a traversal risk
    once every `/` in the input has been removed), so a "GC/../evil" input becomes ONE safe,
    single-segment filename stem, never spanning an extra directory level."""
    path = quality_record_relative_path("GC/../evil")
    assert path.startswith("quality/")
    stem = path[len("quality/"):-len(".json")]
    assert "/" not in stem
    assert path.count("/") == 1  # exactly the one "quality/" separator -- no extra segment smuggled in
