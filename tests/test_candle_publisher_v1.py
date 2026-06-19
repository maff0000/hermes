"""Tests for the HERMES governed candle publisher v1 (inert + shadow + gating + observability).
WO-HELM-HERMES-GOVERNED-CANDLE-FORWARD-CONTRACT-AND-PUBLISHER-0001.
"""
import ast
import json
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
import utils.candle_contract_v1 as cc        # noqa: E402
import utils.candle_publisher_v1 as cp        # noqa: E402

PUB_MODULE = os.path.join(ROOT, "utils", "candle_publisher_v1.py")
TS = datetime(2026, 6, 16, 8, 0, 0, tzinfo=timezone.utc)
GEN = TS + timedelta(seconds=cc.TF_SECONDS["H4"] + 0.5)


def _env(tf="H4"):
    return cc.build_derived_candle_contract(
        instrument="XAU_USD", timeframe=tf, timestamp_utc=datetime(2026, 6, 16, 8, 0, tzinfo=timezone.utc),
        source_candles=[{"open": 2000, "high": 2010, "low": 1995, "close": 2005, "volume": 10} for _ in range(4)],
        expected_source_count=4,
        generated_at_utc=datetime(2026, 6, 16, 8, 0, tzinfo=timezone.utc) + timedelta(seconds=cc.TF_SECONDS[tf] + 0.5),
        source_timeframe="H1", source_policy_epoch="epoch-2026-06")


def _shadow_cfg(**over):
    base = dict(publish_enabled=False, publish_authorised=False, shadow_publish_enabled=True,
                shadow_authorised=True, namespace="hermes", contract_version="v1",
                redis_host="192.168.0.50", redis_port=6380, redis_db=0,
                treat_as_production=False, dev_shadow=True)
    base.update(over)
    return cp.CandlePublisherConfig(**base)


class StrictFakeRedis:
    def __init__(self):
        self.store = {}

    def set(self, key, value, ex=None):
        if isinstance(value, dict):
            raise AssertionError("real client received a dict")
        if not isinstance(value, (str, bytes)):
            raise AssertionError("value must be str/bytes")
        self.store[key] = (value, ex); return True


# ---------- inert write plan ----------
def test_inert_plan_shape_canonical_key():
    plan = cp.build_inert_write_plan(_env("H4"))
    assert plan["operation"] == "SET"
    assert plan["key"] == "hermes:candles:XAU_USD:H4:latest:v1"
    assert plan["ex_seconds"] == cc.redis_ex_seconds("H4")
    assert plan["write_mode"] == "INERT_NO_WRITE"


def test_no_write_sink_captures_inert_only():
    sink = cp.NoWriteCandleSink()
    sink.publish(cp.build_inert_write_plan(_env("H1")))
    assert len(sink.captured) == 1
    try:
        sink.publish({"operation": "SET", "key": "k", "write_mode": "CANONICAL_LIVE"}); assert False
    except ValueError as e:
        assert "GOV-CANDLE-PUB-SINK-001" in str(e)


# ---------- shadow key transform + gating ----------
def test_shadow_key_transform_and_refusal():
    assert cp.to_shadow_key("hermes:candles:XAU_USD:H4:latest:v1") == "hermes:shadow:candles:XAU_USD:H4:latest:v1"
    try:
        cp.assert_shadow_key("hermes:candles:XAU_USD:H4:latest:v1"); assert False
    except ValueError as e:
        assert "GOV-CANDLE-PUB-KEY-002" in str(e)


def test_shadow_plan_rekeys_to_shadow_namespace():
    plan = cp.build_shadow_write_plan(_env("H4"))
    assert plan["key"] == "hermes:shadow:candles:XAU_USD:H4:latest:v1"
    assert plan["write_mode"] == "SHADOW_NO_LIVE"


# ---------- canonical publishing disabled by default ----------
def test_canonical_publish_disabled_by_default():
    cfg = cp.CandlePublisherConfig(publish_enabled=False, publish_authorised=False,
                                   shadow_publish_enabled=False, shadow_authorised=False,
                                   namespace="hermes", contract_version="v1")
    try:
        cfg.assert_canonical_allowed(); assert False
    except ValueError as e:
        assert "GOV-CANDLE-PUB-CANON-001" in str(e) and "PUBLISH_DISABLED" in str(e)


def test_canonical_requires_both_enabled_and_authorised():
    for en, au in ((True, False), (False, True)):
        cfg = cp.CandlePublisherConfig(publish_enabled=en, publish_authorised=au,
                                       shadow_publish_enabled=False, shadow_authorised=False,
                                       namespace="hermes", contract_version="v1")
        try:
            cfg.assert_canonical_allowed(); assert False
        except ValueError as e:
            assert "GOV-CANDLE-PUB-CANON-001" in str(e)


