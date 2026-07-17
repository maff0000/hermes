"""HERMES shared-stream recovery-authority PURE DECISION CORE v1 — PRODUCTION-OWNED, INERT (Phase 1).

WO-HELM-HERMES-SHARED-STREAM-RECOVERY-PHASE1-PURE-DECISION-CORE-IMPLEMENTATION-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-17. Owner: HERMES (Helm).
Base: canonical main ae80d113 (contains merged PR#108 design contract).

CHANGE HISTORY
  v1  2026-07-17  Promote the audited PR#108 prototype (design/shared_stream_recovery_contract_v1.py) into a
                  production-owned pure decision core under utils/, matching the utils/hermes_<domain>_v1.py
                  convention (frozen dataclasses, stdlib-only, injected clock, versioned, module header). Phase 1
                  is INERT: this module is imported by NOTHING on any live runtime path — only by tests / schemas /
                  docs. It executes no reconnect and performs no side effect.

STATUS: INERT / NOT WIRED / NOT FOR MERGE-BY-IMPLEMENTATION-WO. This module is imported by NO runtime path
(main.py / utils/watchdog.py / provider client / startup / any runner / docker-compose / Dockerfile / cron /
systemd). Proven by the static-guard test tests/test_hermes_shared_stream_recovery_v1.py::
test_static_guard_no_runtime_or_infra_imports_the_core. It exists to make the shared-stream recovery-authority
contract deterministic, testable and PRODUCTION-OWNED, ahead of a Phase-2 shadow adapter.

PURE BOUNDARY (enforced by tests + static scan): standard library ONLY. No Redis / SQL / HTTP / socket /
subprocess / provider SDK client; no filesystem write; no environment/policy-file read; no logging side effect
at import or call; no module singleton; starts no thread/task/timer; NO internal wall-clock (the evaluated
timestamp is INJECTED). Inputs are frozen/immutable; the output DecisionEnvelope is frozen/immutable. The core
takes primitives (transport signals + instrument observations + limiter state + an injected UTC clock) and
returns ONE DecisionEnvelope carrying a single ACTION + reason codes + evidence. It EXECUTES nothing; a future
Phase-3 executor consumes ONLY a decision whose action == RECONNECT_AUTHORISED.

RULING IMPLEMENTED — Option C (see docs/design/shared_stream_recovery/architecture_v1.md): SEPARATE shared-stream
reconnect authority from unvalidated per-instrument freshness. Per-instrument freshness may drive health /
incidents / alerts / recovery PROPOSALS, but MUST NOT alone mutate the shared transport while INDEPENDENT
TRANSPORT EVIDENCE is healthy. A full-stream reconnect requires a TRANSPORT-AUTHORITY condition. Genuine
socket/provider/auth/heartbeat/shared-progress/parser faults retain reconnect authority. A NARROW, reason-coded
emergency limiter bypass exists for genuine provider/socket/auth/shared faults ONLY; ordinary freshness can NEVER
invoke it. When transport truth is MISSING or CONTRADICTORY the core FAILS CLOSED to safety (escalate, never
mutate the shared transport on garbage). This module is byte-for-byte decision-equivalent to the audited
prototype on the shared fixtures (see docs/design/shared_stream_recovery/prototype_parity_matrix_v1.md).
"""
from __future__ import annotations

import datetime
import enum
import hashlib
import json
from dataclasses import dataclass, field
from typing import List, Mapping, Optional, Sequence, Tuple

UTC = datetime.timezone.utc

# Versioned decision-contract identity. contract_version is the version of THIS decision contract (the input +
# envelope shape). config_version (recorded, not gating) is the governed market-hours config version ("3" on the
# canonical base). An unsupported contract_version fails safely (OPERATOR_ESCALATION) rather than mutating transport.
CONTRACT_VERSION = "1"
ENVELOPE_VERSION = "1"
SUPPORTED_CONTRACT_VERSIONS = frozenset({"1"})
SUPPORTED_ENVELOPE_VERSIONS = frozenset({"1"})


# =========================================================================== versioned taxonomies (stable STRING values, never ordinals)
class SocketState(str, enum.Enum):
    """Provider control-plane socket state (mirrors adapters.base.AdapterState values, decoupled)."""
    CONNECTED = "connected"
    CONNECTING = "connecting"
    RECONNECTING = "reconnecting"
    DISCONNECTED = "disconnected"
    FAILED = "failed"
    UNKNOWN = "unknown"          # transport truth missing -> fail closed


class TransportState(str, enum.Enum):
    """Transport-authority state-machine states (architecture_v1 §3). Resolved PURELY from transport signals,
    NEVER from per-instrument freshness."""
    HEALTHY = "TRANSPORT_HEALTHY"           # socket connected + heartbeat fresh -> transport proven alive
    DEGRADED = "TRANSPORT_DEGRADED"         # connected + heartbeat soft-stale -> advisory, not yet authority
    SILENT_UNCONFIRMED = "SILENT_UNCONFIRMED"  # connected + hb stale/missing, or socket unknown -> conflict/fail-closed
    FAULT_CONFIRMED = "FAULT_CONFIRMED"     # shared-stream silent (no line) or fatal parser -> authority
    AUTH_FAILED = "AUTH_FAILED"             # auth/session reject -> authority (bounded re-auth reconnect)
    DISCONNECTED = "DISCONNECTED"           # socket down/failed or provider disconnect event -> authority
    RECONNECTING = "RECONNECTING"           # reconnect already in progress -> take no new action
    RATE_LIMITED = "RATE_LIMITED"           # authority present but application limiter blocks (non-emergency)
    RECOVERED = "RECOVERED"                 # post-reconnect proof success -- ADAPTER-SUPPLIED input/outcome ONLY.


