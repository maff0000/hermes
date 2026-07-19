"""Pre-Stage-B build-context / OCI-provenance / safety-gate correction tests.

WO-HELM-HERMES-PRE-STAGE-B-BUILD-CONTEXT-OCI-PROVENANCE-AND-SAFETY-GATE-CORRECTIONS-IMPLEMENTATION-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-18. Contract version: 1.

These tests build NO image, deploy NOTHING, wire NOTHING, enable NO shadow, touch NO runtime. They prove:
  * the clean exact-SHA build-context tool (F-DR-01) exports ONLY tracked content, fails closed on
    invalid/abbreviated SHA, wrong repo, path-traversal / symlink-escape / nested-.git, and produces a
    deterministic, schema-valid manifest with a bounded, value-free security scan;
  * the hardened .dockerignore excludes secrets/.claude/*.jsonl/tests/ops-evidence while KEEPING every
    required production file (Phase-1 core, Phase-2 sss modules, entrypoint, config loader, adapters);
  * the OCI provenance-label contract (F-DR-02) is mandatory + fail-closed for future candidate images,
    while the legacy image is not retroactively invalid;
  * the design prose + models agree the labels + clean context are MANDATORY (no "not blocking" statement
    applies to future candidate-image labels), with one canonical control-identifier mapping;
  * the two dedicated safety gates (socket-ambiguity + historical-inference) hold behaviourally against
    the already-merged inert Phase-2 modules;
  * Phase-1/Phase-2 remain unimported by any runtime path.

Run: cd <worktree> && python3 -m pytest tests/test_pre_stage_b_build_corrections_v1.py -q
"""
from __future__ import annotations

import ast
import copy
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import jsonschema
import pytest

import tools.hermes_clean_build_context_v1 as cbc
import tools.hermes_image_label_verify_v1 as lbl

import utils.hermes_shared_stream_recovery_v1 as core
from utils.hermes_sss_evidence_snapshot_v1 import snapshot_from_dict, Observation
from utils.hermes_sss_shadow_record_v1 import current_authority_from_dict
from utils.hermes_sss_mapper_v1 import map_snapshot_to_recovery_input
from utils.hermes_sss_comparator_v1 import classify, ComparisonClass
from utils.hermes_sss_config_v1 import load_config
from utils.hermes_sss_shadow_adapter_v1 import (
    run_shadow_evaluation, RefusingShadowExecutor, ShadowExecutionForbidden,
)

REPO = Path(__file__).resolve().parents[1]
BASE_SHA = "a780a16182c3035e1161ac396092e203aa0a5eac"
EXPECTED_REMOTE = "git@github.com:maff0000/hermes.git"
FIXTURES = REPO / "tests" / "fixtures" / "sss_phase2"
DR = REPO / "docs" / "design" / "deployment_readiness"
MODELS = DR / "models"
SCHEMAS = REPO / "schemas" / "deployment_readiness"
ARCH = DR / "architecture_v1.md"
DECIDED_AT = "2026-07-16T21:09:00+00:00"
NOW_UTC = "2026-07-18T00:00:00+00:00"


# =============================================================================== helpers / fixtures
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


@pytest.fixture(scope="module")
def temp_repo(tmp_path_factory):
    """A self-contained git repo with a committed tree, an UNTRACKED file, and a DIRTY modification, so
    the clean-context tool's exact-SHA export can be proven not to leak working-tree content."""
    root = tmp_path_factory.mktemp("clean_ctx_repo")
    _git(["init", "-q"], root)
    _git(["remote", "add", "origin", EXPECTED_REMOTE], root)
    (root / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "keep.txt").write_text("tracked content v1\n", encoding="utf-8")
    sub = root / "pkg"
    sub.mkdir()
    (sub / "mod.py").write_text("X = 1\n", encoding="utf-8")
    (root / ".dockerignore").write_text("tests/\n*.jsonl\n", encoding="utf-8")
    _git(["add", "main.py", "keep.txt", "pkg/mod.py", ".dockerignore"], root)
    _git(["commit", "-q", "-m", "c1"], root)
    committed = _git(["rev-parse", "HEAD"], root).stdout.strip()
    # Contaminate the working tree AFTER the commit.
    (root / "UNTRACKED_SECRET.txt").write_text("api_key='AKIAABCDEFGHIJKLMNOP'\n", encoding="utf-8")
    (root / "keep.txt").write_text("DIRTY working-tree change v2\n", encoding="utf-8")
    return root, committed