def test_shadow_requires_auth_and_explicit_target():
    try:
        _shadow_cfg(shadow_authorised=False).assert_shadow_allowed(); assert False
    except ValueError as e:
        assert "GOV-CANDLE-PUB-SHADOW-001" in str(e)
    try:
        _shadow_cfg(redis_host="").assert_shadow_allowed(); assert False
    except ValueError as e:
        assert "GOV-CANDLE-PUB-SHADOW-002" in str(e)
    try:
        _shadow_cfg(redis_host="127.0.0.1", treat_as_production=True, dev_shadow=False).assert_shadow_allowed(); assert False
    except ValueError as e:
        assert "GOV-CANDLE-PUB-SHADOW-003" in str(e)


# ---------- serialisation / no dict to real client ----------
def test_shadow_writer_serialises_and_never_dict():
    w = cp.SerializingCandleShadowWriter(config=_shadow_cfg(), redis_client=StrictFakeRedis())
    res = w.publish(_env("H4"))
    assert res["value_type"] == "str" and res["write_mode"] == "SHADOW_NO_LIVE"
    stored, ex = w.redis_client.store[res["key"]]
    assert isinstance(stored, str) and ex == cc.redis_ex_seconds("H4")
    assert cc.validate_candle_contract(cp.deserialize_envelope(stored)) is True


def test_shadow_writer_only_shadow_keys():
    w = cp.SerializingCandleShadowWriter(config=_shadow_cfg(), redis_client=StrictFakeRedis())
    res = w.publish(_env("D"))
    assert res["key"].startswith("hermes:shadow:candles:")
    for k in w.redis_client.store:
        assert not (k.startswith("hermes:candles:") and not k.startswith("hermes:shadow:"))


def test_shadow_writer_requires_shadow_auth():
    try:
        cp.SerializingCandleShadowWriter(config=_shadow_cfg(shadow_authorised=False),
                                         redis_client=StrictFakeRedis()); assert False
    except ValueError as e:
        assert "GOV-CANDLE-PUB-SHADOW-001" in str(e)


# ---------- observability ----------
def test_metrics_counters_and_gap_tracking():
    m = cp.CandlePublishMetrics()
    now = datetime(2026, 6, 16, 9, 0, tzinfo=timezone.utc)
    m.record_attempt(); m.record_success(now, "XAU_USD", "H4", "OK", "NONE")
    m.record_attempt(); m.record_success(now, "XAU_USD", "H4", "SOURCE_INCOMPLETE", "INCOMPLETE")
    m.record_attempt(); m.record_failure(now, "GOV-X")
    s = m.status()
    assert s["attempted"] == 3 and s["success"] == 2 and s["failure"] == 1
    assert s["incomplete_source_count"] == 1 and s["last_failure_reason"] == "GOV-X"
    assert s["last_published_instrument"] == "XAU_USD" and s["last_published_timeframe"] == "H4"


def test_warning_rate_limit():
    m = cp.CandlePublishMetrics(warn_min_interval_seconds=30)
    t = datetime(2026, 6, 16, 9, 0, tzinfo=timezone.utc)
    assert m.should_warn(t)[0] is True
    assert m.should_warn(t + timedelta(seconds=1))[0] is False
    assert m.should_warn(t + timedelta(seconds=31))[0] is True


# ---------- disabled-by-default seam ----------
def test_from_env_returns_disabled_emitter():
    em = cp.build_candle_emitter_from_env()
    assert isinstance(em, cp.DisabledCandleEmitter)
    assert em.emit(_env("H4"))["emitted"] is False
    assert em.status()["enabled"] is False


# ---------- TRIPWIRES ----------
def test_tripwire_publisher_no_cross_app_imports():
    tree = ast.parse(open(PUB_MODULE).read())
    imp = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imp.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imp.add(n.module.split(".")[0])
    assert imp <= {"__future__", "json", "datetime", "utils", "env_config"}
    assert not (imp & {"tradingProteus", "falcon", "ares", "helios", "structure_engine"})


def test_tripwire_no_signals_candle_in_emitted_plans():
    for tf in ("M5", "H1", "H4", "D"):
        for plan in (cp.build_inert_write_plan(_env(tf)), cp.build_shadow_write_plan(_env(tf))):
            assert "signals:candle" not in str(plan).lower()
            assert plan["key"].startswith("hermes:")


def test_tripwire_no_live_redis_write_in_tests():
    # the inert path uses a no-write sink; shadow path uses an in-memory fake. No real client here.
    sink = cp.NoWriteCandleSink()
    assert not any(hasattr(sink, a) for a in ("client", "connection", "redis", "socket"))


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
