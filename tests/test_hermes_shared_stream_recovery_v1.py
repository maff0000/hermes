"""Fixture-driven proof of the PRODUCTION-OWNED shared-stream recovery-authority pure decision core (INERT, Phase 1).

WO-HELM-HERMES-SHARED-STREAM-RECOVERY-PHASE1-PURE-DECISION-CORE-IMPLEMENTATION-0001.

Covers: contract taxonomy (Task 2), input/envelope models (Task 3/6), decision precedence (Task 4), emergency-bypass
boundary (Task 5), the July-16 regression (Task 7), 12 genuine-fault fixtures (Task 8), instrument-doctrine fixtures
(Task 9), determinism/validation (Task 10), prototype-parity (Task 11), inertness static guard (Task 12), and the
JSON-schema/back-compat integration (Task 6). The audited design tests
(tests/test_shared_stream_recovery_contract_v1.py) remain unchanged and must keep passing alongside these.

Run: python3 -m pytest tests/test_hermes_shared_stream_recovery_v1.py -q
"""
import ast
import datetime
import inspect
import json
import pathlib

import pytest

import utils.hermes_shared_stream_recovery_v1 as core
from utils.hermes_shared_stream_recovery_v1 import (
    Action, AuthorityStatus, DecisionEnvelope, HeartbeatState, InstrumentObservation, LimiterState,
    RecoveryContractError, RecoveryDecisionInput, ReasonCode, SocketState, TransportState,
    ALL_ACTIONS, ALL_AUTHORITY_STATES, ALL_REASON_CODES, ALL_TRANSPORT_STATES,
    decide, envelope_from_dict, resolve_transport_state,
)

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 7, 16, 21, 15, 0, tzinfo=UTC)  # inside the July-16 metals-break window (21:00-22:00Z)
PROVIDER = "OANDA"
REPO = pathlib.Path(__file__).resolve().parent.parent


# ---- instrument builders reflecting the July-16 board ----
def xau_closed():
    return InstrumentObservation("XAU_USD", expected_flow=False, stale=False, validated=True, governed_closed=True)

def spx_stale():   # SPX500_USD: unvalidated (fail-loud), expected-open, stale
    return InstrumentObservation("SPX500_USD", expected_flow=True, stale=True, validated=False)

def wtico_stale():
    return InstrumentObservation("WTICO_USD", expected_flow=True, stale=True, validated=False)

def eur_flowing():  # a validated FX pair still delivering ticks -> transport proven alive
    return InstrumentObservation("EUR_USD", expected_flow=True, stale=False, validated=True)

def eur_stale():
    return InstrumentObservation("EUR_USD", expected_flow=True, stale=True, validated=True)


def _inp(instruments, *, socket_state=SocketState.CONNECTED, heartbeat_age_s=5.0, heartbeat_available=True,
         provider_disconnect_event=False, auth_failure=False, shared_stream_silent=False, parser_fatal=False,
         reconnect_in_progress=False, limiter=None, now=NOW, cfg="3", contract_version=core.CONTRACT_VERSION):
    return RecoveryDecisionInput(
        evaluated_at_utc=now, provider=PROVIDER, contract_version=contract_version, config_version=cfg,
        socket_state=socket_state, heartbeat_available=heartbeat_available, heartbeat_age_s=heartbeat_age_s,
        provider_disconnect_event=provider_disconnect_event, auth_failure=auth_failure,
        shared_stream_silent=shared_stream_silent, parser_fatal=parser_fatal,
        reconnect_in_progress=reconnect_in_progress, instruments=tuple(instruments),
        limiter=limiter or LimiterState())


# =========================================================================== Task 2 — versioned taxonomies
def test_taxonomy_actions():
    assert ALL_ACTIONS == {"NO_ACTION", "INCIDENT_ONLY", "RECOVERY_PROPOSAL_ONLY", "RECONNECT_AUTHORISED",
                           "RECONNECT_RATE_LIMITED", "OPERATOR_ESCALATION"}

def test_taxonomy_authority_states():
    assert ALL_AUTHORITY_STATES == {"authorised", "not_authorised", "fail_closed", "rate_limited"}

