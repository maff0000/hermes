"""WO-HELM-HERMES-PR101-MARKET-HOURS-STREAM-HEALTH-DEPLOYMENT-READINESS-0001 — hardening tests.
Unknown-instrument fail-closed, explicit asset-class default, config-completeness, SPX500/ICO fail-closed, fallback safety.
"""
import datetime as dt
import json
import pathlib

import pytest

import utils.hermes_market_hours_health_v1 as M

UTC = dt.timezone.utc
ROOT = pathlib.Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config/market_hours_schedule.v1.json").read_text())
# actual configured HERMES inventory (from .env INSTRUMENTS, observed 2026-07-16)
CONFIGURED = ["AUD_USD", "EUR_GBP", "EUR_USD", "GBP_USD", "NZD_USD", "SPX500_USD",
              "USD_CAD", "USD_CHF", "USD_JPY", "XAG_USD", "XAU_USD", "XCU_USD", "XPT_USD", "WTICO_USD"]


def ih(inst, now, tick, candle):
    return M.classify_instrument_health(instrument=inst, now_utc=now, sched=M.load_schedule(CFG, inst),
                                        last_tick_utc=tick, last_candle_utc=candle, tick_threshold_s=120, candle_threshold_s=180)


# --------------------------------------------------------------------------- unknown-instrument doctrine
def test_truly_unknown_instrument_resolves_none_no_default():
    assert M.load_schedule(CFG, "ZZZ_UNKNOWN") is None
    assert "instruments" not in CFG and "_default_fx" not in CFG.get("instrument_map", {})  # no silent fx default in resolution


def test_unknown_instrument_never_suppresses_weekend_staleness():
    # a SYNTHETIC unknown instrument (crypto-like ~24/7) on a Saturday: no schedule -> fail-closed -> classify open ->
    # stale incident eligible (NOT suppressed). Proves no generic fallback can re-introduce the weekend-suppression hazard.
    sat = dt.datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    d = ih("UNKNOWN_TEST_INSTRUMENT", sat, sat - dt.timedelta(hours=1), sat - dt.timedelta(hours=1))
    assert M.load_schedule(CFG, "UNKNOWN_TEST_INSTRUMENT") is None
    assert d.fail_closed and d.incident_eligible and d.recovery_eligible


