"""HERMES shared-stream recovery Phase-2 — READ-ONLY EVIDENCE ADAPTERS + SNAPSHOT BUILDER (PRODUCTION-OWNED, INERT).

WO-HELM-HERMES-SHARED-STREAM-RECOVERY-PHASE2-SHADOW-ADAPTER-IMPLEMENTATION-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-17. Owner: HERMES (Helm).
Contract: docs/design/shared_stream_recovery/architecture_phase2_v1.md §1-§9/§12/§19 (binding). Contract version "1".

STATUS: INERT / NOT WIRED. Imported by NO live runtime path; only tests + sibling utils/hermes_sss_* modules.
These collectors DO NOT import adapters/oanda.py, adapters/base.py, main.py, or utils/watchdog.py — they operate
ONLY on injected, immutable read-only VIEWS of that state (see §19: a snapshot is built from an immutable copy of
each surface). They therefore cannot mutate a live surface, cannot reorder watchdog state, and cannot block the
market-data or recovery paths. A future wiring WO (Phase 3+) would populate the views from a short read of the
live surfaces; that wiring is explicitly out of scope here.

GROUNDED TRACE (build read-only adapters over these; invent nothing):
  * AdapterHealth.last_tick_at advances on OANDA HEARTBEAT (~5s) AND PRICE = GLOBAL any-message liveness (NOT
    per-instrument, NOT price-only). The heartbeat signal and the shared-progress signal read the SAME surface and
    therefore carry the SAME provenance surface_id (the mapper collapses the second to a mirror — §8).
  * AdapterState / watchdog StreamState = socket/control-plane state. connect() only proves auth/API reachability
    -> CONNECTED is AMBIGUOUS, never transport-health proof.
  * provider_disconnect_event / reconnect_in_progress = main.oanda_stream_task outer-except + retry_count.
  * auth_failure (401/403), parser_fatal (stream task/async-for termination), shared_stream_silent
    (asyncio.TimeoutError) — distinct reason surfaces (§7/§8).
  * provider_maintenance_indication: OANDA emits NO signal -> UNAVAILABLE / UNSAFE_TO_INFER, value False, never
    inferred.

PURITY: standard-library only + the evidence-snapshot model. No wall-clock read (now_utc supplied); no I/O; no
thread/task/timer; no hidden mutable global. Every collector returns UNAVAILABLE for a missing surface — never a
favourable default.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Tuple

import utils.hermes_shared_stream_recovery_v1 as core
import utils.hermes_sss_evidence_snapshot_v1 as snap_mod
from utils.hermes_sss_evidence_snapshot_v1 import (
    AvailabilityClass, EvidenceCompleteness, EvidenceSnapshot, FieldProvenance, Observation, SnapshotInstrument,
)

UTC = datetime.timezone.utc

_OWNER = "HERMES"
_HEARTBEAT_SURFACE = "AdapterHealth.last_tick_at"


# =========================================================================== injected read-only state views
@dataclass(frozen=True)
class AdapterStateView:
    """An immutable read-only COPY of the OANDA adapter's health/state surface (adapters/base.AdapterHealth +
    AdapterState). Populated by a future wiring under a short read; never a live reference. `last_tick_at_utc` is an
    ISO-8601 UTC string (or None when no message has advanced the surface -> heartbeat truth UNAVAILABLE)."""
    socket_state: str = core.SocketState.UNKNOWN.value
    last_tick_at_utc: Optional[str] = None
    error_count: int = 0
    # connect() proves ONLY auth/API reachability -> a positive socket flag is UNPROVEN for transport health.
    connect_only_reachability: bool = False


@dataclass(frozen=True)
class StreamRuntimeView:
    """An immutable read-only COPY of the main.oanda_stream_task loop signals (provider disconnect / auth / parser /
    silent-stall / reconnect-in-progress / connection generation). Every field defaults to the SAFE (no-fault)
    value; a fault is only present when the live loop actually reported it."""
    provider_disconnect_event: bool = False
    auth_failure: bool = False
    shared_stream_silent: bool = False
    parser_fatal: bool = False
    reconnect_in_progress: bool = False
    # connection_generation: each connect() = a new generation. None => UNAVAILABLE (never manufactured).
    connection_generation: Optional[int] = None


@dataclass(frozen=True)
class InstrumentStateView:
    """An immutable read-only COPY of one watchdog per-instrument classification (already produced by
    utils/hermes_market_hours_health_v1.py via the governed DstAwareMarketHours checker). `validated=False` =>
    unvalidated schedule (WTICO_USD/SPX500_USD) => fail-loud, NO transport vote."""
    instrument: str
    expected_flow: bool
    stale: bool
    validated: bool
    governed_closed: bool = False
    reopening_grace: bool = False


@dataclass(frozen=True)
class LimiterStateView:
    """An immutable read-only COPY of the watchdog reconnect limiter window. `available=False` models the store
    being unreadable (fail closed for non-emergency authority — the core still bypasses for genuine faults)."""
    attempts_in_window: int = 0
    max_per_window: int = 3
    window_seconds: int = 3600
    available: bool = True


@dataclass(frozen=True)
class WatchdogStateView:
    """An immutable read-only COPY of the passive watchdog surfaces the shadow may observe (architecture_phase2 §10).
    NONE of these are consumed or mutated — the view is a frozen copy, so observation MECHANICALLY cannot change the
    authority's ordering, timing, limiter, cooldown, or the one-shot recovery request."""
    current_stream_state: str = "DISCONNECTED"
    instruments: Tuple[InstrumentStateView, ...] = ()
    limiter: LimiterStateView = field(default_factory=LimiterStateView)
    recovery_request_pending: bool = False
    recovery_request_reason: Optional[str] = None
    last_recovery_request_at_utc: Optional[str] = None


