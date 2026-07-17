"""HERMES shared-stream recovery-authority decision core v1 — PURE, INERT, DESIGN-ONLY.

WO-HELM-HERMES-SHARED-STREAM-RECOVERY-CONTRACT-DESIGN-0001. Created (UTC): 2026-07-17. Owner: HERMES (Helm).

STATUS: INERT. This module lives under ``design/`` and is imported by NOTHING on any live runtime
path (main.py / utils/watchdog.py / docker-compose / startup / DI). It exists to PROVE the shared-
stream recovery-authority contract is deterministic and testable. It has NO side effects: stdlib
only, no Redis / SQL / network / file-write / env-read / thread / singleton / logging at import or call.

RULING IMPLEMENTED — Option C: SEPARATE shared-stream reconnect authority from unvalidated per-instrument
freshness. Per-instrument freshness may drive health / incidents / alerts / recovery-PROPOSALS, but MUST
NOT alone mutate the shared transport while INDEPENDENT TRANSPORT EVIDENCE is healthy. A full-stream
reconnect requires a TRANSPORT-AUTHORITY condition. Per-instrument fail-loud health is retained
(WTICO_USD / SPX500_USD stay visibly stale). Provider schedules are never invented. Genuine faults are
never silenced. Rate limiting is retained. When transport truth is MISSING or CONTRADICTORY, the core
FAILS CLOSED to safety (it does NOT authorise a destructive shared-transport reconnect on garbage; it
escalates to the operator).

ROOT CAUSE this contract corrects (grounded in deployed source 71ea3bd; same files at 8de293de):
    utils/watchdog._evaluate_per_instrument_recovery() grants a full-stream reconnect on the authority of
    a SINGLE sustained-RED instrument (``sustained[0]``) with NO independent transport check. On 2026-07-16
    the metals-break stale of SPX500_USD (+ co-red WTICO_USD, both UNVALIDATED) drove 3 rate-limited full-
    stream reconnects (21:09/21:19/21:29Z) while the socket stayed conn=CONNECTED, fault=NONE and heartbeats
    kept flowing. Instrument RED (freshness) was wrongly coupled to shared-transport mutation.

TRANSPORT-ALIVE SIGNAL (grounded): adapters/oanda.py bumps AdapterHealth.last_tick_at on BOTH HEARTBEAT
    and PRICE messages (line ~141). Heartbeat freshness is therefore a SHARED transport-alive signal that
    is INDEPENDENT of any single instrument's tick/candle freshness. main.py already detects a genuine
    shared-stream stall (no line at all, incl. heartbeat) via an asyncio.TimeoutError on the stream
    iterator (~line 707). These are the transport-truth seams this contract formalises. REST quote
    freshness is NOT accepted as proof of transport health.

The pure core takes PRIMITIVES (transport signals + instrument states + limiter state + injected clock)
and returns an immutable DecisionEnvelope with a single ACTION + reason codes + evidence. It EXECUTES
nothing. A future executor consumes ONLY a decision whose action is RECONNECT_AUTHORISED.
"""
from __future__ import annotations

import datetime
import hashlib
import json
from dataclasses import dataclass, field
from typing import List, Mapping, Optional, Sequence, Tuple

UTC = datetime.timezone.utc
DECISION_VERSION = "1"

# --------------------------------------------------------------------------- socket state (mirrors adapters.base.AdapterState values, decoupled)
SOCKET_CONNECTED = "connected"
SOCKET_CONNECTING = "connecting"
SOCKET_RECONNECTING = "reconnecting"
SOCKET_DISCONNECTED = "disconnected"
SOCKET_FAILED = "failed"
SOCKET_UNKNOWN = "unknown"          # transport truth missing -> fail closed
_KNOWN_SOCKET = frozenset({SOCKET_CONNECTED, SOCKET_CONNECTING, SOCKET_RECONNECTING,
                           SOCKET_DISCONNECTED, SOCKET_FAILED, SOCKET_UNKNOWN})

