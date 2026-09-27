"""market_truth.acquisition.orchestration.host_guards -- HMT-2 host-wide resource guard primitives.

Ports the host-guard read primitives and guard-decision logic that lived, undocumented and
untested, inside Trinity's `hmt2_canonical_root_orchestrator.py`. Every function here is a pure
read (or a pure decision over previously-read values) so it can be fully unit-tested by pointing
it at a disposable fixture file/directory instead of the real `/proc` or `/sys/fs/cgroup`.

CRITICAL DISTINCTION -- HOST vs CGROUP-LOCAL, read carefully before touching this file:

  * `mem_available_kb()` and `psi_memory_full_avg10()` read HOST-WIDE state
    (`/proc/meminfo`, `/proc/pressure/memory`). These, and ONLY these, are the guard's decision
    input.
  * `read_cgroup_int()` reads `hmt2.slice`'s OWN cgroup-local counters
    (`memory.current`/`memory.peak`/`memory.swap.current`) for DIAGNOSTIC purposes only.
    `hmt2.slice`'s own cgroup-local PSI file (`/sys/fs/cgroup/<slice>/memory.pressure`) is
    DELIBERATELY never read anywhere in this module and must never become a guard decision
    input -- see `reports/host_guards.md` in the Trinity handoff bundle for the documented,
    real confusion this distinction resolved: HMT's own cgroup exceeding its `memory.high` soft
    cap can drive genuine HOST-WIDE PSI up (real reclaim stall time is host-wide accounting) even
    while true host-wide `MemAvailable` stays abundant -- so host PSI is a real, trustworthy host
    signal, but conflating it with cgroup-local PSI would not be.

CAUSAL SAMPLING -- read carefully before touching `CausalMemorySampler`:

  A real incident (documented in the Trinity handoff, `reports/host_guards.md`, incident
  GC-2025-10-01) was caused by taking a resource-state snapshot only INSIDE the guard-fire
  handler, AFTER `proc.terminate()`/`proc.wait()` had already run -- a POST-KILL reading that
  wrongly looked "trivial" and was once mistakenly treated as causal evidence. `CausalMemorySampler`
  is designed so this mistake is structurally impossible to reintroduce: it must be started
  BEFORE the worker process launches, sampled on every poll tick (a rolling buffer, not a
  single point-in-time call), and a guard-fire decision is made FROM that already-live buffer --
  there is no separate "diagnostic snapshot" call site at all, so there is nothing to
  accidentally place after `proc.terminate()`.
"""
from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, Optional

DEFAULT_MEMINFO_PATH = "/proc/meminfo"
DEFAULT_PSI_MEMORY_PATH = "/proc/pressure/memory"
DEFAULT_CGROUP_ROOT = "/sys/fs/cgroup"


