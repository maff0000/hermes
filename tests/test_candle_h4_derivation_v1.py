"""HERMES H4 derivation from H1 (NY-5PM aligned) — code-only, no Redis I/O.
WO-HELM-HERMES-GOLD-H4-DERIVATION-FROM-H1-0001.
"""
from datetime import datetime, timedelta, timezone

import utils.candle_h4_derivation_v1 as h4
import utils.candle_contract_v1 as cc
import utils.candle_history_v1 as hist

UTC = timezone.utc


def _h1(open_dt, o, hi, lo, c, v=10):
    return {"timestamp": open_dt, "open": o, "high": hi, "low": lo, "close": c, "volume": v}


def _children(h4_open, ohlcs):
    return [_h1(h4_open + timedelta(hours=i), *ohlcs[i]) for i in range(len(ohlcs))]


# ---------------- anchor alignment ----------------
def test_ny5pm_bucket_alignment():
    cases = {
        datetime(2026, 6, 1, 22, 0, tzinfo=UTC): datetime(2026, 6, 1, 22, 0, tzinfo=UTC),
        datetime(2026, 6, 1, 23, 30, tzinfo=UTC): datetime(2026, 6, 1, 22, 0, tzinfo=UTC),
        datetime(2026, 6, 2, 1, 0, tzinfo=UTC): datetime(2026, 6, 1, 22, 0, tzinfo=UTC),   # spills to prev day
        datetime(2026, 6, 2, 2, 0, tzinfo=UTC): datetime(2026, 6, 2, 2, 0, tzinfo=UTC),
        datetime(2026, 6, 2, 5, 59, tzinfo=UTC): datetime(2026, 6, 2, 2, 0, tzinfo=UTC),
        datetime(2026, 6, 2, 6, 0, tzinfo=UTC): datetime(2026, 6, 2, 6, 0, tzinfo=UTC),
        datetime(2026, 6, 2, 17, 59, tzinfo=UTC): datetime(2026, 6, 2, 14, 0, tzinfo=UTC),
        datetime(2026, 6, 2, 18, 0, tzinfo=UTC): datetime(2026, 6, 2, 18, 0, tzinfo=UTC),
    }
    for t, expect in cases.items():
        assert h4.h4_bucket_open(t) == expect, (t, h4.h4_bucket_open(t), expect)


def test_22utc_daily_boundary():
    # 21:59 -> 18:00 bucket; 22:00 -> new 22:00 bucket
    assert h4.h4_bucket_open(datetime(2026, 6, 1, 21, 59, tzinfo=UTC)) == datetime(2026, 6, 1, 18, 0, tzinfo=UTC)
    assert h4.h4_bucket_open(datetime(2026, 6, 1, 22, 0, tzinfo=UTC)) == datetime(2026, 6, 1, 22, 0, tzinfo=UTC)
    # all 4 children of the 22:00 bucket map back to it (22,23,00,01)
    bo = datetime(2026, 6, 1, 22, 0, tzinfo=UTC)
    for i in range(4):
        assert h4.h4_bucket_open(bo + timedelta(hours=i)) == bo
    # next bucket opens at 02:00
    assert h4.h4_bucket_open(bo + timedelta(hours=4)) == datetime(2026, 6, 2, 2, 0, tzinfo=UTC)


def test_bucket_opens_for_utc_day():
    day = datetime(2026, 6, 2, 0, 0, tzinfo=UTC)
    opens = h4.h4_bucket_opens_for_utc_day(day)
    assert [o.hour for o in opens] == [2, 6, 10, 14, 18, 22]


def test_h1_children_selection():
    bo = datetime(2026, 6, 2, 2, 0, tzinfo=UTC)
    cands = [_h1(datetime(2026, 6, 2, hh, 0, tzinfo=UTC), 1, 1, 1, 1) for hh in range(0, 8)]
    sel = h4.h1_children_in_bucket(bo, cands)
    assert [c["timestamp"].hour for c in sel] == [2, 3, 4, 5]      # exactly the 4 in [02,06)


# ---------------- derivation correctness ----------------
def _complete_h4(h4_open=datetime(2026, 6, 2, 2, 0, tzinfo=UTC)):
    # 4 H1 children; OHLC chosen so high=2012 (2nd), low=1990 (3rd), open=2000 (1st), close=2006 (last)
    ohlcs = [(2000.0, 2008.0, 1998.0, 2004.0), (2004.0, 2012.0, 2002.0, 2009.0),
             (2009.0, 2010.0, 1990.0, 1995.0), (1995.0, 2007.0, 1994.0, 2006.0)]
    children = _children(h4_open, ohlcs)
    gen = h4_open + timedelta(hours=4)             # close time -> status OK at its own close
    return h4.derive_h4(instrument="XAU_USD", h4_open=h4_open, h1_children=children,
                        generated_at_utc=gen, is_closed=True)


