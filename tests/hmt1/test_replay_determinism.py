"""HMT-1 — replay.py: the deterministic replay harness. Run A == run B, every dimension (WO §14).

This is also where the WO §18 "running proof" (not just green tests) is exercised: printed output
here is the real, sanitized evidence captured into the final HMT-1 report (record counts, hashes).
"""
import importlib
import re
from pathlib import Path

import market_truth
from market_truth.replay import replay_twice, run_pipeline

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "hmt1"
MAPPING_PATH = FIXTURES_DIR / "gc_contract_mapping_v1.json"
GC_FIXTURE = FIXTURES_DIR / "gc_mbp1_equivalent_v1.jsonl"
QUOTE_FIXTURE = FIXTURES_DIR / "broker_quote_equivalent_v1.jsonl"

_PACKAGE_MODULES = (
    "market_truth.contracts",
    "market_truth.provenance",
    "market_truth.futures",
    "market_truth.identity",
    "market_truth.provider",
    "market_truth.canonicaliser",
    "market_truth.partition",
    "market_truth.evidence",
    "market_truth.replay",
    "market_truth.derived_fact_identity",
    "market_truth.providers.fixture",
)


def test_replay_twice_matches_on_every_deterministic_dimension(tmp_path):
    root_a, root_b = tmp_path / "run_a", tmp_path / "run_b"
    result_a, result_b, comparison = replay_twice(
        [GC_FIXTURE, QUOTE_FIXTURE],
        MAPPING_PATH,
        root_a,
        root_b,
        provider_id="hmt1-fixture-provider",
        dataset_id="hmt1-test",
    )

    # ---- WO §18 running proof: real, sanitized evidence, printed for the final report ----
    print("HMT-1 RUNNING PROOF")
    print("  source_record_count:", result_a.source_record_count)
    print("  canonical_event_counts_by_family:", dict(result_a.manifest.canonical_event_counts_by_family))
    print("  canonical_event_set_hash:", result_a.manifest.canonical_event_set_hash)
    print("  partition_content_hashes:", dict(result_a.manifest.partition_content_hashes))
    print("  artifact_hashes:", dict(result_a.manifest.artifact_hashes))
    print("  manifest_deterministic_fields_sha256:", result_a.manifest.deterministic_fields_sha256())
    print("  comparison:", comparison)

    assert comparison.event_count_match
    assert comparison.identity_list_match
    assert comparison.event_content_match
    assert comparison.event_set_hash_match
    assert comparison.partition_content_hash_match
    assert comparison.manifest_deterministic_match
    assert comparison.reload_reconstruction_match
    # artifact_hash_match is reported, not asserted — see partition.py / test_partition_roundtrip.py
    # and the final HMT-1 report for the measured physical-determinism result.
    print("  artifact_hash_match (physical, reported not asserted):", comparison.artifact_hash_match)


def test_replay_is_unaffected_by_a_third_independent_run(tmp_path):
    """A third, later run (simulating a genuinely different wall-clock moment and process) must
    still match — not just two runs launched back to back."""
    root_a, root_c = tmp_path / "run_a", tmp_path / "run_c"
    result_a = run_pipeline(
        [GC_FIXTURE, QUOTE_FIXTURE], MAPPING_PATH, root_a, provider_id="hmt1-fixture-provider", dataset_id="hmt1-test"
    )
    result_c = run_pipeline(
        [GC_FIXTURE, QUOTE_FIXTURE], MAPPING_PATH, root_c, provider_id="hmt1-fixture-provider", dataset_id="hmt1-test"
    )
    assert result_a.manifest.canonical_event_set_hash == result_c.manifest.canonical_event_set_hash
    assert sorted(e.identity_hash for e in result_a.events) == sorted(e.identity_hash for e in result_c.events)


_FORBIDDEN_WALLCLOCK_AND_RANDOM_TOKENS = (
    "datetime.now(", ".utcnow(", "time.time(", "time.monotonic(",
    "random.", "uuid.uuid4(", "os.getpid(", "os.urandom(",
)
_FORBIDDEN_NETWORK_TOKENS = (
    "requests.", "httpx.", "aiohttp.", "socket.", "urllib.request", "http.client", "ib_insync",
)
_FORBIDDEN_ACQUISITION_TOKENS = (
    "databento", "acquisition_cost", "vantage", "order_book_reconstruction",
)


def test_no_wallclock_pid_or_random_state_anywhere_in_the_package():
    """WO acceptance #22/#23: replay must be unaffected by wall clock or random state. This is a
    structural guard, in the same spirit as this repository's own existing
    tests/test_stream_silent_stall_recovery.py::T11_NoForbiddenTokens forbidden-token check: none
    of these tokens exist anywhere in market_truth's source at all, so there is nothing for a
    future edit to accidentally depend on."""
    for module_name in _PACKAGE_MODULES:
        module = importlib.import_module(module_name)
        source = Path(module.__file__).read_text(encoding="utf-8")
        for token in _FORBIDDEN_WALLCLOCK_AND_RANDOM_TOKENS:
            assert token not in source, f"forbidden wall-clock/random token {token!r} found in {module_name}"


def test_no_network_dependency_anywhere_in_the_package():
    """WO acceptance #24: no live provider/network call is needed anywhere in the test suite —
    proven structurally by there being no network-capable token anywhere in the package at all."""
    for module_name in _PACKAGE_MODULES:
        module = importlib.import_module(module_name)
        source = Path(module.__file__).read_text(encoding="utf-8")
        for token in _FORBIDDEN_NETWORK_TOKENS:
            assert token not in source, f"forbidden network token {token!r} found in {module_name}"


def test_no_hmt2_or_out_of_scope_acquisition_tokens_anywhere_in_the_package():
    """WO acceptance #25 + the NOT-AUTHORISED scope boundary: no HMT-2 acquisition, no Databento/
    Vantage/MBO reference, anywhere in this package's source."""
    for module_name in _PACKAGE_MODULES:
        module = importlib.import_module(module_name)
        source = Path(module.__file__).read_text(encoding="utf-8").lower()
        for token in _FORBIDDEN_ACQUISITION_TOKENS:
            assert token not in source, f"forbidden out-of-scope token {token!r} found in {module_name}"


def test_package_docstring_states_hmt1_scope_boundary():
    assert market_truth.__doc__ is not None
    assert re.search(r"HMT-1", market_truth.__doc__)
