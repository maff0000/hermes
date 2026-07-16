"""WO-HELM-HERMES-OANDA-INSTRUMENT-SESSION-EVIDENCE-PACK-0001 — deterministic session fixtures.

Evidence-backed fixtures for the five in-scope instruments, asserted against the UNCHANGED deployed config
(config/market_hours_schedule.v1.json, config_version 3) via the pure decision core
utils/hermes_market_hours_health_v1.py. No Redis/SQL/network; injected UTC clocks only.

Coverage per the WO for the VALIDATED_SCHEDULE_CANDIDATE metals instruments (XAG/XPT/XCU):
winter DST (EST), summer DST (EDT), Sunday open, Friday close, daily break, reopening grace, holiday
uncertainty (fail-open), provider-session ambiguity. Plus: WTICO_USD / SPX500_USD remain fail-closed (None) and
fail OPEN under the real config; and the INERT candidate schedules (NY energy for WTICO; Chicago index for
SPX500/XCU) are validated in-memory and shown UTC-equivalent to the deployed metals break — WITHOUT editing config.

Run: python3 -m pytest tests/test_oanda_instrument_session_fixtures_v1.py -q
"""
import datetime as dt
import json
import pathlib

import pytest

import utils.hermes_market_hours_health_v1 as M

UTC = dt.timezone.utc
CFG = json.loads((pathlib.Path(__file__).resolve().parents[1] / "config/market_hours_schedule.v1.json").read_text())

XAG = M.load_schedule(CFG, "XAG_USD")
XPT = M.load_schedule(CFG, "XPT_USD")
XCU = M.load_schedule(CFG, "XCU_USD")

# stale reference tick/candle far in the past so "open + past grace" reads as stale/missing (drives incident-eligibility)
STALE = dt.datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


def U(y, mo, d, h, mi):
    return dt.datetime(y, mo, d, h, mi, tzinfo=UTC)


def ih(inst, sched, now, tick=None, candle=None, tthr=120, cthr=180, **kw):
    return M.classify_instrument_health(instrument=inst, now_utc=now, sched=sched,
                                        last_tick_utc=tick, last_candle_utc=candle,
                                        tick_threshold_s=tthr, candle_threshold_s=cthr, **kw)


# --------------------------------------------------------------------------- deployed-config sanity
def test_metals_instruments_resolve_to_metals():
    for s, name in [(XAG, "XAG_USD"), (XPT, "XPT_USD"), (XCU, "XCU_USD")]:
        assert s is not None, f"{name} must resolve"
        assert str(s.tz) == "America/New_York"
        assert s.open_weekday == 6 and s.open_local == dt.time(18, 0)   # Sun 18:00 ET
        assert s.close_weekday == 4 and s.close_local == dt.time(17, 0)  # Fri 17:00 ET
        assert s.daily_breaks == ((dt.time(17, 0), dt.time(18, 0)),)     # 17:00-18:00 ET daily rollover


# --------------------------------------------------------------------------- XAG/XPT/XCU: summer (EDT) daily break
@pytest.mark.parametrize("inst,sched", [("XAG_USD", XAG), ("XPT_USD", XPT), ("XCU_USD", XCU)])
def test_summer_edt_daily_break_expected_closed(inst, sched):
    # 2026-07-15 (Wed) 21:30Z = 17:30 EDT -> inside 17:00-18:00 ET break
    d = ih(inst, sched, U(2026, 7, 15, 21, 30), tick=STALE, candle=STALE)
    assert d.state == M.MARKET_CLOSED_EXPECTED
    assert not d.incident_eligible and not d.recovery_eligible and not d.contributes_to_full_stream
    assert d.reason_code == M.REASON_EXPECTED_CLOSED
    assert d.expected_reopen_utc == U(2026, 7, 15, 22, 0)   # reopen 18:00 EDT = 22:00Z


@pytest.mark.parametrize("inst,sched", [("XAG_USD", XAG), ("XPT_USD", XPT), ("XCU_USD", XCU)])
def test_summer_edt_pre_break_open_flowing(inst, sched):
    # 2026-07-15 20:30Z = 16:30 EDT -> open; fresh data -> flowing, no incident
    now = U(2026, 7, 15, 20, 30)
    d = ih(inst, sched, now, tick=now - dt.timedelta(seconds=5), candle=now - dt.timedelta(seconds=5))
    assert d.state == M.MARKET_OPEN_FLOWING and not d.incident_eligible


