"""HMT-2 canonicalisation checkpoint — tests for `market_truth.acquisition.canonical_worker`.

Uses ONLY zero-vendor-dependency fixtures already committed for HMT-1
(`tests/fixtures/hmt1/gc_contract_mapping_v1.json`, `tests/fixtures/hmt1/gc_mbp1_equivalent_v1.
jsonl`, replayed via `market_truth.providers.fixture.FixtureMarketDataProvider`) — this is what
lets `canonicalise_records()` (the provider-agnostic core) be exercised fully in CI, with no real
retained MBP-1 bytes and no vendor SDK anywhere in this file. The REAL-data pilot-reproduction
gate lives in a separate file (`test_hmt2i_pilot_reproduction.py`) that skips cleanly when the
real retained artefacts are not present (e.g. a bare CI checkout).
"""
from __future__ import annotations

import dataclasses
import hashlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from market_truth.acquisition import canonical_worker as cw  # noqa: E402
from market_truth.acquisition.canonical_quality_record import (  # noqa: E402
    EMPTY_REASON_NO_CANONICAL_EMISSIONS_AFTER_VALID_PROCESSING,
    EMPTY_REASON_SOURCE_RETURNED_ZERO_RECORDS,
    RESULT_KIND_EMPTY_VALID,
    RESULT_KIND_NONEMPTY,
    SessionQualityCounters,
)
from market_truth.acquisition.lineage import LineageError, read_lineage_record  # noqa: E402
from market_truth.canonicaliser import DuplicateConflictError  # noqa: E402
from market_truth.futures import ContractMappingTable  # noqa: E402
from market_truth.providers.databento_mbp1 import Mbp1AdapterQualityCounters  # noqa: E402
from market_truth.providers.fixture import FixtureMarketDataProvider  # noqa: E402

FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "hmt1"
MAPPING_PATH = FIXTURES_DIR / "gc_contract_mapping_v1.json"
GC_FIXTURE = FIXTURES_DIR / "gc_mbp1_equivalent_v1.jsonl"

SESSION_ID = "GC-TEST-2026-09-15"
PROVIDER_ID = "hmt1-fixture-provider"
DATASET_ID = "hmt1-test"


def _fixture_records():
    provider = FixtureMarketDataProvider(GC_FIXTURE, provider_id=PROVIDER_ID, dataset_id=DATASET_ID)
    return list(provider.iter_records()), provider.content_sha256()


def _mapping_table():
    return ContractMappingTable.from_json_file(MAPPING_PATH)


def _canonicalise(canonical_store_root, *, session_id=SESSION_ID):
    records, native_sha256 = _fixture_records()
    quality_counters = SessionQualityCounters(session_id=session_id)
    result = cw.canonicalise_records(
        session_id=session_id,
        records=records,
        mapping_table=_mapping_table(),
        canonical_store_root=canonical_store_root,
        native_artefact_relative_path="hmt2-gc-mbp1-v1/sessions/%s/source/fixture.jsonl" % session_id,
        native_artefact_sha256=native_sha256,
        provider_request_identity="test-request-identity-0001",
        provider_definition_ref="tests/fixtures/hmt1/gc_contract_mapping_v1.json",
        provider_adapter_version="hmt1-fixture-provider-v1",
        fixture_schema_version="hmt1-fixture-line-schema-v1",
        corpus_manifest_ref="research/hmt2/corpus-selection-manifest-v2.json",
        quality_counters=quality_counters,
    )
    return result, native_sha256


# ------------------------------------------------------------------------------------------------
# resolve_canonical_research_root — external config, no hardcoded path.
# ------------------------------------------------------------------------------------------------

def test_resolve_canonical_research_root_defaults_under_repo_root():
    root = cw.resolve_canonical_research_root(repo_root=Path("/some/repo"))
    assert root == Path("/some/repo") / cw.DEFAULT_CANONICAL_RESEARCH_ROOT_DIRNAME


def test_resolve_canonical_research_root_honours_explicit_argument():
    assert cw.resolve_canonical_research_root("/explicit/path") == Path("/explicit/path")


def test_resolve_canonical_research_root_honours_env_var(monkeypatch):
    monkeypatch.setenv(cw.CANONICAL_RESEARCH_ROOT_ENV_VAR, "/from/env")
    assert cw.resolve_canonical_research_root() == Path("/from/env")