def test_taxonomy_transport_states_nine():
    assert ALL_TRANSPORT_STATES == {"TRANSPORT_HEALTHY", "TRANSPORT_DEGRADED", "SILENT_UNCONFIRMED",
                                    "FAULT_CONFIRMED", "AUTH_FAILED", "DISCONNECTED", "RECONNECTING",
                                    "RATE_LIMITED", "RECOVERED"}
    assert len(ALL_TRANSPORT_STATES) == 9

def test_taxonomy_eighteen_reason_codes():
    assert len(ALL_REASON_CODES) == 18

def test_enum_values_are_stable_strings_not_ordinals():
    for member in list(Action) + list(ReasonCode) + list(TransportState) + list(AuthorityStatus):
        assert isinstance(member.value, str) and member.value  # string values, never ordinals

def test_recovered_is_adapter_supplied_not_core_inferred():
    """RECOVERED must never be produced by the core (it is an adapter-emitted outcome)."""
    for insts in ([xau_closed(), spx_stale(), eur_flowing()], [eur_stale()], [xau_closed()]):
        assert decide(_inp(insts)).transport_state != TransportState.RECOVERED.value
    for ss in (SocketState.CONNECTED, SocketState.DISCONNECTED, SocketState.UNKNOWN, SocketState.FAILED):
        assert decide(_inp([eur_flowing()], socket_state=ss)).transport_state != "RECOVERED"


# =========================================================================== Task 3 — input model primitives
def test_input_model_carries_all_listed_primitives_and_collections():
    inp = _inp([xau_closed(), spx_stale(), wtico_stale(), eur_flowing(),
                InstrumentObservation("GBP_USD", expected_flow=False, stale=False, validated=True, reopening_grace=True)])
    assert set(inp.expected_flow_instruments) == {"SPX500_USD", "WTICO_USD", "EUR_USD"}
    assert set(inp.stale_governed_instruments) == {"SPX500_USD", "WTICO_USD"}
    assert set(inp.stale_unvalidated_instruments) == {"SPX500_USD", "WTICO_USD"}
    assert set(inp.expected_closed_instruments) == {"XAU_USD"}
    assert set(inp.reopening_grace_instruments) == {"GBP_USD"}
    # canonical ordering: instruments sorted by name regardless of input order
    assert [i.instrument for i in inp.instruments] == sorted(i.instrument for i in inp.instruments)

def test_input_field_count_reported():
    assert len(RecoveryDecisionInput.__dataclass_fields__) == 21


# =========================================================================== Task 7 — the July-16 regression fixture
def test_july16_spx_wtico_stale_transport_healthy_PROPOSAL_ONLY():
    """XAU expected-closed; SPX500/WTICO stale+unvalidated; socket connected; no provider-disconnect; auth healthy;
    heartbeat healthy; validated FX expected-flow progressing; parser healthy; not reconnecting; limiter available;
    NO genuine fault -> RECOVERY_PROPOSAL_ONLY / reconnect=False / bypass=False. No false GREEN, no schedule guess."""
    d = decide(_inp([xau_closed(), spx_stale(), wtico_stale(), eur_flowing()]))
    assert d.action == Action.RECOVERY_PROPOSAL_ONLY.value
    assert d.reconnect_authorised is False
    assert d.emergency_bypass_eligible is False
    assert d.authority_status == AuthorityStatus.NOT_AUTHORISED.value
    assert ReasonCode.UNVALIDATED_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY.value in d.reason_codes
    assert ReasonCode.RECONNECT_NOT_AUTHORISED.value in d.reason_codes
    assert "XAU_USD" not in d.stale_governed_instruments           # governed-closed -> suppressed, no incident
    assert set(d.stale_unvalidated_instruments) == {"SPX500_USD", "WTICO_USD"}
    assert d.transport_state == TransportState.HEALTHY.value


# =========================================================================== Task 8 — 12 genuine-fault fixtures
def test_fault_01_socket_disconnected_AUTHORISED():
    d = decide(_inp([xau_closed(), spx_stale(), eur_flowing()], socket_state=SocketState.DISCONNECTED))
    assert d.action == Action.RECONNECT_AUTHORISED.value and d.emergency_bypass_eligible is True
    assert ReasonCode.SOCKET_DISCONNECTED.value in d.reason_codes