# NOTE on RECOVERED: the pure single-shot core cannot observe a transition. RECOVERED is an adapter-emitted OUTCOME
# after a proof-window success (control-plane reconnect + resumed heartbeat/flow). The core NEVER infers RECOVERED
# from a prior reconnect decision; it is part of the state vocabulary for the Phase-2 adapter to supply as input.


class HeartbeatState(str, enum.Enum):
    FRESH = "FRESH"
    SOFT_STALE = "SOFT_STALE"
    HARD_STALE = "HARD_STALE"
    UNKNOWN = "UNKNOWN"


class Action(str, enum.Enum):
    """The single decided action (architecture_v1 §19). A future executor obeys ONLY RECONNECT_AUTHORISED."""
    NO_ACTION = "NO_ACTION"
    INCIDENT_ONLY = "INCIDENT_ONLY"
    RECOVERY_PROPOSAL_ONLY = "RECOVERY_PROPOSAL_ONLY"
    RECONNECT_AUTHORISED = "RECONNECT_AUTHORISED"
    RECONNECT_RATE_LIMITED = "RECONNECT_RATE_LIMITED"
    OPERATOR_ESCALATION = "OPERATOR_ESCALATION"


class AuthorityStatus(str, enum.Enum):
    AUTHORISED = "authorised"
    NOT_AUTHORISED = "not_authorised"
    FAIL_CLOSED = "fail_closed"          # indeterminate / missing-or-contradictory transport truth -> escalate
    RATE_LIMITED = "rate_limited"        # authority present but throttled by the application limiter


class ReasonCode(str, enum.Enum):
    """The 18 audited reason codes (architecture_v1 §12). Stable string values."""
    SOCKET_DISCONNECTED = "SOCKET_DISCONNECTED"
    PROVIDER_DISCONNECT_EVENT = "PROVIDER_DISCONNECT_EVENT"
    AUTHENTICATION_FAILURE = "AUTHENTICATION_FAILURE"
    HEARTBEAT_STALE = "HEARTBEAT_STALE"
    SHARED_STREAM_PROGRESS_STALE = "SHARED_STREAM_PROGRESS_STALE"
    PARTIAL_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY = "PARTIAL_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY"
    UNVALIDATED_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY = "UNVALIDATED_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY"
    ALL_EXPECTED_FLOW_INSTRUMENTS_STALE = "ALL_EXPECTED_FLOW_INSTRUMENTS_STALE"
    ALL_GOVERNED_INSTRUMENTS_CLOSED = "ALL_GOVERNED_INSTRUMENTS_CLOSED"
    TRANSPORT_SIGNAL_CONFLICT = "TRANSPORT_SIGNAL_CONFLICT"
    RECONNECT_RATE_LIMITED = "RECONNECT_RATE_LIMITED"
    RECONNECT_AUTHORISED = "RECONNECT_AUTHORISED"
    RECONNECT_NOT_AUTHORISED = "RECONNECT_NOT_AUTHORISED"
    RECOVERY_PROPOSAL_ONLY = "RECOVERY_PROPOSAL_ONLY"
    PARSER_EXCEPTION_FATAL = "PARSER_EXCEPTION_FATAL"
    RECONNECT_IN_PROGRESS = "RECONNECT_IN_PROGRESS"
    EMERGENCY_BYPASS_LIMITER = "EMERGENCY_BYPASS_LIMITER"
    NO_TRANSPORT_TRUTH_FAILCLOSED = "NO_TRANSPORT_TRUTH_FAILCLOSED"


# The 18 audited reason codes (all members of ReasonCode). Kept explicit for the contract test + docs.
ALL_REASON_CODES: frozenset = frozenset(c.value for c in ReasonCode)
ALL_ACTIONS: frozenset = frozenset(a.value for a in Action)
ALL_TRANSPORT_STATES: frozenset = frozenset(s.value for s in TransportState)
ALL_AUTHORITY_STATES: frozenset = frozenset(s.value for s in AuthorityStatus)

# Genuine-fault transport states that alone carry reconnect authority AND are eligible for the NARROW emergency
# limiter bypass. Ordinary freshness / per-instrument staleness is deliberately EXCLUDED from this set.
_EMERGENCY_FAULT_STATES: frozenset = frozenset({
    TransportState.DISCONNECTED, TransportState.AUTH_FAILED, TransportState.FAULT_CONFIRMED,
})


class RecoveryContractError(Exception):
    """Structurally-impossible model input (naive/non-UTC datetime, negative age, malformed limiter, bad version
    type). Raised at the CONSTRUCTION boundary ONLY — governed fail-safe outcomes are returned as a
    DecisionEnvelope, never raised."""