def test_explicit_argument_wins_over_env_var(monkeypatch):
    monkeypatch.setenv(cw.CANONICAL_RESEARCH_ROOT_ENV_VAR, "/from/env")
    assert cw.resolve_canonical_research_root("/explicit") == Path("/explicit")


# ------------------------------------------------------------------------------------------------
# canonicalise_records — happy path: partitions + evidence + lineage + quality, all durable.
# ------------------------------------------------------------------------------------------------

def test_happy_path_produces_partitions_evidence_lineage_quality(tmp_path):
    result, native_sha256 = _canonicalise(tmp_path)

    assert result.session_id == SESSION_ID
    assert result.native_artefact_sha256 == native_sha256
    assert result.canonical_event_set_hash
    assert result.partition_relative_paths
    for rel in result.partition_relative_paths:
        # storage-layout defect remediation: every promoted partition's relative path is now
        # namespaced by session_id, physically stored under CANONICAL_STORAGE_ROOT_DIRNAME
        # ("canonical-v2"), never the old, unnamespaced "canonical/" tree.
        assert rel.startswith(f"session_id={SESSION_ID}/")
        assert (tmp_path / cw.CANONICAL_STORAGE_ROOT_DIRNAME / rel).exists()
    assert (tmp_path / result.evidence_manifest_relative_path).exists()
    assert (tmp_path / result.lineage_record_relative_path).exists()
    assert (tmp_path / result.quality_record_relative_path).exists()

    # no leftover staging directory
    assert not any((tmp_path / ".staging").glob("*")) if (tmp_path / ".staging").exists() else True

    from market_truth.acquisition.lineage import CANONICAL_STORAGE_LAYOUT_VERSION, read_lineage_record

    row = read_lineage_record(tmp_path, SESSION_ID)
    assert row.synthetic is False
    assert row.canonical_event_set_hash == result.canonical_event_set_hash
    assert row.native_artefact_sha256 == native_sha256
    assert row.evidence_manifest_deterministic_hash == result.evidence_manifest_deterministic_hash
    assert row.quality_record_ref == result.quality_record_relative_path
    assert row.canonical_storage_layout_version == CANONICAL_STORAGE_LAYOUT_VERSION


def test_quality_record_reflects_real_counts(tmp_path):
    result, _ = _canonicalise(tmp_path)
    summary = result.quality_summary
    assert summary["native_record_count"] == 7  # tests/fixtures/hmt1/gc_mbp1_equivalent_v1.jsonl: 5 TOB + 2 TRADE
    assert summary["source_gap_completeness_status"] in ("COMPLETE", "GAP_OR_ANOMALY_SUSPECTED")
    assert summary["duplicate_count"] == 0
    assert summary["conflict_count"] == 0


