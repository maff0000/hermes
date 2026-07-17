"""Fixture-driven proof of the shared-stream recovery-authority contract (INERT design core).

WO-HELM-HERMES-SHARED-STREAM-RECOVERY-CONTRACT-DESIGN-0001.

20 deterministic fixtures. Fixture #1 is the EXACT 2026-07-16 metals-break transition and MUST yield
RECONNECT_NOT_AUTHORISED / RECOVERY_PROPOSAL_ONLY for the SPX/WTICO-stale + transport-healthy sub-scenario.
Genuine transport faults (socket disconnect / provider disconnect / auth / heartbeat-stale-all-flow /
silent stall / parser) MUST yield RECONNECT_AUTHORISED. Fail-safe rows fail CLOSED to safety.

Run: python3 -m pytest tests/test_shared_stream_recovery_contract_v1.py -q
"""
import datetime

import pytest

from design.shared_stream_recovery_contract_v1 import (
    InstrumentState, LimiterState, TransportSignals, decide,
    SOCKET_CONNECTED, SOCKET_DISCONNECTED, SOCKET_UNKNOWN,
    NO_ACTION, RECOVERY_PROPOSAL_ONLY, RECONNECT_AUTHORISED, RECONNECT_RATE_LIMITED,
    OPERATOR_ESCALATION,
    SOCKET_DISCONNECTED_R, PROVIDER_DISCONNECT_EVENT, AUTHENTICATION_FAILURE,
    HEARTBEAT_STALE, SHARED_STREAM_PROGRESS_STALE, PARSER_EXCEPTION_FATAL,
    UNVALIDATED_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY, PARTIAL_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY,
    ALL_EXPECTED_FLOW_INSTRUMENTS_STALE, ALL_GOVERNED_INSTRUMENTS_CLOSED, TRANSPORT_SIGNAL_CONFLICT,
    RECONNECT_NOT_AUTHORISED, EMERGENCY_BYPASS_LIMITER, RECONNECT_RATE_LIMITED_R,
    ALL_ACTIONS, MANDATED_REASON_CODES,
)

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 7, 16, 21, 15, 0, tzinfo=UTC)  # inside the metals-break window (21:00-22:00Z)
PROVIDER = "OANDA"


# ---- instrument builders reflecting the July 16 board ----
def xau_closed():
    return InstrumentState("XAU_USD", expected_flow=False, stale=False, validated=True, governed_closed=True)

def spx_stale():   # SPX500_USD: unvalidated (fail-loud), expected-open, stale
    return InstrumentState("SPX500_USD", expected_flow=True, stale=True, validated=False)

def wtico_stale():
    return InstrumentState("WTICO_USD", expected_flow=True, stale=True, validated=False)

def eur_flowing():  # a validated FX pair still delivering ticks -> transport proven alive
    return InstrumentState("EUR_USD", expected_flow=True, stale=False, validated=True)

def eur_stale():
    return InstrumentState("EUR_USD", expected_flow=True, stale=True, validated=True)


def _healthy_transport(**kw):
    base = dict(socket_state=SOCKET_CONNECTED, heartbeat_age_s=5.0, shared_stream_silent=False)
    base.update(kw)
    return TransportSignals(**base)


def _decide(transport, instruments, limiter=None, now=NOW, cfg="3"):
    return decide(evaluated_at_utc=now, provider=PROVIDER, transport=transport,
                  instruments=instruments, limiter=limiter or LimiterState(), config_version=cfg)


# =========================================================================== FIXTURE 1 — the July 16 record
def test_fixture_01_july16_spx_wtico_stale_transport_healthy_NOT_AUTHORISED():
    """XAU closed; SPX500_USD + WTICO_USD (both UNVALIDATED) stale; socket CONNECTED; heartbeat fresh;
    EUR_USD progressing. MUST NOT authorise a full-stream reconnect. Proposal only."""
    d = _decide(_healthy_transport(), [xau_closed(), spx_stale(), wtico_stale(), eur_flowing()])
    assert d.action == RECOVERY_PROPOSAL_ONLY
    assert d.transport_authority is False
    assert d.authority_status == "NOT_AUTHORISED"
    assert UNVALIDATED_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY in d.reason_codes
    assert RECONNECT_NOT_AUTHORISED in d.reason_codes
    assert "XAU_USD" not in d.stale_instruments          # governed-closed -> suppressed, no incident
    assert set(d.unvalidated_stale_instruments) == {"SPX500_USD", "WTICO_USD"}
    assert d.transport_state == "TRANSPORT_HEALTHY"


