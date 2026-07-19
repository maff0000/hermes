"""FW-08 governed Stage-B candidate-image build enforcement tests.

WO-HELM-HERMES-FW08-GOVERNED-STAGE-B-IMAGE-BUILD-ENFORCEMENT-IMPLEMENTATION-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-19. Contract version: 1.

These tests build NO real image, run NO real docker/SBOM/scan, publish NOTHING, deploy NOTHING, wire NO
runtime, enable NO shadow, and mutate NO live state. They prove the governed wrapper
(tools/hermes_stage_b_build_v1.py) makes a FUTURE Stage-B build mechanically governed + fail-closed:
  * §7 trusted canonical provenance (accept reachable-from-origin/main + authorised PR head; reject
    orphan / foreign / spoofed-remote / replaced-object / abbreviated / branch / HEAD);
  * §8/§9 clean context MANDATORY + quarantine (arbitrary/stale/modified/failed context rejected,
    prohibited members never become a usable context, failed context destroyed);
  * §10/F-113-03 Docker-FAITHFUL effective-context enumeration + parity fixtures (**/rooted/dir/negation),
    and the homemade PR#113 matcher is NOT the readiness proof;
  * §11/§12 mandatory inputs, no env fallback, FAKE RUNNER ONLY, real docker NEVER invoked, exact command
    array, no shell, prohibited docker args + no-push/deploy rejected;
  * §13/F-113-02 OCI post-build inspection (expected-source-SHA never optional, mismatch/empty rejected);
  * §14 image-content, §15 SBOM (mandatory), §16 vuln-scan (mandatory) fixture contracts;
  * §18 candidate-readiness state machine (no skip, failure->rejected, rejected permanence, no publish/
    deploy state); §19 pure evaluator (all-green required, any failure/consumer_live/shadow rejects);
  * §20 no-publish/no-deploy static + behavioural guards; §21 config/Phase-2 safety.

Run: cd <worktree> && python3 -m pytest tests/test_fw08_governed_build_v1.py -q
"""
from __future__ import annotations

import ast
import copy
import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import jsonschema
import pytest

import tools.hermes_clean_build_context_v1 as cbc
import tools.hermes_image_label_verify_v1 as lbl
import tools.hermes_stage_b_build_v1 as w
import design.hermes_fw08_dockerignore_matcher_v1 as di
import design.hermes_fw08_candidate_state_v1 as sm
import design.hermes_fw08_readiness_evaluator_v1 as ev
import design.hermes_fw08_context_contract_v1 as cc

REPO = Path(__file__).resolve().parents[1]
CANONICAL_BASE = "fe2037c5b4b4836d1038b4c0f734426ac9b3fa02"
EXPECTED_REMOTE = "git@github.com:maff0000/hermes.git"
SCHEMAS = REPO / "schemas" / "deployment_readiness"
NOW = "2026-07-19T00:00:00+00:00"
BUILD_UTC = "2026-07-19T12:00:00+00:00"
IMAGE_ID = "sha256:" + "a" * 64


# =============================================================================== git fixtures
def _git(args, cwd, env=None):
    e = dict(os.environ)
    e.update({
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(cwd),
    })
    if env:
        e.update(env)
    return subprocess.run(["git", *args], cwd=str(cwd), env=e, capture_output=True, text=True, check=True)


_SSS_MODULES = tuple(f"utils/hermes_sss_{n}_v1.py" for n in
                     ("comparator", "config", "evidence_adapters", "evidence_snapshot", "jsonl_writer",
                      "mapper", "observer", "redaction", "shadow_adapter", "shadow_record"))

_GOVERNED_DOCKERIGNORE = (
    ".git\n**/.git\ntests/\n*.jsonl\n**/*.jsonl\n.env\n.env.*\n!.env.example\nDockerfile\n.dockerignore\n"
)

_GOVERNED_FILES = tuple(list(w.REQUIRED_EFFECTIVE_INCLUSIONS) + list(_SSS_MODULES))