def test_cross_symbol_channel_interleaving_no_longer_flips_gap_status(tmp_path):
    """v3 quality-instrumentation correction — Databento's `sequence` field is a CHANNEL-level
    (not per-symbol) venue counter shared across every instrument multiplexed on that channel
    (confirmed against Databento's own schema docs and CME MDP 3.0's own docs, identically
    worded across MBO/MBP-1/MBP-10/Trade: "the message sequence number assigned at the venue").
    A purely per-symbol view of that shared counter can look non-monotonic even when nothing is
    actually wrong — this is the exact false positive that previously flipped
    `source_gap_completeness_status` to GAP_OR_ANOMALY_SUSPECTED on essentially every real
    session. This synthetic fixture (dataclasses.replace on the committed HMT-1 fixture's own
    first, already-valid record — never real retained bytes) mimics that pattern deterministically:
    two symbols multiplexed on the same channel where GCM26's own per-symbol sequence view goes
    101 -> 99."""
    base_records, native_sha256 = _fixture_records()
    base = base_records[0]  # a valid TOP_OF_BOOK_UPDATE record (GCZ26)

    interleaved = [
        dataclasses.replace(base, raw_provider_symbol="GCZ26", source_sequence=100, source_artifact_id="synthetic-0001"),
        dataclasses.replace(base, raw_provider_symbol="GCM26", source_sequence=101, source_artifact_id="synthetic-0002"),
        dataclasses.replace(base, raw_provider_symbol="GCZ26", source_sequence=102, source_artifact_id="synthetic-0003"),
        # The exact false-positive this correction fixes: GCM26's own per-symbol sequence view
        # regresses (101 -> 99) purely because the underlying counter is shared across the whole
        # channel, never owned by GCM26 alone -- not a real gap.
        dataclasses.replace(base, raw_provider_symbol="GCM26", source_sequence=99, source_artifact_id="synthetic-0004"),
    ]

    quality_counters = SessionQualityCounters(session_id="GC-INTERLEAVE-TEST")
    result = cw.canonicalise_records(
        session_id="GC-INTERLEAVE-TEST", records=interleaved, mapping_table=_mapping_table(),
        canonical_store_root=tmp_path,
        native_artefact_relative_path="hmt2-gc-mbp1-v1/sessions/GC-INTERLEAVE-TEST/source/fixture.jsonl",
        native_artefact_sha256=native_sha256, provider_request_identity="req-interleave-0001",
        provider_definition_ref="tests/fixtures/hmt1/gc_contract_mapping_v1.json",
        provider_adapter_version="hmt1-fixture-provider-v1",
        fixture_schema_version="hmt1-fixture-line-schema-v1",
        corpus_manifest_ref="research/hmt2/corpus-selection-manifest-v2.json",
        quality_counters=quality_counters,
    )

    # The OLD, incorrect classifier would have flagged this GAP_OR_ANOMALY_SUSPECTED purely from
    # the per-symbol view of GCM26's sequence going 101 -> 99. The corrected classifier must not.
    assert result.quality_summary["source_gap_completeness_status"] == "COMPLETE"
    # ... but the raw per-symbol diagnostic still honestly counts it -- never deleted, only
    # relabelled/disclosed as non-authoritative for gap status (module docstring discipline:
    # "never delete, only disclose").
    assert result.quality_summary["sequence_non_monotonic_per_symbol_count"] == 1
    assert result.quality_summary["source_channel_maybe_bad_book_count"] == 0


def test_two_sessions_get_independent_fresh_canonicaliser_state(tmp_path):
    """Session-scoped (WO Part 4): running the SAME fixture twice, as two different session ids,
    must not let book-state leak from one session into the other."""
    result_a, _ = _canonicalise(tmp_path, session_id="GC-TEST-A")
    result_b, _ = _canonicalise(tmp_path, session_id="GC-TEST-B")
    assert result_a.canonical_event_set_hash == result_b.canonical_event_set_hash  # same input, same result
    assert result_a.lineage_record_relative_path != result_b.lineage_record_relative_path


# ------------------------------------------------------------------------------------------------
# Idempotent reprocessing — verify-and-skip vs. redo.
# ------------------------------------------------------------------------------------------------

def test_verify_existing_completion_true_for_a_genuinely_complete_session(tmp_path):
    result, native_sha256 = _canonicalise(tmp_path)
    native_path = GC_FIXTURE  # stand-in "native artefact" for this fixture-based test

    assert cw.verify_existing_completion(
        canonical_store_root=tmp_path, session_id=SESSION_ID,
        native_artefact_path=native_path, expected_native_sha256=native_sha256,
    ) is True


def test_verify_existing_completion_false_when_never_processed(tmp_path):
    assert cw.verify_existing_completion(
        canonical_store_root=tmp_path, session_id="GC-NEVER-PROCESSED",
        native_artefact_path=GC_FIXTURE, expected_native_sha256="0" * 64,
    ) is False


def test_verify_existing_completion_fails_closed_on_native_hash_drift(tmp_path):
    result, native_sha256 = _canonicalise(tmp_path)
    with pytest.raises(cw.VerificationFailedError):
        cw.verify_existing_completion(
            canonical_store_root=tmp_path, session_id=SESSION_ID,
            native_artefact_path=GC_FIXTURE, expected_native_sha256="f" * 64,
        )


def test_verify_existing_completion_fails_closed_on_partition_tamper(tmp_path):
    result, native_sha256 = _canonicalise(tmp_path)
    tampered = tmp_path / cw.CANONICAL_STORAGE_ROOT_DIRNAME / sorted(result.partition_relative_paths)[0]
    tampered.write_bytes(b"TAMPERED BYTES, NOT A REAL PARQUET FILE")

    with pytest.raises(cw.VerificationFailedError):
        cw.verify_existing_completion(
            canonical_store_root=tmp_path, session_id=SESSION_ID,
            native_artefact_path=GC_FIXTURE, expected_native_sha256=native_sha256,
        )


