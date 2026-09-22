"""HMT-2 canonicalisation checkpoint — tests for
`research/hmt2/hmt2i_gc_corpus_canonicalise.py`'s `process_sessions()` orchestration loop:
explicit allowlist only, idempotent verify-and-skip, no-auto-retry-on-ambiguous-failure, a
session must never be falsely CANONICAL_COMPLETE.

Every test injects a FAKE `canonical_worker.canonicalise_mbp1_session()` /
`canonical_worker.verify_existing_completion()` — never a real vendor decode — mirroring
`tests/hmt2/test_hmt2h_gc_corpus_acquire.py`'s own established fake-dependency discipline
exactly.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from market_truth.acquisition import canonical_worker  # noqa: E402


def _load_module(name: str, relative_path: str):
    """Dynamically load a `research/hmt2/*.py` script by file path (it is not a Python package)
    — mirrors `tests/hmt2/test_hmt2h_gc_corpus_acquire.py`'s own established discipline, PLUS
    registers the freshly-loaded module into `sys.modules` under `name` before executing it.
    That registration matters here specifically: `hmt2i_gc_corpus_canonicalise.py` itself does a
    plain `import hmt2h_gc_corpus_ledger` / `import hmt2i_gc_corpus_canonical_ledger` at its own
    top level. Without pre-registering, that plain import would silently re-execute those files
    as a SECOND, distinct module (Python only reuses `sys.modules`, it does not deduplicate by
    file path), producing a second, distinct `CanonicalLedgerError` class object — so
    `pytest.raises(canonical_ledger_mod.CanonicalLedgerError)` in THIS file would never match an
    instance actually raised from inside `hmt2i_canonicalise.process_sessions()`. Loading
    dependencies (`source_ledger_mod`, `canonical_ledger_mod`) BEFORE the dependent module below
    ensures the plain imports inside it resolve to these exact SAME objects."""
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


def _acquisition_entry(session_id, trade_date, *, sha256="a" * 64, request_identity="req-0001"):
    return {
        "session_id": session_id, "trade_date": trade_date, "request_identity": request_identity,
        "state": source_ledger_mod.STATE_COMPLETE,
        "artefact": {"sha256": sha256, "object_relative_path": f"hmt2-gc-mbp1-v1/sessions/{session_id}/source/x.dbn.zst"},
    }


def _fake_empty_valid_result(session_id, *, event_set_hash="deadbeef"):
    return canonical_worker.SessionCanonicalisationResult(
        session_id=session_id, native_artefact_sha256="a" * 64, source_record_count=0,
        canonical_event_counts_by_family={}, canonical_event_set_hash=event_set_hash,
        partition_relative_paths=(), partition_semantic_hashes={}, partition_artifact_hashes={},
        evidence_manifest_relative_path=f"evidence/{session_id}.json",
        evidence_manifest_deterministic_hash="d" * 64,
        lineage_record_relative_path=f"lineage/{session_id}.json",
        quality_record_relative_path=f"quality/{session_id}.json",
        quality_summary={"native_record_count": 0},
        canonical_result_kind="EMPTY_VALID", empty_reason="SOURCE_RETURNED_ZERO_RECORDS",
    )


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


def _setup(tmp_path, session_ids):
    acquisition_ledger = {sid: _acquisition_entry(sid, f"2020-01-{i+1:02d}") for i, sid in enumerate(session_ids)}
    canonical_ledger = canonical_ledger_mod.build_initial_canonical_ledger(acquisition_ledger=acquisition_ledger)
    for sid in session_ids:
        acq = acquisition_ledger[sid]
        native_dir = tmp_path / "research-source" / acq["artefact"]["object_relative_path"]
        native_dir.parent.mkdir(parents=True, exist_ok=True)
        native_dir.write_bytes(b"native bytes")
    return acquisition_ledger, canonical_ledger


# ----------------------------------------------------------------------------------------------
# Happy path — a PENDING session is canonicalised and marked COMPLETE.
# ----------------------------------------------------------------------------------------------

def test_pending_session_is_canonicalised_and_marked_complete(tmp_path, monkeypatch):
    acquisition_ledger, canonical_ledger = _setup(tmp_path, ["GC-2020-01-01"])
    monkeypatch.setattr(canonical_worker, "canonicalise_mbp1_session", lambda **kw: _fake_result("GC-2020-01-01"))

    result = hmt2i_canonicalise.process_sessions(
        canonical_ledger=canonical_ledger, acquisition_ledger=acquisition_ledger,
        session_ids=["GC-2020-01-01"], canonical_store_root=tmp_path / "canonical-store",
        mapping_table_path=str(tmp_path / "mapping.json"),
        research_source_root=str(tmp_path / "research-source"),
        ledger_state_path=str(tmp_path / "canonical_ledger.json"),
    )

    assert result["stopped_reason"] is None
    assert canonical_ledger["GC-2020-01-01"]["state"] == canonical_ledger_mod.STATE_CANONICAL_COMPLETE
    assert canonical_ledger["GC-2020-01-01"]["canonical_event_set_hash"] == "deadbeef"
    assert canonical_ledger["GC-2020-01-01"]["lineage_record_relative_path"] == "lineage/GC-2020-01-01.json"
    reloaded = canonical_ledger_mod.load_canonical_ledger(str(tmp_path / "canonical_ledger.json"))
    assert reloaded == canonical_ledger


# ----------------------------------------------------------------------------------------------
# Scope discipline — only sessions in the explicit allowlist are ever touched.
# ----------------------------------------------------------------------------------------------

def test_only_allowlisted_sessions_are_touched(tmp_path, monkeypatch):
    acquisition_ledger, canonical_ledger = _setup(tmp_path, ["GC-2020-01-01", "GC-2020-01-02"])
    calls = []
    monkeypatch.setattr(
        canonical_worker, "canonicalise_mbp1_session",
        lambda **kw: (calls.append(kw["session_id"]), _fake_result(kw["session_id"]))[1],
    )

    hmt2i_canonicalise.process_sessions(
        canonical_ledger=canonical_ledger, acquisition_ledger=acquisition_ledger,
        session_ids=["GC-2020-01-01"], canonical_store_root=tmp_path / "canonical-store",
        mapping_table_path=str(tmp_path / "mapping.json"),
        research_source_root=str(tmp_path / "research-source"),
        ledger_state_path=str(tmp_path / "canonical_ledger.json"),
    )

    assert calls == ["GC-2020-01-01"]
    assert canonical_ledger["GC-2020-01-02"]["state"] == canonical_ledger_mod.STATE_CANONICAL_PENDING


def test_raises_if_session_has_no_canonical_ledger_row(tmp_path):
    acquisition_ledger, canonical_ledger = _setup(tmp_path, ["GC-2020-01-01"])
    with pytest.raises(canonical_ledger_mod.CanonicalLedgerError):
        hmt2i_canonicalise.process_sessions(
            canonical_ledger=canonical_ledger, acquisition_ledger=acquisition_ledger,
            session_ids=["GC-NEVER-ACQUIRED"], canonical_store_root=tmp_path / "canonical-store",
            mapping_table_path=str(tmp_path / "mapping.json"),
            research_source_root=str(tmp_path / "research-source"),
            ledger_state_path=str(tmp_path / "canonical_ledger.json"),
        )


def test_raises_if_acquisition_no_longer_reports_source_complete(tmp_path):
    acquisition_ledger, canonical_ledger = _setup(tmp_path, ["GC-2020-01-01"])
    acquisition_ledger["GC-2020-01-01"]["state"] = source_ledger_mod.STATE_FAILED_AMBIGUOUS
    with pytest.raises(canonical_ledger_mod.CanonicalLedgerError):
        hmt2i_canonicalise.process_sessions(
            canonical_ledger=canonical_ledger, acquisition_ledger=acquisition_ledger,
            session_ids=["GC-2020-01-01"], canonical_store_root=tmp_path / "canonical-store",
            mapping_table_path=str(tmp_path / "mapping.json"),
            research_source_root=str(tmp_path / "research-source"),
            ledger_state_path=str(tmp_path / "canonical_ledger.json"),
        )


# ----------------------------------------------------------------------------------------------
# Crash/failure safety — an ambiguous failure marks FAILED, stops the batch, never COMPLETE.
# ----------------------------------------------------------------------------------------------

def test_ambiguous_failure_marks_failed_stops_batch_never_complete(tmp_path, monkeypatch):
    acquisition_ledger, canonical_ledger = _setup(tmp_path, ["GC-2020-01-01", "GC-2020-01-02"])

    def _boom(**kw):
        raise RuntimeError("simulated ambiguous canonicalisation failure")

    monkeypatch.setattr(canonical_worker, "canonicalise_mbp1_session", _boom)

    result = hmt2i_canonicalise.process_sessions(
        canonical_ledger=canonical_ledger, acquisition_ledger=acquisition_ledger,
        session_ids=["GC-2020-01-01", "GC-2020-01-02"], canonical_store_root=tmp_path / "canonical-store",
        mapping_table_path=str(tmp_path / "mapping.json"),
        research_source_root=str(tmp_path / "research-source"),
        ledger_state_path=str(tmp_path / "canonical_ledger.json"),
    )

    assert canonical_ledger["GC-2020-01-01"]["state"] == canonical_ledger_mod.STATE_CANONICAL_FAILED
    assert "simulated ambiguous" in canonical_ledger["GC-2020-01-01"]["failure_reason"]
    assert result["stopped_reason"] is not None
    # batch stopped BEFORE the second session — never auto-retried, never silently continued
    assert canonical_ledger["GC-2020-01-02"]["state"] == canonical_ledger_mod.STATE_CANONICAL_PENDING
    reloaded = canonical_ledger_mod.load_canonical_ledger(str(tmp_path / "canonical_ledger.json"))
    assert reloaded["GC-2020-01-01"]["state"] == canonical_ledger_mod.STATE_CANONICAL_FAILED


# ----------------------------------------------------------------------------------------------
# Idempotent reprocessing — a claimed-COMPLETE session is verified, not blindly redone.
# ----------------------------------------------------------------------------------------------

def test_claimed_complete_session_is_verified_and_reused_not_reprocessed(tmp_path, monkeypatch):
    acquisition_ledger, canonical_ledger = _setup(tmp_path, ["GC-2020-01-01"])
    canonical_ledger["GC-2020-01-01"]["state"] = canonical_ledger_mod.STATE_CANONICAL_COMPLETE

    calls = {"reprocess": 0, "verify": 0}
    monkeypatch.setattr(canonical_worker, "canonicalise_mbp1_session", lambda **kw: calls.__setitem__("reprocess", calls["reprocess"] + 1) or _fake_result("GC-2020-01-01"))
    monkeypatch.setattr(canonical_worker, "verify_existing_completion", lambda **kw: calls.__setitem__("verify", calls["verify"] + 1) or True)

    result = hmt2i_canonicalise.process_sessions(
        canonical_ledger=canonical_ledger, acquisition_ledger=acquisition_ledger,
        session_ids=["GC-2020-01-01"], canonical_store_root=tmp_path / "canonical-store",
        mapping_table_path=str(tmp_path / "mapping.json"),
        research_source_root=str(tmp_path / "research-source"),
        ledger_state_path=str(tmp_path / "canonical_ledger.json"),
    )

    assert calls["verify"] == 1
    assert calls["reprocess"] == 0  # never blindly redone
    assert canonical_ledger["GC-2020-01-01"]["state"] == canonical_ledger_mod.STATE_CANONICAL_COMPLETE
    assert result["processed"][0]["reused_existing_canonical_result"] is True


def test_claimed_complete_session_failing_reverification_is_marked_failed_and_stops(tmp_path, monkeypatch):
    acquisition_ledger, canonical_ledger = _setup(tmp_path, ["GC-2020-01-01", "GC-2020-01-02"])
    canonical_ledger["GC-2020-01-01"]["state"] = canonical_ledger_mod.STATE_CANONICAL_COMPLETE

    def _fail_verify(**kw):
        raise canonical_worker.VerificationFailedError("simulated drift detected")

    monkeypatch.setattr(canonical_worker, "verify_existing_completion", _fail_verify)

    result = hmt2i_canonicalise.process_sessions(
        canonical_ledger=canonical_ledger, acquisition_ledger=acquisition_ledger,
        session_ids=["GC-2020-01-01", "GC-2020-01-02"], canonical_store_root=tmp_path / "canonical-store",
        mapping_table_path=str(tmp_path / "mapping.json"),
        research_source_root=str(tmp_path / "research-source"),
        ledger_state_path=str(tmp_path / "canonical_ledger.json"),
    )

    assert canonical_ledger["GC-2020-01-01"]["state"] == canonical_ledger_mod.STATE_CANONICAL_FAILED
    assert "simulated drift" in canonical_ledger["GC-2020-01-01"]["failure_reason"]
    assert result["stopped_reason"] is not None
    assert canonical_ledger["GC-2020-01-02"]["state"] == canonical_ledger_mod.STATE_CANONICAL_PENDING


# ------------------------------------------------------------------------------------------------
# Valid-empty architecture ruling — canonical_result_kind/empty_reason are persisted onto the
# ledger row, and both are CANONICAL_COMPLETE (no new lifecycle state).
# ------------------------------------------------------------------------------------------------

def test_empty_valid_session_is_marked_complete_with_result_kind_and_reason_persisted(tmp_path, monkeypatch):
    acquisition_ledger, canonical_ledger = _setup(tmp_path, ["GC-2020-01-01"])
    monkeypatch.setattr(
        canonical_worker, "canonicalise_mbp1_session", lambda **kw: _fake_empty_valid_result("GC-2020-01-01"),
    )

    result = hmt2i_canonicalise.process_sessions(
        canonical_ledger=canonical_ledger, acquisition_ledger=acquisition_ledger,
        session_ids=["GC-2020-01-01"], canonical_store_root=tmp_path / "canonical-store",
        mapping_table_path=str(tmp_path / "mapping.json"),
        research_source_root=str(tmp_path / "research-source"),
        ledger_state_path=str(tmp_path / "canonical_ledger.json"),
    )

    assert result["stopped_reason"] is None
    # Still CANONICAL_COMPLETE -- NOT a new lifecycle state.
    assert canonical_ledger["GC-2020-01-01"]["state"] == canonical_ledger_mod.STATE_CANONICAL_COMPLETE
    assert canonical_ledger["GC-2020-01-01"]["canonical_result_kind"] == "EMPTY_VALID"
    assert canonical_ledger["GC-2020-01-01"]["empty_reason"] == "SOURCE_RETURNED_ZERO_RECORDS"
    assert result["processed"][0]["canonical_result_kind"] == "EMPTY_VALID"
    assert result["processed"][0]["empty_reason"] == "SOURCE_RETURNED_ZERO_RECORDS"
    reloaded = canonical_ledger_mod.load_canonical_ledger(str(tmp_path / "canonical_ledger.json"))
    assert reloaded["GC-2020-01-01"]["canonical_result_kind"] == "EMPTY_VALID"


def test_nonempty_session_result_kind_defaults_to_nonempty_on_the_ledger(tmp_path, monkeypatch):
    """Regression pin: the ordinary NONEMPTY happy path (already proven by
    `test_pending_session_is_canonicalised_and_marked_complete` above) also now carries an
    explicit `canonical_result_kind` on the ledger row."""
    acquisition_ledger, canonical_ledger = _setup(tmp_path, ["GC-2020-01-01"])
    monkeypatch.setattr(canonical_worker, "canonicalise_mbp1_session", lambda **kw: _fake_result("GC-2020-01-01"))

    hmt2i_canonicalise.process_sessions(
        canonical_ledger=canonical_ledger, acquisition_ledger=acquisition_ledger,
        session_ids=["GC-2020-01-01"], canonical_store_root=tmp_path / "canonical-store",
        mapping_table_path=str(tmp_path / "mapping.json"),
        research_source_root=str(tmp_path / "research-source"),
        ledger_state_path=str(tmp_path / "canonical_ledger.json"),
    )
    assert canonical_ledger["GC-2020-01-01"]["canonical_result_kind"] == "NONEMPTY"
    assert canonical_ledger["GC-2020-01-01"]["empty_reason"] is None
