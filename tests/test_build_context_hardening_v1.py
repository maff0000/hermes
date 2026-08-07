"""Build-context secret-leak containment + clean-source build hardening.
WO-HELM-HERMES-BUILD-CONTEXT-SECRET-LEAK-CONTAINMENT-AND-CLEAN-SOURCE-BUILD-HARDENING-0001.

Infra-free: pure scanner/classify logic, canary fail-closed config, source assertions, and a LOAD-BEARING clean-context
git-archive isolation regression proving an untracked nested `healthcheck/canary/.env` cannot enter the exact-SHA context.
"""
import importlib.util
import os
import pathlib
import subprocess
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, _ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


scan = _load("ops/build/hermes_image_secret_scan_v1.py", "hermes_image_secret_scan_v1")


# ----------------------------------------------------------------- image secret scanner
@pytest.mark.parametrize("path,cls", [
    ("app/healthcheck/canary/.env", "KNOWN_CANARY_ENV"),
    ("app/foo/bar/.env", "NESTED_ENV"),
    ("app/.env", "NESTED_ENV"),
    ("app/config/.env.production", "NESTED_ENV_SUFFIXED"),
    ("app/keys/server.pem", "PRIVATE_KEY"),
    ("app/secret/id_rsa", "SSH_KEY"),
    ("app/home/.ssh/known_hosts", "SSH_DIR"),
    ("app/.netrc", "NETRC"),
])
def test_scanner_classifies_prohibited_artefacts(path, cls):
    assert scan.classify(path) == cls


@pytest.mark.parametrize("path", [
    "app/.env.example", "app/ops/staging/staging.env.example",
    "app/deploy/advanced_v1/dark.env.template", "app/deploy/advanced_v1/deployment.env.template",
    "app/healthcheck/canary/canary.env.template", "app/main.py", "app/utils/x.py",
])
def test_scanner_allows_templates_examples_and_source(path):
    assert scan.classify(path) is None


def test_scanner_member_scan_rejects_canary_env():
    ok, findings = scan.scan_member_names(["app/main.py", "app/healthcheck/canary/.env", "app/.env.example"])
    assert not ok
    assert any(f["class"] == "KNOWN_CANARY_ENV" and f["verdict"] == "REJECT" for f in findings)


def test_scanner_member_scan_clean_passes():
    ok, findings = scan.scan_member_names(["app/main.py", "app/.env.example", "app/deploy/advanced_v1/dark.env.template"])
    assert ok and findings == []


# ----------------------------------------------------------------- canary fail-closed config
canary = _load("healthcheck/canary/hermes_signal_truth_canary.py", "hermes_signal_truth_canary")
_CANARY_KEYS = ["CANARY_ENABLED", "CANARY_INSTRUMENT", "CANARY_CHECK_INTERVAL_SECONDS",
                "CANARY_M1_STALE_THRESHOLD_SECONDS", "CANARY_SIGNAL_TRUTH_STALE_THRESHOLD_SECONDS",
                "CANARY_DB_HOST", "CANARY_DB_PORT", "CANARY_DB_USER", "CANARY_DB_PASSWORD", "CANARY_DB_NAME"]


def _clear_env(monkeypatch):
    for k in _CANARY_KEYS:
        monkeypatch.delenv(k, raising=False)
        monkeypatch.delenv("HERMES_" + k, raising=False)


def test_canary_fail_closed_when_env_missing(monkeypatch, capsys):
    _clear_env(monkeypatch)
    with pytest.raises(SystemExit) as e:
        canary.load_config()
    assert e.value.code == 1
    assert "HERMES_CANARY_CONFIG_MISSING" in capsys.readouterr().out


def test_canary_loads_from_hermes_prefixed_env(monkeypatch):
    _clear_env(monkeypatch)
    vals = {"CANARY_ENABLED": "true", "CANARY_INSTRUMENT": "XAU_USD", "CANARY_CHECK_INTERVAL_SECONDS": "60",
            "CANARY_M1_STALE_THRESHOLD_SECONDS": "180", "CANARY_SIGNAL_TRUTH_STALE_THRESHOLD_SECONDS": "600",
            "CANARY_DB_HOST": "db.example", "CANARY_DB_PORT": "3307", "CANARY_DB_USER": "hermes_canary",
            "CANARY_DB_PASSWORD": "DUMMY_TEST_PW", "CANARY_DB_NAME": "tradingSignals"}
    for k, v in vals.items():
        monkeypatch.setenv("HERMES_" + k, v)
    cfg = canary.load_config()
    assert cfg["CANARY_DB_HOST"] == "db.example" and cfg["CANARY_DB_PORT"] == 3307 and cfg["CANARY_DB_USER"] == "hermes_canary"