def test_verify_existing_completion_fails_closed_on_missing_evidence_file(tmp_path):
    result, native_sha256 = _canonicalise(tmp_path)
    (tmp_path / result.evidence_manifest_relative_path).unlink()

    with pytest.raises(cw.VerificationFailedError):
        cw.verify_existing_completion(
            canonical_store_root=tmp_path, session_id=SESSION_ID,
            native_artefact_path=GC_FIXTURE, expected_native_sha256=native_sha256,
        )


# ------------------------------------------------------------------------------------------------
# Crash safety — a session must never be falsely CANONICAL_COMPLETE.
# ------------------------------------------------------------------------------------------------

def test_crash_before_lineage_write_leaves_no_lineage_record_then_recovers_on_retry(tmp_path, monkeypatch):
    """Simulate a process crash AFTER partitions/evidence are durably written but BEFORE the
    lineage record (the one file a caller checks for completeness) is written. Asserts: (1) no
    lineage record exists after the simulated crash — canonical partitions/evidence existing
    alone must never look like CANONICAL_COMPLETE; (2) a plain retry (fresh quality counters,
    same inputs) succeeds and produces a genuine, verifiable lineage record."""
    records, native_sha256 = _fixture_records()

    original_write = cw.write_lineage_record_atomic

    def _boom(*_args, **_kwargs):
        raise RuntimeError("SIMULATED CRASH before lineage write")

    monkeypatch.setattr(cw, "write_lineage_record_atomic", _boom)

    quality_counters_1 = SessionQualityCounters(session_id=SESSION_ID)
    with pytest.raises(RuntimeError, match="SIMULATED CRASH"):
        cw.canonicalise_records(
            session_id=SESSION_ID, records=records, mapping_table=_mapping_table(),
            canonical_store_root=tmp_path,
            native_artefact_relative_path="hmt2-gc-mbp1-v1/sessions/GC-TEST/source/fixture.jsonl",
            native_artefact_sha256=native_sha256, provider_request_identity="req-0001",
            provider_definition_ref="tests/fixtures/hmt1/gc_contract_mapping_v1.json",
            provider_adapter_version="hmt1-fixture-provider-v1",
            fixture_schema_version="hmt1-fixture-line-schema-v1",
            corpus_manifest_ref="research/hmt2/corpus-selection-manifest-v2.json",
            quality_counters=quality_counters_1,
        )

    from market_truth.acquisition.lineage import lineage_record_exists

    assert lineage_record_exists(tmp_path, SESSION_ID) is False
    # Partitions and evidence WERE durably written (they happen before the lineage write) --
    # this is expected and fine: the session is simply not yet CANONICAL_COMPLETE.
    assert (tmp_path / cw.CANONICAL_STORAGE_ROOT_DIRNAME).exists()
    assert any((tmp_path / cw.CANONICAL_STORAGE_ROOT_DIRNAME).rglob("*.parquet"))
    assert (tmp_path / "evidence" / f"{SESSION_ID}.json").exists()

    # ---- recovery: restore the real function and retry from scratch ----
    monkeypatch.setattr(cw, "write_lineage_record_atomic", original_write)
    quality_counters_2 = SessionQualityCounters(session_id=SESSION_ID)
    result = cw.canonicalise_records(
        session_id=SESSION_ID, records=records, mapping_table=_mapping_table(),
        canonical_store_root=tmp_path,
        native_artefact_relative_path="hmt2-gc-mbp1-v1/sessions/GC-TEST/source/fixture.jsonl",
        native_artefact_sha256=native_sha256, provider_request_identity="req-0001",
        provider_definition_ref="tests/fixtures/hmt1/gc_contract_mapping_v1.json",
        provider_adapter_version="hmt1-fixture-provider-v1",
        fixture_schema_version="hmt1-fixture-line-schema-v1",
        corpus_manifest_ref="research/hmt2/corpus-selection-manifest-v2.json",
        quality_counters=quality_counters_2,
    )
    assert lineage_record_exists(tmp_path, SESSION_ID) is True
    assert cw.verify_existing_completion(
        canonical_store_root=tmp_path, session_id=SESSION_ID,
        native_artefact_path=GC_FIXTURE, expected_native_sha256=native_sha256,
    ) is True
    assert result.canonical_event_set_hash


