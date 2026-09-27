"""market_truth.acquisition.orchestration.exceptional_envelope -- governed HMT-2 resource-envelope
exception mechanism.

Ports the manually-executed procedure documented in the Trinity operational-delta handoff
(`reports/host_guards.md`, incidents GC-2022-10-21 and GC-2025-10-01) as a real, tested,
reusable mechanism -- not a literal transcription of the shell commands used on Trinity.

Procedure (unconditionally, in this order):

  1. Validate the caller-supplied `EnvelopeAuthorization` against policy (never above the
     configured `authorised_ceiling_memory_max_bytes`, never `memory_high >= memory_max`,
     session id must match the fixed grammar).
  2. Preflight: host `MemAvailable` >= a configured floor, host PSI healthy, `hmt2.slice` idle
     (no other work) -- refuse if any fails.
  3. Apply: `systemctl set-property --runtime <slice> MemoryHigh=<X> MemoryMax=<Y>` (runtime-only,
     never persisted across reboot).
  4. Verify live from cgroupfs before letting the caller proceed.
  5. Caller runs EXACTLY ONE session under the exception (enforced by this module's context-
     manager shape, not by caller discipline alone).
  6. Restore the routine envelope and verify it live again -- ALWAYS, on success OR failure
     (the `finally` block below is what makes "never leave the exceptional envelope active" a
     structural guarantee rather than a hope).

This module NEVER decides, on its own initiative, that an arbitrary session should run under an
exceptional envelope -- it only ever *applies* an authorization the caller already holds. There
is no code path here that can auto-escalate beyond `authorised_ceiling_memory_max_bytes`; that
value is itself an operator/Architect-owned configuration input (see `config.Hmt2OpsConfig`),
never something this module raises on its own.

RACE SAFETY -- read before touching `preflight()` / `apply_envelope()` / `exceptional_envelope()`:

  The idle-check-then-apply sequence (`preflight()` calling `slice_is_idle_fn()`, immediately
  followed, if it passed, by `apply_envelope()`'s `run_systemctl` call) is a single synchronous
  Python call stack with no `await`, no thread handoff, and no I/O wait between the two -- nothing
  this module itself executes can interleave a competing worker-launch call between them; see
  `tests/hmt2/orchestration/test_exceptional_envelope.py::test_preflight_then_apply_is_an_uninterrupted_synchronous_sequence`
  for the proof.

  This does NOT, by itself, prevent a wholly separate OS process (a second, independently
  launched invocation of this mechanism, a second `canonical_root_orchestrator.py` run, or a
  direct/manual `deployment/hmt2/hmt2-run.sh` invocation) from launching a new worker into the
  slice in the gap between this module's own cgroupfs/systemd reads and its `systemctl
  set-property` call. As of this fix, nothing anywhere in this repository (no PID file, no
  `flock`, no systemd single-instance constraint) provides that cross-process exclusion --
  `canonical_root_orchestrator.py`'s dispatch loop does not currently call this module at all
  (it is invoked only as a standalone, operator-driven procedure), and
  `deployment/hmt2/hmt2-run.sh` places every session in an unnamed transient scope with no
  collision/locking of any kind. Closing that cross-process gap would require synchronization
  that spans this module AND every worker-launch entry point (including the launcher shell
  script), which is a new locking architecture, not a narrow guard fix -- deliberately NOT
  attempted here; see the WO report for this defect correction for the full analysis and the
  recommendation to open a separate, appropriately-scoped work order if closing it is wanted.
"""
from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass
from typing import Callable, Iterator, List, Optional, Protocol, Tuple, runtime_checkable

SESSION_ID_RE = re.compile(r"^GC-\d{4}-\d{2}-\d{2}$")


class EnvelopeError(RuntimeError):
    """Raised when an exceptional-envelope authorization fails validation, preflight, apply, or
    verify -- always fail-closed; the caller must not proceed to launch a worker."""


@dataclass(frozen=True)
class EnvelopeAuthorization:
    """An explicit, externally-supplied, single-session authorization. Never constructed by the
    orchestrator itself from its own judgement -- always supplied by the caller (an operator or
    an Architect-authorised record read from the evidence root), scoped to exactly one session.
    """

    session_id: str
    memory_high_bytes: int
    memory_max_bytes: int
    authorised_by: str
    authorised_utc: str
    reason: str


