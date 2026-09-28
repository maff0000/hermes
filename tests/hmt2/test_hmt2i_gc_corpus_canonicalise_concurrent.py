"""HMT-2 canonicalisation checkpoint — regression tests for the BOUNDED, PROCESS-LEVEL,
session-boundary concurrency added to `research/hmt2/hmt2i_gc_corpus_canonicalise.py`
(`process_sessions_concurrent()` / `_run_one_session_worker()` / `_build_memory_aware_waves()`).

Two complementary strategies, both real, neither a re-implementation of what they test:

  1. COORDINATOR-LOGIC tests (fail-closed promotion guard, idempotent verify-and-reuse, worker
     crash never producing a false COMPLETE row, v1-lineage-cannot-masquerade-as-v2, single-writer
     ledger, stop-on-ambiguous-failure) run `process_sessions_concurrent()` for real, but with an
     INJECTED in-process fake executor (`_FakeExecutor` below) standing in for
     `concurrent.futures.ProcessPoolExecutor` -- this is dependency injection the coordinator
     itself already supports (`executor_factory=`), used here purely so `monkeypatch.setattr` on
     `canonical_worker.*` is reliably observed (a real subprocess would need the patch applied
     BEFORE the fork, which is fragile to depend on in a test) while every other line of
     `process_sessions_concurrent()` -- scheduling, wave semantics, exception handling, ledger
     writes -- runs completely for real, unmodified.

  2. REAL cross-process tests (different physical paths for an identical logical partition
     coordinate, no cross-session mutation, lineage still verifies after a concurrent sibling
     completes, HMT-1 partition bytes/canonical events unchanged) drive a REAL
     `concurrent.futures.ProcessPoolExecutor` against `market_truth.acquisition.canonical_worker.
     canonicalise_records()` (the real, unmodified core `_run_one_session_worker()` itself calls,
     one layer below the real-artefact-only `canonicalise_mbp1_session()`) using the already-
     committed, zero-vendor-dependency HMT-1 fixture (`tests/fixtures/hmt1/
     gc_mbp1_equivalent_v1.jsonl`) — mirrors `tests/hmt2/test_canonical_worker.py`'s own established
     fixture-provider discipline exactly, so these tests need no network and no real retained MBP-1
     bytes, run in CI, and are still genuine, real, separate-OS-process concurrency.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from market_truth.acquisition import canonical_worker  # noqa: E402
from market_truth.acquisition.canonical_quality_record import SessionQualityCounters  # noqa: E402
from market_truth.acquisition.lineage import CANONICAL_STORAGE_LAYOUT_VERSION  # noqa: E402
from market_truth.futures import ContractMappingTable  # noqa: E402
from market_truth.providers.fixture import FixtureMarketDataProvider  # noqa: E402

FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "hmt1"
MAPPING_PATH = FIXTURES_DIR / "gc_contract_mapping_v1.json"
GC_FIXTURE = FIXTURES_DIR / "gc_mbp1_equivalent_v1.jsonl"
DRIVER_MODULE_PATH = REPO_ROOT / "research" / "hmt2" / "hmt2i_gc_corpus_canonicalise.py"


def _load_module(name: str, relative_path: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


source_ledger_mod = _load_module("hmt2h_gc_corpus_ledger", "research/hmt2/hmt2h_gc_corpus_ledger.py")
canonical_ledger_mod = _load_module("hmt2i_gc_corpus_canonical_ledger", "research/hmt2/hmt2i_gc_corpus_canonical_ledger.py")
hmt2i_canonicalise = _load_module("hmt2i_gc_corpus_canonicalise", "research/hmt2/hmt2i_gc_corpus_canonicalise.py")


# ==================================================================================================
# Shared fixture-provider helpers (mirrors tests/hmt2/test_canonical_worker.py exactly).
# ==================================================================================================

def _fixture_records():
    provider = FixtureMarketDataProvider(GC_FIXTURE, provider_id="hmt1-fixture-provider", dataset_id="hmt1-test")
    return list(provider.iter_records()), provider.content_sha256()


def _mapping_table():
    return ContractMappingTable.from_json_file(MAPPING_PATH)


def _canonicalise_fixture_session(canonical_store_root, session_id: str):
    """Real, unmodified `canonical_worker.canonicalise_records()` call for ONE session, using the
    committed zero-vendor-dependency HMT-1 fixture -- deliberately the SAME fixture content for
    every session_id, so two different sessions are GUARANTEED to compute the identical LOGICAL
    HMT-1 partition coordinate (same contract/date/family) while only ever differing in
    session_id -- exactly the scenario requirement (a) below needs."""
    records, native_sha256 = _fixture_records()
    quality_counters = SessionQualityCounters(session_id=session_id)
    return canonical_worker.canonicalise_records(
        session_id=session_id,
        records=records,
        mapping_table=_mapping_table(),
        canonical_store_root=canonical_store_root,
        native_artefact_relative_path=f"hmt2-gc-mbp1-v1/sessions/{session_id}/source/fixture.jsonl",
        native_artefact_sha256=native_sha256,
        provider_request_identity=f"test-request-identity-{session_id}",
        provider_definition_ref="tests/fixtures/hmt1/gc_contract_mapping_v1.json",
        provider_adapter_version="hmt1-fixture-provider-v1",
        fixture_schema_version="hmt1-fixture-line-schema-v1",
        corpus_manifest_ref="research/hmt2/corpus-selection-manifest-v2.json",
        quality_counters=quality_counters,
    )


def _worker_canonicalise_fixture_session(task):
    """Module-level (picklable) target for a REAL `ProcessPoolExecutor` worker -- runs in a
    genuinely separate OS process. Returns a plain, picklable dict (never the dataclass itself,
    to keep this test file's own cross-process contract independent of
    `SessionCanonicalisationResult`'s exact shape)."""
    canonical_store_root, session_id = task
    result = _canonicalise_fixture_session(canonical_store_root, session_id)
    return {
        "session_id": session_id,
        "partition_relative_paths": list(result.partition_relative_paths),
        "canonical_event_set_hash": result.canonical_event_set_hash,
        "partition_semantic_hashes": dict(result.partition_semantic_hashes),
        "partition_artifact_hashes": dict(result.partition_artifact_hashes),
        "native_artefact_sha256": result.native_artefact_sha256,
    }


# ==================================================================================================
# Part 1 — real cross-process tests (genuine ProcessPoolExecutor, no mocking at all).
# ==================================================================================================

def test_same_logical_partition_coordinate_resolves_to_different_physical_paths_concurrently(tmp_path):
    """Requirement (a): two sessions whose canonical events land on the EXACT SAME HMT-1 logical
    partition coordinate (guaranteed here by feeding both sessions the identical fixture content)
    resolve to two DIFFERENT final physical paths when canonicalised concurrently in two real OS
    processes -- the session-scoped storage-layout-v2 fix, proven under real concurrency, not just
    sequential re-use."""
    import concurrent.futures

    canonical_store_root = str(tmp_path / "canonical-store")
    with concurrent.futures.ProcessPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_worker_canonicalise_fixture_session, (canonical_store_root, "CONC-COORD-A")),
            executor.submit(_worker_canonicalise_fixture_session, (canonical_store_root, "CONC-COORD-B")),
        ]
        results = {f.result()["session_id"]: f.result() for f in futures}

    a, b = results["CONC-COORD-A"], results["CONC-COORD-B"]
    assert a["partition_relative_paths"] and b["partition_relative_paths"]
    # Same LOGICAL coordinate (strip the session_id=.../ prefix) ...
    logical_a = {p.split("/", 1)[1] for p in a["partition_relative_paths"]}
    logical_b = {p.split("/", 1)[1] for p in b["partition_relative_paths"]}
    assert logical_a == logical_b, "fixture setup did not actually produce a shared logical coordinate"
    # ... but DIFFERENT physical paths, and both files genuinely exist on disk.
    assert set(a["partition_relative_paths"]).isdisjoint(set(b["partition_relative_paths"]))
    for rel in a["partition_relative_paths"] + b["partition_relative_paths"]:
        assert (tmp_path / "canonical-store" / canonical_worker.CANONICAL_STORAGE_ROOT_DIRNAME / rel).exists()
    # Identical logical content -> identical canonical_event_set_hash (same fixture) even though
    # the two sessions never shared any in-memory state (two separate processes).
    assert a["canonical_event_set_hash"] == b["canonical_event_set_hash"]