def test_fault_02_provider_disconnect_AUTHORISED():
    d = decide(_inp([eur_flowing()], provider_disconnect_event=True))
    assert d.action == Action.RECONNECT_AUTHORISED.value and d.emergency_bypass_eligible is True
    assert ReasonCode.PROVIDER_DISCONNECT_EVENT.value in d.reason_codes

def test_fault_03_auth_failure_AUTHORISED():
    d = decide(_inp([eur_flowing()], auth_failure=True))
    assert d.action == Action.RECONNECT_AUTHORISED.value and d.transport_state == TransportState.AUTH_FAILED.value
    assert ReasonCode.AUTHENTICATION_FAILURE.value in d.reason_codes

def test_fault_04_parser_fatal_AUTHORISED():
    d = decide(_inp([eur_flowing()], parser_fatal=True))
    assert d.action == Action.RECONNECT_AUTHORISED.value and d.transport_state == TransportState.FAULT_CONFIRMED.value
    assert ReasonCode.PARSER_EXCEPTION_FATAL.value in d.reason_codes

def test_fault_05_heartbeat_stale_corroborated_AUTHORISED():
    d = decide(_inp([xau_closed(), spx_stale(), eur_stale()], heartbeat_age_s=120.0))
    assert d.action == Action.RECONNECT_AUTHORISED.value and d.transport_state == TransportState.SILENT_UNCONFIRMED.value
    assert ReasonCode.HEARTBEAT_STALE.value in d.reason_codes
    assert ReasonCode.ALL_EXPECTED_FLOW_INSTRUMENTS_STALE.value in d.reason_codes
    assert ReasonCode.SHARED_STREAM_PROGRESS_STALE.value in d.reason_codes

def test_fault_06_shared_stream_silent_corroborated_AUTHORISED():
    d = decide(_inp([xau_closed(), spx_stale(), eur_stale()], shared_stream_silent=True, heartbeat_age_s=200.0))
    assert d.action == Action.RECONNECT_AUTHORISED.value and d.transport_state == TransportState.FAULT_CONFIRMED.value
    assert d.emergency_bypass_eligible is True
    assert ReasonCode.SHARED_STREAM_PROGRESS_STALE.value in d.reason_codes

def test_fault_07_limiter_exhausted_provider_disconnect_BYPASS():
    lim = LimiterState(attempts_in_window=3, max_per_window=3)
    d = decide(_inp([eur_flowing()], provider_disconnect_event=True, limiter=lim))
    assert d.action == Action.RECONNECT_AUTHORISED.value and d.emergency_bypass_eligible is True
    assert ReasonCode.EMERGENCY_BYPASS_LIMITER.value in d.reason_codes

def test_fault_08_limiter_exhausted_ordinary_freshness_NOT_BYPASSED_RATE_LIMITED():
    """Application-derived authority (heartbeat-stale-all-flow, i.e. freshness-derived) + limiter exhausted ->
    RECONNECT_RATE_LIMITED, emergency bypass NOT invoked. Ordinary freshness never bypasses."""
    lim = LimiterState(attempts_in_window=3, max_per_window=3)
    d = decide(_inp([xau_closed(), eur_stale()], heartbeat_age_s=120.0, limiter=lim))
    assert d.action == Action.RECONNECT_RATE_LIMITED.value
    assert d.authority_status == AuthorityStatus.RATE_LIMITED.value
    assert d.emergency_bypass_eligible is False
    assert ReasonCode.EMERGENCY_BYPASS_LIMITER.value not in d.reason_codes
    assert ReasonCode.RECONNECT_RATE_LIMITED.value in d.reason_codes

def test_fault_09_reconnect_in_progress_no_duplicate_authority():
    d = decide(_inp([xau_closed(), spx_stale(), eur_stale()], reconnect_in_progress=True, heartbeat_age_s=200.0,
                    socket_state=SocketState.DISCONNECTED))
    assert d.action == Action.NO_ACTION.value and d.reconnect_authorised is False
    assert ReasonCode.RECONNECT_IN_PROGRESS.value in d.reason_codes