def mem_available_kb(meminfo_path: str = DEFAULT_MEMINFO_PATH) -> Optional[int]:
    """Host-wide `MemAvailable` (kB), from `/proc/meminfo`. `None` if unreadable/absent."""
    try:
        with open(meminfo_path, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def psi_memory_full_avg10(psi_path: str = DEFAULT_PSI_MEMORY_PATH) -> Optional[float]:
    """Host-wide `/proc/pressure/memory` "full" line, `avg10` field ONLY. `None` if unreadable
    (e.g. PSI accounting disabled on this kernel) -- callers must treat `None` as "no PSI signal
    available", never as "0.0 / healthy"."""
    try:
        with open(psi_path, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("full "):
                    for tok in line.split():
                        if tok.startswith("avg10="):
                            return float(tok.split("=", 1)[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def read_cgroup_int(
    name: str, *, slice_name: str = "hmt2.slice", cgroup_root: str = DEFAULT_CGROUP_ROOT
) -> Optional[int]:
    """DIAGNOSTIC ONLY -- reads `<cgroup_root>/<slice_name>/<name>` (e.g. `memory.current`,
    `memory.peak`, `memory.swap.current`, `memory.high`, `memory.max`). Never used as a guard
    decision input -- see module docstring."""
    try:
        with open(os.path.join(cgroup_root, slice_name, name), "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def slice_cgroup_procs(*, slice_name: str = "hmt2.slice", cgroup_root: str = DEFAULT_CGROUP_ROOT) -> Optional[list]:
    """PIDs currently attached directly to `<slice_name>`'s own `cgroup.procs` (does not recurse
    into child scopes/services -- an empty slice with an active child scope still shows an empty
    `cgroup.procs` here, by cgroup v2 design; see `slice_is_idle()` for the check that actually
    matters operationally). `None` if unreadable."""
    try:
        with open(os.path.join(cgroup_root, slice_name, "cgroup.procs"), "r", encoding="utf-8") as fh:
            return [line.strip() for line in fh if line.strip()]
    except OSError:
        return None


def slice_is_idle(*, slice_name: str = "hmt2.slice", cgroup_root: str = DEFAULT_CGROUP_ROOT) -> bool:
    """True only if `<slice_name>`'s own cgroup-local `memory.current` is reported AND is exactly
    zero -- the practical, portable proxy for "no live work under this slice right now" used by
    the exceptional-envelope preflight (an idle slice has released all charged memory back to
    zero; a slice with any live scope/service underneath will not read exactly zero). Fails
    closed (returns `False`, i.e. "not idle") if the counter cannot be read at all, since an
    unreadable slice is never safe to assume idle."""
    current = read_cgroup_int("memory.current", slice_name=slice_name, cgroup_root=cgroup_root)
    return current == 0


def disk_pct(path: str = "/srv") -> float:
    """Percent of `path`'s filesystem currently used, via `os.statvfs` (no subprocess)."""
    st = os.statvfs(path)
    if st.f_blocks == 0:
        return 0.0
    return (st.f_blocks - st.f_bfree) / st.f_blocks * 100


@dataclass
class MemorySample:
    utc_monotonic: float
    mem_available_kb: Optional[int]
    psi_memory_full_avg10: Optional[float]
    slice_memory_current: Optional[int]
    slice_memory_peak: Optional[int]
    slice_memory_swap_current: Optional[int]

    def as_dict(self) -> Dict[str, object]:
        return {
            "utc_monotonic": self.utc_monotonic,
            "mem_available_kb": self.mem_available_kb,
            "psi_memory_full_avg10": self.psi_memory_full_avg10,
            "slice_memory_current": self.slice_memory_current,
            "slice_memory_peak": self.slice_memory_peak,
            "slice_memory_swap_current": self.slice_memory_swap_current,
        }


class CausalMemorySampler:
    """A rolling-buffer, always-live host/cgroup telemetry sampler.

    Correctness contract (see module docstring for the incident this exists to prevent):
    the caller MUST construct this and call `sample()` at least once BEFORE launching the worker
    process, and MUST keep calling `sample()` on every poll tick thereafter, including the tick
    on which a guard-fire decision is made -- the fire decision is made from the sample just
    taken, never from a snapshot taken only after `proc.terminate()`. There is deliberately no
    "diagnostic snapshot" method separate from `sample()` -- using this class at all makes the
    post-kill-snapshot mistake structurally unavailable.
    """

    def __init__(
        self,
        *,
        slice_name: str = "hmt2.slice",
        cgroup_root: str = DEFAULT_CGROUP_ROOT,
        max_samples: Optional[int] = 4096,
        clock: Callable[[], float] = time.monotonic,
        mem_available_fn: Callable[[], Optional[int]] = mem_available_kb,
        psi_fn: Callable[[], Optional[float]] = psi_memory_full_avg10,
        cgroup_fn: Callable[..., Optional[int]] = read_cgroup_int,
    ) -> None:
        self._slice_name = slice_name
        self._cgroup_root = cgroup_root
        self._clock = clock
        self._mem_available_fn = mem_available_fn
        self._psi_fn = psi_fn
        self._cgroup_fn = cgroup_fn
        self._buffer: Deque[MemorySample] = deque(maxlen=max_samples)

    def sample(self) -> MemorySample:
        s = MemorySample(
            utc_monotonic=self._clock(),
            mem_available_kb=self._mem_available_fn(),
            psi_memory_full_avg10=self._psi_fn(),
            slice_memory_current=self._cgroup_fn(
                "memory.current", slice_name=self._slice_name, cgroup_root=self._cgroup_root
            ),
            slice_memory_peak=self._cgroup_fn(
                "memory.peak", slice_name=self._slice_name, cgroup_root=self._cgroup_root
            ),
            slice_memory_swap_current=self._cgroup_fn(
                "memory.swap.current", slice_name=self._slice_name, cgroup_root=self._cgroup_root
            ),
        )
        self._buffer.append(s)
        return s

    def latest(self) -> Optional[MemorySample]:
        return self._buffer[-1] if self._buffer else None

    def history(self) -> list:
        return list(self._buffer)

    def sample_count(self) -> int:
        return len(self._buffer)


@dataclass
class SustainedLowMemoryGuard:
    """Edge-triggered "MemAvailable below `threshold_kb` continuously for >= `sustained_seconds`"
    tracker. `observe()` is called once per poll tick with the tick's own `mem_available_kb`
    reading (e.g. from a `CausalMemorySampler` sample taken the same tick) and returns `True`
    exactly once the sustained condition is first met (subsequent calls while still below
    threshold return `False` again until `reset()`, so a caller's guard-fire handling runs once
    per sustained episode, not once per poll tick)."""

    threshold_kb: int
    sustained_seconds: float
    clock: Callable[[], float] = time.monotonic
    _low_since: Optional[float] = field(default=None, init=False, repr=False)
    _fired: bool = field(default=False, init=False, repr=False)

    def observe(self, mem_available_kb_value: Optional[int], *, now: Optional[float] = None) -> bool:
        now = self.clock() if now is None else now
        if mem_available_kb_value is None or mem_available_kb_value >= self.threshold_kb:
            self._low_since = None
            self._fired = False
            return False
        if self._low_since is None:
            self._low_since = now
        elapsed = now - self._low_since
        if elapsed >= self.sustained_seconds and not self._fired:
            self._fired = True
            return True
        return False

    def reset(self) -> None:
        self._low_since = None
        self._fired = False


def psi_guard_fires(psi_avg10: Optional[float], *, threshold: float) -> bool:
    """PSI branch of the in-session guard: fires immediately (no sustain window -- PSI's own
    `avg10` is already a decayed running average) once host `full avg10 >= threshold`. `None`
    (PSI unreadable) never fires -- absence of signal is not evidence of pressure."""
    return psi_avg10 is not None and psi_avg10 >= threshold
