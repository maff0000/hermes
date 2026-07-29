"""C-WP2-SECRET-FILE-UNBOUNDED-PATH correction — adversarial secret-file path-boundary tests.

WO-HELM-HERMES-CONTAINER-MVP-WP2-PR125-SECRET-FILE-PATH-BOUNDARY-CORRECTION-0001.
Authority: HELM. Created (UTC): 2026-07-29. Contract version: 1.

R2D2 (AMBER, C-WP2-SECRET-FILE-UNBOUNDED-PATH) demonstrated the pre-correction loader read /etc/passwd and
/proc/self/environ by execution. These tests prove the corrected loader confines every secret file BENEATH
the configured HERMES_SECRET_ROOT, rejects non-regular / symlink-escaping / oversized / pseudo-fs / cross-app
targets, fails closed with distinct non-secret codes, and never leaks the secret value into logs or exceptions.
All fixtures are synthetic tmp files — NO real secret is read.
"""
from __future__ import annotations

import os
import socket
import stat as _stat
import importlib

import pytest

import env_config as ec


# --------------------------------------------------------------------------- fixtures / helpers
@pytest.fixture
def secret_root(tmp_path, monkeypatch):
    root = tmp_path / "secrets"
    root.mkdir()
    monkeypatch.setenv("HERMES_SECRET_ROOT", str(root))
    # env_config reads {ENV}_HERMES_SECRET_ROOT then HERMES_SECRET_ROOT; clear the prefixed one.
    monkeypatch.delenv(f"{ec.ENV}_HERMES_SECRET_ROOT", raising=False)
    return root


def _write(p, content="s3cr3t\n", mode=0o600):
    p.write_text(content)
    os.chmod(p, mode)
    return p


# =========================================================================== ALLOWED cases
def test_allowed_file_directly_under_root(secret_root):
    f = _write(secret_root / "oanda_api_key")
    assert ec.load_secret_file(str(f)) == "s3cr3t"


def test_allowed_nested_file_under_root(secret_root):
    (secret_root / "sub").mkdir()
    f = _write(secret_root / "sub" / "db_password")
    assert ec.load_secret_file(str(f)) == "s3cr3t"


def test_allowed_relative_reference_resolved_under_root(secret_root):
    _write(secret_root / "webhook")
    # a RELATIVE _FILE value is resolved beneath the root
    assert ec.load_secret_file("webhook") == "s3cr3t"


def test_allowed_read_only_file(secret_root):
    f = _write(secret_root / "ro", mode=0o400)
    assert ec.load_secret_file(str(f)) == "s3cr3t"


def test_trailing_newline_trimmed(secret_root):
    f = _write(secret_root / "nl", content="value\n")
    assert ec.load_secret_file(str(f)) == "value"


def test_docker_secret_conventional_shape(secret_root):
    # /run/secrets is the default; here the root is redirected to tmp but the shape (flat name) matches.
    f = _write(secret_root / "OANDA_API_KEY")
    assert ec.load_secret_file(str(f)) == "s3cr3t"


# =========================================================================== ROOT ENFORCEMENT (the CVE)
@pytest.mark.parametrize("target", [
    "/etc/passwd",
    "/etc/shadow",
    "/proc/self/environ",
    "/proc/self/cmdline",
    "/proc/1/environ",
    "/sys/kernel/hostname",
    "/dev/null",
    "/srv-dev/tradingProteus/ares/.env",
    "/root/.ssh/id_rsa",
])
def test_arbitrary_host_path_rejected(secret_root, target):
    with pytest.raises(ValueError) as ei:
        ec.load_secret_file(target)
    msg = str(ei.value)
    assert any(c in msg for c in (
        "SECRET-PATH-OUTSIDE-ROOT", "SECRET-SPECIAL-FS", "SECRET-CROSS-APP-PATH")), msg
    # the file's CONTENT must never appear in the exception
    assert "root:" not in msg and "PATH=" not in msg