# --------------------------------------------------------------------------- XAG/XPT/XCU: winter (EST) daily break shifts +1h UTC
@pytest.mark.parametrize("inst,sched", [("XAG_USD", XAG), ("XPT_USD", XPT), ("XCU_USD", XCU)])
def test_winter_est_daily_break_expected_closed(inst, sched):
    # 2026-01-14 (Wed) 22:30Z = 17:30 EST -> inside break (winter break = 22:00-23:00 UTC, DST-aware)
    d = ih(inst, sched, U(2026, 1, 14, 22, 30), tick=STALE, candle=STALE)
    assert d.state == M.MARKET_CLOSED_EXPECTED and not d.incident_eligible
    assert d.expected_reopen_utc == U(2026, 1, 14, 23, 0)   # reopen 18:00 EST = 23:00Z
    # 21:30Z = 16:30 EST is OPEN in winter (would have been break in summer) -> proves DST-awareness
    assert M.classify_market_phase(sched, U(2026, 1, 14, 21, 30)).phase == M.PHASE_OPEN


# --------------------------------------------------------------------------- Sunday open / Friday close
@pytest.mark.parametrize("sched", [XAG, XPT, XCU])
def test_sunday_open_boundary(sched):
    assert M.classify_market_phase(sched, U(2026, 7, 19, 21, 59)).phase == M.PHASE_CLOSED  # Sun 17:59 EDT pre-open
    assert M.classify_market_phase(sched, U(2026, 7, 19, 22, 1)).phase == M.PHASE_OPEN      # Sun 18:01 EDT open


@pytest.mark.parametrize("inst,sched", [("XAG_USD", XAG), ("XPT_USD", XPT), ("XCU_USD", XCU)])
def test_friday_close_expected_closed(inst, sched):
    # 2026-07-17 (Fri) 21:30Z = 17:30 EDT -> after Fri 17:00 ET weekly close -> weekend closed
    d = ih(inst, sched, U(2026, 7, 17, 21, 30), tick=STALE, candle=STALE)
    assert d.state == M.MARKET_CLOSED_EXPECTED and not d.incident_eligible and not d.contributes_to_full_stream


# --------------------------------------------------------------------------- reopening grace after the daily break
@pytest.mark.parametrize("sched", [XAG, XPT, XCU])
def test_reopening_grace_then_missing_after_grace(sched):
    # break ends 22:00Z (Wed). +120s within 300s grace -> REOPENING_GRACE (absence tolerated)
    d_grace = ih("X", sched, U(2026, 7, 15, 22, 2), tick=STALE, candle=STALE)
    assert d_grace.state == M.MARKET_REOPENING_GRACE and not d_grace.incident_eligible
    # +600s past grace, still stale -> escalate
    d_miss = ih("X", sched, U(2026, 7, 15, 22, 10), tick=STALE, candle=STALE)
    assert d_miss.state == M.MARKET_OPEN_MISSING_AFTER_GRACE and d_miss.incident_eligible and d_miss.recovery_eligible


# --------------------------------------------------------------------------- holiday uncertainty: fail-open (noise > hiding)
@pytest.mark.parametrize("sched", [XAG, XPT, XCU])
def test_holiday_uncertainty_fails_open_not_suppressed(sched):
    # holiday_support=false: on a holiday during governed open hours the schedule is NOT holiday-aware -> phase OPEN.
    assert CFG.get("holiday_support") is False
    # e.g. a US holiday weekday at 15:00Z = 11:00 EDT -> still governed-OPEN; a stale stream is NOT suppressed as "expected".
    now = U(2026, 7, 3, 15, 0)   # Fri (independence-day-adjacent), mid-session ET
    assert M.classify_market_phase(sched, now).phase == M.PHASE_OPEN
    d = ih("X", sched, now, tick=STALE, candle=STALE)
    assert d.incident_eligible, "a stale stream during an un-governed holiday must still be flagged (noise > fault-hiding)"