def test_canary_does_not_read_a_local_env_file():
    src = (_ROOT / "healthcheck/canary/hermes_signal_truth_canary.py").read_text()
    # the nearby-.env auto-load (the leak vector) must be gone; no fallback to a shared estate account
    assert "os.path.dirname(os.path.abspath(__file__)), \".env\"" not in src
    assert "open(env_path)" not in src
    assert "trinity" not in src.lower()


# ----------------------------------------------------------------- .dockerignore defence-in-depth
def test_dockerignore_excludes_nested_env_and_allows_templates():
    di = (_ROOT / ".dockerignore").read_text()
    assert "**/.env" in di and "**/.env.*" in di
    assert "!**/*.env.example" in di and "!**/*.env.template" in di


# ----------------------------------------------------------------- wrapper builds from clean context
def test_wrapper_builds_from_exact_sha_clean_context_only():
    w = (_ROOT / "ops/build/build_production_candidate.sh").read_text()
    active = "\n".join(l for l in w.splitlines() if not l.lstrip().startswith("#"))
    assert "tools/hermes_clean_build_context_v1.py" in active            # uses the clean-context tool
    assert 'docker build --target runtime -f "$CTX/Dockerfile"' in active  # builds from the exported context
    assert "docker compose build" not in active                         # no mutable-checkout compose build (active)
    assert "hermes_image_secret_scan_v1.py" in active                   # image secret gate wired
    assert "canary/.env" in active                                      # explicit canary sentinel in manifest check


# ----------------------------------------------------------------- LOAD-BEARING clean-context isolation regression
def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def test_clean_context_excludes_untracked_nested_canary_env(tmp_path):
    """§11/§26 load-bearing: an untracked healthcheck/canary/.env (+ a dummy secret) in the worktree must NOT enter the
    exact-SHA clean context produced by the tool (git archive = tracked-only)."""
    # build a tiny throwaway git repo with a tracked Dockerfile + an UNTRACKED nested canary .env and a dummy secret
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    _git("remote", "add", "origin", "git@github.com:maff0000/hermes.git", cwd=repo)
    (repo / "Dockerfile").write_text("FROM scratch\n")
    (repo / "main.py").write_text("print('hi')\n")
    _git("add", "Dockerfile", "main.py", cwd=repo)
    _git("commit", "-qm", "init", cwd=repo)
    sha = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()
    # UNTRACKED nested secret files (the leak vectors)
    (repo / "healthcheck" / "canary").mkdir(parents=True)
    (repo / "healthcheck" / "canary" / ".env").write_text("CANARY_DB_PASSWORD=DUMMY_UNTRACKED_SECRET_SENTINEL\n")
    (repo / "untracked_dummy.env").write_text("SECRET=DUMMY_UNTRACKED_SECRET_SENTINEL\n")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "x").write_text("agent cruft\n")

    out = tmp_path / "ctx"
    manifest = tmp_path / "m.json"
    r = subprocess.run([sys.executable, str(_ROOT / "tools/hermes_clean_build_context_v1.py"),
                        "--source-sha", sha, "--output-dir", str(out), "--repo-dir", str(repo),
                        "--manifest-out", str(manifest)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    # exported context contains ONLY tracked files; untracked secrets absent
    exported = {str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()}
    assert "Dockerfile" in exported and "main.py" in exported
    assert "healthcheck/canary/.env" not in exported
    assert "untracked_dummy.env" not in exported
    assert not any(p.startswith(".claude") for p in exported)
    # the dummy secret sentinel must not appear anywhere in the exported context
    blob = "\n".join((out / p).read_text(errors="ignore") for p in exported)
    assert "DUMMY_UNTRACKED_SECRET_SENTINEL" not in blob
