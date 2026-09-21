"""HMT-2 (real-money checkpoint, MBP-1 quote resolution) — tests for the pure, network-free
request-construction step of research/hmt2/hmt2c_mbp1_quote.py (`build_session_runs()`), run
against the REAL committed gc-session-contract-activity-v1.json (448 sessions) and
corpus-selection-manifest-v2.json. This never touches the network — `build_session_runs()`
only reads two already-committed JSON files and calls the pure `session_calendar`/
`mbp1_quote_request` modules.
"""
from __future__ import annotations

import importlib.util
import os

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT_PATH = os.path.join(REPO_ROOT, "research", "hmt2", "hmt2c_mbp1_quote.py")
ACTIVITY_PATH = os.path.join(REPO_ROOT, "research", "hmt2", "gc-session-contract-activity-v1.json")
MANIFEST_PATH = os.path.join(REPO_ROOT, "research", "hmt2", "corpus-selection-manifest-v2.json")

pytestmark = pytest.mark.skipif(
    not (os.path.exists(ACTIVITY_PATH) and os.path.exists(MANIFEST_PATH)),
    reason="requires the real, committed gc-session-contract-activity-v1.json and "
    "corpus-selection-manifest-v2.json evidence files",
)


def _load_module():
    spec = importlib.util.spec_from_file_location("hmt2c_mbp1_quote", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_session_runs_is_deterministic_across_calls():
    module = _load_module()
    first = module.build_session_runs()
    second = module.build_session_runs()
    assert first == second


def test_every_selected_session_appears_in_exactly_one_run():
    module = _load_module()
    runs = module.build_session_runs()
    all_session_ids = [sid for run in runs for sid in run.session_ids]
    assert len(all_session_ids) == 448
    assert len(set(all_session_ids)) == 448


def test_no_run_exceeds_the_vendor_2000_symbol_limit():
    module = _load_module()
    runs = module.build_session_runs()
    for run in runs:
        assert run.symbol_count <= 2000, run


def test_runs_are_never_empty_and_end_is_never_before_start():
    module = _load_module()
    runs = module.build_session_runs()
    assert len(runs) > 0
    for run in runs:
        assert run.session_ids
        assert run.raw_symbols
        assert run.start_utc < run.end_utc_exclusive


def test_distinct_contract_count_across_all_runs_matches_active_windows_evidence():
    module = _load_module()
    runs = module.build_session_runs()
    distinct = {symbol for run in runs for symbol in run.raw_symbols}
    assert len(distinct) == 93