def test_fault_10_transport_signal_conflict_failclosed_visible_NOT_AUTHORISED():
    """Heartbeat hard-stale but a validated FX pair still progressing -> transport alive despite gap; conflict visible."""
    d = decide(_inp([xau_closed(), spx_stale(), eur_flowing()], heartbeat_age_s=120.0))
    assert d.reconnect_authorised is False
    assert ReasonCode.TRANSPORT_SIGNAL_CONFLICT.value in d.reason_codes
    assert d.transport_state == TransportState.SILENT_UNCONFIRMED.value

def test_fault_11_transport_truth_missing_failclosed_ESCALATE():
    """Socket UNKNOWN + heartbeat unavailable + only unvalidated stale, nothing validated to corroborate -> fail closed."""
    d = decide(_inp([spx_stale(), wtico_stale()], socket_state=SocketState.UNKNOWN,
                    heartbeat_available=False, heartbeat_age_s=None))
    assert d.reconnect_authorised is False
    assert d.action == Action.OPERATOR_ESCALATION.value
    assert d.authority_status == AuthorityStatus.FAIL_CLOSED.value
    assert d.transport_state == TransportState.SILENT_UNCONFIRMED.value

def test_fault_12_all_governed_expected_flow_closed_NOT_AUTHORISED():
    insts = [xau_closed(),
             InstrumentObservation("EUR_USD", expected_flow=False, stale=False, validated=True, governed_closed=True)]
    d = decide(_inp(insts))
    assert d.action == Action.NO_ACTION.value and d.reconnect_authorised is False
    assert ReasonCode.ALL_GOVERNED_INSTRUMENTS_CLOSED.value in d.reason_codes


def test_genuine_disconnect_still_reconnects_when_all_closed():
    insts = [xau_closed(),
             InstrumentObservation("EUR_USD", expected_flow=False, stale=False, validated=True, governed_closed=True)]
    d = decide(_inp(insts, socket_state=SocketState.DISCONNECTED))
    assert d.action == Action.RECONNECT_AUTHORISED.value
    assert ReasonCode.SOCKET_DISCONNECTED.value in d.reason_codes


# =========================================================================== Task 5 — emergency-bypass boundary
def test_bypass_boundary_only_genuine_faults_and_not_caller_settable():
    # 1) no 'emergency' caller flag exists on the input model at all
    assert "emergency" not in RecoveryDecisionInput.__dataclass_fields__
    assert not any("emergency" in f for f in RecoveryDecisionInput.__dataclass_fields__)
    # 2) genuine fault -> bypass eligible + adapter bounding required + reason surfaced
    d = decide(_inp([eur_flowing()], provider_disconnect_event=True))
    assert d.emergency_bypass_eligible is True and d.adapter_bounding_required is True
    assert d.emergency_bypass_reason == ReasonCode.PROVIDER_DISCONNECT_EVENT.value
    # 3) ordinary freshness (unvalidated stale, transport healthy) -> never bypass
    d2 = decide(_inp([spx_stale(), wtico_stale()]))
    assert d2.emergency_bypass_eligible is False and d2.emergency_bypass_reason is None
    assert d2.adapter_bounding_required is False
    # 4) application-derived (corroborated) authority is authority but NOT an emergency bypass
    d3 = decide(_inp([xau_closed(), eur_stale()], heartbeat_age_s=120.0))
    assert d3.action == Action.RECONNECT_AUTHORISED.value and d3.emergency_bypass_eligible is False


# =========================================================================== Task 9 — instrument-doctrine fixtures
def test_doctrine_one_unvalidated_stale_PROPOSAL_no_transport_vote():
    d = decide(_inp([spx_stale()]))
    assert d.action == Action.RECOVERY_PROPOSAL_ONLY.value and d.reconnect_authorised is False
    assert ReasonCode.UNVALIDATED_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY.value in d.reason_codes

def test_doctrine_two_unvalidated_stale_PROPOSAL_no_partial_code():
    d = decide(_inp([spx_stale(), wtico_stale()]))
    assert d.action == Action.RECOVERY_PROPOSAL_ONLY.value
    assert ReasonCode.PARTIAL_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY.value not in d.reason_codes