# =========================================================================== pure evidence collectors
def _prov(field_name: str, source_module: str, source_symbol: str, representation: str, clock: str,
          freshness_basis: str, availability: AvailabilityClass, authority: str, observation: Observation,
          note: str = "") -> FieldProvenance:
    return FieldProvenance(
        field=field_name, source_module=source_module, source_symbol=source_symbol, owner=_OWNER,
        representation=representation, clock=clock, freshness_basis=freshness_basis, availability=availability,
        authority=authority, observation=observation, read_only_safe=True, note=note,
    )


def collect_socket_evidence(view: AdapterStateView) -> Tuple[dict, List[FieldProvenance]]:
    """socket_state (#5). CONNECTED != flow: a positive connected flag is UNPROVEN (connect() proves only auth
    reachability). DISCONNECTED/FAILED are authoritative. Never elevate CONNECTED to a health assertion."""
    try:
        state = core.SocketState(view.socket_state).value
    except ValueError:
        state = core.SocketState.UNKNOWN.value
    connected = state == core.SocketState.CONNECTED.value
    availability = AvailabilityClass.AMBIGUOUS if connected else AvailabilityClass.AVAILABLE_AUTHORITATIVE
    note = ("CONNECTED is UNPROVEN (connect() proves auth reachability only, not flow); corroborate via heartbeat"
            if connected else "explicit control-plane transition")
    prov = _prov("socket_state", "adapters/oanda.py+utils/watchdog.py", "AdapterState/_current_stream_state",
                 "enum string", "n/a (control-plane state)", "explicit control-plane transition; CONNECTED != flow",
                 availability, "AUTHORITATIVE", Observation.DIRECTLY_OBSERVED, note)
    return {"socket_state": state}, [prov]


