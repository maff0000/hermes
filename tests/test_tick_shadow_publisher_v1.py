"""Tests for the HERMES Redis tick publisher v1 — SHADOW build.
WO-HELM-HERMES-REDIS-TICK-PUBLISHER-SHADOW-BUILD-0001.

In-memory fake Redis client only. No network, no proteus-redis, no dev/prod Redis.
"""
import ast
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
import utils.tick_contract_v1 as tc            # noqa: E402
import utils.tick_publisher_v1 as tp            # noqa: E402
import utils.tick_shadow_publisher_v1 as sh     # noqa: E402

REC = datetime(2026, 6, 16, 9, 58, 0, 0, tzinfo=timezone.utc)
GEN = datetime(2026, 6, 16, 9, 58, 0, 500000, tzinfo=timezone.utc)
REGISTRY = {"XAU_USD", "EUR_USD"}
SHADOW_MODULE = os.path.join(ROOT, "utils", "tick_shadow_publisher_v1.py")


class FakeShadowRedisClient:
    """In-memory only. Captures SET calls. Connects to NOTHING — no network, no real Redis."""

    def __init__(self):
        self.sets = []   # list of (key, value, ex)

    def set(self, key, value, ex=None):
        self.sets.append((key, value, ex))
        return True


def _cfg(**over):
    base = dict(publisher_enabled=True, write_mode=sh.WRITE_MODE_SHADOW, namespace="hermes",
                shadow_prefix="hermes:shadow:", contract_version="v1", redis_ex_seconds=10,
                payload_ttl_seconds=5, redis_host="127.0.0.1", redis_port=6399, redis_db=0,
                shadow_authorised=True, treat_as_production=False, test_only=True)
    base.update(over)
    return sh.ShadowPublisherConfig(**base)


def _tick(**over):
    t = {"instrument": "XAU_USD", "bid": 4207.015, "ask": 4207.265, "source": "oanda",
         "source_received_at_utc": REC}
    t.update(over)
    return t


def _pub():
    return sh.ShadowTickPublisher(config=_cfg(), redis_client=FakeShadowRedisClient())


# ---------------- shadow key transform ----------------
def test_shadow_key_transform_per_instrument():
    assert sh.to_shadow_key("hermes:ticks:XAU_USD:latest:v1") == "hermes:shadow:ticks:XAU_USD:latest:v1"


def test_shadow_key_transform_aggregate():
    assert sh.to_shadow_key("hermes:ticks:latest:v1") == "hermes:shadow:ticks:latest:v1"


def test_assert_shadow_key_rejects_live_keys():
    for live in ("hermes:ticks:XAU_USD:latest:v1", "hermes:ticks:latest:v1"):
        try:
            sh.assert_shadow_key(live); assert False
        except ValueError as e:
            assert "GOV-PUB-SHADOW-KEY-002" in str(e)


def test_assert_shadow_key_rejects_non_shadow():
    try:
        sh.assert_shadow_key("hermes:candles:M30:v1"); assert False
    except ValueError as e:
        assert "GOV-PUB-SHADOW-KEY-003" in str(e)


# ---------------- publish writes shadow keys only ----------------
def test_publish_writes_shadow_key_with_ex10():
    pub = _pub()
    res = pub.publish_tick(_tick(), generated_at_utc=GEN, instrument_registry=REGISTRY)
    assert res["key"] == "hermes:shadow:ticks:XAU_USD:latest:v1"
    assert res["ex_seconds"] == 10
    assert res["write_mode"] == "SHADOW_NO_LIVE"
    assert res["payload_validated"] is True
    assert res["redis_result"] is True
    # fake captured exactly that SET with ex=10 and a valid payload
    key, value, ex = pub.redis_client.sets[0]
    assert key == "hermes:shadow:ticks:XAU_USD:latest:v1" and ex == 10
    assert value["ttl_seconds"] == 5
    assert tc.validate_tick_contract(value) is True


def test_publish_aggregate_writes_shadow_catalog():
    pub = _pub()
    res = pub.publish_aggregate(["XAU_USD", "EUR_USD"], generated_at_utc=GEN)
    assert res["key"] == "hermes:shadow:ticks:latest:v1"
    assert "bid" not in pub.redis_client.sets[0][1]["data"]


def test_no_live_key_ever_written():
    pub = _pub()
    pub.publish_tick(_tick(), generated_at_utc=GEN, instrument_registry=REGISTRY)
    pub.publish_aggregate(["XAU_USD"], generated_at_utc=GEN)
    for key, _v, _ex in pub.redis_client.sets:
        assert key.startswith("hermes:shadow:")
        assert key not in ("hermes:ticks:latest:v1",)
        assert not (key.startswith("hermes:ticks:") and not key.startswith("hermes:shadow:"))


# ---------------- payload semantics ----------------
def test_payload_validates_and_valid_until():
    v = _pub().publish_tick(_tick(), generated_at_utc=GEN, instrument_registry=REGISTRY)
    assert v["generated_at_utc"] == "2026-06-16T09:58:00.500Z"
    assert v["valid_until_utc"] == "2026-06-16T09:58:05.500Z"


def test_seq_remains_null():
    val = _pub().redis_client if False else None  # noqa
    pub = _pub()
    pub.publish_tick(_tick(), generated_at_utc=GEN, instrument_registry=REGISTRY)
    assert pub.redis_client.sets[0][1]["data"]["seq"] is None