def test_doctrine_single_governed_open_stale_while_peer_flows_PARTIAL():
    d = decide(_inp([eur_stale(), InstrumentObservation("GBP_USD", expected_flow=True, stale=False, validated=True)]))
    assert d.action == Action.RECOVERY_PROPOSAL_ONLY.value
    assert ReasonCode.PARTIAL_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY.value in d.reason_codes

def test_doctrine_multiple_partial_stale_visible_no_transport_mutation():
    d = decide(_inp([eur_stale(), spx_stale(),
                     InstrumentObservation("GBP_USD", expected_flow=True, stale=False, validated=True)]))
    assert d.reconnect_authorised is False
    assert set(d.stale_governed_instruments) == {"EUR_USD", "SPX500_USD"}
    assert ReasonCode.PARTIAL_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY.value in d.reason_codes
    assert ReasonCode.UNVALIDATED_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY.value in d.reason_codes

def test_doctrine_expected_closed_absent_suppressed():
    d = decide(_inp([xau_closed(), eur_flowing()]))
    assert "XAU_USD" not in d.stale_governed_instruments
    assert d.action in (Action.NO_ACTION.value, Action.RECOVERY_PROPOSAL_ONLY.value)

def test_doctrine_reopening_grace_absence_tolerated_NO_ACTION():
    d = decide(_inp([InstrumentObservation("XAU_USD", expected_flow=False, stale=False, validated=True, reopening_grace=True)]))
    assert d.action == Action.NO_ACTION.value and d.reconnect_authorised is False

def test_doctrine_unknown_instrument_unvalidated_no_vote():
    """An unknown/unmapped instrument is unvalidated (fail-loud) -> visible, no transport vote alone."""
    d = decide(_inp([InstrumentObservation("MYSTERY_X", expected_flow=True, stale=True, validated=False), eur_flowing()]))
    assert d.reconnect_authorised is False
    assert "MYSTERY_X" in d.stale_unvalidated_instruments

def test_doctrine_no_expected_flow_instruments_healthy_NO_ACTION():
    d = decide(_inp([InstrumentObservation("XAU_USD", expected_flow=False, stale=False, validated=True, reopening_grace=True)]))
    assert d.action == Action.NO_ACTION.value

def test_doctrine_weekend_all_governed_closed_NO_ACTION():
    insts = [xau_closed(),
             InstrumentObservation("SPX500_USD", expected_flow=False, stale=False, validated=False, governed_closed=True),
             InstrumentObservation("EUR_USD", expected_flow=False, stale=False, validated=True, governed_closed=True)]
    d = decide(_inp(insts))
    assert d.action == Action.NO_ACTION.value
    assert ReasonCode.ALL_GOVERNED_INSTRUMENTS_CLOSED.value in d.reason_codes

def test_doctrine_instrument_health_visible_without_mutating_transport():
    """Per-instrument staleness stays visible (fail-loud) while transport authority stays False."""
    d = decide(_inp([xau_closed(), spx_stale(), wtico_stale(), eur_flowing()]))
    assert d.stale_unvalidated_instruments and d.reconnect_authorised is False


# =========================================================================== Task 10 — determinism & validation
def test_determinism_identical_input_identical_output():
    a = decide(_inp([xau_closed(), spx_stale(), eur_flowing()]))
    b = decide(_inp([xau_closed(), spx_stale(), eur_flowing()]))
    assert a.decision_id == b.decision_id and a.to_json() == b.to_json()

def test_input_collection_order_does_not_change_decision():
    a = decide(_inp([xau_closed(), spx_stale(), wtico_stale(), eur_flowing()]))
    b = decide(_inp([eur_flowing(), wtico_stale(), spx_stale(), xau_closed()]))
    assert a.to_json() == b.to_json()

def test_reason_code_order_deterministic():
    d = decide(_inp([xau_closed(), spx_stale(), eur_stale()], heartbeat_age_s=120.0))
    assert list(d.reason_codes) == list(dict.fromkeys(d.reason_codes))  # no dupes, stable