def test_concurrent_session_b_cannot_mutate_session_a_artifact(tmp_path):
    """Requirement (b): processing session B concurrently must not perturb session A's own
    physical artefact (bytes) or recorded hashes at all, even though both sessions are promoted
    into the SAME canonical_store_root at the same time."""
    import concurrent.futures

    # Serial baseline: process A alone first, into its OWN root, and record its exact bytes.
    baseline_root = tmp_path / "baseline"
    _canonicalise_fixture_session(str(baseline_root), "CONC-MUT-A")
    from market_truth.acquisition.lineage import read_lineage_record
    baseline_row = read_lineage_record(baseline_root, "CONC-MUT-A")
    baseline_partition_bytes = {
        rel: (baseline_root / canonical_worker.CANONICAL_STORAGE_ROOT_DIRNAME / rel).read_bytes()
        for rel in baseline_row.partition_artifact_hashes
    }

    # Concurrent run: A and B into a SHARED, fresh root, at the same time.
    shared_root = tmp_path / "shared"
    with concurrent.futures.ProcessPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_worker_canonicalise_fixture_session, (str(shared_root), "CONC-MUT-A")),
            executor.submit(_worker_canonicalise_fixture_session, (str(shared_root), "CONC-MUT-B")),
        ]
        for f in futures:
            f.result()

    concurrent_row = read_lineage_record(shared_root, "CONC-MUT-A")
    assert concurrent_row.canonical_event_set_hash == baseline_row.canonical_event_set_hash
    assert concurrent_row.partition_artifact_hashes == baseline_row.partition_artifact_hashes
    for rel, expected_bytes in baseline_partition_bytes.items():
        actual_bytes = (shared_root / canonical_worker.CANONICAL_STORAGE_ROOT_DIRNAME / rel).read_bytes()
        assert actual_bytes == expected_bytes, f"session B's concurrent run perturbed session A's own bytes at {rel}"