# =========================================================================== genuine transport faults -> AUTHORISED
def test_fixture_02_socket_disconnected_AUTHORISED():
    d = _decide(_healthy_transport(socket_state=SOCKET_DISCONNECTED), [xau_closed(), spx_stale(), eur_flowing()])
    assert d.action == RECONNECT_AUTHORISED
    assert d.emergency_bypass is True
    assert SOCKET_DISCONNECTED_R in d.reason_codes


def test_fixture_03_provider_disconnect_event_AUTHORISED():
    d = _decide(_healthy_transport(provider_disconnect_event=True), [eur_flowing()])
    assert d.action == RECONNECT_AUTHORISED
    assert d.emergency_bypass is True
    assert PROVIDER_DISCONNECT_EVENT in d.reason_codes


def test_fixture_04_auth_failure_AUTHORISED():
    d = _decide(_healthy_transport(auth_failure=True), [eur_flowing()])
    assert d.action == RECONNECT_AUTHORISED
    assert d.transport_state == "AUTH_FAILED"
    assert AUTHENTICATION_FAILURE in d.reason_codes


def test_fixture_05_heartbeat_stale_all_expected_flow_stale_AUTHORISED():
    """Heartbeat hard-stale AND every validated expected-flow instrument stale -> genuine shared loss."""
    t = _healthy_transport(heartbeat_age_s=120.0)  # > hard horizon
    d = _decide(t, [xau_closed(), spx_stale(), eur_stale()])
    assert d.action == RECONNECT_AUTHORISED
    assert d.transport_state == "SILENT_UNCONFIRMED"
    assert HEARTBEAT_STALE in d.reason_codes
    assert ALL_EXPECTED_FLOW_INSTRUMENTS_STALE in d.reason_codes
    assert SHARED_STREAM_PROGRESS_STALE in d.reason_codes


def test_fixture_06_heartbeat_stale_but_fx_progressing_NOT_AUTHORISED():
    """Heartbeat hard-stale but a validated FX pair still progressing -> transport alive despite gap."""
    t = _healthy_transport(heartbeat_age_s=120.0)
    d = _decide(t, [xau_closed(), spx_stale(), eur_flowing()])
    assert d.action == RECOVERY_PROPOSAL_ONLY
    assert d.transport_authority is False
    assert TRANSPORT_SIGNAL_CONFLICT in d.reason_codes
    assert UNVALIDATED_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY in d.reason_codes


def test_fixture_07_shared_stream_silent_AUTHORISED():
    """No line at all (incl. heartbeat) past the stall horizon -> FAULT_CONFIRMED, emergency."""
    t = _healthy_transport(shared_stream_silent=True, heartbeat_age_s=200.0)
    d = _decide(t, [xau_closed(), spx_stale(), eur_stale()])
    assert d.action == RECONNECT_AUTHORISED
    assert d.transport_state == "FAULT_CONFIRMED"
    assert d.emergency_bypass is True
    assert SHARED_STREAM_PROGRESS_STALE in d.reason_codes


def test_fixture_08_parser_fatal_AUTHORISED():
    t = _healthy_transport(parser_fatal=True)
    d = _decide(t, [eur_flowing()])
    assert d.action == RECONNECT_AUTHORISED
    assert d.transport_state == "FAULT_CONFIRMED"
    assert PARSER_EXCEPTION_FATAL in d.reason_codes


# =========================================================================== unvalidated / partial doctrine
def test_fixture_09_only_unvalidated_stale_transport_healthy_PROPOSAL():
    d = _decide(_healthy_transport(), [spx_stale(), wtico_stale()])
    assert d.action == RECOVERY_PROPOSAL_ONLY
    assert UNVALIDATED_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY in d.reason_codes
    assert PARTIAL_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY not in d.reason_codes