def test_supplied_correlation_id_used_as_decision_id():
    d = decide(_inp([eur_flowing()]), correlation_id="abc123corr")
    assert d.decision_id == "abc123corr"

def test_naive_timestamp_rejected_at_construction():
    with pytest.raises(RecoveryContractError):
        _inp([spx_stale()], now=datetime.datetime(2026, 7, 16, 21, 15, 0))  # naive

def test_non_utc_timestamp_rejected_at_construction():
    tz = datetime.timezone(datetime.timedelta(hours=2))
    with pytest.raises(RecoveryContractError):
        _inp([spx_stale()], now=datetime.datetime(2026, 7, 16, 23, 15, 0, tzinfo=tz))

def test_negative_ages_rejected():
    with pytest.raises(RecoveryContractError):
        _inp([spx_stale()], heartbeat_age_s=-1.0)

def test_malformed_limiter_rejected():
    with pytest.raises(RecoveryContractError):
        LimiterState(attempts_in_window=-1)
    with pytest.raises(RecoveryContractError):
        LimiterState(max_per_window=0)

def test_unsupported_contract_version_fails_safely():
    d = decide(_inp([eur_flowing()], contract_version="99"))
    assert d.action == Action.OPERATOR_ESCALATION.value
    assert d.authority_status == AuthorityStatus.FAIL_CLOSED.value
    assert ReasonCode.NO_TRANSPORT_TRUTH_FAILCLOSED.value in d.reason_codes

def test_unsupported_envelope_version_decode_fails_safely():
    d = decide(_inp([eur_flowing()]))
    payload = d.to_dict()
    payload["envelope_version"] = "99"
    with pytest.raises(RecoveryContractError):
        envelope_from_dict(payload)

def test_envelope_roundtrips_through_dict():
    d = decide(_inp([xau_closed(), spx_stale(), eur_flowing()]))
    assert envelope_from_dict(d.to_dict()).to_json() == d.to_json()

def test_input_not_mutated_and_output_frozen():
    inp = _inp([xau_closed(), spx_stale(), eur_flowing()])
    before = inp.canonical_json()
    d = decide(inp)
    assert inp.canonical_json() == before
    with pytest.raises(Exception):
        d.action = "x"  # frozen dataclass

def test_contradictory_evidence_conflict_visible():
    # socket claims connected but heartbeat missing while a validated instrument still flows -> conflict, no authority
    d = decide(_inp([eur_flowing(), spx_stale()], heartbeat_available=False, heartbeat_age_s=None))
    assert ReasonCode.TRANSPORT_SIGNAL_CONFLICT.value in d.reason_codes
    assert d.reconnect_authorised is False


# =========================================================================== Task 11 — prototype parity
import design.shared_stream_recovery_contract_v1 as proto  # noqa: E402

def _proto_decide(instruments_spec, *, socket=proto.SOCKET_CONNECTED, hb=5.0, prov_disc=False, auth=False,
                  silent=False, parser=False, reconnecting=False, limiter=None):
    t = proto.TransportSignals(socket_state=socket, heartbeat_age_s=hb, provider_disconnect_event=prov_disc,
                               auth_failure=auth, shared_stream_silent=silent, parser_fatal=parser,
                               reconnect_in_progress=reconnecting)
    insts = [proto.InstrumentState(n, expected_flow=ef, stale=st, validated=v, governed_closed=gc)
             for (n, ef, st, v, gc) in instruments_spec]
    return proto.decide(evaluated_at_utc=NOW, provider=PROVIDER, transport=t, instruments=insts,
                        limiter=limiter or proto.LimiterState(), config_version="3")

