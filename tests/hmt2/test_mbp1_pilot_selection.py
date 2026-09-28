"""HMT-2 (real-money checkpoint, MBP-1 pilot) — Part 1 deterministic pilot-selection
reproducibility. Pure, no network: reads the committed, hash-verified corpus-selection manifest
v2 and exercises `hmt2d_mbp1_pilot_selection_and_quote.select_pilot_pair` directly.
"""
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RESEARCH_HMT2_DIR = REPO_ROOT / "research" / "hmt2"
MANIFEST_PATH = RESEARCH_HMT2_DIR / "corpus-selection-manifest-v2.json"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from market_truth.acquisition.corpus_manifest_v2 import read_manifest_v2  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "hmt2d_mbp1_pilot_selection_and_quote", RESEARCH_HMT2_DIR / "hmt2d_mbp1_pilot_selection_and_quote.py"
)
_hmt2d = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_hmt2d)

EXPECTED_MANIFEST_SHA256 = "962f49c2d607d4aaad6d511314bcc47ee02491f85feb019849fc02390c2a497c"
EXPECTED_SEED_INPUT = f"HERMES|HMT-2|MBP1-PILOT-V1|{EXPECTED_MANIFEST_SHA256}"
EXPECTED_SEED_DIGEST = "176eeaabf0b43c19715160e63636b2ed86c3c27f10e552e995184ef7fd8f5d24"
EXPECTED_PILOT_INDEX = 22
EXPECTED_EVENT_SESSION_ID = "GC-2019-03-29"
EXPECTED_CONTROL_SESSION_ID = "GC-2019-03-22"


def _load_manifest():
    return read_manifest_v2(str(MANIFEST_PATH))


def test_manifest_hash_matches_the_governed_value():
    doc = _load_manifest()
    assert doc["manifest_sha256"] == EXPECTED_MANIFEST_SHA256


def test_pilot_selection_is_exactly_reproducible_across_independent_calls():
    doc = _load_manifest()
    result_a = _hmt2d.select_pilot_pair(doc)
    result_b = _hmt2d.select_pilot_pair(doc)
    assert result_a == result_b


def test_pilot_selection_matches_the_recorded_governed_values():
    doc = _load_manifest()
    result = _hmt2d.select_pilot_pair(doc)
    assert result["manifest_sha256"] == EXPECTED_MANIFEST_SHA256
    assert result["seed_input"] == EXPECTED_SEED_INPUT
    assert result["pilot_seed_digest"] == EXPECTED_SEED_DIGEST
    assert result["candidate_pair_count"] == 79
    assert result["pilot_index"] == EXPECTED_PILOT_INDEX
    assert result["selected_event_session_id"] == EXPECTED_EVENT_SESSION_ID
    assert result["selected_control_session_id"] == EXPECTED_CONTROL_SESSION_ID


def test_seed_digest_is_a_real_sha256_of_the_documented_seed_input():
    import hashlib

    assert hashlib.sha256(EXPECTED_SEED_INPUT.encode("utf-8")).hexdigest() == EXPECTED_SEED_DIGEST


def test_pilot_index_is_seed_integer_mod_candidate_count():
    assert int(EXPECTED_SEED_DIGEST, 16) % 79 == EXPECTED_PILOT_INDEX


def test_exactly_79_scheduled_event_and_79_matched_control_rows_with_one_to_one_lineage():
    doc = _load_manifest()
    rows = doc["rows"]
    events = [r for r in rows if r["primary_stratum"] == "SCHEDULED_EVENT"]
    controls = [r for r in rows if r["primary_stratum"] == "MATCHED_CONTROL"]
    assert len(events) == 79
    assert len(controls) == 79

    event_ids = {e["session_id"] for e in events}
    parents = [c["matched_parent_session"] for c in controls]
    assert set(parents) == event_ids, "every event must be referenced by exactly one control, and vice versa"
    assert len(set(parents)) == len(parents), "no event may be referenced by more than one control"


def test_selection_fails_closed_on_a_tampered_manifest_hash():
    doc = _load_manifest()
    tampered = dict(doc)
    tampered["manifest_sha256"] = "0" * 64
    import market_truth.acquisition.corpus_manifest_v2 as cm2

    try:
        _hmt2d.select_pilot_pair(tampered)
    except cm2.ManifestIntegrityErrorV2:
        pass
    else:
        raise AssertionError("expected ManifestIntegrityErrorV2 on a tampered manifest_sha256")


def test_selection_fails_closed_when_pair_lineage_is_broken():
    """Break lineage (point every MATCHED_CONTROL row's matched_parent_session at a
    non-existent id) while keeping the manifest'S OWN self-integrity hash consistent with the
    tampered content — isolating the LINEAGE check (`PilotSelectionError`) from the separate,
    already-covered integrity check (`ManifestIntegrityErrorV2`, see the test above)."""
    import hashlib

    from market_truth.acquisition.corpus_manifest import canonical_json

    doc = _load_manifest()
    rows = [dict(r) for r in doc["rows"]]
    for r in rows:
        if r["primary_stratum"] == "MATCHED_CONTROL":
            r["matched_parent_session"] = "GC-1900-01-01"
    payload = {"metadata": doc["metadata"], "rows": rows}
    recomputed_hash = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    broken = {"manifest_sha256": recomputed_hash, "metadata": doc["metadata"], "rows": rows}

    try:
        _hmt2d.select_pilot_pair(broken)
    except _hmt2d.PilotSelectionError:
        pass
    else:
        raise AssertionError("expected PilotSelectionError when lineage is broken")