def test_stored_row_seq_rejected():
    try:
        _pub().publish_tick(_tick(seq=99), generated_at_utc=GEN, instrument_registry=REGISTRY)
        assert False
    except ValueError as e:
        assert "GOV-PUB-TICK-004" in str(e)


def test_malformed_tick_fails_loud():
    try:
        _pub().publish_tick(_tick(bid=0, ask=1), generated_at_utc=GEN, instrument_registry=REGISTRY)
        assert False
    except ValueError as e:
        assert "GOV-TICK-CONTRACT-003" in str(e)


def test_unsupported_instrument_fails_loud():
    try:
        _pub().publish_tick(_tick(instrument="GBP_JPY"), generated_at_utc=GEN,
                            instrument_registry=REGISTRY)
        assert False
    except ValueError as e:
        assert "GOV-PUB-TICK-003" in str(e)


def test_absent_source_produces_unavailable():
    pub = _pub()
    res = pub.publish_tick({"instrument": "XAU_USD"}, generated_at_utc=GEN,
                           instrument_registry=REGISTRY)
    assert res["freshness_state"] == "UNAVAILABLE"
    assert res["key"] == "hermes:shadow:ticks:XAU_USD:latest:v1"


# ---------------- config / fail-loud ----------------
def test_shadow_requires_authorisation():
    try:
        _cfg(shadow_authorised=False); assert False
    except ValueError as e:
        assert "GOV-PUB-SHADOW-CFG-010" in str(e)


def test_live_mode_fails_loud():
    try:
        _cfg(write_mode=sh.WRITE_MODE_LIVE); assert False
    except ValueError as e:
        assert "GOV-PUB-SHADOW-CFG-002" in str(e)


def test_missing_host_port_db_fail_loud():
    for field, val, code in (("redis_host", "", "007"), ("redis_port", None, "008"),
                             ("redis_db", None, "009")):
        try:
            _cfg(**{field: val}); assert False
        except ValueError as e:
            assert "GOV-PUB-SHADOW-CFG-" + code in str(e)


def test_default_target_impossible():
    # all three connection fields are required args; omitting one is a TypeError (no default)
    import inspect
    params = inspect.signature(sh.ShadowPublisherConfig.__init__).parameters
    for f in ("redis_host", "redis_port", "redis_db"):
        assert params[f].default is inspect._empty


def test_localhost_as_production_fails_loud():
    try:
        _cfg(redis_host="127.0.0.1", treat_as_production=True, test_only=False); assert False
    except ValueError as e:
        assert "GOV-PUB-SHADOW-CFG-011" in str(e)


def test_localhost_must_be_marked_test_only():
    try:
        _cfg(redis_host="localhost", test_only=False, treat_as_production=False); assert False
    except ValueError as e:
        assert "GOV-PUB-SHADOW-CFG-013" in str(e)


def test_explicit_prod_target_constructs():
    cfg = _cfg(redis_host="redis.prod.internal", redis_port=6379, redis_db=0,
               treat_as_production=True, test_only=False)
    assert cfg.redis_host == "redis.prod.internal" and cfg.shadow_authorised is True


def test_publisher_requires_explicit_client():
    try:
        sh.ShadowTickPublisher(config=_cfg(), redis_client=None); assert False
    except ValueError as e:
        assert "GOV-PUB-SHADOW-PUB-002" in str(e)


# ---------------- ownership / boundary ----------------
def test_publisher_is_hermes_only():
    v = _pub(); v.publish_tick(_tick(), generated_at_utc=GEN, instrument_registry=REGISTRY)
    payload = v.redis_client.sets[0][1]
    assert payload["service"] == "HERMES" and payload["provenance"]["publisher"] == "HERMES"


def test_no_interpretive_fields_in_payload():
    pub = _pub()
    t = _tick(); t["regime_label"] = "x"   # forbidden token on the raw input
    try:
        pub.publish_tick(t, generated_at_utc=GEN, instrument_registry=REGISTRY)
        payload = pub.redis_client.sets[0][1]
        assert "regime_label" not in payload["data"]
    except ValueError:
        pass


def test_no_sql_or_legacy_import_in_module():
    src = open(SHADOW_MODULE).read()
    tree = ast.parse(src)
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module.split(".")[0])
    # only stdlib datetime, __future__, the merged utils, and a LAZY redis client (shadow gate)
    assert imported <= {"__future__", "datetime", "utils", "redis"}
    # no SQL/legacy connector imports and no direct ticks-table read as a Falcon workaround
    assert not (imported & {"mysql", "pymysql", "mariadb", "sqlite3", "sqlalchemy", "MySQLdb"})
    assert "tradingsignals.ticks" not in src.lower()


# ---------------- tripwire ----------------
def test_tripwire_no_forbidden_tokens_in_shadow_module():
    forbidden = ["tradingproteus", "structure_engine", "falcon_structure", "helios_structures",
                 "choch", "bos", "order_block", "signal", "setup", "confidence", "permission",
                 "regime", "risk"]
    src = open(SHADOW_MODULE).read().lower()
    hits = [t for t in forbidden if t in src]
    assert hits == [], f"forbidden tokens in shadow module: {hits}"


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
