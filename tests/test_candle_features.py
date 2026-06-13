"""Tests for HERMES deterministic candle features + GOVERNED classification config.
WO-HELM-HERMES-CANDLE-H4-M30-FORWARD-DERIVATION-AND-FEATURES-0001. Pure-logic; no DB (config injected).
"""
import contextlib
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
import utils.candle_features as cf  # noqa: E402

# explicit governed config (no hidden defaults) — fixtures supply config, never module constants
CFG = cf.CandleFeatureConfig(doji_body_to_range_max=0.10, full_body_min_body_to_range=0.80,
                             long_wick_ratio_min=0.50, pin_bar_wick_to_body_min=2.0,
                             pin_bar_body_to_range_max=0.35, swing_lookback=2, config_id=1).validate()


# ---------- geometry is pure/config-free ----------
def test_bullish_geometry_pure():
    g = cf.candle_geometry(10, 15, 9, 14)
    assert g["body_high"] == 14 and g["body_low"] == 10
    assert g["wick_high"] == 15 and g["wick_low"] == 9
    assert g["total_range"] == 6 and g["body_size"] == 4
    assert g["upper_wick_size"] == 1 and g["lower_wick_size"] == 1
    assert g["close_position_in_range"] == round(5 / 6, 6)
    assert "candle_direction" not in g  # classification is config-governed, not in pure geometry


def test_zero_range_safe():
    z = cf.candle_geometry(10, 10, 10, 10)
    assert z["total_range"] == 0 and z["body_to_range_ratio"] is None


def test_malformed_fail_loud():
    for args in [(None, 1, 1, 1), (10, 8, 9, 9)]:
        try:
            cf.candle_geometry(*args); assert False
        except ValueError as e:
            assert "GOV-FEAT-00" in str(e)


# ---------- classification is GOVERNED by config ----------
def test_classify_requires_config():
    try:
        cf.classify(cf.candle_geometry(10, 15, 9, 14), None); assert False
    except ValueError as e:
        assert "GOV-FEAT-CFG-001" in str(e)


def test_classify_bullish_bearish_doji():
    assert cf.classify(cf.candle_geometry(10, 15, 9, 14), CFG)["candle_direction"] == "BULLISH"
    assert cf.classify(cf.candle_geometry(14, 15, 9, 10), CFG)["candle_direction"] == "BEARISH"
    assert cf.classify(cf.candle_geometry(10, 10.05, 9.95, 10.0), CFG)["is_doji"]


def test_explicit_config_changes_outcome_deterministically():
    geo = cf.candle_geometry(10, 12, 9.5, 10.4)  # body 0.4 / range 2.5 = 0.16
    strict = cf.CandleFeatureConfig(0.20, 0.80, 0.50, 2.0, 0.35, 2).validate()   # doji_max 0.20 -> DOJI
    loose = cf.CandleFeatureConfig(0.10, 0.80, 0.50, 2.0, 0.35, 2).validate()    # doji_max 0.10 -> BULLISH
    assert cf.classify(geo, strict)["candle_direction"] == "DOJI"
    assert cf.classify(geo, loose)["candle_direction"] == "BULLISH"


def test_classification_provenance_present():
    c = cf.classify(cf.candle_geometry(10, 15, 9, 14), CFG)
    assert c["classification_config"]["config_key"] == "candle_feature_classification:v1"
    assert c["classification_config"]["config_id"] == 1


# ---------- config validation fail-loud ----------
def test_config_missing_keys_fail_loud():
    try:
        cf.config_from_json('{"doji_body_to_range_max": 0.1}'); assert False
    except ValueError as e:
        assert "GOV-FEAT-CFG-002" in str(e)


def test_config_malformed_json_fail_loud():
    try:
        cf.config_from_json("{not json"); assert False
    except ValueError as e:
        assert "GOV-FEAT-CFG-002" in str(e)


def test_config_out_of_range_fail_loud():
    try:
        cf.CandleFeatureConfig(1.5, 0.8, 0.5, 2.0, 0.35, 2).validate(); assert False
    except ValueError as e:
        assert "GOV-FEAT-CFG-003" in str(e)


