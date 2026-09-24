"""HMT-2 bounded-memory canonicalisation fix (Stage 1) — direct old-vs-new equivalence and
crash-safety tests for `market_truth.acquisition.canonical_worker.canonicalise_records()`.

Uses ONLY the zero-vendor-dependency HMT-1 fixtures already committed
(`tests/fixtures/hmt1/gc_mbp1_equivalent_v1.jsonl`), exactly like `test_canonical_worker.py` — no
real retained MBP-1 bytes needed. The real-data pilot/corpus reproduction proofs are separate,
disposable, non-committed driver scripts (per this fix's own dispatch report), because they need
real retained bytes this repository does not commit.

The core proof here: feed the SAME canonical events, from the SAME governed fixture, through (a)
the OLD, whole-session-list code path (`Canonicaliser` + `market_truth.replay.
compute_event_set_hash` + `market_truth.partition.PartitionWriter.write()` called once over the
full list) and (b) the NEW bounded pipeline (`canonicalise_records()`, which spools per-partition
and hashes via `ExternalRowHasher`) and require byte-for-byte identical event-set hash and
per-partition semantic/artifact hashes.
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
from market_truth.acquisition.lineage import lineage_record_exists  # noqa: E402
from market_truth.canonicaliser import Canonicaliser  # noqa: E402
from market_truth.futures import ContractMappingTable  # noqa: E402
from market_truth.partition import PartitionWriter  # noqa: E402
from market_truth.providers.fixture import FixtureMarketDataProvider  # noqa: E402
from market_truth.replay import compute_event_set_hash  # noqa: E402

FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "hmt1"
MAPPING_PATH = FIXTURES_DIR / "gc_contract_mapping_v1.json"
GC_FIXTURE = FIXTURES_DIR / "gc_mbp1_equivalent_v1.jsonl"

PROVIDER_ID = "hmt1-fixture-provider"
DATASET_ID = "hmt1-test"


def _fixture_records():
    provider = FixtureMarketDataProvider(GC_FIXTURE, provider_id=PROVIDER_ID, dataset_id=DATASET_ID)
    return list(provider.iter_records()), provider.content_sha256()


def _old_unbounded_reference():
    """Exactly what `canonicalise_records()` did BEFORE this fix: one fresh `Canonicaliser`,
    accumulate every emitted event into one list, hash via `compute_event_set_hash`, write via one
    `PartitionWriter.write(all_events)` call."""
    records, _native_sha256 = _fixture_records()
    mapping_table = ContractMappingTable.from_json_file(MAPPING_PATH)
    canonicaliser = Canonicaliser(mapping_table=mapping_table)
    events = []
    for record in records:
        for event in canonicaliser.canonicalise(record):
            events.append(event)
    return events


def _new_bounded_result(tmp_path, session_id):
    records, native_sha256 = _fixture_records()
    quality_counters = SessionQualityCounters(session_id=session_id)
    result = cw.canonicalise_records(
        session_id=session_id,
        records=records,
        mapping_table=ContractMappingTable.from_json_file(MAPPING_PATH),
        canonical_store_root=tmp_path,
        native_artefact_relative_path="hmt2-gc-mbp1-v1/sessions/%s/source/fixture.jsonl" % session_id,
        native_artefact_sha256=native_sha256,
        provider_request_identity="test-request-identity-bounded-0001",
        provider_definition_ref="tests/fixtures/hmt1/gc_contract_mapping_v1.json",
        provider_adapter_version="hmt1-fixture-provider-v1",
        fixture_schema_version="hmt1-fixture-line-schema-v1",
        corpus_manifest_ref="research/hmt2/corpus-selection-manifest-v2.json",
        quality_counters=quality_counters,
    )
    return result


def test_bounded_pipeline_reproduces_old_unbounded_event_set_hash_exactly(tmp_path):
    old_events = _old_unbounded_reference()
    old_hash = compute_event_set_hash(old_events)

    result = _new_bounded_result(tmp_path, "GC-BOUNDED-EQUIV-0001")
    assert result.canonical_event_set_hash == old_hash


def test_bounded_pipeline_reproduces_old_unbounded_partition_hashes_exactly(tmp_path):
    old_events = _old_unbounded_reference()
    old_root = tmp_path / "old-reference-write"
    old_results = PartitionWriter(old_root).write(old_events)
    old_semantic = {r.relative_path: r.partition_content_sha256 for r in old_results}
    old_artifact = {r.relative_path: r.artifact_sha256 for r in old_results}
    old_row_counts = {r.relative_path: r.row_count for r in old_results}

    result = _new_bounded_result(tmp_path / "new", "GC-BOUNDED-EQUIV-0002")

    # Strip this fix's own session_id=<...>/ physical-namespace prefix (storage-layout defect
    # remediation, unrelated to this fix, unchanged) to compare against the OLD writer's
    # session-blind relative paths.
    new_semantic = {rel.split("/", 1)[1]: h for rel, h in result.partition_semantic_hashes.items()}
    new_artifact = {rel.split("/", 1)[1]: h for rel, h in result.partition_artifact_hashes.items()}

    assert new_semantic == old_semantic
    assert new_artifact == old_artifact
    assert set(new_semantic) == set(old_row_counts)  # sanity: same partition set either way


def test_bounded_pipeline_produces_more_than_one_partition_for_this_fixture(tmp_path):
    """Sanity check that this equivalence proof is actually exercising the partition-at-a-time
    finalization loop over >1 partition, not accidentally degenerating to a single group."""
    old_events = _old_unbounded_reference()
    old_results = PartitionWriter(tmp_path / "old-check").write(old_events)
    assert len(old_results) > 1, "fixture must produce >1 partition for this proof to be meaningful"


# ------------------------------------------------------------------------------------------------
# Crash safety — no false CANONICAL_COMPLETE, spool/scratch is cleanable, retry recomputes cleanly
# ------------------------------------------------------------------------------------------------

def _run_ok(tmp_path, session_id="GC-CRASH-0001"):
    return _new_bounded_result(tmp_path, session_id)


def test_interrupted_during_record_traversal_leaves_no_lineage(tmp_path, monkeypatch):
    from market_truth.canonicaliser import Canonicaliser as RealCanonicaliser

    session_id = "GC-CRASH-TRAVERSAL"
    call_count = {"n": 0}
    real_canonicalise = RealCanonicaliser.canonicalise

    def _flaky_canonicalise(self, record):
        call_count["n"] += 1
        if call_count["n"] == 5:
            raise RuntimeError("simulated crash mid-record-traversal")
        return real_canonicalise(self, record)

    monkeypatch.setattr(RealCanonicaliser, "canonicalise", _flaky_canonicalise)
    with pytest.raises(RuntimeError):
        _run_ok(tmp_path, session_id)
    assert not lineage_record_exists(tmp_path, session_id)

    monkeypatch.undo()
    # retry (fresh attempt) must cleanly succeed and reproduce the same result as an uninterrupted
    # run -- the prior attempt's spool/hash-run scratch (if any survived the raise) must not
    # corrupt or block the retry.
    result = _run_ok(tmp_path, session_id)
    assert lineage_record_exists(tmp_path, session_id)
    baseline = _run_ok(tmp_path, session_id + "-BASELINE")
    assert result.canonical_event_set_hash == baseline.canonical_event_set_hash


def test_interrupted_during_partition_finalisation_leaves_no_lineage(tmp_path, monkeypatch):
    from market_truth.partition import PartitionWriter as RealPartitionWriter

    session_id = "GC-CRASH-PARTITION-FINALISE"
    real_write = RealPartitionWriter.write
    call_count = {"n": 0}

    def _flaky_write(self, events):
        call_count["n"] += 1
        if call_count["n"] == 2:  # let the first partition finalize, fail on the second
            raise RuntimeError("simulated crash mid-partition-finalisation")
        return real_write(self, events)

    monkeypatch.setattr(RealPartitionWriter, "write", _flaky_write)
    with pytest.raises(RuntimeError):
        _run_ok(tmp_path, session_id)
    assert not lineage_record_exists(tmp_path, session_id)
    # no partial promotion into the durable canonical-v2 tree either
    canonical_root = tmp_path / cw.CANONICAL_STORAGE_ROOT_DIRNAME
    assert not any(canonical_root.glob(f"session_id={session_id}/**/*.parquet")) if canonical_root.exists() else True

    monkeypatch.undo()
    result = _run_ok(tmp_path, session_id)
    baseline = _run_ok(tmp_path, session_id + "-BASELINE")
    assert result.canonical_event_set_hash == baseline.canonical_event_set_hash


def test_interrupted_during_promotion_leaves_no_lineage_and_stale_scratch_is_cleanable(tmp_path, monkeypatch):
    session_id = "GC-CRASH-PROMOTION"
    real_promote = cw._promote_partition_results
    call_count = {"n": 0}

    def _flaky_promote(*args, **kwargs):
        call_count["n"] += 1
        raise RuntimeError("simulated crash during promotion")

    monkeypatch.setattr(cw, "_promote_partition_results", _flaky_promote)
    with pytest.raises(RuntimeError):
        _run_ok(tmp_path, session_id)
    assert not lineage_record_exists(tmp_path, session_id)

    # the crashed attempt's scratch directory is exactly the kind of leftover the NEXT attempt's
    # own stale-cleanup glob is designed to discard -- prove it is actually still there (not
    # silently vanished) and, separately, that the next attempt discards it and succeeds cleanly.
    staging_parent = tmp_path / ".staging"
    session_segment = cw._safe_session_segment(session_id)
    leftover = list(staging_parent.glob(f"{session_segment}-*"))
    assert leftover, "expected a leftover attempt directory from the simulated crash"

    monkeypatch.undo()
    result = _run_ok(tmp_path, session_id)
    assert lineage_record_exists(tmp_path, session_id)
    assert not list(staging_parent.glob(f"{session_segment}-*")), "stale attempt must be discarded by the retry"
    baseline = _run_ok(tmp_path, session_id + "-BASELINE")
    assert result.canonical_event_set_hash == baseline.canonical_event_set_hash


def test_interrupted_during_reload_verification_leaves_no_lineage(tmp_path, monkeypatch):
    from market_truth.partition import PartitionReader as RealPartitionReader

    session_id = "GC-CRASH-RELOAD-VERIFY"
    real_read_events = RealPartitionReader.read_events
    call_count = {"n": 0}

    def _flaky_read_events(self, relative_dir, family):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated crash mid-reload-verification")
        return real_read_events(self, relative_dir, family)

    monkeypatch.setattr(RealPartitionReader, "read_events", _flaky_read_events)
    with pytest.raises(RuntimeError):
        _run_ok(tmp_path, session_id)
    assert not lineage_record_exists(tmp_path, session_id)

    monkeypatch.undo()
    # the partitions were already promoted into canonical-v2/ before the simulated reload-verify
    # crash -- retry must still succeed (either by re-verifying/re-promoting idempotently, exactly
    # as the existing fail-closed promotion guard already allows for a byte-identical re-write).
    result = _run_ok(tmp_path, session_id)
    assert lineage_record_exists(tmp_path, session_id)
    baseline = _run_ok(tmp_path, session_id + "-BASELINE")
    assert result.canonical_event_set_hash == baseline.canonical_event_set_hash


def test_interrupted_before_lineage_write_leaves_no_lineage(tmp_path, monkeypatch):
    session_id = "GC-CRASH-BEFORE-LINEAGE"
    real_write_lineage = cw.write_lineage_record_atomic
    call_count = {"n": 0}

    def _flaky_write_lineage(*args, **kwargs):
        call_count["n"] += 1
        raise RuntimeError("simulated crash immediately before the lineage write")

    monkeypatch.setattr(cw, "write_lineage_record_atomic", _flaky_write_lineage)
    with pytest.raises(RuntimeError):
        _run_ok(tmp_path, session_id)
    assert not lineage_record_exists(tmp_path, session_id)
    # evidence/quality files WERE written (this is expected/pre-existing ordering -- lineage is
    # always last) -- but with no lineage record, this session is still correctly "never happened"
    # to any caller that gates on lineage.
    assert (tmp_path / "evidence" / f"{session_id}.json").exists()

    monkeypatch.undo()
    result = _run_ok(tmp_path, session_id)
    assert lineage_record_exists(tmp_path, session_id)
    baseline = _run_ok(tmp_path, session_id + "-BASELINE")
    assert result.canonical_event_set_hash == baseline.canonical_event_set_hash


def test_no_leftover_scratch_directories_after_a_clean_success(tmp_path):
    session_id = "GC-CRASH-CLEAN-SUCCESS"
    _run_ok(tmp_path, session_id)
    staging_parent = tmp_path / ".staging"
    assert not any(staging_parent.glob("*")) if staging_parent.exists() else True
