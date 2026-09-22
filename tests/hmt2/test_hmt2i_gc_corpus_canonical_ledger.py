"""HMT-2 canonicalisation checkpoint — tests for
`research/hmt2/hmt2i_gc_corpus_canonical_ledger.py`: the dedicated canonical-processing ledger
(structure/schema only — no network, no vendor SDK).

Mirrors `tests/hmt2/test_hmt2h_gc_corpus_ledger.py`'s own established dynamic-module-load
discipline exactly (`research/hmt2/*.py` scripts are not a Python package).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


source_ledger_mod = _load_module("hmt2h_gc_corpus_ledger", "research/hmt2/hmt2h_gc_corpus_ledger.py")
canonical_ledger_mod = _load_module("hmt2i_gc_corpus_canonical_ledger", "research/hmt2/hmt2i_gc_corpus_canonical_ledger.py")


def _acquisition_entry(session_id, trade_date, *, state, sha256="a" * 64, request_identity="req-0001"):
    return {
        "session_id": session_id,
        "trade_date": trade_date,
        "request_identity": request_identity,
        "state": state,
        "artefact": ({"sha256": sha256, "object_relative_path": f"sessions/{session_id}/source/x.dbn.zst"} if state == source_ledger_mod.STATE_COMPLETE else None),
    }


# ----------------------------------------------------------------------------------------------
# The acquisition ledger's own COMPLETE-state literal never drifts from the real module.
# ----------------------------------------------------------------------------------------------

def test_source_state_complete_literal_matches_real_acquisition_ledger():
    assert canonical_ledger_mod.SOURCE_STATE_COMPLETE == source_ledger_mod.STATE_COMPLETE


# ----------------------------------------------------------------------------------------------
# build_initial_canonical_ledger — only SOURCE-COMPLETE sessions get a row, always PENDING.
# ----------------------------------------------------------------------------------------------

def test_only_source_complete_sessions_get_a_row():
    acquisition_ledger = {
        "GC-2020-01-01": _acquisition_entry("GC-2020-01-01", "2020-01-01", state=source_ledger_mod.STATE_COMPLETE),
        "GC-2020-01-02": _acquisition_entry("GC-2020-01-02", "2020-01-02", state=source_ledger_mod.STATE_PLANNED),
        "GC-2020-01-03": _acquisition_entry("GC-2020-01-03", "2020-01-03", state=source_ledger_mod.STATE_FAILED_AMBIGUOUS),
    }
    ledger = canonical_ledger_mod.build_initial_canonical_ledger(acquisition_ledger=acquisition_ledger)
    assert set(ledger) == {"GC-2020-01-01"}
    assert ledger["GC-2020-01-01"]["state"] == canonical_ledger_mod.STATE_CANONICAL_PENDING
    assert ledger["GC-2020-01-01"]["lineage_record_relative_path"] is None
    assert ledger["GC-2020-01-01"]["native_artefact_sha256_at_seed_time"] == "a" * 64


def test_source_complete_and_canonical_pending_coexist_for_the_same_session_id():
    """The WO's own explicit expectation: SOURCE COMPLETE + CANONICAL_PENDING is normal, not a
    bug — two independent durable facts about the same session_id."""
    acquisition_ledger = {"GC-2020-01-01": _acquisition_entry("GC-2020-01-01", "2020-01-01", state=source_ledger_mod.STATE_COMPLETE)}
    canonical_ledger = canonical_ledger_mod.build_initial_canonical_ledger(acquisition_ledger=acquisition_ledger)
    assert acquisition_ledger["GC-2020-01-01"]["state"] == source_ledger_mod.STATE_COMPLETE
    assert canonical_ledger["GC-2020-01-01"]["state"] == canonical_ledger_mod.STATE_CANONICAL_PENDING


# ----------------------------------------------------------------------------------------------
# merge_new_source_complete_sessions — resumable, never overwrites an existing row.
# ----------------------------------------------------------------------------------------------

def test_merge_adds_only_newly_source_complete_sessions_and_never_overwrites():
    acquisition_ledger = {
        "GC-2020-01-01": _acquisition_entry("GC-2020-01-01", "2020-01-01", state=source_ledger_mod.STATE_COMPLETE),
    }
    canonical_ledger = canonical_ledger_mod.build_initial_canonical_ledger(acquisition_ledger=acquisition_ledger)
    canonical_ledger["GC-2020-01-01"]["state"] = canonical_ledger_mod.STATE_CANONICAL_COMPLETE  # simulate prior work

    acquisition_ledger["GC-2020-01-02"] = _acquisition_entry("GC-2020-01-02", "2020-01-02", state=source_ledger_mod.STATE_COMPLETE)

    added = canonical_ledger_mod.merge_new_source_complete_sessions(
        canonical_ledger=canonical_ledger, acquisition_ledger=acquisition_ledger,
    )
    assert added == 1
    assert canonical_ledger["GC-2020-01-01"]["state"] == canonical_ledger_mod.STATE_CANONICAL_COMPLETE  # untouched
    assert canonical_ledger["GC-2020-01-02"]["state"] == canonical_ledger_mod.STATE_CANONICAL_PENDING


# ----------------------------------------------------------------------------------------------
# load/save atomicity.
# ----------------------------------------------------------------------------------------------

def test_load_returns_empty_dict_when_file_missing(tmp_path):
    assert canonical_ledger_mod.load_canonical_ledger(str(tmp_path / "nope.json")) == {}


def test_save_then_load_round_trips_exactly(tmp_path):
    acquisition_ledger = {"GC-2020-01-01": _acquisition_entry("GC-2020-01-01", "2020-01-01", state=source_ledger_mod.STATE_COMPLETE)}
    ledger = canonical_ledger_mod.build_initial_canonical_ledger(acquisition_ledger=acquisition_ledger)
    path = str(tmp_path / "ledger.json")
    canonical_ledger_mod.save_canonical_ledger_atomic(path, ledger)
    reloaded = canonical_ledger_mod.load_canonical_ledger(path)
    assert reloaded == ledger


def test_save_never_leaves_a_tmp_file_behind(tmp_path):
    path = str(tmp_path / "sub" / "ledger.json")
    canonical_ledger_mod.save_canonical_ledger_atomic(path, {"a": 1})
    assert not (tmp_path / "sub" / "ledger.json.tmp").exists()
    assert (tmp_path / "sub" / "ledger.json").exists()


# ----------------------------------------------------------------------------------------------
# summarize_canonical.
# ----------------------------------------------------------------------------------------------

def test_summarize_counts_by_state():
    ledger = {
        "a": {"session_id": "a", "state": canonical_ledger_mod.STATE_CANONICAL_PENDING},
        "b": {"session_id": "b", "state": canonical_ledger_mod.STATE_CANONICAL_COMPLETE},
        "c": {"session_id": "c", "state": canonical_ledger_mod.STATE_CANONICAL_COMPLETE},
        "d": {"session_id": "d", "state": canonical_ledger_mod.STATE_CANONICAL_FAILED},
    }
    summary = canonical_ledger_mod.summarize_canonical(ledger)
    assert summary["total_sessions"] == 4
    assert summary["by_state"][canonical_ledger_mod.STATE_CANONICAL_PENDING] == 1
    assert summary["by_state"][canonical_ledger_mod.STATE_CANONICAL_COMPLETE] == 2
    assert summary["by_state"][canonical_ledger_mod.STATE_CANONICAL_FAILED] == 1
    assert summary["by_state"][canonical_ledger_mod.STATE_CANONICAL_IN_PROGRESS] == 0


def test_summarize_fails_closed_on_invalid_state():
    with pytest.raises(canonical_ledger_mod.CanonicalLedgerError):
        canonical_ledger_mod.summarize_canonical({"a": {"session_id": "a", "state": "NOT_A_REAL_STATE"}})


def test_canonical_ledger_content_sha256_is_deterministic_and_order_independent():
    ledger_a = {"a": {"x": 1}, "b": {"y": 2}}
    ledger_b = {"b": {"y": 2}, "a": {"x": 1}}
    assert canonical_ledger_mod.canonical_ledger_content_sha256(ledger_a) == canonical_ledger_mod.canonical_ledger_content_sha256(ledger_b)
