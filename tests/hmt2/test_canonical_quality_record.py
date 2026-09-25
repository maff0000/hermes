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
    EMPTY_REASON_NO_CANONICAL_EMISSIONS_AFTER_VALID_PROCESSING,
    EMPTY_REASON_SOURCE_RETURNED_ZERO_RECORDS,
    QualityRecordError,
    RESULT_KIND_EMPTY_VALID,
    RESULT_KIND_NONEMPTY,
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


class _FakeAdapterCountersWithSymbols(_FakeAdapterCounters):
    source_observed_raw_symbols = {"GCZ26", "GCM26"}


class _FakeAdapterCountersWithMaybeBadBook(_FakeAdapterCounters):
    maybe_bad_book_records = 5


def test_defaults_are_all_zero():
    counters = SessionQualityCounters(session_id="GC-2026-01-01")
    d = counters.to_dict()
    assert d["native_record_count"] == 0
    assert d["duplicate_count"] == 0
    assert d["conflict_count"] == 0
    assert d["sequence_non_monotonic_per_symbol_count"] == 0
    assert d["source_channel_maybe_bad_book_count"] == 0
    assert d["observed_contract_count"] == 0
    assert d["source_gap_completeness_status"] == "COMPLETE"


# ------------------------------------------------------------------------------------------------
# v3 quality-instrumentation correction — source_gap_completeness_status is now driven by the
# REAL, Databento-decoded channel-level MAYBE_BAD_BOOK signal (source_channel_maybe_bad_book_
# count), NEVER by the per-symbol sequence diagnostic (sequence_non_monotonic_per_symbol_count),
# which is a genuine false-positive: Databento's `sequence` field is a CHANNEL-level (not
# per-symbol) venue counter, so filtering it to one symbol looks non-monotonic under completely
# normal cross-symbol interleaving on the same channel.
# ------------------------------------------------------------------------------------------------

def test_source_gap_completeness_status_flips_on_a_real_channel_gap():
    counters = SessionQualityCounters(session_id="GC-2026-01-01")
    counters.source_channel_maybe_bad_book_count = 1
    assert counters.source_gap_completeness_status == "GAP_OR_ANOMALY_SUSPECTED"


def test_source_gap_completeness_status_stays_complete_despite_per_symbol_sequence_diagnostic():
    """The exact false-positive this v3 correction fixes: a large per-symbol sequence-
    diagnostic count (e.g. from normal cross-symbol channel interleaving) must NEVER, by
    itself, flip the gap-completeness status."""
    counters = SessionQualityCounters(session_id="GC-2026-01-01")
    counters.sequence_non_monotonic_per_symbol_count = 4467
    assert counters.source_gap_completeness_status == "COMPLETE"
    d = counters.to_dict()
    assert d["sequence_non_monotonic_per_symbol_count"] == 4467
    assert d["source_gap_completeness_status"] == "COMPLETE"


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
    assert d["source_channel_maybe_bad_book_count"] == 0  # attribute absent on this fake -> 0


def test_apply_adapter_counters_wires_through_the_real_channel_gap_signal():
    counters = SessionQualityCounters(session_id="GC-2026-01-01")
    counters.apply_adapter_counters(_FakeAdapterCountersWithMaybeBadBook())
    d = counters.to_dict()
    assert d["source_channel_maybe_bad_book_count"] == 5
    assert d["source_gap_completeness_status"] == "GAP_OR_ANOMALY_SUSPECTED"


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


# ------------------------------------------------------------------------------------------------
# Valid-empty architecture ruling additions — source_observed_symbols / source_resolved_
# contract_ids / canonical_result_kind / empty_reason, all additive.
# ------------------------------------------------------------------------------------------------

def test_valid_empty_fields_default_empty_or_none():
    counters = SessionQualityCounters(session_id="GC-2026-01-01")
    d = counters.to_dict()
    assert d["source_observed_symbols"] == []
    assert d["source_resolved_contract_count"] == 0
    assert d["source_resolved_contract_ids"] == []
    assert d["canonical_result_kind"] is None
    assert d["empty_reason"] is None
    assert d["canonical_event_count"] == 0


def test_canonical_event_count_sums_trade_and_top_of_book():
    counters = SessionQualityCounters(session_id="GC-2026-01-01")
    counters.market_trade_event_count = 3
    counters.top_of_book_event_count = 2
    assert counters.canonical_event_count == 5
    assert counters.to_dict()["canonical_event_count"] == 5


def test_source_resolved_contract_count_matches_set_size():
    counters = SessionQualityCounters(session_id="GC-2026-01-01")
    counters.source_resolved_contract_ids = {"COMEX:GC:2026-12", "COMEX:GC:2027-02"}
    assert counters.source_resolved_contract_count == 2
    assert counters.to_dict()["source_resolved_contract_ids"] == ["COMEX:GC:2026-12", "COMEX:GC:2027-02"]


def test_canonical_emitted_contract_aliases_mirror_observed_contract_ids():
    """`observed_contract_ids`/`observed_contract_count` keep their EXACT original meaning; the
    architecture ruling's own vocabulary is exposed as an alias, never a second computation."""
    counters = SessionQualityCounters(session_id="GC-2026-01-01")
    counters.observed_contract_ids = {"COMEX:GC:2026-12"}
    d = counters.to_dict()
    assert d["canonical_emitted_contract_ids"] == d["observed_contract_ids"]
    assert d["canonical_emitted_contract_count"] == d["observed_contract_count"] == 1


def test_result_kind_and_empty_reason_round_trip():
    counters = SessionQualityCounters(session_id="GC-2026-01-01")
    counters.canonical_result_kind = RESULT_KIND_EMPTY_VALID
    counters.empty_reason = EMPTY_REASON_NO_CANONICAL_EMISSIONS_AFTER_VALID_PROCESSING
    d = counters.to_dict()
    assert d["canonical_result_kind"] == RESULT_KIND_EMPTY_VALID
    assert d["empty_reason"] == EMPTY_REASON_NO_CANONICAL_EMISSIONS_AFTER_VALID_PROCESSING


def test_apply_adapter_counters_merges_source_observed_raw_symbols():
    counters = SessionQualityCounters(session_id="GC-2026-01-01")
    counters.apply_adapter_counters(_FakeAdapterCountersWithSymbols())
    assert counters.source_observed_symbols == {"GCZ26", "GCM26"}


def test_apply_adapter_counters_tolerates_missing_source_observed_raw_symbols_attribute():
    """Defensive/additive: an adapter-counters object that predates this field (e.g. the plain
    `_FakeAdapterCounters` test double above) is still accepted without error."""
    counters = SessionQualityCounters(session_id="GC-2026-01-01")
    counters.apply_adapter_counters(_FakeAdapterCounters())
    assert counters.source_observed_symbols == set()