@pytest.fixture(scope="module")
def july16():
    return json.loads((FIXTURES / "july16_snapshot.json").read_text())


def _july16_current(july16):
    return current_authority_from_dict(july16["current_authority"])


def _evaluate(snapshot_dict, current):
    snap = snapshot_from_dict(snapshot_dict)
    env = core.decide(map_snapshot_to_recovery_input(snap))
    klass, _ = classify(env, current)
    return snap, env, klass


def _run_shadow(snapshot_dict, current):
    snap = snapshot_from_dict(snapshot_dict)
    cfg = load_config({"shadow_enabled": True, "shadow_consumer_live": False})
    return run_shadow_evaluation(
        snap, current, config=cfg, decided_at_utc=DECIDED_AT,
        source_sha="8355044ba04a2afbf94c0d92cf371145577d16d6", runtime_identity={"service": "hermes"},
    )


# =============================================================================== 1. clean-context
def test_clean_context_exports_exactly_tracked_content(temp_repo):
    root, sha = temp_repo
    out = root / "_ctx"
    manifest = cbc.build_clean_context(
        source_sha=sha, output_dir=out, repo_dir=root, expected_remote=EXPECTED_REMOTE, now_utc=NOW_UTC,
    )
    paths = {f.path for f in manifest.files}
    assert paths == {"main.py", "keep.txt", "pkg/mod.py", ".dockerignore"}
    # keep.txt is the COMMITTED content, not the dirty working-tree change.
    assert (out / "keep.txt").read_text() == "tracked content v1\n"


def test_clean_context_dirty_worktree_cannot_contaminate(temp_repo):
    root, sha = temp_repo
    out = root / "_ctx2"
    manifest = cbc.build_clean_context(
        source_sha=sha, output_dir=out, repo_dir=root, expected_remote=EXPECTED_REMOTE, now_utc=NOW_UTC,
    )
    paths = {f.path for f in manifest.files}
    assert "UNTRACKED_SECRET.txt" not in paths
    assert not (out / "UNTRACKED_SECRET.txt").exists()


def test_clean_context_manifest_is_deterministic(temp_repo):
    root, sha = temp_repo
    m1 = cbc.build_clean_context(source_sha=sha, output_dir=root / "_d1", repo_dir=root,
                                 expected_remote=EXPECTED_REMOTE, now_utc=NOW_UTC)
    m2 = cbc.build_clean_context(source_sha=sha, output_dir=root / "_d2", repo_dir=root,
                                 expected_remote=EXPECTED_REMOTE, now_utc=NOW_UTC)
    assert m1.manifest_checksum == m2.manifest_checksum
    assert [f.to_dict() for f in m1.files] == [f.to_dict() for f in m2.files]
    # Checksum is verifiable / recomputable.
    assert m1.manifest_checksum == m1.compute_checksum()


def test_clean_context_invalid_sha_rejected():
    with pytest.raises(cbc.CleanBuildContextError):
        cbc.verify_source_sha("not-a-sha")
    with pytest.raises(cbc.CleanBuildContextError):
        cbc.verify_source_sha("ZZZ" * 13 + "z")


def test_clean_context_abbreviated_sha_rejected(temp_repo):
    root, sha = temp_repo
    with pytest.raises(cbc.CleanBuildContextError):
        cbc.verify_source_sha(sha[:12])
    with pytest.raises(cbc.CleanBuildContextError):
        cbc.build_clean_context(source_sha=sha[:12], output_dir=root / "_abbr", repo_dir=root,
                                expected_remote=EXPECTED_REMOTE, now_utc=NOW_UTC)