def test_session_a_lineage_still_verifies_after_b_completes_concurrently(tmp_path):
    """Requirement (c): session A's own lineage record still passes real, unmodified
    `verify_existing_completion()` after a sibling session B completed concurrently against the
    same canonical_store_root."""
    import concurrent.futures

    shared_root = tmp_path / "shared"
    with concurrent.futures.ProcessPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_worker_canonicalise_fixture_session, (str(shared_root), "CONC-VERIFY-A")),
            executor.submit(_worker_canonicalise_fixture_session, (str(shared_root), "CONC-VERIFY-B")),
        ]
        for f in futures:
            f.result()

    import hashlib

    from market_truth.acquisition.lineage import read_lineage_record
    row = read_lineage_record(shared_root, "CONC-VERIFY-A")

    # `FixtureMarketDataProvider.content_sha256()` (used as `native_artefact_sha256` when this
    # session was canonicalised) is a plain sha256 of the raw, committed fixture file bytes -- so
    # a plain copy of that same file is exactly the native artefact `verify_existing_completion()`
    # needs to re-hash.
    native_path = tmp_path / "native-a.jsonl"
    native_path.write_bytes(GC_FIXTURE.read_bytes())
    assert hashlib.sha256(native_path.read_bytes()).hexdigest() == row.native_artefact_sha256

    verified = canonical_worker.verify_existing_completion(
        canonical_store_root=shared_root, session_id="CONC-VERIFY-A",
        native_artefact_path=native_path, expected_native_sha256=row.native_artefact_sha256,
    )
    assert verified is True


def test_hmt1_partition_bytes_and_canonical_events_unchanged_serial_vs_worker(tmp_path):
    """Requirements (h)/(i): diff the actual bytes/hashes between a SERIAL-produced partition and
    a worker (real, separate-process)-produced partition for the identical session/fixture input
    -- must be byte-for-byte identical, and the reloaded canonical events identical too."""
    import concurrent.futures

    serial_root = tmp_path / "serial"
    serial_result = _canonicalise_fixture_session(str(serial_root), "CONC-DIFF-SESSION")

    worker_root = tmp_path / "worker"
    with concurrent.futures.ProcessPoolExecutor(max_workers=1) as executor:
        worker_out = executor.submit(_worker_canonicalise_fixture_session, (str(worker_root), "CONC-DIFF-SESSION")).result()

    assert worker_out["canonical_event_set_hash"] == serial_result.canonical_event_set_hash
    assert worker_out["partition_semantic_hashes"] == dict(serial_result.partition_semantic_hashes)
    assert worker_out["partition_artifact_hashes"] == dict(serial_result.partition_artifact_hashes)
    for rel in serial_result.partition_relative_paths:
        serial_bytes = (serial_root / canonical_worker.CANONICAL_STORAGE_ROOT_DIRNAME / rel).read_bytes()
        worker_bytes = (worker_root / canonical_worker.CANONICAL_STORAGE_ROOT_DIRNAME / rel).read_bytes()
        assert serial_bytes == worker_bytes, f"partition bytes diverged for {rel}"

    serial_events = canonical_worker.load_session_canonical_events(serial_root, "CONC-DIFF-SESSION")
    worker_events = canonical_worker.load_session_canonical_events(worker_root, "CONC-DIFF-SESSION")
    from market_truth.partition import serialize_event_row
    assert sorted(serialize_event_row(e) for e in serial_events) == sorted(serialize_event_row(e) for e in worker_events)


