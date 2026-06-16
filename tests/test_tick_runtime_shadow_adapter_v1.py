"""Tests for the HERMES runtime tick-path SHADOW emit boundary (INERT / disabled-by-default).
WO-HELM-HERMES-REDIS-TICK-PUBLISHER-RUNTIME-INTEGRATE-INERT-0001.

In-memory strict fakes only. No network, no proteus-redis, no dev/prod Redis, no daemon.
"""
import ast
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
import utils.tick_contract_v1 as tc                  # noqa: E402
import utils.tick_shadow_publisher_v1 as sh          # noqa: E402
import utils.tick_shadow_activation_v1 as act         # noqa: E402
import utils.tick_runtime_shadow_adapter_v1 as rt      # noqa: E402

REC = datetime(2026, 6, 16, 9, 58, 0, 0, tzinfo=timezone.utc)
GEN = datetime(2026, 6, 16, 9, 58, 0, 500000, tzinfo=timezone.utc)
REGISTRY = {"XAU_USD", "EUR_USD"}
ADAPTER_MODULE = os.path.join(ROOT, "utils", "tick_runtime_shadow_adapter_v1.py")
SHADOW_MODULE = os.path.join(ROOT, "utils", "tick_shadow_publisher_v1.py")


class StrictTypeFakeRedis:
    """In-memory fake; FAILS LOUD if handed a Python dict. No network."""

    def __init__(self):
        self.store = {}

    def set(self, key, value, ex=None):
        if isinstance(value, dict):
            raise AssertionError("real-client contract breached: received a dict")
        if not isinstance(value, (str, bytes)):
            raise AssertionError(f"value must be str/bytes, got {type(value).__name__}")
        self.store[key] = (value, ex)
        return True


class RealishRedisClient:
    """Looks like a real network Redis client (has connection_pool) — must be refused by the
    legacy dict-path ShadowTickPublisher."""

    def __init__(self):
        self.connection_pool = object()

    def set(self, key, value, ex=None):
        return True


class _Tick:
    """Duck-typed runtime tick fact (mirrors the runtime tick object's market fields)."""

    def __init__(self, instrument="XAU_USD", bid=4207.015, ask=4207.265, source="oanda",
                 source_timestamp=REC):
        self.instrument = instrument
        self.bid = bid
        self.ask = ask
        self.source = source
        self.source_timestamp = source_timestamp
        self.received_at = REC


def _runtime_cfg(**over):
    base = dict(publisher_enabled=True, write_mode=rt.WRITE_MODE_SHADOW_RUNTIME_INERT,
                namespace="hermes", shadow_prefix="hermes:shadow:", redis_host="192.168.0.50",
                redis_port=6380, redis_db=0, redis_ex_seconds=10, payload_ttl_seconds=5,
                shadow_authorised=True, treat_as_production=False, dev_shadow=True)
    base.update(over)
    return rt.RuntimeShadowConfig(**base)


def _enabled_emitter(client=None):
    return rt.build_runtime_shadow_emitter(config=_runtime_cfg(),
                                           redis_client=client or StrictTypeFakeRedis())


# ---------------- disabled / enabled states ----------------
def test_disabled_state_is_explicit_noop():
    cfg = _runtime_cfg(publisher_enabled=False, redis_host=None, redis_port=None, redis_db=None,
                       shadow_authorised=False)
    em = rt.build_runtime_shadow_emitter(config=cfg)
    assert isinstance(em, rt.DisabledShadowEmitter)
    res = em.emit_tick(_Tick())
    assert res["emitted"] is False and res["reason"] == "SHADOW_RUNTIME_DISABLED"


def test_enabled_requires_client_or_factory():
    try:
        rt.build_runtime_shadow_emitter(config=_runtime_cfg())  # enabled, no client/factory
        assert False
    except ValueError as e:
        assert "GOV-PUB-RT-ADP-001" in str(e)


def test_enabled_emits_through_serialising_writer():
    em = _enabled_emitter()
    assert isinstance(em, rt.RuntimeShadowEmitter)
    assert isinstance(em.writer, act.SerializingShadowWriter)
    res = em.emit_tick(_Tick(), generated_at_utc=GEN, instrument_registry=REGISTRY)
    assert res["emitted"] is True
    assert res["key"] == "hermes:shadow:ticks:XAU_USD:latest:v1"


