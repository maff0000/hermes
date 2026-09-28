"""HMT-2 (real-money checkpoint, MBP-1 quote resolution) — integration tests for
research/hmt2/generate_session_contract_activity_v1.py, run against the REAL committed
corpus-selection-manifest-v2.json (448 sessions) and gc-outright-active-windows-v1.json (93
clean outright contracts' real activation/expiration windows, derived from the real, retained
GC.FUT definitions artefact — see the WO final report).

Mirrors tests/hmt2/test_manifest_v2_generation.py's own real-evidence reproducibility-check
pattern: this deliberately RE-RUNS the generation script's `main()` (writing to the same real
output path it always writes to) — "deterministic, reproducible" means doing so is idempotent
and never produces different bytes.
"""
from __future__ import annotations

import importlib.util
import json
import os

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT_PATH = os.path.join(REPO_ROOT, "research", "hmt2", "generate_session_contract_activity_v1.py")
MANIFEST_PATH = os.path.join(REPO_ROOT, "research", "hmt2", "corpus-selection-manifest-v2.json")
ACTIVE_WINDOWS_PATH = os.path.join(REPO_ROOT, "research", "hmt2", "gc-outright-active-windows-v1.json")
OUT_PATH = os.path.join(REPO_ROOT, "research", "hmt2", "gc-session-contract-activity-v1.json")

pytestmark = pytest.mark.skipif(
    not (os.path.exists(MANIFEST_PATH) and os.path.exists(ACTIVE_WINDOWS_PATH)),
    reason="requires the real, committed corpus-selection-manifest-v2.json and "
    "gc-outright-active-windows-v1.json evidence files",
)


def _run_generation_script():
    spec = importlib.util.spec_from_file_location("generate_session_contract_activity_v1", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main()
    return module


def _load_output():
    with open(OUT_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_generation_script_is_byte_for_byte_reproducible():
    _run_generation_script()
    with open(OUT_PATH, "rb") as f:
        first_bytes = f.read()
    _run_generation_script()
    with open(OUT_PATH, "rb") as f:
        second_bytes = f.read()
    assert first_bytes == second_bytes


def test_generation_never_modifies_its_two_inputs():
    with open(MANIFEST_PATH, "rb") as f:
        manifest_before = f.read()
    with open(ACTIVE_WINDOWS_PATH, "rb") as f:
        windows_before = f.read()
    _run_generation_script()
    with open(MANIFEST_PATH, "rb") as f:
        manifest_after = f.read()
    with open(ACTIVE_WINDOWS_PATH, "rb") as f:
        windows_after = f.read()
    assert manifest_before == manifest_after
    assert windows_before == windows_after


def test_total_session_count_is_exactly_448():
    _run_generation_script()
    doc = _load_output()
    assert doc["total_sessions"] == 448
    assert len(doc["sessions"]) == 448


def test_clean_outright_contract_count_is_93():
    _run_generation_script()
    doc = _load_output()
    assert doc["clean_outright_contract_count_in_mapping_table"] == 93


def test_every_session_has_at_least_one_active_contract():
    _run_generation_script()
    doc = _load_output()
    for session in doc["sessions"]:
        assert len(session["active_raw_symbols"]) >= 1, session["session_id"]
    assert doc["sessions_with_zero_active_contracts"] == 0


def test_contract_session_counts_sum_matches_total_active_memberships():
    _run_generation_script()
    doc = _load_output()
    total_from_sessions = sum(len(s["active_raw_symbols"]) for s in doc["sessions"])
    total_from_counts = sum(doc["contract_session_counts"].values())
    assert total_from_sessions == total_from_counts


def test_no_duplicate_session_ids():
    _run_generation_script()
    doc = _load_output()
    session_ids = [s["session_id"] for s in doc["sessions"]]
    assert len(session_ids) == len(set(session_ids))
