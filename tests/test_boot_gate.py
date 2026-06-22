"""Boot-gate contract tests for docker/entrypoint.sh.
WO-HERMES-OPS-BOOT-GATE-ENFORCEMENT.

Proves the HARD gate: if resource_cap_verify.py exits non-zero the entrypoint aborts with RC=101,
prints the GOV diagnostic mapped from the verifier's exit code (001/002/003), and NEVER execs the app;
on PASS it exec's the command. Hermetic — a stub cap-verifier run with cwd=app; no container/cgroups.
"""
import os
import shutil
import subprocess
import tempfile

_ENTRY = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "docker", "entrypoint.sh"))


def _run(stub_body):
    app = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(app, "ops", "staging"))
        with open(os.path.join(app, "ops", "staging", "resource_cap_verify.py"), "w") as fh:
            fh.write(stub_body)
        return subprocess.run(["sh", _ENTRY, "echo", "APP-BOOTED"],
                              capture_output=True, text=True, cwd=app)
    finally:
        shutil.rmtree(app, ignore_errors=True)


def test_boot_gate_aborts_rc101_and_maps_exit2_to_cap002():
    # verifier exit 2 -> GOV-STAGE-CAP-002 diagnostic (the misdiagnosis bug this fix closes)
    r = _run("import sys; sys.exit(2)\n")
    assert r.returncode == 101, r.stderr
    assert "GOV-STAGE-CAP-002" in r.stderr
    assert "GOV-STAGE-CAP-001" not in r.stderr      # must NOT mislabel a -002 as -001
    assert "APP-BOOTED" not in r.stdout


def test_boot_gate_execs_command_on_pass():
    r = _run("print('RESULT: PASS')\n")  # exit 0
    assert r.returncode == 0, r.stderr
    assert "APP-BOOTED" in r.stdout
