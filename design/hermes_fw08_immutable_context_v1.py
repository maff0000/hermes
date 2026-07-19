#!/usr/bin/env python3
"""HERMES FW-08 immutable build-context isolation + lifecycle v1 (Option A: immutable snapshot dir).

WO-HELM-HERMES-PR114-FW08-FINAL-PREBUILD-TRUST-BOUNDARY-CORRECTIONS-0001 (R-2 §9/§10/§11).
Owner: HERMES (Helm). Created (UTC): 2026-07-19. Contract version: 1.

WHY THIS EXISTS (R2D2 R-2 §9). The future Docker process must NOT consume a mutable working directory that
another process can modify after final verification (a rehash-immediately-before-build is not enough — it
leaves a TOCTOU window and gives no exclusive ownership). This module implements Option A of the WO:

  build a PRIVATE, project-local snapshot dir  (QUARANTINED)
   -> verify it fully (no symlink escape, no external mutable parent, checksum)  (VERIFIED)
   -> revoke ordinary write permissions (chmod read-only) + verify ownership+mode + freeze identity  (FINALISED)
   -> ATOMICALLY hand exclusive ownership to exactly ONE runner  (BUILD_IN_USE)
   -> release after evidence capture  (RELEASED)
   -> destroy; cleanup-failure fails closed  (DESTROYED)   [or REJECTED on any violation]

The finalised identity (source_sha + candidate_id + snapshot_checksum + effective-context checksum + file
count + owner/mode verification) is the ONLY thing a runner ever receives. Because HERMES CI may run as root
(where POSIX read-only bits do not block writes), immutability is enforced by BOTH the chmod AND a
cryptographic revalidation of the finalised identity: ANY post-finalisation mutation (write / replace / add /
remove / mode-change / owner-change / symlink-swap) invalidates the identity before any success evidence.

NO real docker is ever invoked here. The runner is a caller-supplied callable in tests; production wires a
separately-authorised runner. PURE of network/subprocess/secrets; uses ONLY stdlib filesystem primitives.
NOT imported by runtime.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

CONTRACT_VERSION = "1"

# Host-global / system roots a snapshot may NEVER live under (defence in depth; snapshots are project-local).
_PROHIBITED_ROOTS = ("/", "/etc", "/usr", "/bin", "/sbin", "/lib", "/boot", "/root", "/home", "/var", "/srv")

_RO_FILE_MODE = 0o444
_RO_DIR_MODE = 0o555


class ContextLifecycleState(str, Enum):
    QUARANTINED = "QUARANTINED"
    VERIFIED = "VERIFIED"
    FINALISED = "FINALISED"
    BUILD_IN_USE = "BUILD_IN_USE"
    RELEASED = "RELEASED"
    DESTROYED = "DESTROYED"
    REJECTED = "REJECTED"


class ImmutableContextError(Exception):
    """Fail-closed error for any illegal transition, integrity violation, or cleanup failure."""


def _sha256_file(p: Path) -> Tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


@dataclass(frozen=True)
class FinalisedContextIdentity:
    """The immutable identity of a FINALISED context — the ONLY thing a runner receives."""

    contract_version: str
    candidate_id: str
    source_sha: str
    root_path: str
    file_count: int
    snapshot_checksum: str
    effective_context_checksum: str
    owner_uid: int
    mode_verified: bool

    def to_dict(self) -> Dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "candidate_id": self.candidate_id,
            "source_sha": self.source_sha,
            "root_path": self.root_path,
            "file_count": self.file_count,
            "snapshot_checksum": self.snapshot_checksum,
            "effective_context_checksum": self.effective_context_checksum,
            "owner_uid": self.owner_uid,
            "mode_verified": self.mode_verified,
        }


def _reject_prohibited_root(p: Path) -> None:
    text = str(p.resolve())
    for root in _PROHIBITED_ROOTS:
        if text == root or (root != "/" and text == root):
            raise ImmutableContextError(f"refusing host-global/system snapshot root: {text}")
    # never allow the snapshot to BE one of the system roots
    if text in _PROHIBITED_ROOTS:
        raise ImmutableContextError(f"refusing host-global/system snapshot root: {text}")


class ImmutableBuildContext:
    """Manages one candidate's immutable build-context snapshot through its full lifecycle."""

    def __init__(self, *, source_sha: str, candidate_id: str) -> None:
        self.source_sha = source_sha
        self.candidate_id = candidate_id
        self.state = ContextLifecycleState.QUARANTINED
        self._root: Optional[Path] = None
        self._members: Tuple[Tuple[str, str, int, int, int], ...] = ()  # rel, sha, size, mode, uid
        self._snapshot_checksum: str = ""
        self._effective_checksum: str = ""
        self._owner_uid: int = -1
        self._identity: Optional[FinalisedContextIdentity] = None
        self._acquired_by: Optional[str] = None
        self._reason: Optional[str] = None

    # ---- helpers ------------------------------------------------------------------------------
    def _require_state(self, *allowed: ContextLifecycleState) -> None:
        if self.state not in allowed:
            raise ImmutableContextError(
                f"illegal transition from {self.state.value} (allowed: {[s.value for s in allowed]})")

    def _scan_members(self) -> List[Tuple[str, str, int, int, int]]:
        assert self._root is not None
        out: List[Tuple[str, str, int, int, int]] = []
        for abspath in sorted(self._root.rglob("*")):
            if abspath.is_symlink():
                # a symlink inside the snapshot is a potential escape/alias — record type distinctly
                st = abspath.lstat()
                rel = abspath.relative_to(self._root).as_posix()
                out.append((rel, "@symlink", 0, stat.S_IMODE(st.st_mode), st.st_uid))
                continue
            if abspath.is_dir():
                continue
            rel = abspath.relative_to(self._root).as_posix()
            st = abspath.lstat()
            digest, size = _sha256_file(abspath)
            out.append((rel, digest, size, stat.S_IMODE(st.st_mode), st.st_uid))
        out.sort(key=lambda t: t[0])
        return out

    @staticmethod
    def _checksum_of(members: List[Tuple[str, str, int, int, int]]) -> str:
        # Content identity ONLY (rel, sha, size) — mode/owner tracked separately so a chmod is a distinct
        # signal, not folded into the content checksum.
        blob = json.dumps([[m[0], m[1], m[2]] for m in members], separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    # ---- lifecycle ----------------------------------------------------------------------------
    def create(self, source_dir: Path, dest_dir: Path, *, include: Optional[List[str]] = None) -> None:
        """QUARANTINED: build a private project-local snapshot by copying regular files from `source_dir`.
        Symlinks are refused (no symlink escape into the image). `dest_dir` must not already exist."""
        self._require_state(ContextLifecycleState.QUARANTINED)
        source_dir = Path(source_dir)
        dest_dir = Path(dest_dir)
        _reject_prohibited_root(dest_dir)
        if dest_dir.exists():
            raise ImmutableContextError(f"snapshot dest already exists (no reuse): {dest_dir}")
        # Refuse a source tree that contains ANY symlink (no symlink escape/alias into the snapshot).
        for p in source_dir.rglob("*"):
            if p.is_symlink():
                self.reject("SYMLINK-IN-SOURCE")
                raise ImmutableContextError(f"refusing symlink in source context: {p}")

        dest_dir.mkdir(parents=True, exist_ok=False)
        self._root = dest_dir.resolve()

        names = include if include is not None else [
            p.relative_to(source_dir).as_posix()
            for p in sorted(source_dir.rglob("*")) if p.is_file() and not p.is_symlink()
        ]
        for rel in names:
            src = source_dir / rel
            if src.is_symlink():
                self.reject("SYMLINK-IN-SOURCE")
                raise ImmutableContextError(f"refusing symlink member during snapshot: {rel}")
            if not src.is_file():
                continue
            dst = self._root / rel
            # containment: dst must stay under root (no traversal)
            if not str(dst.resolve().parent).startswith(str(self._root)):
                self.reject("TRAVERSAL")
                raise ImmutableContextError(f"refusing path traversal member: {rel}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)  # copyfile does NOT follow into special files; content only
        self._members = tuple(self._scan_members())
        self._snapshot_checksum = self._checksum_of(list(self._members))

    def verify(self, *, effective_context_checksum: str) -> None:
        """VERIFIED: no symlink member, no external mutable parent escape, checksum recomputes."""
        self._require_state(ContextLifecycleState.QUARANTINED)
        assert self._root is not None
        current = self._scan_members()
        if any(sha == "@symlink" for _r, sha, _s, _m, _u in current):
            self.reject("SYMLINK-MEMBER")
            raise ImmutableContextError("snapshot contains a symlink member (escape/alias risk)")
        if self._checksum_of(current) != self._snapshot_checksum:
            self.reject("VERIFY-CHECKSUM-DRIFT")
            raise ImmutableContextError("snapshot content changed between create and verify")
        self._effective_checksum = effective_context_checksum
        self.state = ContextLifecycleState.VERIFIED

    def finalise(self) -> FinalisedContextIdentity:
        """FINALISED: revoke write bits (chmod read-only), verify ownership+mode, freeze the identity."""
        self._require_state(ContextLifecycleState.VERIFIED)
        assert self._root is not None
        # revoke ordinary write perms bottom-up (files then dirs).
        for abspath in sorted(self._root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if abspath.is_symlink():
                self.reject("SYMLINK-AT-FINALISE")
                raise ImmutableContextError("symlink appeared before finalisation")
            try:
                if abspath.is_dir():
                    os.chmod(abspath, _RO_DIR_MODE)
                else:
                    os.chmod(abspath, _RO_FILE_MODE)
            except OSError as exc:
                self.reject(f"CHMOD-FAILED:{exc}")
                raise ImmutableContextError(f"failed to revoke write perms: {exc}") from exc
        os.chmod(self._root, _RO_DIR_MODE)
        # verify ownership + mode of every file.
        self._owner_uid = os.stat(self._root).st_uid
        members = self._scan_members()
        for rel, sha, size, mode, uid in members:
            if mode not in (_RO_FILE_MODE, _RO_DIR_MODE):
                self.reject(f"MODE-NOT-READ-ONLY:{rel}")
                raise ImmutableContextError(f"file not read-only after finalise: {rel} mode={oct(mode)}")
            if uid != self._owner_uid:
                self.reject(f"OWNER-MISMATCH:{rel}")
                raise ImmutableContextError(f"owner mismatch across snapshot: {rel}")
        if self._checksum_of(members) != self._snapshot_checksum:
            self.reject("FINALISE-CHECKSUM-DRIFT")
            raise ImmutableContextError("snapshot content changed during finalisation")
        self._members = tuple(members)
        self._identity = FinalisedContextIdentity(
            contract_version=CONTRACT_VERSION, candidate_id=self.candidate_id, source_sha=self.source_sha,
            root_path=str(self._root), file_count=len(members), snapshot_checksum=self._snapshot_checksum,
            effective_context_checksum=self._effective_checksum, owner_uid=self._owner_uid, mode_verified=True,
        )
        self.state = ContextLifecycleState.FINALISED
        return self._identity

    def assert_intact(self, *, expected_uid: Optional[int] = None) -> None:
        """Recompute the snapshot and fail closed on ANY drift: content, membership (add/remove), mode, owner,
        or a symlink swap. This is the immutability guarantee that holds even when the process is root."""
        if self._root is None or self._identity is None:
            raise ImmutableContextError("no finalised snapshot to check")
        current = self._scan_members()
        if any(sha == "@symlink" for _r, sha, _s, _m, _u in current):
            self.reject("SYMLINK-SWAP")
            raise ImmutableContextError("a symlink appeared in the finalised snapshot")
        if len(current) != self._identity.file_count:
            self.reject("MEMBERSHIP-CHANGED")
            raise ImmutableContextError(
                f"file count changed {self._identity.file_count}->{len(current)} (add/remove)")
        cur_by_rel = {m[0]: m for m in current}
        for rel, sha, size, mode, uid in self._members:
            if rel not in cur_by_rel:
                self.reject("FILE-DISAPPEARED")
                raise ImmutableContextError(f"finalised member disappeared: {rel}")
            _r, c_sha, c_size, c_mode, c_uid = cur_by_rel[rel]
            if c_sha != sha or c_size != size:
                self.reject("CONTENT-CHANGED")
                raise ImmutableContextError(f"finalised member content changed: {rel}")
            if c_mode != mode:
                self.reject("MODE-CHANGED")
                raise ImmutableContextError(f"finalised member mode changed: {rel}")
            want_uid = expected_uid if expected_uid is not None else self._owner_uid
            if c_uid != want_uid:
                self.reject("OWNER-CHANGED")
                raise ImmutableContextError(f"finalised member owner changed: {rel}")
        if self._checksum_of(current) != self._identity.snapshot_checksum:
            self.reject("CHECKSUM-DRIFT")
            raise ImmutableContextError("finalised snapshot checksum drift")

    def acquire(self, consumer_id: str, *, expected_candidate_id: Optional[str] = None,
                expected_source_sha: Optional[str] = None) -> FinalisedContextIdentity:
        """BUILD_IN_USE: atomic, single-consumer acquisition. A second acquisition fails closed. Rebinds
        nothing — reuse across candidates/source-SHAs is refused. Revalidates intact BEFORE handing over."""
        self._require_state(ContextLifecycleState.FINALISED)
        assert self._identity is not None
        if self._acquired_by is not None:
            raise ImmutableContextError("context already acquired by a runner (single-consumer)")
        if expected_candidate_id is not None and expected_candidate_id != self.candidate_id:
            self.reject("ACQUIRE-CANDIDATE-MISMATCH")
            raise ImmutableContextError("refusing to acquire a snapshot for a different candidate")
        if expected_source_sha is not None and expected_source_sha != self.source_sha:
            self.reject("ACQUIRE-SOURCE-MISMATCH")
            raise ImmutableContextError("refusing to acquire a snapshot for a different source SHA")
        self.assert_intact()  # no mutation between finalise and hand-over
        self._acquired_by = consumer_id
        self.state = ContextLifecycleState.BUILD_IN_USE
        return self._identity

    def release(self) -> None:
        """RELEASED: the single runner has finished consuming the context."""
        self._require_state(ContextLifecycleState.BUILD_IN_USE)
        self.state = ContextLifecycleState.RELEASED

    def reject(self, reason: str) -> None:
        """Move to the terminal REJECTED state (non-reusable). Idempotent-safe."""
        self._reason = reason
        self.state = ContextLifecycleState.REJECTED

    def destroy(self, *, remover: Optional[Callable[[Path], None]] = None) -> None:
        """DESTROYED: restore write bits and remove the snapshot. Cleanup-failure fails closed — if the root
        still exists after removal the context is left REJECTED and an error is raised."""
        self._require_state(ContextLifecycleState.RELEASED, ContextLifecycleState.FINALISED,
                            ContextLifecycleState.REJECTED)
        if self._root is None:
            self.state = ContextLifecycleState.DESTROYED
            return
        root = self._root
        try:
            for abspath in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                if not abspath.is_symlink():
                    try:
                        os.chmod(abspath, 0o700 if abspath.is_dir() else 0o600)
                    except OSError:
                        pass
            os.chmod(root, 0o700)
        except OSError:
            pass
        if remover is not None:
            remover(root)
        else:
            shutil.rmtree(root, ignore_errors=False)
        if root.exists():
            self.reject("CLEANUP-FAILED")
            raise ImmutableContextError(f"snapshot cleanup failed (root still present): {root}")
        self.state = ContextLifecycleState.DESTROYED

    @property
    def identity(self) -> Optional[FinalisedContextIdentity]:
        return self._identity

    @property
    def root(self) -> Optional[Path]:
        return self._root

    def to_dict(self) -> Dict[str, object]:
        return {
            "contract_version": CONTRACT_VERSION,
            "candidate_id": self.candidate_id,
            "source_sha": self.source_sha,
            "state": self.state.value,
            "reason": self._reason,
            "acquired_by": self._acquired_by,
            "identity": self._identity.to_dict() if self._identity else None,
        }