def test_crash_partway_through_record_stream_writes_nothing_durable(tmp_path):
    """A crash WHILE iterating records (before any partition write) must leave zero durable
    output at all."""
    records, native_sha256 = _fixture_records()

    def _exploding_records():
        for i, record in enumerate(records):
            if i == 2:
                raise RuntimeError("SIMULATED CRASH mid-stream")
            yield record

    quality_counters = SessionQualityCounters(session_id=SESSION_ID)
    with pytest.raises(RuntimeError, match="SIMULATED CRASH"):
        cw.canonicalise_records(
            session_id=SESSION_ID, records=_exploding_records(), mapping_table=_mapping_table(),
            canonical_store_root=tmp_path,
            native_artefact_relative_path="hmt2-gc-mbp1-v1/sessions/GC-TEST/source/fixture.jsonl",
            native_artefact_sha256=native_sha256, provider_request_identity="req-0001",
            provider_definition_ref="tests/fixtures/hmt1/gc_contract_mapping_v1.json",
            provider_adapter_version="hmt1-fixture-provider-v1",
            fixture_schema_version="hmt1-fixture-line-schema-v1",
            corpus_manifest_ref="research/hmt2/corpus-selection-manifest-v2.json",
            quality_counters=quality_counters,
        )

    assert not (tmp_path / cw.CANONICAL_STORAGE_ROOT_DIRNAME).exists()
    assert not (tmp_path / "evidence").exists()
    assert not (tmp_path / "lineage").exists()


# ------------------------------------------------------------------------------------------------
# Duplicate/conflict quality counters — observed at the canonicaliser's own _register() boundary.
# ------------------------------------------------------------------------------------------------

def test_duplicate_count_increments_on_exact_repeat_record(tmp_path):
    records, native_sha256 = _fixture_records()
    doubled = records + [records[0]]  # exact repeat of the very first record

    quality_counters = SessionQualityCounters(session_id=SESSION_ID)
    result = cw.canonicalise_records(
        session_id=SESSION_ID, records=doubled, mapping_table=_mapping_table(),
        canonical_store_root=tmp_path,
        native_artefact_relative_path="hmt2-gc-mbp1-v1/sessions/GC-TEST/source/fixture.jsonl",
        native_artefact_sha256=native_sha256, provider_request_identity="req-0001",
        provider_definition_ref="tests/fixtures/hmt1/gc_contract_mapping_v1.json",
        provider_adapter_version="hmt1-fixture-provider-v1",
        fixture_schema_version="hmt1-fixture-line-schema-v1",
        corpus_manifest_ref="research/hmt2/corpus-selection-manifest-v2.json",
        quality_counters=quality_counters,
    )
    assert result.quality_summary["duplicate_count"] == 1
    assert result.quality_summary["conflict_count"] == 0


# ------------------------------------------------------------------------------------------------
# compute_corpus_level_event_set_hash — union of independently-canonicalised sessions.
# ------------------------------------------------------------------------------------------------

def test_compute_corpus_level_event_set_hash_unions_two_sessions(tmp_path):
    result_a, _ = _canonicalise(tmp_path, session_id="GC-UNION-A")
    result_b, _ = _canonicalise(tmp_path, session_id="GC-UNION-B")

    combined = cw.compute_corpus_level_event_set_hash(tmp_path, ["GC-UNION-A", "GC-UNION-B"])
    # Combining two IDENTICAL event sets from two different sessions must not equal either one
    # alone (the identity hash is computed over the union, which is strictly larger).
    assert combined != result_a.canonical_event_set_hash
    # But it must be DETERMINISTIC regardless of the order the session ids are given in.
    combined_reversed = cw.compute_corpus_level_event_set_hash(tmp_path, ["GC-UNION-B", "GC-UNION-A"])
    assert combined == combined_reversed


# ------------------------------------------------------------------------------------------------
# Valid-empty architecture ruling — a session that fully, honestly processes to zero canonical
# events is a valid CANONICAL_COMPLETE result (canonical_result_kind="EMPTY_VALID"), provably
# distinguished from a session that produced zero events because something silently broke.
# ------------------------------------------------------------------------------------------------

EMPTY_SESSION_ID = "GC-TEST-EMPTY-2026-09-15"