def _prod_decide(instruments_spec, **kw):
    smap = {proto.SOCKET_CONNECTED: SocketState.CONNECTED, proto.SOCKET_DISCONNECTED: SocketState.DISCONNECTED,
            proto.SOCKET_UNKNOWN: SocketState.UNKNOWN}
    socket = smap.get(kw.get("socket", proto.SOCKET_CONNECTED), SocketState.CONNECTED)
    hb = kw.get("hb", 5.0)
    lim = kw.get("limiter")
    insts = [InstrumentObservation(n, expected_flow=ef, stale=st, validated=v, governed_closed=gc)
             for (n, ef, st, v, gc) in instruments_spec]
    return decide(_inp(insts, socket_state=socket, heartbeat_age_s=hb, heartbeat_available=(hb is not None),
                       provider_disconnect_event=kw.get("prov_disc", False), auth_failure=kw.get("auth", False),
                       shared_stream_silent=kw.get("silent", False), parser_fatal=kw.get("parser", False),
                       reconnect_in_progress=kw.get("reconnecting", False),
                       limiter=LimiterState(attempts_in_window=lim.attempts_in_window, max_per_window=lim.max_per_window,
                                            window_seconds=lim.window_seconds, available=lim.available) if lim else None))

# (name, expected_flow, stale, validated, governed_closed)
_PARITY_SCENARIOS = [
    ("july16_proposal", [("XAU_USD", False, False, True, True), ("SPX500_USD", True, True, False, False),
                         ("WTICO_USD", True, True, False, False), ("EUR_USD", True, False, True, False)], {}),
    ("socket_disconnect", [("EUR_USD", True, False, True, False)], {"socket": proto.SOCKET_DISCONNECTED}),
    ("provider_disconnect", [("EUR_USD", True, False, True, False)], {"prov_disc": True}),
    ("auth_failure", [("EUR_USD", True, False, True, False)], {"auth": True}),
    ("parser_fatal", [("EUR_USD", True, False, True, False)], {"parser": True}),
    ("hb_stale_all_flow", [("XAU_USD", False, False, True, True), ("SPX500_USD", True, True, False, False),
                           ("EUR_USD", True, True, True, False)], {"hb": 120.0}),
    ("hb_stale_fx_flowing", [("XAU_USD", False, False, True, True), ("SPX500_USD", True, True, False, False),
                             ("EUR_USD", True, False, True, False)], {"hb": 120.0}),
    ("silent_stall", [("XAU_USD", False, False, True, True), ("EUR_USD", True, True, True, False)],
     {"silent": True, "hb": 200.0}),
    ("only_unvalidated", [("SPX500_USD", True, True, False, False), ("WTICO_USD", True, True, False, False)], {}),
    ("partial_validated", [("EUR_USD", True, True, True, False), ("GBP_USD", True, False, True, False)], {}),
    ("all_closed", [("XAU_USD", False, False, True, True), ("EUR_USD", False, False, True, True)], {}),
    ("limiter_rate_limited", [("XAU_USD", False, False, True, True), ("EUR_USD", True, True, True, False)],
     {"hb": 120.0, "limiter": proto.LimiterState(attempts_in_window=3, max_per_window=3)}),
    ("limiter_bypass", [("EUR_USD", True, False, True, False)],
     {"prov_disc": True, "limiter": proto.LimiterState(attempts_in_window=3, max_per_window=3)}),
    ("reconnecting", [("EUR_USD", True, True, True, False)], {"reconnecting": True}),
]

@pytest.mark.parametrize("name,spec,kw", _PARITY_SCENARIOS, ids=[s[0] for s in _PARITY_SCENARIOS])
def test_prototype_parity_action_reconnect_and_reasons(name, spec, kw):
    p = _proto_decide(spec, **kw)
    q = _prod_decide(spec, **kw)
    assert p.action == q.action, name                                  # same action
    assert p.transport_authority == q.reconnect_authorised or p.action == q.action, name
    assert (p.action == proto.RECONNECT_AUTHORISED) == q.reconnect_authorised, name
    assert set(p.reason_codes) == set(q.reason_codes), name            # same reason-code set
    assert p.transport_state == q.transport_state, name                # same transport state


# =========================================================================== Task 12 — inertness / purity static guards
# "design" is the INERT design location (the audited prototype + the Phase-2 shadow-adapter design modules live
# here). It is NOT a runtime/infra path — no runner, compose, or systemd unit imports it, proven independently by
# tests/test_sss_phase2_shadow_design_v1.py::test_static_guard_no_runtime_imports_phase2_design. The Phase-2 replay
# harness legitimately imports the pure core from design/, so design/ is an allowed inert referrer here too.
_ALLOWED_REFERRERS = {"tests", "schemas", "docs", "ops", "design"}

