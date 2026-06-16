"""Tests for the HERMES Redis tick publisher v1 — INERT (no-write) build.
WO-HELM-HERMES-REDIS-TICK-PUBLISHER-INERT-BUILD-0001.

Pure-logic + in-memory sink. No Redis/DB/network. Live writer is not constructible here.
"""
import ast
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
import utils.tick_contract_v1 as tc          # noqa: E402
import utils.tick_publisher_v1 as tp          # noqa: E402

REC = datetime(2026, 6, 16, 9, 58, 0, 0, tzinfo=timezone.utc)
GEN = datetime(2026, 6, 16, 9, 58, 0, 500000, tzinfo=timezone.utc)
REGISTRY = {"XAU_USD", "EUR_USD"}
PUBLISHER_MODULE = os.path.join(ROOT, "utils", "tick_publisher_v1.py")


def _cfg():
    return tp.InertPublisherConfig(publisher_enabled=False, namespace="hermes",
                                   contract_version="v1", redis_ex_seconds=10,
                                   payload_ttl_seconds=5, write_mode=tp.WRITE_MODE_INERT)


def _tick(**over):
    t = {"instrument": "XAU_USD", "bid": 4207.015, "ask": 4207.265, "source": "oanda",
         "source_received_at_utc": REC}
    t.update(over)
    return t


def _plan(**over):
    return tp.build_tick_write_plan(_tick(**over), generated_at_utc=GEN,
                                    instrument_registry=REGISTRY, config=_cfg())


# ---------------- write-plan shape ----------------
def test_per_instrument_plan_shape():
    p = _plan()
    assert p["operation"] == "SET"
    assert p["key"] == "hermes:ticks:XAU_USD:latest:v1"
    assert p["ex_seconds"] == 10
    assert p["write_mode"] == "INERT_NO_WRITE"
    assert p["value"]["ttl_seconds"] == 5


def test_aggregate_plan_shape():
    p = tp.build_aggregate_write_plan(["XAU_USD", "EUR_USD"], generated_at_utc=GEN, config=_cfg())
    assert p["operation"] == "SET"
    assert p["key"] == "hermes:ticks:latest:v1"
    assert p["ex_seconds"] == 10
    assert p["write_mode"] == "INERT_NO_WRITE"
    assert "bid" not in p["value"]["data"]


# ---------------- payload validates via contract ----------------
def test_payload_validates_with_contract_validator():
    assert tc.validate_tick_contract(_plan()["value"]) is True
    agg = tp.build_aggregate_write_plan(["XAU_USD"], generated_at_utc=GEN, config=_cfg())
    assert tc.validate_tick_contract(agg["value"]) is True


def test_valid_until_is_generated_plus_ttl():
    v = _plan()["value"]
    assert v["generated_at_utc"] == "2026-06-16T09:58:00.500Z"
    assert v["valid_until_utc"] == "2026-06-16T09:58:05.500Z"


def test_ttl_and_ex_constants():
    assert tc.TTL_SECONDS == 5 and tc.REDIS_EX_SECONDS == 10
    assert _plan()["value"]["ttl_seconds"] == 5 and _plan()["ex_seconds"] == 10


# ---------------- seq rules ----------------
def test_seq_remains_null():
    assert _plan()["value"]["data"]["seq"] is None


def test_stored_row_seq_is_rejected_not_propagated():
    try:
        _plan(seq=12345)
        assert False
    except ValueError as e:
        assert "GOV-PUB-TICK-004" in str(e)


# ---------------- price sanity / malformed / unavailable ----------------
def test_bid_ask_mid_spread_preserved():
    d = _plan()["value"]["data"]
    assert d["mid"] == round((4207.015 + 4207.265) / 2, 6)
    assert d["spread"] == round(4207.265 - 4207.015, 6)


def test_malformed_tick_fails_loud():
    for bad in ({"bid": 0, "ask": 1}, {"bid": 1, "ask": 0}, {"bid": 5, "ask": 4}):
        try:
            _plan(**bad)
            assert False
        except ValueError as e:
            assert "GOV-TICK-CONTRACT-003" in str(e)


def test_missing_source_produces_unavailable():
    p = tp.build_tick_write_plan({"instrument": "XAU_USD"}, generated_at_utc=GEN,
                                 instrument_registry=REGISTRY, config=_cfg())
    assert p["value"]["freshness_state"] == "UNAVAILABLE"
    assert p["value"]["data"]["bid"] is None
    assert tc.validate_tick_contract(p["value"]) is True


def test_unsupported_instrument_fails_loud():
    try:
        tp.build_tick_write_plan(_tick(instrument="GBP_JPY"), generated_at_utc=GEN,
                                 instrument_registry=REGISTRY, config=_cfg())
        assert False
    except ValueError as e:
        assert "GOV-PUB-TICK-003" in str(e)


# ---------------- no-write sink ----------------
def test_no_write_sink_captures_without_io():
    sink = tp.build_publish_sink("inert")
    sink.publish(_plan())
    sink.publish(_plan(instrument="EUR_USD", bid=1.1, ask=1.1002))
    assert sink.keys() == ["hermes:ticks:XAU_USD:latest:v1", "hermes:ticks:EUR_USD:latest:v1"]
    assert all(p["write_mode"] == "INERT_NO_WRITE" for p in sink.captured)
    # sink holds no client/connection/socket attributes
    assert not any(a in dir(sink) for a in ("client", "connection", "redis", "socket", "conn"))