def test_fixture_10_single_validated_open_stale_partial_PROPOSAL():
    """One validated open instrument stale while another progresses + heartbeat fresh -> per-instrument, no transport."""
    d = _decide(_healthy_transport(), [eur_stale(),
                                       InstrumentState("GBP_USD", expected_flow=True, stale=False, validated=True)])
    assert d.action == RECOVERY_PROPOSAL_ONLY
    assert PARTIAL_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY in d.reason_codes


# =========================================================================== all-governed-closed / weekend / holiday
def test_fixture_11_all_governed_closed_weekend_NO_ACTION():
    insts = [xau_closed(),
             InstrumentState("SPX500_USD", expected_flow=False, stale=False, validated=False, governed_closed=True),
             InstrumentState("EUR_USD", expected_flow=False, stale=False, validated=True, governed_closed=True)]
    d = _decide(_healthy_transport(), insts)
    assert d.action == NO_ACTION
    assert ALL_GOVERNED_INSTRUMENTS_CLOSED in d.reason_codes
    assert d.transport_authority is False


def test_fixture_12_all_governed_closed_but_genuine_disconnect_AUTHORISED():
    """Weekend/holiday genuine provider disconnect MUST still reconnect promptly."""
    insts = [xau_closed(),
             InstrumentState("EUR_USD", expected_flow=False, stale=False, validated=True, governed_closed=True)]
    d = _decide(_healthy_transport(socket_state=SOCKET_DISCONNECTED), insts)
    assert d.action == RECONNECT_AUTHORISED
    assert SOCKET_DISCONNECTED_R in d.reason_codes


def test_fixture_13_holiday_all_closed_NO_ACTION():
    insts = [InstrumentState("XAU_USD", expected_flow=False, stale=False, validated=True, governed_closed=True)]
    d = _decide(_healthy_transport(), insts)
    assert d.action == NO_ACTION
    assert ALL_GOVERNED_INSTRUMENTS_CLOSED in d.reason_codes


def test_fixture_14_reopening_grace_absence_tolerated_NO_ACTION():
    """In reopening grace: expected_flow=False (grace), not stale-counted, transport healthy -> no action."""
    insts = [InstrumentState("XAU_USD", expected_flow=False, stale=False, validated=True, governed_closed=False)]
    d = _decide(_healthy_transport(), insts)
    assert d.action == NO_ACTION


# =========================================================================== limiter behaviour
def test_fixture_15_limiter_exhausted_application_authority_RATE_LIMITED():
    """Heartbeat-stale shared loss (application-derived authority) + limiter exhausted -> throttled."""
    t = _healthy_transport(heartbeat_age_s=120.0)
    lim = LimiterState(attempts_in_window=3, max_per_window=3)
    d = _decide(t, [xau_closed(), eur_stale()], limiter=lim)
    assert d.action == RECONNECT_RATE_LIMITED
    assert RECONNECT_RATE_LIMITED_R in d.reason_codes
    assert d.transport_authority is True  # authority exists, action throttled


def test_fixture_16_limiter_exhausted_genuine_provider_disconnect_BYPASS():
    lim = LimiterState(attempts_in_window=3, max_per_window=3)
    d = _decide(_healthy_transport(provider_disconnect_event=True), [eur_flowing()], limiter=lim)
    assert d.action == RECONNECT_AUTHORISED
    assert d.emergency_bypass is True
    assert EMERGENCY_BYPASS_LIMITER in d.reason_codes


def test_fixture_17_restart_resets_inmemory_limiter_AUTHORISED():
    """Fresh process: attempts_in_window=0 -> not exhausted -> application authority authorised."""
    t = _healthy_transport(heartbeat_age_s=120.0)
    lim = LimiterState(attempts_in_window=0, max_per_window=3)
    d = _decide(t, [xau_closed(), eur_stale()], limiter=lim)
    assert d.action == RECONNECT_AUTHORISED


