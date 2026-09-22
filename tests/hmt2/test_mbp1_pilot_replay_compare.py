"""HMT-2 (real-money checkpoint, MBP-1 pilot) — Part 5: unit tests for
`hmt2g_mbp1_pilot_replay_compare.compare_summaries()`, the pure comparison logic the real
3-independent-subprocess replay-determinism proof is built on. Uses small, clearly-synthetic
run-summary dicts (never real pilot data — the real pipeline is exercised deliberately outside
pytest/CI, see `test_mbp1_pilot_replay_script_guard.py`'s docstring) to prove the comparator
itself correctly PASSes on genuine matches and fails closed on every real mismatch dimension —
mirroring HMT-1's own `test_replay_determinism.py::test_all_match_is_false_when_physical_
artifact_hashes_differ` discipline: prove the GATE, not just the pipeline.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_SPEC = importlib.util.spec_from_file_location(
    "hmt2g_mbp1_pilot_replay_compare", REPO_ROOT / "research" / "hmt2" / "hmt2g_mbp1_pilot_replay_compare.py"
)
hmt2g = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(hmt2g)


def _base_summary(**overrides) -> dict:
    summary = {
        "pythonhashseed": "0",
        "pid": 111,
        "source_hashes": {"GC-2019-03-22": "aaa", "GC-2019-03-29": "bbb"},
        "record_counts_by_session": {"GC-2019-03-22": 100, "GC-2019-03-29": 10},
        "source_record_count": 110,
        "canonical_event_counts_by_family": {"market_trade": 5, "top_of_book": 20},
        "canonical_event_set_hash": "deadbeef",
        "identity_hashes_sorted": ["h1", "h2", "h3"],
        "event_rows_sorted_hex": ["r1", "r2", "r3"],
        "reload_reconstructed_rows_sorted_hex": ["r1", "r2", "r3"],
        "reload_reconstruction_matches_live": True,
        "partition_content_hashes": {"part-a": "pa1", "part-b": "pb1"},
        "artifact_hashes": {"part-a": "aa1", "part-b": "ab1"},
        "manifest_deterministic_fields_sha256": "manifestsha",
    }
    summary.update(overrides)
    return summary


def test_three_genuinely_identical_runs_pass_every_dimension():
    run1, run2, run3 = _base_summary(pid=1, pythonhashseed="0"), _base_summary(pid=2, pythonhashseed="1"), _base_summary(pid=3, pythonhashseed="42")
    comparison = hmt2g.compare_summaries([run1, run2, run3])
    assert comparison["all_pass"] is True
    for dim, status in comparison["results"].items():
        assert status == "PASS", f"{dim}: expected PASS, got {status}"


def test_source_hash_mismatch_fails_closed():
    run1 = _base_summary()
    run2 = _base_summary(source_hashes={"GC-2019-03-22": "DIFFERENT", "GC-2019-03-29": "bbb"})
    comparison = hmt2g.compare_summaries([run1, run2])
    assert comparison["all_pass"] is False
    assert comparison["results"]["source_hashes"] != "PASS"


def test_identity_hash_list_mismatch_fails_closed():
    run1 = _base_summary()
    run2 = _base_summary(identity_hashes_sorted=["h1", "h2", "h4"])
    comparison = hmt2g.compare_summaries([run1, run2])
    assert comparison["all_pass"] is False
    assert comparison["results"]["identity_hashes_sorted"] != "PASS"


def test_event_row_content_mismatch_fails_closed():
    run1 = _base_summary()
    run2 = _base_summary(event_rows_sorted_hex=["r1", "r2", "DIFFERENT"])
    comparison = hmt2g.compare_summaries([run1, run2])
    assert comparison["all_pass"] is False
    assert comparison["results"]["event_rows_sorted_hex"] != "PASS"


def test_event_set_hash_mismatch_fails_closed():
    run1 = _base_summary()
    run2 = _base_summary(canonical_event_set_hash="different_hash")
    comparison = hmt2g.compare_summaries([run1, run2])
    assert comparison["all_pass"] is False
    assert comparison["results"]["canonical_event_set_hash"] != "PASS"


def test_partition_content_hash_mismatch_fails_closed():
    run1 = _base_summary()
    run2 = _base_summary(partition_content_hashes={"part-a": "DIFFERENT", "part-b": "pb1"})
    comparison = hmt2g.compare_summaries([run1, run2])
    assert comparison["all_pass"] is False
    assert comparison["results"]["partition_content_hashes"] != "PASS"


def test_physical_artifact_hash_mismatch_fails_closed():
    run1 = _base_summary()
    run2 = _base_summary(artifact_hashes={"part-a": "DIFFERENT", "part-b": "ab1"})
    comparison = hmt2g.compare_summaries([run1, run2])
    assert comparison["all_pass"] is False
    assert comparison["results"]["artifact_hashes"] != "PASS"


def test_manifest_deterministic_fields_hash_mismatch_fails_closed():
    run1 = _base_summary()
    run2 = _base_summary(manifest_deterministic_fields_sha256="different")
    comparison = hmt2g.compare_summaries([run1, run2])
    assert comparison["all_pass"] is False
    assert comparison["results"]["manifest_deterministic_fields_sha256"] != "PASS"


def test_reload_reconstruction_failure_in_any_single_run_fails_closed():
    run1 = _base_summary()
    run2 = _base_summary(reload_reconstruction_matches_live=False)
    comparison = hmt2g.compare_summaries([run1, run2])
    assert comparison["all_pass"] is False


def test_source_record_count_mismatch_fails_closed():
    run1 = _base_summary()
    run2 = _base_summary(source_record_count=999)
    comparison = hmt2g.compare_summaries([run1, run2])
    assert comparison["all_pass"] is False
    assert comparison["results"]["source_record_count"] != "PASS"


def test_canonical_event_counts_by_family_mismatch_fails_closed():
    run1 = _base_summary()
    run2 = _base_summary(canonical_event_counts_by_family={"market_trade": 5, "top_of_book": 21})
    comparison = hmt2g.compare_summaries([run1, run2])
    assert comparison["all_pass"] is False
    assert comparison["results"]["canonical_event_counts_by_family"] != "PASS"


def test_requires_at_least_two_summaries():
    import pytest

    with pytest.raises(ValueError):
        hmt2g.compare_summaries([_base_summary()])
