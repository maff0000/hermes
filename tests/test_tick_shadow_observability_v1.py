"""Tests for HERMES runtime shadow tick-emit observability (counters, rate-limit, recovery probe).
WO-HELM-HERMES-REDIS-TICK-PUBLISHER-RUNTIME-SHADOW-ENABLE-DEV-0001.

In-memory strict fakes only. No network, no real Redis, no daemon.
"""
import ast
import json
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
import utils.tick_contract_v1 as tc                  # noqa: E402
import utils.tick_shadow_publisher_v1 as sh          # noqa: E402
import utils.tick_shadow_activation_v1 as act         # noqa: E402
import utils.tick_shadow_observability_v1 as obs       # noqa: E402
import utils.tick_runtime_shadow_adapter_v1 as rt      # noqa: E402

REC = datetime(2026, 6, 16, 9, 58, 0, 0, tzinfo=timezone.utc)
GEN = datetime(2026, 6, 16, 9, 58, 0, 500000, tzinfo=timezone.utc)
T0 = datetime(2026, 6, 16, 10, 0, 0, tzinfo=timezone.utc)
REGISTRY = {"XAU_USD", "EUR_USD"}
OBS_MODULE = os.path.join(ROOT, "utils", "tick_shadow_observability_v1.py")


class StrictTypeFakeRedis:
    def __init__(self):
        self.store = {}
        self.sets = []

    def set(self, key, value, ex=None):
        if isinstance(value, dict):
            raise AssertionError("real-client contract breached: received a dict")
        if not isinstance(value, (str, bytes)):
            raise AssertionError(f"value must be str/bytes, got {type(value).__name__}")
        self.store[key] = (value, ex)
        self.sets.append((key, value, ex))
        return True

    def get(self, key):
        return self.store.get(key, (None,))[0]

    def delete(self, *keys):
        n = 0
        for k in keys:
            if k in self.store:
                del self.store[k]; n += 1
        return n


class _Tick:
    def __init__(self, instrument="XAU_USD", bid=4207.015, ask=4207.265, source="oanda",
                 source_timestamp=REC):
        self.instrument = instrument
        self.bid = bid
        self.ask = ask
        self.source = source
        self.source_timestamp = source_timestamp
        self.received_at = REC


def _cfg(**over):
    base = dict(publisher_enabled=True, write_mode=rt.WRITE_MODE_SHADOW_RUNTIME_INERT,
                namespace="hermes", shadow_prefix="hermes:shadow:", redis_host="192.168.11.10",
                redis_port=6380, redis_db=0, redis_ex_seconds=10, payload_ttl_seconds=5,
                shadow_authorised=True, treat_as_production=False, dev_shadow=True)
    base.update(over)
    return rt.RuntimeShadowConfig(**base)


def _emitter(client=None):
    return rt.build_runtime_shadow_emitter(config=_cfg(), redis_client=client or StrictTypeFakeRedis())


# ---------------- counters ----------------
def test_attempt_and_success_counters():
    em = _emitter()
    em.emit_tick(_Tick(), generated_at_utc=GEN, instrument_registry=REGISTRY)
    assert em.metrics.attempted == 1 and em.metrics.succeeded == 1 and em.metrics.failed == 0
    assert em.metrics.last_success_utc is not None


def test_failure_counter_and_reason():
    em = _emitter()
    try:
        em.emit_tick(_Tick(bid=0, ask=1), generated_at_utc=GEN, instrument_registry=REGISTRY)
        assert False
    except ValueError:
        pass
    assert em.metrics.attempted == 1 and em.metrics.failed == 1 and em.metrics.succeeded == 0
    assert em.metrics.last_failure_utc is not None
    assert "GOV-TICK-CONTRACT-003" in em.metrics.last_failure_reason


def test_observed_emit_never_raises_and_counts_failure():
    em = _emitter()
    res = em.emit_tick_observed(_Tick(bid=0, ask=1))  # malformed -> fail loud internally, but no raise
    assert res["emitted"] is False and res["reason"] == obs.REASON_EMIT_FAIL
    assert em.metrics.failed == 1


def test_observed_emit_success_path():
    em = _emitter()
    res = em.emit_tick_observed(_Tick(), generated_at_utc=GEN, instrument_registry=REGISTRY)
    assert res["emitted"] is True and res["reason"] == obs.REASON_EMIT_OK
    assert em.metrics.succeeded == 1


def test_status_exposes_counters():
    em = _emitter()
    em.emit_tick(_Tick(), generated_at_utc=GEN, instrument_registry=REGISTRY)
    s = em.status()
    assert s["enabled"] is True and s["attempted"] == 1 and s["succeeded"] == 1
    assert s["last_success_utc"] is not None


