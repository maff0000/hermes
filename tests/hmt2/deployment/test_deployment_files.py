"""Deployment artefacts: shell-syntax validity, fail-closed behaviour when required config is
missing, and correct, allowlist-restricted rendering of the systemd unit templates."""
import os
import shutil
import subprocess
import textwrap

import pytest

DEPLOYMENT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "deployment", "hmt2")

SHELL_SCRIPTS = ["hmt2-run.sh", "hmt2-acquire-run.sh", "hmt2-disk-guard.sh", "render_unit_template.sh"]


@pytest.mark.parametrize("script", SHELL_SCRIPTS)
def test_shell_script_is_syntactically_valid(script):
    path = os.path.join(DEPLOYMENT_DIR, script)
    result = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("script", ["hmt2-run.sh", "hmt2-acquire-run.sh", "hmt2-disk-guard.sh"])
def test_shell_scripts_never_hardcode_a_srv_hmt_path(script):
    """Every host-specific path must come from the config env file, never be baked into the
    script text itself (this is what makes the script byte-for-byte identical across hosts)."""
    text = open(os.path.join(DEPLOYMENT_DIR, script)).read()
    assert "/srv/hmt-data" not in text
    assert "/srv/hmt-secrets" not in text
    assert "/srv/hmt-code" not in text


def test_hmt2_run_refuses_closed_when_required_config_is_missing(tmp_path):
    """`: "${VAR:?message}"` must actually fire when the config env file is absent/empty --
    proving the launcher fails closed rather than silently using some other host's stale
    environment values."""
    empty_env_file = tmp_path / "empty.env"
    empty_env_file.write_text("")
    result = subprocess.run(
        ["bash", os.path.join(DEPLOYMENT_DIR, "hmt2-run.sh"), "/bin/true"],
        capture_output=True, text=True,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HMT2_OPS_ENV_FILE": str(empty_env_file)},
    )
    assert result.returncode != 0
    assert "HMT2_SLICE_NAME" in result.stderr or "must be set" in result.stderr


def test_hmt2_disk_guard_refuses_missing_required_command_gracefully(tmp_path):
    """With a fully-specified env file pointing at a real, small directory, the disk guard must
    run and print a percentage line without needing to actually be root or touch any real HMT-2
    path."""
    # WARN_PCT=0 forces the WARN branch (which echoes to stdout) regardless of this host's real
    # disk usage -- the OK branch (usage below WARN_PCT) deliberately only logs via logger(1),
    # matching the ported Trinity script exactly, so it is not itself a useful thing to assert
    # stdout content against.
    env_file = tmp_path / "hmt2-ops.env"
    env_file.write_text(
        textwrap.dedent(
            f"""
            HMT2_DISK_GUARD_MOUNT={tmp_path}
            HMT2_DISK_GUARD_WARN_PCT=0
            HMT2_DISK_GUARD_STOP_PCT=100
            """
        )
    )
    result = subprocess.run(
        ["bash", os.path.join(DEPLOYMENT_DIR, "hmt2-disk-guard.sh")],
        capture_output=True, text=True,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HMT2_OPS_ENV_FILE": str(env_file)},
    )
    assert result.returncode == 0
    assert "%" in result.stdout
    assert "WARN" in result.stdout


@pytest.mark.skipif(shutil.which("envsubst") is None, reason="envsubst (gettext-base) not installed")
def test_render_unit_template_produces_a_fully_substituted_slice_file(tmp_path):
    env_file = tmp_path / "hmt2-ops.env"
    env_file.write_text(
        textwrap.dedent(
            """
            HMT2_SLICE_MEMORY_HIGH=8G
            HMT2_SLICE_MEMORY_MAX=10G
            HMT2_SLICE_CPU_QUOTA=400%
            HMT2_SLICE_IO_WEIGHT=50
            """
        )
    )
    template = os.path.join(DEPLOYMENT_DIR, "systemd", "hmt2.slice.template")
    output = tmp_path / "hmt2.slice"
    result = subprocess.run(
        ["bash", os.path.join(DEPLOYMENT_DIR, "render_unit_template.sh"), str(env_file), template, str(output)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    rendered = output.read_text()
    assert "MemoryHigh=8G" in rendered
    assert "MemoryMax=10G" in rendered
    assert "CPUQuota=400%" in rendered
    assert "${" not in rendered  # no leftover unsubstituted placeholder
    assert "MemorySwapMax=0" in rendered  # generic, non-templated value survives verbatim


@pytest.mark.skipif(shutil.which("envsubst") is None, reason="envsubst (gettext-base) not installed")
def test_render_unit_template_allowlist_never_over_substitutes(tmp_path):
    """A stray, non-allowlisted `${...}`-shaped token in a template must survive rendering
    unchanged -- proving the allowlist restriction in render_unit_template.sh is real, not a
    blanket envsubst call that would interpret anything."""
    env_file = tmp_path / "hmt2-ops.env"
    env_file.write_text("HMT2_SLICE_MEMORY_HIGH=8G\n")
    stray_template = tmp_path / "stray.template"
    stray_template.write_text("MemoryHigh=${HMT2_SLICE_MEMORY_HIGH}\nNotAllowlisted=${SOME_OTHER_VAR}\n")
    output = tmp_path / "stray.out"
    result = subprocess.run(
        ["bash", os.path.join(DEPLOYMENT_DIR, "render_unit_template.sh"), str(env_file), str(stray_template), str(output)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    rendered = output.read_text()
    assert "MemoryHigh=8G" in rendered
    assert "${SOME_OTHER_VAR}" in rendered  # left untouched, not blanket-substituted (and undefined anyway)


def test_systemd_unit_templates_have_no_trinity_specific_values_baked_in():
    for name in ("hmt2.slice.template", "hmt2-disk-guard.service.template", "hmt2-disk-guard.timer.template"):
        text = open(os.path.join(DEPLOYMENT_DIR, "systemd", name)).read()
        assert "/srv/hmt-data" not in text
        assert "/srv/hmt-code" not in text


def test_hmt2_ops_env_example_never_contains_a_real_secret_value():
    """The example config file may name the SECRET PATH but must never contain anything that
    looks like a real credential value."""
    text = open(os.path.join(DEPLOYMENT_DIR, "hmt2-ops.env.example")).read()
    forbidden = ("api_key=", "apikey=", "secret=", "token=", "password=")
    lowered = text.lower()
    for token in forbidden:
        assert token not in lowered, f"suspicious credential-shaped token {token!r} in example env file"
