"""HERMES shared-stream recovery Phase-2 — BOUNDED APPEND-ONLY JSONL EVIDENCE WRITER (PRODUCTION-OWNED, INERT).

WO-HELM-HERMES-SHARED-STREAM-RECOVERY-PHASE2-SHADOW-ADAPTER-IMPLEMENTATION-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-17. Owner: HERMES (Helm).
Contract: docs/design/shared_stream_recovery/architecture_phase2_v1.md §7/§18/§20/§21/§30 (binding). Version "1".

STATUS: INERT / NOT WIRED. Imported by NO live runtime path; only tests + sibling utils/hermes_sss_* modules.
Nothing here creates a directory or writes a file at import — the writer is inert until explicitly constructed AND
`append()` is called by a (future) caller with an externally-configured path.

CAPABILITIES (§7):
  * APPEND-ONLY JSONL, deterministic single-line schema, UTC, source_sha / runtime identity / config_version /
    connection_generation / provenance / current-authority observation / shadow decision / comparison class carried
    through from the record payload.
  * Externally CONFIGURABLE path (no hard-coded env-specific path; the caller supplies it via config).
  * BOUNDED max file size -> ROTATION; RETENTION limit -> oldest rotated files pruned.
  * SAFE permissions (dir 0o700, file 0o600) best-effort on POSIX.
  * CHECKSUM: each rotated file gets a companion `<file>.sha256`; the active file checksum is available on demand.
  * ISOLATION: disk-full / write-failure / redaction-violation -> a bounded WriteResult(ok=False, reason=...); the
    writer NEVER raises into the caller, NEVER retries in a blocking loop, keeps NO unbounded in-memory queue, and
    can therefore NEVER affect the recovery path or the current authority.
  * SECURITY: every payload is scanned by the redaction layer; a prohibited field is refused (never written).

Standard-library only + the redaction layer. No Redis / SQL / network / subprocess / eval.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from utils.hermes_sss_redaction_v1 import scan_for_prohibited

_MAX_RECORD_BYTES = 64 * 1024  # a single shadow record is bounded (§20: snapshot ~8KB); reject a pathological line.


@dataclass(frozen=True)
class WriteResult:
    """Bounded local diagnostic of ONE append. Never affects the recovery path."""
    ok: bool
    reason: Optional[str] = None
    bytes_written: int = 0
    rotated: bool = False

    def to_dict(self) -> dict:
        return {"ok": self.ok, "reason": self.reason, "bytes_written": self.bytes_written, "rotated": self.rotated}


class BoundedJsonlWriter:
    """Append-only, size-bounded, rotating, retention-capped JSONL sink with checksums and failure isolation.

    Construction does NOT touch the filesystem. The directory is created lazily on the first successful `append`.
    The path is supplied by the caller (externalised — never a hard-coded env-specific constant)."""

    def __init__(
        self,
        path: str,
        *,
        max_bytes: int = 8 * 1024 * 1024,
        max_retained_files: int = 14,
        dir_mode: int = 0o700,
        file_mode: int = 0o600,
    ) -> None:
        if not isinstance(path, str) or not path:
            raise ValueError("path must be a non-empty string")
        if max_bytes < 1024:
            raise ValueError("max_bytes must be >= 1024")
        if max_retained_files < 1:
            raise ValueError("max_retained_files must be >= 1")
        self._path = Path(path)
        self._max_bytes = int(max_bytes)
        self._max_retained = int(max_retained_files)
        self._dir_mode = dir_mode
        self._file_mode = file_mode

    # ---- public API -------------------------------------------------------
    @property
    def path(self) -> Path:
        return self._path

    def append(self, payload: Mapping[str, object]) -> WriteResult:
        """Append ONE record as a single canonical JSON line. Returns a WriteResult; NEVER raises into the caller.

        Order of guards: redaction (secret rejection) -> serialise -> size bound -> ensure dir/perms -> rotate if
        needed -> atomic-ish append -> return. Any OSError (disk full, permission) is caught and reported, never
        propagated, never retried in a loop."""
        try:
            violations = scan_for_prohibited(payload)
            if violations:
                return WriteResult(ok=False, reason=f"redaction_violation:{violations[0].kind}")

            line = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            encoded = (line + "\n").encode("utf-8")
            if len(encoded) > _MAX_RECORD_BYTES:
                return WriteResult(ok=False, reason="record_too_large")

            rotated = False
            try:
                self._ensure_dir()
                if self._should_rotate(len(encoded)):
                    self._rotate()
                    rotated = True
                with open(self._path, "ab") as fh:
                    fh.write(encoded)
                self._chmod(self._path, self._file_mode)
            except OSError as exc:
                # Disk full / permission / IO error -> visible, bounded, non-raising. No retry loop.
                return WriteResult(ok=False, reason=f"io_error:{exc.__class__.__name__}", rotated=rotated)

            return WriteResult(ok=True, bytes_written=len(encoded), rotated=rotated)
        except Exception as exc:  # noqa: BLE001 - final isolation boundary; a shadow write must NEVER escape.
            # Deliberate last-resort catch: the writer is off the hot path and MUST NOT raise into the market-data
            # or recovery path (§21). The failure is reported, not swallowed silently.
            return WriteResult(ok=False, reason=f"unexpected_error:{exc.__class__.__name__}")

    def active_checksum(self) -> Optional[str]:
        """sha256 of the active JSONL file, or None if it does not exist. Tamper-evidence hook (§32)."""
        try:
            if not self._path.exists():
                return None
            return _sha256_file(self._path)
        except OSError:
            return None

    # ---- internals --------------------------------------------------------
    def _ensure_dir(self) -> None:
        parent = self._path.parent
        parent.mkdir(parents=True, exist_ok=True)
        self._chmod(parent, self._dir_mode)

    def _should_rotate(self, incoming: int) -> bool:
        try:
            current = self._path.stat().st_size if self._path.exists() else 0
        except OSError:
            current = 0
        return current > 0 and (current + incoming) > self._max_bytes

    def _rotate(self) -> None:
        """Rename the active file to a numbered sibling, write its checksum companion, then prune to retention."""
        if not self._path.exists():
            return
        idx = 1
        while True:
            candidate = self._path.with_name(f"{self._path.name}.{idx}")
            if not candidate.exists():
                break
            idx += 1
        os.replace(self._path, candidate)
        try:
            digest = _sha256_file(candidate)
            checksum_path = candidate.with_name(candidate.name + ".sha256")
            _atomic_write_text(checksum_path, f"{digest}  {candidate.name}\n", self._file_mode)
        except OSError:
            pass  # checksum companion best-effort; a failure here does not corrupt the rotated evidence.
        self._prune()

    def _prune(self) -> None:
        """Keep at most `max_retained_files` rotated JSONL files (plus their .sha256), deleting the oldest."""
        rotated = sorted(
            (p for p in self._path.parent.glob(f"{self._path.name}.*") if _is_rotated_jsonl(p, self._path.name)),
            key=lambda p: _rotation_index(p, self._path.name),
        )
        excess = len(rotated) - self._max_retained
        for p in rotated[:max(0, excess)]:
            try:
                p.unlink()
                companion = p.with_name(p.name + ".sha256")
                if companion.exists():
                    companion.unlink()
            except OSError:
                pass

    @staticmethod
    def _chmod(target: Path, mode: int) -> None:
        try:
            os.chmod(target, mode)
        except (OSError, NotImplementedError):
            pass  # non-POSIX / restricted fs: best-effort, never fatal.


def _is_rotated_jsonl(p: Path, base_name: str) -> bool:
    suffix = p.name[len(base_name) + 1:] if p.name.startswith(base_name + ".") else ""
    return suffix.isdigit()


def _rotation_index(p: Path, base_name: str) -> int:
    try:
        return int(p.name[len(base_name) + 1:])
    except ValueError:
        return 0


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_write_text(path: Path, text: str, file_mode: int) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
        try:
            os.chmod(path, file_mode)
        except (OSError, NotImplementedError):
            pass
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
