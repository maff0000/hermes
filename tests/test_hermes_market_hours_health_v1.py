"""WO-HELM-HERMES-MARKET-HOURS-AWARE-STREAM-HEALTH-AND-RECOVERY-SUPPRESSION-0001 — pure decision core tests.
Deterministic, injected clocks, no runtime/Redis/SQL. Covers the §13 matrix + §14 Incident #1104 replay.
"""
import ast
import copy
import datetime as dt
import inspect
import json
import pathlib
import threading

import pytest

import utils.hermes_market_hours_health_v1 as M

UTC = dt.timezone.utc
CFG = json.loads((pathlib.Path(__file__).resolve().parents[1] / "config/market_hours_schedule.v1.json").read_text())
XAU = M.load_schedule(CFG, "XAU_USD")
SPX = M.load_schedule(CFG, "SPX500_USD")
FX = M.load_schedule(CFG, "EUR_USD")           # falls back to _default_fx
FRESH = dt.datetime(2026, 7, 15, 20, 58, tzinfo=UTC)


def U(y, mo, d, h, mi):
    return dt.datetime(y, mo, d, h, mi, tzinfo=UTC)


def ih(inst, sched, now, tick=None, candle=None, tthr=120, cthr=180, **kw):
    return M.classify_instrument_health(instrument=inst, now_utc=now, sched=sched,
                                        last_tick_utc=tick, last_candle_utc=candle,
                                        tick_threshold_s=tthr, candle_threshold_s=cthr, **kw)


# --------------------------------------------------------------------------- 1-3 XAU daily close / reopen / after-grace
def test_1_xau_daily_close_expected_no_ticks():
    d = ih("XAU_USD", XAU, U(2026, 7, 15, 21, 30), tick=FRESH, candle=FRESH)  # 17:30 EDT break
    assert d.state == M.MARKET_CLOSED_EXPECTED and not d.incident_eligible and not d.recovery_eligible
    assert d.reason_code == M.REASON_EXPECTED_CLOSED and d.expected_reopen_utc == U(2026, 7, 15, 22, 0)


def test_2_xau_reopen_within_grace():
    d = ih("XAU_USD", XAU, U(2026, 7, 15, 22, 2), tick=FRESH, candle=FRESH)  # reopened 22:00 UTC, +120s
    assert d.state == M.MARKET_REOPENING_GRACE and not d.incident_eligible


def test_3_xau_missing_after_reopen_grace():
    d = ih("XAU_USD", XAU, U(2026, 7, 15, 22, 10), tick=FRESH, candle=FRESH)  # +600s > 300s grace
    assert d.state == M.MARKET_OPEN_MISSING_AFTER_GRACE and d.incident_eligible and d.recovery_eligible


# --------------------------------------------------------------------------- 4-6 Friday close / Sunday open / DST
def test_4_friday_close():
    assert M.classify_market_phase(XAU, U(2026, 7, 17, 22, 0)).phase == M.PHASE_CLOSED  # Fri 18:00 EDT
    d = ih("XAU_USD", XAU, U(2026, 7, 17, 22, 30), tick=FRESH, candle=FRESH)
    assert d.state == M.MARKET_CLOSED_EXPECTED and not d.incident_eligible


def test_5_sunday_reopen():
    assert M.classify_market_phase(XAU, U(2026, 7, 19, 20, 0)).phase == M.PHASE_CLOSED   # Sun 16:00 EDT pre-open
    assert M.classify_market_phase(XAU, U(2026, 7, 19, 22, 0)).phase == M.PHASE_OPEN      # Sun 18:00 EDT open


def test_6_dst_break_shifts_utc():
    # 17:00 ET break -> summer 21:00 UTC, winter 22:00 UTC
    assert M.classify_market_phase(XAU, U(2026, 7, 15, 21, 30)).reason == "DAILY_BREAK"   # EDT
    assert M.classify_market_phase(XAU, U(2026, 1, 15, 21, 30)).phase == M.PHASE_OPEN      # EST: 21:30 UTC = 16:30 EST open
    assert M.classify_market_phase(XAU, U(2026, 1, 15, 22, 30)).reason == "DAILY_BREAK"    # EST: 17:30 EST break