def test_clean_context_wrong_repo_rejected(temp_repo):
    root, sha = temp_repo
    with pytest.raises(cbc.CleanBuildContextError):
        cbc.verify_repo_identity(root, "git@github.com:someone/else.git")
    with pytest.raises(cbc.CleanBuildContextError):
        cbc.build_clean_context(source_sha=sha, output_dir=root / "_wr", repo_dir=root,
                                expected_remote="git@github.com:someone/else.git", now_utc=NOW_UTC)


def test_clean_context_non_commit_object_rejected(temp_repo):
    root, sha = temp_repo
    blob = _git(["rev-parse", "HEAD:keep.txt"], root).stdout.strip()  # a blob, not a commit
    with pytest.raises(cbc.CleanBuildContextError):
        cbc.verify_commit(blob, root)


def _tarinfo(name, *, typ=tarfile.REGTYPE, linkname=""):
    ti = tarfile.TarInfo(name=name)
    ti.type = typ
    ti.linkname = linkname
    return ti


def test_clean_context_path_traversal_rejected(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(cbc.CleanBuildContextError):
        cbc._validate_member(_tarinfo("../escape.txt"), dest)
    with pytest.raises(cbc.CleanBuildContextError):
        cbc._validate_member(_tarinfo("/abs/escape.txt"), dest)


def test_clean_context_symlink_escape_rejected(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(cbc.CleanBuildContextError):
        cbc._validate_member(_tarinfo("evil", typ=tarfile.SYMTYPE, linkname="../../etc/passwd"), dest)
    with pytest.raises(cbc.CleanBuildContextError):
        cbc._validate_member(_tarinfo("evil", typ=tarfile.SYMTYPE, linkname="/etc/passwd"), dest)


def test_clean_context_nested_git_rejected(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(cbc.CleanBuildContextError):
        cbc._validate_member(_tarinfo("sub/.git/config"), dest)


def test_clean_context_output_dir_refuses_host_global():
    with pytest.raises(cbc.CleanBuildContextError):
        cbc.validate_output_dir(Path("/etc/hermes_ctx"))
    with pytest.raises(cbc.CleanBuildContextError):
        cbc.validate_output_dir(Path("/usr/local/hermes_ctx"))


def test_clean_context_no_network_no_docker_build():
    src = Path(cbc.__file__).read_text()
    for forbidden in ("docker build", "urllib", "urlopen", "http://", "https://", "socket.socket", "requests."):
        assert forbidden not in src, forbidden


def test_tools_are_stdlib_only():
    stdlib = {
        "argparse", "datetime", "hashlib", "io", "json", "os", "re", "subprocess", "sys",
        "tarfile", "dataclasses", "pathlib", "typing", "__future__",
    }
    for mod in (cbc, lbl):
        tree = ast.parse(Path(mod.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert a.name.split(".")[0] in stdlib, a.name
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    assert (node.module or "").split(".")[0] in stdlib, node.module


# =============================================================================== 2. real build context
def test_real_build_context_passes_and_scans_clean(tmp_path):
    manifest = cbc.build_clean_context(
        source_sha=BASE_SHA, output_dir=tmp_path / "ctx", repo_dir=REPO,
        expected_remote=EXPECTED_REMOTE, now_utc=NOW_UTC,
    )
    assert manifest.result == "PASS"
    assert manifest.exported_file_count > 1000
    assert manifest.prohibited_findings == ()
    assert manifest.secret_findings == ()
    # findings never expose a value — only path + rule id fields exist.
    for f in list(manifest.prohibited_findings) + list(manifest.secret_findings):
        assert set(f.to_dict().keys()) == {"path", "rule_id"}


def test_real_manifest_validates_against_schema(tmp_path):
    manifest = cbc.build_clean_context(
        source_sha=BASE_SHA, output_dir=tmp_path / "ctx", repo_dir=REPO,
        expected_remote=EXPECTED_REMOTE, now_utc=NOW_UTC,
    )
    schema = json.loads((SCHEMAS / "build_context_manifest.v1.schema.json").read_text())
    jsonschema.Draft7Validator.check_schema(schema)
    jsonschema.validate(instance=manifest.to_dict(), schema=schema)


def test_manifest_round_trips(tmp_path):
    manifest = cbc.build_clean_context(
        source_sha=BASE_SHA, output_dir=tmp_path / "ctx", repo_dir=REPO,
        expected_remote=EXPECTED_REMOTE, now_utc=NOW_UTC,
    )
    blob = manifest.canonical_json()
    reparsed = json.loads(blob)
    assert reparsed["source_sha"] == BASE_SHA
    assert reparsed["manifest_checksum"] == manifest.manifest_checksum
    # No raw file contents leak into the manifest — only checksums/paths.
    assert "tracked content" not in blob


# =============================================================================== 3. .dockerignore
def _tracked_paths():
    out = subprocess.run(["git", "-C", str(REPO), "ls-tree", "-r", "--name-only", BASE_SHA],
                         capture_output=True, text=True, check=True).stdout
    return [p for p in out.splitlines() if p]


def _effective_included():
    di = (REPO / ".dockerignore").read_text()
    incl, _excl = cbc.apply_dockerignore(_tracked_paths(), di)
    return set(incl)


REQUIRED_INCLUSIONS = [
    "utils/hermes_shared_stream_recovery_v1.py",  # Phase-1 core
    "main.py",                                    # entrypoint
    "config.py", "env_config.py",                 # config loaders
    "utils/hermes_market_hours_health_v1.py",
    "config/market_hours_schedule.v1.json",
    "adapters/oanda.py", "adapters/base.py",
]


def test_dockerignore_keeps_required_production_files():
    incl = _effective_included()
    for req in REQUIRED_INCLUSIONS:
        assert req in incl, req
    sss = [p for p in _tracked_paths() if p.startswith("utils/hermes_sss_") and p.endswith("_v1.py")]
    assert len(sss) >= 10
    for m in sss:
        assert m in incl, m  # every Phase-2 module survives the build manifest


def test_dockerignore_excludes_tests_evidence_claude_jsonl():
    di = (REPO / ".dockerignore").read_text()
    synthetic = [
        ".claude/settings.json", "utils/.claude/x", "notes.jsonl", "a/b/c.jsonl",
        "tests/test_x.py", "ops/evidence/WO/x.md", "secret.pem", "id_rsa.key",
        "local.sqlite", "sub/.git/config", "worktrees/foo/main.py", ".venv/lib/x.py",
        "server.pid", "app.sock", ".DS_Store",
    ]
    incl, excl = cbc.apply_dockerignore(_tracked_paths() + synthetic, di)
    exset = set(excl)
    for p in synthetic:
        assert p in exset, p
    # real excluded categories present
    assert any(p.startswith("tests/") for p in excl)
    assert any(p.startswith("ops/evidence/") for p in excl)


def test_dockerignore_still_ships_env_example():
    di = (REPO / ".dockerignore").read_text()
    incl, excl = cbc.apply_dockerignore([".env", ".env.production", ".env.example"], di)
    assert ".env" in set(excl)
    assert ".env.production" in set(excl)
    assert ".env.example" in set(incl)  # whitelisted template still included


# =============================================================================== 4. OCI labels (F-DR-02)
FULL_SHA = "adc21c4dfef571debd0ef05bb22bc7af0b289ea3"
GOOD_LABELS = {
    lbl.LABEL_REVISION: FULL_SHA,
    lbl.LABEL_CREATED: "2026-07-18T12:00:00+00:00",
}


def test_labels_valid_full_sha_and_utc_accepted():
    r = lbl.verify_labels(GOOD_LABELS)
    assert r.ok is True
    assert r.source_sha == FULL_SHA
    assert r.checks["revision_present_and_valid"] and r.checks["created_present_and_valid"]


def test_labels_both_required():
    assert lbl.verify_labels({lbl.LABEL_REVISION: FULL_SHA}).ok is False
    assert lbl.verify_labels({lbl.LABEL_CREATED: "2026-07-18T12:00:00+00:00"}).ok is False
    assert lbl.verify_labels({}).ok is False
    assert lbl.verify_labels(None).ok is False


def test_labels_abbreviated_or_branch_or_latest_rejected():
    for bad in (FULL_SHA[:12], "latest", "main", "HEAD", ""):
        r = lbl.verify_labels({**GOOD_LABELS, lbl.LABEL_REVISION: bad})
        assert r.ok is False


def test_labels_naive_or_nonutc_created_rejected():
    for bad in ("2026-07-18T12:00:00", "2026-07-18T12:00:00+02:00", "not-a-date", ""):
        r = lbl.verify_labels({**GOOD_LABELS, lbl.LABEL_CREATED: bad})
        assert r.ok is False
    # Z-suffixed UTC accepted.
    assert lbl.verify_labels({**GOOD_LABELS, lbl.LABEL_CREATED: "2026-07-18T12:00:00Z"}).ok is True


def test_labels_missing_build_time_rejected():
    r = lbl.verify_labels({lbl.LABEL_REVISION: FULL_SHA, lbl.LABEL_CREATED: None})
    assert r.ok is False


def test_candidate_readiness_fails_without_labels():
    assert lbl.candidate_readiness({}).ok is False
    assert lbl.candidate_readiness(None).ok is False
    assert lbl.candidate_readiness(GOOD_LABELS).ok is True


def test_labels_expected_source_sha_mismatch_fails():
    r = lbl.verify_labels(GOOD_LABELS, expected_source_sha="0" * 40)
    assert r.ok is False
    assert r.checks["revision_matches_clean_context"] is False
    r2 = lbl.verify_labels(GOOD_LABELS, expected_source_sha=FULL_SHA)
    assert r2.ok is True


def test_label_compare_expected_vs_actual_mismatch_detected():
    actual = {**GOOD_LABELS, lbl.LABEL_REVISION: "b" * 40}
    mism = lbl.compare_expected_actual(GOOD_LABELS, actual)
    assert mism and any("revision" in m for m in mism)
    assert lbl.compare_expected_actual(GOOD_LABELS, GOOD_LABELS) == []


def test_labels_from_docker_inspect_shapes():
    arr = [{"Config": {"Labels": GOOD_LABELS}}]
    assert lbl.labels_from_docker_inspect(arr) == GOOD_LABELS
    assert lbl.labels_from_docker_inspect(json.dumps(arr)) == GOOD_LABELS
    assert lbl.labels_from_docker_inspect({"Labels": GOOD_LABELS}) == GOOD_LABELS


def test_legacy_image_not_retroactively_invalid():
    # Legacy image carries NO provenance labels; evaluated as non-candidate it is NOT a contract breach.
    r = lbl.verify_labels({}, is_candidate=False)
    assert r.ok is True
    assert r.is_candidate is False
    assert lbl.LEGACY_IMAGE_DIGEST == "c5fc2a62f424"
    # But the SAME empty labels as a CANDIDATE image fail closed.
    assert lbl.verify_labels({}, is_candidate=True).ok is False


def test_labels_no_env_fallback_invents_source_sha(monkeypatch):
    monkeypatch.setenv("SOURCE_SHA", FULL_SHA)
    monkeypatch.setenv("GIT_COMMIT", FULL_SHA)
    # No label present -> must still fail; the tool never reads env to invent a SHA.
    assert lbl.verify_labels({lbl.LABEL_CREATED: "2026-07-18T12:00:00Z"}).ok is False
    assert "os.environ" not in Path(lbl.__file__).read_text()
    assert "getenv" not in Path(lbl.__file__).read_text()


# =============================================================================== 5. Dockerfile identity
def test_dockerfile_runtime_only_adds_arg_and_label():
    txt = (REPO / "Dockerfile").read_text()
    assert 'ARG SOURCE_SHA' in txt
    assert 'ARG BUILD_UTC' in txt
    assert 'org.opencontainers.image.revision="${SOURCE_SHA}"' in txt
    assert 'org.opencontainers.image.created="${BUILD_UTC}"' in txt
    # Entrypoint / base / user / healthcheck unchanged.
    assert 'ENTRYPOINT ["/app/docker/entrypoint.sh"]' in txt
    assert 'CMD ["python", "main.py"]' in txt
    assert "FROM python:3.12-slim AS runtime" in txt
    assert "USER hermes" in txt
    assert "HEALTHCHECK" in txt


# =============================================================================== 6. design consistency
def _acceptance():
    return json.loads((MODELS / "acceptance_matrix.v1.json").read_text())


def _stage_model():
    return json.loads((MODELS / "deployment_stage_model.v1.json").read_text())


def _mapping():
    return json.loads((MODELS / "control_identifier_mapping.v1.json").read_text())


def test_architecture_labels_mandatory_no_stray_not_blocking():
    txt = ARCH.read_text()
    assert "MANDATORY" in txt
    assert "Stage B FAILS" in txt
    assert "NOT optional recommendations" in txt
    # Any 'not blocking' / 'non-blocking' statement must be SCOPED to the legacy image only.
    low = txt.lower()
    for needle in ("not blocking", "non-blocking"):
        start = 0
        while True:
            idx = low.find(needle, start)
            if idx == -1:
                break
            window = low[max(0, idx - 400): idx + 200]
            assert "legacy" in window or "c5fc2a62f424" in window, f"unscoped '{needle}' near: {window}"
            start = idx + len(needle)


def test_control_identifier_mapping_is_canonical_and_complete():
    m = _mapping()
    ids = {c["control_id"] for c in m["controls"]}
    assert ids == {"F-DR-01", "F-DR-02"}
    assert m["no_duplicate_identifiers"] is True
    by = {c["control_id"]: c for c in m["controls"]}
    assert "T-DOCKERIGNORE" in by["F-DR-01"]["acceptance_tests"]
    assert "T-SBOM-SCAN" in by["F-DR-02"]["acceptance_tests"]
    assert by["F-DR-01"]["blocking"] is True and by["F-DR-02"]["blocking"] is True
    for c in m["controls"]:
        assert "legacy" in c["legacy_scope"].lower()


def test_acceptance_matrix_maps_controls():
    tests = {t["id"]: t for t in _acceptance()["tests"]}
    assert tests["T-DOCKERIGNORE"]["control_id"] == "F-DR-01"
    assert tests["T-SBOM-SCAN"]["control_id"] == "F-DR-02"
    for tid in ("T-CLEAN-CONTEXT", "T-BUILD-MANIFEST", "T-SECRET-SCAN", "T-PROHIBITED-PATH",
                "T-OCI-LABELS", "T-SOCKET-AMBIGUITY", "T-HISTORICAL-INFERENCE"):
        assert tid in tests, tid
        assert tests[tid]["implemented_here"] is True


def test_stage_b_blocks_when_labels_or_clean_context_absent():
    sm = _stage_model()
    stage_b = next(s for s in sm["stages"] if s["id"] == "B")
    assert set(stage_b["mandatory_controls"]) == {"F-DR-01", "F-DR-02"}
    assert "FAILS" in stage_b["blocking_controls_note"]
    inv_blob = " ".join(sm["invariants"]).lower()
    assert "stage b is blocked" in inv_blob
    assert "not retroactively invalid" in inv_blob


def test_no_stage_before_F_enables_shadow_and_no_redis_sql():
    sm = _stage_model()
    ids = [s["id"] for s in sm["stages"]]
    f = ids.index("F")
    for s in sm["stages"][:f]:
        assert s["shadow_enabled"] is False, s["id"]
    for s in sm["stages"]:
        assert s["consumer_live"] is False, s["id"]
        assert s["introduces_redis_or_sql"] is False, s["id"]
    assert sm.get("phase3_in_scope") is False


def test_no_redis_or_sql_dependency_added_by_this_wo():
    for tool_file in (cbc.__file__, lbl.__file__):
        src = Path(tool_file).read_text().lower()
        for banned in ("import redis", "pymysql", "sqlalchemy", "import sqlite3", "aioredis"):
            assert banned not in src, banned


# =============================================================================== 7. socket-ambiguity gate
def test_socket_connected_is_not_transport_health(july16):
    cur = _july16_current(july16)
    # connected + heartbeat fresh + FX flowing: TRANSPORT_HEALTHY yet reconnect NOT authorised — socket
    # connectivity alone did not force an authorisation.
    _s, env, klass = _evaluate(july16["snapshot"], cur)
    assert env.socket_connected is True
    assert env.transport_state == "TRANSPORT_HEALTHY"
    assert env.reconnect_authorised is False
    assert klass == ComparisonClass.SHADOW_DENIES_CURRENT_RECONNECT


def test_socket_connected_unavailable_heartbeat_is_incomplete(july16):
    cur = _july16_current(july16)
    d = copy.deepcopy(july16["snapshot"])
    d["heartbeat_available"] = False
    d["evidence_completeness"] = "INCOMPLETE"
    res = _run_shadow(d, cur)
    assert res.comparison_class == "EVIDENCE_INCOMPLETE"
    assert res.reconnect_authorised_shadow is False


def test_socket_connected_stale_shared_progress_not_healthy(july16):
    cur = _july16_current(july16)
    d = copy.deepcopy(july16["snapshot"])
    d["heartbeat_age_s"] = 60.0
    d["shared_progress_age_s"] = 60.0
    _s, env, _k = _evaluate(d, cur)
    assert env.socket_connected is True
    assert env.transport_state != "TRANSPORT_HEALTHY"
    assert env.reconnect_authorised is False  # stale != healthy, and not authorised as-if-proven


def test_socket_connected_auth_failure_is_fault_governed(july16):
    cur = _july16_current(july16)
    d = copy.deepcopy(july16["snapshot"])
    d["auth_failure"] = True
    _s, env, _k = _evaluate(d, cur)
    assert env.transport_state == "AUTH_FAILED"  # authority from the fault, not from connectivity


def test_socket_connected_contradictory_evidence_fail_closed(july16):
    cur = _july16_current(july16)
    d = copy.deepcopy(july16["snapshot"])
    d["contradictory_evidence"] = [{"claim": "connected_healthy", "conflict": "shared_silent"}]
    res = _run_shadow(d, cur)
    assert res.comparison_class == "EVIDENCE_CONFLICT"
    assert res.reconnect_authorised_shadow is False


def test_socket_alone_cannot_authorise_via_executor(july16):
    # There is NO reconnect executor in the shadow path; the only stand-in refuses ANY record.
    res = _run_shadow(july16["snapshot"], _july16_current(july16))
    executor = RefusingShadowExecutor()
    with pytest.raises(ShadowExecutionForbidden):
        executor.refuse(res.record)


# =============================================================================== 8. historical-inference gate
def test_inference_gate_strict_unavailable_heartbeat_denies_reconnect(july16):
    """STRICT: July-16 heartbeat unavailable -> evidence incomplete -> reconnect unauthorised, with NO
    silently-inferred heartbeat substituted."""
    cur = _july16_current(july16)
    d = copy.deepcopy(july16["snapshot"])
    d["heartbeat_available"] = False
    d["heartbeat_age_s"] = None
    d["evidence_completeness"] = "INCOMPLETE"
    # mark provenance honest: heartbeat is UNAVAILABLE (not inferred healthy)
    for p in d["field_provenance"]:
        if p["field"] in ("heartbeat_available", "heartbeat_age_s"):
            p["observation"] = "UNAVAILABLE"
            p["availability"] = "NOT_CURRENTLY_AVAILABLE"
    res = _run_shadow(d, cur)
    assert res.comparison_class == "EVIDENCE_INCOMPLETE"
    assert res.reconnect_authorised_shadow is False


def test_inference_gate_permitted_preserves_justified_inference(july16):
    """INFERENCE-permitted: JUSTIFIED_INFERENCE preserved through the full pipeline; shadow proposal-only;
    comparison SHADOW_DENIES_CURRENT_RECONNECT."""
    cur = _july16_current(july16)
    snap, env, klass = _evaluate(july16["snapshot"], cur)
    prov = {p.field: p for p in snap.field_provenance}
    assert prov["heartbeat_available"].observation == Observation.JUSTIFIED_INFERENCE
    assert prov["heartbeat_age_s"].observation == Observation.JUSTIFIED_INFERENCE
    assert prov["socket_state"].observation == Observation.DIRECTLY_OBSERVED
    assert env.action == "RECOVERY_PROPOSAL_ONLY"
    assert env.reconnect_authorised is False
    assert klass == ComparisonClass.SHADOW_DENIES_CURRENT_RECONNECT


def test_inferred_never_serialises_as_directly_observed(july16):
    snap = snapshot_from_dict(july16["snapshot"])
    sd = snap.to_dict()
    obs = {p["field"]: p["observation"] for p in sd["field_provenance"]}
    assert obs["heartbeat_available"] == "JUSTIFIED_INFERENCE"
    assert obs["heartbeat_age_s"] == "JUSTIFIED_INFERENCE"
    # The inferred fields must NOT be serialised as DIRECTLY_OBSERVED anywhere in their provenance.
    for p in sd["field_provenance"]:
        if p["field"] in ("heartbeat_available", "heartbeat_age_s"):
            assert p["observation"] != "DIRECTLY_OBSERVED"


def _inference_relabelled_as_observation(snapshot_dict) -> bool:
    """Gate mechanism: return True if a genuinely-inferred heartbeat field has been relabelled as
    DIRECTLY_OBSERVED (a dishonest promotion of inference to observation)."""
    snap = snapshot_from_dict(snapshot_dict)
    for p in snap.field_provenance:
        if p.field in ("heartbeat_available", "heartbeat_age_s") and p.observation == Observation.DIRECTLY_OBSERVED:
            return True
    return False


def test_inference_gate_rejects_relabelling_inference_as_observation(july16):
    # Honest fixture: no relabelling.
    assert _inference_relabelled_as_observation(july16["snapshot"]) is False
    # Dishonest variant: promote inferred heartbeat to DIRECTLY_OBSERVED -> the gate detects it.
    bad = copy.deepcopy(july16["snapshot"])
    for p in bad["field_provenance"]:
        if p["field"] in ("heartbeat_available", "heartbeat_age_s"):
            p["observation"] = "DIRECTLY_OBSERVED"
    assert _inference_relabelled_as_observation(bad) is True


# =============================================================================== 9. regression / import guards
_RUNTIME_FILES = ["main.py", "signal_builder.py", "config.py", "env_config.py",
                  "utils/watchdog.py", "adapters/oanda.py", "adapters/base.py"]
_PHASE2_NAMES = ("hermes_sss_", "hermes_shared_stream_recovery")


def test_no_runtime_path_imports_phase2():
    for rel in _RUNTIME_FILES:
        path = REPO / rel
        if not path.exists():
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert not any(n in a.name for n in _PHASE2_NAMES), f"{rel} imports {a.name}"
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert not any(n in mod for n in _PHASE2_NAMES), f"{rel} imports {mod}"


def test_phase1_and_phase2_modules_import_cleanly():
    # already imported at module top; assert they are the inert merged modules.
    assert hasattr(core, "decide")
    assert callable(map_snapshot_to_recovery_input)
    assert issubclass(ShadowExecutionForbidden, Exception)
