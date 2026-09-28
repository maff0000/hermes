"""HMT-2 (real-money checkpoint) — integration tests for
research/hmt2/generate_selection_manifest_v2.py, run against the REAL committed v1 manifest,
v1/v2 event snapshots, and the real reference-series-per-session-v2.json evidence (derived
from the real, retained GC.v.0 acquisition — see the WO final report). Mirrors
tests/hmt2/test_corpus_selection.py's own real-snapshot integration-check pattern.

This deliberately RE-RUNS the generation script's `main()` (writing to the same real output
path research/hmt2/corpus-selection-manifest-v2.json it always writes to) — the whole point of
"frozen, deterministic, reproducible" is that doing so is idempotent and never produces
different bytes. If this ever fails, the manifest is not actually reproducible and that is a
real defect, not a test artefact to route around.
"""
from __future__ import annotations

import importlib.util
import json
import os
from collections import Counter

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT_PATH = os.path.join(REPO_ROOT, "research", "hmt2", "generate_selection_manifest_v2.py")
V1_MANIFEST_PATH = os.path.join(REPO_ROOT, "research", "hmt2", "corpus-selection-manifest-v1.json")
V2_MANIFEST_PATH = os.path.join(REPO_ROOT, "research", "hmt2", "corpus-selection-manifest-v2.json")
REFERENCE_PER_SESSION_PATH = os.path.join(REPO_ROOT, "research", "hmt2", "reference-series-per-session-v2.json")

pytestmark = pytest.mark.skipif(
    not os.path.exists(REFERENCE_PER_SESSION_PATH),
    reason="requires the real, committed reference-series-per-session-v2.json evidence file",
)