# --------------------------------------------------------------------------- transport-authority state machine states (§3)
TRANSPORT_HEALTHY = "TRANSPORT_HEALTHY"          # socket connected + heartbeat fresh -> transport proven alive
TRANSPORT_DEGRADED = "TRANSPORT_DEGRADED"        # connected + heartbeat in soft-stale band -> advisory, not yet authority
SILENT_UNCONFIRMED = "SILENT_UNCONFIRMED"        # connected + heartbeat stale/missing -> conflict; authority ONLY if corroborated
FAULT_CONFIRMED = "FAULT_CONFIRMED"              # shared-stream silent (no line at all) or fatal parser failure -> authority
AUTH_FAILED = "AUTH_FAILED"                      # auth / session reject -> authority (bounded re-auth reconnect)
DISCONNECTED = "DISCONNECTED"                    # socket down or provider disconnect event -> authority
RECONNECTING = "RECONNECTING"                    # reconnect already in progress -> take no new action
RATE_LIMITED = "RATE_LIMITED"                    # authority present but application limiter blocks (non-emergency)
RECOVERED = "RECOVERED"                          # transitional: post-reconnect proof succeeded (emitted by adapter, see NOTE)
ALL_TRANSPORT_STATES = frozenset({
    TRANSPORT_HEALTHY, TRANSPORT_DEGRADED, SILENT_UNCONFIRMED, FAULT_CONFIRMED, AUTH_FAILED,
    DISCONNECTED, RECONNECTING, RATE_LIMITED, RECOVERED,
})
# NOTE on RECOVERED: the pure single-shot core cannot observe a transition; RECOVERED is emitted by the
# thin runtime adapter after a proof-window success (control-plane connect + resumed heartbeat/flow). It
# is documented here to complete the state machine; the core never returns it.

# --------------------------------------------------------------------------- actions (§19) — executor obeys ONLY RECONNECT_AUTHORISED
NO_ACTION = "NO_ACTION"
INCIDENT_ONLY = "INCIDENT_ONLY"
RECOVERY_PROPOSAL_ONLY = "RECOVERY_PROPOSAL_ONLY"
RECONNECT_AUTHORISED = "RECONNECT_AUTHORISED"
RECONNECT_RATE_LIMITED = "RECONNECT_RATE_LIMITED"
OPERATOR_ESCALATION = "OPERATOR_ESCALATION"
ALL_ACTIONS = frozenset({NO_ACTION, INCIDENT_ONLY, RECOVERY_PROPOSAL_ONLY, RECONNECT_AUTHORISED,
                         RECONNECT_RATE_LIMITED, OPERATOR_ESCALATION})

# --------------------------------------------------------------------------- reason codes (§12)
SOCKET_DISCONNECTED_R = "SOCKET_DISCONNECTED"
PROVIDER_DISCONNECT_EVENT = "PROVIDER_DISCONNECT_EVENT"
AUTHENTICATION_FAILURE = "AUTHENTICATION_FAILURE"
HEARTBEAT_STALE = "HEARTBEAT_STALE"
SHARED_STREAM_PROGRESS_STALE = "SHARED_STREAM_PROGRESS_STALE"
PARTIAL_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY = "PARTIAL_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY"
UNVALIDATED_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY = "UNVALIDATED_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY"
ALL_EXPECTED_FLOW_INSTRUMENTS_STALE = "ALL_EXPECTED_FLOW_INSTRUMENTS_STALE"
ALL_GOVERNED_INSTRUMENTS_CLOSED = "ALL_GOVERNED_INSTRUMENTS_CLOSED"
TRANSPORT_SIGNAL_CONFLICT = "TRANSPORT_SIGNAL_CONFLICT"
RECONNECT_RATE_LIMITED_R = "RECONNECT_RATE_LIMITED"
RECONNECT_AUTHORISED_R = "RECONNECT_AUTHORISED"
RECONNECT_NOT_AUTHORISED = "RECONNECT_NOT_AUTHORISED"
RECOVERY_PROPOSAL_ONLY_R = "RECOVERY_PROPOSAL_ONLY"
# extra codes (superset permitted; the 15 mandated above are all present)
PARSER_EXCEPTION_FATAL = "PARSER_EXCEPTION_FATAL"
RECONNECT_IN_PROGRESS = "RECONNECT_IN_PROGRESS"
EMERGENCY_BYPASS_LIMITER = "EMERGENCY_BYPASS_LIMITER"
NO_TRANSPORT_TRUTH_FAILCLOSED = "NO_TRANSPORT_TRUTH_FAILCLOSED"