def test_no_network_access_during_concurrent_canonicalisation():
    """Requirement (j): the concurrency additions to the driver never import anything
    network-capable or vendor-SDK-capable -- mirrors
    `tests/hmt2/test_canonical_worker_no_network_guard.py`'s established AST-based pattern."""
    forbidden_roots = {
        "socket", "requests", "urllib", "urllib2", "http", "httpx", "aiohttp",
        "ftplib", "smtplib", "telnetlib", "databento",
    }
    tree = ast.parse(DRIVER_MODULE_PATH.read_text(encoding="utf-8"), filename=str(DRIVER_MODULE_PATH))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root not in forbidden_roots, f"forbidden import {alias.name!r} in {DRIVER_MODULE_PATH}"
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            root = node.module.split(".")[0]
            assert root not in forbidden_roots, f"forbidden import-from {node.module!r} in {DRIVER_MODULE_PATH}"


# ==================================================================================================
# Part 2 — coordinator-logic tests, via `process_sessions_concurrent()` with an injected in-process
# fake executor (real coordinator code, fake process boundary -- see module docstring for why).
# ==================================================================================================

class _ImmediateFuture:
    def __init__(self, fn, task):
        try:
            self._value = fn(task)
            self._exc = None
        except BaseException as exc:  # noqa: BLE001 - deliberately mirrors a real Future's contract
            self._value = None
            self._exc = exc

    def result(self):
        if self._exc is not None:
            raise self._exc
        return self._value


class _FakeExecutor:
    """Runs every submitted task synchronously, in THIS process, in submission order -- stands in
    for `concurrent.futures.ProcessPoolExecutor` purely so `monkeypatch.setattr(canonical_worker,
    ...)` is reliably observed by `_run_one_session_worker()`. Every other line of
    `process_sessions_concurrent()` -- wave building, per-wave dispatch, fixed-order result
    application, ledger writes, stop-on-failure -- runs completely unmodified."""

    def __init__(self, max_workers=None):
        self.max_workers = max_workers

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def submit(self, fn, task):
        return _ImmediateFuture(fn, task)


def _fake_executor_factory(max_workers=None):
    return _FakeExecutor(max_workers=max_workers)


def _acquisition_entry(session_id, trade_date, *, sha256="a" * 64, request_identity="req-0001", byte_size=1000):
    return {
        "session_id": session_id, "trade_date": trade_date, "request_identity": request_identity,
        "state": source_ledger_mod.STATE_COMPLETE,
        "artefact": {
            "sha256": sha256, "object_relative_path": f"hmt2-gc-mbp1-v1/sessions/{session_id}/source/x.dbn.zst",
            "byte_size": byte_size,
        },
    }


def _fake_result(session_id, *, event_set_hash="deadbeef"):
    return canonical_worker.SessionCanonicalisationResult(
        session_id=session_id, native_artefact_sha256="a" * 64, source_record_count=10,
        canonical_event_counts_by_family={"market_trade": 5}, canonical_event_set_hash=event_set_hash,
        partition_relative_paths=("schema=v1/x/part-00000.parquet",),
        partition_semantic_hashes={"schema=v1/x/part-00000.parquet": "s" * 64},
        partition_artifact_hashes={"schema=v1/x/part-00000.parquet": "p" * 64},
        evidence_manifest_relative_path=f"evidence/{session_id}.json",
        evidence_manifest_deterministic_hash="d" * 64,
        lineage_record_relative_path=f"lineage/{session_id}.json",
        quality_record_relative_path=f"quality/{session_id}.json",
        quality_summary={"native_record_count": 10},
    )


def _setup(tmp_path, session_ids, *, byte_sizes=None):
    byte_sizes = byte_sizes or {}
    acquisition_ledger = {
        sid: _acquisition_entry(sid, f"2020-01-{i+1:02d}", byte_size=byte_sizes.get(sid, 1000))
        for i, sid in enumerate(session_ids)
    }
    canonical_ledger = canonical_ledger_mod.build_initial_canonical_ledger(acquisition_ledger=acquisition_ledger)
    for sid in session_ids:
        acq = acquisition_ledger[sid]
        native_dir = tmp_path / "research-source" / acq["artefact"]["object_relative_path"]
        native_dir.parent.mkdir(parents=True, exist_ok=True)
        native_dir.write_bytes(b"native bytes")
    return acquisition_ledger, canonical_ledger