def test_sink_rejects_non_inert_plan():
    sink = tp.NoWriteTickPublishSink()
    try:
        sink.publish({"operation": "SET", "key": "k", "value": {}, "write_mode": "LIVE"})
        assert False
    except ValueError as e:
        assert "GOV-PUB-SINK-001" in str(e)


# ---------------- live writer not constructible ----------------
def test_live_sink_not_constructible():
    try:
        tp.build_publish_sink("live")
        assert False
    except ValueError as e:
        assert "GOV-PUB-SINK-003" in str(e)


def test_no_live_redis_client_in_module():
    src = open(PUBLISHER_MODULE).read()
    tree = ast.parse(src)
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module.split(".")[0])
    assert "redis" not in imported
    assert imported <= {"__future__", "datetime", "utils"}


# ---------------- config / fail-loud ----------------
def test_config_requires_explicit_enable():
    try:
        tp.InertPublisherConfig(publisher_enabled=None, namespace="hermes", contract_version="v1",
                                redis_ex_seconds=10, payload_ttl_seconds=5,
                                write_mode=tp.WRITE_MODE_INERT)
        assert False
    except ValueError as e:
        assert "GOV-PUB-CFG-001" in str(e)


def test_config_namespace_must_be_hermes():
    try:
        tp.InertPublisherConfig(publisher_enabled=False, namespace="proteus", contract_version="v1",
                                redis_ex_seconds=10, payload_ttl_seconds=5,
                                write_mode=tp.WRITE_MODE_INERT)
        assert False
    except ValueError as e:
        assert "GOV-PUB-CFG-002" in str(e)


def test_config_rejects_wrong_ttl_ex_version():
    base = dict(publisher_enabled=False, namespace="hermes", contract_version="v1",
                redis_ex_seconds=10, payload_ttl_seconds=5, write_mode=tp.WRITE_MODE_INERT)
    for field, val, code in (("contract_version", "v2", "003"), ("redis_ex_seconds", 5, "004"),
                             ("payload_ttl_seconds", 10, "005")):
        kw = dict(base); kw[field] = val
        try:
            tp.InertPublisherConfig(**kw)
            assert False
        except ValueError as e:
            assert "GOV-PUB-CFG-" + code in str(e)


def test_live_mode_not_permitted_in_this_wo():
    try:
        tp.InertPublisherConfig(publisher_enabled=True, namespace="hermes", contract_version="v1",
                                redis_ex_seconds=10, payload_ttl_seconds=5,
                                write_mode=tp.WRITE_MODE_LIVE)
        assert False
    except ValueError as e:
        assert "GOV-PUB-CFG-006" in str(e)


def test_live_readiness_no_default_target():
    try:
        tp.validate_live_readiness(live_target=None, live_authorised=True, treat_as_production=True)
        assert False
    except ValueError as e:
        assert "GOV-PUB-LIVE-002" in str(e)


def test_live_readiness_requires_authorisation():
    try:
        tp.validate_live_readiness(live_target={"host": "redis.prod", "port": 6379, "db": 0},
                                   live_authorised=False, treat_as_production=True)
        assert False
    except ValueError as e:
        assert "GOV-PUB-LIVE-001" in str(e)


def test_live_readiness_no_localhost_as_production():
    try:
        tp.validate_live_readiness(live_target={"host": "127.0.0.1", "port": 6379, "db": 0},
                                   live_authorised=True, treat_as_production=True)
        assert False
    except ValueError as e:
        assert "GOV-PUB-LIVE-003" in str(e)


def test_live_readiness_passes_when_explicit_and_authorised():
    assert tp.validate_live_readiness(
        live_target={"host": "redis.prod.internal", "port": 6379, "db": 0},
        live_authorised=True, treat_as_production=True) is True


# ---------------- ownership / boundary ----------------
def test_publisher_is_hermes_only():
    assert _plan()["value"]["provenance"]["publisher"] == "HERMES"
    assert _plan()["value"]["service"] == "HERMES"


def test_no_interpretive_fields_accepted():
    # an interpretive field injected on the raw tick must not survive into a valid contract
    t = _tick()
    t["structure_state"] = "x"   # forbidden token
    try:
        # the contract builder ignores unknown kwargs in dict; validator must catch it if present.
        # Here we prove the published payload never carries it.
        p = tp.build_tick_write_plan(t, generated_at_utc=GEN, instrument_registry=REGISTRY,
                                     config=_cfg())
        assert "structure_state" not in p["value"]["data"]
        assert tc.validate_tick_contract(p["value"]) is True
    except ValueError:
        pass


def test_no_consumer_shaped_keys():
    # publisher only ever produces hermes:ticks:* keys
    keys = {_plan()["key"],
            tp.build_aggregate_write_plan(["XAU_USD"], generated_at_utc=GEN, config=_cfg())["key"]}
    for k in keys:
        assert k.startswith("hermes:ticks:")


# ---------------- tripwire: forbidden imports/strings in publisher code ----------------
def test_tripwire_no_forbidden_tokens_in_publisher_module():
    forbidden = ["tradingproteus", "structure_engine", "falcon_structure", "helios_structures",
                 "choch", "bos", "order_block", "signal", "setup", "confidence", "permission",
                 "regime", "risk"]
    src = open(PUBLISHER_MODULE).read().lower()
    hits = [t for t in forbidden if t in src]
    assert hits == [], f"forbidden tokens in publisher module: {hits}"


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
