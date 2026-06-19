"""Tests for the HERMES governed candle contract v1.
WO-HELM-HERMES-GOVERNED-CANDLE-FORWARD-CONTRACT-AND-PUBLISHER-0001.
"""
import ast
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
import utils.candle_contract_v1 as cc  # noqa: E402

CONTRACT_MODULE = os.path.join(ROOT, "utils", "candle_contract_v1.py")
PUB_MODULE = os.path.join(ROOT, "utils", "candle_publisher_v1.py")


def _ts(tf="H4"):
    return datetime(2026, 6, 16, 8, 0, 0, tzinfo=timezone.utc)


def _gen(tf="H4", offset_s=0.5):
    # generated shortly after the candle close (timestamp + tf_seconds)
    return _ts(tf) + timedelta(seconds=cc.TF_SECONDS[tf] + offset_s)


def _src(n, base=2000.0):
    return [{"open": base + i, "high": base + i + 2, "low": base + i - 1, "close": base + i + 1,
             "volume": 10} for i in range(n)]


def _direct(tf="H1", **over):
    kw = dict(instrument="XAU_USD", timeframe=tf, timestamp_utc=_ts(tf),
              ohlc={"open": 2000, "high": 2010, "low": 1995, "close": 2005, "volume": 120},
              is_closed=True, generated_at_utc=_gen(tf), source_timeframe="M1",
              source_count=cc.TF_SECONDS[tf] // 60, expected_source_count=cc.TF_SECONDS[tf] // 60,
              derivation=cc.DERIVATION_DIRECT, derivation_policy=cc.DERIVATION_POLICY_FORWARD,
              source_policy_epoch="epoch-2026-06")
    kw.update(over)
    return cc.build_candle_contract(**kw)


# ---------- keys / ttl ----------
def test_canonical_and_shadow_keys():
    assert cc.canonical_key("XAU_USD", "H4") == "hermes:candles:XAU_USD:H4:latest:v1"
    assert cc.shadow_key("XAU_USD", "H4") == "hermes:shadow:candles:XAU_USD:H4:latest:v1"


def test_bad_timeframe_fails_loud():
    for bad in ("M1", "M30", "W"):
        try:
            cc.canonical_key("XAU_USD", bad); assert False
        except ValueError as e:
            assert "GOV-CANDLE-CONTRACT-001" in str(e)


def test_ttl_policy_per_timeframe():
    assert cc.TF_SECONDS == {"M5": 300, "H1": 3600, "H4": 14400, "D": 86400}
    assert cc.redis_ex_seconds("M5") == 360 and cc.redis_ex_seconds("D") == 90000


# ---------- builds for all timeframes ----------
def test_builds_all_timeframes_validate():
    for tf in ("M5", "H1", "H4", "D"):
        p = _direct(tf)
        assert cc.validate_candle_contract(p) is True
        assert p["ttl_seconds"] == cc.TF_SECONDS[tf]
        assert p["key"] == cc.canonical_key("XAU_USD", tf)


def test_valid_until_equals_generated_plus_ttl():
    p = _direct("H4")
    g = datetime.strptime(p["generated_at_utc"][:-1], cc._UTC_MS)
    v = datetime.strptime(p["valid_until_utc"][:-1], cc._UTC_MS)
    assert abs((v - g).total_seconds() - cc.TF_SECONDS["H4"]) < 0.001


# ---------- schema / missing fields / malformed ----------
def test_missing_envelope_field_fails():
    p = _direct(); del p["provenance"]
    try:
        cc.validate_candle_contract(p); assert False
    except ValueError as e:
        assert "GOV-CANDLE-CONTRACT-010" in str(e)


def test_missing_data_field_fails():
    p = _direct(); del p["data"]["gap_state"]
    try:
        cc.validate_candle_contract(p); assert False
    except ValueError as e:
        assert "GOV-CANDLE-CONTRACT-012" in str(e)


def test_malformed_timestamp_rejected():
    p = _direct(); p["generated_at_utc"] = "2026-06-16T08:00:00Z"  # no ms
    try:
        cc.validate_candle_contract(p); assert False
    except ValueError as e:
        assert "GOV-CANDLE-CONTRACT-017" in str(e)


# ---------- forming vs closed ----------
def test_forming_candle_state():
    p = _direct("H1", is_closed=False)
    assert p["status"] == "FORMING" and p["freshness_state"] == "FORMING"
    assert "FORMING" in p["reason_codes"]
    assert cc.validate_candle_contract(p) is True


def test_stale_candle_state():
    p = _direct("H1", generated_at_utc=_ts("H1") + timedelta(seconds=cc.TF_SECONDS["H1"] * 3))
    assert p["status"] == "STALE" and p["freshness_state"] == "STALE"


# ---------- derivation: complete vs incomplete ----------
def test_h4_derived_complete_is_ok():
    p = cc.build_derived_candle_contract(
        instrument="XAU_USD", timeframe="H4", timestamp_utc=_ts("H4"),
        source_candles=_src(4), expected_source_count=4, generated_at_utc=_gen("H4"),
        source_timeframe="H1", source_policy_epoch="epoch-2026-06")
    assert p["status"] == "OK" and p["data"]["gap_state"] == "NONE"
    assert p["data"]["source_coverage"] == 1.0
    assert p["provenance"]["derivation"] == cc.DERIVATION_DERIVED
    assert "DERIVED_FROM_LOWER_TIMEFRAME" in p["reason_codes"]
    assert cc.validate_candle_contract(p) is True


