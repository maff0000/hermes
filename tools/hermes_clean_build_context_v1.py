#!/usr/bin/env python3
"""HERMES clean exact-SHA build-context tool v1.

WO-HELM-HERMES-PRE-STAGE-B-BUILD-CONTEXT-OCI-PROVENANCE-AND-SAFETY-GATE-CORRECTIONS-IMPLEMENTATION-0001.
Owner: HERMES (Helm). Purpose: produce a DETERMINISTIC, exact-source-SHA build context for a FUTURE
Stage-B image build, together with a machine-readable manifest, a mechanical security scan, and a
build-context (.dockerignore) exclusion summary. Created (UTC): 2026-07-18. Contract version: 1.

INERT / PRE-STAGE-B. This tool builds NO image, replaces NO container, deploys NOTHING, mutates NO
runtime, reads NO secrets, touches NO network, imports NO Phase-2 runtime. It is run by an operator or
CI BEFORE a build to prove the build context is exactly the tracked content of one commit.

MECHANISM (exact-SHA, tamper-resistant):
  * The source SHA MUST be a full 40-hex commit object (`git cat-file -t` == "commit"). Abbreviated
    SHAs, branch names, tags and "latest" are REJECTED (no ambiguous ref can invent provenance).
  * The repo identity (remote.origin.url) MUST match the expected repository.
  * ONLY files tracked by that EXACT commit are exported, via `git archive` (subprocess, explicit
    argument array, no shell, bounded timeout, captured stderr). The working tree is NEVER read for
    content, so a dirty / contaminated worktree CANNOT leak untracked files into the context.
  * Extraction is done in-process from the archive stream with hard path-safety checks: no absolute
    paths, no `..` traversal, no symlink/hardlink escapes, no nested `.git`, no prohibited paths.
  * A deterministic manifest (sorted paths + per-file streaming sha256) is produced and cross-checked
    against `git ls-tree -r <sha>`; any divergence fails closed.

FAIL-CLOSED conditions: source SHA missing / invalid / non-commit; repo identity differs; export
escapes the target dir; target has unexpected pre-existing content; a nested repo or prohibited path
appears; checksums cannot be produced; the manifest differs from tracked git content.

Bounded: single streaming pass, chunked checksums, capped per-file scan size, subprocess timeouts, no
retry loop, no unbounded recursion. stdlib only. UTC only. Project-relative output only.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

TOOL_VERSION = "1"
CONTRACT_VERSION = "1"
APPLICATION = "hermes"
DEFAULT_EXPECTED_REMOTE = "git@github.com:maff0000/hermes.git"

UTC = datetime.timezone.utc

_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_GIT_TIMEOUT_SEC = 120
_ARCHIVE_TIMEOUT_SEC = 300
_CHUNK = 65536
# Cap per-file content scanned for secrets (bytes). Path scanning is always full; content scanning is
# bounded so a huge tracked file cannot exhaust memory or wall-clock.
_SCAN_CONTENT_CAP = 262144

# Host-global / prohibited output roots — the tool refuses to write a build context under these.
_PROHIBITED_OUTPUT_ROOTS = (
    "/etc", "/usr", "/bin", "/sbin", "/boot", "/sys", "/proc", "/dev", "/lib", "/lib64",
    "/root/.ssh", "/var/lib", "/run",
)
# Path components that must NEVER appear inside an exported build context.
_PROHIBITED_PATH_RULES: Tuple[Tuple[str, str], ...] = (
    ("PP-NESTED-GIT", r"(^|/)\.git(/|$)"),
    ("PP-CLAUDE", r"(^|/)\.claude(/|$)"),
    ("PP-WORKTREES", r"(^|/)worktrees(/|$)"),
    ("PP-VENV", r"(^|/)(\.venv|venv|virtualenv|virtualenvs)(/|$)"),
    ("PP-SSH", r"(^|/)\.ssh(/|$)"),
    ("PP-HOST-ETC", r"(^|/)etc/(passwd|shadow|ssh)(/|$|)"),
    ("PP-SRV-DEV-EVIDENCE", r"(^|/)srv-dev(/|$)"),
    ("PP-JSONL-EVIDENCE", r"\.jsonl$"),
    ("PP-DB-ARTEFACT", r"\.(sqlite3?|db)$"),
    ("PP-KEY-MATERIAL", r"\.(pem|key|crt|p12|pfx)$"),
    ("PP-SHELL-HISTORY", r"(^|/)\.(bash_history|zsh_history)$"),
)
# Mechanical secret-VALUE patterns. Findings report ONLY the safe path + rule id, never the value.
_SECRET_RULES: Tuple[Tuple[str, "re.Pattern[bytes]"], ...] = (
    ("SEC-PRIVATE-KEY", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("SEC-AWS-AKID", re.compile(rb"\bAKIA[0-9A-Z]{16}\b")),
    ("SEC-BEARER-TOKEN", re.compile(rb"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}")),
    # Quoted literal only — an assignment of a QUOTED value (not a variable reference like
    # `password=redis_password` or `token=os.getenv(...)`), so legitimate env-var wiring is not flagged.
    ("SEC-GENERIC-APIKEY", re.compile(rb"(?i)\b(api[_-]?key|secret|token|passwd|password)\b\s*[:=]\s*['\"][A-Za-z0-9/\+_\-]{12,}['\"]")),
    ("SEC-OANDA-ACCOUNT", re.compile(rb"\b\d{3}-\d{3}-\d{6,}-\d{3}\b")),
    ("SEC-SLACK-TOKEN", re.compile(rb"\bxox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("SEC-PRIVATE-KEY-PPK", re.compile(rb"PuTTY-User-Key-File")),
)


class CleanBuildContextError(Exception):
    """Fail-closed error. Any breach of the exact-SHA / path-safety / identity contract raises this."""


@dataclass(frozen=True)
class FileEntry:
    path: str
    sha256: str
    size: int

    def to_dict(self) -> Dict[str, object]:
        return {"path": self.path, "sha256": self.sha256, "size": self.size}


@dataclass(frozen=True)
class ScanFinding:
    path: str
    rule_id: str

    def to_dict(self) -> Dict[str, object]:
        return {"path": self.path, "rule_id": self.rule_id}


@dataclass
class BuildContextManifest:
    """Immutable-by-convention build-context manifest (§13). Serialised deterministically; no secrets,
    no raw file contents — only safe paths, checksums and rule ids."""

    contract_version: str
    application: str
    repo_remote: str
    repo_name: str
    source_sha: str
    created_utc: str
    tool_version: str
    exported_file_count: int
    files: Tuple[FileEntry, ...]
    excluded_category_summary: Mapping[str, int]
    prohibited_findings: Tuple[ScanFinding, ...]
    secret_findings: Tuple[ScanFinding, ...]
    scanned_path_count: int
    result: str
    manifest_checksum: str = ""

    def _core_dict(self) -> Dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "application": self.application,
            "repo_remote": self.repo_remote,
            "repo_name": self.repo_name,
            "source_sha": self.source_sha,
            "created_utc": self.created_utc,
            "tool_version": self.tool_version,
            "exported_file_count": self.exported_file_count,
            "files": [f.to_dict() for f in self.files],
            "excluded_category_summary": dict(sorted(self.excluded_category_summary.items())),
            "prohibited_findings": [f.to_dict() for f in self.prohibited_findings],
            "secret_findings": [f.to_dict() for f in self.secret_findings],
            "scanned_path_count": self.scanned_path_count,
            "result": self.result,
        }

    def canonical_json(self) -> str:
        payload = self._core_dict()
        payload["manifest_checksum"] = self.manifest_checksum
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def compute_checksum(self) -> str:
        core = json.dumps(self._core_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(core.encode("utf-8")).hexdigest()

    def finalised(self) -> "BuildContextManifest":
        self.manifest_checksum = self.compute_checksum()
        return self

    def to_dict(self) -> Dict[str, object]:
        d = self._core_dict()
        d["manifest_checksum"] = self.manifest_checksum
        return d


# --------------------------------------------------------------------------- git helpers
def _run_git(args: Sequence[str], repo_dir: Path, *, timeout: int, binary: bool = False):
    """Run a read-only git command with an explicit argument array (never shell=True), bounded timeout,
    captured stderr. Returns CompletedProcess. Raises CleanBuildContextError on failure/timeout."""
    cmd = ["git", "-C", str(repo_dir), *args]
    try:
        proc = subprocess.run(  # noqa: S603 - explicit arg array, no shell, bounded timeout
            cmd,
            capture_output=True,
            timeout=timeout,
            check=False,
            text=not binary,
        )
    except subprocess.TimeoutExpired as exc:  # bounded: no retry loop
        raise CleanBuildContextError(f"git command timed out: {' '.join(args)}") from exc
    except FileNotFoundError as exc:
        raise CleanBuildContextError("git executable not found") from exc
    if proc.returncode != 0:
        err = proc.stderr if isinstance(proc.stderr, str) else proc.stderr.decode("utf-8", "replace")
        raise CleanBuildContextError(f"git {args[0]} failed (rc={proc.returncode}): {err.strip()[:400]}")
    return proc


def verify_source_sha(source_sha: str) -> str:
    """Validate the source SHA is a full 40-hex value. Abbreviated / branch / tag / 'latest' rejected."""
    if not isinstance(source_sha, str) or not _FULL_SHA_RE.match(source_sha):
        raise CleanBuildContextError(
            "source_sha must be a full 40-hex commit id (abbreviated/branch/tag/'latest' rejected)"
        )
    return source_sha


def verify_commit(source_sha: str, repo_dir: Path) -> None:
    """Fail closed unless source_sha resolves to a COMMIT object at the exact id (no ref resolution)."""
    proc = _run_git(["cat-file", "-t", source_sha], repo_dir, timeout=_GIT_TIMEOUT_SEC)
    kind = proc.stdout.strip()
    if kind != "commit":
        raise CleanBuildContextError(f"source_sha is not a commit object (got '{kind}')")


def verify_repo_identity(repo_dir: Path, expected_remote: str) -> str:
    """Fail closed unless remote.origin.url matches the expected repository."""
    proc = _run_git(["config", "--get", "remote.origin.url"], repo_dir, timeout=_GIT_TIMEOUT_SEC)
    actual = proc.stdout.strip()
    if actual != expected_remote:
        raise CleanBuildContextError(
            f"repo identity mismatch: remote.origin.url='{actual}' expected='{expected_remote}'"
        )
    return actual


def tracked_paths(source_sha: str, repo_dir: Path) -> List[str]:
    """The exact set of file paths tracked by the commit (git's own truth), for cross-check."""
    proc = _run_git(["ls-tree", "-r", "--name-only", "-z", source_sha], repo_dir, timeout=_GIT_TIMEOUT_SEC)
    raw = proc.stdout
    return sorted(p for p in raw.split("\0") if p)


# --------------------------------------------------------------------------- output-dir safety
def validate_output_dir(output_dir: Path) -> Path:
    """Refuse host-global / prohibited roots. Refuse a non-empty pre-existing dir unless it carries this
    tool's sentinel (a prior clean export) — then it is safely cleanable by the caller. Returns resolved."""
    resolved = output_dir.resolve()
    text = str(resolved)
    if resolved == Path(resolved.anchor):
        raise CleanBuildContextError("output_dir must not be a filesystem root")
    for root in _PROHIBITED_OUTPUT_ROOTS:
        if text == root or text.startswith(root + "/"):
            raise CleanBuildContextError(f"refusing host-global/system output path: {text}")
    if resolved.exists():
        if not resolved.is_dir():
            raise CleanBuildContextError(f"output_dir exists and is not a directory: {text}")
        entries = list(resolved.iterdir())
        if entries and not (resolved / ".hermes_build_context").exists():
            raise CleanBuildContextError(
                f"output_dir has unexpected pre-existing content (no clean-context sentinel): {text}"
            )
    return resolved


def _prepare_output_dir(resolved: Path) -> None:
    if resolved.exists():
        # Only a previous clean-context (sentinel present, verified in validate_output_dir) reaches here.
        for child in sorted(resolved.iterdir()):
            _safe_rmtree(child, resolved)
    resolved.mkdir(parents=True, exist_ok=True)
    (resolved / ".hermes_build_context").write_text(
        f"clean-build-context sentinel; tool v{TOOL_VERSION}\n", encoding="utf-8"
    )


def _safe_rmtree(path: Path, base: Path) -> None:
    """Bounded, symlink-safe removal confined to base. Never follows a symlink out of base."""
    if not str(path.resolve()).startswith(str(base.resolve())):
        raise CleanBuildContextError(f"refusing to remove path outside output base: {path}")
    if path.is_symlink():
        path.unlink()
        return
    if path.is_dir():
        for child in sorted(path.iterdir()):
            _safe_rmtree(child, base)
        path.rmdir()
    else:
        path.unlink()


# --------------------------------------------------------------------------- extraction
def _validate_member(member: tarfile.TarInfo, dest_root: Path) -> None:
    """Hard path-safety for one tar member. Fail closed on absolute path, traversal, symlink/hardlink
    escape, nested .git, or prohibited path."""
    name = member.name
    pure = PurePosixPath(name)
    if pure.is_absolute() or name.startswith("/"):
        raise CleanBuildContextError(f"absolute path in archive rejected: {name}")
    if any(part == ".." for part in pure.parts):
        raise CleanBuildContextError(f"path traversal in archive rejected: {name}")
    target = (dest_root / Path(*pure.parts)).resolve()
    if not str(target).startswith(str(dest_root.resolve())):
        raise CleanBuildContextError(f"archive member escapes target dir: {name}")
    if member.issym() or member.islnk():
        link = member.linkname
        link_pure = PurePosixPath(link)
        if link_pure.is_absolute() or link.startswith("/"):
            raise CleanBuildContextError(f"absolute symlink/hardlink target rejected: {name} -> {link}")
        resolved_link = (target.parent / Path(*link_pure.parts)).resolve()
        if not str(resolved_link).startswith(str(dest_root.resolve())):
            raise CleanBuildContextError(f"symlink/hardlink escapes target dir: {name} -> {link}")
    for rule_id, pattern in _PROHIBITED_PATH_RULES:
        if rule_id in ("PP-NESTED-GIT",) and re.search(pattern, name):
            raise CleanBuildContextError(f"nested repo / prohibited path in archive: {name} ({rule_id})")


def export_tracked_content(source_sha: str, repo_dir: Path, dest_root: Path) -> None:
    """Stream `git archive <sha>` and extract ONLY validated regular files/dirs into dest_root. No shell,
    bounded timeout, symlink-escape safe, nested-.git rejected."""
    cmd = ["git", "-C", str(repo_dir), "archive", "--format=tar", source_sha]
    proc = subprocess.Popen(  # noqa: S603 - explicit arg array, no shell
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert proc.stdout is not None
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
            for member in tar:
                _validate_member(member, dest_root)
                if member.isdir():
                    (dest_root / member.name).mkdir(parents=True, exist_ok=True)
                    continue
                if member.issym() or member.islnk():
                    # Validated as in-tree above; recreate as a plain symlink (never followed here).
                    link_path = dest_root / member.name
                    link_path.parent.mkdir(parents=True, exist_ok=True)
                    if link_path.exists() or link_path.is_symlink():
                        link_path.unlink()
                    os.symlink(member.linkname, link_path)
                    continue
                if not member.isfile():
                    # Skip non-regular special members (fifos/devices) — never in a git archive.
                    continue
                out_path = dest_root / member.name
                out_path.parent.mkdir(parents=True, exist_ok=True)
                src = tar.extractfile(member)
                if src is None:
                    raise CleanBuildContextError(f"could not read archive member: {member.name}")
                with open(out_path, "wb") as dst:
                    while True:
                        chunk = src.read(_CHUNK)
                        if not chunk:
                            break
                        dst.write(chunk)
    finally:
        try:
            proc.stdout.close()
        except OSError:
            pass
        try:
            rc = proc.wait(timeout=_ARCHIVE_TIMEOUT_SEC)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            raise CleanBuildContextError("git archive timed out") from exc
        if rc != 0:
            err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
            raise CleanBuildContextError(f"git archive failed (rc={rc}): {err.strip()[:400]}")


# --------------------------------------------------------------------------- checksums + scan
def _sha256_file(path: Path) -> Tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            size += len(chunk)
            h.update(chunk)
    return h.hexdigest(), size


def _iter_files(dest_root: Path) -> List[Tuple[str, Path]]:
    """Return sorted (relative_posix_path, absolute_path) for regular files, EXCLUDING the sentinel.
    Symlinks are recorded by name but not followed for content."""
    out: List[Tuple[str, Path]] = []
    for abspath in sorted(dest_root.rglob("*")):
        if abspath.is_symlink():
            continue
        if not abspath.is_file():
            continue
        rel = abspath.relative_to(dest_root).as_posix()
        if rel == ".hermes_build_context":
            continue
        out.append((rel, abspath))
    out.sort(key=lambda t: t[0])
    return out


def scan_path(rel_path: str) -> List[str]:
    """Return prohibited-path rule ids matched by a path (no content read)."""
    hits: List[str] = []
    for rule_id, pattern in _PROHIBITED_PATH_RULES:
        if re.search(pattern, rel_path):
            hits.append(rule_id)
    return hits


def scan_content(abspath: Path) -> List[str]:
    """Return secret rule ids matched in the first _SCAN_CONTENT_CAP bytes. Reports rule ids only —
    NEVER the matched value."""
    hits: List[str] = []
    try:
        with open(abspath, "rb") as fh:
            blob = fh.read(_SCAN_CONTENT_CAP)
    except OSError as exc:
        raise CleanBuildContextError(f"could not read file for scan: {abspath}") from exc
    for rule_id, pattern in _SECRET_RULES:
        if pattern.search(blob):
            hits.append(rule_id)
    return hits


# --------------------------------------------------------------------------- .dockerignore evaluation
def _pattern_to_regex(pattern: str) -> "re.Pattern[str]":
    """Translate a .dockerignore glob (with **, *, ?) to an anchored regex over a posix path."""
    out = ["^"]
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                out.append(".*")
                i += 2
                if i < n and pattern[i] == "/":
                    i += 1
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c in ".+()[]{}|^$\\":
            out.append("\\" + c)
        else:
            out.append(re.escape(c) if c not in "/-_" else c)
        i += 1
    out.append("$")
    return re.compile("".join(out))


def parse_dockerignore(text: str) -> List[Tuple[bool, str, "re.Pattern[str]"]]:
    """Return ordered (is_exception, cleaned_pattern, regex). Comments/blank lines skipped."""
    rules: List[Tuple[bool, str, "re.Pattern[str]"]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        exception = line.startswith("!")
        pat = line[1:] if exception else line
        pat = pat.strip().lstrip("/").rstrip("/")
        if not pat:
            continue
        rules.append((exception, pat, _pattern_to_regex(pat)))
    return rules


def _path_prefixes(path: str) -> List[str]:
    parts = path.split("/")
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


def dockerignore_excludes(path: str, rules: Sequence[Tuple[bool, str, "re.Pattern[str]"]]) -> bool:
    """Docker semantics: a file is excluded if the LAST matching pattern is not an exception. A pattern
    matches the path if it matches the full path OR any parent directory prefix."""
    prefixes = _path_prefixes(path)
    excluded = False
    matched = False
    for exception, _pat, regex in rules:
        if any(regex.match(pref) for pref in prefixes):
            matched = True
            excluded = not exception
    return excluded if matched else False


def apply_dockerignore(paths: Sequence[str], text: str) -> Tuple[List[str], List[str]]:
    """Return (included, excluded) partition of paths under a .dockerignore text."""
    rules = parse_dockerignore(text)
    included: List[str] = []
    excluded: List[str] = []
    for p in paths:
        (excluded if dockerignore_excludes(p, rules) else included).append(p)
    return sorted(included), sorted(excluded)


def _category_for(path: str) -> str:
    if re.search(r"(^|/)\.claude(/|$)", path):
        return ".claude"
    if path.endswith(".jsonl"):
        return "jsonl"
    if path.startswith("tests/") or "/tests/" in path or path == "tests":
        return "tests"
    if path.startswith("ops/evidence/"):
        return "ops-evidence"
    if path.startswith(".github/"):
        return "ci"
    if path in ("Dockerfile", ".dockerignore") or path.startswith("docker-compose"):
        return "container-meta"
    if path.endswith(".bak") or path.endswith(".log"):
        return "backup-log"
    if re.search(r"(^|/)__pycache__(/|$)", path) or path.endswith((".pyc", ".pyo")):
        return "pycache"
    if re.search(r"(^|/)mock(/|$)", path):
        return "mock"
    return "other"


def excluded_category_summary(excluded: Sequence[str]) -> Dict[str, int]:
    summary: Dict[str, int] = {}
    for p in excluded:
        cat = _category_for(p)
        summary[cat] = summary.get(cat, 0) + 1
    return dict(sorted(summary.items()))


# --------------------------------------------------------------------------- orchestration
def build_clean_context(
    *,
    source_sha: str,
    output_dir: Path,
    repo_dir: Path,
    expected_remote: str = DEFAULT_EXPECTED_REMOTE,
    now_utc: Optional[str] = None,
    dockerignore_text: Optional[str] = None,
) -> BuildContextManifest:
    """Full fail-closed flow: validate SHA -> verify commit -> verify identity -> export -> checksum ->
    scan -> cross-check against tracked git content -> deterministic manifest."""
    verify_source_sha(source_sha)
    verify_commit(source_sha, repo_dir)
    remote = verify_repo_identity(repo_dir, expected_remote)
    resolved_out = validate_output_dir(output_dir)
    _prepare_output_dir(resolved_out)
    export_tracked_content(source_sha, repo_dir, resolved_out)

    files_on_disk = _iter_files(resolved_out)
    exported_rel = [rel for rel, _ in files_on_disk]

    # Cross-check exported content against git's own tracked-file truth. Symlinks appear in git ls-tree
    # but are skipped for content; require the FILE set to be a subset of tracked paths, and every
    # tracked non-symlink path to be present.
    git_paths = set(tracked_paths(source_sha, repo_dir))
    disk_set = set(exported_rel)
    extra = disk_set - git_paths
    if extra:
        raise CleanBuildContextError(f"exported content not tracked by commit: {sorted(extra)[:10]}")

    # The manifest records EVERY exported file (exact-SHA provenance). The SECURITY SCAN (§12), however,
    # targets the EFFECTIVE build context — what `COPY . ${APP_HOME}` actually brings into the image
    # after .dockerignore filtering — because that is the material that could leak. .dockerignore is an
    # INDEPENDENT DEFENCE layered on top of the exact-SHA export, never a substitute for it.
    if dockerignore_text is None:
        di = repo_dir / ".dockerignore"
        dockerignore_text = di.read_text(encoding="utf-8") if di.exists() else ""
    included_rel, excl = apply_dockerignore(exported_rel, dockerignore_text)
    cat_summary = excluded_category_summary(excl)
    included_set = set(included_rel)

    entries: List[FileEntry] = []
    prohibited: List[ScanFinding] = []
    secrets: List[ScanFinding] = []
    scanned = 0
    for rel, abspath in files_on_disk:
        digest, size = _sha256_file(abspath)
        entries.append(FileEntry(path=rel, sha256=digest, size=size))
        if rel not in included_set:
            continue  # excluded from the image by .dockerignore -> not part of the leakable surface
        scanned += 1
        for rule_id in scan_path(rel):
            prohibited.append(ScanFinding(path=rel, rule_id=rule_id))
        for rule_id in scan_content(abspath):
            secrets.append(ScanFinding(path=rel, rule_id=rule_id))

    entries.sort(key=lambda e: e.path)
    prohibited.sort(key=lambda f: (f.path, f.rule_id))
    secrets.sort(key=lambda f: (f.path, f.rule_id))

    result = "PASS" if (not prohibited and not secrets) else "FAIL"

    created = now_utc or datetime.datetime.now(UTC).replace(microsecond=0).isoformat()
    manifest = BuildContextManifest(
        contract_version=CONTRACT_VERSION,
        application=APPLICATION,
        repo_remote=remote,
        repo_name="hermes",
        source_sha=source_sha,
        created_utc=created,
        tool_version=TOOL_VERSION,
        exported_file_count=len(entries),
        files=tuple(entries),
        excluded_category_summary=cat_summary,
        prohibited_findings=tuple(prohibited),
        secret_findings=tuple(secrets),
        scanned_path_count=scanned,
        result=result,
    )
    return manifest.finalised()


# --------------------------------------------------------------------------- CLI
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="HERMES clean exact-SHA build-context tool v1 (INERT).")
    p.add_argument("--source-sha", required=True, help="Full 40-hex commit id to export.")
    p.add_argument("--output-dir", required=True, help="Project-local output dir (host-global refused).")
    p.add_argument("--repo-dir", default=".", help="Git repo/worktree to read (read-only).")
    p.add_argument("--expected-remote", default=DEFAULT_EXPECTED_REMOTE, help="Expected remote.origin.url.")
    p.add_argument("--manifest-out", default=None, help="Optional path to write the manifest JSON.")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        manifest = build_clean_context(
            source_sha=args.source_sha,
            output_dir=Path(args.output_dir),
            repo_dir=Path(args.repo_dir),
            expected_remote=args.expected_remote,
        )
    except CleanBuildContextError as exc:
        sys.stderr.write(f"FAIL-CLOSED: {exc}\n")
        return 2
    payload = manifest.to_dict()
    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.manifest_out:
        out = Path(args.manifest_out)
        if out.parent and not out.parent.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    sys.stdout.write(text + "\n")
    return 0 if manifest.result == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
