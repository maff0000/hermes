"""Reusable metadata-driven market-hours policy + its consumption by the generic gap classifier.
WO-HELM-HERMES-ADVANCED-V1-EIGHT-INSTRUMENT-SHADOW-0001. Pure, no I/O. Parameterised by policy class + registry data;
no per-instrument test module. UTC is the sole time authority; NY-local reasoning is DST-correct via zoneinfo.
"""
from datetime import datetime, timezone, timedelta

import pytest

import utils.hermes_market_hours_policy_v1 as mhp
import utils.hermes_gaps_v1 as gaps

UTC = timezone.utc
# Summer (EDT, UTC-4) and winter (EST, UTC-5) reference instants — same NY-local wall time, different UTC.
WED_SUMMER_MIDDAY = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)     # Wed 08:00 EDT -> OPEN all policies
WED_WINTER_MIDDAY = datetime(2026, 1, 14, 12, 0, tzinfo=UTC)    # Wed 07:00 EST -> OPEN all policies
SAT = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)                  # weekend
# NY 17:30 daily-break instant: summer = 21:30Z, winter = 22:30Z (DST-shifted by one hour).
BREAK_SUMMER = datetime(2026, 7, 8, 21, 30, tzinfo=UTC)        # Wed 17:30 EDT
BREAK_WINTER = datetime(2026, 1, 14, 22, 30, tzinfo=UTC)       # Wed 17:30 EST


# --------------------------------------------------------------------------- resolution + fail-closed
def test_resolve_by_metadata_key_only():
    for key in ("fx_24x5", "metals", "index_cash", "energy"):
        p = mhp.resolve_policy(key)
        assert isinstance(p, mhp.SessionPolicy) and p.key == key


@pytest.mark.parametrize("bad", ["SPX500_USD", "WTICO_USD", "wti", "unknown", ""])
def test_unknown_or_ticker_key_rejected(bad):
    with pytest.raises(mhp.PolicyError):
        mhp.resolve_policy(bad)


def test_missing_metadata_fails_closed_no_default():
    with pytest.raises(mhp.PolicyError):
        mhp.resolve_policy(None)
    with pytest.raises(mhp.PolicyError):
        mhp.resolve_policy("   ")


def test_no_ticker_lookup_in_resolver():
    import inspect
    src = inspect.getsource(mhp)
    for ticker in ("SPX500_USD", "WTICO_USD", "XAU_USD", "EUR_USD"):
        # tickers may not drive behaviour in the policy module (data instances are keyed by governed policy key only)
        assert ticker not in src


# --------------------------------------------------------------------------- open-market
def test_all_policies_open_midweek_both_seasons():
    for key in mhp.GOVERNED_POLICY_KEYS:
        p = mhp.resolve_policy(key)
        assert p.phase(WED_SUMMER_MIDDAY) == mhp.PHASE_OPEN
        assert p.phase(WED_WINTER_MIDDAY) == mhp.PHASE_OPEN


def test_all_policies_closed_weekend():
    for key in mhp.GOVERNED_POLICY_KEYS:
        assert mhp.resolve_policy(key).phase(SAT) == mhp.PHASE_CLOSED_WEEKEND


# --------------------------------------------------------------------------- FX = continuous week (no daily break)
def test_fx_no_daily_break_open_during_1730ny():
    fx = mhp.resolve_policy("fx_24x5")
    assert fx.phase(BREAK_SUMMER) == mhp.PHASE_OPEN and fx.phase(BREAK_WINTER) == mhp.PHASE_OPEN


def test_fx_reopen_sunday_1700ny():
    fx = mhp.resolve_policy("fx_24x5")
    # summer: Sun 17:00 EDT = 21:00Z -> OPEN at/after; 16:59 EDT = 20:59Z -> still weekend
    assert fx.phase(datetime(2026, 7, 12, 21, 0, tzinfo=UTC)) == mhp.PHASE_OPEN
    assert fx.phase(datetime(2026, 7, 12, 20, 59, tzinfo=UTC)) == mhp.PHASE_CLOSED_WEEKEND


# --------------------------------------------------------------------------- scheduled daily closures (metadata-driven)
@pytest.mark.parametrize("key", ["metals", "index_cash", "energy"])
def test_daily_break_is_expected_closure_dst_correct(key):
    p = mhp.resolve_policy(key)
    # NY 17:30 is inside the 17:00-18:00 daily halt in BOTH seasons, at a DIFFERENT UTC hour -> DST-correct
    assert p.phase(BREAK_SUMMER) == mhp.PHASE_CLOSED_SESSION      # 21:30Z summer
    assert p.phase(BREAK_WINTER) == mhp.PHASE_CLOSED_SESSION      # 22:30Z winter
    assert p.is_expected_closure(BREAK_SUMMER) and not p.is_open(BREAK_SUMMER)
    # a missing candle during this window is an EXPECTED closure, not an outage
    assert p.is_expected_closure(BREAK_WINTER)