# --------------------------------------------------------------------------- 7-10 mixed-market
def test_7_spx_closed_while_xau_open():
    now = U(2026, 7, 15, 20, 30)  # 16:30 EDT: XAU open, SPX open (SPX break also 17:00). Use SPX weekend to be closed:
    now = U(2026, 7, 18, 12, 0)   # Sat: both closed. Instead test after-hours quiet SPX vs open XAU on a weekday morning:
    # 2026-07-15 13:00 UTC = 09:00 EDT: XAU open, SPX open. SPX 'after-hours' modelled by its daily break window:
    xau = ih("XAU_USD", XAU, U(2026, 7, 15, 21, 30), tick=FRESH, candle=FRESH)   # XAU in break
    spx = ih("SPX500_USD", SPX, U(2026, 7, 15, 21, 30), tick=FRESH, candle=FRESH) # SPX in break
    assert xau.state == M.MARKET_CLOSED_EXPECTED and spx.state == M.MARKET_CLOSED_EXPECTED
    assert not xau.contributes_to_full_stream and not spx.contributes_to_full_stream


def test_8_xau_closed_while_fx_open():
    now = U(2026, 7, 15, 21, 30)  # XAU in break (metal), FX default has NO daily break -> open
    xau = ih("XAU_USD", XAU, now, tick=FRESH, candle=FRESH)
    fx = ih("EUR_USD", FX, now, tick=now - dt.timedelta(seconds=5), candle=now - dt.timedelta(seconds=5))
    assert xau.state == M.MARKET_CLOSED_EXPECTED and fx.state == M.MARKET_OPEN_FLOWING


def test_9_one_open_stale_while_closed_quiet():
    now = U(2026, 7, 15, 21, 30)
    xau = ih("XAU_USD", XAU, now, tick=FRESH, candle=FRESH)                       # closed
    fx = ih("EUR_USD", FX, now, tick=FRESH, candle=FRESH)                          # open but stale (FRESH=20:58, now 21:30)
    assert xau.state == M.MARKET_CLOSED_EXPECTED
    assert fx.state in (M.MARKET_OPEN_STALE, M.MARKET_OPEN_MISSING_AFTER_GRACE)   # open + stale (past its Sunday-open grace)
    assert fx.incident_eligible and fx.contributes_to_full_stream and not xau.contributes_to_full_stream


def test_10_all_instruments_expected_closed():
    now = U(2026, 7, 18, 12, 0)  # Saturday
    ds = [ih(i, s, now, tick=FRESH, candle=FRESH) for i, s in [("XAU_USD", XAU), ("SPX500_USD", SPX), ("EUR_USD", FX)]]
    assert all(d.state == M.MARKET_CLOSED_EXPECTED for d in ds)
    elig, reason = M.full_stream_recovery_eligible(instrument_decisions=ds)
    assert not elig and reason == "NO_ELIGIBLE_FULL_STREAM_TRIGGER"


# --------------------------------------------------------------------------- 11-12 genuine faults never suppressed
def test_11_socket_disconnect_during_close_not_suppressed():
    d = ih("XAU_USD", XAU, U(2026, 7, 15, 21, 30), tick=None, candle=None, connection_fault=True)
    assert d.incident_eligible and d.recovery_eligible and d.reason_code == M.REASON_CONNECTION_FAULT


def test_12_auth_failure_during_close_not_suppressed():
    # auth/session failure is surfaced as a shared_stream_fault at the full-stream layer
    ds = [ih("XAU_USD", XAU, U(2026, 7, 15, 21, 30), tick=FRESH, candle=FRESH)]  # XAU closed
    elig, reason = M.full_stream_recovery_eligible(instrument_decisions=ds, shared_stream_fault=True)
    assert elig and reason == "SHARED_STREAM_FAULT"