# --------------------------------------------------------------------------- UTC clock guard (no wall-clock read)
def _require_utc(value: datetime.datetime, name: str) -> datetime.datetime:
    if not isinstance(value, datetime.datetime):
        raise RecoveryContractError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise RecoveryContractError(f"{name} must be timezone-aware")
    if value.utcoffset() != datetime.timedelta(0):
        raise RecoveryContractError(f"{name} must be UTC (offset 0)")
    return value


def _opt_nonneg(value: Optional[float], name: str) -> Optional[float]:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise RecoveryContractError(f"{name} must be a number or None")
    if value < 0:
        raise RecoveryContractError(f"{name} must be non-negative")
    return float(value)


# =========================================================================== immutable input models
@dataclass(frozen=True)
class LimiterState:
    """Application-level reconnect rate limiter (existing 3/hr contract). available=False models the limiter store
    being unreadable (Redis/SQL down) -> fail closed = treat as exhausted for NON-emergency authority; genuine
    provider/socket/auth/shared faults still bypass."""
    attempts_in_window: int = 0
    max_per_window: int = 3
    window_seconds: int = 3600
    available: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.attempts_in_window, int) or isinstance(self.attempts_in_window, bool) or self.attempts_in_window < 0:
            raise RecoveryContractError("LimiterState.attempts_in_window must be a non-negative int")
        if not isinstance(self.max_per_window, int) or isinstance(self.max_per_window, bool) or self.max_per_window < 1:
            raise RecoveryContractError("LimiterState.max_per_window must be a positive int")
        if not isinstance(self.window_seconds, int) or isinstance(self.window_seconds, bool) or self.window_seconds < 1:
            raise RecoveryContractError("LimiterState.window_seconds must be a positive int")
        if not isinstance(self.available, bool):
            raise RecoveryContractError("LimiterState.available must be a bool")

    @property
    def exhausted(self) -> bool:
        if not self.available:
            return True
        return self.attempts_in_window >= self.max_per_window

    def to_dict(self) -> dict:
        return {
            "attempts_in_window": self.attempts_in_window,
            "max_per_window": self.max_per_window,
            "window_seconds": self.window_seconds,
            "available": self.available,
            "exhausted": self.exhausted,
        }


@dataclass(frozen=True)
class InstrumentObservation:
    """Per-instrument state, already classified upstream by utils/hermes_market_hours_health_v1.py
    (classify_instrument_health). Advisory to transport (architecture_v1 §4). Primitives:
      expected_flow   : governed-OPEN, past reopening grace -> data IS expected now.
      stale           : data stale past threshold.
      validated       : instrument has a governed, validated schedule (WTICO_USD / SPX500_USD -> False).
      governed_closed : governed closed interval -> expected silence (suppressed).
      reopening_grace : just reopened, within bounded grace -> absence tolerated.
    """
    instrument: str
    expected_flow: bool
    stale: bool
    validated: bool
    governed_closed: bool = False
    reopening_grace: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, str) or not self.instrument:
            raise RecoveryContractError("InstrumentObservation.instrument must be a non-empty str")
        for name in ("expected_flow", "stale", "validated", "governed_closed", "reopening_grace"):
            if not isinstance(getattr(self, name), bool):
                raise RecoveryContractError(f"InstrumentObservation.{name} must be a bool")


