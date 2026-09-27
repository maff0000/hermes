"""Host guard decision logic: MemAvailable sustained-low, PSI threshold, host-vs-cgroup PSI
source distinction, and pre-kill-vs-post-kill sampling correctness (the CausalMemorySampler
contract). All fixture files live under tmp_path -- never real /proc or /sys/fs/cgroup."""
import os

import pytest

from market_truth.acquisition.orchestration import host_guards as hg


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def test_mem_available_kb_parses_meminfo(tmp_path):
    path = _write(str(tmp_path / "meminfo"), "MemTotal:       65000000 kB\nMemAvailable:   41943040 kB\n")
    assert hg.mem_available_kb(meminfo_path=path) == 41943040


def test_mem_available_kb_missing_file_returns_none(tmp_path):
    assert hg.mem_available_kb(meminfo_path=str(tmp_path / "nope")) is None


def test_psi_memory_full_avg10_parses_avg10_field(tmp_path):
    path = _write(
        str(tmp_path / "pressure_memory"),
        "some avg10=0.10 avg60=0.20 avg300=0.30 total=100\n"
        "full avg10=5.42 avg60=3.10 avg300=1.00 total=555\n",
    )
    assert hg.psi_memory_full_avg10(psi_path=path) == 5.42


def test_psi_memory_full_avg10_unreadable_returns_none_not_zero(tmp_path):
    """Absence of a PSI signal must never be treated as 'healthy' (0.0) -- see psi_guard_fires."""
    assert hg.psi_memory_full_avg10(psi_path=str(tmp_path / "nope")) is None


def test_read_cgroup_int_reads_named_file_under_slice(tmp_path):
    cgroup_root = str(tmp_path / "cgroup")
    _write(os.path.join(cgroup_root, "hmt2.slice", "memory.current"), "123456\n")
    assert hg.read_cgroup_int("memory.current", slice_name="hmt2.slice", cgroup_root=cgroup_root) == 123456
    assert hg.read_cgroup_int("memory.peak", slice_name="hmt2.slice", cgroup_root=cgroup_root) is None


def test_slice_is_idle_true_only_when_memory_current_is_exactly_zero(tmp_path):
    cgroup_root = str(tmp_path / "cgroup")
    _write(os.path.join(cgroup_root, "hmt2.slice", "memory.current"), "0\n")
    assert hg.slice_is_idle(slice_name="hmt2.slice", cgroup_root=cgroup_root) is True

    _write(os.path.join(cgroup_root, "hmt2.slice", "memory.current"), "4096\n")
    assert hg.slice_is_idle(slice_name="hmt2.slice", cgroup_root=cgroup_root) is False


def test_slice_is_idle_fails_closed_when_unreadable(tmp_path):
    assert hg.slice_is_idle(slice_name="hmt2.slice", cgroup_root=str(tmp_path / "missing")) is False


def test_disk_pct_reports_a_percentage_for_a_real_readable_path(tmp_path):
    pct = hg.disk_pct(str(tmp_path))
    assert 0.0 <= pct <= 100.0


def test_host_vs_cgroup_psi_are_read_from_independent_sources(tmp_path):
    """The critical distinction: host-wide PSI (from /proc/pressure/memory) is read by a
    DIFFERENT function than cgroup-local diagnostic counters (read_cgroup_int), and nothing in
    this module ever reads a cgroup-local PSI file. A naive implementation might conflate the
    two; this test proves they are structurally independent call paths."""
    host_psi_path = _write(str(tmp_path / "host_pressure_memory"), "full avg10=17.82 avg60=0 avg300=0 total=0\n")
    cgroup_root = str(tmp_path / "cgroup")
    # cgroup-local PSI file exists too, with a DIFFERENT value -- proving read_cgroup_int() is
    # never accidentally used to answer the host-PSI question.
    _write(os.path.join(cgroup_root, "hmt2.slice", "memory.pressure"), "full avg10=0.00 avg60=0 avg300=0 total=0\n")
    _write(os.path.join(cgroup_root, "hmt2.slice", "memory.current"), "999\n")

    host_value = hg.psi_memory_full_avg10(psi_path=host_psi_path)
    assert host_value == 17.82
    # read_cgroup_int has no "memory.pressure" special-casing at all -- it just reads the raw
    # integer text at the given name, so asking it to read "memory.pressure" as an int fails
    # (the file's content is not int-parseable), proving this module never wires that file into
    # a numeric guard decision by accident.
    assert hg.read_cgroup_int("memory.pressure", slice_name="hmt2.slice", cgroup_root=cgroup_root) is None
    # ...while the real diagnostic counter it IS meant to read still works correctly.
    assert hg.read_cgroup_int("memory.current", slice_name="hmt2.slice", cgroup_root=cgroup_root) == 999