# ---------------- warning rate-limit ----------------
def test_rate_limit_suppresses_but_surfaces():
    m = obs.ShadowEmitMetrics(warn_min_interval_seconds=30)
    warn0, withheld0 = m.should_emit_warning(T0)
    assert warn0 is True and withheld0 == 0                      # first warning fires
    warn1, c1 = m.should_emit_warning(T0 + timedelta(seconds=1))
    warn2, c2 = m.should_emit_warning(T0 + timedelta(seconds=2))
    assert warn1 is False and warn2 is False                    # suppressed within interval
    assert c1 == 1 and c2 == 2                                  # ...but suppressed count grows (visible)
    warn3, withheld3 = m.should_emit_warning(T0 + timedelta(seconds=31))
    assert warn3 is True and withheld3 == 2                     # next window fires + reports withheld


def test_rate_limit_does_not_hide_persistent_failure():
    # persistent failures keep incrementing the failed counter even while warnings are suppressed
    em = _emitter()
    for _ in range(5):
        em.emit_tick_observed(_Tick(bid=0, ask=1))
    assert em.metrics.failed == 5                                 # every failure counted


# ---------------- recovery probe ----------------
def test_recovery_probe_ok_and_shadow_only():
    fake = StrictTypeFakeRedis()
    em = _emitter(client=fake)
    ok, tag, detail = em.recovery_probe()
    assert ok is True and tag == obs.REASON_PROBE_OK
    # probe only ever touched a hermes:shadow:* key, and cleaned it up
    for key, _v, _ex in fake.sets:
        assert key.startswith("hermes:shadow:")
        assert not (key.startswith("hermes:ticks:") and not key.startswith("hermes:shadow:"))
    assert fake.store == {}                                       # probe key deleted


def test_recovery_probe_value_is_json_string():
    fake = StrictTypeFakeRedis()
    _emitter(client=fake).recovery_probe()
    _key, value, _ex = fake.sets[0]
    assert isinstance(value, str) and json.loads(value)["probe"] == "SHADOW_TICK_RECOVERY"


def test_recovery_probe_cannot_write_canonical_live_key():
    assert obs.RECOVERY_PROBE_KEY.startswith("hermes:shadow:")
    # assert_shadow_key would reject any canonical key the probe might be asked to use
    for live in ("hermes:ticks:XAU_USD:latest:v1", "hermes:ticks:latest:v1"):
        try:
            sh.assert_shadow_key(live); assert False
        except ValueError as e:
            assert "GOV-PUB-SHADOW-KEY-002" in str(e)


def test_recovery_probe_fail_surfaces_tag():
    class BrokenClient:
        def set(self, *a, **k):
            raise RuntimeError("route down")
    ok, tag, _detail = obs.run_recovery_probe(redis_client=BrokenClient())
    assert ok is False and tag == obs.REASON_PROBE_FAIL


def test_disabled_emitter_probe_and_status():
    cfg = _cfg(publisher_enabled=False, redis_host=None, redis_port=None, redis_db=None,
              shadow_authorised=False)
    em = rt.build_runtime_shadow_emitter(config=cfg)
    ok, tag, _ = em.recovery_probe()
    assert ok is False and tag == "SHADOW_RUNTIME_DISABLED"
    assert em.status()["enabled"] is False
    assert em.emit_tick_observed(_Tick())["emitted"] is False


# ---------------- serialisation / key invariants still hold ----------------
def test_shadow_only_json_and_seq_null():
    em = _emitter()
    res = em.emit_tick(_Tick(), generated_at_utc=GEN, instrument_registry=REGISTRY)
    stored, ex = em.writer.redis_client.store[res["key"]]
    assert res["key"].startswith("hermes:shadow:") and ex == 10
    assert isinstance(stored, str)
    env = act.deserialize_envelope(stored)
    assert tc.validate_tick_contract(env) is True and env["data"]["seq"] is None


# ---------------- reason tags + hygiene ----------------
def test_required_reason_tags_present():
    for tag in ("SHADOW_TICK_EMIT_OK", "SHADOW_TICK_EMIT_FAIL", "SHADOW_TICK_BOOT_FAIL",
                "SHADOW_TICK_RECOVERY_PROBE_OK", "SHADOW_TICK_RECOVERY_PROBE_FAIL",
                "SHADOW_TICK_RATE_LIMITED_WARNING"):
        assert tag in (obs.REASON_EMIT_OK, obs.REASON_EMIT_FAIL, obs.REASON_BOOT_FAIL,
                       obs.REASON_PROBE_OK, obs.REASON_PROBE_FAIL, obs.REASON_RATE_LIMITED)


def test_obs_module_imports_clean_and_no_forbidden_tokens():
    src = open(OBS_MODULE).read()
    tree = ast.parse(src)
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module.split(".")[0])
    assert imported <= {"__future__", "json", "datetime", "utils"}
    assert not (imported & {"redis", "mysql", "pymysql", "schedule", "crontab", "systemd"})
    forbidden = ["tradingproteus", "structure_engine", "falcon_structure", "helios_structures",
                 "choch", "bos", "order_block", "signal", "setup", "confidence", "permission",
                 "regime", "risk"]
    low = src.lower()
    assert [t for t in forbidden if t in low] == []


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn(); passed += 1; print("PASS", fn.__name__)
        except Exception:
            print("FAIL", fn.__name__); traceback.print_exc()
    print(f"{passed}/{len(fns)} passed"); raise SystemExit(0 if passed == len(fns) else 1)