def test_h4_derived_incomplete_is_never_ok():
    p = cc.build_derived_candle_contract(
        instrument="XAU_USD", timeframe="H4", timestamp_utc=_ts("H4"),
        source_candles=_src(3), expected_source_count=4, generated_at_utc=_gen("H4"),
        source_timeframe="H1", source_policy_epoch="epoch-2026-06")
    assert p["status"] == "SOURCE_INCOMPLETE"
    assert p["data"]["gap_state"] == "INCOMPLETE"
    assert p["data"]["source_coverage"] == 0.75
    assert cc.validate_candle_contract(p) is True


def test_validator_rejects_incomplete_marked_ok():
    p = cc.build_derived_candle_contract(
        instrument="XAU_USD", timeframe="H4", timestamp_utc=_ts("H4"),
        source_candles=_src(3), expected_source_count=4, generated_at_utc=_gen("H4"),
        source_timeframe="H1", source_policy_epoch="epoch-2026-06")
    p["status"] = "OK"   # tamper: pretend incomplete is clean
    try:
        cc.validate_candle_contract(p); assert False
    except ValueError as e:
        assert "GOV-CANDLE-CONTRACT-023" in str(e)


def test_d_candle_direct_and_derived():
    direct = _direct("D")
    assert direct["provenance"]["derivation"] == cc.DERIVATION_DIRECT
    derived = cc.build_derived_candle_contract(
        instrument="XAU_USD", timeframe="D", timestamp_utc=_ts("D"),
        source_candles=_src(6), expected_source_count=6, generated_at_utc=_gen("D"),
        source_timeframe="H4", source_policy_epoch="epoch-2026-06")
    assert derived["status"] == "OK" and derived["provenance"]["derivation"] == cc.DERIVATION_DERIVED


def test_no_source_data_unavailable():
    p = cc.build_derived_candle_contract(
        instrument="XAU_USD", timeframe="H4", timestamp_utc=_ts("H4"),
        source_candles=[], expected_source_count=4, generated_at_utc=_gen("H4"),
        source_timeframe="H1", source_policy_epoch="epoch-2026-06")
    assert p["status"] == "NO_SOURCE_DATA" and p["freshness_state"] == "UNAVAILABLE"
    assert p["data"]["open"] is None
    assert cc.validate_candle_contract(p) is True


def test_market_closed_state():
    p = _direct("H1", market_open=False)
    assert p["status"] == "MARKET_CLOSED" and p["freshness_state"] == "UNAVAILABLE"


# ---------- ohlc sanity ----------
def test_ohlc_sanity_enforced():
    try:
        _direct("H1", ohlc={"open": 2000, "high": 1990, "low": 1995, "close": 2005, "volume": 1})
        assert False
    except ValueError as e:
        assert "GOV-CANDLE-CONTRACT-006" in str(e)


def test_aggregate_ohlc_deterministic():
    agg = cc.aggregate_ohlc(_src(4, base=100.0))
    assert agg["open"] == 100.0 and agg["close"] == 100.0 + 3 + 1
    assert agg["high"] == max(100.0 + i + 2 for i in range(4))
    assert agg["volume"] == 40


# ---------- ownership / boundary ----------
def test_publisher_is_hermes_only():
    p = _direct()
    assert p["service"] == "HERMES" and p["provenance"]["publisher"] == "HERMES"
    assert p["data"]["publisher"] == "HERMES"


def test_no_interpretive_fields_rejected():
    p = _direct(); p["data"]["regime_label"] = "x"
    try:
        cc.validate_candle_contract(p); assert False
    except ValueError as e:
        assert "GOV-CANDLE-CONTRACT-029" in str(e)


def test_rejects_legacy_signals_candle_or_proteus_leak():
    p = _direct(); p["provenance"]["legacy_ref"] = "signals:candle:H1:XAU_USD:latest"
    try:
        cc.validate_candle_contract(p); assert False
    except ValueError as e:
        assert "GOV-CANDLE-CONTRACT-028" in str(e)


# ---------- TRIPWIRES (mandatory) ----------
def _imports(path):
    tree = ast.parse(open(path).read())
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module.split(".")[0])
    return out


def test_tripwire_no_tradingproteus_import():
    for m in (CONTRACT_MODULE, PUB_MODULE):
        imp = _imports(m)
        assert "tradingProteus" not in imp and "tradingproteus" not in imp
        assert not (imp & {"falcon", "ares", "helios", "structure_engine", "solo", "neo"})


def test_tripwire_no_signals_candle_emitted_as_output():
    # the governed builders must never emit a signals:candle key/value as output
    for tf in ("M5", "H1", "H4", "D"):
        p = _direct(tf)
        blob = str(p).lower()
        assert "signals:candle" not in blob
        assert p["key"].startswith("hermes:candles:")


def test_tripwire_required_fields_present_in_output():
    p = _direct("H4")
    for f in ("source_coverage", "source_count", "expected_source_count", "gap_state",
              "derivation_policy", "source_policy_epoch"):
        assert f in p["data"]
    assert "derivation" in p["provenance"] and "source_policy_epoch" in p["provenance"]


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