def collect_heartbeat_evidence(view: AdapterStateView, *, now_utc: str) -> Tuple[dict, List[FieldProvenance]]:
    """heartbeat_available (#8) + heartbeat_age_s (#9) from AdapterHealth.last_tick_at (bumped on HEARTBEAT AND
    PRICE). last_tick_at None -> UNAVAILABLE (available False / age None) -> fail closed, NEVER healthy-defaulted."""
    if view.last_tick_at_utc is None:
        prov = _prov("heartbeat_available", "adapters/base.py", f"{_HEARTBEAT_SURFACE} (bumped on HEARTBEAT and PRICE)",
                     "bool", "UTC wall (adapter)", "presence of a bump surface", AvailabilityClass.NOT_CURRENTLY_AVAILABLE,
                     "AUTHORITATIVE", Observation.UNAVAILABLE, "no message has advanced last_tick_at -> heartbeat truth unavailable")
        return {"heartbeat_available": False, "heartbeat_age_s": None}, [prov]
    age = _age_seconds(view.last_tick_at_utc, now_utc)
    prov_avail = _prov("heartbeat_available", "adapters/base.py", f"{_HEARTBEAT_SURFACE} (bumped on HEARTBEAT and PRICE)",
                       "bool", "UTC wall (adapter)", "presence of a bump surface", AvailabilityClass.AVAILABLE_DERIVED,
                       "AUTHORITATIVE", Observation.DIRECTLY_OBSERVED, "any provider message advances it")
    prov_age = _prov("heartbeat_age_s", "adapters/base.py", f"now - {_HEARTBEAT_SURFACE}",
                     "seconds float", "UTC wall (adapter)", "age since last ANY provider message",
                     AvailabilityClass.AVAILABLE_DERIVED, "AUTHORITATIVE", Observation.DIRECTLY_OBSERVED,
                     "same-host UTC delta; no provider-vs-local skew")
    return {"heartbeat_available": True, "heartbeat_age_s": age}, [prov_avail, prov_age]


def collect_shared_progress_evidence(view: AdapterStateView, *, now_utc: str) -> Tuple[dict, List[FieldProvenance]]:
    """shared_progress (#13/#14) — any-message liveness read of the SAME AdapterHealth.last_tick_at surface. Because
    this is the SAME physical surface as the heartbeat, its provenance carries the SAME surface_id; the mapper's
    double-count guard (§8) collapses it to the mirror form so the core cannot count it as an independent second
    transport confirmation. Emitting it here (available=True) is deliberately NOT independent corroboration."""
    if view.last_tick_at_utc is None:
        prov = _prov("shared_progress_available", "adapters/base.py", f"{_HEARTBEAT_SURFACE} (any-message liveness)",
                     "bool", "UTC wall (adapter)", "any provider message advances it — DISTINCT from per-instrument",
                     AvailabilityClass.NOT_CURRENTLY_AVAILABLE, "AUTHORITATIVE", Observation.UNAVAILABLE,
                     "no message has advanced last_tick_at")
        # None => mirror heartbeat in the core (no independent claim).
        return {"shared_progress_available": None, "shared_progress_age_s": None}, [prov]
    age = _age_seconds(view.last_tick_at_utc, now_utc)
    prov = _prov("shared_progress_available", "adapters/base.py", f"{_HEARTBEAT_SURFACE} (any-message liveness)",
                 "bool", "UTC wall (adapter)", "any provider message advances it — DISTINCT from per-instrument",
                 AvailabilityClass.AVAILABLE_DERIVED, "AUTHORITATIVE", Observation.DIRECTLY_OBSERVED,
                 "SAME surface as heartbeat -> mapper collapses to mirror (not independent corroboration)")
    prov_age = _prov("shared_progress_age_s", "adapters/base.py", f"now - {_HEARTBEAT_SURFACE} (any-message liveness)",
                     "seconds float", "UTC wall (adapter)", "age since any provider message",
                     AvailabilityClass.AVAILABLE_DERIVED, "AUTHORITATIVE", Observation.DIRECTLY_OBSERVED, "")
    return {"shared_progress_available": True, "shared_progress_age_s": age}, [prov, prov_age]