def test_complete_4of4_ohlcv_and_status_ok():
    env, meta = _complete_h4()
    assert cc.validate_candle_contract(env) is True
    d = env["data"]
    assert d["timeframe"] == "H4" and d["instrument"] == "XAU_USD"
    assert d["open"] == 2000.0 and d["close"] == 2006.0 and d["high"] == 2012.0 and d["low"] == 1990.0
    assert d["volume"] == 40                                       # 4 children x 10
    assert d["source_count"] == 4 and d["expected_source_count"] == 4 and d["source_coverage"] == 1.0
    assert env["provenance"]["derivation"] == cc.DERIVATION_DERIVED
    assert env["provenance"]["source_timeframe"] == "H1"
    assert d["derivation_policy"] == cc.DERIVATION_POLICY_H4_FROM_H1
    assert env["status"] == "OK" and d["gap_state"] == "NONE"
    assert meta["source_count"] == 4 and len(meta["child_open_epochs"]) == 4


def test_geometry_and_direction_on_derived_h4():
    d = _complete_h4()[0]["data"]
    assert d["body_high"] <= d["high"] and d["body_low"] >= d["low"]
    assert d["wick_high"] >= 0 and d["wick_low"] >= 0 and d["range_size"] >= d["body_size"]
    assert d["wick_high"] != d["high"] and d["wick_low"] != d["low"]
    assert d["candle_direction"] == "UP"                          # close 2006 > open 2000


def test_incomplete_3of4_never_ok():
    bo = datetime(2026, 6, 2, 2, 0, tzinfo=UTC)
    children = _children(bo, [(2000.0, 2008.0, 1998.0, 2004.0)] * 3)   # only 3 children
    env, meta = h4.derive_h4(instrument="XAU_USD", h4_open=bo, h1_children=children,
                             generated_at_utc=bo + timedelta(hours=4), is_closed=True)
    d = env["data"]
    assert env["status"] != "OK" and env["status"] == "SOURCE_INCOMPLETE"
    assert d["source_count"] == 3 and d["expected_source_count"] == 4 and d["source_coverage"] == 0.75
    assert d["gap_state"] in ("INCOMPLETE", "GAP_DETECTED")
    assert cc.validate_candle_contract(env) is True


def test_forming_h4_status_forming():
    bo = datetime(2026, 6, 2, 2, 0, tzinfo=UTC)
    children = _children(bo, [(2000.0, 2008.0, 1998.0, 2004.0)] * 2)   # 2 so far, still forming
    env, _ = h4.derive_h4(instrument="XAU_USD", h4_open=bo, h1_children=children,
                          generated_at_utc=bo + timedelta(hours=2), is_closed=False)
    assert env["status"] == "FORMING" and env["freshness_state"] == "FORMING"
    assert env["status"] != "OK"


def test_missing_all_children_no_source_data_no_synthesis():
    bo = datetime(2026, 6, 2, 2, 0, tzinfo=UTC)
    env, meta = h4.derive_h4(instrument="XAU_USD", h4_open=bo, h1_children=[],
                             generated_at_utc=bo + timedelta(hours=4), is_closed=True)
    assert env["status"] in ("NO_SOURCE_DATA",) and env["data"]["gap_state"] == "GAP_DETECTED"
    assert env["data"]["source_count"] == 0 and meta["source_count"] == 0   # nothing synthesised
    assert env["data"]["open"] is None                                       # no fabricated OHLC


# ---------------- guards / scope ----------------
def test_no_regime_field():
    env, _ = _complete_h4()
    assert "regime" not in str(env).lower()
    env["data"]["regime"] = "TREND"
    try:
        cc.validate_candle_contract(env); assert False
    except ValueError as e:
        assert "GOV-CANDLE-CONTRACT-029" in str(e)


def test_non_xau_and_alias_rejected():
    bo = datetime(2026, 6, 2, 2, 0, tzinfo=UTC)
    ch = _children(bo, [(2000.0, 2008.0, 1998.0, 2004.0)] * 4)
    for inst in ("XAUUSD", "EUR_USD", "XAG_USD"):
        try:
            h4.derive_h4(instrument=inst, h4_open=bo, h1_children=ch,
                         generated_at_utc=bo + timedelta(hours=4)); assert False, inst
        except ValueError as e:
            assert "GOV-CANDLE-H4-001" in str(e)


def test_history_supports_h4_rejects_d1():
    ep = int(datetime(2026, 6, 2, 2, 0, tzinfo=UTC).timestamp())
    assert hist.history_key("XAU_USD", "H4", ep) == f"hermes:candles:XAU_USD:H4:history:v1:{ep}"
    assert hist.assert_history_target(f"hermes:candles:XAU_USD:H4:history:v1:{ep}") is True
    for tf in ("D1", "D"):
        try:
            hist.history_key("XAU_USD", tf, ep); assert False
        except ValueError as e:
            assert "GOV-CANDLE-HIST-003" in str(e)


def test_latest_guard_still_protects_from_history_writer():
    # H4 added to history must NOT weaken the latest-key guard
    for tf in ("H4", "M5", "H1"):
        try:
            hist.assert_history_target(f"hermes:candles:XAU_USD:{tf}:latest:v1"); assert False
        except ValueError as e:
            assert "GOV-CANDLE-HIST-TGT-002" in str(e)


def test_h4_history_expected_opens_ny5pm_aligned():
    day = datetime(2026, 6, 2, 0, 0, tzinfo=UTC)
    opens = hist.expected_opens_for_day("H4", day)
    assert [o.hour for o in opens] == [2, 6, 10, 14, 18, 22]      # NOT midnight-aligned 0,4,8,...