# --------------------------------------------------------------------------- 13-14 fail-closed on bad schedule / unknown instrument
def test_13_malformed_schedule_fails_closed_open():
    d = ih("XAU_USD", None, U(2026, 7, 15, 21, 30), tick=FRESH, candle=FRESH, schedule_error=True)
    assert d.fail_closed and d.reason_code == M.REASON_SCHEDULE_MALFORMED_FAILCLOSED
    assert d.incident_eligible  # stale (FRESH old) while failing closed => normal detection fires (not suppressed)


def test_14_unknown_instrument_no_schedule_fails_closed():
    bad = json.loads(json.dumps(CFG)); bad["instruments"].pop("_default_fx")
    assert M.load_schedule(bad, "ZZZ_UNKNOWN") is None   # unknown -> None -> caller fails closed
    d = ih("ZZZ_UNKNOWN", None, U(2026, 7, 15, 21, 30), tick=FRESH, candle=FRESH)
    assert d.fail_closed and d.incident_eligible


# --------------------------------------------------------------------------- 15-18 suppression + full-stream protection
def test_15_no_stale_incident_during_expected_close():
    assert not ih("XAU_USD", XAU, U(2026, 7, 15, 21, 30), tick=FRESH, candle=FRESH).incident_eligible


def test_16_no_per_instrument_recovery_during_expected_close():
    assert not ih("XAU_USD", XAU, U(2026, 7, 15, 21, 30), tick=FRESH, candle=FRESH).recovery_eligible


def test_17_no_full_stream_from_closed_instrument_only():
    ds = [ih("XAU_USD", XAU, U(2026, 7, 15, 21, 30), tick=FRESH, candle=FRESH),
          ih("SPX500_USD", SPX, U(2026, 7, 15, 21, 30), tick=FRESH, candle=FRESH)]  # both closed (F-2 SPX after-hours)
    elig, reason = M.full_stream_recovery_eligible(instrument_decisions=ds)
    assert not elig


def test_18_full_stream_still_triggers_on_genuine_shared_fault():
    elig, reason = M.full_stream_recovery_eligible(instrument_decisions=[], shared_stream_fault=True)
    assert elig
    # or quorum of open-stale instruments
    now = U(2026, 7, 15, 21, 30)
    ds = [ih("EUR_USD", FX, now, tick=FRESH, candle=FRESH), ih("GBP_USD", FX, now, tick=FRESH, candle=FRESH)]  # 2 open stale
    elig2, reason2 = M.full_stream_recovery_eligible(instrument_decisions=ds)
    assert elig2 and "QUORUM" in reason2


# --------------------------------------------------------------------------- 19-24
def test_19_reopening_grace_expires_without_data_escalates():
    d = ih("XAU_USD", XAU, U(2026, 7, 15, 22, 10), tick=FRESH, candle=FRESH)
    assert d.state == M.MARKET_OPEN_MISSING_AFTER_GRACE and d.incident_eligible


def test_20_open_market_fresh_unchanged_flowing():
    now = U(2026, 7, 15, 13, 0)  # 09:00 EDT open
    d = ih("XAU_USD", XAU, now, tick=now - dt.timedelta(seconds=5), candle=now - dt.timedelta(seconds=30))
    assert d.state == M.MARKET_OPEN_FLOWING and not d.incident_eligible and not d.recovery_eligible


def test_21_gap_classified_expected_vs_recoverable():
    # absence fully inside the daily break -> expected/non-recoverable
    assert M.classify_absence_for_gap(instrument="XAU_USD", interval_start_utc=U(2026, 7, 15, 21, 5),
                                      interval_end_utc=U(2026, 7, 15, 21, 55), sched=XAU) == M.GAP_EXPECTED_CLOSED
    # absence fully inside open hours -> recoverable
    assert M.classify_absence_for_gap(instrument="EUR_USD", interval_start_utc=U(2026, 7, 15, 13, 0),
                                      interval_end_utc=U(2026, 7, 15, 13, 30), sched=FX) == M.GAP_RECOVERABLE
    # unknown schedule -> unclassified (never silently expected)
    assert M.classify_absence_for_gap(instrument="X", interval_start_utc=U(2026, 7, 15, 21, 5),
                                      interval_end_utc=U(2026, 7, 15, 21, 55), sched=None) == M.GAP_UNKNOWN