def validate_authorization(auth: EnvelopeAuthorization, *, authorised_ceiling_memory_max_bytes: int) -> None:
    if not SESSION_ID_RE.match(auth.session_id):
        raise EnvelopeError(f"authorization session_id fails grammar check: {auth.session_id!r}")
    if auth.memory_high_bytes >= auth.memory_max_bytes:
        raise EnvelopeError(
            f"memory_high_bytes ({auth.memory_high_bytes}) must be strictly less than "
            f"memory_max_bytes ({auth.memory_max_bytes})"
        )
    if auth.memory_max_bytes > authorised_ceiling_memory_max_bytes:
        raise EnvelopeError(
            f"authorization requests memory_max_bytes={auth.memory_max_bytes}, which exceeds the "
            f"configured authorised ceiling {authorised_ceiling_memory_max_bytes} -- this module "
            f"never auto-escalates beyond an Architect-authorised ceiling; issue a new ceiling "
            f"configuration value first if a larger envelope is genuinely required."
        )
    if not auth.authorised_by:
        raise EnvelopeError("authorization must name who authorised it (authorised_by)")
    if not auth.authorised_utc:
        raise EnvelopeError("authorization must record when it was authorised (authorised_utc)")


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    reasons: List[str]


@runtime_checkable
class SliceIdleCheckLike(Protocol):
    """Structural (duck-typed) contract `preflight()` needs from whatever `slice_is_idle_fn()`
    returns -- deliberately NOT an import of `host_guards.SliceIdleCheck`, matching this module's
    existing convention of depending only on injected callables/shapes, never on concrete
    `host_guards` types. `host_guards.SliceIdleCheck` satisfies this today; any replacement must
    keep satisfying it."""

    is_confidently_idle: bool
    reasons: Tuple[str, ...]


def preflight(
    *,
    min_memavailable_kb: int,
    psi_stall_threshold: float,
    mem_available_fn: Callable[[], Optional[int]],
    psi_fn: Callable[[], Optional[float]],
    slice_is_idle_fn: Callable[[], SliceIdleCheckLike],
) -> PreflightResult:
    """Preflight, matching the Trinity procedure exactly: `MemAvailable >= floor`, host PSI
    healthy (below threshold, and readable), `hmt2.slice` idle (no competing HMT work).

    `slice_is_idle_fn()` must return something satisfying `SliceIdleCheckLike` -- in production
    this is `host_guards.slice_is_idle(...)`'s result. Only `is_confidently_idle` decides
    ok/not-ok here; `reasons` is folded into this preflight's own `reasons` purely for
    diagnostics/evidence, never re-interpreted. `is_confidently_idle is False` covers BOTH
    "confirmed not idle" and "could not determine" -- both are fail-closed here, identically;
    this function does not, and must not, distinguish between them when deciding whether to
    proceed.
    """
    reasons: List[str] = []
    mem = mem_available_fn()
    if mem is None or mem < min_memavailable_kb:
        reasons.append(f"MemAvailable={mem!r}kB below required floor {min_memavailable_kb}kB")
    psi = psi_fn()
    if psi is None:
        reasons.append("host PSI unreadable -- cannot confirm host is healthy")
    elif psi >= psi_stall_threshold:
        reasons.append(f"host PSI full avg10={psi} >= stall threshold {psi_stall_threshold}")
    idle_check = slice_is_idle_fn()
    if not idle_check.is_confidently_idle:
        detail = "; ".join(idle_check.reasons) if idle_check.reasons else "no further detail available"
        reasons.append(f"hmt2.slice is not confidently idle -- competing HMT-2 work may be present ({detail})")
    return PreflightResult(ok=not reasons, reasons=reasons)


def apply_envelope(
    *,
    slice_name: str,
    memory_high_bytes: int,
    memory_max_bytes: int,
    run_systemctl: Callable[[List[str]], None],
    read_cgroup_int_fn: Callable[[str], Optional[int]],
) -> None:
    run_systemctl(
        [
            "systemctl",
            "set-property",
            "--runtime",
            slice_name,
            f"MemoryHigh={memory_high_bytes}",
            f"MemoryMax={memory_max_bytes}",
        ]
    )
    live_high = read_cgroup_int_fn("memory.high")
    live_max = read_cgroup_int_fn("memory.max")
    if live_high != memory_high_bytes or live_max != memory_max_bytes:
        raise EnvelopeError(
            f"apply verify failed: cgroupfs reports memory.high={live_high!r} memory.max={live_max!r}, "
            f"expected {memory_high_bytes}/{memory_max_bytes} -- refusing to proceed with an "
            f"unverified envelope"
        )