MANDATED_REASON_CODES = frozenset({
    SOCKET_DISCONNECTED_R, PROVIDER_DISCONNECT_EVENT, AUTHENTICATION_FAILURE, HEARTBEAT_STALE,
    SHARED_STREAM_PROGRESS_STALE, PARTIAL_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY,
    UNVALIDATED_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY, ALL_EXPECTED_FLOW_INSTRUMENTS_STALE,
    ALL_GOVERNED_INSTRUMENTS_CLOSED, TRANSPORT_SIGNAL_CONFLICT, RECONNECT_RATE_LIMITED_R,
    RECONNECT_AUTHORISED_R, RECONNECT_NOT_AUTHORISED, RECOVERY_PROPOSAL_ONLY_R,
})


# =========================================================================== inputs (primitives)
@dataclass(frozen=True)
class TransportSignals:
    """Independent transport-truth signals. Authoritative signals can drive reconnect authority;
    advisory signals cannot alone. REST quote freshness is deliberately ABSENT (not proof of transport).

    Authoritative: socket_state, provider_disconnect_event, auth_failure, heartbeat_age_s,
                   shared_stream_silent, parser_fatal, reconnect_in_progress.
    Advisory only : provider_maintenance_indication (never alone), error_count_delta (context).
    """
    socket_state: str = SOCKET_UNKNOWN
    provider_disconnect_event: bool = False        # outer-except / StopAsyncIteration provider-initiated drop
    auth_failure: bool = False                      # connect() False / stream status 401/403 / session reject
    heartbeat_age_s: Optional[float] = None         # now - last_tick_at (bumped by HEARTBEAT or PRICE); None = unavailable
    heartbeat_soft_horizon_s: float = 15.0          # > soft -> DEGRADED (advisory)
    heartbeat_hard_horizon_s: float = 45.0          # > hard (or None) while connected -> SILENT_UNCONFIRMED
    shared_stream_silent: bool = False              # no line at all (incl. heartbeat) past the stall horizon -> genuine fault
    parser_fatal: bool = False                      # every message failing to parse -> genuine fault
    reconnect_in_progress: bool = False
    provider_maintenance_indication: bool = False   # advisory only
    error_count_delta: int = 0                       # advisory context only


@dataclass(frozen=True)
class InstrumentState:
    """Per-instrument state, already classified by the market-hours health core (utils.hermes_market_hours_health_v1).

    expected_flow   : instrument is governed-OPEN, past reopening grace -> data IS expected right now.
    stale           : data is stale past threshold.
    validated       : instrument has a governed, validated schedule (WTICO_USD / SPX500_USD -> False).
    governed_closed : instrument is in a governed closed interval -> expected silence (suppressed).
    """
    instrument: str
    expected_flow: bool
    stale: bool
    validated: bool
    governed_closed: bool = False


@dataclass(frozen=True)
class LimiterState:
    """Application-level reconnect rate limiter (existing 3/hr contract). available=False models the
    limiter store being unreadable (Redis/SQL down) -> fail closed = treat as exhausted for NON-emergency
    authority; genuine provider/socket faults still bypass."""
    attempts_in_window: int = 0
    max_per_window: int = 3
    window_seconds: int = 3600
    available: bool = True

    @property
    def exhausted(self) -> bool:
        if not self.available:
            return True
        return self.attempts_in_window >= self.max_per_window