def _base_kwargs(session_id, **overrides):
    base = dict(
        native_artefact_relative_path="hmt2-gc-mbp1-v1/sessions/%s/source/fixture.jsonl" % session_id,
        native_artefact_sha256="e" * 64,
        provider_request_identity="test-request-identity-0001",
        provider_definition_ref="tests/fixtures/hmt1/gc_contract_mapping_v1.json",
        provider_adapter_version="hmt1-fixture-provider-v1",
        fixture_schema_version="hmt1-fixture-line-schema-v1",
        corpus_manifest_ref="research/hmt2/corpus-selection-manifest-v2.json",
    )
    base.update(overrides)
    return base


def _canonicalise_empty(canonical_store_root, *, session_id=EMPTY_SESSION_ID, adapter_quality_counters=None):
    """Zero native records at all -- the trivial, unambiguous valid-empty case (WO acceptance
    test 1): SOURCE_RETURNED_ZERO_RECORDS."""
    quality_counters = SessionQualityCounters(session_id=session_id)
    result = cw.canonicalise_records(
        session_id=session_id,
        records=[],
        mapping_table=_mapping_table(),
        canonical_store_root=canonical_store_root,
        quality_counters=quality_counters,
        adapter_quality_counters=adapter_quality_counters,
        **_base_kwargs(session_id),
    )
    return result


def test_zero_native_records_is_valid_empty_completion(tmp_path):
    """WO acceptance test 1: zero native records -> valid empty completion."""
    result = _canonicalise_empty(tmp_path)
    assert result.canonical_result_kind == RESULT_KIND_EMPTY_VALID
    assert result.empty_reason == EMPTY_REASON_SOURCE_RETURNED_ZERO_RECORDS
    assert result.source_record_count == 0
    assert result.canonical_event_counts_by_family == {}
    assert result.source_resolved_contract_ids == ()
    assert result.canonical_emitted_contract_ids == ()


def test_nonzero_records_valid_mapping_zero_emissions_is_valid_empty_completion(tmp_path):
    """WO acceptance test 2: non-zero native records + valid source mappings + zero emissions ->
    valid empty completion. Mirrors the REAL GC-2017-07-13 remediation case exactly: the sole
    native record's raw symbol genuinely, successfully resolved to a real governed GC contract
    (GCZ26 -> COMEX:GC:2026-12), but the record itself was filtered by the adapter BEFORE ever
    becoming a RawSourceRecord (e.g. an incomplete/crossed book on a non-trade action) -- so
    `records` (what canonicalise_records() itself iterates) is empty, and the only way this
    session's real resolution is ever seen is via the adapter's own
    `source_observed_raw_symbols` side channel."""
    adapter_counters = Mbp1AdapterQualityCounters(
        total_native_records=1, incomplete_book_skipped_records=1, source_observed_raw_symbols={"GCZ26"},
    )
    result = _canonicalise_empty(tmp_path, adapter_quality_counters=adapter_counters)
    assert result.canonical_result_kind == RESULT_KIND_EMPTY_VALID
    assert result.empty_reason == EMPTY_REASON_NO_CANONICAL_EMISSIONS_AFTER_VALID_PROCESSING
    assert result.source_record_count == 1
    assert result.source_resolved_contract_ids == ("COMEX:GC:2026-12",)
    assert result.canonical_emitted_contract_ids == ()
    assert result.canonical_event_counts_by_family == {}


def test_nonzero_records_zero_resolved_contracts_stays_failed(tmp_path):
    """WO acceptance test 3: non-zero native records + zero source-resolved contracts -> NOT
    valid empty, stays failed. Here the adapter observed a native record but never assigned it
    ANY raw symbol at all (distinct from an unmapped symbol, WO acceptance test 4, below)."""
    adapter_counters = Mbp1AdapterQualityCounters(total_native_records=1, source_observed_raw_symbols=set())
    with pytest.raises(cw.CanonicalWorkerError, match="zero resolved to a governed GC contract"):
        _canonicalise_empty(tmp_path, adapter_quality_counters=adapter_counters)


def test_unmapped_symbol_is_not_valid_empty(tmp_path):
    """WO acceptance test 4: an unmapped symbol -> NOT valid empty, fails closed -- even when it
    comes from a record the adapter would otherwise have filtered before ever reaching the
    canonicaliser (the exact new case this correction can now detect)."""
    adapter_counters = Mbp1AdapterQualityCounters(
        total_native_records=1, source_observed_raw_symbols={"GCZ99_NOT_IN_TABLE"},
    )
    with pytest.raises(cw.CanonicalWorkerError, match="do not resolve to a governed GC contract"):
        _canonicalise_empty(tmp_path, adapter_quality_counters=adapter_counters)