def collect_fault_evidence(view: StreamRuntimeView) -> Tuple[dict, List[FieldProvenance]]:
    """Genuine-fault surfaces with DISTINCT reason provenance (§7/§8): provider_disconnect_event (#6),
    auth_failure (#7), shared_stream_silent (#10), parser_fatal (#11), reconnect_in_progress (#12). auth !=
    rate-limit != ordinary-malformed != freshness-loss; each keeps its own source symbol."""
    out = {
        "provider_disconnect_event": view.provider_disconnect_event,
        "auth_failure": view.auth_failure,
        "shared_stream_silent": view.shared_stream_silent,
        "parser_fatal": view.parser_fatal,
        "reconnect_in_progress": view.reconnect_in_progress,
    }
    provs = [
        _prov("provider_disconnect_event", "main.py", "oanda_stream_task outer-except / StopAsyncIteration",
              "bool", "UTC (log)", "provider-initiated drop event", AvailabilityClass.AVAILABLE_DERIVED,
              "AUTHORITATIVE", Observation.DIRECTLY_OBSERVED, ""),
        _prov("auth_failure", "adapters/oanda.py", "connect() False / stream status!=200 (401/403)",
              "bool", "UTC (log)", "auth/session reject (distinct from rate-limit 429 and from freshness)",
              AvailabilityClass.AVAILABLE_DERIVED, "AUTHORITATIVE", Observation.DIRECTLY_OBSERVED, ""),
        _prov("shared_stream_silent", "main.py", "asyncio.TimeoutError on stream_iter.__anext__ (silent stall)",
              "bool", "UTC (log)", "no line at all past the stall horizon", AvailabilityClass.AVAILABLE_DERIVED,
              "AUTHORITATIVE", Observation.DIRECTLY_OBSERVED, ""),
        _prov("parser_fatal", "adapters/oanda.py", "stream() async-for/task termination (NOT ordinary json.JSONDecodeError)",
              "bool", "UTC (log)", "enumerated FATAL only; ordinary malformed stays recoverable/advisory",
              AvailabilityClass.AVAILABLE_DERIVED, "AUTHORITATIVE", Observation.DIRECTLY_OBSERVED, ""),
        _prov("reconnect_in_progress", "utils/watchdog.py+main.py", "stream_state==RECOVERING / retry_count>0",
              "bool", "n/a", "reconnect loop active", AvailabilityClass.AVAILABLE_DERIVED,
              "AUTHORITATIVE", Observation.DIRECTLY_OBSERVED, ""),
    ]
    return out, provs


def collect_error_count_evidence(view: AdapterStateView, *, baseline_error_count: int = 0) -> Tuple[dict, List[FieldProvenance]]:
    """error_count_delta (#16) — advisory only (never alone a transport vote). Delta since the last snapshot; never
    negative (a counter reset yields 0, not a spurious negative)."""
    delta = max(0, int(view.error_count) - int(baseline_error_count))
    prov = _prov("error_count_delta", "adapters/base.py", "AdapterHealth.error_count (delta since last snapshot)",
                 "int", "UTC", "error counter movement", AvailabilityClass.AVAILABLE_ADVISORY, "ADVISORY",
                 Observation.DIRECTLY_OBSERVED, "advisory; cannot alone authorise a reconnect")
    return {"error_count_delta": delta}, [prov]


def collect_maintenance_evidence() -> Tuple[dict, List[FieldProvenance]]:
    """provider_maintenance_indication (#15) — OANDA emits NO maintenance signal. UNSAFE_TO_INFER -> UNAVAILABLE,
    value inert False, NEVER inferred from a metals break."""
    prov = _prov("provider_maintenance_indication", "(none)", "n/a — OANDA emits no maintenance signal",
                 "bool", "n/a", "UNSAFE_TO_INFER -> never inferred, value False", AvailabilityClass.NOT_CURRENTLY_AVAILABLE,
                 "ADVISORY", Observation.UNAVAILABLE, "marked unavailable; never inferred from a metals break")
    return {"provider_maintenance_indication": False}, [prov]


def collect_instrument_evidence(view: WatchdogStateView) -> Tuple[Tuple[SnapshotInstrument, ...], List[FieldProvenance]]:
    """Per-instrument evidence (#19) — advisory to transport. Reuses the governed classification already produced
    upstream. `validated=False` (WTICO_USD/SPX500_USD) -> fail-loud, no transport vote. No substring parsing here."""
    insts = tuple(
        SnapshotInstrument(instrument=i.instrument, expected_flow=i.expected_flow, stale=i.stale,
                           validated=i.validated, governed_closed=i.governed_closed, reopening_grace=i.reopening_grace)
        for i in view.instruments
    )
    provs = [
        _prov(f"{i.instrument}.stale", "utils/watchdog.py", "_instrument_health / _instrument_last_tick",
              "bool", "UTC", "tick/M1 freshness past threshold (validated=False -> no transport vote)",
              AvailabilityClass.AVAILABLE_ADVISORY, "ADVISORY", Observation.DIRECTLY_OBSERVED,
              "unvalidated -> fail-loud, no transport vote" if not i.validated else "")
        for i in insts if i.stale
    ]
    return insts, provs


