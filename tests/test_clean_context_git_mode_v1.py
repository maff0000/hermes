"""Clean-context Git-mode preservation (exact-SHA provenance includes executable intent).
WO-HELM-HERMES-CLEAN-CONTEXT-GIT-MODE-PRESERVATION-AND-REAL-ENTRYPOINT-REHEARSAL-0001.

Behavioural (not source-string) infra-free tests: build a throwaway repo with tracked 100755 + 100644 files, export
the exact-SHA clean context via the tool, and prove executable intent is preserved. Includes the negative that
reproduces the original dropped-exec-bit defect (validate_export_modes / entrypoint check turn RED)."""
import importlib.util
import os
import pathlib
import subprocess
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hermes_clean_build_context_v1", _ROOT / "tools/hermes_clean_build_context_v1.py")
cbc = importlib.util.module_from_spec(spec)
sys.modules["hermes_clean_build_context_v1"] = cbc   # required so dataclass field-type resolution works
spec.loader.exec_module(cbc)


def _git(*a, cwd):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True)


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git("init", "-q", cwd=r)
    _git("config", "user.email", "t@t", cwd=r)
    _git("config", "user.name", "t", cwd=r)
    _git("remote", "add", "origin", "git@github.com:maff0000/hermes.git", cwd=r)
    (r / "Dockerfile").write_text("FROM scratch\n")
    (r / "docker").mkdir()
    (r / "docker" / "entrypoint.sh").write_text("#!/bin/sh\nexec \"$@\"\n")
    (r / "ops").mkdir()
    (r / "ops" / "tool.sh").write_text("#!/bin/sh\necho hi\n")
    (r / "main.py").write_text("print('x')\n")               # non-exec source
    (r / "config.yml").write_text("a: 1\n")                   # non-exec data
    # stage with explicit exec bits: entrypoint + ops/tool executable; main.py/config.yml non-exec
    _git("add", "Dockerfile", "main.py", "config.yml", cwd=r)
    _git("add", "--chmod=+x", "docker/entrypoint.sh", "ops/tool.sh", cwd=r)
    _git("commit", "-qm", "init", cwd=r)
    sha = _git("rev-parse", "HEAD", cwd=r).stdout.strip()
    # confirm git authority
    modes = cbc.tracked_modes(sha, r)
    assert modes["docker/entrypoint.sh"] == "100755" and modes["ops/tool.sh"] == "100755"
    assert modes["main.py"] == "100644" and modes["config.yml"] == "100644"
    return r, sha


def _export(tmp_path, repo, sha, name="ctx"):
    out = tmp_path / name
    cbc.build_clean_context(source_sha=sha, output_dir=out, repo_dir=repo)
    return out


def test_100755_preserved_and_100644_not_executable(tmp_path, repo):
    r, sha = repo
    out = _export(tmp_path, r, sha)
    assert os.access(out / "docker/entrypoint.sh", os.X_OK)
    assert os.access(out / "ops/tool.sh", os.X_OK)
    assert not os.access(out / "main.py", os.X_OK)
    assert not os.access(out / "config.yml", os.X_OK)


def test_entrypoint_is_executable_helper(tmp_path, repo):
    r, sha = repo
    out = _export(tmp_path, r, sha)
    assert cbc.entrypoint_is_executable(out) is True


def test_validate_export_modes_green_when_preserved(tmp_path, repo):
    r, sha = repo
    out = _export(tmp_path, r, sha)
    assert cbc.validate_export_modes(sha, r, out) == []


def test_manifest_carries_mode_provenance(tmp_path, repo):
    r, sha = repo
    out = tmp_path / "ctx2"
    man = cbc.build_clean_context(source_sha=sha, output_dir=out, repo_dir=r)
    files = {f["path"]: f for f in man.to_dict()["files"]}
    assert files["docker/entrypoint.sh"]["git_mode"] == "100755"
    assert files["docker/entrypoint.sh"]["exported_executable"] is True
    assert files["main.py"]["git_mode"] == "100644"
    assert files["main.py"]["exported_executable"] is False


def test_negative_reproduces_original_defect(tmp_path, repo):
    """Simulate the dropped-exec-bit defect: strip the entrypoint's exec bit in the export -> validators turn RED."""
    r, sha = repo
    out = _export(tmp_path, r, sha, name="ctx_broken")
    os.chmod(out / "docker/entrypoint.sh", 0o644)          # <- the original defect
    findings = cbc.validate_export_modes(sha, r, out)
    assert any(f["path"] == "docker/entrypoint.sh" for f in findings), "defect not detected"
    assert cbc.entrypoint_is_executable(out) is False


def test_no_all_files_executable_regression(tmp_path, repo):
    r, sha = repo
    out = _export(tmp_path, r, sha, name="ctx3")
    # a 100644 file made executable would be a regression the validator must catch
    os.chmod(out / "main.py", 0o755)
    findings = cbc.validate_export_modes(sha, r, out)
    assert any(f["path"] == "main.py" and f["expected_executable"] == "False" for f in findings)