def test_parent_traversal_rejected(secret_root):
    with pytest.raises(ValueError) as ei:
        ec.load_secret_file(str(secret_root / ".." / ".." / "etc" / "passwd"))
    assert "SECRET-PATH-OUTSIDE-ROOT" in str(ei.value)


def test_relative_traversal_escaping_root_rejected(secret_root):
    with pytest.raises(ValueError) as ei:
        ec.load_secret_file("../../etc/passwd")
    assert "SECRET-PATH-OUTSIDE-ROOT" in str(ei.value)


def test_absolute_path_outside_root_rejected(secret_root, tmp_path):
    outside = _write(tmp_path / "outside_secret")
    with pytest.raises(ValueError) as ei:
        ec.load_secret_file(str(outside))
    assert "SECRET-PATH-OUTSIDE-ROOT" in str(ei.value)


def test_cross_app_ares_path_rejected(secret_root):
    with pytest.raises(ValueError) as ei:
        ec.load_secret_file("/srv-dev/tradingProteus/ares/.env")
    assert ("SECRET-CROSS-APP-PATH" in str(ei.value)) or ("SECRET-SPECIAL-FS" in str(ei.value))


# --- root misconfiguration ---
def test_root_relative_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_SECRET_ROOT", "relative/dir")
    monkeypatch.delenv(f"{ec.ENV}_HERMES_SECRET_ROOT", raising=False)
    with pytest.raises(ValueError) as ei:
        ec.load_secret_file("x")
    assert "SECRET-ROOT-RELATIVE" in str(ei.value)


def test_root_missing_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_SECRET_ROOT", str(tmp_path / "does_not_exist"))
    monkeypatch.delenv(f"{ec.ENV}_HERMES_SECRET_ROOT", raising=False)
    with pytest.raises(ValueError) as ei:
        ec.load_secret_file("x")
    assert "SECRET-ROOT-MISSING" in str(ei.value)


def test_root_is_a_file_fails(monkeypatch, tmp_path):
    rootfile = _write(tmp_path / "rootfile")
    monkeypatch.setenv("HERMES_SECRET_ROOT", str(rootfile))
    monkeypatch.delenv(f"{ec.ENV}_HERMES_SECRET_ROOT", raising=False)
    with pytest.raises(ValueError) as ei:
        ec.load_secret_file(str(rootfile))
    assert "SECRET-ROOT-NOT-DIRECTORY" in str(ei.value)


# =========================================================================== FILE TYPES
def test_directory_rejected(secret_root):
    d = secret_root / "adir"
    d.mkdir()
    with pytest.raises(ValueError) as ei:
        ec.load_secret_file(str(d))
    assert "SECRET-FILE-NOT-REGULAR" in str(ei.value)


def test_fifo_rejected(secret_root):
    fifo = secret_root / "afifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError) as ei:
        ec.load_secret_file(str(fifo))
    assert "SECRET-FILE-NOT-REGULAR" in str(ei.value)


def test_socket_rejected(secret_root):
    sockpath = secret_root / "asock"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(sockpath))
    try:
        with pytest.raises(ValueError) as ei:
            ec.load_secret_file(str(sockpath))
        # a socket either fails to open (UNREADABLE) or fstat rejects it (NOT-REGULAR) — both fail-closed
        assert any(c in str(ei.value) for c in ("SECRET-FILE-NOT-REGULAR", "SECRET-FILE-UNREADABLE"))
    finally:
        s.close()


def test_symlink_to_outside_file_rejected(secret_root, tmp_path):
    outside = _write(tmp_path / "outside")
    link = secret_root / "link_out"
    os.symlink(str(outside), str(link))
    with pytest.raises(ValueError) as ei:
        ec.load_secret_file(str(link))
    # realpath collapses the symlink to its outside target -> containment rejects it
    assert "SECRET-PATH-OUTSIDE-ROOT" in str(ei.value)


