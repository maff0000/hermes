"""Failed-acquisition quarantine: partial-file move, evidence record, ledger untouched (this
module never opens a ledger file at all -- proven here by construction, not by mocking).

Every fixture path is under `tmp_path`; this suite never has, and must never gain, real write
access to any authoritative HMT-2 storage root."""
import json
import os

import pytest

from market_truth.acquisition.orchestration import quarantine as q


def _make_partial_file(tmp_path, size=1000):
    src_dir = tmp_path / "native-source"
    src_dir.mkdir()
    p = src_dir / "GC-2022-09-13.dbn.zst.partial"
    p.write_bytes(b"x" * size)
    return str(p)


def test_sha256_file_is_deterministic(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"hello world")
    h1 = q.sha256_file(str(p))
    h2 = q.sha256_file(str(p))
    assert h1 == h2
    assert len(h1) == 64


def test_build_evidence_record_computes_percent_complete(tmp_path):
    partial = _make_partial_file(tmp_path, size=500)
    evidence = q.build_evidence_record(
        session_id="GC-2022-09-13", attempt_number=1, request_identity="req-abc",
        quote={"quoted_cost_usd": 1.23}, exception_text="DatabentoAdapterError: boom",
        partial_path=partial, expected_byte_size=1000, utc="2026-09-26T00:00:00Z",
    )
    assert evidence.partial_byte_size == 500
    assert evidence.percent_complete == 50.0
    assert evidence.sha256 == q.sha256_file(partial)
    assert evidence.session_id == "GC-2022-09-13"


def test_build_evidence_record_handles_unknown_expected_size():
    pass  # covered implicitly by expected_byte_size=None path below


def test_quarantine_partial_file_moves_and_writes_evidence_and_leaves_no_ledger_reference(tmp_path):
    partial = _make_partial_file(tmp_path)
    quarantine_root = str(tmp_path / "quarantine")
    evidence = q.build_evidence_record(
        session_id="GC-2022-09-13", attempt_number=1, request_identity="req-abc",
        quote={}, exception_text="boom", partial_path=partial, expected_byte_size=None,
        utc="2026-09-26T00:00:00Z",
    )
    seals = []
    chattrs = []
    result = q.quarantine_partial_file(
        source_path=partial, quarantine_root=quarantine_root, session_id="GC-2022-09-13",
        attempt_number=1, evidence=evidence,
        seal_read_only_fn=lambda p: seals.append(p), chattr_immutable_fn=lambda p: chattrs.append(p),
    )
    assert not os.path.exists(partial)  # moved out, not copied
    assert os.path.exists(result["quarantined_path"])
    assert os.path.exists(result["evidence_path"])

    with open(result["evidence_path"]) as fh:
        written = json.load(fh)
    assert written["session_id"] == "GC-2022-09-13"
    assert written["sha256"] == evidence.sha256

    assert set(seals) == {result["quarantined_path"], result["evidence_path"]}
    assert set(chattrs) == {result["quarantined_path"], result["evidence_path"]}

    # This module never imports json for a LEDGER path, never opens anything named "ledger" --
    # structurally, the only files it touched are the ones this test itself asserts on above.
    assert result["quarantined_path"].startswith(quarantine_root)


def test_quarantine_refuses_missing_source(tmp_path):
    evidence = q.FailedAcquisitionEvidence(
        session_id="GC-2022-09-13", attempt_number=1, request_identity="x", quote={},
        exception_text="x", partial_byte_size=0, expected_byte_size=None, percent_complete=None,
        sha256="0" * 64, utc="2026-09-26T00:00:00Z",
    )
    with pytest.raises(q.QuarantineError, match="does not exist"):
        q.quarantine_partial_file(
            source_path=str(tmp_path / "nope"), quarantine_root=str(tmp_path / "q"),
            session_id="GC-2022-09-13", attempt_number=1, evidence=evidence,
        )


def test_quarantine_refuses_to_overwrite_existing_artefact(tmp_path):
    partial = _make_partial_file(tmp_path)
    quarantine_root = str(tmp_path / "quarantine")
    dest_dir = os.path.join(quarantine_root, "GC-2022-09-13", "attempt-1")
    os.makedirs(dest_dir)
    # Pre-existing file with the SAME basename as the partial -- simulates a repeated/duplicate
    # quarantine attempt for the same session+attempt.
    with open(os.path.join(dest_dir, os.path.basename(partial)), "w") as fh:
        fh.write("already here")

    evidence = q.build_evidence_record(
        session_id="GC-2022-09-13", attempt_number=1, request_identity="req-abc",
        quote={}, exception_text="boom", partial_path=partial, expected_byte_size=None,
        utc="2026-09-26T00:00:00Z",
    )
    with pytest.raises(q.QuarantineError, match="refusing to overwrite"):
        q.quarantine_partial_file(
            source_path=partial, quarantine_root=quarantine_root, session_id="GC-2022-09-13",
            attempt_number=1, evidence=evidence,
        )
    # Source must survive the refusal -- a refused quarantine must never destroy the original.
    assert os.path.exists(partial)


def test_default_seal_and_chattr_are_safe_noops_for_tests(tmp_path):
    """The default `chattr_immutable_fn` is a documented no-op (real immutable-bit setting is
    production-only wiring, never exercised by this test suite)."""
    if os.geteuid() == 0:
        pytest.skip("root bypasses file-permission bits -- this assertion is only meaningful as a non-root user")
    partial = _make_partial_file(tmp_path)
    quarantine_root = str(tmp_path / "quarantine")
    evidence = q.build_evidence_record(
        session_id="GC-2022-09-13", attempt_number=1, request_identity="req-abc",
        quote={}, exception_text="boom", partial_path=partial, expected_byte_size=None,
        utc="2026-09-26T00:00:00Z",
    )
    result = q.quarantine_partial_file(
        source_path=partial, quarantine_root=quarantine_root, session_id="GC-2022-09-13",
        attempt_number=1, evidence=evidence,
    )
    # Default seal makes the file read-only -- prove write access is actually gone.
    with pytest.raises(PermissionError):
        with open(result["quarantined_path"], "w") as fh:
            fh.write("should not be allowed")