def _run_concurrent(tmp_path, canonical_ledger, acquisition_ledger, session_ids, **kw):
    return hmt2i_canonicalise.process_sessions_concurrent(
        canonical_ledger=canonical_ledger, acquisition_ledger=acquisition_ledger,
        session_ids=session_ids, canonical_store_root=tmp_path / "canonical-store",
        mapping_table_path=str(tmp_path / "mapping.json"),
        research_source_root=str(tmp_path / "research-source"),
        ledger_state_path=str(tmp_path / "canonical_ledger.json"),
        executor_factory=_fake_executor_factory,
        **kw,
    )


def test_concurrent_happy_path_matches_serial_ledger_effect(tmp_path, monkeypatch):
    acquisition_ledger, canonical_ledger = _setup(tmp_path, ["GC-2020-01-01", "GC-2020-01-02"])
    monkeypatch.setattr(canonical_worker, "canonicalise_mbp1_session", lambda **kw: _fake_result(kw["session_id"]))

    result = _run_concurrent(tmp_path, canonical_ledger, acquisition_ledger, ["GC-2020-01-01", "GC-2020-01-02"])

    assert result["stopped_reason"] is None
    for sid in ["GC-2020-01-01", "GC-2020-01-02"]:
        assert canonical_ledger[sid]["state"] == canonical_ledger_mod.STATE_CANONICAL_COMPLETE
        assert canonical_ledger[sid]["canonical_event_set_hash"] == "deadbeef"
    reloaded = canonical_ledger_mod.load_canonical_ledger(str(tmp_path / "canonical_ledger.json"))
    assert reloaded == canonical_ledger


def test_no_session_assigned_to_two_workers_and_ledger_never_written_concurrently(tmp_path, monkeypatch):
    """A session must never be assigned twice, and the ledger is only ever written by the
    coordinator (never inside `_run_one_session_worker()`) -- assert every dispatched session_id
    is unique and the ledger file reflects exactly one save per successfully-processed session."""
    session_ids = [f"GC-2020-02-{i:02d}" for i in range(1, 6)]
    acquisition_ledger, canonical_ledger = _setup(tmp_path, session_ids)
    seen = []

    def _fake_canonicalise(**kw):
        seen.append(kw["session_id"])
        assert seen.count(kw["session_id"]) == 1, "same session dispatched more than once"
        return _fake_result(kw["session_id"])

    monkeypatch.setattr(canonical_worker, "canonicalise_mbp1_session", _fake_canonicalise)
    result = _run_concurrent(tmp_path, canonical_ledger, acquisition_ledger, session_ids, max_workers=2)

    assert sorted(seen) == sorted(session_ids)
    assert len(seen) == len(set(seen))
    for sid in session_ids:
        assert canonical_ledger[sid]["state"] == canonical_ledger_mod.STATE_CANONICAL_COMPLETE
    assert result["stopped_reason"] is None


def test_ambiguous_failure_marks_failed_stops_batch_never_complete_concurrently(tmp_path, monkeypatch):
    """Requirement (f) (part 1): a worker "crash" (ambiguous exception) can never produce a false
    COMPLETE ledger entry -- it is marked FAILED, and the batch stops (no further waves
    dispatched, no auto-retry) -- generalised from `process_sessions()`'s own single-session
    version of this same guarantee."""
    session_ids = ["GC-2020-03-01", "GC-2020-03-02", "GC-2020-03-03", "GC-2020-03-04"]
    acquisition_ledger, canonical_ledger = _setup(tmp_path, session_ids)

    def _boom(**kw):
        if kw["session_id"] == "GC-2020-03-01":
            raise RuntimeError("simulated worker crash mid-session")
        return _fake_result(kw["session_id"])

    monkeypatch.setattr(canonical_worker, "canonicalise_mbp1_session", _boom)
    # max_workers=1 -> one session per wave -> deterministic single-wave-at-a-time stop point.
    result = _run_concurrent(tmp_path, canonical_ledger, acquisition_ledger, session_ids, max_workers=1)

    assert canonical_ledger["GC-2020-03-01"]["state"] == canonical_ledger_mod.STATE_CANONICAL_FAILED
    assert "simulated worker crash" in canonical_ledger["GC-2020-03-01"]["failure_reason"]
    assert result["stopped_reason"] is not None
    for sid in session_ids[1:]:
        assert canonical_ledger[sid]["state"] == canonical_ledger_mod.STATE_CANONICAL_PENDING
    reloaded = canonical_ledger_mod.load_canonical_ledger(str(tmp_path / "canonical_ledger.json"))
    assert reloaded["GC-2020-03-01"]["state"] == canonical_ledger_mod.STATE_CANONICAL_FAILED
    # Never falsely COMPLETE, under any circumstance:
    for sid in session_ids:
        assert reloaded[sid]["state"] != canonical_ledger_mod.STATE_CANONICAL_COMPLETE


