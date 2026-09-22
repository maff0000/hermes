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

import hashlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from market_truth.acquisition import canonical_worker as cw  # noqa: E402
from market_truth.acquisition.canonical_quality_record import SessionQualityCounters  # noqa: E402
from market_truth.acquisition.lineage import LineageError  # noqa: E402
from market_truth.futures import ContractMappingTable  # noqa: E402
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
        assert (tmp_path / "canonical" / rel).exists()
    assert (tmp_path / result.evidence_manifest_relative_path).exists()
    assert (tmp_path / result.lineage_record_relative_path).exists()
    assert (tmp_path / result.quality_record_relative_path).exists()

    # no leftover staging directory
    assert not any((tmp_path / ".staging").glob("*")) if (tmp_path / ".staging").exists() else True

    from market_truth.acquisition.lineage import read_lineage_record

    row = read_lineage_record(tmp_path, SESSION_ID)
    assert row.synthetic is False
    assert row.canonical_event_set_hash == result.canonical_event_set_hash
    assert row.native_artefact_sha256 == native_sha256
    assert row.evidence_manifest_deterministic_hash == result.evidence_manifest_deterministic_hash
    assert row.quality_record_ref == result.quality_record_relative_path


def test_quality_record_reflects_real_counts(tmp_path):
    result, _ = _canonicalise(tmp_path)
    summary = result.quality_summary
    assert summary["native_record_count"] == 7  # tests/fixtures/hmt1/gc_mbp1_equivalent_v1.jsonl: 5 TOB + 2 TRADE
    assert summary["source_gap_completeness_status"] in ("COMPLETE", "GAP_OR_ANOMALY_SUSPECTED")
    assert summary["duplicate_count"] == 0
    assert summary["conflict_count"] == 0


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
    tampered = tmp_path / "canonical" / sorted(result.partition_relative_paths)[0]
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
    assert (tmp_path / "canonical").exists()
    assert any((tmp_path / "canonical").rglob("*.parquet"))
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

    assert not (tmp_path / "canonical").exists()
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