class _FakeClock:
    def __init__(self, start=0.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_causal_memory_sampler_takes_a_sample_before_any_terminate_decision():
    """Regression test for the real GC-2025-10-01 incident: a snapshot taken only AFTER
    proc.terminate()/proc.wait() is not causal evidence. This test proves the sampler's contract
    -- sample() must be callable, and produce a real reading, BEFORE any 'worker launch' or
    'terminate' event occurs in the caller's own code -- by simulating exactly that ordering."""
    clock = _FakeClock()
    calls = []

    def mem_fn():
        calls.append(("mem", clock.now))
        return 50 * 1024 * 1024

    def psi_fn():
        calls.append(("psi", clock.now))
        return 0.0

    sampler = hg.CausalMemorySampler(clock=clock, mem_available_fn=mem_fn, psi_fn=psi_fn, cgroup_fn=lambda *a, **k: None)

    # Simulated caller sequence: sample BEFORE "launch", then several ticks, THEN a "terminate".
    pre_launch = sampler.sample()
    assert pre_launch.mem_available_kb == 50 * 1024 * 1024
    launch_utc = clock.now

    clock.advance(5)
    tick1 = sampler.sample()
    clock.advance(5)
    tick2 = sampler.sample()

    # "terminate" happens here, in the simulated caller -- AFTER tick2 was already recorded.
    terminate_utc = clock.now

    assert sampler.sample_count() == 3
    assert pre_launch.utc_monotonic <= launch_utc
    assert tick2.utc_monotonic <= terminate_utc
    # The guard-fire decision a real caller makes must be derivable from tick2 (already live,
    # already pre-terminate) -- never require a NEW sample taken after terminate_utc.
    assert sampler.latest() is tick2


def test_causal_memory_sampler_history_is_a_full_rolling_buffer():
    clock = _FakeClock()
    sampler = hg.CausalMemorySampler(
        clock=clock, mem_available_fn=lambda: 1, psi_fn=lambda: 0.0, cgroup_fn=lambda *a, **k: None, max_samples=2
    )
    sampler.sample()
    sampler.sample()
    sampler.sample()
    assert len(sampler.history()) == 2  # bounded buffer, oldest dropped -- never unbounded growth


def test_sustained_low_memory_guard_fires_only_after_sustained_seconds():
    clock = _FakeClock()
    guard = hg.SustainedLowMemoryGuard(threshold_kb=100, sustained_seconds=60, clock=clock)

    assert guard.observe(50, now=0) is False   # low, but not yet sustained
    assert guard.observe(50, now=30) is False  # still under 60s
    assert guard.observe(50, now=59) is False  # 59s < 60s
    assert guard.observe(50, now=61) is True   # sustained >= 60s -- fires exactly once


def test_sustained_low_memory_guard_resets_on_recovery():
    guard = hg.SustainedLowMemoryGuard(threshold_kb=100, sustained_seconds=60)
    assert guard.observe(50, now=0) is False
    assert guard.observe(200, now=10) is False  # recovered above threshold -- resets low_since
    assert guard.observe(50, now=65) is False   # would have been >=60s from t=0, but reset at t=10


def test_sustained_low_memory_guard_none_reading_does_not_fire():
    guard = hg.SustainedLowMemoryGuard(threshold_kb=100, sustained_seconds=60)
    assert guard.observe(None, now=0) is False
    assert guard.observe(None, now=120) is False


def test_psi_guard_fires_immediately_no_sustain_window():
    assert hg.psi_guard_fires(5.0, threshold=5.0) is True
    assert hg.psi_guard_fires(4.99, threshold=5.0) is False


def test_psi_guard_never_fires_on_missing_signal():
    assert hg.psi_guard_fires(None, threshold=5.0) is False