def restore_routine_envelope(
    *,
    slice_name: str,
    routine_memory_high_bytes: int,
    routine_memory_max_bytes: int,
    run_systemctl: Callable[[List[str]], None],
    read_cgroup_int_fn: Callable[[str], Optional[int]],
) -> None:
    """Restores and re-verifies the routine envelope. Callers must call this unconditionally --
    `exceptional_envelope()` below does so in a `finally` block, so a caller using the context
    manager gets this for free even if the worker itself raised."""
    run_systemctl(
        [
            "systemctl",
            "set-property",
            "--runtime",
            slice_name,
            f"MemoryHigh={routine_memory_high_bytes}",
            f"MemoryMax={routine_memory_max_bytes}",
        ]
    )
    live_high = read_cgroup_int_fn("memory.high")
    live_max = read_cgroup_int_fn("memory.max")
    if live_high != routine_memory_high_bytes or live_max != routine_memory_max_bytes:
        raise EnvelopeError(
            f"RESTORE VERIFY FAILED: cgroupfs reports memory.high={live_high!r} memory.max={live_max!r} "
            f"after attempting to restore the routine envelope "
            f"({routine_memory_high_bytes}/{routine_memory_max_bytes}) -- hmt2.slice may be left in an "
            f"exceptional state; this must be treated as a STOP-severity operational incident, not "
            f"retried silently."
        )


@contextlib.contextmanager
def exceptional_envelope(
    auth: EnvelopeAuthorization,
    *,
    slice_name: str,
    routine_memory_high_bytes: int,
    routine_memory_max_bytes: int,
    authorised_ceiling_memory_max_bytes: int,
    preflight_min_memavailable_kb: int,
    psi_stall_threshold: float,
    run_systemctl: Callable[[List[str]], None],
    read_cgroup_int_fn: Callable[[str], Optional[int]],
    mem_available_fn: Callable[[], Optional[int]],
    psi_fn: Callable[[], Optional[float]],
    slice_is_idle_fn: Callable[[], SliceIdleCheckLike],
) -> Iterator[None]:
    """The full apply/verify/restore/verify state machine as a context manager: validates and
    preflights BEFORE yielding, and unconditionally restores + re-verifies the routine envelope
    in `finally` -- success or failure inside the `with` block. A caller runs exactly one
    session's worker launch inside the `with` block; there is no supported way to run more than
    one session per `exceptional_envelope()` call, matching the Trinity procedure's "run exactly
    ONE session under the exception" rule structurally, not just by convention.

    See the module docstring's "RACE SAFETY" section for exactly what is, and is not, guaranteed
    about the gap between the idle check inside `preflight()` and the mutation inside
    `apply_envelope()`.
    """
    validate_authorization(auth, authorised_ceiling_memory_max_bytes=authorised_ceiling_memory_max_bytes)
    result = preflight(
        min_memavailable_kb=preflight_min_memavailable_kb,
        psi_stall_threshold=psi_stall_threshold,
        mem_available_fn=mem_available_fn,
        psi_fn=psi_fn,
        slice_is_idle_fn=slice_is_idle_fn,
    )
    if not result.ok:
        raise EnvelopeError(f"exceptional-envelope preflight failed: {'; '.join(result.reasons)}")

    apply_envelope(
        slice_name=slice_name,
        memory_high_bytes=auth.memory_high_bytes,
        memory_max_bytes=auth.memory_max_bytes,
        run_systemctl=run_systemctl,
        read_cgroup_int_fn=read_cgroup_int_fn,
    )
    try:
        yield
    finally:
        restore_routine_envelope(
            slice_name=slice_name,
            routine_memory_high_bytes=routine_memory_high_bytes,
            routine_memory_max_bytes=routine_memory_max_bytes,
            run_systemctl=run_systemctl,
            read_cgroup_int_fn=read_cgroup_int_fn,
        )
