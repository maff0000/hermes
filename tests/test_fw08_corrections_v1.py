"""FW-08 PR#114 exact-audit corrections + canonical-preflight closure tests.

WO-HELM-HERMES-PR114-FW08-EXACT-AUDIT-CORRECTIONS-AND-CANONICAL-PREFLIGHT-CLOSURE-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-19. Contract version: 1.

These tests build NO real image, run NO real docker/SBOM/scan, publish NOTHING, deploy NOTHING, wire NO
runtime, enable NO shadow, mutate NO live state, and add NO third-party dependency. They close the eight
R2D2 exact-head findings:
  §6  secret-scanner false-positive -> versioned, deterministic, fingerprint-bound TYPED disposition
      mechanism (12 adversarial cases); detection is NOT weakened; matched values NEVER stored/exposed.
  §7  a REAL non-building canonical preflight reaches USABLE against the EXACT canonical fe2037c5, proving
      no docker/image/tag/SBOM/vuln/publish/deploy.
  §8  trusted-ref freshness binding (stale/tamper/fetch-failure/remote-mismatch/authorised-mismatch/
      canonical-state-mismatch/foreign-remote/offline), using ISOLATED fixture repos.
  §9  final TOCTOU boundary: mutation after manifest / after effective-context / immediately-before-runner
      (replace/symlink-swap/added/removed/mode-change) all reject BEFORE the runner is invoked.
  §10 docker-pattern parity: leading-whitespace comments, escaped markers, inverted char classes, malformed
      classes (fail-closed), literal brackets, parent+child negation, repeated **, trailing spaces, rooted.
  §11 vuln allow-list governance: only an exact, typed, unexpired disposition binding EVERY field governs.
  §12 readiness evidence binding: bare/fabricated/mismatched/stale/contradictory/missing evidence rejects.
  §13 active Phase-2 import evidence: direct/transitive/dynamic/runner/plugin imports reject; inert accepts.

Run: cd <worktree> && python3 -m pytest tests/test_fw08_corrections_v1.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import tools.hermes_clean_build_context_v1 as cbc
import tools.hermes_stage_b_build_v1 as w
import design.hermes_fw08_dockerignore_matcher_v1 as di
import design.hermes_fw08_readiness_evidence_v1 as ee
import design.hermes_fw08_vuln_disposition_v1 as vd
import design.hermes_fw08_active_import_evidence_v1 as ai

REPO = Path(__file__).resolve().parents[1]
CANONICAL_BASE = "fe2037c5b4b4836d1038b4c0f734426ac9b3fa02"
EXPECTED_REMOTE = "git@github.com:maff0000/hermes.git"
NOW = "2026-07-19T00:00:00+00:00"
BUILD_UTC = "2026-07-19T12:00:00+00:00"
IMAGE_ID = "sha256:" + "a" * 64
SRC40 = "a" * 40
# Built by concatenation so the literal never appears verbatim in this test source (no stored secret bytes).
PPK_MARKER = b"PuTTY-User-" + b"Key-File"
PPK_PATH = "tools/hermes_clean_build_context_v1.py"


# =============================================================================== helpers
def _git(args, cwd, env=None):
    e = dict(os.environ)
    e.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
              "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(cwd)})
    if env:
        e.update(env)
    return subprocess.run(["git", *args], cwd=str(cwd), env=e, capture_output=True, text=True, check=True)


_SSS = tuple(f"utils/hermes_sss_{n}_v1.py" for n in
             ("comparator", "config", "evidence_adapters", "evidence_snapshot", "jsonl_writer",
              "mapper", "observer", "redaction", "shadow_adapter", "shadow_record"))
_GOVERNED_DI = (".git\n**/.git\ntests/\n*.jsonl\n**/*.jsonl\n.env\n.env.*\n!.env.example\nDockerfile\n"
                ".dockerignore\n")
_GOVERNED_FILES = tuple(list(w.REQUIRED_EFFECTIVE_INCLUSIONS) + list(_SSS))


def _make_repo(root, *, extra=None, dockerignore=None, origin_main=True):
    _git(["init", "-q"], root)
    _git(["remote", "add", "origin", EXPECTED_REMOTE], root)
    for rel in _GOVERNED_FILES:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# stub {rel}\npass\n", encoding="utf-8")
    (root / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    (root / ".dockerignore").write_text(dockerignore or _GOVERNED_DI, encoding="utf-8")
    for rel, content in (extra or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    _git(["add", *(list(_GOVERNED_FILES) + ["Dockerfile", ".dockerignore"] + list((extra or {}).keys()))], root)
    _git(["commit", "-q", "-m", "gov"], root)
    sha = _git(["rev-parse", "HEAD"], root).stdout.strip()
    if origin_main:
        _git(["update-ref", "refs/remotes/origin/main", sha], root)
    return sha


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    return root, _make_repo(root)


def _details(content: bytes, tmp_path, rel=PPK_PATH):
    p = tmp_path / "blob.bin"
    p.write_bytes(content)
    return cbc.scan_content_detailed(p, rel)


def _disp(fingerprint, path, *, rule="SEC-PRIVATE-KEY-PPK", source_sha=SRC40, **over):
    d = {
        "contract_version": "1", "disposition_id": "D-1", "rule_id": rule, "path": path,
        "fingerprint": fingerprint, "category": "RULE_DEFINITION_SELF_MATCH",
        "reason_code": "SELF-MATCH", "owner": "HELM", "created_utc": NOW, "evidence_reference": "ev",
        "source_binding": {"type": "SOURCE_SHA", "source_sha": source_sha}, "permanent": False,
        "expiry_utc": "2027-07-19T00:00:00+00:00",
    }
    d.update(over)
    return cbc.disposition_from_dict(d)


# =============================================================================== §6 scanner: 12 adversarial
def test_s6_01_exact_selfmatch_dispositioned_passes(tmp_path):
    f = _details(b'rule = compile(rb"' + PPK_MARKER + b'")\n', tmp_path)[0]
    d = _disp(f.fingerprint, f.path)
    klass, reason, did = cbc.classify_secret_finding([f][0], [d], source_sha=SRC40, now_utc=NOW)
    assert klass == "GOVERNED_NONSECRET" and did == "D-1"


def test_s6_02_changed_matched_literal_fails(tmp_path):
    # AWS-AKID rule: a governed value, then a DIFFERENT value at the same structural position -> new fp.
    a = _details(b'key = "AKIA' + b"A" * 16 + b'"\n', tmp_path)[0]
    d = _disp(a.fingerprint, a.path, rule="SEC-AWS-AKID")
    b = _details(b'key = "AKIA' + b"B" * 16 + b'"\n', tmp_path)[0]
    klass, reason, _ = cbc.classify_secret_finding(b, [d], source_sha=SRC40, now_utc=NOW)
    assert klass == "UNGOVERNED"


def test_s6_03_real_ppk_elsewhere_same_file_fails(tmp_path):
    got = _details(b'rule = compile(rb"' + PPK_MARKER + b'")\n# later:\nx = "' + PPK_MARKER + b'"\n', tmp_path)
    assert len(got) == 2
    d = _disp(got[0].fingerprint, got[0].path)
    classes = [cbc.classify_secret_finding(g, [d], source_sha=SRC40, now_utc=NOW)[0] for g in got]
    assert classes.count("GOVERNED_NONSECRET") == 1 and classes.count("UNGOVERNED") == 1


def test_s6_04_real_ppk_adjacent_to_rule_def_fails(tmp_path):
    got = _details(b'rule = compile(rb"' + PPK_MARKER + b'")\nleak="' + PPK_MARKER + b'"\n', tmp_path)
    d = _disp(got[0].fingerprint, got[0].path)
    adj = cbc.classify_secret_finding(got[1], [d], source_sha=SRC40, now_utc=NOW)[0]
    assert adj == "UNGOVERNED"


def test_s6_05_real_credential_in_docs_fails(tmp_path):
    doc = _details(b'example key: ' + PPK_MARKER + b'\n', tmp_path, rel="docs/guide.md")[0]
    # disposition governs the tool-file path only; the docs finding has a different path -> different fp.
    tool = _details(b'rule = compile(rb"' + PPK_MARKER + b'")\n', tmp_path)[0]
    d = _disp(tool.fingerprint, tool.path)
    assert cbc.classify_secret_finding(doc, [d], source_sha=SRC40, now_utc=NOW)[0] == "UNGOVERNED"


def test_s6_06_copy_rule_literal_to_other_path_fails(tmp_path):
    orig = _details(b'rule = compile(rb"' + PPK_MARKER + b'")\n', tmp_path, rel=PPK_PATH)[0]
    d = _disp(orig.fingerprint, orig.path)
    copy = _details(b'rule = compile(rb"' + PPK_MARKER + b'")\n', tmp_path, rel="utils/other.py")[0]
    assert copy.fingerprint != orig.fingerprint
    assert cbc.classify_secret_finding(copy, [d], source_sha=SRC40, now_utc=NOW)[0] == "UNGOVERNED"


def test_s6_07_changed_rule_id_fails(tmp_path):
    f = _details(b'rule = compile(rb"' + PPK_MARKER + b'")\n', tmp_path)[0]
    d = _disp(f.fingerprint, f.path, rule="SEC-AWS-AKID")  # disposition claims a different (known) rule
    klass, reason, _ = cbc.classify_secret_finding(f, [d], source_sha=SRC40, now_utc=NOW)
    assert klass == "UNGOVERNED" and reason == "DISPOSITION-RULE-MISMATCH"


def test_s6_08_missing_owner_fails(tmp_path):
    f = _details(b'rule = compile(rb"' + PPK_MARKER + b'")\n', tmp_path)[0]
    d = _disp(f.fingerprint, f.path, owner="")
    klass, reason, _ = cbc.classify_secret_finding(f, [d], source_sha=SRC40, now_utc=NOW)
    assert klass == "UNGOVERNED" and reason == "DISPOSITION-MALFORMED"


def test_s6_09_expired_disposition_fails(tmp_path):
    f = _details(b'rule = compile(rb"' + PPK_MARKER + b'")\n', tmp_path)[0]
    d = _disp(f.fingerprint, f.path, expiry_utc="2020-01-01T00:00:00+00:00")
    klass, reason, _ = cbc.classify_secret_finding(f, [d], source_sha=SRC40, now_utc=NOW)
    assert klass == "UNGOVERNED" and reason == "DISPOSITION-EXPIRED"


def test_s6_10_malformed_disposition_fails(tmp_path):
    f = _details(b'rule = compile(rb"' + PPK_MARKER + b'")\n', tmp_path)[0]
    # wildcard path is malformed; also a wildcard fingerprint; also a bad source binding
    malformed = [
        _disp(f.fingerprint, "tools/*"),                                   # wildcard path
        _disp("*" * 64, f.path),                                           # wildcard fingerprint
        _disp(f.fingerprint, f.path, source_binding={"type": "X"}),        # bad source binding
    ]
    for d in malformed:
        with pytest.raises(cbc.CleanBuildContextError):
            cbc.validate_disposition(d)
    # a malformed dispositions FILE fails closed on load
    bad_file = tmp_path / "bad.json"
    bad_file.write_text(json.dumps({"contract_version": "1", "dispositions": [{"rule_id": "x"}]}))
    with pytest.raises(cbc.CleanBuildContextError):
        cbc.load_dispositions(bad_file)


def test_s6_11_unknown_finding_fails_closed(tmp_path):
    f = _details(b'rule = compile(rb"' + PPK_MARKER + b'")\n', tmp_path)[0]
    klass, reason, _ = cbc.classify_secret_finding(f, [], source_sha=SRC40, now_utc=NOW)  # no dispositions
    assert klass == "UNGOVERNED" and reason == "NO-DISPOSITION"


def test_s6_12_evidence_never_contains_matched_value(tmp_path):
    got = _details(b'rule = compile(rb"' + PPK_MARKER + b'")\n', tmp_path)
    d = _disp(got[0].fingerprint, got[0].path)
    df = cbc.DispositionedFinding(path=got[0].path, rule_id=got[0].rule_id, fingerprint=got[0].fingerprint,
                                  disposition_id=d.disposition_id, reason_code="SELF-MATCH", category="X")
    blobs = [json.dumps(g.to_dict()) for g in got] + [json.dumps(df.to_dict())]
    marker = PPK_MARKER.decode()
    for blob in blobs:
        assert marker not in blob
    # the source-mismatch reason path also never leaks the value
    other = cbc.classify_secret_finding(got[0], [_disp(got[0].fingerprint, got[0].path, source_sha="c" * 40)],
                                        source_sha=SRC40, now_utc=NOW)
    assert other == ("UNGOVERNED", "DISPOSITION-SOURCE-SHA-MISMATCH", "D-1")


def test_s6_governed_file_selfmatch_passes_and_default_fails(tmp_path):
    """Integration: at fe2037c5 the governed disposition clears the PPK self-match (PASS); with NO
    dispositions the same scan still FAILS closed (detection unchanged)."""
    disps = w.load_governed_dispositions()
    m_ok = cbc.build_clean_context(source_sha=CANONICAL_BASE, output_dir=tmp_path / "c1", repo_dir=REPO,
                                   expected_remote=EXPECTED_REMOTE, now_utc=NOW, dispositions=disps)
    assert m_ok.result == "PASS" and m_ok.secret_findings == () and len(m_ok.dispositioned) == 1
    m_fail = cbc.build_clean_context(source_sha=CANONICAL_BASE, output_dir=tmp_path / "c2", repo_dir=REPO,
                                     expected_remote=EXPECTED_REMOTE, now_utc=NOW)
    assert m_fail.result == "FAIL" and len(m_fail.secret_findings) == 1


# =============================================================================== §7 canonical preflight
def _immutable():
    return {"authorised_sha": CANONICAL_BASE, "binding_id": "WO-PR114", "canonical_state_sha": CANONICAL_BASE}


def test_s7_canonical_preflight_usable_against_exact_fe2037c5(tmp_path):
    rep = w.run_canonical_preflight(
        source_sha=CANONICAL_BASE, repo_dir=REPO, quarantine_dir=tmp_path / "q", now_utc=NOW,
        build_utc=BUILD_UTC, candidate_name="fw08canonical", dispositions=w.load_governed_dispositions(),
        authorised_sha=CANONICAL_BASE, immutable_source_binding=_immutable(),
    )
    assert rep.terminal_state == "USABLE", rep.reason_codes
    assert len(rep.dispositioned) == 1
    # no-build proof
    assert rep.docker_invoked is False and rep.runner_constructed is False
    assert rep.image_id is None and rep.tag is None and rep.sbom is None and rep.vuln_scan is None
    assert rep.published is False and rep.deployed is False


def test_s7_preflight_invokes_no_real_docker(monkeypatch, tmp_path):
    real_run, real_popen = subprocess.run, subprocess.Popen

    def gr(cmd, *a, **k):
        assert not (cmd and "docker" in str(cmd[0])), "no real docker run"
        return real_run(cmd, *a, **k)

    def gp(cmd, *a, **k):
        assert not (cmd and "docker" in str(cmd[0])), "no real docker popen"
        return real_popen(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", gr)
    monkeypatch.setattr(subprocess, "Popen", gp)
    rep = w.run_canonical_preflight(
        source_sha=CANONICAL_BASE, repo_dir=REPO, quarantine_dir=tmp_path / "q", now_utc=NOW,
        build_utc=BUILD_UTC, candidate_name="fw08canonical", dispositions=w.load_governed_dispositions(),
        authorised_sha=CANONICAL_BASE, immutable_source_binding=_immutable(),
    )
    assert rep.usable


def test_s7_preflight_without_dispositions_rejects(tmp_path):
    # without the governed disposition the PPK self-match makes the canonical context UNUSABLE (fail-closed).
    rep = w.run_canonical_preflight(
        source_sha=CANONICAL_BASE, repo_dir=REPO, quarantine_dir=tmp_path / "q", now_utc=NOW,
        build_utc=BUILD_UTC, candidate_name="fw08canonical", dispositions=None,
        authorised_sha=CANONICAL_BASE, immutable_source_binding=_immutable(),
    )
    assert rep.terminal_state == "REJECTED"


# =============================================================================== §8 trusted-ref freshness
def test_s8_authenticated_fetch_fresh(repo):
    root, sha = repo
    fr = w.verify_trusted_ref_freshness(sha, root, authorised_sha=sha, expected_remote=EXPECTED_REMOTE,
                                        fetcher=w.FixtureRemoteRefFetcher(sha))
    assert fr.fresh and fr.mechanism == "FRESH_AUTHENTICATED_FETCH"


def test_s8_stale_local_ref_rejects(tmp_path):
    root = tmp_path / "s"
    root.mkdir()
    base = _make_repo(root)
    (root / "extra.py").write_text("x=1\n", encoding="utf-8")
    _git(["add", "extra.py"], root)
    _git(["commit", "-q", "-m", "advance"], root)
    head = _git(["rev-parse", "HEAD"], root).stdout.strip()
    # local origin/main still at base, but the fresh remote is `head` -> stale local ref.
    fr = w.verify_trusted_ref_freshness(head, root, authorised_sha=head, expected_remote=EXPECTED_REMOTE,
                                        fetcher=w.FixtureRemoteRefFetcher(head))
    assert not fr.fresh and "STALE-OR-TAMPERED-LOCAL-REF" in fr.reason_codes


def test_s8_authorised_sha_mismatch_rejects(repo):
    root, sha = repo
    fr = w.verify_trusted_ref_freshness(sha, root, authorised_sha="b" * 40, expected_remote=EXPECTED_REMOTE,
                                        fetcher=w.FixtureRemoteRefFetcher(sha))
    assert not fr.fresh and "AUTHORISED-SHA-MISMATCH" in fr.reason_codes


def test_s8_remote_mismatch_rejects(repo):
    root, sha = repo
    # a fresh remote SHA that differs from the local ref AND authorised -> reachable? not reachable.
    fr = w.verify_trusted_ref_freshness(sha, root, authorised_sha=sha, expected_remote=EXPECTED_REMOTE,
                                        fetcher=w.FixtureRemoteRefFetcher("c" * 40))
    assert not fr.fresh
    assert "AUTHORISED-SHA-MISMATCH" in fr.reason_codes or "STALE-OR-TAMPERED-LOCAL-REF" in fr.reason_codes


def test_s8_canonical_state_mismatch_rejects(repo):
    root, sha = repo
    fr = w.verify_trusted_ref_freshness(sha, root, authorised_sha=sha, expected_remote=EXPECTED_REMOTE,
                                        fetcher=w.FixtureRemoteRefFetcher(sha),
                                        canonical_state_record={"source_sha": "d" * 40})
    assert not fr.fresh and "CANONICAL-STATE-MISMATCH" in fr.reason_codes


def test_s8_foreign_remote_rejects(repo):
    root, sha = repo
    with pytest.raises(w.SourceFreshnessError):
        w.verify_trusted_ref_freshness(sha, root, authorised_sha=sha,
                                       expected_remote="git@github.com:someone/else.git",
                                       fetcher=w.FixtureRemoteRefFetcher(sha))


def test_s8_offline_fetch_failure_rejects_closed(repo):
    root, sha = repo
    with pytest.raises(w.SourceFreshnessError):
        w.verify_trusted_ref_freshness(sha, root, authorised_sha=sha, expected_remote=EXPECTED_REMOTE)
    # default fetcher refuses (offline) -> fail closed, unless an immutable binding is supplied
    fr = w.verify_trusted_ref_freshness(sha, root, authorised_sha=sha, expected_remote=EXPECTED_REMOTE,
                                        immutable_source_binding={"authorised_sha": sha, "binding_id": "B",
                                                                  "canonical_state_sha": sha})
    assert fr.fresh and fr.mechanism == "GOVERNED_IMMUTABLE_BINDING"


def test_s8_fetch_failure_no_binding_rejects(repo):
    root, sha = repo
    with pytest.raises(w.SourceFreshnessError):
        w.verify_trusted_ref_freshness(
            sha, root, authorised_sha=sha, expected_remote=EXPECTED_REMOTE,
            fetcher=w.FixtureRemoteRefFetcher(raises=w.SourceFreshnessError("network down")),
        )


def test_s8_local_ref_tamper_under_immutable_binding_rejects(tmp_path):
    root = tmp_path / "t"
    root.mkdir()
    base = _make_repo(root)
    (root / "e.py").write_text("y=1\n", encoding="utf-8")
    _git(["add", "e.py"], root)
    _git(["commit", "-q", "-m", "x"], root)
    tampered = _git(["rev-parse", "HEAD"], root).stdout.strip()
    _git(["update-ref", "refs/remotes/origin/main", tampered], root)  # local ref points at a non-authorised sha
    fr = w.verify_trusted_ref_freshness(
        base, root, authorised_sha=base, expected_remote=EXPECTED_REMOTE,
        immutable_source_binding={"authorised_sha": base, "binding_id": "B", "canonical_state_sha": base},
    )
    assert not fr.fresh and "LOCAL-REF-NOT-AUTHORISED-SHA" in fr.reason_codes


# =============================================================================== §9 final TOCTOU
def _usable_ctx(root, sha, q):
    ctx = w.prepare_governed_context(sha, root, q, expected_remote=EXPECTED_REMOTE, now_utc=NOW)
    assert ctx.usable
    return ctx


def test_s9_no_mutation_revalidates_clean(repo, tmp_path):
    root, sha = repo
    ctx = _usable_ctx(root, sha, tmp_path / "q")
    snap = w.finalise_context_snapshot(ctx)
    assert w.revalidate_context_snapshot(ctx, snap) is None


def test_s9_content_replacement_rejected(repo, tmp_path):
    root, sha = repo
    ctx = _usable_ctx(root, sha, tmp_path / "q")
    snap = w.finalise_context_snapshot(ctx)
    (ctx.context_dir / "main.py").write_text("TAMPERED\n", encoding="utf-8")
    r = w.revalidate_context_snapshot(ctx, snap)
    assert r and r.startswith("FILE-CONTENT-CHANGED")


def test_s9_symlink_swap_rejected(repo, tmp_path):
    root, sha = repo
    ctx = _usable_ctx(root, sha, tmp_path / "q")
    snap = w.finalise_context_snapshot(ctx)
    target = ctx.context_dir / "main.py"
    target.unlink()
    os.symlink("/etc/hostname", target)
    r = w.revalidate_context_snapshot(ctx, snap)
    assert r and r.startswith("FILE-TYPE-CHANGED")


def test_s9_added_file_rejected(repo, tmp_path):
    root, sha = repo
    ctx = _usable_ctx(root, sha, tmp_path / "q")
    snap = w.finalise_context_snapshot(ctx)
    (ctx.context_dir / "sneaky.py").write_text("x\n", encoding="utf-8")
    r = w.revalidate_context_snapshot(ctx, snap)
    assert r and (r.startswith("FILE-APPEARED") or r.startswith("FILE-COUNT-CHANGED"))


def test_s9_removed_file_rejected(repo, tmp_path):
    root, sha = repo
    ctx = _usable_ctx(root, sha, tmp_path / "q")
    snap = w.finalise_context_snapshot(ctx)
    (ctx.context_dir / "main.py").unlink()
    r = w.revalidate_context_snapshot(ctx, snap)
    assert r and (r.startswith("FILE-DISAPPEARED") or r.startswith("FILE-COUNT-CHANGED"))


def test_s9_mode_change_rejected(repo, tmp_path):
    root, sha = repo
    ctx = _usable_ctx(root, sha, tmp_path / "q")
    snap = w.finalise_context_snapshot(ctx)
    (ctx.context_dir / "main.py").chmod(0o777)
    r = w.revalidate_context_snapshot(ctx, snap)
    assert r and r.startswith("FILE-MODE-CHANGED")


def test_s9_flow_rejects_before_runner_on_mutation(repo, tmp_path):
    """A mutation injected at the finalisation boundary is rejected BEFORE the fake runner is invoked."""
    root, sha = repo
    from tests.test_fw08_governed_build_v1 import FakeRunner
    runner = FakeRunner(sha)

    def mutate(_snapshot):
        # find the quarantined context and tamper a file just before the runner would receive it
        for ctxdir in (tmp_path / "q").rglob("main.py"):
            ctxdir.write_text("TAMPERED-JUST-BEFORE-RUNNER\n", encoding="utf-8")
            break

    rep = w.run_stage_b_candidate_build(
        source_sha=sha, build_utc=BUILD_UTC, candidate_name="fw08cand", repo_dir=root,
        quarantine_dir=tmp_path / "q", now_utc=NOW, runner=runner, on_context_finalised=mutate,
    )
    assert rep.ready is False and rep.state == "REJECTED"
    assert any("TOCTOU-CONTEXT-MUTATED" in r for r in rep.reason_codes)
    assert all(c[0] != "build" for c in runner.calls), "runner.build must NOT have been called"


# =============================================================================== §10 matcher parity
def test_s10_parity_fixtures_all_pass():
    bad = [(c.label, c.excluded, a) for c, a in di.run_parity_cases() if a != c.excluded]
    assert not bad, bad
    labels = " ".join(c.label for c in di.PARITY_CASES)
    for needle in ("leading-ws", "escaped", "inverted-class", "literal-bracket", "parent-excluded",
                   "repeated-doublestar", "trailing-spaces", "rooted"):
        assert needle in labels, needle


def test_s10_unsupported_syntax_fails_closed():
    for pat, raised in di.run_unsupported_cases():
        assert raised, pat
    with pytest.raises(di.UnsupportedPatternError):
        di.parse_dockerignore("file[abc\n")


def test_s10_inverted_char_class_faithful():
    pats = di.parse_dockerignore("file[^a].txt\n")
    assert di.path_excluded("fileb.txt", pats) is True     # non-member matches
    assert di.path_excluded("filea.txt", pats) is False    # member excluded from the class


def test_s10_leading_whitespace_is_not_a_comment():
    pats = di.parse_dockerignore("   #cache\n")
    assert di.path_excluded("#cache", pats) is True        # a literal pattern, not a comment
    assert di.parse_dockerignore("#cache\n") == ()          # a true comment is skipped


def test_s10_effective_context_fails_closed_on_bad_syntax():
    verdict = w.verify_effective_context(["main.py"], "logs[unterminated\n")
    assert not verdict.ok and "UNSUPPORTED-DOCKERIGNORE-SYNTAX" in verdict.reason_codes


# =============================================================================== §11 vuln governance
def _vd(**over):
    d = {"contract_version": "1", "disposition_id": "V1", "vuln_id": "CVE-1", "package": "openssl",
         "installed_version": "3.0.1", "image_id": IMAGE_ID, "source_sha": SRC40, "severity": "HIGH",
         "reason": "accepted", "risk_owner": "HELM", "approval_authority": "HELM", "created_utc": NOW,
         "expiry_utc": "2027-07-19T00:00:00+00:00", "scanner_id": "grype", "scanner_version": "0.74.0",
         "vuln_db_id": "db1", "vuln_db_timestamp_utc": NOW, "evidence_checksum": "e" * 64}
    d.update(over)
    return vd.from_dict(d)


def _finding(**over):
    f = {"id": "CVE-1", "package": "openssl", "installed_version": "3.0.1", "severity": "HIGH"}
    f.update(over)
    return f


def test_s11_full_disposition_governs():
    ok, reason = vd.governs(_vd(), _finding(), image_id=IMAGE_ID, source_sha=SRC40, scanner_id="grype",
                            scanner_version="0.74.0", now_utc=NOW)
    assert ok and reason == "GOVERNED"


@pytest.mark.parametrize("mut,ctx,label", [
    ({}, {"image_id": "sha256:" + "z" * 64}, "image-mismatch"),
    ({}, {"source_sha": "c" * 40}, "source-mismatch"),
    ({}, {"scanner_version": "9.9"}, "scanner-version-mismatch"),
    ({"expiry_utc": "2020-01-01T00:00:00+00:00"}, {}, "expired"),
    ({"package": "*"}, {}, "wildcard-package"),
    ({"vuln_id": "*"}, {}, "wildcard-vuln"),
    ({"reason": ""}, {}, "missing-reason"),
    ({"risk_owner": ""}, {}, "missing-owner"),
    ({"approval_authority": ""}, {}, "missing-approval"),
])
def test_s11_reject_cases(mut, ctx, label):
    kwargs = dict(image_id=IMAGE_ID, source_sha=SRC40, scanner_id="grype", scanner_version="0.74.0",
                  now_utc=NOW)
    kwargs.update(ctx)
    ok, reason = vd.governs(_vd(**mut), _finding(), **kwargs)
    assert not ok, label


def test_s11_truthy_id_alone_is_not_governance():
    # a bare allow-list entry cannot even be parsed into a disposition
    with pytest.raises(vd.VulnDispositionError):
        vd.from_dict({"id": "CVE-1", "governance_id": "GOV-1"})


# =============================================================================== §12 readiness evidence
def _good_evidence():
    order = ee.GATE_ORDER
    img_bearing = {"G-BUILD", "G-OCI-LABELS", "G-IMAGE-CONTENT", "G-SBOM", "G-VULN-SCAN"}
    recs = []
    for i, g in enumerate(order):
        recs.append(ee.make_gate_evidence(
            gate_id=g, result=True, reason_code="OK", source_sha=SRC40, producer="wrapper",
            tool_version="1", at_utc=f"2026-07-19T10:0{i}:00+00:00",
            image_id=(IMAGE_ID if g in img_bearing else None)))
    return recs


def test_s12_all_green_evidence_ready():
    v = ee.evaluate_evidence(_good_evidence(), expected_source_sha=SRC40, expected_image_id=IMAGE_ID,
                             now_utc="2026-07-19T11:00:00+00:00")
    assert v.ready, v.reason_codes


def test_s12_fabricated_bare_pass_rejected():
    recs = _good_evidence()
    r = recs[0]
    recs[0] = ee.GateEvidence(gate_id=r.gate_id, result=True, reason_code="FAKE", source_sha=SRC40,
                              producer="attacker", tool_version="1", at_utc=r.at_utc, image_id=None,
                              evidence_checksum=r.evidence_checksum)  # stale checksum -> mismatch
    v = ee.evaluate_evidence(recs, expected_source_sha=SRC40, expected_image_id=IMAGE_ID,
                             now_utc="2026-07-19T11:00:00+00:00")
    assert not v.ready and any("CHECKSUM-MISMATCH" in c for c in v.reason_codes)


def test_s12_source_sha_mismatch_rejected():
    recs = _good_evidence()
    v = ee.evaluate_evidence(recs, expected_source_sha="b" * 40, expected_image_id=IMAGE_ID,
                             now_utc="2026-07-19T11:00:00+00:00")
    assert not v.ready and any("SOURCE-SHA-CONFLICT" in c for c in v.reason_codes)


def test_s12_image_id_mismatch_rejected():
    recs = _good_evidence()
    v = ee.evaluate_evidence(recs, expected_source_sha=SRC40, expected_image_id="sha256:" + "z" * 64,
                             now_utc="2026-07-19T11:00:00+00:00")
    assert not v.ready and any("IMAGE-ID-MISMATCH" in c for c in v.reason_codes)


def test_s12_stale_evidence_rejected():
    recs = _good_evidence()
    v = ee.evaluate_evidence(recs, expected_source_sha=SRC40, expected_image_id=IMAGE_ID,
                             now_utc="2026-07-25T11:00:00+00:00")  # far in the future -> stale
    assert not v.ready and any(c.startswith("EV-STALE") for c in v.reason_codes)


def test_s12_contradictory_duplicate_rejected():
    recs = _good_evidence()
    dup = ee.make_gate_evidence(gate_id="G-SBOM", result=False, reason_code="CONFLICT", source_sha=SRC40,
                                producer="wrapper", tool_version="1", at_utc="2026-07-19T10:06:00+00:00",
                                image_id=IMAGE_ID)
    recs.append(dup)
    v = ee.evaluate_evidence(recs, expected_source_sha=SRC40, expected_image_id=IMAGE_ID,
                             now_utc="2026-07-19T11:00:00+00:00")
    assert not v.ready and any(c.startswith("EV-DUPLICATE") for c in v.reason_codes)


def test_s12_missing_gate_rejected():
    recs = [r for r in _good_evidence() if r.gate_id != "G-VULN-SCAN"]
    v = ee.evaluate_evidence(recs, expected_source_sha=SRC40, expected_image_id=IMAGE_ID,
                             now_utc="2026-07-19T11:00:00+00:00")
    assert not v.ready and any("EV-MISSING" in c for c in v.reason_codes)


def test_s12_failed_gate_result_rejected():
    order = ee.GATE_ORDER
    img_bearing = {"G-BUILD", "G-OCI-LABELS", "G-IMAGE-CONTENT", "G-SBOM", "G-VULN-SCAN"}
    recs = []
    for i, g in enumerate(order):
        recs.append(ee.make_gate_evidence(
            gate_id=g, result=(g != "G-VULN-SCAN"), reason_code="X", source_sha=SRC40, producer="w",
            tool_version="1", at_utc=f"2026-07-19T10:0{i}:00+00:00",
            image_id=(IMAGE_ID if g in img_bearing else None)))
    v = ee.evaluate_evidence(recs, expected_source_sha=SRC40, expected_image_id=IMAGE_ID,
                             now_utc="2026-07-19T11:00:00+00:00")
    assert not v.ready and any("EV-GATE-NOT-PASSED" in c for c in v.reason_codes)


# =============================================================================== §13 active-import evidence
def _phase2_modules():
    return [f"utils/hermes_sss_{n}_v1.py" for n in range(10)]


def _inert_evidence(**over):
    e = {
        "contract_version": "1", "evidence_method": "STATIC_IMPORT_GRAPH", "entrypoint_module": "main.py",
        "modules": ["main.py", "config.py"] + _phase2_modules(),
        "import_edges": {"main.py": ["config.py"], "config.py": []},
        "runner_registrations": [], "callback_registrations": [], "dynamic_imports": [],
        "activation_env_defaults": {"HERMES_SSS_SHADOW_ENABLED": "0"}, "plugin_discovery": [],
    }
    e.update(over)
    return e


def _validate(ev):
    return ai.validate_phase2_inert(ev, phase2_prefix="utils/hermes_sss_", phase2_suffix="_v1.py",
                                    required_phase2_count=10)


def test_s13_inert_graph_accepted():
    assert _validate(_inert_evidence()).inert


def test_s13_direct_import_rejected():
    v = _validate(_inert_evidence(import_edges={"main.py": ["utils/hermes_sss_0_v1.py"]}))
    assert not v.inert and "AI-PHASE2-IMPORTED-BY-ENTRYPOINT" in v.reason_codes


def test_s13_transitive_import_rejected():
    edges = {"main.py": ["config.py"], "config.py": ["utils/hermes_sss_3_v1.py"]}
    v = _validate(_inert_evidence(import_edges=edges))
    assert not v.inert and "AI-PHASE2-IMPORTED-BY-ENTRYPOINT" in v.reason_codes
    assert "utils/hermes_sss_3_v1.py" in v.reachable_phase2


def test_s13_dynamic_import_rejected():
    v = _validate(_inert_evidence(dynamic_imports=["utils/hermes_sss_5_v1.py"]))
    assert not v.inert and "AI-PHASE2-DYNAMIC-IMPORT" in v.reason_codes


def test_s13_runner_registration_rejected():
    v = _validate(_inert_evidence(runner_registrations=["utils/hermes_sss_2_v1.py"]))
    assert not v.inert and "AI-PHASE2-REGISTERED" in v.reason_codes


def test_s13_plugin_discovery_rejected():
    v = _validate(_inert_evidence(plugin_discovery=["utils/hermes_sss_9_v1.py"]))
    assert not v.inert and "AI-PHASE2-PLUGIN-DISCOVERY" in v.reason_codes


def test_s13_activation_env_default_rejected():
    v = _validate(_inert_evidence(activation_env_defaults={"HERMES_SSS_SHADOW_ENABLED": "1"}))
    assert not v.inert and "AI-PHASE2-ACTIVATION-ENV-DEFAULT" in v.reason_codes


def test_s13_test_only_declaration_not_accepted():
    v = _validate(_inert_evidence(evidence_method="TEST_ONLY_DECLARATION"))
    assert not v.inert and "AI-INADMISSIBLE-METHOD" in v.reason_codes


def test_s13_image_content_consumes_evidence(repo):
    # image-content gate rejects when the (fixture) image ships an actively-importing Phase-2 graph
    from tests.test_fw08_governed_build_v1 import _content
    c = _content()
    c["phase2_import_evidence"] = _inert_evidence(import_edges={"main.py": ["utils/hermes_sss_0_v1.py"]})
    v = w.verify_image_content(c, image_id=IMAGE_ID)
    assert not v.ok and "ACTIVE-PHASE2-IMPORT-EVIDENCE-FAILED" in v.reason_codes
    # inert evidence is accepted
    c2 = _content()
    c2["phase2_import_evidence"] = _inert_evidence()
    assert w.verify_image_content(c2, image_id=IMAGE_ID).ok


# =============================================================================== no new dependency
def test_no_new_third_party_dependency():
    import ast
    stdlib = {"argparse", "datetime", "hashlib", "io", "json", "os", "re", "subprocess", "sys",
              "posixpath", "tarfile", "dataclasses", "pathlib", "typing", "enum", "__future__"}
    for mod in (cbc, w, di, ee, vd, ai):
        tree = ast.parse(Path(mod.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    top = a.name.split(".")[0]
                    assert top in stdlib or top in ("tools", "design"), a.name
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                top = (node.module or "").split(".")[0]
                assert top in stdlib or top in ("tools", "design"), node.module
