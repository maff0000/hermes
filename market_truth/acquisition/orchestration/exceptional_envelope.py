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
"""
from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass
from typing import Callable, Iterator, List, Optional

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


def preflight(
    *,
    min_memavailable_kb: int,
    psi_stall_threshold: float,
    mem_available_fn: Callable[[], Optional[int]],
    psi_fn: Callable[[], Optional[float]],
    slice_is_idle_fn: Callable[[], bool],
) -> PreflightResult:
    """Preflight, matching the Trinity procedure exactly: `MemAvailable >= floor`, host PSI
    healthy (below threshold, and readable), `hmt2.slice` idle (no competing HMT work)."""
    reasons: List[str] = []
    mem = mem_available_fn()
    if mem is None or mem < min_memavailable_kb:
        reasons.append(f"MemAvailable={mem!r}kB below required floor {min_memavailable_kb}kB")
    psi = psi_fn()
    if psi is None:
        reasons.append("host PSI unreadable -- cannot confirm host is healthy")
    elif psi >= psi_stall_threshold:
        reasons.append(f"host PSI full avg10={psi} >= stall threshold {psi_stall_threshold}")
    if not slice_is_idle_fn():
        reasons.append("hmt2.slice is not idle -- competing HMT-2 work is present")
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
    slice_is_idle_fn: Callable[[], bool],
) -> Iterator[None]:
    """The full apply/verify/restore/verify state machine as a context manager: validates and
    preflights BEFORE yielding, and unconditionally restores + re-verifies the routine envelope
    in `finally` -- success or failure inside the `with` block. A caller runs exactly one
    session's worker launch inside the `with` block; there is no supported way to run more than
    one session per `exceptional_envelope()` call, matching the Trinity procedure's "run exactly
    ONE session under the exception" rule structurally, not just by convention.
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
