"""Tests for the UTC-naive timestamp fix (candle contract + shadow seam) + failure visibility.
WO-HELM-HERMES-CANDLE-CONTRACT-UTC-NAIVE-TIMESTAMP-FIX-0001.

In-memory fakes only. No Redis/SQL writes. Scope unchanged (M5/H1 only; M1/M15/D1/D/H4 skipped).
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from utils import candle_contract_v1 as cc
from utils import candle_publisher_v1 as cp
from utils import candle_runtime_seam_v1 as seam


def _gen(tf, naive=False, offset_s=2.0):
    g = datetime(2026, 6, 16, 8, 0) + timedelta(seconds=cc.TF_SECONDS[tf] + offset_s)
    return g if naive else g.replace(tzinfo=timezone.utc)


def _ts(naive=False):
    t = datetime(2026, 6, 16, 8, 0)
    return t if naive else t.replace(tzinfo=timezone.utc)


def _contract(timeframe="M5", ts_naive=False, gen_naive=False, **over):
    kw = dict(instrument="EUR_USD", timeframe=timeframe, timestamp_utc=_ts(ts_naive),
              ohlc={"open": 1.10, "high": 1.101, "low": 1.099, "close": 1.1005, "volume": 120},
              is_closed=True, generated_at_utc=_gen(timeframe, gen_naive), source_timeframe=timeframe,
              source_count=1, expected_source_count=1, derivation=cc.DERIVATION_DIRECT,
              derivation_policy=cc.DERIVATION_POLICY_DIRECT, source_policy_epoch="DIRECT_NATIVE_V1")
    kw.update(over)
    return cc.build_candle_contract(**kw)


# ---------------- normalise_utc helper ----------------
def test_normalise_utc_naive_assumed_utc():
    naive = datetime(2026, 6, 16, 8, 0)
    out = cc.normalise_utc(naive)
    assert out.tzinfo is not None and out.utcoffset() == timedelta(0)
    assert out.hour == 8  # NOT shifted to a local zone


def test_normalise_utc_aware_converted():
    aware = datetime(2026, 6, 16, 8, 0, tzinfo=timezone(timedelta(hours=5)))
    out = cc.normalise_utc(aware)
    assert out.utcoffset() == timedelta(0) and out.hour == 3  # 08:00+05 -> 03:00Z


# ---------------- the bug: naive ts + aware gen ----------------
def test_naive_timestamp_aware_generated_does_not_raise():
    p = _contract("M5", ts_naive=True, gen_naive=False)   # the exact prod failure shape
    assert cc.validate_candle_contract(p) is True
    assert p["status"] == "OK"


def test_aware_timestamp_still_works():
    assert cc.validate_candle_contract(_contract("H1", ts_naive=False, gen_naive=False)) is True


def test_naive_generated_normalised():
    assert cc.validate_candle_contract(_contract("M5", ts_naive=False, gen_naive=True)) is True


def test_both_naive_works():
    assert cc.validate_candle_contract(_contract("H1", ts_naive=True, gen_naive=True)) is True


def test_output_timestamps_are_utc_z():
    p = _contract("M5", ts_naive=True, gen_naive=True)
    for ts in (p["generated_at_utc"], p["valid_until_utc"], p["data"]["timestamp_utc"]):
        assert ts.endswith("Z") and cc._is_utc_ms(ts)


def test_freshness_fresh_and_stale_with_naive():
    fresh = _contract("M5", ts_naive=True, gen_naive=True)             # gen = close + 2s
    assert fresh["freshness_state"] == "FRESH" and fresh["status"] == "OK"
    stale = _contract("M5", ts_naive=True,
                      generated_at_utc=datetime(2026, 6, 16, 8, 0) + timedelta(seconds=cc.TF_SECONDS["M5"] * 3))
    assert stale["freshness_state"] == "STALE" and stale["status"] == "STALE"


def test_forming_never_ok_with_naive():
    p = _contract("M5", ts_naive=True, is_closed=False)
    assert p["status"] == "FORMING" and p["status"] != "OK"


def test_ohlc_sanity_still_enforced():
    try:
        _contract("M5", ts_naive=True, ohlc={"open": 1.1, "high": 1.0, "low": 1.05, "close": 1.1, "volume": 1})
        assert False
    except ValueError as e:
        assert "GOV-CANDLE-CONTRACT-006" in str(e)


# ---------------- runtime seam: naive runtime candle ----------------
class _TF:
    def __init__(s, n): s.name = n


class _Candle:
    def __init__(s, tf="M5", complete=True, ts=None):
        s.instrument = "EUR_USD"; s.timeframe = _TF(tf)
        s.timestamp = ts if ts is not None else datetime(2026, 6, 16, 8, 0)  # NAIVE by default
        s.open = 1.10; s.high = 1.101; s.low = 1.099; s.close = 1.1005; s.volume = 120; s.complete = complete


class StrictFakeRedis:
    def __init__(s): s.store = {}; s.sets = []
    def set(s, k, v, ex=None):
        assert isinstance(v, (str, bytes)) and not isinstance(v, dict)
        s.store[k] = (v, ex); s.sets.append((k, v, ex)); return True


def _shadow(client=None):
    cfg = cp.CandlePublisherConfig(publish_enabled=False, publish_authorised=False,
                                   shadow_publish_enabled=True, shadow_authorised=True, namespace="hermes",
                                   contract_version="v1", redis_host="192.168.11.10", redis_port=6380,
                                   redis_db=0, treat_as_production=False, dev_shadow=True)
    return seam.build_shadow_seam(config=cfg, redis_client=client or StrictFakeRedis())


def test_naive_m5_runtime_candle_emits_valid_shadow():
    sh = _shadow()
    gen = datetime(2026, 6, 16, 8, 0, tzinfo=timezone.utc) + timedelta(seconds=cc.TF_SECONDS["M5"] + 2)
    res = sh.emit(candle=_Candle("M5"), generated_at_utc=gen)
    assert res["emitted"] is True and res["wrote"] is True
    assert res["key"] == "hermes:shadow:candles:EUR_USD:M5:latest:v1"
    env = json.loads(sh.writer.redis_client.store[res["key"]][0])
    assert cc.validate_candle_contract(env) is True
    d = env["data"]
    assert d["source_count"] == 1 and d["expected_source_count"] == 1 and d["source_coverage"] == 1.0
    assert env["provenance"]["derivation"] == cc.DERIVATION_DIRECT and d["derivation_policy"] == cc.DERIVATION_POLICY_DIRECT


def test_naive_h1_runtime_candle_emits_valid_shadow():
    sh = _shadow()
    gen = datetime(2026, 6, 16, 8, 0, tzinfo=timezone.utc) + timedelta(seconds=cc.TF_SECONDS["H1"] + 2)
    res = sh.emit(candle=_Candle("H1"), generated_at_utc=gen)
    assert res["key"] == "hermes:shadow:candles:EUR_USD:H1:latest:v1"
    assert cc.validate_candle_contract(json.loads(sh.writer.redis_client.store[res["key"]][0])) is True


def test_unsupported_timeframes_still_skip_with_naive():
    sh = _shadow()
    for tf in ("M1", "M15", "D1", "D", "H4"):
        r = sh.emit(candle=_Candle(tf), generated_at_utc=datetime(2026, 6, 16, 8, 0, tzinfo=timezone.utc))
        assert r["emitted"] is False and r["reason"] == "UNSUPPORTED_TIMEFRAME"
    assert sh.writer.redis_client.sets == []


def test_no_canonical_key_emitted():
    sh = _shadow()
    sh.emit(candle=_Candle("M5"), generated_at_utc=datetime(2026, 6, 16, 8, 0, tzinfo=timezone.utc) + timedelta(seconds=302))
    for k in sh.writer.redis_client.store:
        assert k.startswith("hermes:shadow:candles:")


# ---------------- failure visibility / logging ----------------
def test_validate_fail_is_logged(caplog):
    caplog.set_level(logging.WARNING, logger="hermes.candle_forward")
    sh = _shadow()
    # bad OHLC -> build/validate raises -> CANDLE_VALIDATE_FAIL
    bad = _Candle("M5"); bad.high = 1.0; bad.low = 1.05
    res = sh.emit(candle=bad, generated_at_utc=datetime(2026, 6, 16, 8, 0, tzinfo=timezone.utc) + timedelta(seconds=302))
    assert res["reason"] == "CANDLE_VALIDATE_FAIL"
    assert sh.metrics["candle_validate_fail"] == 1
    assert any("CANDLE_VALIDATE_FAIL" in r.message for r in caplog.records)
    assert sh.writer.redis_client.sets == []   # no write on failure


def test_emit_fail_is_logged(caplog):
    caplog.set_level(logging.WARNING, logger="hermes.candle_forward")

    class BrokenClient:
        def set(self, *a, **k): raise RuntimeError("redis down")
    sh = _shadow(client=BrokenClient())
    res = sh.emit(candle=_Candle("M5"), generated_at_utc=datetime(2026, 6, 16, 8, 0, tzinfo=timezone.utc) + timedelta(seconds=302))
    assert res["reason"] == "CANDLE_EMIT_FAIL"
    assert sh.metrics["candle_emit_fail"] == 1
    assert any("CANDLE_EMIT_FAIL" in r.message for r in caplog.records)


def test_fail_log_rate_limited(caplog):
    caplog.set_level(logging.WARNING, logger="hermes.candle_forward")
    sh = _shadow()
    bad = _Candle("M5"); bad.high = 1.0; bad.low = 1.05
    for _ in range(5):
        sh.emit(candle=bad, generated_at_utc=datetime(2026, 6, 16, 8, 0, tzinfo=timezone.utc) + timedelta(seconds=302))
    # rate-limited: 1st logs, next 4 suppressed -> exactly 1 log line, but counter=5
    assert sh.metrics["candle_validate_fail"] == 5
    assert sum("CANDLE_VALIDATE_FAIL" in r.message for r in caplog.records) == 1


if __name__ == "__main__":
    import traceback, inspect
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = 0
    for fn in fns:
        if "caplog" in inspect.signature(fn).parameters:
            continue
        try:
            fn(); p += 1; print("PASS", fn.__name__)
        except Exception:
            print("FAIL", fn.__name__); traceback.print_exc()
    print(f"{p} non-caplog passed")
