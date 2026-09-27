"""Ledger-truth-derived allowlist derivation -- never a hardcoded ordinal position, always
re-derived fresh, correctly, after a simulated intervening ledger state change (a guard-fire
pause, or a retry-repair)."""
import json

import pytest

from market_truth.acquisition.orchestration import chunk_allowlist as ca

SOURCE_COMPLETE = "COMPLETE"
CANONICAL_COMPLETE = "CANONICAL_COMPLETE"


def _acq_ledger(*complete_ids):
    return {sid: {"state": SOURCE_COMPLETE} for sid in complete_ids}


def _canon_ledger(*complete_ids):
    return {sid: {"state": CANONICAL_COMPLETE} for sid in complete_ids}


def test_validate_session_id_grammar():
    with pytest.raises(ca.AllowlistError, match="grammar"):
        ca.validate_session_id("not-a-session", {"not-a-session"})


def test_validate_session_id_requires_acquisition_complete_membership():
    with pytest.raises(ca.AllowlistError, match="not COMPLETE"):
        ca.validate_session_id("GC-2020-01-01", set())
    assert ca.validate_session_id("GC-2020-01-01", {"GC-2020-01-01"}) == "GC-2020-01-01"


def test_derive_pending_sessions_is_set_difference_sorted():
    acquisition = _acq_ledger("GC-2020-01-03", "GC-2020-01-01", "GC-2020-01-02")
    canonical = _canon_ledger("GC-2020-01-01")
    pending = ca.derive_pending_sessions(
        acquisition_ledger=acquisition, canonical_ledger=canonical,
        source_state_complete=SOURCE_COMPLETE, canonical_state_complete=CANONICAL_COMPLETE,
    )
    assert pending == ["GC-2020-01-02", "GC-2020-01-03"]


def test_build_chunk_is_bounded():
    acquisition = _acq_ledger(*[f"GC-2020-01-{d:02d}" for d in range(1, 21)])
    canonical = {}
    chunk = ca.build_chunk(
        acquisition_ledger=acquisition, canonical_ledger=canonical, max_chunk_size=10,
        source_state_complete=SOURCE_COMPLETE, canonical_state_complete=CANONICAL_COMPLETE,
    )
    assert len(chunk) == 10
    assert chunk == [f"GC-2020-01-{d:02d}" for d in range(1, 11)]


def test_build_chunk_rejects_nonpositive_size():
    with pytest.raises(ca.AllowlistError):
        ca.build_chunk(
            acquisition_ledger={}, canonical_ledger={}, max_chunk_size=0,
            source_state_complete=SOURCE_COMPLETE, canonical_state_complete=CANONICAL_COMPLETE,
        )


def test_chunk_resumes_correctly_after_an_intervening_guard_fire():
    """Simulates: a chunk is built, one session (GC-2020-01-01) genuinely completes canonically,
    a guard fires mid-session on GC-2020-01-02 (still pending, NOT complete), and the batch is
    resumed. Re-deriving the chunk from CURRENT ledger truth must still include GC-2020-01-02 and
    GC-2020-01-03 -- never skip past GC-2020-01-02 based on its position in the FIRST chunk."""
    acquisition = _acq_ledger("GC-2020-01-01", "GC-2020-01-02", "GC-2020-01-03")
    canonical_before = {}
    first_chunk = ca.build_chunk(
        acquisition_ledger=acquisition, canonical_ledger=canonical_before, max_chunk_size=10,
        source_state_complete=SOURCE_COMPLETE, canonical_state_complete=CANONICAL_COMPLETE,
    )
    assert first_chunk == ["GC-2020-01-01", "GC-2020-01-02", "GC-2020-01-03"]

    # GC-2020-01-01 completes; GC-2020-01-02's guard fires (ledger never reaches CANONICAL_COMPLETE).
    canonical_after_fire = _canon_ledger("GC-2020-01-01")

    resumed_chunk = ca.build_chunk(
        acquisition_ledger=acquisition, canonical_ledger=canonical_after_fire, max_chunk_size=10,
        source_state_complete=SOURCE_COMPLETE, canonical_state_complete=CANONICAL_COMPLETE,
    )
    assert resumed_chunk == ["GC-2020-01-02", "GC-2020-01-03"]


def test_load_requested_session_ids_reads_json_file(tmp_path):
    path = tmp_path / "allowlist.json"
    path.write_text(json.dumps({"session_ids": ["GC-2020-01-01", "GC-2020-01-02"]}))
    assert ca.load_requested_session_ids(str(path)) == ["GC-2020-01-01", "GC-2020-01-02"]


def test_load_requested_session_ids_rejects_empty_or_missing_list(tmp_path):
    path = tmp_path / "allowlist.json"
    path.write_text(json.dumps({"session_ids": []}))
    with pytest.raises(ca.AllowlistError):
        ca.load_requested_session_ids(str(path))

    path2 = tmp_path / "allowlist2.json"
    path2.write_text(json.dumps({}))
    with pytest.raises(ca.AllowlistError):
        ca.load_requested_session_ids(str(path2))


def test_validate_and_partition_allowlist_skips_already_complete_without_grammar_check():
    """An already-canonical-complete session is skipped outright -- even if (hypothetically) it
    no longer matches the acquisition-complete set, since it is not re-validated once already
    known-good."""
    acquisition = _acq_ledger("GC-2020-01-01")
    canonical = _canon_ledger("GC-2020-01-02")  # complete in canonical, absent from acquisition-complete
    validated, already_complete = ca.validate_and_partition_allowlist(
        ["GC-2020-01-01", "GC-2020-01-02"],
        canonical_ledger=canonical, acquisition_ledger=acquisition,
        source_state_complete=SOURCE_COMPLETE, canonical_state_complete=CANONICAL_COMPLETE,
    )
    assert validated == ["GC-2020-01-01"]
    assert already_complete == ["GC-2020-01-02"]


def test_validate_and_partition_allowlist_raises_on_first_invalid_id_fail_closed():
    acquisition = _acq_ledger("GC-2020-01-01")
    canonical = {}
    with pytest.raises(ca.AllowlistError):
        ca.validate_and_partition_allowlist(
            ["GC-2020-01-01", "GC-2099-99-99"],  # second id not acquisition-complete
            canonical_ledger=canonical, acquisition_ledger=acquisition,
            source_state_complete=SOURCE_COMPLETE, canonical_state_complete=CANONICAL_COMPLETE,
        )