def test_static_guard_no_runtime_or_infra_imports_the_core():
    """FAIL if any runtime / infra file references the new module. Only tests/schemas/docs/ops may."""
    referrers = []
    for path in REPO.rglob("*.py"):
        rel = path.relative_to(REPO)
        if rel.parts[0] in _ALLOWED_REFERRERS or rel.name == "hermes_shared_stream_recovery_v1.py":
            continue
        if "hermes_shared_stream_recovery" in path.read_text(encoding="utf-8", errors="ignore"):
            referrers.append(str(rel))
    for infra in ("docker-compose.yml", "Dockerfile"):
        p = REPO / infra
        if p.exists() and "hermes_shared_stream_recovery" in p.read_text(encoding="utf-8", errors="ignore"):
            referrers.append(infra)
    for extra_dir in ("cron",):
        d = REPO / extra_dir
        if d.exists():
            for f in d.rglob("*"):
                if f.is_file() and "hermes_shared_stream_recovery" in f.read_text(encoding="utf-8", errors="ignore"):
                    referrers.append(str(f.relative_to(REPO)))
    for svc in REPO.glob("*.service"):
        if "hermes_shared_stream_recovery" in svc.read_text(encoding="utf-8", errors="ignore"):
            referrers.append(svc.name)
    assert referrers == [], f"INERTNESS VIOLATION: runtime/infra references the Phase-1 core: {referrers}"

def test_core_imports_stdlib_only():
    tree = ast.parse(inspect.getsource(core))
    mods = {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    mods |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    forbidden = {"redis", "pymysql", "sqlalchemy", "psycopg2", "requests", "httpx", "socket", "subprocess",
                 "os", "http", "urllib", "threading", "asyncio", "pathlib", "oandapyV20", "aiohttp"}
    assert not (mods & forbidden), f"pure core must not import {mods & forbidden}"

def test_core_has_no_wallclock_or_io_tokens():
    # strip string/docstring constants so prose ("no redis / sql") is not mistaken for a call
    tree = ast.parse(inspect.getsource(core))
    class Strip(ast.NodeTransformer):
        def visit_Constant(self, n):
            return ast.copy_location(ast.Constant(""), n) if isinstance(n.value, str) else n
    code = ast.unparse(Strip().visit(tree)).lower()
    for tok in (".now(", "utcnow(", "time.time(", "datetime.now", "os.environ", "getenv", "open(",
                "import redis", "redis.", "cursor(", "subprocess", "socket.socket", "requests.", "httpx."):
        assert tok not in code, f"forbidden wall-clock/IO token in pure core: {tok!r}"


# =========================================================================== Task 6 — JSON schema integration + back-compat
from jsonschema import Draft202012Validator  # noqa: E402

_SCHEMA = REPO / "schemas" / "shared_stream_recovery" / "decision_envelope.v1.schema.json"


def _validator():
    s = json.loads(_SCHEMA.read_text())
    Draft202012Validator.check_schema(s)
    return Draft202012Validator(s)


def test_production_envelope_validates_against_extended_schema():
    v = _validator()
    for insts, kw in [([xau_closed(), spx_stale(), wtico_stale(), eur_flowing()], {}),
                      ([eur_flowing()], {"provider_disconnect_event": True}),
                      ([eur_flowing()], {"auth_failure": True}),
                      ([xau_closed(), eur_stale()], {"heartbeat_age_s": 120.0})]:
        d = decide(_inp(insts, **kw))
        errs = sorted(v.iter_errors(d.to_dict()), key=lambda e: e.path)
        assert not errs, (d.action, [e.message for e in errs])


def test_prototype_envelope_still_validates_backcompat():
    """The extended v1 schema MUST remain back-compatible with the audited prototype envelope shape."""
    v = _validator()
    p = _proto_decide([("XAU_USD", False, False, True, True), ("SPX500_USD", True, True, False, False),
                       ("EUR_USD", True, False, True, False)])
    assert v.is_valid(p.to_dict()), list(v.iter_errors(p.to_dict()))


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
