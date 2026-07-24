#!/usr/bin/env python3
"""HERMES FW-08 governed Stage-B candidate-image build wrapper v1.

WO-HELM-HERMES-FW08-GOVERNED-STAGE-B-IMAGE-BUILD-ENFORCEMENT-IMPLEMENTATION-0001.
Owner: HERMES (Helm). Created (UTC): 2026-07-19. Contract version: 1.

THE SOLE CANONICAL FUTURE STAGE-B BUILD ENTRYPOINT. A direct `docker build` of HERMES is NOT authorised;
this wrapper makes the PR#113 clean-context + OCI-label tools MANDATORY and mechanically unavoidable, and
resolves R2D2 carry-forward findings:
  * F-113-01  trusted canonical-ref reachability (arbitrary/foreign/orphan/spoofed source REJECTED).
  * F-113-02  OCI provenance labels made mechanically unavoidable (expected-source-SHA NEVER optional).
  * F-113-03  Docker-FAITHFUL effective-context enumeration (parity-fixtured matcher, not the homemade one).
  * F-113-06  quarantine + pre-materialisation rejection (prohibited members never become a usable context).
  * F-113-04  secret scan positioned as documented defence-in-depth (NOT the primary control).

INERT / PRE-STAGE-B. This wrapper builds NO real image, tags/publishes NOTHING, logs into NO registry,
runs NO real SBOM/scan, deploys NOTHING, replaces NO container, wires NO runtime, installs NO config,
enables NO shadow, writes NO Redis/SQL, and mutates NO live Phase-2 state. Docker/SBOM/scan are performed
through an INJECTABLE runner; the DEFAULT runner REFUSES to run anything. The real runner is a distinct
class, gated behind an explicit flag AND an environment variable that tests never set — there is NO code
path that runs a real `docker build` during tests.

Candidate readiness is NOT publication, NOT deployment, NOT activation. A REJECTED candidate can never
publish or deploy. stdlib only, UTC only, bounded, deterministic, reason-coded, no secret values.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import tools.hermes_clean_build_context_v1 as cbc
import tools.hermes_image_label_verify_v1 as lbl
import design.hermes_fw08_dockerignore_matcher_v1 as di
import design.hermes_fw08_candidate_state_v1 as sm
import design.hermes_fw08_context_contract_v1 as cc
import design.hermes_fw08_readiness_evidence_v1 as ee
import design.hermes_fw08_vuln_disposition_v1 as vd
import design.hermes_fw08_active_import_evidence_v1 as ai
import design.hermes_fw08_producer_registry_v1 as prr       # F-2 §9 independent governed producer registry
import design.hermes_fw08_image_identity_anchor_v1 as iia    # F-2 §7 independent image-identity anchor
import design.hermes_fw08_image_filesystem_anchor_v1 as ifa  # F-2 §8 independent image-filesystem anchor
import design.hermes_fw08_anchor_bundle_v1 as anb            # F-2 §10 independent anchor bundle
import design.hermes_fw08_producer_independence_v1 as pi     # F2-R2 §7 build/OCI producer independence
import design.hermes_fw08_registry_activation_v1 as ract     # F2-R1 §11 governed registry activation
import design.hermes_fw08_activated_registry_handle_v1 as arh  # F2-R1 §10 sealed activated registry handle
import design.hermes_fw08_producer_trust_v1 as pt          # R-1 §7 producer-trust + unforgeable seal
import design.hermes_fw08_evidence_chain_v1 as ec          # R-1 §8 Merkle-bound evidence chain
import design.hermes_fw08_immutable_context_v1 as imc       # R-2 §9/§10 immutable build-context lifecycle

TOOL_VERSION = "1"
CONTRACT_VERSION = "1"
APPLICATION = "hermes"
UTC = datetime.timezone.utc

# §6 governed secret-scanner dispositions (narrow, fingerprint-bound). Loaded explicitly by the preflight;
# the default fake-runner flow does NOT load them (fail-closed default unchanged).
DEFAULT_DISPOSITIONS_PATH = Path(__file__).resolve().parent / "fw08_scanner_dispositions.v1.json"


def load_governed_dispositions(path: Optional[Path] = None):
    """Load the governed §6 secret-scanner dispositions (validated, fingerprint-bound)."""
    return cbc.load_dispositions(path or DEFAULT_DISPOSITIONS_PATH)

DEFAULT_EXPECTED_REMOTE = cbc.DEFAULT_EXPECTED_REMOTE
# §7: the ONLY canonical refs from which a Stage-B source SHA may be reached.
ALLOWED_CANONICAL_REFS: Tuple[str, ...] = ("refs/remotes/origin/main",)

_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_UTC_ISO_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(\+00:00|Z)$")
_CANDIDATE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
_GIT_TIMEOUT_SEC = 120
_BUILD_TIMEOUT_SEC = 1800
_MAX_OUTPUT_BYTES = 1_048_576

# §12: docker-arg tokens that must NEVER appear in a governed build command.
_PROHIBITED_DOCKER_ARG_SUBSTRINGS: Tuple[str, ...] = (
    "--push", "push", "--registry", "registry", "--output", "type=registry", "type=image,push",
    "--network", "host", "--privileged", "--add-host", "--secret", "--ssh", "--builder",
    "docker://", "--load-remote", "--cache-to", "--cache-from",
)
# tokens we DO allow even though a prohibited substring is contained (none currently — kept explicit)
_DOCKER_ARG_ALLOWLIST: Tuple[str, ...] = ()

# §10/§14 build-context + image-content contract constants live in the inert design contract module
# (design/ is an allowed inert referrer; keeping the Phase-1-core / Phase-2 path strings out of this
# tools/ file preserves the repo-wide inertness static guards). Aliased here for the wrapper's use.
EXPECTED_ENTRYPOINT = cc.EXPECTED_ENTRYPOINT
EXPECTED_CMD = cc.EXPECTED_CMD
EXPECTED_USER = cc.EXPECTED_USER
EXPECTED_WORKDIR = cc.EXPECTED_WORKDIR
EXPECTED_PORT = cc.EXPECTED_PORT
REQUIRED_EFFECTIVE_INCLUSIONS = cc.REQUIRED_EFFECTIVE_INCLUSIONS
PHASE2_MODULE_PREFIX = cc.PHASE2_MODULE_PREFIX
PHASE2_MODULE_SUFFIX = cc.PHASE2_MODULE_SUFFIX
REQUIRED_PHASE2_MODULE_COUNT = cc.REQUIRED_PHASE2_MODULE_COUNT
REQUIRED_CONTEXT_PRESENT = cc.REQUIRED_CONTEXT_PRESENT
PROHIBITED_EFFECTIVE_PATTERNS = cc.PROHIBITED_EFFECTIVE_PATTERNS

SUPPORTED_SBOM_TOOLS = frozenset({"syft"})
SUPPORTED_SBOM_FORMATS = frozenset({"spdx-json", "cyclonedx-json"})
SUPPORTED_SCANNERS = frozenset({"grype", "trivy"})
SEVERITY_TAXONOMY = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "NEGLIGIBLE", "UNKNOWN")
_REAL_DOCKER_ENV_GATE = "HERMES_FW08_ALLOW_REAL_DOCKER"


class GovernedBuildError(Exception):
    """Fail-closed error for any breach of the governed Stage-B build contract."""


class RealDockerInvocationForbidden(GovernedBuildError):
    """Raised by the real runner when real execution is not explicitly and doubly authorised."""


# =============================================================================== §7 trusted provenance
def _git_ro(args: Sequence[str], repo_dir: Path, *, timeout: int = _GIT_TIMEOUT_SEC, ok_codes=(0,)):
    """Read-only git with --no-replace-objects (defends against git-replace object injection), explicit
    argument array, no shell, bounded timeout. Returns CompletedProcess. Codes outside ok_codes raise."""
    cmd = ["git", "--no-replace-objects", "-C", str(repo_dir), *args]
    try:
        proc = subprocess.run(  # noqa: S603 - explicit arg array, no shell, bounded timeout
            cmd, capture_output=True, timeout=timeout, check=False, text=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise GovernedBuildError(f"git timed out: {' '.join(args)}") from exc
    except FileNotFoundError as exc:
        raise GovernedBuildError("git executable not found") from exc
    if proc.returncode not in ok_codes:
        raise GovernedBuildError(
            f"git {args[0]} failed (rc={proc.returncode}): {proc.stderr.strip()[:300]}"
        )
    return proc


@dataclass(frozen=True)
class ProvenanceResult:
    source_sha: str
    remote: str
    mechanism: str            # REACHABLE_FROM_CANONICAL_REF | AUTHORISED_PR_HEAD
    canonical_ref: Optional[str]
    audit_binding: Optional[str]

    def to_dict(self) -> Dict[str, object]:
        return {
            "source_sha": self.source_sha,
            "remote": self.remote,
            "mechanism": self.mechanism,
            "canonical_ref": self.canonical_ref,
            "audit_binding": self.audit_binding,
        }


def _ref_exists(repo_dir: Path, ref: str) -> Optional[str]:
    proc = _git_ro(["rev-parse", "--verify", "--quiet", ref + "^{commit}"], repo_dir, ok_codes=(0, 1))
    out = proc.stdout.strip()
    return out or None


def _is_ancestor(repo_dir: Path, sha: str, ref: str) -> bool:
    # rc 0 == sha is an ancestor of (or equal to) ref; rc 1 == not; anything else raises.
    proc = _git_ro(["merge-base", "--is-ancestor", sha, ref], repo_dir, ok_codes=(0, 1))
    return proc.returncode == 0


def verify_trusted_provenance(
    source_sha: str,
    repo_dir: Path,
    *,
    expected_remote: str = DEFAULT_EXPECTED_REMOTE,
    allowed_refs: Sequence[str] = ALLOWED_CANONICAL_REFS,
    authorised_pr_heads: Optional[Mapping[str, str]] = None,
) -> ProvenanceResult:
    """§7 / F-113-01. Fail closed unless `source_sha` is a full 40-hex COMMIT, the repo identity matches,
    AND the SHA is reachable from an explicitly-allowed canonical ref (preferred: refs/remotes/origin/main)
    OR is an authorised PR head carrying a governed audit binding. Rejects arbitrary orphan commits,
    foreign repos with a spoofed remote string, git-replace object injection, branch/tag/HEAD/latest and
    abbreviated SHAs. Read-only: never fetches or mutates the canonical repo."""
    try:
        cbc.verify_source_sha(source_sha)                   # full 40-hex; branch/tag/latest/abbrev rejected
        remote = cbc.verify_repo_identity(repo_dir, expected_remote)  # necessary, not sufficient
        cbc.verify_commit(source_sha, repo_dir)             # must be a commit object
    except cbc.CleanBuildContextError as exc:
        raise GovernedBuildError(str(exc)) from exc

    # Preferred path: reachable from an allowed canonical ref.
    any_ref_present = False
    for ref in allowed_refs:
        tip = _ref_exists(repo_dir, ref)
        if tip is None:
            continue
        any_ref_present = True
        if _is_ancestor(repo_dir, source_sha, ref):
            return ProvenanceResult(
                source_sha=source_sha, remote=remote,
                mechanism="REACHABLE_FROM_CANONICAL_REF", canonical_ref=ref, audit_binding=None,
            )

    # Governed fallback: an authorised PR head with an explicit audit binding.
    heads = dict(authorised_pr_heads or {})
    binding = heads.get(source_sha)
    if binding:
        if not isinstance(binding, str) or not binding.strip():
            raise GovernedBuildError("authorised PR head requires a non-empty governed audit binding")
        return ProvenanceResult(
            source_sha=source_sha, remote=remote,
            mechanism="AUTHORISED_PR_HEAD", canonical_ref=None, audit_binding=binding.strip(),
        )

    if not any_ref_present:
        raise GovernedBuildError(
            "no allowed canonical ref is present in the repo; cannot prove trusted provenance "
            f"(allowed: {list(allowed_refs)})"
        )
    raise GovernedBuildError(
        f"source SHA {source_sha} is not reachable from any allowed canonical ref "
        f"{list(allowed_refs)} and is not an authorised PR head (orphan/foreign/spoofed rejected)"
    )


# =============================================================================== §8 trusted-ref freshness
class SourceFreshnessError(GovernedBuildError):
    """Fail-closed error for a stale / tampered / unauthorised / offline source-trust binding."""


class RemoteRefFetcher:
    """Injectable boundary for reading the canonical remote's main SHA. The wrapper NEVER performs a live
    canonical fetch directly — only through a fetcher. The DEFAULT refuses (offline / unauthorised)."""

    def fetch_canonical_main_sha(self, repo_dir: Path, remote: str) -> str:
        raise NotImplementedError


class RefusingRemoteRefFetcher(RemoteRefFetcher):
    """DEFAULT. Refuses any live canonical fetch, so no unauthorised mutating fetch can occur in tests."""

    def fetch_canonical_main_sha(self, repo_dir: Path, remote: str) -> str:
        raise SourceFreshnessError("default fetcher refuses a live canonical fetch (offline/unauthorised)")


class FixtureRemoteRefFetcher(RemoteRefFetcher):
    """Test-only fetcher returning a preset SHA (or raising a preset error). Used against ISOLATED fixture
    repos so no mutation-bearing fetch ever touches the live canonical remote."""

    def __init__(self, sha: Optional[str] = None, *, raises: Optional[Exception] = None) -> None:
        self._sha = sha
        self._raises = raises

    def fetch_canonical_main_sha(self, repo_dir: Path, remote: str) -> str:
        if self._raises is not None:
            raise self._raises
        if not self._sha:
            raise SourceFreshnessError("fixture fetcher has no SHA")
        return self._sha


@dataclass(frozen=True)
class FreshnessResult:
    fresh: bool
    remote_sha: Optional[str]
    mechanism: str            # FRESH_AUTHENTICATED_FETCH | GOVERNED_IMMUTABLE_BINDING
    reason_codes: Tuple[str, ...]

    def to_dict(self) -> Dict[str, object]:
        return {"fresh": self.fresh, "remote_sha": self.remote_sha, "mechanism": self.mechanism,
                "reason_codes": list(self.reason_codes)}


def verify_trusted_ref_freshness(
    source_sha: str,
    repo_dir: Path,
    *,
    authorised_sha: str,
    expected_remote: str = DEFAULT_EXPECTED_REMOTE,
    fetcher: Optional[RemoteRefFetcher] = None,
    canonical_state_record: Optional[Mapping[str, str]] = None,
    immutable_source_binding: Optional[Mapping[str, str]] = None,
    allowed_ref: str = "refs/remotes/origin/main",
) -> FreshnessResult:
    """§8. Bind source trust to a FRESH canonical-main SHA. Preferred path: an authenticated read-only fetch
    (via the injectable fetcher) immediately before trust eval, then require remote==authorised==local-ref
    and source reachable from remote (no local-ref substitution). If the fetch FAILS, fail closed UNLESS a
    separately-governed immutable source binding is supplied (an out-of-band authorised SHA + canonical-state
    binding). Never performs an unauthorised mutating fetch — reject cases use isolated fixture repos."""
    fetcher = fetcher or RefusingRemoteRefFetcher()
    reasons: List[str] = []

    # Identity + object validity first (foreign remote / non-commit / abbreviated rejected).
    try:
        cbc.verify_source_sha(source_sha)
        cbc.verify_source_sha(authorised_sha)
        cbc.verify_repo_identity(repo_dir, expected_remote)   # foreign remote -> raises
        cbc.verify_commit(source_sha, repo_dir)
    except cbc.CleanBuildContextError as exc:
        raise SourceFreshnessError(f"identity/object check failed: {exc}") from exc

    local_ref_sha = _ref_exists(repo_dir, allowed_ref)        # local origin/main (may be stale/tampered)

    # Try the preferred authenticated-fetch path.
    remote_sha: Optional[str] = None
    fetch_error: Optional[Exception] = None
    try:
        remote_sha = fetcher.fetch_canonical_main_sha(repo_dir, expected_remote)
    except Exception as exc:  # noqa: BLE001 - any fetch failure is fail-closed unless immutable binding
        fetch_error = exc

    if remote_sha is not None:
        if not cbc._FULL_SHA_RE.match(str(remote_sha)):
            raise SourceFreshnessError("fetched canonical main SHA is not a full 40-hex commit")
        if remote_sha != authorised_sha:
            reasons.append("AUTHORISED-SHA-MISMATCH")
        if local_ref_sha is None:
            reasons.append("LOCAL-CANONICAL-REF-ABSENT")
        elif local_ref_sha != remote_sha:
            reasons.append("STALE-OR-TAMPERED-LOCAL-REF")        # local ref != fresh remote
        if canonical_state_record is not None and str(canonical_state_record.get("source_sha")) != remote_sha:
            reasons.append("CANONICAL-STATE-MISMATCH")
        if not _sha_reachable_from(repo_dir, source_sha, remote_sha):
            reasons.append("SOURCE-NOT-REACHABLE-FROM-REMOTE")
        return FreshnessResult(
            fresh=(len(reasons) == 0), remote_sha=remote_sha,
            mechanism="FRESH_AUTHENTICATED_FETCH", reason_codes=tuple(sorted(set(reasons))),
        )

    # Fetch failed / refused (offline). Fail closed UNLESS a governed immutable binding is supplied.
    if immutable_source_binding is None:
        raise SourceFreshnessError(
            f"canonical fetch failed and no governed immutable source binding supplied ({fetch_error})"
        )
    bind_sha = str(immutable_source_binding.get("authorised_sha", ""))
    binding_id = str(immutable_source_binding.get("binding_id", ""))
    state_sha = str(immutable_source_binding.get("canonical_state_sha", ""))
    if not binding_id.strip():
        reasons.append("IMMUTABLE-BINDING-ID-MISSING")
    if not cbc._FULL_SHA_RE.match(bind_sha) or bind_sha != authorised_sha:
        reasons.append("IMMUTABLE-BINDING-SHA-MISMATCH")
    if state_sha != authorised_sha:
        reasons.append("IMMUTABLE-CANONICAL-STATE-MISMATCH")
    if local_ref_sha is None or local_ref_sha != authorised_sha:
        reasons.append("LOCAL-REF-NOT-AUTHORISED-SHA")
    if not _sha_reachable_from(repo_dir, source_sha, authorised_sha):
        reasons.append("SOURCE-NOT-REACHABLE-FROM-AUTHORISED")
    return FreshnessResult(
        fresh=(len(reasons) == 0), remote_sha=None,
        mechanism="GOVERNED_IMMUTABLE_BINDING", reason_codes=tuple(sorted(set(reasons))),
    )


def _sha_reachable_from(repo_dir: Path, sha: str, ref_or_sha: str) -> bool:
    """True iff `sha` is an ancestor of (or equal to) `ref_or_sha`, judged with --no-replace-objects."""
    try:
        return _is_ancestor(repo_dir, sha, ref_or_sha)
    except GovernedBuildError:
        return False


# =============================================================================== §8/§9 governed context
@dataclass
class GovernedContext:
    context_dir: Path
    manifest: "cbc.BuildContextManifest"
    effective_included: Tuple[str, ...]
    effective_checksum: str
    status: str               # USABLE | UNUSABLE
    reason_code: Optional[str] = None

    @property
    def usable(self) -> bool:
        return self.status == "USABLE"


_STATUS_FILE = ".fw08_context_status"
_QUARANTINE_MARKER = ".fw08_quarantine"


def _write_status(context_dir: Path, status: str, reason: Optional[str]) -> None:
    payload = {"status": status, "reason_code": reason, "tool_version": TOOL_VERSION}
    (context_dir / _STATUS_FILE).write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _destroy_context(context_dir: Path) -> None:
    """Bounded, symlink-safe destruction of a failed/quarantined context, confined to the dir."""
    if not context_dir.exists():
        return
    for child in sorted(context_dir.iterdir()):
        if child.name == _STATUS_FILE:
            continue
        cbc._safe_rmtree(child, context_dir)


def _tracked_names(source_sha: str, repo_dir: Path) -> List[str]:
    return cbc.tracked_paths(source_sha, repo_dir)


def prepare_governed_context(
    source_sha: str,
    repo_dir: Path,
    quarantine_dir: Path,
    *,
    expected_remote: str = DEFAULT_EXPECTED_REMOTE,
    now_utc: Optional[str] = None,
    dockerignore_text: Optional[str] = None,
    dispositions: Optional[Sequence["cbc.SecretDisposition"]] = None,
) -> GovernedContext:
    """§8/§9 (F-113-06). MANDATORY clean-context integration with pre-materialisation quarantine.

    1. Enumerate tracked names at the exact SHA (git truth).
    2. PRE-MATERIALISATION: compute the Docker-faithful effective context and REJECT prohibited members
       BEFORE any export (no prohibited path is ever materialised where it can be avoided).
    3. Export ONLY the tracked content of the exact SHA into a QUARANTINED project-local dir that is NEVER
       used as a Docker context until every check passes.
    4. Verify the manifest (result/source-sha/identity/counts/checksum) and that no file changed after
       manifest generation.
    On ANY failure: destroy the quarantined context, stamp it UNUSABLE, and return a non-usable result —
    no partial context, no success manifest, no reuse."""
    if dockerignore_text is None:
        digest_path = repo_dir / ".dockerignore"
        dockerignore_text = digest_path.read_text(encoding="utf-8") if digest_path.exists() else ""

    q = quarantine_dir.resolve()
    text = str(q)
    for root in cbc._PROHIBITED_OUTPUT_ROOTS:
        if text == root or text.startswith(root + "/"):
            raise GovernedBuildError(f"refusing host-global/system quarantine path: {text}")
    q.mkdir(parents=True, exist_ok=True)
    (q / _QUARANTINE_MARKER).write_text("fw08 quarantine; never a docker context until USABLE\n",
                                        encoding="utf-8")
    context_dir = q / "context"

    def _fail(reason: str) -> GovernedContext:
        if context_dir.exists():
            _destroy_context(context_dir)
        context_dir.mkdir(parents=True, exist_ok=True)
        _write_status(context_dir, "UNUSABLE", reason)
        return GovernedContext(
            context_dir=context_dir, manifest=None, effective_included=tuple(),
            effective_checksum="", status="UNUSABLE", reason_code=reason,
        )

    # 1-2. Pre-materialisation gate on names only.
    try:
        names = _tracked_names(source_sha, repo_dir)
    except cbc.CleanBuildContextError as exc:
        return _fail(f"NAME-ENUM-FAILED:{exc}")
    try:
        effective = di.effective_context(names, dockerignore_text)   # §10 fail-closed on unsupported syntax
    except di.UnsupportedPatternError as exc:
        return _fail(f"UNSUPPORTED-DOCKERIGNORE-SYNTAX:{exc}")
    pre_prohibited = [p for p in effective if cbc.scan_path(p)]
    if pre_prohibited:
        return _fail(f"PRE-MATERIALISATION-PROHIBITED:{len(pre_prohibited)}")

    # 3. Export exact-SHA tracked content into the quarantine (§6 governed dispositions threaded through).
    try:
        manifest = cbc.build_clean_context(
            source_sha=source_sha, output_dir=context_dir, repo_dir=repo_dir,
            expected_remote=expected_remote, now_utc=now_utc, dockerignore_text=dockerignore_text,
            dispositions=dispositions,
        )
    except cbc.CleanBuildContextError as exc:
        return _fail(f"CLEAN-CONTEXT-EXPORT-FAILED:{exc}")

    # 4. Verify manifest integrity + no post-manifest modification.
    if manifest.result != "PASS":
        return _fail("CONTEXT-MANIFEST-RESULT-NOT-PASS")
    if manifest.source_sha != source_sha:
        return _fail("CONTEXT-SOURCE-SHA-MISMATCH")
    if manifest.exported_file_count <= 0 or not manifest.files:
        return _fail("CONTEXT-EMPTY")
    if manifest.prohibited_findings or manifest.secret_findings:
        return _fail("CONTEXT-PROHIBITED-OR-SECRET-FINDINGS")
    if manifest.manifest_checksum != manifest.compute_checksum():
        return _fail("CONTEXT-CHECKSUM-INVALID")
    mod = _files_modified_since_manifest(context_dir, manifest)
    if mod:
        return _fail(f"CONTEXT-MODIFIED-AFTER-MANIFEST:{mod}")

    effective_checksum = di.effective_context_checksum(effective)
    _write_status(context_dir, "USABLE", None)
    return GovernedContext(
        context_dir=context_dir, manifest=manifest, effective_included=tuple(effective),
        effective_checksum=effective_checksum, status="USABLE", reason_code=None,
    )


def _files_modified_since_manifest(context_dir: Path, manifest: "cbc.BuildContextManifest") -> Optional[str]:
    """Recompute per-file sha256 for the exported files and return the first path whose content no longer
    matches the manifest (detects a file changed after manifest generation). None == unmodified."""
    for entry in manifest.files:
        p = context_dir / entry.path
        if not p.exists() or p.is_symlink() or not p.is_file():
            return entry.path
        digest, _size = cbc._sha256_file(p)
        if digest != entry.sha256:
            return entry.path
    return None


def assert_context_is_governed(ctx: GovernedContext, source_sha: str) -> None:
    """§8 refuse a hand-created dir / cwd / arbitrary tarball / stale / failed context."""
    if ctx is None or ctx.manifest is None:
        raise GovernedBuildError("no governed context (refusing hand-created/arbitrary context)")
    if not ctx.usable:
        raise GovernedBuildError(f"context is not USABLE: {ctx.reason_code}")
    status_file = ctx.context_dir / _STATUS_FILE
    if not status_file.exists():
        raise GovernedBuildError("context carries no governed status marker (arbitrary dir refused)")
    if not (ctx.context_dir / ".hermes_build_context").exists():
        raise GovernedBuildError("context carries no clean-context sentinel (arbitrary dir refused)")
    if ctx.manifest.source_sha != source_sha:
        raise GovernedBuildError("context source SHA does not match the requested build SHA (stale)")


# =============================================================================== §9 final TOCTOU boundary
@dataclass(frozen=True)
class ContextSnapshot:
    """An immutable snapshot of the verified context taken at the finalisation boundary. It records, for
    every context member, the (sha256, size, POSIX mode, is-symlink) plus the file count, the manifest
    checksum and the effective-context checksum. Immediately before the (fake) runner receives the context,
    the snapshot is REVALIDATED: any file changed / appeared / disappeared / changed-type / changed-mode
    fails closed BEFORE the runner is ever invoked."""

    source_sha: str
    manifest_checksum: str
    effective_checksum: str
    file_count: int
    members: Tuple[Tuple[str, str, int, int, bool], ...]   # (relpath, sha256|"", size, mode, is_symlink)


def _iter_all_members(context_dir: Path) -> List[Tuple[str, Path]]:
    """Every member under the context (files AND symlinks), excluding the governed markers, sorted."""
    out: List[Tuple[str, Path]] = []
    for abspath in sorted(context_dir.rglob("*")):
        rel = abspath.relative_to(context_dir).as_posix()
        if rel in (_STATUS_FILE, ".hermes_build_context", _QUARANTINE_MARKER):
            continue
        if abspath.is_dir() and not abspath.is_symlink():
            continue
        out.append((rel, abspath))
    out.sort(key=lambda t: t[0])
    return out


def _member_metadata(abspath: Path) -> Tuple[str, int, int, bool]:
    """(sha256|"" for symlink, size, POSIX mode bits, is_symlink)."""
    is_link = abspath.is_symlink()
    st = abspath.lstat()
    mode = st.st_mode & 0o777
    if is_link:
        return ("", 0, mode, True)
    digest, size = cbc._sha256_file(abspath)
    return (digest, size, mode, False)


def finalise_context_snapshot(ctx: GovernedContext) -> ContextSnapshot:
    """Take the immutable finalisation snapshot of a USABLE governed context."""
    if ctx is None or ctx.manifest is None or not ctx.usable:
        raise GovernedBuildError("cannot snapshot a non-usable context")
    members: List[Tuple[str, str, int, int, bool]] = []
    for rel, abspath in _iter_all_members(ctx.context_dir):
        digest, size, mode, is_link = _member_metadata(abspath)
        members.append((rel, digest, size, mode, is_link))
    return ContextSnapshot(
        source_sha=ctx.manifest.source_sha,
        manifest_checksum=ctx.manifest.manifest_checksum,
        effective_checksum=ctx.effective_checksum,
        file_count=len(members),
        members=tuple(members),
    )


def revalidate_context_snapshot(ctx: GovernedContext, snapshot: ContextSnapshot) -> Optional[str]:
    """Immediately-before-runner TOCTOU revalidation. Returns None if the context is byte-for-byte identical
    to the snapshot (incl. count, per-file checksum, size, mode, symlink-ness, manifest + effective
    checksums), else the first mismatch reason. The runner is invoked ONLY when this returns None."""
    if ctx is None or ctx.manifest is None or not ctx.usable:
        return "CONTEXT-NOT-USABLE-AT-REVALIDATION"
    if ctx.manifest.manifest_checksum != snapshot.manifest_checksum:
        return "MANIFEST-CHECKSUM-CHANGED"
    if ctx.manifest.manifest_checksum != ctx.manifest.compute_checksum():
        return "MANIFEST-CHECKSUM-SELF-INVALID"
    if ctx.effective_checksum != snapshot.effective_checksum:
        return "EFFECTIVE-CHECKSUM-CHANGED"
    current = _iter_all_members(ctx.context_dir)
    if len(current) != snapshot.file_count:
        return f"FILE-COUNT-CHANGED:{snapshot.file_count}->{len(current)}"
    snap_by_rel = {m[0]: m for m in snapshot.members}
    cur_rels = set()
    for rel, abspath in current:
        cur_rels.add(rel)
        if rel not in snap_by_rel:
            return f"FILE-APPEARED:{rel}"
        _r, sha, size, mode, is_link = snap_by_rel[rel]
        c_sha, c_size, c_mode, c_link = _member_metadata(abspath)
        if c_link != is_link:
            return f"FILE-TYPE-CHANGED:{rel}"
        if c_mode != mode:
            return f"FILE-MODE-CHANGED:{rel}"
        if not is_link and (c_sha != sha or c_size != size):
            return f"FILE-CONTENT-CHANGED:{rel}"
    missing = set(snap_by_rel) - cur_rels
    if missing:
        return f"FILE-DISAPPEARED:{sorted(missing)[0]}"
    return None


# =============================================================================== §10 effective context
@dataclass(frozen=True)
class EffectiveContextVerdict:
    ok: bool
    included_count: int
    effective_checksum: str
    missing_inclusions: Tuple[str, ...]
    prohibited_present: Tuple[str, ...]
    missing_context_files: Tuple[str, ...]
    phase2_module_count: int
    reason_codes: Tuple[str, ...]

    def to_dict(self) -> Dict[str, object]:
        return {
            "ok": self.ok,
            "included_count": self.included_count,
            "effective_checksum": self.effective_checksum,
            "missing_inclusions": list(self.missing_inclusions),
            "prohibited_present": list(self.prohibited_present),
            "missing_context_files": list(self.missing_context_files),
            "phase2_module_count": self.phase2_module_count,
            "reason_codes": list(self.reason_codes),
        }


def verify_effective_context(tracked_names: Sequence[str], dockerignore_text: str) -> EffectiveContextVerdict:
    """§10 / F-113-03. Establish the ACTUAL file set Docker would include (via the parity-fixtured
    matcher, NOT PR#113's homemade one) and verify required inclusions survive + required exclusions are
    gone. Deterministic effective-context checksum. §10: unsupported .dockerignore syntax FAILS CLOSED."""
    try:
        included = di.effective_context(tracked_names, dockerignore_text)
    except di.UnsupportedPatternError:
        return EffectiveContextVerdict(
            ok=False, included_count=0, effective_checksum="", missing_inclusions=tuple(),
            prohibited_present=tuple(), missing_context_files=tuple(), phase2_module_count=0,
            reason_codes=("UNSUPPORTED-DOCKERIGNORE-SYNTAX",),
        )
    included_set = set(included)
    tracked_set = set(tracked_names)
    reasons: List[str] = []

    missing = tuple(p for p in REQUIRED_EFFECTIVE_INCLUSIONS if p in tracked_set and p not in included_set)
    # A required inclusion that is not even tracked is also a failure (source drift).
    untracked_required = tuple(p for p in REQUIRED_EFFECTIVE_INCLUSIONS if p not in tracked_set)
    missing_all = tuple(sorted(set(missing) | set(untracked_required)))
    if missing_all:
        reasons.append("MISSING-REQUIRED-INCLUSIONS")

    phase2 = [p for p in included_set
              if p.startswith(PHASE2_MODULE_PREFIX) and p.endswith(PHASE2_MODULE_SUFFIX)]
    if len(phase2) < REQUIRED_PHASE2_MODULE_COUNT:
        reasons.append("MISSING-PHASE2-MODULES")

    prohibited = tuple(sorted(
        p for p in included_set
        for _rid, rx in PROHIBITED_EFFECTIVE_PATTERNS if rx.search(p)
    ))
    if prohibited:
        reasons.append("PROHIBITED-IN-EFFECTIVE-CONTEXT")

    missing_ctx = tuple(p for p in REQUIRED_CONTEXT_PRESENT if p not in tracked_set)
    if missing_ctx:
        reasons.append("MISSING-REQUIRED-CONTEXT-FILES")

    return EffectiveContextVerdict(
        ok=(len(reasons) == 0),
        included_count=len(included),
        effective_checksum=di.effective_context_checksum(included),
        missing_inclusions=missing_all,
        prohibited_present=prohibited,
        missing_context_files=missing_ctx,
        phase2_module_count=len(phase2),
        reason_codes=tuple(reasons),
    )


# =============================================================================== §11/§12 build inputs
@dataclass(frozen=True)
class BuildInputs:
    """§11 MANDATORY build inputs. No env fallback, no branch/HEAD/latest, no arbitrary docker args."""

    source_sha: str
    build_utc: str
    expected_repository: str
    candidate_name: str
    dockerfile_path: str          # exact Dockerfile path inside the verified context
    context_dir: str              # exact verified context path
    local_image_target: str       # non-publishing local target


def validate_candidate_name(name: str) -> str:
    """Bounded, non-registry candidate name. Rejects '/', ':', registry hosts, whitespace, over-length."""
    if not isinstance(name, str) or not _CANDIDATE_NAME_RE.match(name):
        raise GovernedBuildError(
            "candidate_name must be 3-64 chars [a-z0-9._-], no '/' or ':' (non-registry, bounded)"
        )
    if "/" in name or ":" in name or any(c.isspace() for c in name):
        raise GovernedBuildError("candidate_name must not contain '/', ':' or whitespace")
    return name


def validate_build_utc(value: str) -> str:
    """tz-aware UTC ISO-8601 (offset +00:00 or Z). Naive/local rejected — no env fallback."""
    if not isinstance(value, str) or not _UTC_ISO_RE.match(value.strip()):
        raise GovernedBuildError("build_utc must be a tz-aware UTC ISO-8601 timestamp (+00:00 or Z)")
    return value.strip()


def make_local_target(candidate_name: str, source_sha: str) -> str:
    """A purely LOCAL, non-registry image target. No registry host, no namespace slash."""
    return f"hermes-fw08-candidate-{candidate_name}:local-{source_sha[:12]}"


def build_inputs(
    *,
    source_sha: str,
    build_utc: str,
    expected_repository: str,
    candidate_name: str,
    context_dir: Path,
    context_source_sha: str,
) -> BuildInputs:
    """Assemble + validate the MANDATORY build inputs. context source SHA MUST equal the input SHA."""
    try:
        cbc.verify_source_sha(source_sha)
    except cbc.CleanBuildContextError as exc:
        raise GovernedBuildError(str(exc)) from exc
    validate_build_utc(build_utc)
    validate_candidate_name(candidate_name)
    if not expected_repository:
        raise GovernedBuildError("expected_repository is mandatory")
    if context_source_sha != source_sha:
        raise GovernedBuildError("context source SHA != input source SHA (no substitution permitted)")
    dockerfile = context_dir / "Dockerfile"
    if not dockerfile.is_file():
        raise GovernedBuildError("verified context has no Dockerfile at the exact path")
    return BuildInputs(
        source_sha=source_sha,
        build_utc=validate_build_utc(build_utc),
        expected_repository=expected_repository,
        candidate_name=validate_candidate_name(candidate_name),
        dockerfile_path=str(dockerfile),
        context_dir=str(context_dir),
        local_image_target=make_local_target(candidate_name, source_sha),
    )


def build_docker_command(inputs: BuildInputs) -> Tuple[str, ...]:
    """§12 fully wrapper-constructed docker argument ARRAY. NO shell, NO user-supplied arbitrary args,
    NO --push/registry/remote-builder/host-network/privileged/uncontrolled-secret. Mandatory
    --build-arg SOURCE_SHA + BUILD_UTC, explicit local target, exact Dockerfile + context paths."""
    cmd = (
        "docker", "build",
        "--file", inputs.dockerfile_path,
        "--tag", inputs.local_image_target,
        "--build-arg", f"SOURCE_SHA={inputs.source_sha}",
        "--build-arg", f"BUILD_UTC={inputs.build_utc}",
        "--label", f"{lbl.LABEL_REVISION}={inputs.source_sha}",
        "--label", f"{lbl.LABEL_CREATED}={inputs.build_utc}",
        "--no-cache",
        inputs.context_dir,
    )
    assert_no_prohibited_docker_args(cmd)
    return cmd


def assert_no_prohibited_docker_args(cmd: Sequence[str]) -> None:
    """§12/§20. Reject any docker argument that would publish/deploy or open an uncontrolled surface."""
    for arg in cmd:
        if arg in _DOCKER_ARG_ALLOWLIST:
            continue
        low = str(arg).lower()
        for bad in _PROHIBITED_DOCKER_ARG_SUBSTRINGS:
            if bad in low:
                raise GovernedBuildError(f"prohibited docker argument rejected: {arg!r} (matched {bad!r})")
    # A registry-style tag (host with a dot/port before the first '/') is rejected.
    for i, arg in enumerate(cmd):
        if arg == "--tag" and i + 1 < len(cmd):
            tag = cmd[i + 1]
            head = tag.split("/", 1)[0]
            if "/" in tag and ("." in head or ":" in head):
                raise GovernedBuildError(f"registry-style tag rejected (non-local target): {tag!r}")


# =============================================================================== §12 injectable runners
@dataclass(frozen=True)
class BuildOutcome:
    image_id: str
    local_target: str
    returncode: int


class DockerRunner:
    """Injectable runner boundary. The wrapper NEVER calls docker/SBOM/scan directly — only through a
    runner. Subclasses provide behaviour; the default refuses everything."""

    def build(self, command: Sequence[str], *, timeout: int = _BUILD_TIMEOUT_SEC) -> BuildOutcome:
        raise NotImplementedError

    def inspect(self, image_id: str) -> Optional[object]:
        raise NotImplementedError

    def image_content(self, image_id: str) -> Optional[Mapping[str, object]]:
        raise NotImplementedError

    def sbom(self, image_id: str, *, source_sha: str, build_utc: str) -> Optional[Mapping[str, object]]:
        raise NotImplementedError

    def vuln_scan(self, image_id: str, *, sbom: Mapping[str, object]) -> Optional[Mapping[str, object]]:
        raise NotImplementedError


class RefusingDockerRunner(DockerRunner):
    """DEFAULT runner. Refuses every operation — the wrapper cannot build/inspect/scan unless an explicit
    runner is injected. Guarantees no accidental real docker invocation."""

    def build(self, command: Sequence[str], *, timeout: int = _BUILD_TIMEOUT_SEC) -> BuildOutcome:
        raise RealDockerInvocationForbidden(
            "default runner refuses to build; inject an explicit runner (fake for tests / gated real)"
        )

    def inspect(self, image_id: str) -> Optional[object]:
        raise RealDockerInvocationForbidden("default runner refuses to inspect")

    def image_content(self, image_id: str) -> Optional[Mapping[str, object]]:
        raise RealDockerInvocationForbidden("default runner refuses image-content inspection")

    def sbom(self, image_id: str, *, source_sha: str, build_utc: str) -> Optional[Mapping[str, object]]:
        raise RealDockerInvocationForbidden("default runner refuses SBOM generation")

    def vuln_scan(self, image_id: str, *, sbom: Mapping[str, object]) -> Optional[Mapping[str, object]]:
        raise RealDockerInvocationForbidden("default runner refuses vulnerability scanning")


class RealDockerRunner(DockerRunner):
    """The real runner is a DISTINCT class, doubly gated: it requires `enable_real_execution=True` AND the
    environment variable HERMES_FW08_ALLOW_REAL_DOCKER=1. Tests NEVER set either, so no real `docker build`
    can run during tests. Every method fails closed BEFORE any subprocess if the gate is not satisfied."""

    def __init__(self, *, enable_real_execution: bool = False) -> None:
        self._enabled = bool(enable_real_execution)

    def _require_gate(self, op: str) -> None:
        if not self._enabled or os.environ.get(_REAL_DOCKER_ENV_GATE) != "1":
            raise RealDockerInvocationForbidden(
                f"real docker {op} not authorised (needs enable_real_execution=True AND "
                f"{_REAL_DOCKER_ENV_GATE}=1); refusing before any subprocess"
            )

    def build(self, command: Sequence[str], *, timeout: int = _BUILD_TIMEOUT_SEC) -> BuildOutcome:
        self._require_gate("build")  # fail closed before ANY subprocess
        assert_no_prohibited_docker_args(command)
        proc = subprocess.run(  # noqa: S603 - explicit arg array, no shell, bounded timeout, gated
            list(command), capture_output=True, timeout=timeout, check=False, text=True,
        )
        if proc.returncode != 0:
            raise GovernedBuildError(f"docker build failed (rc={proc.returncode})")
        # A real implementation would parse the image id from build metadata; unreachable in tests.
        return BuildOutcome(image_id=proc.stdout.strip()[:_MAX_OUTPUT_BYTES], local_target="",
                            returncode=proc.returncode)

    def inspect(self, image_id: str) -> Optional[object]:
        self._require_gate("inspect")
        raise RealDockerInvocationForbidden("real inspect unreachable in this WO")

    def image_content(self, image_id: str) -> Optional[Mapping[str, object]]:
        self._require_gate("image_content")
        raise RealDockerInvocationForbidden("real image_content unreachable in this WO")

    def sbom(self, image_id: str, *, source_sha: str, build_utc: str) -> Optional[Mapping[str, object]]:
        self._require_gate("sbom")
        raise RealDockerInvocationForbidden("real sbom unreachable in this WO")

    def vuln_scan(self, image_id: str, *, sbom: Mapping[str, object]) -> Optional[Mapping[str, object]]:
        self._require_gate("vuln_scan")
        raise RealDockerInvocationForbidden("real vuln_scan unreachable in this WO")


# =============================================================================== §13 OCI post-build
@dataclass(frozen=True)
class OciPostBuildVerdict:
    ok: bool
    source_sha: Optional[str]
    build_utc: Optional[str]
    reason_codes: Tuple[str, ...]

    def to_dict(self) -> Dict[str, object]:
        return {"ok": self.ok, "source_sha": self.source_sha, "build_utc": self.build_utc,
                "reason_codes": list(self.reason_codes)}


def verify_oci_postbuild(
    inspect_payload: Optional[object],
    *,
    expected_source_sha: str,
    expected_build_utc: str,
    image_id: Optional[str],
) -> OciPostBuildVerdict:
    """§13 / F-113-02. Post-build inspection is MANDATORY. expected_source_sha is NEVER optional. Verifies
    revision + created labels present/valid, source-SHA + build-UTC equality, image id captured, no
    whitespace/duplicate/missing value. Any failure -> candidate readiness FAILS."""
    if not expected_source_sha:
        raise GovernedBuildError("expected_source_sha is mandatory for OCI post-build inspection")
    reasons: List[str] = []
    if not image_id:
        reasons.append("IMAGE-ID-UNAVAILABLE")
    if inspect_payload is None:
        return OciPostBuildVerdict(False, None, None, ("INSPECTION-UNAVAILABLE",) + tuple(reasons))
    try:
        labels = lbl.labels_from_docker_inspect(inspect_payload)
    except lbl.LabelVerifyError:
        return OciPostBuildVerdict(False, None, None, ("INSPECT-PAYLOAD-MALFORMED",) + tuple(reasons))

    result = lbl.verify_labels(labels, expected_source_sha=expected_source_sha, is_candidate=True)
    if not result.ok:
        reasons.append("OCI-LABELS-INVALID")
    # Whitespace in a raw label value is a contract breach even if the trimmed value validated.
    for key in lbl.REQUIRED_LABELS:
        val = labels.get(key)
        if isinstance(val, str) and val != val.strip():
            reasons.append("LABEL-WHITESPACE")
            break
    # Build-UTC equality (verify_labels validates format/revision equality; created equality is explicit).
    if result.build_utc is not None and result.build_utc != expected_build_utc.strip():
        reasons.append("CREATED-NOT-EQUAL-BUILD-UTC")
    if result.build_utc is None:
        reasons.append("CREATED-UNAVAILABLE")

    ok = len(reasons) == 0 and result.ok
    return OciPostBuildVerdict(ok, result.source_sha, result.build_utc, tuple(reasons))


# =============================================================================== §14 image content
@dataclass(frozen=True)
class ImageContentVerdict:
    ok: bool
    reason_codes: Tuple[str, ...]

    def to_dict(self) -> Dict[str, object]:
        return {"ok": self.ok, "reason_codes": list(self.reason_codes)}


def verify_image_content(
    content: Optional[Mapping[str, object]],
    *,
    image_id: str,
) -> ImageContentVerdict:
    """§14 (fixture-backed). Establish labels/entrypoint/cmd/user/workdir/healthcheck/ports/required-files/
    module-inventory and prove NO active Phase-2 runtime import, NO test/evidence/.claude/secret/host-path/
    local-db content. Consumes an inspect/exported-fs fixture. No real image."""
    if content is None:
        return ImageContentVerdict(False, ("IMAGE-CONTENT-UNAVAILABLE",))
    reasons: List[str] = []

    def _seq(key: str) -> Tuple[str, ...]:
        v = content.get(key)
        return tuple(str(x) for x in v) if isinstance(v, (list, tuple)) else ()

    if str(content.get("image_id")) != image_id:
        reasons.append("IMAGE-ID-MISMATCH")
    if _seq("entrypoint") != EXPECTED_ENTRYPOINT:
        reasons.append("ENTRYPOINT-MISMATCH")
    if _seq("cmd") != EXPECTED_CMD:
        reasons.append("CMD-MISMATCH")
    if str(content.get("user")) != EXPECTED_USER:
        reasons.append("USER-MISMATCH")
    if str(content.get("workdir")) != EXPECTED_WORKDIR:
        reasons.append("WORKDIR-MISMATCH")
    if not content.get("healthcheck"):
        reasons.append("HEALTHCHECK-MISSING")
    ports = _seq("exposed_ports")
    if not any(p.split("/")[0] == EXPECTED_PORT for p in ports):
        reasons.append("PORT-MISSING")

    files = [str(f) for f in content.get("files", []) if isinstance(content.get("files"), (list, tuple))]
    file_set = set(files)
    # Required runtime files present.
    required_files = set(REQUIRED_EFFECTIVE_INCLUSIONS)
    if not required_files.issubset(file_set):
        reasons.append("REQUIRED-FILE-MISSING")
    phase2 = [p for p in file_set
              if p.startswith(PHASE2_MODULE_PREFIX) and p.endswith(PHASE2_MODULE_SUFFIX)]
    if len(phase2) < REQUIRED_PHASE2_MODULE_COUNT:
        reasons.append("PHASE2-MODULES-MISSING")
    # Prohibited content present (defence-in-depth over the fixture file list).
    prohibited = [p for p in file_set
                  if cbc.scan_path(p) or any(rx.search(p) for _rid, rx in PROHIBITED_EFFECTIVE_PATTERNS)]
    if prohibited:
        reasons.append("PROHIBITED-FILE-PRESENT")
    # NO active Phase-2 runtime import (coarse list form).
    importers = content.get("phase2_actively_imported_by")
    if isinstance(importers, (list, tuple)) and len(importers) > 0:
        reasons.append("ACTIVE-PHASE2-IMPORT-DETECTED")

    # §13: if the (future real) image carries a static import-graph evidence record, it must PROVE the
    # Phase-2 modules are present-but-inert (no entrypoint direct/transitive import, no runner/callback
    # registration, no dynamic import, no activation env default, no plugin discovery, admissible method).
    evidence = content.get("phase2_import_evidence")
    if isinstance(evidence, Mapping):
        verdict = ai.validate_phase2_inert(
            evidence, phase2_prefix=PHASE2_MODULE_PREFIX, phase2_suffix=PHASE2_MODULE_SUFFIX,
            required_phase2_count=REQUIRED_PHASE2_MODULE_COUNT,
        )
        if not verdict.inert:
            reasons.append("ACTIVE-PHASE2-IMPORT-EVIDENCE-FAILED")

    return ImageContentVerdict(len(reasons) == 0, tuple(sorted(set(reasons))))


# =============================================================================== §15 SBOM
@dataclass(frozen=True)
class SbomVerdict:
    ok: bool
    package_count: int
    reason_codes: Tuple[str, ...]

    def to_dict(self) -> Dict[str, object]:
        return {"ok": self.ok, "package_count": self.package_count, "reason_codes": list(self.reason_codes)}


def _sbom_checksum(sbom: Mapping[str, object]) -> str:
    core = {k: sbom.get(k) for k in ("tool", "format", "image_id", "source_sha", "build_utc", "packages")}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def verify_sbom(
    sbom: Optional[Mapping[str, object]],
    *,
    image_id: str,
    source_sha: str,
) -> SbomVerdict:
    """§15 (fixture/fake-runner). SBOM is MANDATORY for candidate readiness. Verifies supported tool +
    format, image-id + source-SHA binding, package count, and checksum integrity."""
    if sbom is None:
        return SbomVerdict(False, 0, ("SBOM-UNAVAILABLE",))
    reasons: List[str] = []
    if sbom.get("tool") not in SUPPORTED_SBOM_TOOLS:
        reasons.append("SBOM-TOOL-UNSUPPORTED")
    if sbom.get("format") not in SUPPORTED_SBOM_FORMATS:
        reasons.append("SBOM-FORMAT-UNSUPPORTED")
    if str(sbom.get("image_id")) != image_id:
        reasons.append("SBOM-IMAGE-ID-MISMATCH")
    if str(sbom.get("source_sha")) != source_sha:
        reasons.append("SBOM-SOURCE-SHA-MISMATCH")
    packages = sbom.get("packages")
    count = len(packages) if isinstance(packages, (list, tuple)) else 0
    if count <= 0:
        reasons.append("SBOM-NO-PACKAGES")
    declared = sbom.get("checksum")
    if not isinstance(declared, str) or declared != _sbom_checksum(sbom):
        reasons.append("SBOM-CHECKSUM-INVALID")
    return SbomVerdict(len(reasons) == 0, count, tuple(sorted(set(reasons))))


# =============================================================================== §16 vuln scan
@dataclass(frozen=True)
class VulnVerdict:
    ok: bool
    finding_count: int
    ungoverned_critical: int
    ungoverned_high: int
    reason_codes: Tuple[str, ...]

    def to_dict(self) -> Dict[str, object]:
        return {
            "ok": self.ok, "finding_count": self.finding_count,
            "ungoverned_critical": self.ungoverned_critical, "ungoverned_high": self.ungoverned_high,
            "reason_codes": list(self.reason_codes),
        }


def _vuln_checksum(scan: Mapping[str, object]) -> str:
    core = {k: scan.get(k) for k in ("scanner", "image_id", "sbom_ref", "db_timestamp_utc", "findings")}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def verify_vuln_scan(
    scan: Optional[Mapping[str, object]],
    *,
    image_id: str,
    now_utc: str,
    source_sha: str = "",
    max_db_age_hours: int = 168,
) -> VulnVerdict:
    """§11/§16 (fixture/fake-runner). Vuln-scan is MANDATORY. Verifies scanner, image-id binding, DB
    freshness (governed exception required if stale), severity taxonomy, and policy thresholds (0 ungoverned
    CRITICAL/HIGH). A CRITICAL/HIGH is governed ONLY by an EXACT, TYPED, UNEXPIRED disposition (design/
    hermes_fw08_vuln_disposition_v1) binding EVERY required field — a truthy id alone, a wildcard, an
    expired/mismatched disposition, or a missing field does NOT govern. No blanket exception."""
    if scan is None:
        return VulnVerdict(False, 0, 0, 0, ("VULN-SCAN-UNAVAILABLE",))
    reasons: List[str] = []
    scanner_id = str(scan.get("scanner", ""))
    scanner_version = str(scan.get("scanner_version", ""))
    if scanner_id not in SUPPORTED_SCANNERS:
        reasons.append("VULN-SCANNER-UNSUPPORTED")
    if str(scan.get("image_id")) != image_id:
        reasons.append("VULN-IMAGE-ID-MISMATCH")

    # DB freshness.
    db_ts = scan.get("db_timestamp_utc")
    stale = True
    try:
        if isinstance(db_ts, str):
            parsed = datetime.datetime.fromisoformat(db_ts.replace("Z", "+00:00"))
            now = datetime.datetime.fromisoformat(now_utc.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                stale = (now - parsed) > datetime.timedelta(hours=max_db_age_hours)
    except ValueError:
        reasons.append("VULN-DB-TIMESTAMP-MALFORMED")
    if stale and not scan.get("db_freshness_exception"):
        reasons.append("VULN-DB-STALE-NO-EXCEPTION")

    # Build the typed dispositions from the allow-list; a malformed entry simply cannot govern anything.
    dispositions: List[vd.VulnDisposition] = []
    for entry in scan.get("allowlist", []):
        if not isinstance(entry, Mapping):
            continue
        try:
            dispositions.append(vd.from_dict(entry))
        except vd.VulnDispositionError:
            continue  # unusable governance -> does NOT govern (fail closed)

    def _governed(f: Mapping[str, object]) -> bool:
        for d in dispositions:
            ok, _reason = vd.governs(
                d, f, image_id=image_id, source_sha=source_sha,
                scanner_id=scanner_id, scanner_version=scanner_version, now_utc=now_utc,
            )
            if ok:
                return True
        return False

    findings = scan.get("findings") or []
    ungoverned_c = 0
    ungoverned_h = 0
    for f in findings:
        if not isinstance(f, Mapping):
            reasons.append("VULN-FINDING-MALFORMED")
            continue
        sev = str(f.get("severity", "")).upper()
        if sev not in SEVERITY_TAXONOMY:
            reasons.append("VULN-SEVERITY-OUT-OF-TAXONOMY")
        governed = _governed(f) if sev in ("CRITICAL", "HIGH") else False
        if sev == "CRITICAL" and not governed:
            ungoverned_c += 1
        if sev == "HIGH" and not governed:
            ungoverned_h += 1
    if ungoverned_c > 0:
        reasons.append("VULN-UNGOVERNED-CRITICAL")
    if ungoverned_h > 0:
        reasons.append("VULN-UNGOVERNED-HIGH")

    declared = scan.get("checksum")
    if not isinstance(declared, str) or declared != _vuln_checksum(scan):
        reasons.append("VULN-CHECKSUM-INVALID")

    return VulnVerdict(
        ok=(len(reasons) == 0), finding_count=len(findings),
        ungoverned_critical=ungoverned_c, ungoverned_high=ungoverned_h,
        reason_codes=tuple(sorted(set(reasons))),
    )


# =============================================================================== §6 orchestration
@dataclass
class CandidateBuildReport:
    """Immutable-by-convention evidence bundle. Local candidate evidence ONLY — never a publication."""

    source_sha: str
    build_utc: str
    candidate_name: str
    ready: bool
    state: str
    reason_codes: Tuple[str, ...]
    provenance: Optional[Dict[str, object]]
    effective_context: Optional[Dict[str, object]]
    oci_postbuild: Optional[Dict[str, object]]
    image_content: Optional[Dict[str, object]]
    sbom: Optional[Dict[str, object]]
    vuln_scan: Optional[Dict[str, object]]
    transitions: List[Dict[str, object]]
    image_id: Optional[str] = None
    # §21 mechanical safety assertions — always false for this WO.
    runtime_wired: bool = False
    config_installed: bool = False
    shadow_enabled: bool = False
    shadow_executed: bool = False
    consumer_live: bool = False
    phase2_activated: bool = False
    published: bool = False
    deployed: bool = False

    def to_dict(self) -> Dict[str, object]:
        return {
            "contract_version": CONTRACT_VERSION,
            "tool_version": TOOL_VERSION,
            "application": APPLICATION,
            "source_sha": self.source_sha,
            "build_utc": self.build_utc,
            "candidate_name": self.candidate_name,
            "ready": self.ready,
            "state": self.state,
            "reason_codes": list(self.reason_codes),
            "image_id": self.image_id,
            "provenance": self.provenance,
            "effective_context": self.effective_context,
            "oci_postbuild": self.oci_postbuild,
            "image_content": self.image_content,
            "sbom": self.sbom,
            "vuln_scan": self.vuln_scan,
            "transitions": self.transitions,
            "runtime_wired": self.runtime_wired,
            "config_installed": self.config_installed,
            "shadow_enabled": self.shadow_enabled,
            "shadow_executed": self.shadow_executed,
            "consumer_live": self.consumer_live,
            "phase2_activated": self.phase2_activated,
            "published": self.published,
            "deployed": self.deployed,
        }


def _build_gate_evidence(
    *, source_sha: str, image_id: Optional[str], now_utc: str,
    oci_ok: bool, content_ok: bool, sbom_ok: bool, vuln_ok: bool, clean_ok: bool, effective_ok: bool,
) -> Optional[List["ee.GateEvidence"]]:
    """Assemble the §12 immutable typed gate-evidence records for the readiness evaluation. Returns None if
    the preconditions for evidence-binding are not met (non-40-hex source or missing image id) so the
    boolean evaluator remains the guard."""
    if not _FULL_SHA_RE.match(str(source_sha)) or not image_id:
        return None
    def _ev(gate_id: str, result: bool, reason: str, *, image: Optional[str]) -> "ee.GateEvidence":
        return ee.make_gate_evidence(
            gate_id=gate_id, result=result, reason_code=reason, source_sha=source_sha,
            producer="tools/hermes_stage_b_build_v1.py", tool_version=TOOL_VERSION, at_utc=now_utc,
            image_id=image,
        )
    return [
        _ev("G-TRUSTED-SOURCE", True, "TRUSTED-SOURCE-VERIFIED", image=None),
        _ev("G-CLEAN-CONTEXT", clean_ok, "CLEAN-CONTEXT-MANIFEST", image=None),
        _ev("G-EFFECTIVE-CONTEXT", effective_ok, "EFFECTIVE-CONTEXT-VERIFIED", image=None),
        _ev("G-BUILD", True, "BUILD-COMPLETED", image=image_id),
        _ev("G-OCI-LABELS", oci_ok, "OCI-LABELS-VERIFIED", image=image_id),
        _ev("G-IMAGE-CONTENT", content_ok, "IMAGE-CONTENT-VERIFIED", image=image_id),
        _ev("G-SBOM", sbom_ok, "SBOM-VERIFIED", image=image_id),
        _ev("G-VULN-SCAN", vuln_ok, "VULN-SCAN-VERIFIED", image=image_id),
    ]


# R-1 §7 the sole governed evidence producer identity (the in-process wrapper). Trust is conferred by the
# producer-trust contract + unforgeable in-process seal — NEVER by a checksum alone.
GOVERNED_PRODUCER_ID = "tools/hermes_stage_b_build_v1.py"
GOVERNED_TRUST_POLICY_VERSION = "1"


def make_candidate_id(candidate_name: str, source_sha: str) -> str:
    """Deterministic candidate identity binding the candidate name to its exact source SHA."""
    return f"{candidate_name}@{source_sha}"


def _build_trusted_chain(
    evidence: Sequence["ee.GateEvidence"], *, candidate_id: str, source_sha: str, image_id: str,
    now_utc: str,
) -> Tuple["ec.ChainVerdict", Optional[Dict[str, object]]]:
    """R-1 §7/§8. Seal each typed gate-evidence record with an EPHEMERAL in-process key held ONLY by this
    wrapper, register the sole trusted producer, assemble the Merkle-bound evidence chain, and validate it.
    A public caller cannot forge a seal (no key) nor register a trusted producer, so readiness is reachable
    ONLY from evidence this wrapper itself produced. Returns (verdict, chain_manifest_dict)."""
    sealer = pt.GovernedEvidenceSealer(producer_id=GOVERNED_PRODUCER_ID)
    contract = pt.ProducerTrustContract(
        contract_version=pt.CONTRACT_VERSION, producer_id=GOVERNED_PRODUCER_ID,
        producer_type="IN_PROCESS_GOVERNED_WRAPPER", trust_contract_version=CONTRACT_VERSION,
        allowed_gate_ids=ee.GATE_ORDER, application=APPLICATION, module_identity=GOVERNED_PRODUCER_ID,
        content_digest=_self_content_digest(), source_version=source_sha,
        trust_policy_version=GOVERNED_TRUST_POLICY_VERSION, sealing_method=pt.SEALING_METHOD_IN_PROCESS,
        candidate_binding=candidate_id, source_binding=source_sha,
        created_utc=now_utc, expiry_utc=_plus_hours(now_utc, 24), audit_reference="FW08-IN-PROCESS-GOVERNED",
    )
    registry = pt.ProducerTrustRegistry()
    registry.register(contract)
    sealed = [sealer.seal(rec) for rec in evidence]
    manifest = ec.build_evidence_chain(
        sealed, candidate_id=candidate_id, source_sha=source_sha, image_id=image_id,
        producer_trust_ref=contract.reference(), created_utc=now_utc, lifecycle_state="ASSEMBLED",
    )
    verdict = ec.validate_evidence_chain(
        manifest, sealed, verifier=sealer, registry=registry, expected_candidate_id=candidate_id,
        expected_source_sha=source_sha, expected_image_id=image_id,
        expected_producer_trust_ref=contract.reference(), now_utc=now_utc,
    )
    return verdict, manifest.to_dict()


def _self_content_digest() -> str:
    """Best-effort content digest of this wrapper module (producer executable/content digest, §7). Non-fatal
    if unreadable."""
    try:
        return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()
    except OSError:
        return "0" * 64


def _plus_hours(utc_iso: str, hours: int) -> str:
    base = datetime.datetime.fromisoformat(utc_iso.replace("Z", "+00:00"))
    return (base + datetime.timedelta(hours=hours)).isoformat()


def run_stage_b_candidate_build(
    *,
    source_sha: str,
    build_utc: str,
    candidate_name: str,
    repo_dir: Path,
    quarantine_dir: Path,
    now_utc: str,
    runner: Optional[DockerRunner] = None,
    expected_remote: str = DEFAULT_EXPECTED_REMOTE,
    allowed_refs: Sequence[str] = ALLOWED_CANONICAL_REFS,
    authorised_pr_heads: Optional[Mapping[str, str]] = None,
    dispositions: Optional[Sequence["cbc.SecretDisposition"]] = None,
    consumer_live: bool = False,
    shadow_enabled: bool = False,
    phase2_activated: bool = False,
    on_context_finalised: Optional[Callable[["ContextSnapshot"], None]] = None,
) -> CandidateBuildReport:
    """§6 the sole canonical Stage-B build sequence. Drives the state machine through every gate, invokes
    the INJECTABLE runner ONLY after all pre-build gates pass, and returns a candidate-readiness verdict.
    NEVER publishes or deploys. Fails closed (REJECTED) on every missing/contradictory input.

    If no runner is injected, the DEFAULT RefusingDockerRunner is used — the build cannot proceed. That is
    intentional: a real build requires an explicit gated runner that tests never supply."""
    runner = runner or RefusingDockerRunner()
    machine = sm.CandidateStateMachine(source_sha=source_sha if _FULL_SHA_RE.match(str(source_sha)) else "0" * 40)
    candidate_id = make_candidate_id(str(candidate_name), machine.source_sha)

    prov_d: Optional[Dict[str, object]] = None
    eff_d: Optional[Dict[str, object]] = None
    oci_d: Optional[Dict[str, object]] = None
    content_d: Optional[Dict[str, object]] = None
    sbom_d: Optional[Dict[str, object]] = None
    vuln_d: Optional[Dict[str, object]] = None
    image_id: Optional[str] = None

    def _reject(code: str) -> CandidateBuildReport:
        if not machine.is_rejected:
            machine.reject(now_utc, code, image_id=image_id)
        return _finalise(ready=False, extra_reasons=(code,))

    def _finalise(*, ready: bool, extra_reasons: Sequence[str]) -> CandidateBuildReport:
        return CandidateBuildReport(
            source_sha=str(source_sha), build_utc=str(build_utc), candidate_name=str(candidate_name),
            ready=ready, state=machine.state.value, reason_codes=tuple(extra_reasons),
            provenance=prov_d, effective_context=eff_d, oci_postbuild=oci_d, image_content=content_d,
            sbom=sbom_d, vuln_scan=vuln_d, transitions=[r.to_dict() for r in machine.records],
            image_id=image_id, consumer_live=consumer_live, shadow_enabled=shadow_enabled,
            phase2_activated=phase2_activated,
        )

    # --- gate: mandatory inputs -------------------------------------------------------------------
    try:
        validate_build_utc(build_utc)
        validate_candidate_name(candidate_name)
    except GovernedBuildError as exc:
        return _reject(f"INPUT-INVALID:{exc}")

    # --- gate: trusted provenance (§7) -----------------------------------------------------------
    try:
        prov = verify_trusted_provenance(
            source_sha, repo_dir, expected_remote=expected_remote, allowed_refs=allowed_refs,
            authorised_pr_heads=authorised_pr_heads,
        )
        prov_d = prov.to_dict()
        machine.advance(sm.State.SOURCE_VERIFIED, now_utc, "TRUSTED-SOURCE-VERIFIED")
    except GovernedBuildError as exc:
        return _reject(f"PROVENANCE-REJECTED:{exc}")

    # --- gate: governed clean context (§8/§9) ----------------------------------------------------
    ctx = prepare_governed_context(
        source_sha, repo_dir, quarantine_dir, expected_remote=expected_remote, now_utc=now_utc,
        dispositions=dispositions,
    )
    if not ctx.usable:
        return _reject(f"CONTEXT-UNUSABLE:{ctx.reason_code}")
    try:
        assert_context_is_governed(ctx, source_sha)
    except GovernedBuildError as exc:
        return _reject(f"CONTEXT-NOT-GOVERNED:{exc}")
    machine.advance(sm.State.CONTEXT_EXPORTED, now_utc, "CLEAN-CONTEXT-EXPORTED")

    # --- gate: docker-faithful effective context (§10) -------------------------------------------
    tracked = _tracked_names(source_sha, repo_dir)
    dockerignore_text = (repo_dir / ".dockerignore").read_text(encoding="utf-8")
    eff = verify_effective_context(tracked, dockerignore_text)
    eff_d = eff.to_dict()
    if not eff.ok:
        return _reject("EFFECTIVE-CONTEXT-INVALID")
    machine.advance(sm.State.CONTEXT_VERIFIED, now_utc, "EFFECTIVE-CONTEXT-VERIFIED")

    # --- gate: assemble mandatory build inputs + command (§11/§12) -------------------------------
    try:
        inputs = build_inputs(
            source_sha=source_sha, build_utc=build_utc, expected_repository=expected_remote,
            candidate_name=candidate_name, context_dir=ctx.context_dir,
            context_source_sha=ctx.manifest.source_sha,
        )
        command = build_docker_command(inputs)
    except GovernedBuildError as exc:
        return _reject(f"BUILD-INPUTS-INVALID:{exc}")
    machine.advance(sm.State.BUILD_READY, now_utc, "BUILD-READY")

    # --- §9 final TOCTOU boundary: snapshot then revalidate IMMEDIATELY before the runner receives the
    #     context. The runner is invoked ONLY if nothing changed/appeared/disappeared/changed-type/mode. ---
    snapshot = finalise_context_snapshot(ctx)
    if on_context_finalised is not None:
        on_context_finalised(snapshot)          # test hook: may mutate the context to prove TOCTOU rejects
    toctou = revalidate_context_snapshot(ctx, snapshot)
    if toctou is not None:
        return _reject(f"TOCTOU-CONTEXT-MUTATED:{toctou}")

    # --- R-2 §9/§10: IMMUTABLE build context (Option A). Do NOT hand the mutable working dir to the runner.
    #     Build a private project-local snapshot -> verify -> finalise (revoke writes + verify owner/mode) ->
    #     ATOMICALLY acquire exclusive ownership for the SINGLE runner. The runner receives ONLY the finalised
    #     context identity; any post-finalisation mutation invalidates before success. ---
    imm = imc.ImmutableBuildContext(source_sha=machine.source_sha, candidate_id=candidate_id)
    immutable_dir = Path(str(quarantine_dir)) / "_immutable_context"
    try:
        imm.create(ctx.context_dir, immutable_dir)
        imm.verify(effective_context_checksum=ctx.effective_checksum)
        finalised_identity = imm.finalise()
        finalised_identity = imm.acquire(
            "hermes_stage_b_runner",
            expected_candidate_id=candidate_id, expected_source_sha=machine.source_sha,
        )
    except imc.ImmutableContextError as exc:
        return _reject(f"IMMUTABLE-CONTEXT-FAILED:{exc}")

    # Re-point the docker command at the FINALISED immutable context — the runner sees nothing else.
    try:
        imm_inputs = build_inputs(
            source_sha=source_sha, build_utc=build_utc, expected_repository=expected_remote,
            candidate_name=candidate_name, context_dir=Path(finalised_identity.root_path),
            context_source_sha=ctx.manifest.source_sha,
        )
        command = build_docker_command(imm_inputs)
    except GovernedBuildError as exc:
        imm.reject(f"IMMUTABLE-INPUTS-INVALID:{exc}")
        return _reject(f"IMMUTABLE-BUILD-INPUTS-INVALID:{exc}")

    # --- INVOKE runner ONLY after all pre-build gates pass + immutable context finalised (§12/§15) ---
    try:
        outcome = runner.build(command)
        image_id = outcome.image_id
    except RealDockerInvocationForbidden as exc:
        return _reject(f"DOCKER-BUILD-REFUSED:{exc}")
    except GovernedBuildError as exc:
        return _reject(f"DOCKER-BUILD-FAILED:{exc}")
    if not image_id:
        return _reject("IMAGE-ID-NOT-CAPTURED")
    # The immutable context must still be byte-identical after the (fake) build consumed it.
    try:
        imm.assert_intact()
        imm.release()
    except imc.ImmutableContextError as exc:
        return _reject(f"IMMUTABLE-CONTEXT-MUTATED-DURING-BUILD:{exc}")
    machine.advance(sm.State.BUILD_COMPLETED, now_utc, "BUILD-COMPLETED", image_id=image_id)

    # --- gate: OCI post-build inspection (§13) ---------------------------------------------------
    try:
        inspect_payload = runner.inspect(image_id)
    except (RealDockerInvocationForbidden, GovernedBuildError):
        inspect_payload = None
    oci = verify_oci_postbuild(
        inspect_payload, expected_source_sha=source_sha, expected_build_utc=build_utc, image_id=image_id,
    )
    oci_d = oci.to_dict()
    if not oci.ok:
        return _reject("OCI-POSTBUILD-FAILED")

    # --- gate: image-content inspection (§14) ----------------------------------------------------
    try:
        content = runner.image_content(image_id)
    except (RealDockerInvocationForbidden, GovernedBuildError):
        content = None
    ic = verify_image_content(content, image_id=image_id)
    content_d = ic.to_dict()
    if not ic.ok:
        return _reject("IMAGE-CONTENT-FAILED")
    machine.advance(sm.State.IMAGE_INSPECTED, now_utc, "IMAGE-INSPECTED", image_id=image_id)

    # --- gate: SBOM (§15) ------------------------------------------------------------------------
    try:
        sbom_payload = runner.sbom(image_id, source_sha=source_sha, build_utc=build_utc)
    except (RealDockerInvocationForbidden, GovernedBuildError):
        sbom_payload = None
    sbom_v = verify_sbom(sbom_payload, image_id=image_id, source_sha=source_sha)
    sbom_d = sbom_v.to_dict()
    if not sbom_v.ok:
        return _reject("SBOM-FAILED")
    machine.advance(sm.State.SBOM_COMPLETED, now_utc, "SBOM-COMPLETED", image_id=image_id)

    # --- gate: vuln scan (§16) -------------------------------------------------------------------
    try:
        scan_payload = runner.vuln_scan(image_id, sbom=sbom_payload)
    except (RealDockerInvocationForbidden, GovernedBuildError):
        scan_payload = None
    vuln_v = verify_vuln_scan(scan_payload, image_id=image_id, now_utc=now_utc, source_sha=str(source_sha))
    vuln_d = vuln_v.to_dict()
    if not vuln_v.ok:
        return _reject("VULN-SCAN-FAILED")
    machine.advance(sm.State.VULNERABILITY_SCAN_COMPLETED, now_utc, "VULN-SCAN-COMPLETED", image_id=image_id)

    # --- F-2 §12: image-bound active-import evidence validated against INDEPENDENT governed anchors. The
    #     F-2 defect was that this block previously sourced the EXPECTED filesystem digest + trusted producer
    #     FROM ai_evidence ITSELF and fed them back into the comparator — a tautology (a record proving its
    #     own truth by repeating a value). It now derives the expected anchors from an
    #     IndependentAnchorBundle assembled from the GOVERNED BUILD RESULT + a dedicated OCI INSPECTION,
    #     resolved through a governed ProducerRegistry — structurally impossible to supply from ai_evidence.
    #     The wrapper NO LONGER copies fs_digest / producer_ref out of ai_evidence, and it does NOT accept an
    #     externally-assembled all-green active-import result. The default/fake runner supplies no
    #     active-import artifacts — the path is skipped and stays inert (behaviour identical to before). ---
    ai_evidence = None
    _ai_fn = getattr(runner, "active_import_evidence", None)
    if callable(_ai_fn):
        try:
            ai_evidence = _ai_fn(image_id, source_sha=str(source_sha))
        except (RealDockerInvocationForbidden, GovernedBuildError):
            ai_evidence = None
        # Independent producer artifacts + governed registry come from the runner; if ANY is missing, skip
        # (inert) — never fabricate an anchor from ai_evidence.
        build_result = getattr(runner, "build_result_evidence", None)
        oci_inspection = getattr(runner, "oci_inspection_evidence", None)
        registry = getattr(runner, "producer_registry", None)
        build_producer_id = getattr(runner, "build_producer_id", None)
        oci_producer_id = getattr(runner, "oci_producer_id", None)
        active_import_producer_id = getattr(runner, "active_import_producer_id", None)
        if (ai_evidence is not None and build_result is not None and oci_inspection is not None
                and registry is not None and build_producer_id and oci_producer_id
                and active_import_producer_id):
            # --- F2-R2 §7: explicit build/OCI producer-independence gate BEFORE any anchor construction. We
            #     resolve each producer's registration for its OWN role and evaluate independence from the
            #     bound registrations + evidence (same producer / registration / evidence object / evidence
            #     ref / copied payload / same tool+authority / dual-role / provenance). A failure rejects
            #     outright — no anchors are built, no downgrade. For REAL_CANDIDATE this sits inside the
            #     externally-rooted path (which itself fails closed at the trust provider); for TEST_ONLY /
            #     inert it runs here. When artifacts are absent (default runner) this whole block is skipped —
            #     behaviour byte-identical to before. ---
            _bld_reg, _ = registry.resolve(
                producer_id=build_producer_id, evidence_type="BUILD_RESULT", gate_id="STAGE_B_BUILD_RESULT",
                application=APPLICATION, now_utc=now_utc,
            )
            _oci_reg, _ = registry.resolve(
                producer_id=oci_producer_id, evidence_type="OCI_INSPECTION", gate_id="STAGE_B_OCI_INSPECTION",
                application=APPLICATION, now_utc=now_utc,
            )
            if _bld_reg is not None and _oci_reg is not None:
                _pi_reasons = pi.evaluate_producer_independence(
                    build_result=build_result, oci_inspection=oci_inspection,
                    build_registration=_bld_reg, oci_registration=_oci_reg,
                )
                if _pi_reasons:
                    return _reject("PRODUCER-INDEPENDENCE-FAILED:" + ",".join(_pi_reasons))
            identity_anchor, ii_reasons = iia.build_image_identity_anchor(
                build_result=build_result, oci_inspection=oci_inspection, candidate_id=candidate_id,
                source_sha=str(source_sha), producer_registry=registry,
                build_producer_id=build_producer_id, oci_producer_id=oci_producer_id, now_utc=now_utc,
                application=APPLICATION,
            )
            if identity_anchor is None:
                return _reject("IMAGE-BOUND-IMPORT-FAILED:" + ",".join(ii_reasons))
            fs_anchor, fs_reasons = ifa.build_image_filesystem_anchor(
                oci_inspection=oci_inspection, image_identity_anchor=identity_anchor,
                producer_registry=registry, oci_producer_id=oci_producer_id, now_utc=now_utc,
                application=APPLICATION,
            )
            if fs_anchor is None:
                return _reject("IMAGE-BOUND-IMPORT-FAILED:" + ",".join(fs_reasons))
            bundle, ab_reasons = anb.assemble_anchor_bundle(
                image_identity_anchor=identity_anchor, image_filesystem_anchor=fs_anchor,
                producer_registry=registry, build_producer_id=build_producer_id,
                oci_producer_id=oci_producer_id, active_import_producer_id=active_import_producer_id,
                now_utc=now_utc, application=APPLICATION,
            )
            if bundle is None:
                return _reject("IMAGE-BOUND-IMPORT-FAILED:" + ",".join(ab_reasons))
            # --- F2-R1 §17: registry AUTHENTICITY via a governed EXTERNAL authority boundary. A registry
            #     checksum proves INTEGRITY, not external AUTHENTICITY (a caller can mint a fresh registry,
            #     invent an approver, register real-authority producers, freeze it and recompute digests). If
            #     the runner exposes a governed authority resolver + activation authority AND the authority
            #     artifacts, we activate the registry through the resolver, mint a sealed ACTIVATED HANDLE,
            #     and validate active-import via the handle (the caller cannot forge the seal, and a REAL
            #     candidate cannot activate at all here since ProductionAuthorityResolver raises). If those
            #     artifacts are ABSENT (default/fake runner), the EXISTING raw-registry path is used
            #     UNCHANGED — byte-behaviour-identical to before, and no authority is self-created. ---
            _resolver = getattr(runner, "authority_resolver", None)
            _activation_authority = getattr(runner, "activation_authority", None)
            _authority_root_id = getattr(runner, "authority_root_id", None)
            _trust_domain_id = getattr(runner, "trust_domain_id", None)
            _registry_id = getattr(runner, "registry_id", None)
            _revocation_set = getattr(runner, "revocation_set", None)
            _candidate_use_mode = getattr(runner, "candidate_use_mode", None)
            # --- F2-R1-C §15/§C5: the candidate mode is a TYPED string, never a bare bool, and NEVER defaults
            #     to real. REAL_CANDIDATE routes ONLY through the EXTERNALLY-ROOTED path (module-owned trust
            #     boundary): a caller activation_authority is NEVER trusted, the raw-registry else-branch is
            #     MECHANICALLY UNREACHABLE in REAL_CANDIDATE mode, and there is no downgrade to test mode. The
            #     externally-rooted path fails closed here (the production trust-anchor provider is
            #     unavailable). For TEST_ONLY / INERT_SIMULATION / an ABSENT mode we keep the EXISTING inert
            #     behaviour byte-for-byte (raw path when authority artifacts absent; the existing
            #     activated-handle path when present). ---
            if _candidate_use_mode == "REAL_CANDIDATE":
                # Any missing authority artifact under REAL_CANDIDATE is fatal — no raw fallback, no downgrade.
                if not (_resolver is not None and _authority_root_id and _trust_domain_id and _registry_id):
                    return _reject("F2R1-REAL-CANDIDATE-REQUIRES-EXTERNALLY-VERIFIED-HANDLE")
                handle, ra_reasons = ract.activate_registry_externally_rooted(
                    registry=registry, candidate_mode=_candidate_use_mode, resolver=_resolver,
                    authority_root_id=_authority_root_id, trust_domain_id=_trust_domain_id,
                    registry_id=_registry_id, revocation_set=_revocation_set, now_utc=now_utc,
                    application=APPLICATION,
                )
                if handle is None:
                    return _reject("REGISTRY-ACTIVATION-FAILED:" + ",".join(ra_reasons))
                ai_verdict = ai.validate_active_import_externally_rooted(
                    ai_evidence, anchor_bundle=bundle, activated_handle=handle,
                    candidate_mode=_candidate_use_mode, now_utc=now_utc,
                    phase2_prefix=cc.PHASE2_MODULE_PREFIX, phase2_suffix=cc.PHASE2_MODULE_SUFFIX,
                    required_phase2_count=cc.REQUIRED_PHASE2_MODULE_COUNT, application=APPLICATION,
                )
            elif (_resolver is not None and _activation_authority is not None and _authority_root_id
                    and _trust_domain_id and _registry_id and _candidate_use_mode):
                handle, ra_reasons = ract.activate_registry(
                    registry=registry, resolver=_resolver, activation_authority=_activation_authority,
                    authority_root_id=_authority_root_id, trust_domain_id=_trust_domain_id,
                    registry_id=_registry_id, revocation_set=_revocation_set, now_utc=now_utc,
                    candidate_use_mode=_candidate_use_mode, application=APPLICATION,
                )
                if handle is None:
                    return _reject("REGISTRY-ACTIVATION-FAILED:" + ",".join(ra_reasons))
                ai_verdict = ai.validate_active_import_with_activated_handle(
                    ai_evidence, anchor_bundle=bundle, activated_handle=handle,
                    activation_authority=_activation_authority, now_utc=now_utc,
                    real_candidate_mode=False,
                    phase2_prefix=cc.PHASE2_MODULE_PREFIX, phase2_suffix=cc.PHASE2_MODULE_SUFFIX,
                    required_phase2_count=cc.REQUIRED_PHASE2_MODULE_COUNT, application=APPLICATION,
                )
            else:
                # NOTE: reachable ONLY for non-REAL_CANDIDATE modes (the raw path is mechanically excluded
                # from REAL_CANDIDATE by the branch above).
                ai_verdict = ai.validate_active_import_against_bundle(
                    ai_evidence, anchor_bundle=bundle, producer_registry=registry, now_utc=now_utc,
                    phase2_prefix=cc.PHASE2_MODULE_PREFIX, phase2_suffix=cc.PHASE2_MODULE_SUFFIX,
                    required_phase2_count=cc.REQUIRED_PHASE2_MODULE_COUNT, application=APPLICATION,
                )
            if not ai_verdict.accepted or not ai_verdict.inert:
                return _reject("IMAGE-BOUND-IMPORT-FAILED:" + ",".join(ai_verdict.reason_codes))

    # --- §19 mechanical safety gate (bare-Boolean readiness REMOVED; these are enforced independently of the
    #     evidence chain so a set safety flag ALWAYS forces rejection). ---
    if consumer_live:
        return _reject("R-CONSUMER-LIVE-TRUE")
    if shadow_enabled:
        return _reject("R-SHADOW-ENABLED-TRUE")
    if phase2_activated:
        return _reject("R-PHASE2-ACTIVATED")

    # --- R-1 §6/§7/§8 SOLE readiness path: validated, producer-trusted, SEALED evidence chain. There is NO
    #     bare-Boolean evaluator and NO caller-supplied pass path. ---
    evidence = _build_gate_evidence(
        source_sha=str(source_sha), image_id=image_id, now_utc=now_utc,
        oci_ok=oci.ok, content_ok=ic.ok, sbom_ok=sbom_v.ok, vuln_ok=vuln_v.ok,
        clean_ok=(ctx.manifest.result == "PASS"), effective_ok=eff.ok,
    )
    if evidence is None:
        return _reject("READINESS-EVIDENCE-UNBINDABLE")
    chain_verdict, _chain = _build_trusted_chain(
        evidence, candidate_id=candidate_id, source_sha=str(source_sha), image_id=image_id, now_utc=now_utc,
    )
    if not chain_verdict.ready:
        return _reject("READINESS-EVIDENCE-CHAIN-FAILED:" + ",".join(chain_verdict.reason_codes))

    machine.advance(sm.State.CANDIDATE_READY, now_utc, "CANDIDATE-READY", image_id=image_id)
    # Evidence captured — destroy the immutable context (cleanup-failure fails closed).
    try:
        imm.destroy()
    except imc.ImmutableContextError as exc:
        return _reject(f"IMMUTABLE-CONTEXT-CLEANUP-FAILED:{exc}")
    return _finalise(ready=True, extra_reasons=tuple())


# =============================================================================== §7 canonical preflight
@dataclass
class PreflightReport:
    """Result of the §7 NON-BUILDING canonical preflight. `terminal_state` is USABLE (the exact governed
    pre-build state) or REJECTED. This mode STOPS before any Docker invocation: NO runner is constructed,
    NO real docker build runs, NO image id, NO tag, NO SBOM, NO vuln scan, NO publish, NO deploy."""

    source_sha: str
    terminal_state: str                 # USABLE | REJECTED
    reason_codes: Tuple[str, ...]
    provenance: Optional[Dict[str, object]] = None
    freshness: Optional[Dict[str, object]] = None
    effective_context: Optional[Dict[str, object]] = None
    dispositioned: Tuple[Dict[str, object], ...] = field(default_factory=tuple)
    planned_local_target: Optional[str] = None
    context_dir: Optional[str] = None
    # mechanical no-build proof — all constant for this mode.
    runner_constructed: bool = False
    docker_invoked: bool = False
    image_id: Optional[str] = None
    tag: Optional[str] = None
    sbom: Optional[object] = None
    vuln_scan: Optional[object] = None
    published: bool = False
    deployed: bool = False

    @property
    def usable(self) -> bool:
        return self.terminal_state == "USABLE"

    def to_dict(self) -> Dict[str, object]:
        return {
            "contract_version": CONTRACT_VERSION,
            "tool_version": TOOL_VERSION,
            "application": APPLICATION,
            "mode": "CANONICAL_PREFLIGHT_NON_BUILDING",
            "source_sha": self.source_sha,
            "terminal_state": self.terminal_state,
            "reason_codes": list(self.reason_codes),
            "provenance": self.provenance,
            "freshness": self.freshness,
            "effective_context": self.effective_context,
            "dispositioned": list(self.dispositioned),
            "planned_local_target": self.planned_local_target,
            "context_dir": self.context_dir,
            "runner_constructed": self.runner_constructed,
            "docker_invoked": self.docker_invoked,
            "image_id": self.image_id,
            "tag": self.tag,
            "sbom": self.sbom,
            "vuln_scan": self.vuln_scan,
            "published": self.published,
            "deployed": self.deployed,
        }


def run_canonical_preflight(
    *,
    source_sha: str,
    repo_dir: Path,
    quarantine_dir: Path,
    now_utc: str,
    build_utc: str,
    candidate_name: str,
    dispositions: Optional[Sequence["cbc.SecretDisposition"]] = None,
    expected_remote: str = DEFAULT_EXPECTED_REMOTE,
    allowed_refs: Sequence[str] = ALLOWED_CANONICAL_REFS,
    authorised_pr_heads: Optional[Mapping[str, str]] = None,
    authorised_sha: Optional[str] = None,
    fetcher: Optional[RemoteRefFetcher] = None,
    canonical_state_record: Optional[Mapping[str, str]] = None,
    immutable_source_binding: Optional[Mapping[str, str]] = None,
) -> PreflightReport:
    """§7. Run the governed pre-build sequence against an EXACT canonical SHA and STOP before Docker:
    trusted-source verify -> trusted-ref freshness -> clean-context export -> manifest validate -> effective
    context validate -> prohibited-path scan -> secret scan + governed dispositions -> required build-input
    validation -> final TOCTOU revalidation. Reaching USABLE proves the exact canonical repo is a valid
    governed pre-build state WITHOUT building, tagging, SBOM-ing, scanning, publishing or deploying anything.
    NO DockerRunner is ever constructed here."""
    def _reject(code: str, **extra) -> PreflightReport:
        return PreflightReport(source_sha=str(source_sha), terminal_state="REJECTED",
                               reason_codes=(code,), **extra)

    # 1. trusted provenance (reachable-from-canonical / authorised PR head).
    try:
        prov = verify_trusted_provenance(
            source_sha, repo_dir, expected_remote=expected_remote, allowed_refs=allowed_refs,
            authorised_pr_heads=authorised_pr_heads,
        )
    except GovernedBuildError as exc:
        return _reject(f"PROVENANCE-REJECTED:{exc}")

    # 2. trusted-ref freshness (§8) — mandatory for a canonical-main build.
    try:
        fr = verify_trusted_ref_freshness(
            source_sha, repo_dir, authorised_sha=authorised_sha or source_sha,
            expected_remote=expected_remote, fetcher=fetcher,
            canonical_state_record=canonical_state_record,
            immutable_source_binding=immutable_source_binding,
        )
    except GovernedBuildError as exc:
        return _reject(f"FRESHNESS-REJECTED:{exc}", provenance=prov.to_dict())
    if not fr.fresh:
        return PreflightReport(source_sha=str(source_sha), terminal_state="REJECTED",
                               reason_codes=("FRESHNESS-NOT-FRESH",) + fr.reason_codes,
                               provenance=prov.to_dict(), freshness=fr.to_dict())

    # 3-6. governed clean context (export + manifest validate + prohibited + secret + governed dispositions).
    ctx = prepare_governed_context(
        source_sha, repo_dir, quarantine_dir, expected_remote=expected_remote, now_utc=now_utc,
        dispositions=dispositions,
    )
    if not ctx.usable:
        return _reject(f"CONTEXT-UNUSABLE:{ctx.reason_code}", provenance=prov.to_dict(),
                       freshness=fr.to_dict())
    try:
        assert_context_is_governed(ctx, source_sha)
    except GovernedBuildError as exc:
        return _reject(f"CONTEXT-NOT-GOVERNED:{exc}", provenance=prov.to_dict(), freshness=fr.to_dict())

    # 6. effective-context validation (Docker-faithful matcher; §10 unsupported-syntax fails closed).
    tracked = _tracked_names(source_sha, repo_dir)
    dockerignore_text = (repo_dir / ".dockerignore").read_text(encoding="utf-8")
    eff = verify_effective_context(tracked, dockerignore_text)
    if not eff.ok:
        return _reject("EFFECTIVE-CONTEXT-INVALID:" + ",".join(eff.reason_codes),
                       provenance=prov.to_dict(), freshness=fr.to_dict(),
                       effective_context=eff.to_dict())

    # 7. required build-input validation + command construction (NOT executed).
    try:
        inputs = build_inputs(
            source_sha=source_sha, build_utc=build_utc, expected_repository=expected_remote,
            candidate_name=candidate_name, context_dir=ctx.context_dir,
            context_source_sha=ctx.manifest.source_sha,
        )
        command = build_docker_command(inputs)          # constructed only; the runner is NEVER invoked here
        assert_no_prohibited_docker_args(command)
    except GovernedBuildError as exc:
        return _reject(f"BUILD-INPUTS-INVALID:{exc}", provenance=prov.to_dict(), freshness=fr.to_dict(),
                       effective_context=eff.to_dict())

    # 8. final TOCTOU revalidation at the exact governed pre-build boundary.
    snapshot = finalise_context_snapshot(ctx)
    toctou = revalidate_context_snapshot(ctx, snapshot)
    if toctou is not None:
        return _reject(f"TOCTOU-CONTEXT-MUTATED:{toctou}", provenance=prov.to_dict(),
                       freshness=fr.to_dict(), effective_context=eff.to_dict())

    # USABLE — the exact governed pre-build state. STOP: no runner constructed, no docker, no image, no tag,
    # no SBOM, no vuln scan, no publish, no deploy.
    return PreflightReport(
        source_sha=str(source_sha), terminal_state="USABLE", reason_codes=tuple(),
        provenance=prov.to_dict(), freshness=fr.to_dict(), effective_context=eff.to_dict(),
        dispositioned=tuple(d.to_dict() for d in ctx.manifest.dispositioned),
        planned_local_target=inputs.local_image_target, context_dir=str(ctx.context_dir),
        runner_constructed=False, docker_invoked=False, image_id=None, tag=None, sbom=None, vuln_scan=None,
        published=False, deployed=False,
    )