def test_identity_conflict_is_not_valid_empty(tmp_path):
    """WO acceptance test 5: an identity conflict -> NOT valid empty. A second record sharing the
    first record's exact identity-bearing fields (same source_artifact_id/sequence/event-time)
    but DIFFERENT payload content (a different bid price) is a genuine conflict -- HMT-1's own
    unmodified `Canonicaliser._register()` fails closed on this via `DuplicateConflictError`,
    which must propagate all the way out of `canonicalise_records()`, never swallowed/
    reclassified as an empty result."""
    records, native_sha256 = _fixture_records()
    original_bid = records[0].payload["bid"]
    conflicting = dataclasses.replace(
        records[0],
        # A different, but still book-valid (bid < ask), quantity -- same identity-bearing
        # fields, genuinely different row content -> a real conflict, not a crossed-book
        # validation error from unrelated, out-of-scope HMT-1 event construction.
        payload={**records[0].payload, "bid": {**original_bid, "quantity": original_bid["quantity"] + 1}},
    )
    quality_counters = SessionQualityCounters(session_id=SESSION_ID)
    with pytest.raises(DuplicateConflictError):
        cw.canonicalise_records(
            session_id=SESSION_ID,
            records=records + [conflicting],
            mapping_table=_mapping_table(),
            canonical_store_root=tmp_path,
            quality_counters=quality_counters,
            **_base_kwargs(SESSION_ID, native_artefact_sha256=native_sha256),
        )
    assert quality_counters.conflict_count == 1


def test_empty_result_writes_real_evidence_quality_lineage_zero_partitions(tmp_path):
    """WO acceptance tests 6/7/8/9: an EMPTY_VALID result still writes real evidence, quality,
    and lineage files, with ZERO partitions -- and, per the WO, never a dummy/empty Parquet file
    written merely to satisfy a completeness assertion."""
    result = _canonicalise_empty(tmp_path)

    assert result.partition_relative_paths == ()
    assert result.partition_semantic_hashes == {}
    assert result.partition_artifact_hashes == {}
    assert not (tmp_path / cw.CANONICAL_STORAGE_ROOT_DIRNAME).exists() or not any(
        (tmp_path / cw.CANONICAL_STORAGE_ROOT_DIRNAME).rglob("*.parquet")
    )

    evidence_path = tmp_path / result.evidence_manifest_relative_path
    assert evidence_path.exists()
    import json as _json
    evidence_doc = _json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence_doc["source_record_count"] == 0
    assert evidence_doc["canonical_event_counts_by_family"] == {}
    assert evidence_doc["partition_content_hashes"] == {}
    assert evidence_doc["artifact_hashes"] == {}

    quality_path = tmp_path / result.quality_record_relative_path
    assert quality_path.exists()
    quality_doc = _json.loads(quality_path.read_text(encoding="utf-8"))
    assert quality_doc["canonical_result_kind"] == RESULT_KIND_EMPTY_VALID
    assert quality_doc["empty_reason"] == EMPTY_REASON_SOURCE_RETURNED_ZERO_RECORDS
    assert quality_doc["native_record_count"] == 0
    assert quality_doc["canonical_event_count"] == 0

    lineage_path = tmp_path / result.lineage_record_relative_path
    assert lineage_path.exists()
    row = read_lineage_record(tmp_path, EMPTY_SESSION_ID)
    assert row.canonical_result_kind == RESULT_KIND_EMPTY_VALID
    assert row.empty_reason == EMPTY_REASON_SOURCE_RETURNED_ZERO_RECORDS
    assert row.gc_contract is None
    assert row.research_partition_relative_paths == ()
    assert row.partition_semantic_hashes == {}
    assert row.partition_artifact_hashes == {}


