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
    os.chmod(root, 0o700)  # owner-only: satisfies the no-group/world-write root policy
    monkeypatch.setenv("HERMES_SECRET_ROOT", str(root))
    # env_config reads {ENV}_HERMES_SECRET_ROOT then HERMES_SECRET_ROOT; clear the prefixed one.
    monkeypatch.delenv(f"{ec.ENV}_HERMES_SECRET_ROOT", raising=False)
    # POSITIVE allow-list is a MODULE CONSTANT — inject this HERMES-owned test root in-process (never via
    # the environment). This is exactly how a governed alternative root would be declared in the contract.
    monkeypatch.setattr(ec, "_AUTHORISED_SECRET_ROOTS",
                        ec._AUTHORISED_SECRET_ROOTS + (os.path.realpath(str(root)),))
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


# =========================================================================== POSITIVE SECRET-ROOT POLICY
# WO-HELM-HERMES-CONTAINER-MVP-WP2-PR125-POSITIVE-SECRET-ROOT-POLICY-CORRECTION-0001
# C-WP2-SECRET-ROOT-BROAD-ROOT-ACCEPTED: the root must be a governed HERMES secret mount, not an arbitrary
# absolute directory. A broad/system/user/cross-app root must fail closed at root resolution.

def _set_root(monkeypatch, root):
    monkeypatch.setenv("HERMES_SECRET_ROOT", root)
    monkeypatch.delenv(f"{ec.ENV}_HERMES_SECRET_ROOT", raising=False)


@pytest.mark.parametrize("badroot,code", [
    ("/", "SECRET-ROOT-SYSTEM-PATH"),
    ("/etc", "SECRET-ROOT-SYSTEM-PATH"),
    ("/root", "SECRET-ROOT-SYSTEM-PATH"),
    ("/home", "SECRET-ROOT-SYSTEM-PATH"),
    ("/usr", "SECRET-ROOT-SYSTEM-PATH"),
    ("/var", "SECRET-ROOT-SYSTEM-PATH"),
    ("/opt", "SECRET-ROOT-SYSTEM-PATH"),
    ("/bin", "SECRET-ROOT-SYSTEM-PATH"),
    ("/boot", "SECRET-ROOT-SYSTEM-PATH"),
    ("/tmp", "SECRET-ROOT-SYSTEM-PATH"),
    ("/var/tmp", "SECRET-ROOT-SYSTEM-PATH"),
    ("/run", "SECRET-ROOT-SYSTEM-PATH"),
    ("/var/run", "SECRET-ROOT-SYSTEM-PATH"),
    ("/proc", "SECRET-ROOT-SYSTEM-PATH"),
    ("/sys", "SECRET-ROOT-SYSTEM-PATH"),
    ("/dev", "SECRET-ROOT-SYSTEM-PATH"),
    ("/srv", "SECRET-ROOT-CROSS-APPLICATION"),
    ("/srv-dev", "SECRET-ROOT-CROSS-APPLICATION"),
    ("/srv-dev/tradingProteus", "SECRET-ROOT-CROSS-APPLICATION"),
    ("/srv-dev/tradingProteus/ares", "SECRET-ROOT-CROSS-APPLICATION"),
])
def test_broad_root_rejected(monkeypatch, badroot, code):
    _set_root(monkeypatch, badroot)
    with pytest.raises(ValueError) as ei:
        ec.load_secret_file(badroot + "/whatever")
    msg = str(ei.value)
    # The security property: the root is REJECTED at resolution (never accepted, never a read). Which
    # SECRET-ROOT-* code fires can vary by host (e.g. /bin or /var/run may be a symlink -> SECRET-ROOT-
    # SYMLINK; /srv-dev may be absent -> SECRET-ROOT-MISSING) — all are fail-closed denials. It must NOT
    # be a SECRET-FILE-* / SECRET-PATH-* code (those would mean the root was accepted).
    assert "SECRET-ROOT-" in msg, msg
    # the classified code is preferred where the path exists and is not a symlink
    if badroot in ("/", "/etc", "/root", "/home", "/usr", "/var", "/opt", "/tmp", "/proc", "/sys", "/dev"):
        assert any(c in msg for c in (code, "SECRET-ROOT-SYMLINK", "SECRET-ROOT-MISSING")), msg


def test_root_etc_cannot_read_passwd(monkeypatch):
    _set_root(monkeypatch, "/etc")
    with pytest.raises(ValueError) as ei:
        ec.load_secret_file("/etc/passwd")
    assert "SECRET-ROOT" in str(ei.value)  # rejected at ROOT resolution, before any read
    assert "root:" not in str(ei.value)    # zero content leak