def collect_limiter_evidence(view: WatchdogStateView) -> Tuple[dict, List[FieldProvenance]]:
    """Limiter (#20) — control-plane authoritative. available=False => store unreadable => fail closed for
    non-emergency authority (genuine faults still bypass in the core)."""
    lm = view.limiter
    out = {
        "limiter_attempts_in_window": int(lm.attempts_in_window),
        "limiter_max_per_window": int(lm.max_per_window),
        "limiter_window_seconds": int(lm.window_seconds),
        "limiter_available": bool(lm.available),
    }
    prov = _prov("limiter", "utils/watchdog.py", "_recovery_attempts_window",
                 "struct", "UTC", "rolling window", AvailabilityClass.AVAILABLE_AUTHORITATIVE, "AUTHORITATIVE",
                 Observation.DIRECTLY_OBSERVED, "read-only; never mutated/reordered/consumed")
    return out, [prov]


def collect_connection_generation(view: StreamRuntimeView) -> Tuple[Optional[int], List[FieldProvenance]]:
    """connection_generation (§4/§12) — each connect() = a new generation. None -> UNAVAILABLE (never manufactured)."""
    # connection_generation is bounding/provenance metadata (§4/§12), NOT one of the 21 authority-bearing decision
    # inputs. Its absence is recorded honestly but does NOT force EVIDENCE_INCOMPLETE -> classified ADVISORY.
    if view.connection_generation is None:
        prov = _prov("connection_generation", "main.py", "oanda_stream_task connect()-generation",
                     "int", "n/a", "each connect() = a new generation", AvailabilityClass.NOT_CURRENTLY_AVAILABLE,
                     "ADVISORY", Observation.UNAVAILABLE, "generation not derivable -> UNAVAILABLE, never manufactured")
        return None, [prov]
    prov = _prov("connection_generation", "main.py", "oanda_stream_task connect()-generation",
                 "int", "n/a", "each connect() = a new generation", AvailabilityClass.AVAILABLE_ADVISORY,
                 "ADVISORY", Observation.DIRECTLY_OBSERVED, "")
    return int(view.connection_generation), [prov]


# =========================================================================== snapshot builder
def _age_seconds(then_iso: str, now_iso: str) -> float:
    then = datetime.datetime.fromisoformat(then_iso)
    now = datetime.datetime.fromisoformat(now_iso)
    if then.tzinfo is None or now.tzinfo is None:
        raise SnapshotBuildError("timestamps must be timezone-aware UTC")
    return max(0.0, (now - then).total_seconds())


class SnapshotBuildError(Exception):
    """Raised when the injected views cannot form a coherent snapshot."""


def _completeness(unavailable_authority: bool, advisory_missing: bool) -> EvidenceCompleteness:
    if unavailable_authority:
        return EvidenceCompleteness.INCOMPLETE
    if advisory_missing:
        return EvidenceCompleteness.PARTIAL
    return EvidenceCompleteness.COMPLETE


