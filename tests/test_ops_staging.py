"""cgroup v2 parser + cap-assertion coverage for the staging resource verifier.
WO-HERMES-STAGING-CAP-PARSER-TESTS (PR #36 follow-up).

Mocks the filesystem reads for /sys/fs/cgroup/{memory,cpu,pids}.max with valid, mismatched, and
unparseable strings to prove `resource_cap_verify.assert_caps()` reliably flags failures with
GOV-STAGE-CAP-001 (drift/uncapped), -002 (unreadable/unparseable), -003 (configured cap missing).
No real cgroups, no container, pure stdlib.
"""
import importlib.util
import os

_SPEC = importlib.util.spec_from_file_location(
    "resource_cap_verify",
    os.path.join(os.path.dirname(__file__), "..", "ops", "staging", "resource_cap_verify.py"))
rcv = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rcv)

# 4 vCore / 4 GiB / 512 pids — the IONOS M+ target the compose pins.
_GIB4 = 4 * 1024 ** 3
_CONFIGURED_OK = {"cpus": 4.0, "mem_bytes": _GIB4, "pids": 512}


def _patch_reads(monkeypatch, *, mem, cpu, pids):
    """Mock rcv._read so each cgroup path returns the supplied string (or raises if an Exception)."""
    table = {rcv.CG_MEM_MAX: mem, rcv.CG_CPU_MAX: cpu, rcv.CG_PIDS_MAX: pids}

    def fake_read(path):
        val = table.get(path)
        if isinstance(val, Exception):
            raise val
        if val is None:
            raise OSError(f"no such cgroup file: {path}")
        return val

    monkeypatch.setattr(rcv, "_read", fake_read)


# ------------------------------------------------------------------ pure parse helpers -------------
def test_parse_helpers():
    assert rcv.parse_mem_to_bytes("4g") == _GIB4
    assert rcv.parse_cpus("4") == 4.0
    # cpu.max '<quota> <period>' -> cores
    assert rcv.effective_cpus.__doc__  # sanity: helper present


# ------------------------------------------------------------------ effective readers --------------
def test_effective_readers_valid(monkeypatch):
    _patch_reads(monkeypatch, mem=str(_GIB4), cpu="400000 100000", pids="512")
    assert rcv.effective_mem_bytes() == _GIB4
    assert rcv.effective_cpus() == 4.0
    assert rcv.effective_pids() == 512


def test_effective_unlimited_is_none(monkeypatch):
    _patch_reads(monkeypatch, mem="max", cpu="max 100000", pids="max")
    assert rcv.effective_mem_bytes() is None
    assert rcv.effective_cpus() is None
    assert rcv.effective_pids() is None


# ------------------------------------------------------------------ GOV-STAGE-CAP-001 (drift) ------
def test_valid_matching_passes(monkeypatch):
    _patch_reads(monkeypatch, mem=str(_GIB4), cpu="400000 100000", pids="512")
    lines = rcv.assert_caps(_CONFIGURED_OK)
    assert any("PASS" in l for l in lines) and len(lines) == 3


def test_mismatched_memory_flags_001(monkeypatch):
    _patch_reads(monkeypatch, mem=str(2 * 1024 ** 3), cpu="400000 100000", pids="512")  # 2 GiB vs 4
    try:
        rcv.assert_caps(_CONFIGURED_OK); assert False
    except rcv.CapFailure as e:
        assert e.code == rcv.GOV_STAGE_CAP_MISMATCH


def test_mismatched_cpu_flags_001(monkeypatch):
    _patch_reads(monkeypatch, mem=str(_GIB4), cpu="200000 100000", pids="512")  # 2 cores vs 4
    try:
        rcv.assert_caps(_CONFIGURED_OK); assert False
    except rcv.CapFailure as e:
        assert e.code == rcv.GOV_STAGE_CAP_MISMATCH