# ---------------- config fail-loud ----------------
def test_missing_host_port_db_fail_loud_when_enabled():
    for field, val, code in (("redis_host", "", "007"), ("redis_port", None, "008"),
                             ("redis_db", None, "009")):
        cfg = _runtime_cfg(**{field: val})
        try:
            rt.build_runtime_shadow_emitter(config=cfg, redis_client=StrictTypeFakeRedis())
            assert False
        except ValueError as e:
            assert "GOV-PUB-SHADOW-CFG-" + code in str(e)


def test_missing_authorisation_fail_loud_when_enabled():
    cfg = _runtime_cfg(shadow_authorised=False)
    try:
        rt.build_runtime_shadow_emitter(config=cfg, redis_client=StrictTypeFakeRedis())
        assert False
    except ValueError as e:
        assert "GOV-PUB-SHADOW-CFG-010" in str(e)


def test_live_mode_rejected():
    try:
        _runtime_cfg(write_mode=rt.WRITE_MODE_LIVE)
        assert False
    except ValueError as e:
        assert "GOV-PUB-RT-CFG-002" in str(e)


def test_ex_and_ttl_locked():
    for field, val, code in (("redis_ex_seconds", 5, "005"), ("payload_ttl_seconds", 10, "006")):
        try:
            _runtime_cfg(**{field: val})
            assert False
        except ValueError as e:
            assert "GOV-PUB-RT-CFG-" + code in str(e)


def test_localhost_as_production_rejected():
    cfg = _runtime_cfg(redis_host="127.0.0.1", treat_as_production=True, dev_shadow=False)
    try:
        rt.build_runtime_shadow_emitter(config=cfg, redis_client=StrictTypeFakeRedis())
        assert False
    except ValueError as e:
        assert "GOV-PUB-SHADOW-CFG-011" in str(e)


# ---------------- key / serialisation / payload ----------------
def test_canonical_live_key_rejected():
    for live in ("hermes:ticks:XAU_USD:latest:v1", "hermes:ticks:latest:v1"):
        try:
            sh.assert_shadow_key(live); assert False
        except ValueError as e:
            assert "GOV-PUB-SHADOW-KEY-002" in str(e)


def test_shadow_key_only_and_json_string():
    em = _enabled_emitter()
    res = em.emit_tick(_Tick(), generated_at_utc=GEN, instrument_registry=REGISTRY)
    stored, ex = em.writer.redis_client.store[res["key"]]
    assert res["key"].startswith("hermes:shadow:")
    assert isinstance(stored, str) and ex == 10                 # SET EX 10, JSON string value
    assert json.loads(stored)["ttl_seconds"] == 5
    assert res["value_type"] == "str"


def test_client_never_receives_dict():
    # StrictTypeFakeRedis raises on dict; a successful emit proves a str/bytes value
    em = _enabled_emitter()
    em.emit_tick(_Tick(), generated_at_utc=GEN, instrument_registry=REGISTRY)  # would raise on dict


def test_roundtrip_deserialise_validates():
    em = _enabled_emitter()
    res = em.emit_tick(_Tick(), generated_at_utc=GEN, instrument_registry=REGISTRY)
    stored, _ = em.writer.redis_client.store[res["key"]]
    assert tc.validate_tick_contract(act.deserialize_envelope(stored)) is True


def test_seq_null_and_stored_seq_rejected():
    em = _enabled_emitter()
    res = em.emit_tick(_Tick(), generated_at_utc=GEN, instrument_registry=REGISTRY)
    stored, _ = em.writer.redis_client.store[res["key"]]
    assert json.loads(stored)["data"]["seq"] is None
    # a dict tick carrying a stored-row seq is rejected fail-loud
    try:
        em.emit_tick({"instrument": "XAU_USD", "bid": 1, "ask": 2, "source": "oanda",
                      "source_received_at_utc": REC, "seq": 9},
                     generated_at_utc=GEN, instrument_registry=REGISTRY)
        assert False
    except ValueError as e:
        assert "GOV-PUB-TICK-004" in str(e)