def _run_generation_script():
    spec = importlib.util.spec_from_file_location("generate_selection_manifest_v2", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main()
    return module


def _load_v2_manifest():
    with open(V2_MANIFEST_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_generation_script_is_byte_for_byte_reproducible():
    _run_generation_script()
    with open(V2_MANIFEST_PATH, "rb") as f:
        first_bytes = f.read()
    _run_generation_script()
    with open(V2_MANIFEST_PATH, "rb") as f:
        second_bytes = f.read()
    assert first_bytes == second_bytes


def test_generation_never_modifies_v1_manifest():
    with open(V1_MANIFEST_PATH, "rb") as f:
        before = f.read()
    _run_generation_script()
    with open(V1_MANIFEST_PATH, "rb") as f:
        after = f.read()
    assert before == after


def test_v2_manifest_hash_integrity():
    from market_truth.acquisition.corpus_manifest_v2 import verify_manifest_integrity_v2

    _run_generation_script()
    document = _load_v2_manifest()
    verify_manifest_integrity_v2(document)  # must not raise


def test_v2_manifest_superseded_hash_matches_the_real_v1_manifest():
    with open(V1_MANIFEST_PATH, encoding="utf-8") as f:
        v1_document = json.load(f)

    _run_generation_script()
    document = _load_v2_manifest()
    assert document["metadata"]["superseded_manifest_sha256"] == v1_document["manifest_sha256"]
    assert document["metadata"]["superseded_manifest_relative_path"] == "research/hmt2/corpus-selection-manifest-v1.json"


def test_v2_manifest_has_no_duplicate_session_ids():
    _run_generation_script()
    document = _load_v2_manifest()
    session_ids = [row["session_id"] for row in document["rows"]]
    assert len(session_ids) == len(set(session_ids))


def test_v2_manifest_holdout_sessions_never_appear_as_a_non_holdout_stratum():
    _run_generation_script()
    document = _load_v2_manifest()
    holdout_ids = {row["session_id"] for row in document["rows"] if row["protected_holdout"]}
    non_holdout_ids = {row["session_id"] for row in document["rows"] if not row["protected_holdout"]}
    assert holdout_ids.isdisjoint(non_holdout_ids)
    # protected_holdout flag and primary_stratum must agree.
    for row in document["rows"]:
        assert row["protected_holdout"] == (row["primary_stratum"] == "PROTECTED_HOLDOUT")


def test_v2_manifest_every_stratum_present_and_nonempty():
    _run_generation_script()
    document = _load_v2_manifest()
    counts = Counter(row["primary_stratum"] for row in document["rows"])
    for stratum in (
        "SCHEDULED_EVENT", "MATCHED_CONTROL", "HIGH_VOL_NON_EVENT", "COMPRESSION",
        "RANDOM_DEVELOPMENT", "PROTECTED_HOLDOUT",
    ):
        assert counts[stratum] > 0, f"stratum {stratum} has zero sessions in the real v2 manifest"
    assert counts["PROTECTED_HOLDOUT"] == 100
    assert counts["RANDOM_DEVELOPMENT"] == 50


def test_v2_manifest_high_vol_and_compression_rows_carry_reference_evidence():
    _run_generation_script()
    document = _load_v2_manifest()
    for row in document["rows"]:
        if row["primary_stratum"] in ("HIGH_VOL_NON_EVENT", "COMPRESSION"):
            assert row["session_log_range"] is not None
            assert row["reference_quality"] == "REFERENCE_COMPLETE"
            assert row["reference_snapshot_version"]


def test_v2_manifest_matched_controls_are_absent_from_the_full_v2_event_snapshot():
    with open(os.path.join(REPO_ROOT, "research", "hmt2", "scheduled-macro-event-snapshot-v2.json"), encoding="utf-8") as f:
        v2_snapshot = json.load(f)
    full_v2_event_dates = {r["date"] for r in v2_snapshot["records"]}

    _run_generation_script()
    document = _load_v2_manifest()
    for row in document["rows"]:
        if row["primary_stratum"] == "MATCHED_CONTROL":
            assert row["gc_trade_date"] not in full_v2_event_dates


def test_v2_manifest_preserved_random_and_holdout_sessions_were_also_in_v1():
    with open(V1_MANIFEST_PATH, encoding="utf-8") as f:
        v1_document = json.load(f)
    v1_random_ids = {r["session_id"] for r in v1_document["rows"] if r["primary_stratum"] == "RANDOM_DEVELOPMENT"}
    v1_holdout_ids = {r["session_id"] for r in v1_document["rows"] if r["primary_stratum"] == "PROTECTED_HOLDOUT"}

    _run_generation_script()
    document = _load_v2_manifest()
    for row in document["rows"]:
        if row["inclusion_reason"] == "preserved_from_v1_random_development_draw":
            assert row["session_id"] in v1_random_ids
        if row["inclusion_reason"] == "preserved_from_v1_protected_holdout_draw":
            assert row["session_id"] in v1_holdout_ids


# ----------------------------------------------------------------------------------------------
# HMT-2 CORRECTION — real-run precedence + reason-log enforcement against the actual generated
# manifest and its companion allocation-evidence file.
# ----------------------------------------------------------------------------------------------

ALLOCATION_EVIDENCE_PATH = os.path.join(REPO_ROOT, "research", "hmt2", "corpus-selection-manifest-v2-allocation-evidence.json")


def _load_allocation_evidence():
    with open(ALLOCATION_EVIDENCE_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_v2_manifest_precedence_matched_control_high_vol_compression_never_overlap_holdout_or_random_dev():
    _run_generation_script()
    document = _load_v2_manifest()
    by_stratum: dict = {}
    for row in document["rows"]:
        by_stratum.setdefault(row["primary_stratum"], set()).add(row["session_id"])

    protected = by_stratum.get("PROTECTED_HOLDOUT", set())
    random_dev = by_stratum.get("RANDOM_DEVELOPMENT", set())
    for stratum in ("MATCHED_CONTROL", "HIGH_VOL_NON_EVENT", "COMPRESSION", "SCHEDULED_EVENT"):
        rows = by_stratum.get(stratum, set())
        assert rows.isdisjoint(protected), f"{stratum} overlaps PROTECTED_HOLDOUT"
        assert rows.isdisjoint(random_dev), f"{stratum} overlaps RANDOM_DEVELOPMENT"


def test_v2_manifest_holdout_replacement_log_never_contains_a_collision_reason():
    """HMT-2 CORRECTION regression guard: the real generated evidence's holdout invalidation
    log must never contain anything but genuine session-invalidity — specifically, it must
    NEVER contain the old, incorrect "collides with the v2 event snapshot" reason."""
    _run_generation_script()
    evidence = _load_allocation_evidence()
    log = evidence["protected_holdout"]["invalidation_log"]
    for entry in log:
        assert "COLLID" not in entry["reason"], f"holdout was invalidated for a collision reason: {entry}"


def test_v2_manifest_random_development_replacement_log_only_uses_permitted_reasons():
    """HMT-2 CORRECTION regression guard: the real generated evidence's random-development
    invalidation log must contain ONLY the two permitted reason categories — never anything
    volatility/compression-shaped."""
    _run_generation_script()
    evidence = _load_allocation_evidence()
    log = evidence["random_development"]["invalidation_log"]
    permitted = {"NOW_A_FINAL_SELECTED_SCHEDULED_EVENT_SESSION", "NOT_A_VALID_GOVERNED_SESSION_DATE"}
    for entry in log:
        assert entry["reason"] in permitted, f"disallowed random-development replacement reason: {entry}"
        assert "VOL" not in entry["reason"] and "COMPRESS" not in entry["reason"]


def test_v2_manifest_holdout_near_zero_churn():
    _run_generation_script()
    evidence = _load_allocation_evidence()
    assert evidence["protected_holdout"]["kept"] == 100
    assert evidence["protected_holdout"]["invalidated"] == 0


def test_v2_manifest_hamilton_allocation_tables_sum_to_target_for_both_strata():
    _run_generation_script()
    evidence = _load_allocation_evidence()
    for stratum in ("high_vol_non_event", "compression"):
        block = evidence[stratum]
        table_sum = sum(row["final_allocation"] for row in block["allocation_table"])
        assert table_sum == block["final_total"]
        if not block["shortfall"]:
            assert table_sum == block["target_total"]


def test_v2_manifest_secondary_context_never_changes_primary_stratum():
    _run_generation_script()
    document = _load_v2_manifest()
    for row in document["rows"]:
        if row.get("secondary_context"):
            assert row["primary_stratum"] == "PROTECTED_HOLDOUT"
            assert row["protected_holdout"] is True