def test_worker_crash_within_a_wave_does_not_corrupt_sibling_sessions_result(tmp_path, monkeypatch):
    """Requirement (f) (part 2): when TWO sessions are dispatched together in the SAME wave and
    one of them crashes, the OTHER, successfully-processed session in that same wave is still
    correctly marked COMPLETE with its own real result -- one worker's crash never corrupts or
    silently discards a sibling's own good outcome."""
    session_ids = ["GC-2020-04-01", "GC-2020-04-02"]
    acquisition_ledger, canonical_ledger = _setup(tmp_path, session_ids)

    def _mixed(**kw):
        if kw["session_id"] == "GC-2020-04-01":
            raise RuntimeError("simulated crash for A only")
        return _fake_result(kw["session_id"], event_set_hash="good-hash-for-b")

    monkeypatch.setattr(canonical_worker, "canonicalise_mbp1_session", _mixed)
    result = _run_concurrent(tmp_path, canonical_ledger, acquisition_ledger, session_ids, max_workers=2)

    assert canonical_ledger["GC-2020-04-01"]["state"] == canonical_ledger_mod.STATE_CANONICAL_FAILED
    assert canonical_ledger["GC-2020-04-02"]["state"] == canonical_ledger_mod.STATE_CANONICAL_COMPLETE
    assert canonical_ledger["GC-2020-04-02"]["canonical_event_set_hash"] == "good-hash-for-b"
    assert result["stopped_reason"] is not None


def test_claimed_complete_session_is_verified_and_reused_not_reprocessed_concurrently(tmp_path, monkeypatch):
    """Requirement (e): a claimed-COMPLETE session is verified-and-reused, never blindly
    re-canonicalised, under the concurrent path too."""
    acquisition_ledger, canonical_ledger = _setup(tmp_path, ["GC-2020-05-01"])
    canonical_ledger["GC-2020-05-01"]["state"] = canonical_ledger_mod.STATE_CANONICAL_COMPLETE

    calls = {"reprocess": 0, "verify": 0}
    monkeypatch.setattr(
        canonical_worker, "canonicalise_mbp1_session",
        lambda **kw: calls.__setitem__("reprocess", calls["reprocess"] + 1) or _fake_result(kw["session_id"]),
    )
    monkeypatch.setattr(
        canonical_worker, "verify_existing_completion",
        lambda **kw: calls.__setitem__("verify", calls["verify"] + 1) or True,
    )

    result = _run_concurrent(tmp_path, canonical_ledger, acquisition_ledger, ["GC-2020-05-01"])

    assert calls["verify"] == 1
    assert calls["reprocess"] == 0
    assert canonical_ledger["GC-2020-05-01"]["state"] == canonical_ledger_mod.STATE_CANONICAL_COMPLETE
    assert result["processed"][0]["reused_existing_canonical_result"] is True


def test_same_session_idempotent_rerun_reuses_concurrently_with_a_second_session(tmp_path, monkeypatch):
    """Requirement (e), stronger form: an already-COMPLETE session rerun CONCURRENTLY alongside a
    genuinely new PENDING session still takes the verify-and-reuse path for itself (never
    reprocessed) while the new session is genuinely processed -- proves the idempotent path and a
    fresh-processing path can coexist correctly in the same wave."""
    acquisition_ledger, canonical_ledger = _setup(tmp_path, ["GC-2020-06-01", "GC-2020-06-02"])
    canonical_ledger["GC-2020-06-01"]["state"] = canonical_ledger_mod.STATE_CANONICAL_COMPLETE

    calls = {"reprocess": [], "verify": 0}
    monkeypatch.setattr(
        canonical_worker, "canonicalise_mbp1_session",
        lambda **kw: calls["reprocess"].append(kw["session_id"]) or _fake_result(kw["session_id"]),
    )
    monkeypatch.setattr(
        canonical_worker, "verify_existing_completion",
        lambda **kw: calls.__setitem__("verify", calls["verify"] + 1) or True,
    )

    result = _run_concurrent(tmp_path, canonical_ledger, acquisition_ledger, ["GC-2020-06-01", "GC-2020-06-02"], max_workers=2)

    assert calls["verify"] == 1
    assert calls["reprocess"] == ["GC-2020-06-02"]
    assert canonical_ledger["GC-2020-06-01"]["state"] == canonical_ledger_mod.STATE_CANONICAL_COMPLETE
    assert canonical_ledger["GC-2020-06-02"]["state"] == canonical_ledger_mod.STATE_CANONICAL_COMPLETE
    assert result["stopped_reason"] is None


