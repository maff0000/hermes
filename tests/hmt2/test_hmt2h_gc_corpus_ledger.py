"""HMT-2 (real-money checkpoint) — tests for `research/hmt2/hmt2h_gc_corpus_ledger.py`: the
governed GC MBP-1 corpus acquisition ledger (structure/schema only — no network, no vendor SDK).

Mirrors `tests/hmt2/test_mbp1_pilot_acquisition.py`'s own established fixture/import-loading
discipline (dynamic module load by file path, since `research/hmt2/*.py` scripts are not a
Python package).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from market_truth.acquisition.source_store import compute_request_identity  # noqa: E402


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ledger_mod = _load_module("hmt2h_gc_corpus_ledger", "research/hmt2/hmt2h_gc_corpus_ledger.py")


_SESSION_A = {
    "session_id": "GC-2020-01-02",
    "trade_date": "2020-01-02",
    "active_raw_symbols": ["GCG0", "GCJ0"],
    "request_start_utc": "2020-01-01T22:00:00+00:00",
    "request_end_utc": "2020-01-02T21:00:00+00:00",
}
_SESSION_B_PILOT = {
    "session_id": "GC-2019-03-22",
    "trade_date": "2019-03-22",
    "active_raw_symbols": ["GCG0", "GCH9"],
    "request_start_utc": "2019-03-21T22:00:00+00:00",
    "request_end_utc": "2019-03-22T21:00:00+00:00",
}


def _pilot_identity() -> str:
    return compute_request_identity(
        dataset=ledger_mod.DATASET, schema=ledger_mod.SCHEMA,
        symbols=tuple(_SESSION_B_PILOT["active_raw_symbols"]), stype_in=ledger_mod.STYPE_IN,
        start=_SESSION_B_PILOT["request_start_utc"], end=_SESSION_B_PILOT["request_end_utc"],
    )


# ----------------------------------------------------------------------------------------------
# build_initial_ledger — pilot sessions reused/COMPLETE, everything else PLANNED.
# ----------------------------------------------------------------------------------------------

def test_build_initial_ledger_marks_pilot_session_complete_and_reused_from_catalogue():
    pilot_identity = _pilot_identity()
    pilot_catalogue = {
        pilot_identity: {
            "object_relative_path": "hmt2-gc-mbp1-v1/sessions/GC-2019-03-22/source/gc_mbp1_GC-2019-03-22.dbn.zst",
            "byte_size": 27213500,
            "sha256": "0a67a24f74d75ee4f82ca57acf1bdded4d4e9a56122dbd64c5bc6207261c9f31",
            "reported_record_count": 1266770,
            "acquisition_utc": "2026-09-22T05:34:32.589391+00:00",
        }
    }
    ledger = ledger_mod.build_initial_ledger(
        activity_sessions=[_SESSION_A, _SESSION_B_PILOT],
        pilot_session_ids=frozenset({"GC-2019-03-22"}),
        pilot_catalogue=pilot_catalogue,
        compute_request_identity_fn=compute_request_identity,
    )
    assert ledger["GC-2019-03-22"]["state"] == ledger_mod.STATE_COMPLETE
    assert ledger["GC-2019-03-22"]["pilot_reused"] is True
    assert ledger["GC-2019-03-22"]["artefact"]["sha256"] == pilot_catalogue[pilot_identity]["sha256"]
    assert ledger["GC-2020-01-02"]["state"] == ledger_mod.STATE_PLANNED
    assert ledger["GC-2020-01-02"]["pilot_reused"] is False
    assert ledger["GC-2020-01-02"]["artefact"] is None


def test_build_initial_ledger_fails_closed_if_declared_pilot_session_missing_from_catalogue():
    with pytest.raises(ledger_mod.LedgerError):
        ledger_mod.build_initial_ledger(
            activity_sessions=[_SESSION_B_PILOT],
            pilot_session_ids=frozenset({"GC-2019-03-22"}),
            pilot_catalogue={},  # empty -- no matching entry
            compute_request_identity_fn=compute_request_identity,
        )


def test_request_identity_recorded_on_every_entry_matches_compute_request_identity():
    ledger = ledger_mod.build_initial_ledger(
        activity_sessions=[_SESSION_A],
        pilot_session_ids=frozenset(),
        pilot_catalogue={},
        compute_request_identity_fn=compute_request_identity,
    )
    expected = compute_request_identity(
        dataset=ledger_mod.DATASET, schema=ledger_mod.SCHEMA,
        symbols=tuple(_SESSION_A["active_raw_symbols"]), stype_in=ledger_mod.STYPE_IN,
        start=_SESSION_A["request_start_utc"], end=_SESSION_A["request_end_utc"],
    )
    assert ledger["GC-2020-01-02"]["request_identity"] == expected


# ----------------------------------------------------------------------------------------------
# load_ledger / save_ledger_atomic — round-trip, atomicity.
# ----------------------------------------------------------------------------------------------

def test_load_ledger_returns_empty_dict_when_file_missing(tmp_path):
    assert ledger_mod.load_ledger(str(tmp_path / "nope.json")) == {}


def test_save_then_load_round_trips_exactly(tmp_path):
    ledger = ledger_mod.build_initial_ledger(
        activity_sessions=[_SESSION_A], pilot_session_ids=frozenset(), pilot_catalogue={},
        compute_request_identity_fn=compute_request_identity,
    )
    path = str(tmp_path / "ledger.json")
    ledger_mod.save_ledger_atomic(path, ledger)
    reloaded = ledger_mod.load_ledger(path)
    assert reloaded == ledger


def test_save_ledger_atomic_never_leaves_a_tmp_file_behind(tmp_path):
    path = str(tmp_path / "sub" / "ledger.json")
    ledger_mod.save_ledger_atomic(path, {"a": 1})
    assert not (tmp_path / "sub" / "ledger.json.tmp").exists()
    assert (tmp_path / "sub" / "ledger.json").exists()


# ----------------------------------------------------------------------------------------------
# summarize / sum_completed_actual_cost_usd
# ----------------------------------------------------------------------------------------------

def test_summarize_counts_by_state():
    ledger = {
        "a": {"session_id": "a", "state": ledger_mod.STATE_PLANNED},
        "b": {"session_id": "b", "state": ledger_mod.STATE_COMPLETE},
        "c": {"session_id": "c", "state": ledger_mod.STATE_COMPLETE},
        "d": {"session_id": "d", "state": ledger_mod.STATE_FAILED_AMBIGUOUS},
    }
    summary = ledger_mod.summarize(ledger)
    assert summary["total_sessions"] == 4
    assert summary["by_state"][ledger_mod.STATE_PLANNED] == 1
    assert summary["by_state"][ledger_mod.STATE_COMPLETE] == 2
    assert summary["by_state"][ledger_mod.STATE_FAILED_AMBIGUOUS] == 1
    assert summary["by_state"][ledger_mod.STATE_IN_PROGRESS] == 0


def test_summarize_fails_closed_on_invalid_state():
    with pytest.raises(ledger_mod.LedgerError):
        ledger_mod.summarize({"a": {"session_id": "a", "state": "BOGUS"}})


def test_sum_completed_actual_cost_usd_excludes_pilot_reused_by_default():
    ledger = {
        "pilot": {"state": ledger_mod.STATE_COMPLETE, "pilot_reused": True, "actual_cost_usd": 999.0},
        "real1": {"state": ledger_mod.STATE_COMPLETE, "pilot_reused": False, "actual_cost_usd": 0.10},
        "real2": {"state": ledger_mod.STATE_COMPLETE, "pilot_reused": False, "actual_cost_usd": 0.20},
        "planned": {"state": ledger_mod.STATE_PLANNED, "pilot_reused": False, "actual_cost_usd": None},
    }
    assert ledger_mod.sum_completed_actual_cost_usd(ledger) == pytest.approx(0.30)


def test_sum_completed_actual_cost_usd_can_include_pilot_reused():
    ledger = {
        "pilot": {"state": ledger_mod.STATE_COMPLETE, "pilot_reused": True, "actual_cost_usd": 1.0},
    }
    assert ledger_mod.sum_completed_actual_cost_usd(ledger, exclude_pilot_reused=False) == pytest.approx(1.0)


# ----------------------------------------------------------------------------------------------
# check_cost_ceiling — the $100 stop-before-this-request guard.
# ----------------------------------------------------------------------------------------------

def test_check_cost_ceiling_passes_under_ceiling():
    ledger_mod.check_cost_ceiling(spend_so_far=50.0, quoted_cost=10.0, ceiling=100.0)  # no raise


def test_check_cost_ceiling_raises_when_projection_exceeds_ceiling():
    with pytest.raises(ledger_mod.CostCeilingExceededError):
        ledger_mod.check_cost_ceiling(spend_so_far=95.0, quoted_cost=10.0, ceiling=100.0)


def test_check_cost_ceiling_boundary_exactly_at_ceiling_passes():
    ledger_mod.check_cost_ceiling(spend_so_far=90.0, quoted_cost=10.0, ceiling=100.0)  # no raise