def test_uncapped_max_flags_001(monkeypatch):
    _patch_reads(monkeypatch, mem="max", cpu="400000 100000", pids="512")  # mem uncapped
    try:
        rcv.assert_caps(_CONFIGURED_OK); assert False
    except rcv.CapFailure as e:
        assert e.code == rcv.GOV_STAGE_CAP_MISMATCH and "UNLIMITED" in e.message


# ------------------------------------------------------------------ GOV-STAGE-CAP-002 (read/parse) -
def test_unparseable_memory_flags_002(monkeypatch):
    _patch_reads(monkeypatch, mem="potato", cpu="400000 100000", pids="512")
    try:
        rcv.assert_caps(_CONFIGURED_OK); assert False
    except rcv.CapFailure as e:
        assert e.code == rcv.GOV_STAGE_CAP_READ


def test_unparseable_cpu_flags_002(monkeypatch):
    _patch_reads(monkeypatch, mem=str(_GIB4), cpu="garbage data", pids="512")
    try:
        rcv.assert_caps(_CONFIGURED_OK); assert False
    except rcv.CapFailure as e:
        assert e.code == rcv.GOV_STAGE_CAP_READ


def test_unreadable_cgroup_flags_002(monkeypatch):
    _patch_reads(monkeypatch, mem=OSError("EACCES"), cpu="400000 100000", pids="512")
    try:
        rcv.assert_caps(_CONFIGURED_OK); assert False
    except rcv.CapFailure as e:
        assert e.code == rcv.GOV_STAGE_CAP_READ


# ------------------------------------------------------------------ GOV-STAGE-CAP-003 (no config) --
def test_missing_configured_cap_flags_003(monkeypatch):
    _patch_reads(monkeypatch, mem=str(_GIB4), cpu="400000 100000", pids="512")
    try:
        rcv.assert_caps({"cpus": None, "mem_bytes": _GIB4, "pids": 512}); assert False
    except rcv.CapFailure as e:
        assert e.code == rcv.GOV_STAGE_CAP_PARSE


# ------------------------------------------------------------------ main() exit-code contract -------
# WO-HERMES-OPS-BOOT-GATE-ENFORCEMENT: main() exit code ENCODES the GOV class (001->1, 002->2, 003->3)
# so the boot gate (docker/entrypoint.sh) maps the failure precisely from the return code.
def _main_exit(monkeypatch, *, mem, cpu, pids, argv):
    _patch_reads(monkeypatch, mem=mem, cpu=cpu, pids=pids)
    for v in ("HERMES_CPUS", "HERMES_MEM_LIMIT", "HERMES_PIDS_LIMIT"):
        monkeypatch.delenv(v, raising=False)
    return rcv.main(argv)


_OK_ARGV = ["--cpus", "4", "--mem", "4g", "--pids", "512", "--compose-file", "/nonexistent-compose.yml"]


def test_main_exit0_on_match(monkeypatch):
    assert _main_exit(monkeypatch, mem=str(_GIB4), cpu="400000 100000", pids="512", argv=_OK_ARGV) == 0


def test_main_exit1_on_mismatch(monkeypatch):  # GOV-STAGE-CAP-001
    assert _main_exit(monkeypatch, mem=str(2 * 1024 ** 3), cpu="400000 100000", pids="512", argv=_OK_ARGV) == 1


def test_main_exit2_on_unparseable(monkeypatch):  # GOV-STAGE-CAP-002
    assert _main_exit(monkeypatch, mem="potato", cpu="400000 100000", pids="512", argv=_OK_ARGV) == 2


def test_main_exit3_on_missing_config(monkeypatch):  # GOV-STAGE-CAP-003 (no --pids, compose absent)
    argv = ["--cpus", "4", "--mem", "4g", "--compose-file", "/nonexistent-compose.yml"]
    assert _main_exit(monkeypatch, mem=str(_GIB4), cpu="400000 100000", pids="512", argv=argv) == 3