def test_symlink_to_etc_passwd_rejected(secret_root):
    link = secret_root / "passwd_link"
    os.symlink("/etc/passwd", str(link))
    with pytest.raises(ValueError) as ei:
        ec.load_secret_file(str(link))
    assert "SECRET-PATH-OUTSIDE-ROOT" in str(ei.value)


def test_symlink_to_proc_rejected(secret_root):
    link = secret_root / "environ_link"
    os.symlink("/proc/self/environ", str(link))
    with pytest.raises(ValueError) as ei:
        ec.load_secret_file(str(link))
    assert ("SECRET-SPECIAL-FS" in str(ei.value)) or ("SECRET-PATH-OUTSIDE-ROOT" in str(ei.value))


def test_parent_directory_symlink_escape_rejected(secret_root, tmp_path):
    # secret_root/evil -> /etc ; then read secret_root/evil/passwd
    (tmp_path / "elsewhere").mkdir()
    _write(tmp_path / "elsewhere" / "passwd", content="pwned")
    os.symlink(str(tmp_path / "elsewhere"), str(secret_root / "evil"))
    with pytest.raises(ValueError) as ei:
        ec.load_secret_file(str(secret_root / "evil" / "passwd"))
    assert "SECRET-PATH-OUTSIDE-ROOT" in str(ei.value)


def test_symlink_within_root_to_allowed_file_ok(secret_root):
    real = _write(secret_root / "real_secret")
    link = secret_root / "alias"
    os.symlink(str(real), str(link))
    # realpath collapses the symlink to its target, which is a regular file BENEATH the root -> allowed.
    # (The symlink cannot be an escape vector because containment is enforced on the resolved realpath;
    #  O_NOFOLLOW only guards a TOCTOU swap of the final component after resolution.)
    assert ec.load_secret_file(str(link)) == "s3cr3t"


def test_procfs_absolute_rejected(secret_root):
    with pytest.raises(ValueError) as ei:
        ec.load_secret_file("/proc/self/environ")
    assert ("SECRET-SPECIAL-FS" in str(ei.value)) or ("SECRET-PATH-OUTSIDE-ROOT" in str(ei.value))


# --- size / content ---
def test_oversized_file_rejected(secret_root):
    big = secret_root / "big"
    big.write_text("A" * (ec.MAX_SECRET_FILE_BYTES + 100))
    os.chmod(big, 0o600)
    with pytest.raises(ValueError) as ei:
        ec.load_secret_file(str(big))
    assert "SECRET-FILE-TOO-LARGE" in str(ei.value)


def test_at_limit_file_ok(secret_root):
    exact = secret_root / "exact"
    exact.write_text("B" * ec.MAX_SECRET_FILE_BYTES)
    os.chmod(exact, 0o600)
    assert ec.load_secret_file(str(exact)) == "B" * ec.MAX_SECRET_FILE_BYTES


def test_zero_byte_file_rejected(secret_root):
    z = secret_root / "zero"
    z.write_text("")
    os.chmod(z, 0o600)
    with pytest.raises(ValueError) as ei:
        ec.load_secret_file(str(z))
    assert "SECRET-FILE-EMPTY" in str(ei.value)


def test_whitespace_only_file_rejected(secret_root):
    w = _write(secret_root / "ws", content="   \n\t  \n")
    with pytest.raises(ValueError) as ei:
        ec.load_secret_file(str(w))
    assert "SECRET-FILE-EMPTY" in str(ei.value)


def test_missing_file_under_root_rejected(secret_root):
    with pytest.raises(ValueError) as ei:
        ec.load_secret_file(str(secret_root / "nope"))
    assert "SECRET-FILE-UNREADABLE" in str(ei.value)


