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

SLICE-IDLE DETERMINATION -- read carefully before touching `slice_is_idle()`:

  Real incident (Trinity, 2026-09): a genuinely idle `hmt2.slice` (empty `cgroup.procs`, no
  live child scope, no active HMT transient unit) retained a small non-zero residual in
  `memory.current` (observed ~1.5MB on Trinity; a comparable residual is independently
  observable on dell-debian at the time this fix was written -- see
  `tests/hmt2/orchestration/test_host_guards.py::test_slice_is_idle_real_hmt2_slice_with_real_residual_memory_is_idle`).
  This is ordinary cgroup/kernel accounting slop (page cache, slab, kernel-side bookkeeping
  charged to the cgroup), not evidence of live work. An exact-zero `memory.current` requirement
  made the exceptional-envelope preflight (`exceptional_envelope.preflight()`) permanently
  unusable in a legitimate idle condition -- a hard-stop that was the CORRECT response given the
  faulty check, not a bug in the operator's judgement.

  `slice_is_idle()` therefore answers "is there any live or still-operational HMT workload that
  makes changing the slice resource envelope unsafe right now?" using THREE independent signals,
  combined fail-closed:

    1. `cgroup.procs` at the slice root AND at every descendant cgroup directory (a worker's own
       transient scope nests under the slice; an empty top-level `cgroup.procs` says nothing
       about a live child scope -- see `slice_cgroup_procs()`'s own docstring for the cgroup v2
       semantics this exists to avoid getting wrong).
    2. An INDEPENDENT query of systemd's own live unit state (`default_systemd_units_in_slice()`),
       so a live/still-tearing-down HMT transient unit is caught even in the pathological case
       where a raw cgroupfs read is (or looks) empty. On disagreement between the two signals,
       the module fails closed to NOT_IDLE -- it never trusts whichever signal happens to say
       "idle".
    3. `memory.current` remains a DIAGNOSTIC-ONLY signal, logged in every `SliceIdleCheck` but
       NEVER required to be exactly zero. The one exception: if signals 1 and 2 both report no
       workload at all AND `memory.current` is suspiciously large (see
       `SUSPICIOUS_RESIDUAL_MEMORY_BYTES_DEFAULT`), that combination is itself treated as
       inconclusive (INDETERMINATE, fail-closed) rather than confidently idle -- a genuinely idle
       slice with a few hundred KB to a few MB of residual accounting is normal; one with, say,
       hundreds of MB and literally no process or unit anywhere is not something this module will
       guess about.

  Any unreadable/missing cgroup control file, any directory-listing failure while walking the
  slice's cgroup subtree, and any systemd-query failure are all treated as INDETERMINATE
  (cannot determine) -- never silently treated as idle. `SliceIdleCheck.is_confidently_idle` is
  `True` for `SliceIdleStatus.IDLE` only; callers that need a single fail-closed boolean (e.g.
  `exceptional_envelope.preflight()`) use that property, never assume anything else means idle.