@dataclass(frozen=True)
class RecoveryDecisionInput:
    """The immutable, validated decision input. Carries EVERY primitive listed in the architecture contract §2/§19.
    Collections are canonically ordered (instruments sorted by name) and deterministically serialisable.

    Transport-truth signals are classified AUTHORITATIVE vs ADVISORY (architecture_v1 §2). REST quote freshness is
    deliberately ABSENT — it is NOT accepted as transport evidence. shared_progress is a recorded governed
    progress-proxy signal; in Phase 1 it mirrors the heartbeat proxy (an independent shared-progress adapter is a
    Phase-2 handover requirement). The corroboration quorum for derived authority is instrument-based
    (ALL validated expected-flow instruments stale — architecture_v1 §6), never a raw shared_progress threshold.
    """
    # ---- contract / clock / provider ----
    evaluated_at_utc: datetime.datetime
    provider: str
    contract_version: str = CONTRACT_VERSION
    config_version: Optional[str] = None
    # ---- authoritative transport signals ----
    socket_state: SocketState = SocketState.UNKNOWN
    provider_disconnect_event: bool = False        # outer-except / StopAsyncIteration provider-initiated drop
    auth_failure: bool = False                      # connect() False / stream 401/403 / session reject
    heartbeat_available: bool = True
    heartbeat_age_s: Optional[float] = None         # now - last_tick_at (bumped by HEARTBEAT or PRICE)
    shared_stream_silent: bool = False              # no line at all (incl heartbeat) past the stall horizon
    parser_fatal: bool = False                      # every message failing to parse -> genuine fault
    reconnect_in_progress: bool = False
    # ---- recorded governed shared-progress signal (Phase 1: mirrors heartbeat proxy unless explicitly supplied) ----
    shared_progress_available: Optional[bool] = None   # None => mirror heartbeat_available
    shared_progress_age_s: Optional[float] = None       # None (with available None) => mirror heartbeat_age_s
    # ---- advisory-only transport context (never alone a transport vote) ----
    provider_maintenance_indication: bool = False
    error_count_delta: int = 0
    # ---- heartbeat horizons (governed config at wire-time; explicit design defaults, NOT hidden constants) ----
    heartbeat_soft_horizon_s: float = 15.0
    heartbeat_hard_horizon_s: float = 45.0
    # ---- instrument observations (canonically ordered by name) ----
    instruments: Tuple[InstrumentObservation, ...] = ()
    # ---- application limiter ----
    limiter: LimiterState = field(default_factory=LimiterState)
    # ---- observability ----
    evidence_summary: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_utc(self.evaluated_at_utc, "evaluated_at_utc")
        if not isinstance(self.provider, str) or not self.provider:
            raise RecoveryContractError("provider must be a non-empty str")
        if not isinstance(self.contract_version, str) or not self.contract_version:
            raise RecoveryContractError("contract_version must be a non-empty str")
        if self.config_version is not None and not isinstance(self.config_version, str):
            raise RecoveryContractError("config_version must be a str or None")
        if not isinstance(self.socket_state, SocketState):
            raise RecoveryContractError("socket_state must be a SocketState")
        for name in ("provider_disconnect_event", "auth_failure", "heartbeat_available", "shared_stream_silent",
                     "parser_fatal", "reconnect_in_progress", "provider_maintenance_indication"):
            if not isinstance(getattr(self, name), bool):
                raise RecoveryContractError(f"{name} must be a bool")
        _opt_nonneg(self.heartbeat_age_s, "heartbeat_age_s")
        _opt_nonneg(self.shared_progress_age_s, "shared_progress_age_s")
        for name in ("heartbeat_soft_horizon_s", "heartbeat_hard_horizon_s"):
            v = getattr(self, name)
            if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
                raise RecoveryContractError(f"{name} must be a non-negative number")
        if self.heartbeat_hard_horizon_s < self.heartbeat_soft_horizon_s:
            raise RecoveryContractError("heartbeat_hard_horizon_s must be >= heartbeat_soft_horizon_s")
        if self.shared_progress_available is not None and not isinstance(self.shared_progress_available, bool):
            raise RecoveryContractError("shared_progress_available must be a bool or None")
        if not isinstance(self.error_count_delta, int) or isinstance(self.error_count_delta, bool):
            raise RecoveryContractError("error_count_delta must be an int")
        if not isinstance(self.limiter, LimiterState):
            raise RecoveryContractError("limiter must be a LimiterState")
        if not isinstance(self.instruments, tuple):
            raise RecoveryContractError("instruments must be a tuple (immutable)")
        names = [i.instrument for i in self.instruments]
        if len(names) != len(set(names)):
            raise RecoveryContractError("duplicate instrument in observations (double-vote risk)")
        # canonicalise ordering deterministically (order MUST NOT change the decision) without mutating a frozen field
        canonical = tuple(sorted(self.instruments, key=lambda i: i.instrument))
        if canonical != self.instruments:
            object.__setattr__(self, "instruments", canonical)

    # ---- derived canonical instrument collections (architecture_v1 §19) ----
    @property
    def expected_flow_instruments(self) -> Tuple[str, ...]:
        return tuple(i.instrument for i in self.instruments if i.expected_flow)

    @property
    def stale_governed_instruments(self) -> Tuple[str, ...]:
        return tuple(i.instrument for i in self.instruments if i.stale and not i.governed_closed)

    @property
    def stale_unvalidated_instruments(self) -> Tuple[str, ...]:
        return tuple(i.instrument for i in self.instruments
                     if i.stale and not i.validated and not i.governed_closed)

    @property
    def expected_closed_instruments(self) -> Tuple[str, ...]:
        return tuple(i.instrument for i in self.instruments if i.governed_closed)

    @property
    def reopening_grace_instruments(self) -> Tuple[str, ...]:
        return tuple(i.instrument for i in self.instruments if i.reopening_grace)

    @property
    def _validated_expected_flow(self) -> Tuple[InstrumentObservation, ...]:
        return tuple(i for i in self.instruments if i.expected_flow and i.validated)

    @property
    def all_governed_closed(self) -> bool:
        return len(self.instruments) > 0 and all(i.governed_closed for i in self.instruments)

    @property
    def effective_shared_progress_available(self) -> bool:
        return self.heartbeat_available if self.shared_progress_available is None else self.shared_progress_available

    @property
    def effective_shared_progress_age_s(self) -> Optional[float]:
        if self.shared_progress_available is None and self.shared_progress_age_s is None:
            return self.heartbeat_age_s
        return self.shared_progress_age_s

    def canonical_dict(self) -> dict:
        """Deterministic, side-effect-free canonical serialisation of the INPUT (used for the deterministic
        decision_id hash and for evidence). No wall-clock, no randomness."""
        return {
            "contract_version": self.contract_version,
            "evaluated_at_utc": self.evaluated_at_utc.astimezone(UTC).isoformat(),
            "provider": self.provider,
            "config_version": self.config_version,
            "socket_state": self.socket_state.value,
            "provider_disconnect_event": self.provider_disconnect_event,
            "auth_failure": self.auth_failure,
            "heartbeat_available": self.heartbeat_available,
            "heartbeat_age_s": self.heartbeat_age_s,
            "shared_stream_silent": self.shared_stream_silent,
            "parser_fatal": self.parser_fatal,
            "reconnect_in_progress": self.reconnect_in_progress,
            "shared_progress_available": self.effective_shared_progress_available,
            "shared_progress_age_s": self.effective_shared_progress_age_s,
            "provider_maintenance_indication": self.provider_maintenance_indication,
            "error_count_delta": self.error_count_delta,
            "heartbeat_soft_horizon_s": self.heartbeat_soft_horizon_s,
            "heartbeat_hard_horizon_s": self.heartbeat_hard_horizon_s,
            "limiter": self.limiter.to_dict(),
            "instruments": [
                {"instrument": i.instrument, "expected_flow": i.expected_flow, "stale": i.stale,
                 "validated": i.validated, "governed_closed": i.governed_closed, "reopening_grace": i.reopening_grace}
                for i in self.instruments
            ],
        }

    def canonical_json(self) -> str:
        return json.dumps(self.canonical_dict(), sort_keys=True, separators=(",", ":"))