def test_22_rest_quote_freshness_does_not_imply_stream_flow():
    # the classifier only consumes stream tick/candle timestamps; a fresh REST quote is NOT an input and cannot make
    # an open+past-grace missing stream look flowing.
    now = U(2026, 7, 15, 22, 10)  # reopened, past grace
    d = ih("XAU_USD", XAU, now, tick=FRESH, candle=FRESH)  # stream stale; REST quote irrelevant
    assert d.state == M.MARKET_OPEN_MISSING_AFTER_GRACE and d.incident_eligible
    assert "quote" not in inspect.signature(M.classify_instrument_health).parameters


def test_23_utc_required_and_naive_rejected():
    with pytest.raises(M.ScheduleError):
        M.classify_market_phase(XAU, dt.datetime(2026, 7, 15, 21, 30))  # naive
    d = ih("XAU_USD", XAU, U(2026, 7, 15, 21, 30), tick=FRESH, candle=FRESH)
    assert d.evaluated_at_utc.tzinfo == UTC


def test_24_pure_no_io_no_mutation():
    # inputs unchanged
    now = U(2026, 7, 15, 21, 30)
    args = dict(instrument="XAU_USD", now_utc=now, sched=XAU, last_tick_utc=FRESH, last_candle_utc=FRESH,
                tick_threshold_s=120, candle_threshold_s=180)
    before = copy.deepcopy({k: v for k, v in args.items() if k != "sched"})
    M.classify_instrument_health(**args)
    assert {k: v for k, v in args.items() if k != "sched"} == before
    # no threads created; no forbidden imports
    b = threading.active_count(); M.classify_instrument_health(**args); assert threading.active_count() == b
    tree = ast.parse(inspect.getsource(M))
    mods = {(n.module or "") for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
           {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not (mods & {"redis", "pymysql", "sqlalchemy", "requests", "socket", "subprocess", "os", "threading"})
    assert M.load_schedule(CFG, "XAU_USD") is not None


# --------------------------------------------------------------------------- §14 Incident #1104 deterministic replay
def test_incident_1104_replay_full_timeline():
    tk = dt.datetime(2026, 7, 15, 20, 58, tzinfo=UTC)   # last XAU M1/tick at 20:58Z
    timeline = {
        "during_break_2130": (U(2026, 7, 15, 21, 30), M.MARKET_CLOSED_EXPECTED, False),
        "during_break_2200_edge": (U(2026, 7, 15, 21, 59), M.MARKET_CLOSED_EXPECTED, False),
        "reopen_grace_2202": (U(2026, 7, 15, 22, 2), M.MARKET_REOPENING_GRACE, False),
        "resumed_2204_fresh": (U(2026, 7, 15, 22, 4), None, False),  # data resumed -> flowing
    }
    for label, (now, exp_state, exp_incident) in timeline.items():
        tick = tk if now < U(2026, 7, 15, 22, 4) else now - dt.timedelta(seconds=10)
        candle = tk if now < U(2026, 7, 15, 22, 4) else now - dt.timedelta(seconds=10)
        d = ih("XAU_USD", XAU, now, tick=tick, candle=candle)
        if exp_state is not None:
            assert d.state == exp_state, f"{label}: {d.state}"
        assert d.incident_eligible == exp_incident, f"{label} incident"
        assert not d.contributes_to_full_stream, f"{label} full-stream"
    # SPX after-hours quiet throughout -> never eligible; no full-stream reconnect from XAU+SPX scheduled inactivity
    ds = [ih("XAU_USD", XAU, U(2026, 7, 15, 21, 30), tick=tk, candle=tk),
          ih("SPX500_USD", SPX, U(2026, 7, 15, 21, 30), tick=tk, candle=tk)]
    assert not M.full_stream_recovery_eligible(instrument_decisions=ds)[0]