def test_metals_reopen_sunday_1800ny_distinct_from_fx():
    metals = mhp.resolve_policy("metals")
    # metals opens Sun 18:00 NY (summer = 22:00Z); at 17:30 EDT (21:30Z) metals is still weekend but fx is open
    assert metals.phase(datetime(2026, 7, 12, 21, 30, tzinfo=UTC)) == mhp.PHASE_CLOSED_WEEKEND
    assert mhp.resolve_policy("fx_24x5").phase(datetime(2026, 7, 12, 21, 30, tzinfo=UTC)) == mhp.PHASE_OPEN
    assert metals.phase(datetime(2026, 7, 12, 22, 0, tzinfo=UTC)) == mhp.PHASE_OPEN


# --------------------------------------------------------------------------- policy isolation (no global state)
def test_market_hours_policy_isolation_green():
    fx, metals, idx, energy = (mhp.resolve_policy(k) for k in ("fx_24x5", "metals", "index_cash", "energy"))
    t = BREAK_SUMMER   # 17:30 EDT: fx OPEN, metals/index/energy CLOSED_SESSION — simultaneously, no shared phase
    assert fx.phase(t) == mhp.PHASE_OPEN
    assert idx.phase(t) == mhp.PHASE_CLOSED_SESSION and energy.phase(t) == mhp.PHASE_CLOSED_SESSION
    # resolving the same key twice returns the same shared instance; no per-call/global mutable market state
    assert mhp.resolve_policy("metals") is mhp.resolve_policy("metals")
    # each policy independent: index closure does not change fx result and vice-versa
    assert fx.phase(t) == mhp.PHASE_OPEN and metals.phase(t) == mhp.PHASE_CLOSED_SESSION


# --------------------------------------------------------------------------- consumed by the gap classifier
def _complete_open_history(tf, now, policy):
    now_e = int(now.timestamp()); p = gaps.PERIOD_SECONDS[tf]
    floor = now_e - gaps.RETENTION_DAYS[tf] * 86400
    return {o for o in gaps._grid_opens(tf, floor - gaps._OOR_LOOKBACK_PERIODS * p, now_e)
            if o >= floor and gaps._period_fully_open(o, tf, policy)}


def test_index_cash_expected_daily_closure_not_outage():
    idx = mhp.resolve_policy("index_cash")
    # a candle period fully inside the daily halt is NOT counted toward the open-market grid (expected closure)
    halt_open = int(datetime(2026, 7, 8, 21, 0, tzinfo=UTC).timestamp())     # 17:00 EDT M15 open, inside halt
    assert gaps._period_fully_open(halt_open, "M15", idx) is False
    # a fully-open midday period IS counted
    open_mid = int(datetime(2026, 7, 8, 12, 0, tzinfo=UTC).timestamp())
    assert gaps._period_fully_open(open_mid, "M15", idx) is True


def test_open_period_gap_still_detected_under_policy():
    idx = mhp.resolve_policy("index_cash")
    now = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)                 # Wed midday, open
    hist = _complete_open_history("H1", now, idx)
    victim = max(hist)                                            # drop the newest OPEN-market slot
    b = gaps.classify_timeframe("H1", instrument="SPX500_USD", latest=None, history_opens=hist - {victim},
                                now=now, policy=idx)
    assert b["gap_state"] == "GAPS_FOUND" and victim in b["missing_open_epochs_sample"]


def test_expected_closure_slot_missing_is_not_gap():
    energy = mhp.resolve_policy("energy")
    now = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)
    hist = _complete_open_history("H1", now, energy)             # already excludes halt-window slots
    b = gaps.classify_timeframe("H1", instrument="WTICO_USD", latest={"status": "OK", "data": {
        "timestamp_utc": gaps.cc._fmt(now - timedelta(hours=1))}}, history_opens=hist, now=now, policy=energy)
    assert b["gap_state"] != "GAPS_FOUND"                        # expected-closure (halt) slots never fabricate a gap
    assert b["missing_slots"] == 0                               # no OPEN-market slot counted missing


def test_xau_parity_preserved_under_metals_policy():
    """XAU (metals) classification during the normal open week is unchanged from the legacy calendar for the existing
    summer test instants (metals Sun-18:00/Fri-17:00 NY coincide with the legacy fixed-UTC window in EDT)."""
    metals = mhp.resolve_policy("metals")
    now = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)                # Wed midday EDT
    assert gaps.market_phase(now, metals) == "OPEN" == gaps.market_phase(now)   # policy == legacy midweek
    assert gaps.market_phase(datetime(2026, 7, 11, 12, 0, tzinfo=UTC), metals) == "CLOSED_WEEKEND"