# =========================================================================== immutable output envelope (architecture_v1 §19)
@dataclass(frozen=True)
class DecisionEnvelope:
    contract_version: str
    envelope_version: str
    decision_id: str
    evaluated_at_utc: str
    provider: str
    transport_state: str
    socket_connected: bool
    heartbeat_state: str
    heartbeat_age_s: Optional[float]
    shared_progress_state: str
    shared_progress_age_s: Optional[float]
    expected_flow_instruments: Tuple[str, ...]
    stale_governed_instruments: Tuple[str, ...]
    stale_unvalidated_instruments: Tuple[str, ...]
    authority_status: str
    action: str
    reason_codes: Tuple[str, ...]
    reconnect_authorised: bool
    emergency_bypass_eligible: bool
    emergency_bypass_reason: Optional[str]
    adapter_bounding_required: bool
    limiter_state: Mapping[str, object]
    evidence_summary: Mapping[str, object]
    config_version: Optional[str]

    def to_dict(self) -> dict:
        return {
            "contract_version": self.contract_version,
            "envelope_version": self.envelope_version,
            "decision_id": self.decision_id,
            "evaluated_at_utc": self.evaluated_at_utc,
            "provider": self.provider,
            "transport_state": self.transport_state,
            "socket_connected": self.socket_connected,
            "heartbeat_state": self.heartbeat_state,
            "heartbeat_age_s": self.heartbeat_age_s,
            "shared_progress_state": self.shared_progress_state,
            "shared_progress_age_s": self.shared_progress_age_s,
            "expected_flow_instruments": list(self.expected_flow_instruments),
            "stale_governed_instruments": list(self.stale_governed_instruments),
            "stale_unvalidated_instruments": list(self.stale_unvalidated_instruments),
            "authority_status": self.authority_status,
            "action": self.action,
            "reason_codes": list(self.reason_codes),
            "reconnect_authorised": self.reconnect_authorised,
            "emergency_bypass_eligible": self.emergency_bypass_eligible,
            "emergency_bypass_reason": self.emergency_bypass_reason,
            "adapter_bounding_required": self.adapter_bounding_required,
            "limiter_state": dict(self.limiter_state),
            "evidence_summary": dict(self.evidence_summary),
            "config_version": self.config_version,
        }

    def to_json(self) -> str:
        """Stable JSON representation (deterministic key order)."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


def envelope_from_dict(payload: Mapping[str, object]) -> DecisionEnvelope:
    """Decode a persisted envelope dict, failing SAFELY on an unsupported envelope version. Reused by tests /
    Phase-2 shadow comparison. No unsafe deserialisation (plain dict access, bounded, no eval)."""
    ver = payload.get("envelope_version")
    if ver not in SUPPORTED_ENVELOPE_VERSIONS:
        raise RecoveryContractError(f"unsupported envelope_version {ver!r} (supported: {sorted(SUPPORTED_ENVELOPE_VERSIONS)})")
    def _tup(key: str) -> Tuple[str, ...]:
        return tuple(payload.get(key) or ())
    return DecisionEnvelope(
        contract_version=str(payload["contract_version"]),
        envelope_version=str(payload["envelope_version"]),
        decision_id=str(payload["decision_id"]),
        evaluated_at_utc=str(payload["evaluated_at_utc"]),
        provider=str(payload["provider"]),
        transport_state=str(payload["transport_state"]),
        socket_connected=bool(payload["socket_connected"]),
        heartbeat_state=str(payload["heartbeat_state"]),
        heartbeat_age_s=payload.get("heartbeat_age_s"),
        shared_progress_state=str(payload["shared_progress_state"]),
        shared_progress_age_s=payload.get("shared_progress_age_s"),
        expected_flow_instruments=_tup("expected_flow_instruments"),
        stale_governed_instruments=_tup("stale_governed_instruments"),
        stale_unvalidated_instruments=_tup("stale_unvalidated_instruments"),
        authority_status=str(payload["authority_status"]),
        action=str(payload["action"]),
        reason_codes=_tup("reason_codes"),
        reconnect_authorised=bool(payload["reconnect_authorised"]),
        emergency_bypass_eligible=bool(payload["emergency_bypass_eligible"]),
        emergency_bypass_reason=payload.get("emergency_bypass_reason"),
        adapter_bounding_required=bool(payload["adapter_bounding_required"]),
        limiter_state=dict(payload.get("limiter_state") or {}),
        evidence_summary=dict(payload.get("evidence_summary") or {}),
        config_version=payload.get("config_version"),
    )


# =========================================================================== pure helpers
def _heartbeat_state(inp: RecoveryDecisionInput) -> HeartbeatState:
    hb = inp.heartbeat_age_s
    if not inp.heartbeat_available or hb is None:
        return HeartbeatState.UNKNOWN
    if hb <= inp.heartbeat_soft_horizon_s:
        return HeartbeatState.FRESH
    if hb <= inp.heartbeat_hard_horizon_s:
        return HeartbeatState.SOFT_STALE
    return HeartbeatState.HARD_STALE


def resolve_transport_state(inp: RecoveryDecisionInput) -> Tuple[TransportState, HeartbeatState]:
    """Resolve the transport-authority state PURELY from transport signals (never from instrument freshness).
    Total function: every input tuple maps to exactly one state. Fails closed on missing/contradictory truth."""
    hb_state = _heartbeat_state(inp)
    # 1. reconnect already running -> do not stack another action
    if inp.reconnect_in_progress:
        return TransportState.RECONNECTING, hb_state
    # 2. authentication/session failure -> authority (highest genuine-fault priority)
    if inp.auth_failure:
        return TransportState.AUTH_FAILED, hb_state
    # 3. explicit socket-down / provider-initiated disconnect -> authority
    if inp.socket_state in (SocketState.DISCONNECTED, SocketState.FAILED) or inp.provider_disconnect_event:
        return TransportState.DISCONNECTED, hb_state
    if inp.socket_state in (SocketState.CONNECTING, SocketState.RECONNECTING):
        return TransportState.RECONNECTING, hb_state
    # 4. genuine shared-stream fault: no line at all (incl heartbeat) or fatal parser
    if inp.shared_stream_silent or inp.parser_fatal:
        return TransportState.FAULT_CONFIRMED, hb_state
    # 5. transport truth missing entirely -> fail closed (do not assert healthy)
    if inp.socket_state == SocketState.UNKNOWN:
        return TransportState.SILENT_UNCONFIRMED, hb_state
    # socket == CONNECTED -> lean on heartbeat truth
    if hb_state == HeartbeatState.FRESH:
        return TransportState.HEALTHY, hb_state
    if hb_state == HeartbeatState.SOFT_STALE:
        return TransportState.DEGRADED, hb_state
    # HARD_STALE or UNKNOWN heartbeat while socket claims connected -> conflict -> fail closed
    return TransportState.SILENT_UNCONFIRMED, hb_state


def _dedupe(reasons: Sequence[ReasonCode]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(r.value for r in reasons))


def _authority_status(action: Action) -> AuthorityStatus:
    if action == Action.RECONNECT_AUTHORISED:
        return AuthorityStatus.AUTHORISED
    if action == Action.RECONNECT_RATE_LIMITED:
        return AuthorityStatus.RATE_LIMITED
    if action == Action.OPERATOR_ESCALATION:
        return AuthorityStatus.FAIL_CLOSED
    return AuthorityStatus.NOT_AUTHORISED


def _decision_id(inp: RecoveryDecisionInput, transport_state: TransportState, action: Action,
                 reasons: Tuple[str, ...], correlation_id: Optional[str]) -> str:
    """Deterministic: a supplied correlation id, else sha256[:16] over the canonical INPUT + decided
    transport_state/action/sorted-reasons. NO Date.now, NO randomness."""
    if correlation_id is not None:
        return correlation_id
    payload = json.dumps(
        {"input": inp.canonical_dict(), "transport_state": transport_state.value,
         "action": action.value, "reasons": sorted(reasons)},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _build(inp: RecoveryDecisionInput, transport_state: TransportState, hb_state: HeartbeatState,
           action: Action, reasons: Tuple[str, ...], evidence: Mapping[str, object],
           evaluated_at_utc: str, correlation_id: Optional[str]) -> DecisionEnvelope:
    emergency = transport_state in _EMERGENCY_FAULT_STATES and action == Action.RECONNECT_AUTHORISED
    bypass_reason: Optional[str] = None
    if emergency:
        # the primary genuine-fault reason drives the narrow bypass boundary
        for r in reasons:
            if r in (ReasonCode.SOCKET_DISCONNECTED.value, ReasonCode.PROVIDER_DISCONNECT_EVENT.value,
                     ReasonCode.AUTHENTICATION_FAILURE.value, ReasonCode.PARSER_EXCEPTION_FATAL.value,
                     ReasonCode.SHARED_STREAM_PROGRESS_STALE.value):
                bypass_reason = r
                break
    evd = dict(inp.evidence_summary)
    evd.update(evidence)
    return DecisionEnvelope(
        contract_version=inp.contract_version,
        envelope_version=ENVELOPE_VERSION,
        decision_id=_decision_id(inp, transport_state, action, reasons, correlation_id),
        evaluated_at_utc=evaluated_at_utc,
        provider=inp.provider,
        transport_state=transport_state.value,
        socket_connected=(inp.socket_state == SocketState.CONNECTED),
        heartbeat_state=hb_state.value,
        heartbeat_age_s=inp.heartbeat_age_s,
        shared_progress_state=hb_state.value,
        shared_progress_age_s=inp.effective_shared_progress_age_s,
        expected_flow_instruments=inp.expected_flow_instruments,
        stale_governed_instruments=inp.stale_governed_instruments,
        stale_unvalidated_instruments=inp.stale_unvalidated_instruments,
        authority_status=_authority_status(action).value,
        action=action.value,
        reason_codes=reasons,
        reconnect_authorised=(action == Action.RECONNECT_AUTHORISED),
        emergency_bypass_eligible=emergency,
        emergency_bypass_reason=bypass_reason,
        adapter_bounding_required=emergency,
        limiter_state=inp.limiter.to_dict(),
        evidence_summary=evd,
        config_version=inp.config_version,
    )


def _authorised(inp, transport_state, hb_state, reasons: List[ReasonCode], emergency: bool,
                evaluated_at_utc, correlation_id) -> DecisionEnvelope:
    """Transport authority is present. Genuine faults bypass the limiter; application-derived authority obeys it."""
    reasons = list(reasons)
    if emergency:
        if inp.limiter.exhausted:
            reasons.append(ReasonCode.EMERGENCY_BYPASS_LIMITER)
        reasons.append(ReasonCode.RECONNECT_AUTHORISED)
        action = Action.RECONNECT_AUTHORISED
        evidence = {"emergency_bypass": True, "limiter_exhausted": inp.limiter.exhausted}
    else:
        if inp.limiter.exhausted:
            reasons.append(ReasonCode.RECONNECT_RATE_LIMITED)
            action = Action.RECONNECT_RATE_LIMITED
        else:
            reasons.append(ReasonCode.RECONNECT_AUTHORISED)
            action = Action.RECONNECT_AUTHORISED
        evidence = {"emergency_bypass": False, "limiter_exhausted": inp.limiter.exhausted}
    return _build(inp, transport_state, hb_state, action, _dedupe(reasons), evidence, evaluated_at_utc, correlation_id)


def _proposal_or_noaction(inp, transport_state, hb_state, reasons: List[ReasonCode],
                          evaluated_at_utc, correlation_id) -> DecisionEnvelope:
    """Transport is alive. Stale instruments -> advisory proposal (no transport mutation). Else -> no action."""
    reasons = list(reasons)
    stale = inp.stale_governed_instruments
    unval = set(inp.stale_unvalidated_instruments)
    if stale:
        if unval:
            reasons.append(ReasonCode.UNVALIDATED_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY)
        if any(s not in unval for s in stale):
            reasons.append(ReasonCode.PARTIAL_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY)
        reasons.append(ReasonCode.RECOVERY_PROPOSAL_ONLY)
        action = Action.RECOVERY_PROPOSAL_ONLY
        evidence = {"note": "instrument stale but transport proven alive; proposal only (no transport mutation)"}
    else:
        action = Action.NO_ACTION
        evidence = {"note": "transport alive; no stale instruments"}
    return _build(inp, transport_state, hb_state, action, _dedupe(reasons), evidence, evaluated_at_utc, correlation_id)


# =========================================================================== the primary pure entry point
def decide(inp: RecoveryDecisionInput, *, correlation_id: Optional[str] = None) -> DecisionEnvelope:
    """Return the immutable shared-stream recovery-authority DecisionEnvelope. PURE: executes nothing, mutates
    nothing, reads no clock. Deterministic precedence (architecture_v1 §5/§8/§11/§13):

      0. malformed / unsupported contract-version -> FAIL SAFE (OPERATOR_ESCALATION, no reconnect).
      1. reconnect already in progress            -> NO_ACTION (no duplicate authority).
      2. genuine socket/provider disconnect        -> RECONNECT_AUTHORISED (emergency; narrow limiter bypass).
      3. auth/session failure                       -> RECONNECT_AUTHORISED (emergency).
      4. fatal parser / shared-stream silent        -> RECONNECT_AUTHORISED (emergency).
      5. SILENT_UNCONFIRMED (hb hard-stale/missing or socket unknown):
           * corroborated by ALL validated expected-flow instruments stale -> RECONNECT_AUTHORISED (limiter applies);
           * all-governed-closed / no validated expected-flow -> FAIL CLOSED (escalate if unvalidated stale, else NO_ACTION);
           * some validated instrument still progressing -> transport alive -> proposal/NO_ACTION.
      6. transport proven alive (HEALTHY / DEGRADED):
           * all-governed-closed -> NO_ACTION;
           * stale instruments (validated partial and/or unvalidated) -> RECOVERY_PROPOSAL_ONLY, never a transport mutation.

    Genuine transport evidence OUTRANKS ordinary freshness. Ordinary freshness can NEVER invoke the emergency
    bypass and can NEVER alone authorise a shared reconnect.
    """
    if not isinstance(inp, RecoveryDecisionInput):
        raise RecoveryContractError("decide() requires a RecoveryDecisionInput")

    # ---- 0. governed fail-safe: unsupported contract version / defensive clock re-check ----
    if inp.contract_version not in SUPPORTED_CONTRACT_VERSIONS or inp.evaluated_at_utc.utcoffset() != datetime.timedelta(0):
        return _build(
            inp, TransportState.SILENT_UNCONFIRMED, HeartbeatState.UNKNOWN, Action.OPERATOR_ESCALATION,
            _dedupe([ReasonCode.NO_TRANSPORT_TRUTH_FAILCLOSED, ReasonCode.TRANSPORT_SIGNAL_CONFLICT]),
            {"fail_closed": True, "cause": "unsupported contract_version"},
            "INVALID_CONTRACT", correlation_id,
        )

    ts_iso = inp.evaluated_at_utc.astimezone(UTC).isoformat()
    transport_state, hb_state = resolve_transport_state(inp)

    # ---- 1. reconnect already in progress -> no duplicate authority ----
    if transport_state == TransportState.RECONNECTING:
        return _build(inp, transport_state, hb_state, Action.NO_ACTION,
                      _dedupe([ReasonCode.RECONNECT_IN_PROGRESS]),
                      {"note": "reconnect already running; do not stack"}, ts_iso, correlation_id)

    # ---- 2-4. genuine transport faults -> reconnect authority (emergency bypasses the limiter) ----
    if transport_state == TransportState.DISCONNECTED:
        r = ReasonCode.PROVIDER_DISCONNECT_EVENT if inp.provider_disconnect_event else ReasonCode.SOCKET_DISCONNECTED
        return _authorised(inp, transport_state, hb_state, [r], True, ts_iso, correlation_id)
    if transport_state == TransportState.AUTH_FAILED:
        return _authorised(inp, transport_state, hb_state, [ReasonCode.AUTHENTICATION_FAILURE], True, ts_iso, correlation_id)
    if transport_state == TransportState.FAULT_CONFIRMED:
        r = ReasonCode.PARSER_EXCEPTION_FATAL if inp.parser_fatal else ReasonCode.SHARED_STREAM_PROGRESS_STALE
        return _authorised(inp, transport_state, hb_state, [r], True, ts_iso, correlation_id)

    # ---- 5. SILENT_UNCONFIRMED -> authority ONLY if corroborated by a validated shared-progress loss ----
    if transport_state == TransportState.SILENT_UNCONFIRMED:
        reasons: List[ReasonCode] = [ReasonCode.TRANSPORT_SIGNAL_CONFLICT]
        validated_flow = inp._validated_expected_flow
        if validated_flow and all(i.stale for i in validated_flow):
            reasons += [ReasonCode.HEARTBEAT_STALE, ReasonCode.ALL_EXPECTED_FLOW_INSTRUMENTS_STALE,
                        ReasonCode.SHARED_STREAM_PROGRESS_STALE]
            return _authorised(inp, transport_state, hb_state, reasons, False, ts_iso, correlation_id)
        if inp.all_governed_closed or not validated_flow:
            unval = inp.stale_unvalidated_instruments
            if inp.all_governed_closed:
                reasons.append(ReasonCode.ALL_GOVERNED_INSTRUMENTS_CLOSED)
                action = Action.NO_ACTION
            else:
                reasons.append(ReasonCode.UNVALIDATED_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY)
                action = Action.OPERATOR_ESCALATION if unval else Action.NO_ACTION
            reasons.append(ReasonCode.RECONNECT_NOT_AUTHORISED)
            return _build(inp, transport_state, hb_state, action, _dedupe(reasons),
                          {"fail_closed": True, "note": "heartbeat gap w/o validated corroboration"},
                          ts_iso, correlation_id)
        # some validated expected-flow instrument still progressing -> transport alive despite the heartbeat gap
        reasons.append(ReasonCode.RECONNECT_NOT_AUTHORISED)
        return _proposal_or_noaction(inp, transport_state, hb_state, reasons, ts_iso, correlation_id)

    # ---- 6. transport proven alive (HEALTHY / DEGRADED) -> NO reconnect authority ----
    if inp.all_governed_closed:
        return _build(inp, transport_state, hb_state, Action.NO_ACTION,
                      _dedupe([ReasonCode.ALL_GOVERNED_INSTRUMENTS_CLOSED, ReasonCode.RECONNECT_NOT_AUTHORISED]),
                      {"note": "all governed instruments closed; transport healthy"}, ts_iso, correlation_id)
    return _proposal_or_noaction(inp, transport_state, hb_state, [ReasonCode.RECONNECT_NOT_AUTHORISED],
                                 ts_iso, correlation_id)
