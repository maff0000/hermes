"""Boot-gate contract tests for docker/entrypoint.sh.
WO-HERMES-OPS-BOOT-GATE-ENFORCEMENT.

Proves the HARD gate: if resource_cap_verify.py exits non-zero (GOV-STAGE-CAP-00x) the entrypoint
aborts with RC=101, surfaces the diagnostic to stderr, and NEVER execs the app; on PASS it exec's the
command. Hermetic — a stub cap-verifier + a `python`->interpreter shim; no container, no real cgroups.
"""
import os
import shutil
import subprocess
import sys
import tempfile

_ENTRY = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "docker", "entrypoint.sh"))


def _run(stub_body):
    app = tempfile.mkdtemp()
    shim = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(app, "ops", "staging"))
        with open(os.path.join(app, "ops", "staging", "resource_cap_verify.py"), "w") as fh:
            fh.write(stub_body)
        os.symlink(sys.executable, os.path.join(shim, "python"))  # entrypoint calls `python`
        env = dict(os.environ, APP_HOME=app, PATH=shim + os.pathsep + os.environ["PATH"])
        return subprocess.run(["sh", _ENTRY, "echo", "APP-BOOTED"],
                              capture_output=True, text=True, env=env)
    finally:
        shutil.rmtree(app, ignore_errors=True)
        shutil.rmtree(shim, ignore_errors=True)


def test_boot_gate_aborts_rc101_on_cap_failure():
    r = _run("import sys\nprint('GOV-STAGE-CAP-001: mem drift', file=sys.stderr)\nsys.exit(1)\n")
    assert r.returncode == 101, r.stderr
    assert "GOV-STAGE-CAP-001" in r.stderr        # exact diagnostic surfaced
    assert "ABORT" in r.stderr
    assert "APP-BOOTED" not in r.stdout            # never fell through to the app


def test_boot_gate_execs_command_on_pass():
    r = _run("print('RESULT: PASS')\n")
    assert r.returncode == 0, r.stderr
    assert "APP-BOOTED" in r.stdout                # exec passthrough reached the CMD
    assert "PASS" in r.stderr
