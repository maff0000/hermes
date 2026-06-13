"""Tests for HERMES deterministic candle features.
WO-HELM-HERMES-CANDLE-H4-M30-FORWARD-DERIVATION-AND-FEATURES-0001. Pure-logic; no DB.
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
import utils.candle_features as cf  # noqa: E402


def test_bullish_geometry():
    g = cf.candle_geometry(10, 15, 9, 14)  # bullish: close>open, big body
    assert g["candle_direction"] == "BULLISH" and g["is_bullish"]
    assert g["body_high"] == 14 and g["body_low"] == 10
    assert g["wick_high"] == 15 and g["wick_low"] == 9
    assert g["total_range"] == 6 and g["body_size"] == 4
    assert g["upper_wick_size"] == 1 and g["lower_wick_size"] == 1
    assert g["close_position_in_range"] == round(5 / 6, 6)
    assert g["open_position_in_range"] == round(1 / 6, 6)


def test_bearish_geometry():
    g = cf.candle_geometry(14, 15, 9, 10)  # bearish
    assert g["candle_direction"] == "BEARISH" and g["is_bearish"]
    assert g["body_high"] == 14 and g["body_low"] == 10
    assert g["upper_wick_size"] == 1 and g["lower_wick_size"] == 1


def test_doji_and_zero_range_safe():
    g = cf.candle_geometry(10, 10.05, 9.95, 10.0)  # tiny body -> DOJI
    assert g["is_doji"] and g["candle_direction"] == "DOJI"
    z = cf.candle_geometry(10, 10, 10, 10)  # zero range -> ratios None, doji
    assert z["total_range"] == 0 and z["body_to_range_ratio"] is None and z["is_doji"]


def test_long_wick_and_pin_bar():
    g = cf.candle_geometry(10, 20, 9.5, 10.3)  # huge upper wick, tiny body
    assert g["is_long_upper_wick"] and g["is_pin_bar_candidate"]


def test_full_body():
    g = cf.candle_geometry(10, 14.0, 10.0, 13.8)  # body ~95% of range
    assert g["is_full_body"]


def test_previous_break_and_inside_outside():
    geo = cf.candle_geometry(10, 16, 8, 15)
    prev = cf.candle_geometry(11, 14, 9, 12)
    pf = cf.previous_candle_facts(geo, prev)
    assert pf["breaks_previous_high"] and pf["breaks_previous_low"]
    assert pf["closes_above_previous_high"] and pf["is_outside_bar_candidate"]
    inside = cf.previous_candle_facts(cf.candle_geometry(12, 13, 10, 12.5), prev)
    assert inside["is_inside_bar_candidate"] and not inside["is_outside_bar_candidate"]
    assert inside["closes_inside_previous_range"]


def test_previous_none_safe():
    pf = cf.previous_candle_facts(cf.candle_geometry(10, 12, 9, 11), None)
    assert pf["breaks_previous_high"] is None and pf["is_inside_bar_candidate"] is None


def test_swing_status_confirmed_candidate_unavailable():
    geo = cf.candle_geometry(10, 20, 5, 15)  # high=20 highest, low=5 lowest
    # both sides available -> CONFIRMED, and is the local high+low
    s = cf.swing_facts(geo, [12, 13], [8, 7], [11, 14], [6, 9], swing_lookback=2)
    assert s["swing_status"] == "CONFIRMED" and s["is_local_swing_high_candidate"] and s["is_local_swing_low_candidate"]
    # only left available -> CANDIDATE
    s2 = cf.swing_facts(geo, [12, 13], [8, 7], [], [], swing_lookback=2)
    assert s2["swing_status"] == "CANDIDATE"
    # insufficient left -> UNAVAILABLE
    s3 = cf.swing_facts(geo, [12], [8], [11], [6], swing_lookback=2)
    assert s3["swing_status"] == "UNAVAILABLE" and s3["is_local_swing_high_candidate"] is None


def test_malformed_fail_loud():
    for args in [(None, 1, 1, 1), (10, 8, 9, 9)]:  # None, and high<low
        try:
            cf.candle_geometry(*args); assert False
        except ValueError as e:
            assert "GOV-FEAT-00" in str(e)


def test_completeness_block_fail_closed():
    c = cf.completeness_block("COMPLETE", 30, 30)
    assert c["complete"] is True and c["status"] == "OK" and c["missing_source_candle_count"] == 0
    inc = cf.completeness_block("INCOMPLETE", 30, 28)
    assert inc["complete"] is False and inc["missing_source_candle_count"] == 2 and "SOURCE_CANDLE_INCOMPLETE" in inc["reason_codes"]
    fm = cf.completeness_block("FORMING", 30, 12)
    assert fm["complete"] is False and "SOURCE_CANDLE_FORMING" in fm["reason_codes"]
    un = cf.completeness_block("UNAVAILABLE", 30, 0)
    assert un["status"] == "UNAVAILABLE"


def test_build_candle_feature_full_complete():
    f = cf.build_candle_feature(instrument="XAU_USD", timeframe="H4", candle_id=99,
                                source_table="candles_H4", source_candle_id=99,
                                source_open_utc="2026-06-13T08:00:00Z", source_close_utc="2026-06-13T12:00:00Z",
                                generated_at_utc="2026-06-13T12:00:05Z", open_=10, high=15, low=9, close=14,
                                complete_state="COMPLETE", expected=240, actual=240, freshness_state="FRESH")
    assert f["complete"] is True and f["complete_state"] == "COMPLETE"
    assert f["anchor_type"] == "UTC" and f["anchor_status"] == "RATIFIED_FOR_HERMES_V1" and f["not_session_interpretive"]
    assert f["candle_direction"] == "BULLISH" and f["body_high"] == 14 and f["schema_version"] == "v1"


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = 0
    for fn in fns:
        try:
            fn(); p += 1; print("PASS", fn.__name__)
        except Exception:
            print("FAIL", fn.__name__); traceback.print_exc()
    print(f"{p}/{len(fns)} passed"); raise SystemExit(0 if p == len(fns) else 1)