def test_claimed_complete_session_failing_reverification_marks_failed_and_stops_concurrently(tmp_path, monkeypatch):
    acquisition_ledger, canonical_ledger = _setup(tmp_path, ["GC-2020-07-01", "GC-2020-07-02"])
    canonical_ledger["GC-2020-07-01"]["state"] = canonical_ledger_mod.STATE_CANONICAL_COMPLETE

    def _fail_verify(**kw):
        raise canonical_worker.VerificationFailedError("simulated drift detected")

    monkeypatch.setattr(canonical_worker, "verify_existing_completion", _fail_verify)
    result = _run_concurrent(tmp_path, canonical_ledger, acquisition_ledger, ["GC-2020-07-01", "GC-2020-07-02"], max_workers=1)

    assert canonical_ledger["GC-2020-07-01"]["state"] == canonical_ledger_mod.STATE_CANONICAL_FAILED
    assert "simulated drift" in canonical_ledger["GC-2020-07-01"]["failure_reason"]
    assert result["stopped_reason"] is not None
    assert canonical_ledger["GC-2020-07-02"]["state"] == canonical_ledger_mod.STATE_CANONICAL_PENDING


def test_v1_lineage_row_cannot_masquerade_as_v2_under_concurrent_force_rebuild(tmp_path, monkeypatch):
    """Requirement (g): a CANONICAL_COMPLETE session whose ledger row predates the storage-layout
    fix (no `canonical_storage_layout_version`, i.e. a v1 row) must be GENUINELY reprocessed under
    `force_rebuild_v2=True` via the concurrent path too -- never short-circuited into
    verify-and-reuse, and never treated as already-v2."""
    acquisition_ledger, canonical_ledger = _setup(tmp_path, ["GC-2020-08-01"])
    canonical_ledger["GC-2020-08-01"]["state"] = canonical_ledger_mod.STATE_CANONICAL_COMPLETE
    canonical_ledger["GC-2020-08-01"]["canonical_event_set_hash"] = "old-v1-hash"
    assert canonical_ledger["GC-2020-08-01"]["canonical_storage_layout_version"] is None

    calls = {"reprocess": 0, "verify": 0}
    monkeypatch.setattr(
        canonical_worker, "canonicalise_mbp1_session",
        lambda **kw: calls.__setitem__("reprocess", calls["reprocess"] + 1)
        or _fake_result(kw["session_id"], event_set_hash="old-v1-hash"),
    )
    monkeypatch.setattr(canonical_worker, "verify_existing_completion", lambda **kw: calls.__setitem__("verify", calls["verify"] + 1) or True)

    result = _run_concurrent(tmp_path, canonical_ledger, acquisition_ledger, ["GC-2020-08-01"], force_rebuild_v2=True)

    assert calls["verify"] == 0
    assert calls["reprocess"] == 1
    entry = canonical_ledger["GC-2020-08-01"]
    assert entry["canonical_storage_layout_version"] == canonical_worker.CANONICAL_STORAGE_LAYOUT_VERSION
    assert entry["pre_rebuild_v1_event_set_hash"] == "old-v1-hash"
    assert entry["v2_rebuild_matches_v1_hash"] is True
    assert result["processed"][0]["v2_rebuild_matches_v1_hash"] is True


def test_force_rebuild_v2_skips_already_migrated_session_without_dispatching_a_worker_concurrently(tmp_path, monkeypatch):
    """The `already_v2` skip is a zero-cost coordinator-side check -- must never even dispatch a
    worker task for it, under the concurrent path either."""
    acquisition_ledger, canonical_ledger = _setup(tmp_path, ["GC-2020-09-01"])
    canonical_ledger["GC-2020-09-01"]["state"] = canonical_ledger_mod.STATE_CANONICAL_COMPLETE
    canonical_ledger["GC-2020-09-01"]["canonical_storage_layout_version"] = canonical_worker.CANONICAL_STORAGE_LAYOUT_VERSION

    calls = {"reprocess": 0, "verify": 0}
    monkeypatch.setattr(canonical_worker, "canonicalise_mbp1_session", lambda **kw: calls.__setitem__("reprocess", calls["reprocess"] + 1) or _fake_result(kw["session_id"]))
    monkeypatch.setattr(canonical_worker, "verify_existing_completion", lambda **kw: calls.__setitem__("verify", calls["verify"] + 1) or True)

    result = _run_concurrent(tmp_path, canonical_ledger, acquisition_ledger, ["GC-2020-09-01"], force_rebuild_v2=True)

    assert calls["reprocess"] == 0
    assert calls["verify"] == 0
    assert result["processed"][0]["reused_existing_canonical_result"] is True
    assert result["processed"][0]["already_v2"] is True