# =========================================================================== RACE / mutation (best-effort)
def test_truncation_then_grows_bounded(secret_root):
    # a file that is exactly at limit reads OK; if it were larger it fails — the read is bounded to MAX+1
    # so a file that grows past the fstat size still cannot exceed the bound.
    f = secret_root / "grow"
    f.write_text("C" * ec.MAX_SECRET_FILE_BYTES)
    os.chmod(f, 0o600)
    assert len(ec.load_secret_file(str(f))) <= ec.MAX_SECRET_FILE_BYTES


# =========================================================================== SOURCE CONFLICT via get_secret
def test_get_secret_direct_plus_file_conflict(secret_root, monkeypatch):
    _write(secret_root / "mysec")
    monkeypatch.setenv("MYSEC_FILE", str(secret_root / "mysec"))
    monkeypatch.setenv("MYSEC", "PLAINTEXTVAL9")
    with pytest.raises(ValueError) as ei:
        ec.get_secret("MYSEC")
    assert "SECRET-SOURCE-CONFLICT" in str(ei.value)
    # the two SECRET VALUES must not appear in the conflict message (only the SOURCE names)
    assert "PLAINTEXTVAL9" not in str(ei.value) and "s3cr3t" not in str(ei.value)


def test_get_secret_file_only(secret_root, monkeypatch):
    _write(secret_root / "fileonly")
    monkeypatch.setenv("MYSEC2_FILE", str(secret_root / "fileonly"))
    monkeypatch.delenv("MYSEC2", raising=False)
    assert ec.get_secret("MYSEC2") == "s3cr3t"


def test_get_secret_direct_only(monkeypatch):
    monkeypatch.setenv("MYSEC3", "plain")
    monkeypatch.delenv("MYSEC3_FILE", raising=False)
    assert ec.get_secret("MYSEC3") == "plain"


def test_get_secret_optional_absent(monkeypatch):
    monkeypatch.delenv("MYSEC4", raising=False)
    monkeypatch.delenv("MYSEC4_FILE", raising=False)
    assert ec.get_secret("MYSEC4", default="dflt") == "dflt"


def test_get_secret_required_missing(monkeypatch):
    monkeypatch.delenv("MYSEC5", raising=False)
    monkeypatch.delenv("MYSEC5_FILE", raising=False)
    with pytest.raises(ValueError) as ei:
        ec.get_secret("MYSEC5", required=True)
    assert "SECRET-REQUIRED-MISSING" in str(ei.value)


def test_get_secret_file_points_at_passwd_rejected(secret_root, monkeypatch):
    # a caller/operator setting *_FILE to /etc/passwd must NOT disclose it
    monkeypatch.setenv("MYSEC6_FILE", "/etc/passwd")
    monkeypatch.delenv("MYSEC6", raising=False)
    with pytest.raises(ValueError) as ei:
        ec.get_secret("MYSEC6")
    assert any(c in str(ei.value) for c in ("SECRET-PATH-OUTSIDE-ROOT", "SECRET-SPECIAL-FS"))


# =========================================================================== LEAKAGE
def test_secret_value_never_in_logs(secret_root, caplog):
    import logging
    f = _write(secret_root / "leaky", content="TOP-SECRET-XYZ\n")
    with caplog.at_level(logging.DEBUG):
        assert ec.load_secret_file(str(f)) == "TOP-SECRET-XYZ"
    assert "TOP-SECRET-XYZ" not in caplog.text


def test_secret_value_never_in_exception(secret_root):
    # oversized file whose content is a "secret" — the content must not surface in the error
    big = secret_root / "bigleak"
    big.write_text("SENSITIVE" * 2000)
    os.chmod(big, 0o600)
    with pytest.raises(ValueError) as ei:
        ec.load_secret_file(str(big))
    assert "SENSITIVE" not in str(ei.value)


def test_module_constants_present():
    assert ec.DEFAULT_SECRET_ROOT == "/run/secrets"
    assert ec.MAX_SECRET_FILE_BYTES == 8192