def test_config_invalid_swing_lookback_fail_loud():
    for bad in (0, -1, 2.5):
        try:
            cf.CandleFeatureConfig(0.1, 0.8, 0.5, 2.0, 0.35, bad).validate(); assert False
        except ValueError as e:
            assert "GOV-FEAT-CFG-004" in str(e)


def test_no_module_constants_on_live_path():
    # the old hidden thresholds must NOT exist as module constants
    for name in ("DOJI_BODY_TO_RANGE_MAX", "FULL_BODY_MIN", "LONG_WICK_RATIO_MIN",
                 "PIN_BAR_WICK_TO_BODY_MIN", "PIN_BAR_BODY_TO_RANGE_MAX"):
        assert not hasattr(cf, name), f"hidden module constant {name} must be removed"


def test_load_config_fail_loud_when_absent():
    class _Cur:
        def execute(self, *a, **k): pass
        def fetchone(self): return None
        def __enter__(self): return self
        def __exit__(self, *a): return False
    class _Conn:
        def cursor(self): return _Cur()
        def __enter__(self): return self
        def __exit__(self, *a): return False
    @contextlib.contextmanager
    def gc():
        yield _Conn()
    try:
        cf.load_config(gc); assert False
    except ValueError as e:
        assert "GOV-FEAT-CFG-001" in str(e)


# ---------- previous + swing ----------
def test_previous_break_inside_outside():
    geo = cf.candle_geometry(10, 16, 8, 15)
    prev = cf.candle_geometry(11, 14, 9, 12)
    pf = cf.previous_candle_facts(geo, prev)
    assert pf["breaks_previous_high"] and pf["breaks_previous_low"] and pf["is_outside_bar_candidate"]
    inside = cf.previous_candle_facts(cf.candle_geometry(12, 13, 10, 12.5), prev)
    assert inside["is_inside_bar_candidate"] and inside["closes_inside_previous_range"]


def test_swing_status_and_lookback_governed():
    geo = cf.candle_geometry(10, 20, 5, 15)
    assert cf.swing_facts(geo, [12, 13], [8, 7], [11, 14], [6, 9], CFG.swing_lookback)["swing_status"] == "CONFIRMED"
    assert cf.swing_facts(geo, [12, 13], [8, 7], [], [], CFG.swing_lookback)["swing_status"] == "CANDIDATE"
    assert cf.swing_facts(geo, [12], [8], [11], [6], CFG.swing_lookback)["swing_status"] == "UNAVAILABLE"


# ---------- full build ----------
def test_build_feature_requires_config():
    try:
        cf.build_candle_feature(instrument="X", timeframe="H4", candle_id=1, source_table="candles_H4",
            source_candle_id=1, source_open_utc="a", source_close_utc="b", generated_at_utc="c",
            open_=10, high=15, low=9, close=14, complete_state="COMPLETE", expected=240, actual=240,
            freshness_state="FRESH", config=None); assert False
    except ValueError as e:
        assert "GOV-FEAT-CFG-001" in str(e)


def test_build_feature_full_complete_with_config_and_provenance():
    f = cf.build_candle_feature(instrument="XAU_USD", timeframe="H4", candle_id=99,
        source_table="candles_H4", source_candle_id=99, source_open_utc="2026-06-13T08:00:00Z",
        source_close_utc="2026-06-13T12:00:00Z", generated_at_utc="2026-06-13T12:00:05Z",
        open_=10, high=15, low=9, close=14, complete_state="COMPLETE", expected=240, actual=240,
        freshness_state="FRESH", config=CFG, swing_neighbours=([12, 13], [8, 7], [11, 14], [6, 9]))
    assert f["complete"] is True and f["candle_direction"] == "BULLISH"
    assert f["classification_config"]["config_id"] == 1 and f["anchor_status"] == "RATIFIED_FOR_HERMES_V1"
    assert f["swing_lookback"] == 2 and f["swing_status"] == "CONFIRMED"


def test_completeness_block_fail_closed():
    assert cf.completeness_block("COMPLETE", 30, 30)["complete"] is True
    inc = cf.completeness_block("INCOMPLETE", 30, 28)
    assert inc["complete"] is False and inc["missing_source_candle_count"] == 2


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