# =========================================================================== output (immutable envelope) §19
@dataclass(frozen=True)
class DecisionEnvelope:
    decision_version: str
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
    stale_instruments: Tuple[str, ...]
    unvalidated_stale_instruments: Tuple[str, ...]
    authority_status: str            # AUTHORISED | NOT_AUTHORISED | ESCALATE
    transport_authority: bool
    emergency_bypass: bool
    action: str
    reason_codes: Tuple[str, ...]
    limiter_state: dict
    evidence_summary: dict
    config_version: Optional[str]

    def to_dict(self) -> dict:
        d = {
            "decision_version": self.decision_version,
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
            "stale_instruments": list(self.stale_instruments),
            "unvalidated_stale_instruments": list(self.unvalidated_stale_instruments),
            "authority_status": self.authority_status,
            "transport_authority": self.transport_authority,
            "emergency_bypass": self.emergency_bypass,
            "action": self.action,
            "reason_codes": list(self.reason_codes),
            "limiter_state": self.limiter_state,
            "evidence_summary": self.evidence_summary,
            "config_version": self.config_version,
        }
        return d


def _require_utc(ts: datetime.datetime) -> bool:
    return isinstance(ts, datetime.datetime) and ts.tzinfo is not None and ts.utcoffset() == datetime.timedelta(0)