# =========================================================================== fail-closed / infra-down
def test_fixture_18_limiter_store_unreadable_redis_down_application_authority_RATE_LIMITED():
    """Redis/SQL down -> limiter unreadable -> treat as exhausted for non-emergency authority (fail closed)."""
    t = _healthy_transport(heartbeat_age_s=120.0)
    lim = LimiterState(available=False)
    d = _decide(t, [xau_closed(), eur_stale()], limiter=lim)
    assert d.action == RECONNECT_RATE_LIMITED
    assert lim.exhausted is True


def test_fixture_19_transport_truth_missing_socket_unknown_hb_none_FAILCLOSED():
    """Socket UNKNOWN + heartbeat None but a validated instrument progressing -> no authority, fail closed."""
    t = TransportSignals(socket_state=SOCKET_UNKNOWN, heartbeat_age_s=None, shared_stream_silent=False)
    d = _decide(t, [eur_flowing(), spx_stale()])
    assert d.action in (RECOVERY_PROPOSAL_ONLY, NO_ACTION, OPERATOR_ESCALATION)
    assert d.transport_authority is False
    assert TRANSPORT_SIGNAL_CONFLICT in d.reason_codes
    assert d.transport_state == "SILENT_UNCONFIRMED"


def test_fixture_20_conflicting_socket_connected_heartbeat_missing_all_validated_stale_AUTHORISED():
    """Socket claims connected but heartbeat missing AND every validated expected-flow instrument stale
    -> corroborated shared loss -> authorise (application-derived)."""
    t = TransportSignals(socket_state=SOCKET_CONNECTED, heartbeat_age_s=None, shared_stream_silent=False)
    d = _decide(t, [xau_closed(), spx_stale(), eur_stale()])
    assert d.action == RECONNECT_AUTHORISED
    assert d.transport_state == "SILENT_UNCONFIRMED"
    assert SHARED_STREAM_PROGRESS_STALE in d.reason_codes
    assert TRANSPORT_SIGNAL_CONFLICT in d.reason_codes


# =========================================================================== clock guard (extra fail-safe)
def test_fixture_21_clock_error_naive_datetime_OPERATOR_ESCALATION():
    naive = datetime.datetime(2026, 7, 16, 21, 15, 0)  # no tzinfo
    d = decide(evaluated_at_utc=naive, provider=PROVIDER, transport=_healthy_transport(),
               instruments=[spx_stale()], limiter=LimiterState(), config_version="3")
    assert d.action == OPERATOR_ESCALATION
    assert d.authority_status == "ESCALATE"
    assert d.transport_authority is False


# =========================================================================== invariants across all fixtures
def test_all_actions_and_reasons_are_in_contract():
    d = _decide(_healthy_transport(), [xau_closed(), spx_stale(), wtico_stale(), eur_flowing()])
    assert d.action in ALL_ACTIONS
    assert d.decision_version == "1"
    # envelope round-trips to dict (observability contract)
    dd = d.to_dict()
    assert dd["action"] == d.action and dd["reason_codes"] == list(d.reason_codes)


def test_mandated_reason_codes_defined():
    assert len(MANDATED_REASON_CODES) == 14  # the 14 non-duplicative mandated codes (RECONNECT_RATE_LIMITED shared)


def test_only_authorised_actions_carry_authority():
    """No decision may reconnect without a transport-authority condition (Option C invariant)."""
    # per-instrument-only staleness never authorises while transport healthy
    for insts in ([spx_stale()], [wtico_stale()], [spx_stale(), wtico_stale()], [eur_stale(),
                  InstrumentState("GBP_USD", expected_flow=True, stale=False, validated=True)]):
        d = _decide(_healthy_transport(), insts)
        assert d.action != RECONNECT_AUTHORISED
        assert d.transport_authority is False


def test_determinism_same_input_same_decision_id():
    a = _decide(_healthy_transport(), [xau_closed(), spx_stale(), eur_flowing()])
    b = _decide(_healthy_transport(), [xau_closed(), spx_stale(), eur_flowing()])
    assert a.decision_id == b.decision_id


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