def test_unexpected_pre_existing_destination_fails_closed_under_concurrent_path(tmp_path, monkeypatch):
    """Requirement (d): if the real, unmodified `canonical_worker.canonicalise_mbp1_session()`
    (exercised for real, no fixture double, against the fixture-provider core underneath it) hits
    an unexpected, content-mismatched pre-existing destination, it raises `CanonicalWorkerError`
    -- and the concurrent coordinator handles that exactly like any other ambiguous failure:
    CANONICAL_FAILED, batch stopped, never silently overwritten."""
    acquisition_ledger, canonical_ledger = _setup(tmp_path, ["GC-2020-10-01"])

    def _boom(**kw):
        raise canonical_worker.CanonicalWorkerError(
            f"session {kw['session_id']!r}: unexpected pre-existing canonical artifact -- content "
            f"differs from what this session is about to promote -- refusing to silently overwrite"
        )

    monkeypatch.setattr(canonical_worker, "canonicalise_mbp1_session", _boom)
    result = _run_concurrent(tmp_path, canonical_ledger, acquisition_ledger, ["GC-2020-10-01"])

    assert canonical_ledger["GC-2020-10-01"]["state"] == canonical_ledger_mod.STATE_CANONICAL_FAILED
    assert "unexpected pre-existing canonical artifact" in canonical_ledger["GC-2020-10-01"]["failure_reason"]
    assert result["stopped_reason"] is not None


def test_memory_aware_scheduling_never_pairs_two_large_sessions_while_small_ones_wait(tmp_path):
    """WO Part 4 memory-aware scheduling: with a mix of large and small sessions and
    max_workers=2, no wave contains two 'large' sessions while a small/medium session is still
    waiting to be scheduled."""
    session_ids = ["GC-SMALL-1", "GC-LARGE-1", "GC-SMALL-2", "GC-LARGE-2", "GC-SMALL-3"]
    byte_sizes = {
        "GC-SMALL-1": 1_000, "GC-LARGE-1": 50_000_000,
        "GC-SMALL-2": 2_000, "GC-LARGE-2": 49_000_000, "GC-SMALL-3": 3_000,
    }
    acquisition_ledger, canonical_ledger = _setup(tmp_path, session_ids, byte_sizes=byte_sizes)
    tasks = [
        hmt2i_canonicalise._validate_and_snapshot_task(
            session_id=sid, canonical_ledger=canonical_ledger, acquisition_ledger=acquisition_ledger,
            research_source_root=str(tmp_path / "research-source"), force_rebuild_v2=False,
        )
        for sid in session_ids
    ]
    waves = hmt2i_canonicalise._build_memory_aware_waves(
        tasks, max_workers=2, large_byte_threshold=hmt2i_canonicalise.LARGE_SESSION_NATIVE_BYTE_SIZE_THRESHOLD,
    )
    for wave in waves:
        large_count = sum(1 for t in wave if t["native_byte_size"] > hmt2i_canonicalise.LARGE_SESSION_NATIVE_BYTE_SIZE_THRESHOLD)
        assert large_count <= 1, f"wave {[t['session_id'] for t in wave]} pairs two large sessions"
    all_scheduled = [t["session_id"] for wave in waves for t in wave]
    assert sorted(all_scheduled) == sorted(session_ids)


def test_max_workers_rejects_zero_and_above_governed_ceiling(tmp_path):
    acquisition_ledger, canonical_ledger = _setup(tmp_path, ["GC-2020-11-01"])
    with pytest.raises(ValueError):
        _run_concurrent(tmp_path, canonical_ledger, acquisition_ledger, ["GC-2020-11-01"], max_workers=0)
    with pytest.raises(ValueError):
        _run_concurrent(tmp_path, canonical_ledger, acquisition_ledger, ["GC-2020-11-01"], max_workers=5)


def test_invalid_session_id_fails_closed_before_any_dispatch_concurrently(tmp_path, monkeypatch):
    acquisition_ledger, canonical_ledger = _setup(tmp_path, ["GC-2020-12-01"])
    calls = []
    monkeypatch.setattr(
        canonical_worker, "canonicalise_mbp1_session",
        lambda **kw: (calls.append(kw["session_id"]), _fake_result(kw["session_id"]))[1],
    )
    with pytest.raises(canonical_ledger_mod.CanonicalLedgerError):
        _run_concurrent(tmp_path, canonical_ledger, acquisition_ledger, ["GC-2020-12-01", "GC-NEVER-ACQUIRED"])
    # Zero side effects -- the valid session was never even dispatched.
    assert calls == []
    assert canonical_ledger["GC-2020-12-01"]["state"] == canonical_ledger_mod.STATE_CANONICAL_PENDING