# --------------------------------------------------------------------------- provider-session ambiguity: WTICO/SPX500 stay fail-closed
def test_wtico_spx500_remain_fail_closed_and_fail_open():
    assert "WTICO_USD" in CFG["fail_closed_unvalidated"]
    assert "SPX500_USD" in CFG["fail_closed_unvalidated"]
    assert M.load_schedule(CFG, "WTICO_USD") is None
    assert M.load_schedule(CFG, "SPX500_USD") is None
    # None schedule -> classifier fails OPEN (behave as open); stale data still drives an incident (never suppressed)
    for inst in ("WTICO_USD", "SPX500_USD"):
        d = ih(inst, None, U(2026, 7, 15, 21, 30), tick=STALE, candle=STALE)
        assert d.fail_closed and d.state == M.MARKET_OPEN_STALE and d.incident_eligible
        assert d.reason_code == M.REASON_SCHEDULE_UNKNOWN_FAILCLOSED


# --------------------------------------------------------------------------- INERT candidate schedules (NOT in repo config)
# WTICO_USD -> NY 'energy' (17:00-18:00 ET break); SPX500_USD/XCU_USD -> Chicago 'chicago_index' (16:00-17:00 CT break).
ENERGY_CFG = {
    "config_version": "3", "market_timezone": "America/New_York", "reopening_grace_seconds": 300,
    "named_schedules": {"energy": {
        "weekly_open": {"weekday": 6, "local_time": "18:00"},
        "weekly_close": {"weekday": 4, "local_time": "17:00"},
        "daily_breaks": [{"local_start": "17:00", "local_end": "18:00", "label": "OANDA_ENERGY_ROLLOVER"}]}},
    "instrument_map": {"WTICO_USD": "energy"},
}
CHI_CFG = {
    "config_version": "3", "market_timezone": "America/Chicago", "reopening_grace_seconds": 300,
    "named_schedules": {"chicago_index": {
        "weekly_open": {"weekday": 6, "local_time": "17:00"},
        "weekly_close": {"weekday": 4, "local_time": "16:00"},
        "daily_breaks": [{"local_start": "16:00", "local_end": "17:00", "label": "CME_INDEX_ROLLOVER"}]}},
    "instrument_map": {"SPX500_USD": "chicago_index", "XCU_USD": "chicago_index"},
}


def test_inert_wtico_energy_candidate_coherent():
    s = M.load_schedule(ENERGY_CFG, "WTICO_USD")
    assert s is not None and str(s.tz) == "America/New_York"
    assert M.classify_market_phase(s, U(2026, 7, 15, 21, 30)).phase == M.PHASE_CLOSED   # summer break 21:00-22:00Z
    assert M.classify_market_phase(s, U(2026, 7, 15, 20, 30)).phase == M.PHASE_OPEN     # pre-break open
    assert M.classify_market_phase(s, U(2026, 1, 14, 22, 30)).phase == M.PHASE_CLOSED   # winter break 22:00-23:00Z
    assert M.classify_market_phase(s, U(2026, 7, 17, 21, 30)).phase == M.PHASE_CLOSED   # Fri weekend closed


def test_inert_chicago_candidate_utc_equivalent_to_deployed_metals_break():
    """Chicago 16:00-17:00 CT break === deployed NY 17:00-18:00 ET metals break in UTC on every date (US DST co-moves).
    Proves XCU's deployed metals mapping is UTC-correct and the SPX500 Chicago candidate is coherent."""
    spx = M.load_schedule(CHI_CFG, "SPX500_USD")
    xcu_chi = M.load_schedule(CHI_CFG, "XCU_USD")
    assert spx is not None and str(spx.tz) == "America/Chicago"
    probes = [
        (U(2026, 7, 15, 21, 30), M.PHASE_CLOSED),   # summer break
        (U(2026, 7, 15, 20, 30), M.PHASE_OPEN),     # summer pre-break
        (U(2026, 1, 14, 22, 30), M.PHASE_CLOSED),   # winter break
        (U(2026, 1, 14, 21, 30), M.PHASE_OPEN),     # winter pre-break open
        (U(2026, 7, 17, 21, 30), M.PHASE_CLOSED),   # Friday weekend
        (U(2026, 7, 19, 22, 1), M.PHASE_OPEN),      # Sunday reopen
    ]
    for now, expected in probes:
        assert M.classify_market_phase(spx, now).phase == expected
        assert M.classify_market_phase(xcu_chi, now).phase == expected
        # UTC-identical to the deployed NY metals schedule (via XAG)
        assert M.classify_market_phase(XAG, now).phase == expected