def test_malformed_and_unsupported_fail_loud():
    em = _enabled_emitter()
    try:
        em.emit_tick(_Tick(bid=0, ask=1), generated_at_utc=GEN, instrument_registry=REGISTRY)
        assert False
    except ValueError as e:
        assert "GOV-TICK-CONTRACT-003" in str(e)
    try:
        em.emit_tick(_Tick(instrument="GBP_JPY"), generated_at_utc=GEN, instrument_registry=REGISTRY)
        assert False
    except ValueError as e:
        assert "GOV-PUB-TICK-003" in str(e)


def test_absent_source_unavailable():
    em = _enabled_emitter()
    res = em.emit_tick({"instrument": "XAU_USD"}, generated_at_utc=GEN, instrument_registry={"XAU_USD"})
    assert res["freshness_state"] == "UNAVAILABLE"
    stored, _ = em.writer.redis_client.store[res["key"]]
    assert tc.validate_tick_contract(act.deserialize_envelope(stored)) is True


# ---------------- legacy dict-path guard ----------------
def test_legacy_dict_path_refuses_real_client():
    cfg = sh.ShadowPublisherConfig(
        publisher_enabled=True, write_mode=sh.WRITE_MODE_SHADOW, namespace="hermes",
        shadow_prefix="hermes:shadow:", contract_version="v1", redis_ex_seconds=10,
        payload_ttl_seconds=5, redis_host="192.168.0.50", redis_port=6380, redis_db=0,
        shadow_authorised=True, treat_as_production=False, test_only=True)
    try:
        sh.ShadowTickPublisher(config=cfg, redis_client=RealishRedisClient())
        assert False
    except ValueError as e:
        assert "GOV-PUB-SHADOW-PUB-004" in str(e)


def test_serialising_writer_is_only_real_client_path():
    # the real-client-like object is accepted by SerializingShadowWriter (the allowed path)
    cfg = sh.ShadowPublisherConfig(
        publisher_enabled=True, write_mode=sh.WRITE_MODE_SHADOW, namespace="hermes",
        shadow_prefix="hermes:shadow:", contract_version="v1", redis_ex_seconds=10,
        payload_ttl_seconds=5, redis_host="192.168.0.50", redis_port=6380, redis_db=0,
        shadow_authorised=True, treat_as_production=False, test_only=True)
    w = act.SerializingShadowWriter(config=cfg, redis_client=RealishRedisClient())
    assert isinstance(w, act.SerializingShadowWriter)
    # and the legacy fake path still works for in-memory fakes
    p = sh.ShadowTickPublisher(config=cfg, redis_client=StrictTypeFakeRedis())
    assert p is not None


def test_no_callable_path_hands_dict_to_real_client():
    # detector flags real-client-likes; in-memory fakes are not flagged
    assert sh._looks_like_real_redis_client(RealishRedisClient()) is True
    assert sh._looks_like_real_redis_client(StrictTypeFakeRedis()) is False
    assert sh._looks_like_real_redis_client(None) is False


# ---------------- ownership / boundary / hygiene ----------------
def test_adapter_imports_are_clean():
    src = open(ADAPTER_MODULE).read()
    tree = ast.parse(src)
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module.split(".")[0])
    # only stdlib + HERMES utils + the lazy env_config (no redis at import time, no SQL/legacy)
    assert imported <= {"__future__", "datetime", "utils", "env_config"}
    assert not (imported & {"mysql", "pymysql", "mariadb", "sqlite3", "sqlalchemy", "redis",
                            "tradingProteus"})
    assert "tradingsignals.ticks" not in src.lower()


def test_tripwire_no_forbidden_tokens_in_new_code():
    forbidden = ["tradingproteus", "structure_engine", "falcon_structure", "helios_structures",
                 "choch", "bos", "order_block", "signal", "setup", "confidence", "permission",
                 "regime", "risk"]
    src = open(ADAPTER_MODULE).read().lower()
    hits = [t for t in forbidden if t in src]
    assert hits == [], f"forbidden tokens in adapter: {hits}"


def test_no_scheduler_in_adapter():
    src = open(ADAPTER_MODULE).read()
    tree = ast.parse(src)
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module.split(".")[0])
    assert not (imported & {"schedule", "crontab", "apscheduler", "systemd", "sched", "asyncio"})
    assert "while true" not in src.lower()


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