def test_empty_result_has_deterministic_reproducible_empty_event_set_hash(tmp_path):
    """WO acceptance test 10: the deterministic, reproducible empty event-set hash. Reuses HMT-1's
    OWN existing `compute_event_set_hash` (never a hand-crafted magic hash) -- proven stable and
    reproducible for an empty list directly against `market_truth.replay.compute_event_set_hash`
    in `tests/hmt1/test_replay.py`'s style; re-proven here as a regression pin on the LITERAL
    value canonical_worker.py records for every EMPTY_VALID session."""
    result_a = _canonicalise_empty(tmp_path, session_id="GC-EMPTY-HASH-A")
    result_b = _canonicalise_empty(tmp_path, session_id="GC-EMPTY-HASH-B")
    assert result_a.canonical_event_set_hash == result_b.canonical_event_set_hash
    assert result_a.canonical_event_set_hash == hashlib.sha256(b"").hexdigest()  # the well-known empty-input SHA-256


def test_empty_result_reload_verification_asserts_expected_equals_reloaded_equals_empty(tmp_path):
    """WO acceptance test 11: reload verification for EMPTY_VALID asserts expected == reloaded ==
    [] exactly -- never skipped merely because it is trivially empty."""
    _canonicalise_empty(tmp_path)
    reloaded = cw.load_session_canonical_events(tmp_path, EMPTY_SESSION_ID)
    assert reloaded == []


def test_empty_result_idempotency_reuses_not_reprocesses(tmp_path):
    """WO acceptance test 12: a second invocation against an already-EMPTY_VALID-complete session
    reverifies (native SHA, evidence identity, quality record, lineage self-hash, zero expected
    partitions, result kind) and reuses -- exactly like the existing NONEMPTY idempotency path."""
    native_path = tmp_path / "fake-native-artefact.bin"
    native_path.write_bytes(b"a genuinely retained, empty-session native artefact's real bytes")
    real_sha256 = hashlib.sha256(native_path.read_bytes()).hexdigest()

    session_id = "GC-TEST-EMPTY-IDEMPOTENT"
    quality_counters = SessionQualityCounters(session_id=session_id)
    cw.canonicalise_records(
        session_id=session_id, records=[], mapping_table=_mapping_table(), canonical_store_root=tmp_path,
        quality_counters=quality_counters,
        **_base_kwargs(session_id, native_artefact_sha256=real_sha256),
    )

    assert cw.verify_existing_completion(
        canonical_store_root=tmp_path, session_id=session_id,
        native_artefact_path=native_path, expected_native_sha256=real_sha256,
    ) is True


def test_nonempty_completion_rules_are_completely_unchanged(tmp_path):
    """WO acceptance test 13: the existing NONEMPTY completion rules are completely unchanged --
    a real regression pin on `assert_real_row_is_complete()`'s ORIGINAL strict non-empty-
    partition requirement, now reached via its `else` (non-EMPTY_VALID) branch."""
    from market_truth.acquisition.lineage import LineageRow, assert_real_row_is_complete
    from market_truth.futures import GcContractIdentity

    row = LineageRow(
        corpus_session_id="GC-REGRESSION-NONEMPTY",
        provider_request_identity="req-0001",
        native_artefact_relative_path="hmt2-gc-mbp1-v1/sessions/x/source/mbp1.bin",
        native_artefact_sha256="1" * 64,
        provider_definition_ref="ref-0001",
        gc_contract=GcContractIdentity(delivery_year=2026, delivery_month=12),
        canonical_event_set_hash="2" * 64,
        research_partition_relative_paths=(),  # NONEMPTY row with zero partitions -- must fail
        evidence_manifest_ref="evidence-ref-0001",
        synthetic=False,
        canonical_schema_version="v1",
        canonicaliser_version="v1",
        event_identity_algorithm_version="v1",
        evidence_manifest_deterministic_hash="3" * 64,
        quality_record_ref="quality-ref-0001",
        canonical_result_kind=RESULT_KIND_NONEMPTY,
    )
    with pytest.raises(LineageError, match="no research partitions"):
        assert_real_row_is_complete(row)


def test_result_kind_defaults_to_nonempty_for_the_ordinary_happy_path(tmp_path):
    """The ordinary, already-proven happy path (real fixture records, real emitted events) is
    classified NONEMPTY, with a real representative gc_contract on its lineage row -- unchanged
    behaviour, now explicitly asserted under the new vocabulary."""
    result, _ = _canonicalise(tmp_path)
    assert result.canonical_result_kind == RESULT_KIND_NONEMPTY
    assert result.empty_reason is None
    assert result.canonical_emitted_contract_ids
    row = read_lineage_record(tmp_path, SESSION_ID)
    assert row.canonical_result_kind == RESULT_KIND_NONEMPTY
    assert row.gc_contract is not None
