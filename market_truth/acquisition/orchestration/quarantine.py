"""market_truth.acquisition.orchestration.quarantine -- failed-acquisition quarantine.

Ports the hand-executed GC-2022-09-13 incident procedure (Trinity handoff,
`reports/incident_retry_mechanisms.md`) as a real, tested mechanism.

Invariant preserved exactly: the acquisition LEDGER ROW is never touched by anything in this
module. On `FAILED_AMBIGUOUS`, the partial file is moved OUT of the authoritative native-source
location into a dedicated quarantine path and sealed; retry lineage is recorded EXTERNALLY, here,
as an evidence record -- never by widening the ledger schema (no ledger mutation is authorised by
this dispatch; see `retry.py` for the SEPARATE, explicitly-authorised ledger-repair step that
must not be confused with quarantine).

The quarantine move is a same-filesystem atomic rename (`os.rename`), never copy+delete -- if the
quarantine root is not on the same filesystem as the source, `os.rename` raises `OSError` and this
module fails closed rather than silently falling back to a non-atomic copy.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from typing import Callable, Optional


class QuarantineError(RuntimeError):
    """Raised when a quarantine operation cannot be completed safely."""


def sha256_file(path: str, *, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class FailedAcquisitionEvidence:
    session_id: str
    attempt_number: int
    request_identity: str
    quote: dict
    exception_text: str
    partial_byte_size: int
    expected_byte_size: Optional[int]
    percent_complete: Optional[float]
    sha256: str
    utc: str

    def as_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "attempt_number": self.attempt_number,
            "request_identity": self.request_identity,
            "quote": self.quote,
            "exception_text": self.exception_text,
            "partial_byte_size": self.partial_byte_size,
            "expected_byte_size": self.expected_byte_size,
            "percent_complete": self.percent_complete,
            "sha256": self.sha256,
            "utc": self.utc,
        }


def build_evidence_record(
    *,
    session_id: str,
    attempt_number: int,
    request_identity: str,
    quote: dict,
    exception_text: str,
    partial_path: str,
    expected_byte_size: Optional[int],
    utc: str,
) -> FailedAcquisitionEvidence:
    partial_byte_size = os.path.getsize(partial_path)
    percent_complete = None
    if expected_byte_size:
        percent_complete = round(100.0 * partial_byte_size / expected_byte_size, 4)
    return FailedAcquisitionEvidence(
        session_id=session_id,
        attempt_number=attempt_number,
        request_identity=request_identity,
        quote=quote,
        exception_text=exception_text,
        partial_byte_size=partial_byte_size,
        expected_byte_size=expected_byte_size,
        percent_complete=percent_complete,
        sha256=sha256_file(partial_path),
        utc=utc,
    )


def _default_seal_read_only(path: str) -> None:
    os.chmod(path, stat.S_IRUSR | stat.S_IRGRP)


def _noop_chattr_immutable(path: str) -> None:
    """Default no-op. Production wiring supplies a real `chattr +i` implementation (e.g. via
    `subprocess.run(["chattr", "+i", path], check=True)`); tests never need real immutable-bit
    privilege and must never be handed a callable that actually sets it on a shared filesystem.
    """
    return None


def quarantine_partial_file(
    *,
    source_path: str,
    quarantine_root: str,
    session_id: str,
    attempt_number: int,
    evidence: FailedAcquisitionEvidence,
    seal_read_only_fn: Callable[[str], None] = _default_seal_read_only,
    chattr_immutable_fn: Callable[[str], None] = _noop_chattr_immutable,
) -> dict:
    """Atomically (same-filesystem rename) moves `source_path` into
    `<quarantine_root>/<session_id>/attempt-<attempt_number>/`, writes the evidence record
    alongside it as JSON, and seals both read-only (+ the injected immutable-bit callable).
    Leaves the acquisition ledger completely untouched -- this function never opens, reads, or
    writes any ledger file.

    Returns a dict with the resolved `quarantined_path` and `evidence_path`.
    """
    if not os.path.isfile(source_path):
        raise QuarantineError(f"source_path does not exist or is not a regular file: {source_path!r}")

    dest_dir = os.path.join(quarantine_root, session_id, f"attempt-{attempt_number}")
    os.makedirs(dest_dir, exist_ok=True)

    dest_path = os.path.join(dest_dir, os.path.basename(source_path))
    if os.path.exists(dest_path):
        raise QuarantineError(f"refusing to overwrite existing quarantine artefact: {dest_path!r}")

    try:
        os.rename(source_path, dest_path)
    except OSError as exc:
        raise QuarantineError(
            f"quarantine move failed (must be a same-filesystem atomic rename): {exc}"
        ) from exc

    evidence_path = os.path.join(dest_dir, "evidence.json")
    with open(evidence_path, "w", encoding="utf-8") as fh:
        json.dump(evidence.as_dict(), fh, indent=2, sort_keys=True)
        fh.write("\n")

    seal_read_only_fn(dest_path)
    seal_read_only_fn(evidence_path)
    chattr_immutable_fn(dest_path)
    chattr_immutable_fn(evidence_path)

    return {"quarantined_path": dest_path, "evidence_path": evidence_path}
