"""Tests for the HERMES SHADOW activation: JSON serialisation hard gate + real-client write path.
WO-HELM-HERMES-REDIS-TICK-PUBLISHER-SHADOW-ACTIVATE-0001.

In-memory fakes only. No network, no proteus-redis, no dev/prod Redis.
"""
import ast
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
import utils.tick_contract_v1 as tc             # noqa: E402
import utils.tick_publisher_v1 as tp             # noqa: E402
import utils.tick_shadow_publisher_v1 as sh      # noqa: E402
import utils.tick_shadow_activation_v1 as act     # noqa: E402

REC = datetime(2026, 6, 16, 9, 58, 0, 0, tzinfo=timezone.utc)
GEN = datetime(2026, 6, 16, 9, 58, 0, 500000, tzinfo=timezone.utc)
REGISTRY = {"XAU_USD", "EUR_USD"}
ACT_MODULE = os.path.join(ROOT, "utils", "tick_shadow_activation_v1.py")
HARNESS = os.path.join(ROOT, "scripts", "shadow_activate_publish.py")


class StrictTypeFakeRedis:
    """In-memory fake that FAILS LOUD if handed a Python dict — proves the real-client contract."""

    def __init__(self):
        self.store = {}

    def set(self, key, value, ex=None):
        if isinstance(value, dict):
            raise AssertionError("real client received a dict — serialisation gate breached")
        if not isinstance(value, (str, bytes)):
            raise AssertionError(f"value must be str/bytes, got {type(value).__name__}")
        self.store[key] = (value, ex)
        return True

    def get(self, key):
        return self.store.get(key, (None,))[0]

    def ttl(self, key):
        return self.store.get(key, (None, None))[1]

    def scan_iter(self, pattern):
        import fnmatch
        return [k for k in self.store if fnmatch.fnmatch(k, pattern)]

    def delete(self, *keys):
        n = 0
        for k in keys:
            if k in self.store:
                del self.store[k]; n += 1
        return n


def _cfg(**over):
    base = dict(publisher_enabled=True, write_mode=sh.WRITE_MODE_SHADOW, namespace="hermes",
                shadow_prefix="hermes:shadow:", contract_version="v1", redis_ex_seconds=10,
                payload_ttl_seconds=5, redis_host="192.168.0.50", redis_port=6380, redis_db=0,
                shadow_authorised=True, treat_as_production=False, test_only=True)
    base.update(over)
    return sh.ShadowPublisherConfig(**base)


def _tick(**over):
    t = {"instrument": "XAU_USD", "bid": 4207.015, "ask": 4207.265, "source": "oanda",
         "source_received_at_utc": REC}
    t.update(over)
    return t


def _writer():
    return act.SerializingShadowWriter(config=_cfg(), redis_client=StrictTypeFakeRedis())


# ---------------- JSON serialisation hard gate ----------------
def test_serialize_returns_json_string():
    env = tc.build_tick_contract(instrument="XAU_USD", source_received_at_utc=REC,
                                 generated_at_utc=GEN, bid=4207.015, ask=4207.265)
    s = act.serialize_envelope(env)
    assert isinstance(s, str)
    assert json.loads(s)["key"] == "hermes:ticks:XAU_USD:latest:v1"


def test_serialize_rejects_non_dict():
    try:
        act.serialize_envelope("not-a-dict"); assert False
    except ValueError as e:
        assert "GOV-PUB-SHADOW-SER-001" in str(e)


def test_roundtrip_serialise_deserialise_validates():
    env = tc.build_tick_contract(instrument="XAU_USD", source_received_at_utc=REC,
                                 generated_at_utc=GEN, bid=4207.015, ask=4207.265)
    back = act.deserialize_envelope(act.serialize_envelope(env))
    assert back == env
    assert tc.validate_tick_contract(back) is True


def test_deserialise_rejects_dict_input():
    try:
        act.deserialize_envelope({"already": "a dict"}); assert False
    except ValueError as e:
        assert "GOV-PUB-SHADOW-SER-002" in str(e)


def test_utc_timestamps_survive_serialisation():
    env = tc.build_tick_contract(instrument="XAU_USD", source_received_at_utc=REC,
                                 generated_at_utc=GEN, bid=1, ask=2)
    back = json.loads(act.serialize_envelope(env))
    assert back["generated_at_utc"] == "2026-06-16T09:58:00.500Z"
    assert back["valid_until_utc"] == "2026-06-16T09:58:05.500Z"


# ---------------- real client never receives a dict ----------------
def test_real_client_never_receives_dict():
    w = _writer()
    res = w.publish_tick(_tick(), generated_at_utc=GEN, instrument_registry=REGISTRY)
    stored, ex = w.redis_client.store[res["key"]]
    assert isinstance(stored, str) and ex == 10
    assert res["value_type"] == "str" and res["serialised"] is True


def test_set_value_is_json_string_not_dict():
    w = _writer()
    res = w.publish_tick(_tick(), generated_at_utc=GEN, instrument_registry=REGISTRY)
    stored, _ = w.redis_client.store[res["key"]]
    parsed = json.loads(stored)            # must be parseable JSON
    assert isinstance(parsed, dict) and parsed["service"] == "HERMES"


def test_deserialised_redis_value_validates():
    w = _writer()
    res = w.publish_tick(_tick(), generated_at_utc=GEN, instrument_registry=REGISTRY)
    stored, _ = w.redis_client.store[res["key"]]
    assert tc.validate_tick_contract(act.deserialize_envelope(stored)) is True