def test_explicit_fx_asset_class_default_via_map():
    for fx in ("EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "EUR_GBP", "NZD_USD", "USD_CAD", "USD_CHF"):
        s = M.load_schedule(CFG, fx)
        assert s is not None and s.daily_breaks == ()      # fx: no daily break


def test_metals_asset_class_has_daily_break():
    for m in ("XAU_USD", "XAG_USD", "XPT_USD", "XCU_USD"):
        s = M.load_schedule(CFG, m)
        assert s is not None and len(s.daily_breaks) == 1


# --------------------------------------------------------------------------- SPX500 / ICO fail-closed rulings
def test_spx500_fail_closed_pending_validation():
    assert M.load_schedule(CFG, "SPX500_USD") is None
    assert "SPX500_USD" in CFG["fail_closed_unvalidated"]
    # 16:00-17:00 ET (a weekday) -> fail-closed -> classify OPEN -> NOT marked closed merely due to cash-market close
    et_1630 = dt.datetime(2026, 7, 15, 20, 30, tzinfo=UTC)  # 16:30 EDT
    d = ih("SPX500_USD", et_1630, et_1630 - dt.timedelta(seconds=5), et_1630 - dt.timedelta(seconds=30))
    assert d.fail_closed and d.state in (M.MARKET_OPEN_FLOWING, M.MARKET_OPEN_STALE)  # not CLOSED_EXPECTED


def test_wtico_fail_closed():
    # WTICO_USD (WTI crude CFD): fail-closed pending OANDA evidence; NOT silently unmapped; NOT guessed via NYMEX hours
    assert M.load_schedule(CFG, "WTICO_USD") is None and "WTICO_USD" in CFG["fail_closed_unvalidated"]
    ok, rep = M.validate_config_completeness(CFG, CONFIGURED)
    assert "WTICO_USD" in rep["fail_closed"] and "WTICO_USD" not in rep["unmapped"]


def test_no_phantom_ico_in_config():
    assert "ICO_USD" not in CFG.get("instrument_map", {}) and "ICO_USD" not in CFG.get("fail_closed_unvalidated", {})
    assert "ICO_USD" not in CONFIGURED   # phantom removed from the real inventory


def test_ico_not_required_for_success():
    ok, rep = M.validate_config_completeness(CFG, CONFIGURED)  # CONFIGURED has WTICO not ICO
    assert ok and "ICO_USD" not in rep["resolved"] and "ICO_USD" not in rep["fail_closed"]


def test_inventory_substitution_wtico_to_ico_differs():
    # substituting the phantom ICO_USD for the real WTICO_USD yields a DIFFERENT (failing) result: ICO is unmapped
    sub = [i for i in CONFIGURED if i != "WTICO_USD"] + ["ICO_USD"]
    ok, rep = M.validate_config_completeness(CFG, sub)
    assert not ok and "ICO_USD" in rep["unmapped"]


def test_unsupported_config_version_fails_closed():
    import json as _j
    bad = _j.loads(_j.dumps(CFG)); bad["config_version"] = "999"
    assert M.load_schedule(bad, "XAU_USD") is None            # unsupported version -> fail loud (None)
    ok, rep = M.validate_config_completeness(bad, ["XAU_USD"])
    assert not ok and any("config_version" in e for e in rep["errors"])


def test_config_v2_retired_fails_loud():
    # WO-HELM-HERMES-CONFIG-V2-RETIREMENT-AND-COMPATIBILITY-DESIGN-0001: v2 is retired. A v2 config must now be REJECTED
    # (fail loud = None, never suppress) exactly like any other unsupported version -> no silent acceptance of a stale,
    # superseded (phantom-inventory) config. Supported set is {"3"} only.
    import json as _j
    assert "2" not in M.SUPPORTED_CONFIG_VERSIONS and M.SUPPORTED_CONFIG_VERSIONS == frozenset({"3"})
    v2 = _j.loads(_j.dumps(CFG)); v2["config_version"] = "2"
    assert M.load_schedule(v2, "XAU_USD") is None            # retired version -> fail loud (None), no _default_fx path
    ok, rep = M.validate_config_completeness(v2, ["XAU_USD"])
    assert not ok and any("config_version" in e for e in rep["errors"])


# --------------------------------------------------------------------------- config completeness validator
def test_config_completeness_all_configured_resolve_or_failclosed():
    ok, rep = M.validate_config_completeness(CFG, CONFIGURED)
    assert ok, rep
    assert len(rep["resolved"]) == 12 and set(rep["fail_closed"]) == {"SPX500_USD", "WTICO_USD"} and rep["unmapped"] == []
    assert rep["errors"] == [] and rep["config_version"] == "3" and rep["holiday_support"] is False


def test_completeness_rejects_ungoverned_configured_instrument():
    ok, rep = M.validate_config_completeness(CFG, CONFIGURED + ["MYSTERY_XYZ"])  # not mapped, not fail-closed
    assert not ok and "MYSTERY_XYZ" in rep["unmapped"]


def test_completeness_rejects_duplicate_alias():
    ok, rep = M.validate_config_completeness(CFG, ["XAU_USD", "XAU_USD"])
    assert not ok and any("duplicate" in e for e in rep["errors"])


def test_completeness_rejects_missing_named_schedule():
    bad = json.loads(json.dumps(CFG)); bad["instrument_map"]["XAU_USD"] = "does_not_exist"
    ok, rep = M.validate_config_completeness(bad, ["XAU_USD"])
    assert not ok and rep["errors"]


def test_completeness_rejects_invalid_timezone():
    bad = json.loads(json.dumps(CFG)); bad["market_timezone"] = "Not/AZone"
    ok, rep = M.validate_config_completeness(bad, ["XAU_USD"])
    assert not ok and any("timezone" in e for e in rep["errors"])


# --------------------------------------------------------------------------- holiday: unsupported -> not suppressed
def test_unsupported_holiday_does_not_suppress():
    assert CFG["holiday_support"] is False and "holiday_semantics" in CFG
    # a US holiday on a weekday during open hours: no holiday awareness -> normal rule (open) -> stale still eligible
    now = dt.datetime(2026, 7, 3, 15, 0, tzinfo=UTC)  # weekday, open; if data absent -> incident (noise > hiding)
    d = ih("XAU_USD", now, now - dt.timedelta(hours=2), now - dt.timedelta(hours=2))
    assert d.incident_eligible


# --------------------------------------------------------------------------- fallback / adapter safety
def test_adapter_missing_config_fails_open():
    mh = M.DstAwareMarketHours({}, "XAU_USD")  # empty cfg -> no schedule
    openv, reason = mh.is_market_open(dt.datetime(2026, 7, 15, 21, 30, tzinfo=UTC))
    assert openv is True                        # fail closed = open (never suppress)


def test_adapter_malformed_schedule_fails_open():
    bad = json.loads(json.dumps(CFG)); bad["market_timezone"] = "Not/AZone"
    mh = M.DstAwareMarketHours(bad, "XAU_USD")
    openv, reason = mh.is_market_open(dt.datetime(2026, 7, 15, 21, 30, tzinfo=UTC))
    assert openv is True and reason in (M.REASON_SCHEDULE_MALFORMED_FAILCLOSED, M.REASON_SCHEDULE_UNKNOWN_FAILCLOSED)


def test_adapter_unknown_instrument_truth_expected_open():
    mh = M.DstAwareMarketHours(CFG, "XAU_USD")
    ok, reason = mh.is_truth_expected("SPX500_USD")  # fail-closed instrument -> expect data (open) -> not suppressed
    assert ok is True


def test_schedule_file_is_packaged_at_expected_path():
    p = ROOT / "config" / "market_hours_schedule.v1.json"
    assert p.is_file() and json.loads(p.read_text())["config_version"] == "3"
    import re
    assert not re.search(r"(password|secret|api[_-]?key|token)\s*[:=]\s*[\"\047]?[A-Za-z0-9]{6}", p.read_text(), re.I)
