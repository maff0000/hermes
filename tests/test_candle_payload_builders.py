"""Tests for HERMES inert Redis-ready payload builders.
WO-HELM-HERMES-CANDLE-H4-M30-FORWARD-DERIVATION-AND-FEATURES-0001. Pure-logic; no Redis.
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
import utils.candle_payload_builders as pb  # noqa: E402

GEN = "2026-06-13T12:00:05Z"
VALID = "2026-06-13T12:05:05Z"


def _feat(complete=True, fresh="FRESH", cid=1):
    return {"candle_id": cid, "complete": complete, "freshness_state": fresh,
            "source_open_utc": "2026-06-13T08:00:00Z", "source_close_utc": "2026-06-13T12:00:00Z",
            "open": 10, "high": 15, "low": 9, "close": 14, "upper_wick_size": 1, "lower_wick_size": 1,
            "body_size": 4, "total_range": 6, "candle_direction": "BULLISH", "close_position_in_range": 0.83}


# ---------- candle_features:latest (Approach A) ----------
def test_features_latest_serves_complete():
    p = pb.build_candle_features_latest(instrument="XAU_USD", timeframe="H4",
        latest_complete_feature=_feat(True), latest_closed_feature=_feat(True),
        generated_at_utc=GEN, valid_until_utc=VALID, ttl_seconds=5400)
    assert p["key"].startswith("hermes:candle_features:latest:v1") and p["status"] == "OK"
    assert p["data"]["feature"]["complete"] is True and p["data"]["latest_closed_incomplete"] is False


def test_features_latest_incomplete_recent_degrades():
    p = pb.build_candle_features_latest(instrument="XAU_USD", timeframe="H4",
        latest_complete_feature=_feat(True, cid=1), latest_closed_feature=_feat(False, cid=2),
        generated_at_utc=GEN, valid_until_utc=VALID, ttl_seconds=5400)
    assert p["status"] == "DEGRADED" and p["data"]["latest_closed_incomplete"] is True
    assert "LATEST_CLOSED_INCOMPLETE" in p["reason_codes"]


def test_features_latest_never_serves_incomplete_as_complete():
    try:
        pb.build_candle_features_latest(instrument="XAU_USD", timeframe="H4",
            latest_complete_feature=_feat(False), latest_closed_feature=_feat(False),
            generated_at_utc=GEN, valid_until_utc=VALID, ttl_seconds=5400)
        assert False
    except ValueError as e:
        assert "GOV-PAY-004" in str(e)


# ---------- candle_context (active vs last-closed H4) ----------
def test_context_active_forming_lastclosed_complete():
    p = pb.build_candle_context_current(instrument="XAU_USD", active_h4_feature=_feat(False),
        last_closed_h4_feature=_feat(True), generated_at_utc=GEN, valid_until_utc=VALID, ttl_seconds=5400)
    assert p["data"]["active_h4_candle"]["complete_state"] == "FORMING"
    assert p["data"]["active_h4_candle"]["complete"] is False and p["data"]["active_h4_candle"]["candle_id"] is None
    assert p["data"]["last_closed_h4_candle"]["complete_state"] == "COMPLETE" and p["status"] == "OK"
    assert p["data"]["anchor"]["anchor_type"] == "UTC" and p["data"]["anchor"]["not_session_interpretive"]


def test_context_lastclosed_incomplete_degrades():
    p = pb.build_candle_context_current(instrument="XAU_USD", active_h4_feature=_feat(False),
        last_closed_h4_feature=_feat(False), generated_at_utc=GEN, valid_until_utc=VALID, ttl_seconds=5400)
    assert p["data"]["last_closed_h4_candle"]["complete_state"] == "INCOMPLETE" and p["status"] == "DEGRADED"


# ---------- indicators COMPLETE_ONLY ----------
def test_indicators_complete_only_degrades_on_incomplete():
    p = pb.build_indicators_latest(instrument="XAU_USD", timeframe="M30", source_complete=False,
        indicators={"ema_9_21": {"ema_state": "ABOVE"}}, generated_at_utc=GEN, valid_until_utc=VALID, ttl_seconds=360)
    assert p["status"] == "DEGRADED" and p["data"]["indicators"] is None
    assert p["data"]["source_complete_policy"] == "COMPLETE_ONLY"


def test_indicators_complete_serves_ema_state():
    p = pb.build_indicators_latest(instrument="XAU_USD", timeframe="M30", source_complete=True,
        indicators={"ema_9_21": {"ema_state": "CROSS_UP"}}, generated_at_utc=GEN, valid_until_utc=VALID, ttl_seconds=360)
    assert p["status"] == "OK" and p["data"]["indicators"]["ema_9_21"]["ema_state"] == "CROSS_UP"


def test_indicators_invalid_ema_state_fail_loud():
    try:
        pb.build_indicators_latest(instrument="XAU_USD", timeframe="M30", source_complete=True,
            indicators={"ema_9_21": {"ema_state": "UPTREND"}}, generated_at_utc=GEN, valid_until_utc=VALID, ttl_seconds=360)
        assert False
    except ValueError as e:
        assert "GOV-PAY-005" in str(e)


# ---------- governance: no Falcon/SOLO/NEO/Matt keys or fields ----------
def test_forbidden_key_rejected():
    for bad in ("falcon:x", "solo:x", "neo:x", "matt:x", "x:y"):
        try:
            pb.envelope("d", bad, generated_at_utc=GEN, valid_until_utc=VALID, ttl_seconds=1,
                        freshness_state="FRESH", status="OK", reason_codes=[], provenance={}, data={})
            assert False, bad
        except ValueError:
            pass


def test_forbidden_field_token_rejected():
    try:
        pb.assert_no_forbidden_fields({"data": {"regime_confidence": 1}})
        assert False
    except ValueError as e:
        assert "GOV-PAY-003" in str(e)


def test_all_keys_are_hermes_namespaced():
    p = pb.build_candle_features_latest(instrument="XAU_USD", timeframe="H4",
        latest_complete_feature=_feat(True), latest_closed_feature=_feat(True),
        generated_at_utc=GEN, valid_until_utc=VALID, ttl_seconds=5400)
    assert p["key"].startswith("hermes:") and p["service"] == "HERMES"


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