# ---------------- shadow key + live-key refusal ----------------
def test_writes_shadow_key_only():
    w = _writer()
    r1 = w.publish_tick(_tick(), generated_at_utc=GEN, instrument_registry=REGISTRY)
    r2 = w.publish_aggregate(["XAU_USD"], generated_at_utc=GEN)
    assert r1["key"] == "hermes:shadow:ticks:XAU_USD:latest:v1"
    assert r2["key"] == "hermes:shadow:ticks:latest:v1"
    for k in w.redis_client.store:
        assert k.startswith("hermes:shadow:")
        assert not (k.startswith("hermes:ticks:") and not k.startswith("hermes:shadow:"))


def test_canonical_live_key_impossible():
    # assert_shadow_key refuses live keys; writer cannot emit them
    for live in ("hermes:ticks:XAU_USD:latest:v1", "hermes:ticks:latest:v1"):
        try:
            sh.assert_shadow_key(live); assert False
        except ValueError as e:
            assert "GOV-PUB-SHADOW-KEY-002" in str(e)


def test_ex10_and_ttl5_preserved():
    w = _writer()
    res = w.publish_tick(_tick(), generated_at_utc=GEN, instrument_registry=REGISTRY)
    assert res["ex_seconds"] == 10
    stored, ex = w.redis_client.store[res["key"]]
    assert ex == 10
    assert json.loads(stored)["ttl_seconds"] == 5


# ---------------- payload semantics ----------------
def test_seq_null_and_stored_seq_rejected():
    w = _writer()
    res = w.publish_tick(_tick(), generated_at_utc=GEN, instrument_registry=REGISTRY)
    stored, _ = w.redis_client.store[res["key"]]
    assert json.loads(stored)["data"]["seq"] is None
    try:
        w.publish_tick(_tick(seq=7), generated_at_utc=GEN, instrument_registry=REGISTRY); assert False
    except ValueError as e:
        assert "GOV-PUB-TICK-004" in str(e)


def test_malformed_and_unsupported_fail_loud():
    w = _writer()
    try:
        w.publish_tick(_tick(bid=0, ask=1), generated_at_utc=GEN, instrument_registry=REGISTRY); assert False
    except ValueError as e:
        assert "GOV-TICK-CONTRACT-003" in str(e)
    try:
        w.publish_tick(_tick(instrument="GBP_JPY"), generated_at_utc=GEN, instrument_registry=REGISTRY); assert False
    except ValueError as e:
        assert "GOV-PUB-TICK-003" in str(e)


def test_absent_source_unavailable():
    w = _writer()
    res = w.publish_tick({"instrument": "XAU_USD"}, generated_at_utc=GEN, instrument_registry=REGISTRY)
    assert res["freshness_state"] == "UNAVAILABLE"
    stored, _ = w.redis_client.store[res["key"]]
    assert tc.validate_tick_contract(act.deserialize_envelope(stored)) is True


# ---------------- config / fail-loud (carried through activation) ----------------
def test_shadow_requires_authorisation():
    try:
        _cfg(shadow_authorised=False); assert False
    except ValueError as e:
        assert "GOV-PUB-SHADOW-CFG-010" in str(e)


def test_live_mode_rejected():
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


def test_localhost_as_production_rejected():
    try:
        _cfg(redis_host="127.0.0.1", treat_as_production=True, test_only=False); assert False
    except ValueError as e:
        assert "GOV-PUB-SHADOW-CFG-011" in str(e)


def test_writer_requires_explicit_client():
    try:
        act.SerializingShadowWriter(config=_cfg(), redis_client=None); assert False
    except ValueError as e:
        assert "GOV-PUB-SHADOW-ACT-002" in str(e)


# ---------------- ownership / boundary ----------------
def test_no_sql_or_legacy_import_in_activation_module():
    src = open(ACT_MODULE).read()
    tree = ast.parse(src)
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module.split(".")[0])
    assert imported <= {"__future__", "json", "datetime", "utils"}
    assert not (imported & {"mysql", "pymysql", "mariadb", "sqlite3", "sqlalchemy"})
    assert "tradingsignals.ticks" not in src.lower()


def test_hermes_only_publisher():
    w = _writer()
    res = w.publish_tick(_tick(), generated_at_utc=GEN, instrument_registry=REGISTRY)
    env = json.loads(w.redis_client.store[res["key"]][0])
    assert env["service"] == "HERMES" and env["provenance"]["publisher"] == "HERMES"


# ---------------- tripwire over activation module + harness ----------------
def test_tripwire_no_forbidden_tokens():
    forbidden = ["tradingproteus", "structure_engine", "falcon_structure", "helios_structures",
                 "choch", "bos", "order_block", "signal", "setup", "confidence", "permission",
                 "regime", "risk"]
    for path in (ACT_MODULE, HARNESS):
        src = open(path).read().lower()
        hits = [t for t in forbidden if t in src]
        assert hits == [], f"forbidden tokens in {os.path.basename(path)}: {hits}"


def test_harness_has_no_scheduler_or_default_target():
    src = open(HARNESS).read()
    tree = ast.parse(src)
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module.split(".")[0])
    # no scheduling/recurring-runner mechanisms
    assert not (imported & {"schedule", "crontab", "apscheduler", "systemd", "sched"})
    low = src.lower()
    assert "while true" not in low and "crontab" not in low
    # connection target args are required (no defaults) — proven by argparse required=True
    assert src.replace(" ", "").count("required=True".replace(" ", "")) >= 3


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