"""
from __future__ import annotations

import enum
import os
import subprocess
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, Iterator, List, Optional, Tuple

DEFAULT_MEMINFO_PATH = "/proc/meminfo"
DEFAULT_PSI_MEMORY_PATH = "/proc/pressure/memory"
DEFAULT_CGROUP_ROOT = "/sys/fs/cgroup"

# A genuinely idle slice can carry a small non-zero `memory.current` residual (ordinary
# cgroup/kernel accounting slop -- see the real incident this module's docstring documents,
# where ~1.5MB was observed on Trinity for a slice with zero processes and zero live units
# anywhere). This threshold is deliberately far above that kind of residual: it exists only to
# catch a slice reporting a large, unexplained residual with NO process and NO systemd unit found
# anywhere, which is itself worth treating conservatively rather than certifying idle. It is
# NEVER used to require exact zero, and it is NEVER, by itself, sufficient to declare NOT_IDLE --
# it can only downgrade an otherwise-IDLE verdict to INDETERMINATE.
SUSPICIOUS_RESIDUAL_MEMORY_BYTES_DEFAULT = 64 * 1024 * 1024  # 64 MiB

# ActiveState values systemd reports for a unit that is live or still tearing down -- all treated
# as "not idle" if the unit is a member of the slice being checked. "deactivating" is included
# deliberately: a unit mid-teardown still represents operational HMT workload for the purposes of
# this guard, even though its cgroup may already look sparse.
SYSTEMD_LIVE_ACTIVE_STATES = frozenset({"active", "activating", "reloading", "deactivating"})


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


class SliceIdleStatus(enum.Enum):
    """The three possible verdicts `slice_is_idle()` can reach. Only `IDLE` means "safe to
    proceed" -- `NOT_IDLE` and `INDETERMINATE` are both fail-closed and must be treated
    identically by any caller that only wants a yes/no ("safe to mutate the envelope right now"),
    which is exactly what `SliceIdleCheck.is_confidently_idle` gives them."""

    IDLE = "idle"
    NOT_IDLE = "not_idle"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class SliceIdleCheck:
    """Rich result of a `slice_is_idle()` call: the verdict, the human-readable reason(s) for it,
    and the diagnostic readings that informed it (kept for logging/evidence, never re-derived by
    the caller). `is_confidently_idle` is the single fail-closed boolean a caller should act on."""

    status: SliceIdleStatus
    reasons: Tuple[str, ...] = ()
    slice_memory_current: Optional[int] = None
    cgroup_paths_checked: Tuple[str, ...] = ()
    systemd_active_units: Tuple[str, ...] = ()

    @property
    def is_confidently_idle(self) -> bool:
        return self.status is SliceIdleStatus.IDLE

    def as_dict(self) -> Dict[str, object]:
        return {
            "status": self.status.value,
            "reasons": list(self.reasons),
            "slice_memory_current": self.slice_memory_current,
            "cgroup_paths_checked": list(self.cgroup_paths_checked),
            "systemd_active_units": list(self.systemd_active_units),
        }


@dataclass(frozen=True)
class SystemdUnitInfo:
    """One scope/service unit systemd currently knows about, with the two facts `slice_is_idle()`
    needs: its `ActiveState` and its `Slice=` membership. Membership is read per-unit (systemd
    does not expose a single "members of this slice" property), so a unit not currently a member
    of the slice being checked (`slice_name is None` or does not match) is simply not relevant."""

    name: str
    active_state: str
    sub_state: str
    slice_name: Optional[str]


def _run_systemctl_lines(argv: List[str]) -> List[str]:
    """Real, read-only systemctl probe (`list-units` / `show`) -- never a mutating call, never
    `set-property`. Requires no privilege beyond what this orchestrator (root) already holds to
    inspect systemd's own state; grants nothing new and creates/removes no unit."""
    proc = subprocess.run(argv, capture_output=True, text=True, check=True, timeout=15)
    return proc.stdout.splitlines()


def default_systemd_units_in_slice(
    slice_name: str,
    *,
    run_systemctl_lines: Callable[[List[str]], List[str]] = _run_systemctl_lines,
) -> List[SystemdUnitInfo]:
    """Production probe: systemd's OWN live view of scope/service units, INDEPENDENT of any
    cgroupfs read. Lists candidate scope/service units via `systemctl list-units`, then resolves
    each candidate's `Slice=` membership via `systemctl show <unit> --property=Slice --value`,
    returning only the units that are actually members of `slice_name`. Raises on any systemctl
    failure (non-zero exit, timeout, missing binary) -- `slice_is_idle()` treats that as
    "cannot determine", never as "no active units"."""
    list_lines = run_systemctl_lines(
        ["systemctl", "list-units", "--all", "--no-legend", "--plain", "--type=scope,service"]
    )
    candidates: List[Tuple[str, str, str]] = []
    for line in list_lines:
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        unit, _load, active_state, sub_state = parts[0], parts[1], parts[2], parts[3]
        candidates.append((unit, active_state, sub_state))

    result: List[SystemdUnitInfo] = []
    for unit, active_state, sub_state in candidates:
        show_lines = run_systemctl_lines(["systemctl", "show", unit, "--property=Slice", "--value"])
        unit_slice = show_lines[0].strip() if show_lines and show_lines[0].strip() else None
        if unit_slice == slice_name:
            result.append(
                SystemdUnitInfo(name=unit, active_state=active_state, sub_state=sub_state, slice_name=unit_slice)
            )
    return result