def build_snapshot(
    *,
    adapter_view: AdapterStateView,
    stream_view: StreamRuntimeView,
    watchdog_view: WatchdogStateView,
    now_utc: str,
    provider: str = "OANDA",
    config_version: Optional[str],
    source_sha: str,
    adapter_version: str,
    runtime_identity: Mapping[str, object],
    heartbeat_soft_horizon_s: float = 15.0,
    heartbeat_hard_horizon_s: float = 45.0,
    baseline_error_count: int = 0,
) -> EvidenceSnapshot:
    """Atomically assemble an immutable EvidenceSnapshot from the injected read-only views (§12 part 2 / §19). PURE:
    no live-global access, no wall-clock read (now_utc supplied), no lock. Missing authority-bearing evidence -> the
    field is UNAVAILABLE and evidence_completeness is downgraded (never a torn or favourably-defaulted snapshot).

    connection_generation is UNAVAILABLE-safe: when the stream view cannot supply it, it is recorded as 0 with an
    UNAVAILABLE provenance (never manufactured as a real generation)."""
    fields: Dict[str, object] = {}
    provenance: List[FieldProvenance] = []
    unavailable: List[Mapping[str, str]] = []

    for collect in (
        lambda: collect_socket_evidence(adapter_view),
        lambda: collect_heartbeat_evidence(adapter_view, now_utc=now_utc),
        lambda: collect_shared_progress_evidence(adapter_view, now_utc=now_utc),
        lambda: collect_fault_evidence(stream_view),
        lambda: collect_error_count_evidence(adapter_view, baseline_error_count=baseline_error_count),
        lambda: collect_maintenance_evidence(),
        lambda: collect_limiter_evidence(watchdog_view),
    ):
        part, provs = collect()
        fields.update(part)
        provenance.extend(provs)

    instruments, inst_provs = collect_instrument_evidence(watchdog_view)
    provenance.extend(inst_provs)
    gen, gen_provs = collect_connection_generation(stream_view)
    provenance.extend(gen_provs)

    # Availability accounting (§2): an AUTHORITATIVE field resolving to UNAVAILABLE -> INCOMPLETE (fail closed).
    # An ADVISORY field that is a genuine coverage GAP -> PARTIAL. A field that is STRUCTURALLY absent by design
    # (NOT_CURRENTLY_AVAILABLE, e.g. provider_maintenance_indication which OANDA never emits) is EXPECTED and does
    # NOT downgrade completeness — it is recorded in unavailable_fields for honesty but the snapshot stays COMPLETE.
    unavailable_authority = False
    advisory_gap = False
    for p in provenance:
        if p.observation == Observation.UNAVAILABLE:
            unavailable.append({"field": p.field, "reason": p.freshness_basis})
            if p.authority == "AUTHORITATIVE":
                unavailable_authority = True
            elif p.availability != AvailabilityClass.NOT_CURRENTLY_AVAILABLE:
                advisory_gap = True

    completeness = _completeness(unavailable_authority, advisory_gap)

    return EvidenceSnapshot(
        provider=provider,
        captured_at_utc=now_utc,
        evaluated_at_utc=now_utc,
        config_version=config_version,
        source_sha=source_sha,
        adapter_version=adapter_version,
        runtime_identity=dict(runtime_identity),
        connection_generation=gen if gen is not None else 0,
        socket_state=str(fields["socket_state"]),
        provider_disconnect_event=bool(fields["provider_disconnect_event"]),
        auth_failure=bool(fields["auth_failure"]),
        heartbeat_available=bool(fields["heartbeat_available"]),
        heartbeat_age_s=fields["heartbeat_age_s"],
        shared_stream_silent=bool(fields["shared_stream_silent"]),
        parser_fatal=bool(fields["parser_fatal"]),
        reconnect_in_progress=bool(fields["reconnect_in_progress"]),
        shared_progress_available=fields["shared_progress_available"],
        shared_progress_age_s=fields["shared_progress_age_s"],
        provider_maintenance_indication=bool(fields["provider_maintenance_indication"]),
        error_count_delta=int(fields["error_count_delta"]),
        heartbeat_soft_horizon_s=heartbeat_soft_horizon_s,
        heartbeat_hard_horizon_s=heartbeat_hard_horizon_s,
        instruments=instruments,
        limiter_attempts_in_window=int(fields["limiter_attempts_in_window"]),
        limiter_max_per_window=int(fields["limiter_max_per_window"]),
        limiter_window_seconds=int(fields["limiter_window_seconds"]),
        limiter_available=bool(fields["limiter_available"]),
        field_provenance=tuple(provenance),
        unavailable_fields=tuple(unavailable),
        contradictory_evidence=(),
        evidence_completeness=completeness,
    )