def _decision_id(evaluated_at_utc: str, transport_state: str, action: str, reasons: Sequence[str]) -> str:
    payload = json.dumps([evaluated_at_utc, transport_state, action, sorted(reasons)], sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# =========================================================================== transport-state resolution (pure §3)
def resolve_transport_state(sig: TransportSignals) -> Tuple[str, str]:
    """Resolve the transport-authority state PURELY from transport signals (never from instrument freshness).
    Returns (state, heartbeat_state). Fails closed on missing/contradictory transport truth."""
    hb = sig.heartbeat_age_s
    if hb is None:
        hb_state = "UNKNOWN"
    elif hb <= sig.heartbeat_soft_horizon_s:
        hb_state = "FRESH"
    elif hb <= sig.heartbeat_hard_horizon_s:
        hb_state = "SOFT_STALE"
    else:
        hb_state = "HARD_STALE"

    # 1. reconnect already running -> do not stack another action
    if sig.reconnect_in_progress:
        return RECONNECTING, hb_state
    # 2. authentication/session failure -> authority (bounded re-auth reconnect), highest fault priority
    if sig.auth_failure:
        return AUTH_FAILED, hb_state
    # 3. explicit socket-down / provider-initiated disconnect -> authority
    if sig.socket_state in (SOCKET_DISCONNECTED, SOCKET_FAILED) or sig.provider_disconnect_event:
        return DISCONNECTED, hb_state
    if sig.socket_state in (SOCKET_CONNECTING, SOCKET_RECONNECTING):
        return RECONNECTING, hb_state
    # 4. genuine shared-stream fault: no line at all (incl. heartbeat) or fatal parser failure
    if sig.shared_stream_silent or sig.parser_fatal:
        return FAULT_CONFIRMED, hb_state
    # 5. socket CONNECTED (or unknown) -> lean on heartbeat truth
    if sig.socket_state == SOCKET_UNKNOWN:
        # transport truth missing entirely -> fail closed (do not assert healthy)
        return SILENT_UNCONFIRMED, hb_state
    # socket == CONNECTED
    if hb_state == "FRESH":
        return TRANSPORT_HEALTHY, hb_state
    if hb_state == "SOFT_STALE":
        return TRANSPORT_DEGRADED, hb_state
    # HARD_STALE or UNKNOWN heartbeat while socket claims connected -> conflict -> fail closed
    return SILENT_UNCONFIRMED, hb_state


# =========================================================================== the decision (pure §5/§8/§13/§19)
def decide(
    *,
    evaluated_at_utc: datetime.datetime,
    provider: str,
    transport: TransportSignals,
    instruments: Sequence[InstrumentState],
    limiter: LimiterState,
    config_version: Optional[str] = None,
) -> DecisionEnvelope:
    """Return the immutable recovery-authority decision. PURE: executes nothing.

    Contract (Option C):
      * Reconnect authority requires a TRANSPORT-authority condition. Instrument freshness ALONE never mutates
        the shared transport while transport is proven alive (heartbeat fresh / an expected-open validated
        instrument still progressing).
      * Genuine provider/socket/auth faults reconnect promptly and BYPASS the application limiter (emergency).
      * Application-derived authority (heartbeat/shared-progress/parser) is subject to the limiter.
      * Fail CLOSED to safety when transport truth is missing/contradictory: escalate, do NOT reconnect on garbage.
    """
    # ---- clock guard: fail closed to operator (never reconnect on a bad clock) ----
    if not _require_utc(evaluated_at_utc):
        return _envelope(
            evaluated_at_utc="INVALID_CLOCK", provider=provider, transport=transport,
            transport_state=SILENT_UNCONFIRMED, hb_state="UNKNOWN",
            expected_flow=(), stale=(), unval_stale=(),
            authority=False, emergency=False, action=OPERATOR_ESCALATION,
            reasons=(NO_TRANSPORT_TRUTH_FAILCLOSED, TRANSPORT_SIGNAL_CONFLICT),
            limiter=limiter, config_version=config_version,
            evidence={"fail_closed": True, "cause": "evaluated_at_utc not tz-aware UTC"},
        )

    ts_iso = evaluated_at_utc.astimezone(UTC).isoformat()
    transport_state, hb_state = resolve_transport_state(transport)

    # ---- instrument partitioning (validated vs unvalidated, expected-flow vs closed) ----
    expected_flow = tuple(i.instrument for i in instruments if i.expected_flow)
    expected_flow_validated = [i for i in instruments if i.expected_flow and i.validated]
    stale = tuple(i.instrument for i in instruments if i.stale and not i.governed_closed)
    unval_stale = tuple(i.instrument for i in instruments if i.stale and not i.validated and not i.governed_closed)
    val_expected_stale = [i for i in expected_flow_validated if i.stale]
    all_governed_closed = len(instruments) > 0 and all(i.governed_closed for i in instruments)

    shared_progress_state = hb_state  # heartbeat IS the shared-progress proxy (bumped by heartbeat or any price)
    shared_progress_age = transport.heartbeat_age_s

    reasons: List[str] = []
    emergency = False

    # ======== branch 1: reconnect already in progress -> take no new action ========
    if transport_state == RECONNECTING:
        return _envelope(
            evaluated_at_utc=ts_iso, provider=provider, transport=transport,
            transport_state=transport_state, hb_state=hb_state,
            expected_flow=expected_flow, stale=stale, unval_stale=unval_stale,
            authority=False, emergency=False, action=NO_ACTION,
            reasons=(RECONNECT_IN_PROGRESS,), limiter=limiter, config_version=config_version,
            evidence={"note": "reconnect already running; do not stack"},
        )

    # ======== branch 2: genuine transport fault -> reconnect authority (emergency bypasses limiter) ========
    if transport_state == DISCONNECTED:
        emergency = True
        reasons.append(PROVIDER_DISCONNECT_EVENT if transport.provider_disconnect_event else SOCKET_DISCONNECTED_R)
        return _authorised(ts_iso, provider, transport, transport_state, hb_state, expected_flow, stale,
                           unval_stale, reasons, emergency, limiter, config_version, shared_progress_state,
                           shared_progress_age)

    if transport_state == AUTH_FAILED:
        emergency = True
        reasons.append(AUTHENTICATION_FAILURE)
        return _authorised(ts_iso, provider, transport, transport_state, hb_state, expected_flow, stale,
                           unval_stale, reasons, emergency, limiter, config_version, shared_progress_state,
                           shared_progress_age)

    if transport_state == FAULT_CONFIRMED:
        emergency = True  # a real shared-stream fault must reconnect even if the freshness limiter is exhausted
        reasons.append(PARSER_EXCEPTION_FATAL if transport.parser_fatal else SHARED_STREAM_PROGRESS_STALE)
        return _authorised(ts_iso, provider, transport, transport_state, hb_state, expected_flow, stale,
                           unval_stale, reasons, emergency, limiter, config_version, shared_progress_state,
                           shared_progress_age)

    # ======== branch 3: SILENT_UNCONFIRMED -> authority ONLY if corroborated by a validated shared-progress loss ==
    if transport_state == SILENT_UNCONFIRMED:
        reasons.append(TRANSPORT_SIGNAL_CONFLICT)
        # Corroboration requires VALIDATED expected-flow instruments that are ALL stale (a real shared loss).
        # Unvalidated instruments NEVER corroborate (doctrine). A validated instrument still PROGRESSING proves
        # transport is alive despite the heartbeat gap -> no authority.
        if expected_flow_validated and all(i.stale for i in expected_flow_validated):
            reasons.append(HEARTBEAT_STALE)
            reasons.append(ALL_EXPECTED_FLOW_INSTRUMENTS_STALE)
            reasons.append(SHARED_STREAM_PROGRESS_STALE)
            # application-derived authority -> subject to the limiter (NOT emergency)
            return _authorised(ts_iso, provider, transport, transport_state, hb_state, expected_flow, stale,
                               unval_stale, reasons, emergency=False, limiter=limiter, config_version=config_version,
                               shared_progress_state=shared_progress_state, shared_progress_age=shared_progress_age)
        # No validated corroboration:
        if all_governed_closed or not expected_flow_validated:
            # nothing validated is expected to flow -> cannot confirm a shared loss; do not blindly reconnect.
            # Heartbeat gap during an all-closed / only-unvalidated window is not proof. Escalate, fail closed.
            code = ALL_GOVERNED_INSTRUMENTS_CLOSED if all_governed_closed else UNVALIDATED_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY
            reasons.append(code)
            reasons.append(RECONNECT_NOT_AUTHORISED)
            action = OPERATOR_ESCALATION if not all_governed_closed and unval_stale else NO_ACTION
            if all_governed_closed:
                action = NO_ACTION
            return _envelope(
                evaluated_at_utc=ts_iso, provider=provider, transport=transport,
                transport_state=transport_state, hb_state=hb_state,
                expected_flow=expected_flow, stale=stale, unval_stale=unval_stale,
                authority=False, emergency=False, action=action, reasons=tuple(reasons),
                limiter=limiter, config_version=config_version,
                evidence={"fail_closed": True, "note": "heartbeat gap w/o validated corroboration"},
                shared_progress_state=shared_progress_state, shared_progress_age=shared_progress_age,
            )
        # some validated expected-flow instruments still progressing -> transport alive despite heartbeat gap
        reasons.append(RECONNECT_NOT_AUTHORISED)
        return _proposal_or_noaction(ts_iso, provider, transport, transport_state, hb_state, expected_flow,
                                     stale, unval_stale, reasons, limiter, config_version,
                                     shared_progress_state, shared_progress_age)

    # ======== branch 4: transport proven alive (TRANSPORT_HEALTHY / DEGRADED) -> NO reconnect authority ========
    # Instrument staleness here is advisory: incidents + proposals only, NEVER a shared-transport mutation.
    if all_governed_closed:
        reasons.append(ALL_GOVERNED_INSTRUMENTS_CLOSED)
        reasons.append(RECONNECT_NOT_AUTHORISED)
        return _envelope(
            evaluated_at_utc=ts_iso, provider=provider, transport=transport,
            transport_state=transport_state, hb_state=hb_state,
            expected_flow=expected_flow, stale=stale, unval_stale=unval_stale,
            authority=False, emergency=False, action=NO_ACTION, reasons=tuple(reasons),
            limiter=limiter, config_version=config_version,
            evidence={"note": "all governed instruments closed; transport healthy"},
            shared_progress_state=shared_progress_state, shared_progress_age=shared_progress_age,
        )

    reasons.append(RECONNECT_NOT_AUTHORISED)
    return _proposal_or_noaction(ts_iso, provider, transport, transport_state, hb_state, expected_flow,
                                 stale, unval_stale, reasons, limiter, config_version,
                                 shared_progress_state, shared_progress_age)


# --------------------------------------------------------------------------- helpers
def _proposal_or_noaction(ts_iso, provider, transport, transport_state, hb_state, expected_flow, stale,
                          unval_stale, reasons, limiter, config_version, shared_progress_state,
                          shared_progress_age) -> DecisionEnvelope:
    """Transport is alive. If instruments are stale -> advisory proposal + incident. Else -> no action."""
    reasons = list(reasons)
    if stale:
        if unval_stale:
            reasons.append(UNVALIDATED_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY)
        # a stale VALIDATED open instrument that is NOT the whole set is a partial loss (per-instrument, no transport)
        validated_stale = [s for s in stale if s not in unval_stale]
        if validated_stale:
            reasons.append(PARTIAL_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY)
        reasons.append(RECOVERY_PROPOSAL_ONLY_R)
        action = RECOVERY_PROPOSAL_ONLY
        evidence = {"note": "instrument stale but transport proven alive; proposal only (no transport mutation)"}
    else:
        action = NO_ACTION
        evidence = {"note": "transport alive; no stale instruments"}
    return _envelope(
        evaluated_at_utc=ts_iso, provider=provider, transport=transport,
        transport_state=transport_state, hb_state=hb_state,
        expected_flow=expected_flow, stale=stale, unval_stale=unval_stale,
        authority=False, emergency=False, action=action, reasons=tuple(reasons),
        limiter=limiter, config_version=config_version, evidence=evidence,
        shared_progress_state=shared_progress_state, shared_progress_age=shared_progress_age,
    )


def _authorised(ts_iso, provider, transport, transport_state, hb_state, expected_flow, stale, unval_stale,
                reasons, emergency, limiter, config_version, shared_progress_state,
                shared_progress_age) -> DecisionEnvelope:
    """Transport authority is present. Emergency faults bypass the limiter; application-derived authority obeys it."""
    reasons = list(reasons)
    if emergency:
        if limiter.exhausted:
            reasons.append(EMERGENCY_BYPASS_LIMITER)
        reasons.append(RECONNECT_AUTHORISED_R)
        action = RECONNECT_AUTHORISED
        authority = True
    else:
        if limiter.exhausted:
            reasons.append(RECONNECT_RATE_LIMITED_R)
            action = RECONNECT_RATE_LIMITED
            authority = True  # authority exists, but the action is throttled + escalated
        else:
            reasons.append(RECONNECT_AUTHORISED_R)
            action = RECONNECT_AUTHORISED
            authority = True
    return _envelope(
        evaluated_at_utc=ts_iso, provider=provider, transport=transport,
        transport_state=transport_state, hb_state=hb_state,
        expected_flow=expected_flow, stale=stale, unval_stale=unval_stale,
        authority=authority, emergency=emergency, action=action, reasons=tuple(reasons),
        limiter=limiter, config_version=config_version,
        evidence={"emergency_bypass": emergency, "limiter_exhausted": limiter.exhausted},
        shared_progress_state=shared_progress_state, shared_progress_age=shared_progress_age,
    )


def _envelope(*, evaluated_at_utc, provider, transport, transport_state, hb_state, expected_flow, stale,
              unval_stale, authority, emergency, action, reasons, limiter, config_version, evidence,
              shared_progress_state="UNKNOWN", shared_progress_age=None) -> DecisionEnvelope:
    reasons = tuple(dict.fromkeys(reasons))  # dedupe, preserve order
    if action == RECONNECT_AUTHORISED:
        authority_status = "AUTHORISED"
    elif action == OPERATOR_ESCALATION:
        authority_status = "ESCALATE"
    else:
        authority_status = "NOT_AUTHORISED"
    limiter_state = {
        "attempts_in_window": limiter.attempts_in_window,
        "max_per_window": limiter.max_per_window,
        "window_seconds": limiter.window_seconds,
        "available": limiter.available,
        "exhausted": limiter.exhausted,
    }
    return DecisionEnvelope(
        decision_version=DECISION_VERSION,
        decision_id=_decision_id(evaluated_at_utc, transport_state, action, reasons),
        evaluated_at_utc=evaluated_at_utc,
        provider=provider,
        transport_state=transport_state,
        socket_connected=(transport.socket_state == SOCKET_CONNECTED),
        heartbeat_state=hb_state,
        heartbeat_age_s=transport.heartbeat_age_s,
        shared_progress_state=shared_progress_state,
        shared_progress_age_s=shared_progress_age,
        expected_flow_instruments=tuple(expected_flow),
        stale_instruments=tuple(stale),
        unvalidated_stale_instruments=tuple(unval_stale),
        authority_status=authority_status,
        transport_authority=authority,
        emergency_bypass=emergency,
        action=action,
        reason_codes=reasons,
        limiter_state=limiter_state,
        evidence_summary=evidence,
        config_version=config_version,
    )