def test_root_root_cannot_read_bashrc(monkeypatch):
    _set_root(monkeypatch, "/root")
    with pytest.raises(ValueError) as ei:
        ec.load_secret_file("/root/.bashrc")
    assert "SECRET-ROOT" in str(ei.value)


def test_root_slash_cannot_read_arbitrary(monkeypatch):
    _set_root(monkeypatch, "/")
    with pytest.raises(ValueError) as ei:
        ec.load_secret_file("/etc/hostname")
    assert "SECRET-ROOT" in str(ei.value)


def test_default_root_is_run_secrets_and_authorised(monkeypatch):
    # unset -> default /run/secrets; it is in the positive allow-list (resolution only fails on MISSING if
    # the mount is absent, NOT on authorisation).
    monkeypatch.delenv("HERMES_SECRET_ROOT", raising=False)
    monkeypatch.delenv(f"{ec.ENV}_HERMES_SECRET_ROOT", raising=False)
    assert "/run/secrets" in ec._AUTHORISED_SECRET_ROOTS
    try:
        ec._resolve_secret_root()
    except ValueError as e:
        # on a host without the mount this is MISSING (authorised but absent) — never NOT-AUTHORISED
        assert "SECRET-ROOT-MISSING" in str(e), str(e)


def test_governed_alternative_root_under_authorised_parent(monkeypatch, tmp_path):
    # a directory BENEATH an authorised parent is accepted (simulated by injecting the parent in-process)
    parent = tmp_path / "hermes" / "secrets"
    child = parent / "app1"
    child.mkdir(parents=True)
    os.chmod(child, 0o700)
    monkeypatch.setattr(ec, "_AUTHORISED_SECRET_ROOTS",
                        ec._AUTHORISED_SECRET_ROOTS + (os.path.realpath(str(parent)),))
    _set_root(monkeypatch, str(child))
    f = _write(child / "key")
    assert ec.load_secret_file(str(f)) == "s3cr3t"


def test_world_writable_root_rejected(monkeypatch, tmp_path):
    root = tmp_path / "wwsecrets"
    root.mkdir()
    os.chmod(root, 0o777)  # world-writable
    monkeypatch.setattr(ec, "_AUTHORISED_SECRET_ROOTS",
                        ec._AUTHORISED_SECRET_ROOTS + (os.path.realpath(str(root)),))
    _set_root(monkeypatch, str(root))
    with pytest.raises(ValueError) as ei:
        ec.load_secret_file(str(root / "k"))
    assert "SECRET-ROOT-UNSAFE-PERMISSIONS" in str(ei.value)


def test_group_writable_root_rejected(monkeypatch, tmp_path):
    root = tmp_path / "gwsecrets"
    root.mkdir()
    os.chmod(root, 0o770)  # group-writable
    monkeypatch.setattr(ec, "_AUTHORISED_SECRET_ROOTS",
                        ec._AUTHORISED_SECRET_ROOTS + (os.path.realpath(str(root)),))
    _set_root(monkeypatch, str(root))
    with pytest.raises(ValueError) as ei:
        ec.load_secret_file(str(root / "k"))
    assert "SECRET-ROOT-UNSAFE-PERMISSIONS" in str(ei.value)


def test_symlink_root_rejected(monkeypatch, tmp_path):
    real = tmp_path / "realsecrets"
    real.mkdir(); os.chmod(real, 0o700)
    link = tmp_path / "linksecrets"
    os.symlink(str(real), str(link))
    monkeypatch.setattr(ec, "_AUTHORISED_SECRET_ROOTS",
                        ec._AUTHORISED_SECRET_ROOTS + (os.path.realpath(str(real)),))
    _set_root(monkeypatch, str(link))
    with pytest.raises(ValueError) as ei:
        ec.load_secret_file(str(link / "k"))
    assert "SECRET-ROOT-SYMLINK" in str(ei.value)


def test_unauthorised_but_safe_dir_rejected(monkeypatch, tmp_path):
    # a perfectly ordinary, owner-only directory that is simply NOT in the allow-list -> NOT-AUTHORISED
    d = tmp_path / "randomdir"
    d.mkdir(); os.chmod(d, 0o700)
    _set_root(monkeypatch, str(d))
    with pytest.raises(ValueError) as ei:
        ec.load_secret_file(str(d / "k"))
    assert "SECRET-ROOT-NOT-AUTHORISED" in str(ei.value)