def _iter_cgroup_procs_files(base_dir: str) -> Iterator[Tuple[str, str]]:
    """Yields `(relative_path, cgroup.procs absolute path)` for `base_dir` itself and every
    descendant cgroup directory, depth-first, recursing to whatever depth the real hierarchy
    actually has (never a hardcoded "one level of children" assumption -- a worker's own
    transient scope, or anything delegated further beneath it, is still found).

    Deliberately does NOT catch `OSError` from `os.scandir()` -- an unreadable/race-deleted
    directory while walking must propagate to the caller, who fails closed (INDETERMINATE),
    never silently skips the subtree and risks missing a live process underneath it."""
    yield ".", os.path.join(base_dir, "cgroup.procs")
    stack = [base_dir]
    while stack:
        current = stack.pop()
        with os.scandir(current) as it:
            for entry in it:
                if entry.is_dir(follow_symlinks=False):
                    rel = os.path.relpath(entry.path, base_dir)
                    yield rel, os.path.join(entry.path, "cgroup.procs")
                    stack.append(entry.path)


def slice_is_idle(
    *,
    slice_name: str = "hmt2.slice",
    cgroup_root: str = DEFAULT_CGROUP_ROOT,
    systemd_units_fn: Optional[Callable[[], List[SystemdUnitInfo]]] = None,
    suspicious_residual_memory_bytes: int = SUSPICIOUS_RESIDUAL_MEMORY_BYTES_DEFAULT,
) -> SliceIdleCheck:
    """Determines whether `<slice_name>` is genuinely free of live/bounded HMT workload right
    now -- the exceptional-envelope preflight's answer to "is it safe to mutate the resource
    envelope?". See the module docstring ("SLICE-IDLE DETERMINATION") for the full reasoning.

    `systemd_units_fn` is an injectable seam for tests; production callers should leave it unset
    (defaults to a real, read-only `default_systemd_units_in_slice(slice_name)` query).

    Fails closed (`SliceIdleStatus.INDETERMINATE`) on: a missing/non-directory slice cgroup path,
    any unreadable `cgroup.procs` anywhere in the slice's cgroup subtree, any directory-walk
    error, any systemd-query failure or empty/`None` result, and the "large unexplained residual
    memory with no process or unit found anywhere" case. Returns `SliceIdleStatus.NOT_IDLE` for
    any live process (slice-level or in any descendant cgroup) or any systemd-reported active unit
    in the slice. Only returns `SliceIdleStatus.IDLE` when every signal agrees there is no live or
    still-operational workload.
    """
    slice_dir = os.path.join(cgroup_root, slice_name)
    mem_current = read_cgroup_int("memory.current", slice_name=slice_name, cgroup_root=cgroup_root)

    if not os.path.isdir(slice_dir):
        return SliceIdleCheck(
            status=SliceIdleStatus.INDETERMINATE,
            reasons=(f"slice cgroup directory {slice_dir!r} does not exist or is not a directory",),
            slice_memory_current=mem_current,
        )

    checked: List[str] = []
    try:
        for rel_path, procs_file in _iter_cgroup_procs_files(slice_dir):
            checked.append(rel_path)
            try:
                with open(procs_file, "r", encoding="utf-8") as fh:
                    procs = [line.strip() for line in fh if line.strip()]
            except OSError as exc:
                return SliceIdleCheck(
                    status=SliceIdleStatus.INDETERMINATE,
                    reasons=(f"cgroup.procs unreadable at {rel_path!r} ({procs_file}): {exc}",),
                    slice_memory_current=mem_current,
                    cgroup_paths_checked=tuple(checked),
                )
            if procs:
                where = "the slice root" if rel_path == "." else f"child cgroup {rel_path!r}"
                return SliceIdleCheck(
                    status=SliceIdleStatus.NOT_IDLE,
                    reasons=(f"{len(procs)} live process(es) found in {where}",),
                    slice_memory_current=mem_current,
                    cgroup_paths_checked=tuple(checked),
                )
    except OSError as exc:
        return SliceIdleCheck(
            status=SliceIdleStatus.INDETERMINATE,
            reasons=(f"error walking the cgroup subtree under {slice_dir!r}: {exc}",),
            slice_memory_current=mem_current,
            cgroup_paths_checked=tuple(checked),
        )

    units_fn = systemd_units_fn or (lambda: default_systemd_units_in_slice(slice_name))
    try:
        units = units_fn()
    except Exception as exc:  # noqa: BLE001 -- any systemctl/probe failure means "cannot determine"
        return SliceIdleCheck(
            status=SliceIdleStatus.INDETERMINATE,
            reasons=(f"systemd unit query failed -- cannot independently confirm idleness: {exc}",),
            slice_memory_current=mem_current,
            cgroup_paths_checked=tuple(checked),
        )
    if units is None:
        return SliceIdleCheck(
            status=SliceIdleStatus.INDETERMINATE,
            reasons=("systemd unit query returned no result (unavailable) -- cannot confirm idleness",),
            slice_memory_current=mem_current,
            cgroup_paths_checked=tuple(checked),
        )

    # Never trust an injected/default units_fn to have already filtered by slice -- re-check
    # membership here too, so a unit belonging to some OTHER slice can never make THIS slice
    # look busy, regardless of what the units_fn implementation does or doesn't filter.
    active_units = tuple(
        u.name for u in units if u.slice_name == slice_name and u.active_state in SYSTEMD_LIVE_ACTIVE_STATES
    )
    if active_units:
        return SliceIdleCheck(
            status=SliceIdleStatus.NOT_IDLE,
            reasons=(
                f"systemd reports active unit(s) in {slice_name} even though the cgroupfs read found "
                f"no live process anywhere in the slice: {', '.join(active_units)}",
            ),
            slice_memory_current=mem_current,
            cgroup_paths_checked=tuple(checked),
            systemd_active_units=active_units,
        )

    # Both independent signals agree: no live process anywhere in the slice's cgroup subtree, and
    # systemd reports no active/tearing-down unit in this slice. memory.current is diagnostic-only
    # from here on -- logged always, required to be zero never -- except the one deliberately
    # conservative exception below.
    if mem_current is not None and mem_current > suspicious_residual_memory_bytes:
        return SliceIdleCheck(
            status=SliceIdleStatus.INDETERMINATE,
            reasons=(
                f"no process or active systemd unit found anywhere in {slice_name}, but "
                f"memory.current={mem_current} exceeds the suspicious-residual threshold "
                f"({suspicious_residual_memory_bytes} bytes) -- refusing to certify idle without "
                f"an explanation for that residual",
            ),
            slice_memory_current=mem_current,
            cgroup_paths_checked=tuple(checked),
        )

    if mem_current is None:
        mem_reason = "memory.current diagnostic unreadable (not required for an idle verdict)"
    else:
        mem_reason = f"residual memory.current={mem_current} bytes (diagnostic only, not required to be zero)"

    return SliceIdleCheck(
        status=SliceIdleStatus.IDLE,
        reasons=(
            f"no live process found anywhere in {slice_name}'s cgroup subtree; systemd reports no "
            f"active unit in this slice",
            mem_reason,
        ),
        slice_memory_current=mem_current,
        cgroup_paths_checked=tuple(checked),
    )


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