def _make_governed_repo(root: Path, *, extra_files=None, dockerignore=None, set_origin_main=True):
    """A self-contained git repo carrying every required Stage-B file, a clean effective context, an
    origin remote + origin/main ref, so the wrapper's full governed path can pass."""
    _git(["init", "-q"], root)
    _git(["remote", "add", "origin", EXPECTED_REMOTE], root)
    for rel in _GOVERNED_FILES:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# stub {rel}\npass\n", encoding="utf-8")
    (root / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    (root / ".dockerignore").write_text(dockerignore or _GOVERNED_DOCKERIGNORE, encoding="utf-8")
    for rel, content in (extra_files or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    add = list(_GOVERNED_FILES) + ["Dockerfile", ".dockerignore"] + list((extra_files or {}).keys())
    _git(["add", *add], root)
    _git(["commit", "-q", "-m", "governed"], root)
    sha = _git(["rev-parse", "HEAD"], root).stdout.strip()
    if set_origin_main:
        _git(["update-ref", "refs/remotes/origin/main", sha], root)
    return sha


@pytest.fixture()
def governed_repo(tmp_path):
    root = tmp_path / "gov"
    root.mkdir()
    sha = _make_governed_repo(root)
    return root, sha


# =============================================================================== fake runner
def _sbom(sha, image_id=IMAGE_ID, build_utc=BUILD_UTC, **over):
    s = {"tool": "syft", "format": "spdx-json", "image_id": image_id, "source_sha": sha,
         "build_utc": build_utc, "packages": [{"name": "fastapi"}, {"name": "redis"}]}
    s.update(over)
    if "checksum" not in over:
        s["checksum"] = w._sbom_checksum(s)
    return s


def _scan(image_id=IMAGE_ID, **over):
    s = {"scanner": "grype", "image_id": image_id, "sbom_ref": "sbom-1",
         "db_timestamp_utc": "2026-07-18T00:00:00+00:00",
         "findings": [{"id": "CVE-1", "severity": "LOW"}], "allowlist": []}
    s.update(over)
    if "checksum" not in over:
        s["checksum"] = w._vuln_checksum(s)
    return s


def _content(image_id=IMAGE_ID, **over):
    files = list(w.REQUIRED_EFFECTIVE_INCLUSIONS) + list(_SSS_MODULES)
    c = {"image_id": image_id, "entrypoint": list(w.EXPECTED_ENTRYPOINT), "cmd": list(w.EXPECTED_CMD),
         "user": "hermes", "workdir": "/app", "healthcheck": True, "exposed_ports": ["8210/tcp"],
         "files": files, "phase2_actively_imported_by": []}
    c.update(over)
    return c


def _labels(sha, build_utc=BUILD_UTC):
    return [{"Config": {"Labels": {lbl.LABEL_REVISION: sha, lbl.LABEL_CREATED: build_utc}}}]


class FakeRunner(w.DockerRunner):
    """Injected fake runner. NEVER touches docker; returns fixture payloads. Any per-payload override
    lets a test force a single-gate failure."""

    def __init__(self, sha, *, inspect=None, content=None, sbom=None, scan=None, image_id=IMAGE_ID,
                 build_ok=True):
        self.sha = sha
        self.image_id = image_id
        self.build_ok = build_ok
        self._inspect = inspect if inspect is not None else _labels(sha)
        self._content = content if content is not None else _content(image_id)
        self._sbom = sbom if sbom is not None else _sbom(sha, image_id)
        self._scan = scan if scan is not None else _scan(image_id)
        self.calls = []

    def build(self, command, *, timeout=0):
        self.calls.append(("build", tuple(command)))
        assert command[0] == "docker" and command[1] == "build"
        if not self.build_ok:
            raise w.GovernedBuildError("fake build failure")
        tag = command[command.index("--tag") + 1]
        return w.BuildOutcome(image_id=self.image_id, local_target=tag, returncode=0)

    def inspect(self, image_id):
        self.calls.append(("inspect", image_id))
        return self._inspect

    def image_content(self, image_id):
        return self._content

    def sbom(self, image_id, *, source_sha, build_utc):
        return self._sbom

    def vuln_scan(self, image_id, *, sbom):
        return self._scan


def _run(root, sha, runner, **over):
    kw = dict(source_sha=sha, build_utc=BUILD_UTC, candidate_name="fw08cand", repo_dir=root,
              quarantine_dir=root / "_q", now_utc=NOW, runner=runner)
    kw.update(over)
    return w.run_stage_b_candidate_build(**kw)


# =============================================================================== §7 trusted provenance
def test_provenance_accepts_reachable_from_origin_main(governed_repo):
    root, sha = governed_repo
    prov = w.verify_trusted_provenance(sha, root, expected_remote=EXPECTED_REMOTE)
    assert prov.mechanism == "REACHABLE_FROM_CANONICAL_REF"
    assert prov.canonical_ref == "refs/remotes/origin/main"


def test_provenance_accepts_authorised_pr_head_with_binding(tmp_path):
    root = tmp_path / "prhead"
    root.mkdir()
    # origin/main points at an EARLIER commit; the build SHA is a later head NOT on main.
    base = _make_governed_repo(root)
    (root / "extra.py").write_text("x=1\n", encoding="utf-8")
    _git(["add", "extra.py"], root)
    _git(["commit", "-q", "-m", "pr head"], root)
    head = _git(["rev-parse", "HEAD"], root).stdout.strip()
    _git(["update-ref", "refs/remotes/origin/main", base], root)  # main stays at base
    with pytest.raises(w.GovernedBuildError):
        w.verify_trusted_provenance(head, root, expected_remote=EXPECTED_REMOTE)
    prov = w.verify_trusted_provenance(
        head, root, expected_remote=EXPECTED_REMOTE,
        authorised_pr_heads={head: "AUDIT-BIND-PR-999-r2d2-signed"},
    )
    assert prov.mechanism == "AUTHORISED_PR_HEAD"
    assert prov.audit_binding == "AUDIT-BIND-PR-999-r2d2-signed"


def test_provenance_rejects_orphan_commit(tmp_path):
    root = tmp_path / "orphan"
    root.mkdir()
    _make_governed_repo(root)
    _git(["checkout", "-q", "--orphan", "floating"], root)
    _git(["commit", "-q", "--allow-empty", "-m", "orphan"], root)
    orphan = _git(["rev-parse", "HEAD"], root).stdout.strip()
    with pytest.raises(w.GovernedBuildError):
        w.verify_trusted_provenance(orphan, root, expected_remote=EXPECTED_REMOTE)


def test_provenance_rejects_foreign_repo_with_spoofed_remote(tmp_path):
    """A foreign repo whose remote.origin.url is spoofed to the expected string still fails: the SHA is
    not reachable from the (foreign) origin/main."""
    root = tmp_path / "foreign"
    root.mkdir()
    _git(["init", "-q"], root)
    _git(["remote", "add", "origin", EXPECTED_REMOTE], root)  # spoofed identity
    (root / "a.py").write_text("a=1\n", encoding="utf-8")
    _git(["add", "a.py"], root)
    _git(["commit", "-q", "-m", "foreign main"], root)
    main_sha = _git(["rev-parse", "HEAD"], root).stdout.strip()
    _git(["update-ref", "refs/remotes/origin/main", main_sha], root)
    # A different (foreign) commit not reachable from that main.
    _git(["checkout", "-q", "--orphan", "foreign2"], root)
    _git(["commit", "-q", "--allow-empty", "-m", "foreign2"], root)
    foreign = _git(["rev-parse", "HEAD"], root).stdout.strip()
    with pytest.raises(w.GovernedBuildError):
        w.verify_trusted_provenance(foreign, root, expected_remote=EXPECTED_REMOTE)


def test_provenance_rejects_replaced_object_injection(tmp_path):
    """git-replace lets an attacker swap object content; --no-replace-objects means a replaced commit is
    still judged by its TRUE reachability. A replacement that is not truly reachable is rejected."""
    root = tmp_path / "replace"
    root.mkdir()
    base = _make_governed_repo(root)
    _git(["checkout", "-q", "--orphan", "evil"], root)
    _git(["commit", "-q", "--allow-empty", "-m", "evil"], root)
    evil = _git(["rev-parse", "HEAD"], root).stdout.strip()
    _git(["replace", base, evil], root)  # pretend base == evil
    # With --no-replace-objects the wrapper still refuses `evil` (not reachable from real origin/main).
    with pytest.raises(w.GovernedBuildError):
        w.verify_trusted_provenance(evil, root, expected_remote=EXPECTED_REMOTE)


def test_provenance_rejects_abbreviated_and_symbolic(governed_repo):
    root, sha = governed_repo
    for bad in (sha[:12], "main", "HEAD", "latest", "refs/heads/main"):
        with pytest.raises(w.GovernedBuildError):
            w.verify_trusted_provenance(bad, root, expected_remote=EXPECTED_REMOTE)


def test_provenance_rejects_wrong_remote(governed_repo):
    root, sha = governed_repo
    with pytest.raises(w.GovernedBuildError):
        w.verify_trusted_provenance(sha, root, expected_remote="git@github.com:someone/else.git")


def test_provenance_rejects_when_no_canonical_ref_present(tmp_path):
    root = tmp_path / "noref"
    root.mkdir()
    sha = _make_governed_repo(root, set_origin_main=False)
    with pytest.raises(w.GovernedBuildError):
        w.verify_trusted_provenance(sha, root, expected_remote=EXPECTED_REMOTE)


def test_provenance_real_repo_base_reachable():
    prov = w.verify_trusted_provenance(CANONICAL_BASE, REPO, expected_remote=EXPECTED_REMOTE)
    assert prov.mechanism == "REACHABLE_FROM_CANONICAL_REF"


# =============================================================================== §8/§9 context + quarantine
def test_clean_context_is_mandatory_and_usable(governed_repo):
    root, sha = governed_repo
    ctx = w.prepare_governed_context(sha, root, root / "_q1", expected_remote=EXPECTED_REMOTE, now_utc=NOW)
    assert ctx.usable and ctx.manifest.result == "PASS"
    w.assert_context_is_governed(ctx, sha)  # does not raise
    assert (ctx.context_dir / w._STATUS_FILE).exists()


def test_arbitrary_or_handcreated_context_rejected(tmp_path):
    hand = tmp_path / "hand"
    hand.mkdir()
    (hand / "main.py").write_text("x\n", encoding="utf-8")
    bogus = w.GovernedContext(context_dir=hand, manifest=None, effective_included=(),
                              effective_checksum="", status="USABLE")
    with pytest.raises(w.GovernedBuildError):
        w.assert_context_is_governed(bogus, "0" * 40)


def test_stale_context_source_sha_mismatch_rejected(governed_repo):
    root, sha = governed_repo
    ctx = w.prepare_governed_context(sha, root, root / "_q2", expected_remote=EXPECTED_REMOTE, now_utc=NOW)
    with pytest.raises(w.GovernedBuildError):
        w.assert_context_is_governed(ctx, "b" * 40)  # different SHA => stale


def test_context_missing_status_marker_rejected(governed_repo):
    root, sha = governed_repo
    ctx = w.prepare_governed_context(sha, root, root / "_q3", expected_remote=EXPECTED_REMOTE, now_utc=NOW)
    (ctx.context_dir / w._STATUS_FILE).unlink()
    with pytest.raises(w.GovernedBuildError):
        w.assert_context_is_governed(ctx, sha)


def test_context_modified_after_manifest_detected(governed_repo):
    root, sha = governed_repo
    ctx = w.prepare_governed_context(sha, root, root / "_q4", expected_remote=EXPECTED_REMOTE, now_utc=NOW)
    assert w._files_modified_since_manifest(ctx.context_dir, ctx.manifest) is None
    (ctx.context_dir / "main.py").write_text("TAMPERED\n", encoding="utf-8")
    assert w._files_modified_since_manifest(ctx.context_dir, ctx.manifest) == "main.py"


def test_prohibited_member_never_becomes_usable_and_quarantine_destroyed(tmp_path):
    """A tracked .jsonl that survives a permissive .dockerignore is rejected PRE-MATERIALISATION; the
    quarantine is stamped UNUSABLE and no exported content is left behind."""
    root = tmp_path / "bad"
    root.mkdir()
    sha = _make_governed_repo(
        root, dockerignore=".git\n**/.git\ntests/\n",  # does NOT exclude *.jsonl
        extra_files={"leak.jsonl": "{}\n"},
    )
    ctx = w.prepare_governed_context(sha, root, root / "_q", expected_remote=EXPECTED_REMOTE, now_utc=NOW)
    assert not ctx.usable
    assert ctx.reason_code.startswith("PRE-MATERIALISATION-PROHIBITED")
    # Only the UNUSABLE status marker remains — no materialised leak.jsonl, no success manifest.
    remaining = [p.name for p in ctx.context_dir.iterdir()]
    assert remaining == [w._STATUS_FILE]
    assert not (ctx.context_dir / "leak.jsonl").exists()
    status = json.loads((ctx.context_dir / w._STATUS_FILE).read_text())
    assert status["status"] == "UNUSABLE"


def test_wrapper_refuses_failed_context(tmp_path):
    root = tmp_path / "bad2"
    root.mkdir()
    sha = _make_governed_repo(root, dockerignore=".git\n**/.git\n", extra_files={"leak.jsonl": "{}\n"})
    rep = _run(root, sha, FakeRunner(sha))
    assert rep.ready is False and rep.state == "REJECTED"
    assert any("CONTEXT-UNUSABLE" in r for r in rep.reason_codes)


def test_canonical_base_selfscan_fails_closed(tmp_path):
    """The PR#113 secret heuristic self-matches its own 'PuTTY-User-Key-File' literal at the canonical
    base, so the clean-context result is FAIL — the wrapper correctly refuses (documented F-113-04 limit).
    The manifest-result gate fires first, so the reason code is CONTEXT-MANIFEST-RESULT-NOT-PASS."""
    ctx = w.prepare_governed_context(CANONICAL_BASE, REPO, tmp_path / "_selfscan_q",
                                     expected_remote=EXPECTED_REMOTE, now_utc=NOW)
    assert not ctx.usable
    assert ctx.reason_code == "CONTEXT-MANIFEST-RESULT-NOT-PASS"


# =============================================================================== §10 docker-faithful matcher
def test_dockerignore_parity_fixtures_all_pass():
    results = di.run_parity_cases()
    assert results, "parity fixtures must exist"
    for case, actual in results:
        assert actual == case.excluded, f"parity mismatch: {case.label} expected {case.excluded} got {actual}"
    # coverage: the four required classes are all represented.
    labels = " ".join(c.label for c in di.PARITY_CASES)
    assert "doublestar" in labels and "rooted" in labels and "dir-" in labels and "negation" in labels


def test_effective_context_real_repo_inclusions_and_exclusions():
    tracked = cbc.tracked_paths(CANONICAL_BASE, REPO)
    dtext = (REPO / ".dockerignore").read_text()
    verdict = w.verify_effective_context(tracked, dtext)
    assert verdict.ok, verdict.reason_codes
    assert verdict.phase2_module_count >= 10
    included = set(di.effective_context(tracked, dtext))
    for req in w.REQUIRED_EFFECTIVE_INCLUSIONS:
        assert req in included, req
    for bad in ("Dockerfile", ".dockerignore"):
        assert bad not in included
    assert not any(p.startswith("tests/") for p in included)
    assert not any(p.startswith("ops/evidence/") for p in included)
    assert not any(p.endswith(".jsonl") for p in included)


def test_homemade_matcher_is_not_the_readiness_proof():
    """The wrapper's effective-context proof uses the parity-validated matcher (design.*matcher), NOT the
    homemade PR#113 matcher (cbc.apply_dockerignore)."""
    src = Path(w.__file__).read_text()
    assert "di.effective_context" in src
    assert "cbc.apply_dockerignore" not in src
    # And the two matchers actually diverge on a case the homemade one gets wrong is not required; but the
    # new matcher must be Docker-faithful on single-star non-recursion (a class R2D2 flagged).
    pats = di.parse_dockerignore("*.jsonl\n")
    assert di.path_excluded("a/b/c.jsonl", pats) is False   # single-star is NOT recursive
    assert di.path_excluded("top.jsonl", pats) is True


def test_effective_context_missing_required_inclusion_fails():
    tracked = [p for p in cbc.tracked_paths(CANONICAL_BASE, REPO) if p != "main.py"]
    dtext = (REPO / ".dockerignore").read_text()
    verdict = w.verify_effective_context(tracked, dtext)
    assert not verdict.ok
    assert "MISSING-REQUIRED-INCLUSIONS" in verdict.reason_codes


# =============================================================================== §11/§12 inputs + command
def test_mandatory_inputs_no_env_fallback(monkeypatch, governed_repo):
    root, sha = governed_repo
    monkeypatch.setenv("SOURCE_SHA", sha)
    monkeypatch.setenv("BUILD_UTC", BUILD_UTC)
    # naive / local build_utc rejected — no env fallback invents it.
    for bad in ("2026-07-19T12:00:00", "2026-07-19T12:00:00+02:00", "", "not-a-date"):
        with pytest.raises(w.GovernedBuildError):
            w.validate_build_utc(bad)
    for bad in ("has/slash", "has:colon", "AA", "x y", "A" * 65):
        with pytest.raises(w.GovernedBuildError):
            w.validate_candidate_name(bad)
    assert "os.environ" not in Path(w.__file__).read_text().replace("os.environ.get(_REAL_DOCKER_ENV_GATE)", "")


def test_exact_docker_command_array_and_no_shell(governed_repo):
    root, sha = governed_repo
    ctx = w.prepare_governed_context(sha, root, root / "_qc", expected_remote=EXPECTED_REMOTE, now_utc=NOW)
    inp = w.build_inputs(source_sha=sha, build_utc=BUILD_UTC, expected_repository=EXPECTED_REMOTE,
                         candidate_name="fw08cand", context_dir=ctx.context_dir,
                         context_source_sha=ctx.manifest.source_sha)
    cmd = w.build_docker_command(inp)
    assert isinstance(cmd, tuple)
    assert cmd[0] == "docker" and cmd[1] == "build"
    assert "--file" in cmd and str(ctx.context_dir / "Dockerfile") in cmd
    assert f"SOURCE_SHA={sha}" in cmd and f"BUILD_UTC={BUILD_UTC}" in cmd
    assert cmd[-1] == str(ctx.context_dir)
    assert inp.local_image_target.startswith("hermes-fw08-candidate-") and "/" not in inp.local_image_target
    # no shell anywhere in the module
    s = Path(w.__file__).read_text()
    for forbidden in ("shell=True", "os.system", "os.popen", "subprocess.getoutput", "eval(", "exec(",
                      "pickle"):
        assert forbidden not in s, forbidden


def test_prohibited_docker_args_rejected(governed_repo):
    root, sha = governed_repo
    base = ("docker", "build", "--file", "Dockerfile", "--tag", "hermes-fw08-candidate-x:local-1", ".")
    for extra in (("--push",), ("--network", "host"), ("--privileged",),
                  ("--tag", "registry.example.com/hermes:latest"), ("--output", "type=registry"),
                  ("--secret", "id=x")):
        with pytest.raises(w.GovernedBuildError):
            w.assert_no_prohibited_docker_args(base + extra)
    # the wrapper-built command is clean
    ctx = w.prepare_governed_context(sha, root, root / "_qp", expected_remote=EXPECTED_REMOTE, now_utc=NOW)
    inp = w.build_inputs(source_sha=sha, build_utc=BUILD_UTC, expected_repository=EXPECTED_REMOTE,
                         candidate_name="fw08cand", context_dir=ctx.context_dir,
                         context_source_sha=ctx.manifest.source_sha)
    w.assert_no_prohibited_docker_args(w.build_docker_command(inp))  # does not raise


def test_build_inputs_context_sha_must_equal_input():
    with pytest.raises(w.GovernedBuildError):
        w.build_inputs(source_sha="a" * 40, build_utc=BUILD_UTC, expected_repository=EXPECTED_REMOTE,
                       candidate_name="fw08cand", context_dir=Path("/tmp"), context_source_sha="b" * 40)


# =============================================================================== §12 real-docker non-invocation
def test_default_runner_refuses_build():
    with pytest.raises(w.RealDockerInvocationForbidden):
        w.RefusingDockerRunner().build(("docker", "build", "."))
    # wrapper with NO runner uses the refusing default and rejects.
    with pytest.raises(w.RealDockerInvocationForbidden):
        w.RefusingDockerRunner().inspect("img")


def test_real_runner_refuses_without_double_gate(monkeypatch):
    called = {"n": 0}
    real_run = subprocess.run

    def guard(cmd, *a, **k):
        if cmd and "docker" in str(cmd[0]):
            called["n"] += 1
            raise AssertionError("real docker must NOT be invoked")
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", guard)
    # no flag, no env
    with pytest.raises(w.RealDockerInvocationForbidden):
        w.RealDockerRunner().build(("docker", "build", "."))
    # flag set but env missing -> still refuses BEFORE any subprocess
    with pytest.raises(w.RealDockerInvocationForbidden):
        w.RealDockerRunner(enable_real_execution=True).build(("docker", "build", "."))
    assert called["n"] == 0


def test_full_flow_never_invokes_real_docker(monkeypatch, governed_repo):
    root, sha = governed_repo
    real_run, real_popen = subprocess.run, subprocess.Popen

    def guard_run(cmd, *a, **k):
        assert not (cmd and "docker" in str(cmd[0])), "no real docker run"
        return real_run(cmd, *a, **k)

    def guard_popen(cmd, *a, **k):
        assert not (cmd and "docker" in str(cmd[0])), "no real docker popen"
        return real_popen(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", guard_run)
    monkeypatch.setattr(subprocess, "Popen", guard_popen)
    runner = FakeRunner(sha)
    rep = _run(root, sha, runner)
    assert rep.ready is True and rep.state == "CANDIDATE_READY"
    assert runner.calls[0][0] == "build"  # fake runner did the build, not docker


# =============================================================================== §13 OCI post-build
def test_oci_expected_source_sha_never_optional(governed_repo):
    root, sha = governed_repo
    with pytest.raises(w.GovernedBuildError):
        w.verify_oci_postbuild(_labels(sha), expected_source_sha="", expected_build_utc=BUILD_UTC,
                               image_id=IMAGE_ID)


def test_oci_postbuild_accepts_good_labels(governed_repo):
    root, sha = governed_repo
    v = w.verify_oci_postbuild(_labels(sha), expected_source_sha=sha, expected_build_utc=BUILD_UTC,
                               image_id=IMAGE_ID)
    assert v.ok and v.source_sha == sha


def test_oci_postbuild_rejects_empty_wrong_and_missing(governed_repo):
    root, sha = governed_repo
    # direct-build empty labels (no provenance ARGs) -> fail
    empty = [{"Config": {"Labels": {}}}]
    assert not w.verify_oci_postbuild(empty, expected_source_sha=sha, expected_build_utc=BUILD_UTC,
                                      image_id=IMAGE_ID).ok
    # wrong revision
    wrong = [{"Config": {"Labels": {lbl.LABEL_REVISION: "b" * 40, lbl.LABEL_CREATED: BUILD_UTC}}}]
    assert not w.verify_oci_postbuild(wrong, expected_source_sha=sha, expected_build_utc=BUILD_UTC,
                                      image_id=IMAGE_ID).ok
    # created != build_utc
    mism = [{"Config": {"Labels": {lbl.LABEL_REVISION: sha, lbl.LABEL_CREATED: "2020-01-01T00:00:00+00:00"}}}]
    assert "CREATED-NOT-EQUAL-BUILD-UTC" in w.verify_oci_postbuild(
        mism, expected_source_sha=sha, expected_build_utc=BUILD_UTC, image_id=IMAGE_ID).reason_codes
    # inspection unavailable / image-id unavailable
    assert not w.verify_oci_postbuild(None, expected_source_sha=sha, expected_build_utc=BUILD_UTC,
                                      image_id=IMAGE_ID).ok
    assert not w.verify_oci_postbuild(_labels(sha), expected_source_sha=sha, expected_build_utc=BUILD_UTC,
                                      image_id=None).ok


def test_full_flow_rejects_on_label_mismatch(governed_repo):
    root, sha = governed_repo
    bad_inspect = [{"Config": {"Labels": {lbl.LABEL_REVISION: "c" * 40, lbl.LABEL_CREATED: BUILD_UTC}}}]
    rep = _run(root, sha, FakeRunner(sha, inspect=bad_inspect))
    assert rep.ready is False and rep.state == "REJECTED"
    assert "OCI-POSTBUILD-FAILED" in rep.reason_codes


# =============================================================================== §14 image content
def test_image_content_good(governed_repo):
    v = w.verify_image_content(_content(), image_id=IMAGE_ID)
    assert v.ok, v.reason_codes


@pytest.mark.parametrize("mut,reason", [
    ({"entrypoint": ["/bin/sh"]}, "ENTRYPOINT-MISMATCH"),
    ({"cmd": ["python", "evil.py"]}, "CMD-MISMATCH"),
    ({"user": "root"}, "USER-MISMATCH"),
    ({"files": list(w.REQUIRED_EFFECTIVE_INCLUSIONS)[1:] + list(_SSS_MODULES)}, "REQUIRED-FILE-MISSING"),
    ({"phase2_actively_imported_by": ["main.py"]}, "ACTIVE-PHASE2-IMPORT-DETECTED"),
])
def test_image_content_failures(mut, reason):
    v = w.verify_image_content(_content(**mut), image_id=IMAGE_ID)
    assert not v.ok and reason in v.reason_codes


def test_image_content_prohibited_file_present():
    c = _content()
    c["files"] = c["files"] + ["tests/test_x.py", "secret.pem", "notes.jsonl", ".claude/settings.json"]
    v = w.verify_image_content(c, image_id=IMAGE_ID)
    assert not v.ok and "PROHIBITED-FILE-PRESENT" in v.reason_codes


def test_image_content_unavailable():
    assert not w.verify_image_content(None, image_id=IMAGE_ID).ok


# =============================================================================== §15 SBOM
def test_sbom_good(governed_repo):
    root, sha = governed_repo
    assert w.verify_sbom(_sbom(sha), image_id=IMAGE_ID, source_sha=sha).ok


def test_sbom_failures(governed_repo):
    root, sha = governed_repo
    assert not w.verify_sbom(None, image_id=IMAGE_ID, source_sha=sha).ok            # missing
    assert not w.verify_sbom(_sbom(sha, image_id="sha256:" + "z" * 64), image_id=IMAGE_ID,
                             source_sha=sha).ok                                      # id mismatch
    bad = _sbom(sha); bad["checksum"] = "deadbeef"
    assert not w.verify_sbom(bad, image_id=IMAGE_ID, source_sha=sha).ok             # checksum fail
    assert not w.verify_sbom(_sbom(sha, tool="homegrown"), image_id=IMAGE_ID, source_sha=sha).ok  # tool


def test_full_flow_rejects_on_sbom_tool_failure(governed_repo):
    root, sha = governed_repo

    class Boom(FakeRunner):
        def sbom(self, image_id, *, source_sha, build_utc):
            raise w.GovernedBuildError("sbom tool crashed")

    rep = _run(root, sha, Boom(sha))
    assert rep.ready is False and "SBOM-FAILED" in rep.reason_codes


# =============================================================================== §16 vuln scan
def test_vuln_good():
    assert w.verify_vuln_scan(_scan(), image_id=IMAGE_ID, now_utc=NOW).ok


def test_vuln_failures():
    assert not w.verify_vuln_scan(None, image_id=IMAGE_ID, now_utc=NOW).ok            # scanner unavailable
    assert not w.verify_vuln_scan(_scan(image_id="sha256:" + "z" * 64), image_id=IMAGE_ID,
                                  now_utc=NOW).ok                                     # image mismatch
    crit = _scan(findings=[{"id": "CVE-9", "severity": "CRITICAL"}])
    assert "VULN-UNGOVERNED-CRITICAL" in w.verify_vuln_scan(crit, image_id=IMAGE_ID,
                                                            now_utc=NOW).reason_codes
    high = _scan(findings=[{"id": "CVE-8", "severity": "HIGH"}])
    assert "VULN-UNGOVERNED-HIGH" in w.verify_vuln_scan(high, image_id=IMAGE_ID, now_utc=NOW).reason_codes
    stale = _scan(db_timestamp_utc="2020-01-01T00:00:00+00:00")
    assert "VULN-DB-STALE-NO-EXCEPTION" in w.verify_vuln_scan(stale, image_id=IMAGE_ID,
                                                             now_utc=NOW).reason_codes


def test_vuln_governed_exceptions_pass():
    # allow-listed HIGH with governance id + stale DB with governed exception -> pass.
    scan = _scan(findings=[{"id": "CVE-7", "severity": "HIGH"}],
                 allowlist=[{"id": "CVE-7", "governance_id": "GOV-VULN-001"}],
                 db_timestamp_utc="2020-01-01T00:00:00+00:00", db_freshness_exception="GOV-DB-EX-1")
    assert w.verify_vuln_scan(scan, image_id=IMAGE_ID, now_utc=NOW).ok


# =============================================================================== §18 state machine
def test_state_machine_no_skip():
    m = sm.CandidateStateMachine(source_sha="a" * 40)
    with pytest.raises(sm.StateMachineError):
        m.advance(sm.State.CONTEXT_EXPORTED, NOW, "SKIP")  # cannot skip SOURCE_VERIFIED
    m.advance(sm.State.SOURCE_VERIFIED, NOW, "ok")
    with pytest.raises(sm.StateMachineError):
        m.advance(sm.State.BUILD_READY, NOW, "SKIP")


def test_state_machine_failure_forces_rejected_and_permanence():
    m = sm.CandidateStateMachine(source_sha="a" * 40)
    m.advance(sm.State.SOURCE_VERIFIED, NOW, "ok")
    m.reject(NOW, "FAILURE")
    assert m.is_rejected and not m.is_ready
    # rejected cannot advance / become ready / retry / re-reject
    for call in (lambda: m.advance(sm.State.CONTEXT_EXPORTED, NOW, "x"),
                 lambda: m.advance(sm.State.CANDIDATE_READY, NOW, "x"),
                 lambda: m.reject(NOW, "again")):
        with pytest.raises(sm.StateMachineError):
            call()


def test_state_machine_has_no_publish_or_deploy_state():
    names = set(sm.state_order()) | {sm.State.REJECTED.value}
    for forbidden in ("PUBLISHED", "DEPLOYED", "ACTIVATED", "LIVE"):
        assert forbidden not in names
    assert set(s.value for s in sm.State) == names


def test_state_machine_records_immutable():
    m = sm.CandidateStateMachine(source_sha="a" * 40)
    rec = m.advance(sm.State.SOURCE_VERIFIED, NOW, "ok")
    with pytest.raises(Exception):
        rec.to_state = sm.State.REJECTED  # frozen dataclass
    with pytest.raises(sm.StateMachineError):
        m.advance(sm.State.CONTEXT_EXPORTED, "not-utc", "bad-ts")  # UTC required


# =============================================================================== §19 evaluator
def _all_green():
    return ev.ReadinessInputs(
        trusted_source_verified=True, clean_context_manifest_valid=True,
        effective_docker_context_valid=True, prohibited_findings_count=0, secret_findings_count=0,
        secret_findings_all_governed_nonsecret=False, build_succeeded=True, image_id_captured=True,
        oci_labels_exact_match=True, image_content_passed=True, sbom_passed=True, vuln_scan_passed=True,
        publish_attempted=False, deploy_attempted=False, consumer_live=False, shadow_enabled=False,
        phase2_activated=False,
    )


def test_evaluator_all_green_required():
    assert ev.evaluate(_all_green()).ready is True


@pytest.mark.parametrize("field,val,code", [
    ("trusted_source_verified", False, "R-TRUSTED-SOURCE-NOT-VERIFIED"),
    ("clean_context_manifest_valid", False, "R-CLEAN-CONTEXT-MANIFEST-INVALID"),
    ("effective_docker_context_valid", False, "R-EFFECTIVE-CONTEXT-INVALID"),
    ("prohibited_findings_count", 1, "R-PROHIBITED-PATHS-PRESENT"),
    ("secret_findings_count", 1, "R-SECRET-FINDINGS-PRESENT"),
    ("build_succeeded", False, "R-BUILD-NOT-SUCCEEDED"),
    ("image_id_captured", False, "R-IMAGE-ID-NOT-CAPTURED"),
    ("oci_labels_exact_match", False, "R-OCI-LABELS-NOT-EXACT"),
    ("image_content_passed", False, "R-IMAGE-CONTENT-FAILED"),
    ("sbom_passed", False, "R-SBOM-FAILED"),
    ("vuln_scan_passed", False, "R-VULN-SCAN-FAILED"),
    ("publish_attempted", True, "R-PUBLISH-ATTEMPTED"),
    ("deploy_attempted", True, "R-DEPLOY-ATTEMPTED"),
    ("consumer_live", True, "R-CONSUMER-LIVE-TRUE"),
    ("shadow_enabled", True, "R-SHADOW-ENABLED-TRUE"),
    ("phase2_activated", True, "R-PHASE2-ACTIVATED"),
])
def test_evaluator_any_single_failure_rejects(field, val, code):
    verdict = ev.evaluate(replace(_all_green(), **{field: val}))
    assert verdict.ready is False and code in verdict.reason_codes


def test_evaluator_secret_governed_nonsecret_permitted():
    inp = replace(_all_green(), secret_findings_count=2, secret_findings_all_governed_nonsecret=True)
    assert ev.evaluate(inp).ready is True


# =============================================================================== §6/§20/§21 orchestration
def test_full_flow_ready_and_schema_valid(governed_repo):
    root, sha = governed_repo
    rep = _run(root, sha, FakeRunner(sha))
    assert rep.ready is True and rep.state == "CANDIDATE_READY"
    states = [t["to_state"] for t in rep.transitions]
    assert states == list(sm.state_order()[1:])  # every state in order, none skipped
    # §21 safety flags
    for flag in (rep.runtime_wired, rep.config_installed, rep.shadow_enabled, rep.shadow_executed,
                 rep.consumer_live, rep.phase2_activated, rep.published, rep.deployed):
        assert flag is False
    schema = json.loads((SCHEMAS / "stage_b_candidate_readiness.v1.schema.json").read_text())
    jsonschema.Draft7Validator.check_schema(schema)
    jsonschema.validate(instance=rep.to_dict(), schema=schema)


def test_full_flow_consumer_live_rejects(governed_repo):
    root, sha = governed_repo
    rep = _run(root, sha, FakeRunner(sha), consumer_live=True)
    assert rep.ready is False and rep.state == "REJECTED"


def test_full_flow_shadow_enabled_rejects(governed_repo):
    root, sha = governed_repo
    rep = _run(root, sha, FakeRunner(sha), shadow_enabled=True)
    assert rep.ready is False and rep.state == "REJECTED"


def test_no_publish_no_deploy_static_guards():
    src = Path(w.__file__).read_text().lower()
    # no publish/deploy execution verbs, no orchestrator control planes, no db writes.
    for banned in ("docker push", "docker login", "registry login", "docker run ", "docker compose",
                   "kubectl", "systemctl ", "\nimport redis", "pymysql", "sqlalchemy", "import sqlite3"):
        assert banned not in src, banned
    # 'helm ' as a shell verb must not appear (the word Helm-the-persona is fine but lowercased check
    # for the CLI invocation pattern 'helm upgrade'/'helm install').
    for banned in ("helm upgrade", "helm install"):
        assert banned not in src, banned


def test_tools_and_models_stdlib_only():
    stdlib = {
        "argparse", "datetime", "hashlib", "io", "json", "os", "re", "subprocess", "sys", "posixpath",
        "tarfile", "dataclasses", "pathlib", "typing", "enum", "__future__",
    }
    local_prefixes = ("tools", "design")
    for mod in (w, di, sm, ev, cc):
        tree = ast.parse(Path(mod.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    top = a.name.split(".")[0]
                    assert top in stdlib or top in local_prefixes, a.name
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                top = (node.module or "").split(".")[0]
                assert top in stdlib or top in local_prefixes, node.module


def test_wrapper_not_imported_by_runtime():
    runtime_files = ["main.py", "signal_builder.py", "config.py", "env_config.py",
                     "utils/watchdog.py", "adapters/oanda.py", "adapters/base.py"]
    banned = ("hermes_stage_b_build", "hermes_fw08_")
    for rel in runtime_files:
        p = REPO / rel
        if not p.exists():
            continue
        tree = ast.parse(p.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert not any(b in a.name for b in banned), f"{rel} imports {a.name}"
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert not any(b in mod for b in banned), f"{rel} imports {mod}"
