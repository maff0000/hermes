"""Tests for the dev-SHADOW candle writer wiring (DIRECT-NATIVE M5/H1 only).
WO-HELM-HERMES-CANDLE-FORWARD-SHADOW-WRITER-WIRE-DEV-0001.

In-memory fake Redis only. No live Redis/SQL. No backfill. Disabled by default.
"""
import ast
import json
import os
from datetime import datetime, timedelta, timezone

from utils import candle_runtime_seam_v1 as seam
from utils import candle_publisher_v1 as cp
from utils import candle_contract_v1 as cc

SEAM_MODULE = seam.__file__


class _TF:
    def __init__(self, name):
        self.name = name


class _Candle:
    def __init__(self, instrument="XAU_USD", tf="M5", complete=True, ts=None,
                 o=2000.0, h=2010.0, low=1995.0, c=2005.0, volume=120):
        self.instrument = instrument
        self.timeframe = _TF(tf)
        self.timestamp = ts or datetime(2026, 6, 16, 8, 0, tzinfo=timezone.utc)
        self.open = o
        self.high = h
        self.low = low
        self.close = c
        self.volume = volume
        self.complete = complete


class StrictFakeRedis:
    def __init__(self):
        self.store = {}
        self.sets = []

    def set(self, key, value, ex=None):
        if isinstance(value, dict):
            raise AssertionError("real client received a dict")
        if not isinstance(value, (str, bytes)):
            raise AssertionError("value must be str/bytes")
        self.store[key] = (value, ex)
        self.sets.append((key, value, ex))
        return True


def _shadow_cfg(**over):
    base = dict(publish_enabled=False, publish_authorised=False, shadow_publish_enabled=True,
                shadow_authorised=True, namespace="hermes", contract_version="v1",
                redis_host="192.168.11.10", redis_port=6380, redis_db=0,
                treat_as_production=False, dev_shadow=True)
    base.update(over)
    return cp.CandlePublisherConfig(**base)


def _shadow_seam(client=None):
    return seam.build_shadow_seam(config=_shadow_cfg(), redis_client=client or StrictFakeRedis())


def _gen(tf):
    return datetime(2026, 6, 16, 8, 0, tzinfo=timezone.utc) + timedelta(seconds=cc.TF_SECONDS[tf] + 0.5)


# ---------------- M5 / H1 native write ----------------
def test_m5_closed_writes_valid_shadow_key():
    sh = _shadow_seam()
    res = sh.emit(_Candle(tf="M5"), generated_at_utc=_gen("M5"))
    assert res["emitted"] is True and res["wrote"] is True
    assert res["key"] == "hermes:shadow:candles:XAU_USD:M5:latest:v1"
    key, value, ex = sh.writer.redis_client.sets[0]
    assert isinstance(value, str) and ex == cc.redis_ex_seconds("M5")
    env = cc.validate_candle_contract(json.loads(value))
    assert env is True
    assert sh.metrics["candles_shadow_published"]["M5"] == 1


def test_h1_closed_writes_valid_shadow_key():
    sh = _shadow_seam()
    res = sh.emit(_Candle(tf="H1"), generated_at_utc=_gen("H1"))
    assert res["key"] == "hermes:shadow:candles:XAU_USD:H1:latest:v1"
    env = json.loads(sh.writer.redis_client.store[res["key"]][0])
    assert cc.validate_candle_contract(env) is True
    d = env["data"]
    assert d["source_count"] == 1 and d["expected_source_count"] == 1 and d["source_coverage"] == 1.0
    assert env["provenance"]["derivation"] == cc.DERIVATION_DIRECT
    assert d["derivation_policy"] == cc.DERIVATION_POLICY_DIRECT
    assert d["source_policy_epoch"] == "DIRECT_NATIVE_V1"
    assert env["status"] == "OK" and d["gap_state"] == "NONE"


def test_payload_is_json_string_not_dict():
    sh = _shadow_seam()
    res = sh.emit(_Candle(tf="M5"), generated_at_utc=_gen("M5"))
    stored, _ = sh.writer.redis_client.store[res["key"]]
    assert isinstance(stored, str) and isinstance(json.loads(stored), dict)


# ---------------- forming never OK ----------------
def test_forming_candle_is_forming_never_ok():
    sh = _shadow_seam()
    res = sh.emit(_Candle(tf="M5", complete=False), generated_at_utc=_gen("M5"))
    assert res["emitted"] is True
    env = json.loads(sh.writer.redis_client.store[res["key"]][0])
    assert env["status"] == "FORMING" and env["status"] != "OK"
    assert env["freshness_state"] == "FORMING"


# ---------------- unsupported timeframes skipped ----------------
def test_unsupported_timeframes_skipped_no_write():
    sh = _shadow_seam()
    for tf in ("M1", "M15", "D1", "H4", "D"):
        res = sh.emit(_Candle(tf=tf), generated_at_utc=_gen("M5"))
        assert res["emitted"] is False and res["wrote"] is False
        assert res["reason"] == "UNSUPPORTED_TIMEFRAME" and res["timeframe"] == tf
    assert sh.writer.redis_client.sets == []   # nothing written
    for tf in ("M1", "M15", "D1", "H4", "D"):
        assert sh.metrics["candles_skipped_unsupported_tf"][tf] == 1


def test_no_silent_remap_of_unsupported():
    sh = _shadow_seam()
    sh.emit(_Candle(tf="M1"), generated_at_utc=_gen("M5"))
    # an M1 candle never produces an M5/H1 shadow key
    assert all("M1" not in k for k in sh.writer.redis_client.store)
    assert sh.writer.redis_client.store == {}


# ---------------- key boundary ----------------
def test_only_shadow_keys_no_canonical_no_6379():
    sh = _shadow_seam()
    sh.emit(_Candle(tf="M5"), generated_at_utc=_gen("M5"))
    sh.emit(_Candle(tf="H1"), generated_at_utc=_gen("H1"))
    for k in sh.writer.redis_client.store:
        assert k.startswith("hermes:shadow:candles:")
        assert not (k.startswith("hermes:candles:") and not k.startswith("hermes:shadow:"))


def test_assert_shadow_key_blocks_canonical():
    for canon in ("hermes:candles:XAU_USD:M5:latest:v1", "hermes:candles:XAU_USD:H1:latest:v1"):
        try:
            cp.assert_shadow_key(canon); assert False
        except ValueError as e:
            assert "GOV-CANDLE-PUB-KEY-002" in str(e)


def test_ttl_per_timeframe():
    sh = _shadow_seam()
    sh.emit(_Candle(tf="M5"), generated_at_utc=_gen("M5"))
    sh.emit(_Candle(tf="H1"), generated_at_utc=_gen("H1"))
    exs = {k.rsplit(":", 3)[0].rsplit(":", 1)[-1]: ex for k, (_v, ex) in sh.writer.redis_client.store.items()}
    # M5 EX 360, H1 EX 3900
    assert sh.writer.redis_client.store["hermes:shadow:candles:XAU_USD:M5:latest:v1"][1] == 360
    assert sh.writer.redis_client.store["hermes:shadow:candles:XAU_USD:H1:latest:v1"][1] == 3900


# ---------------- config gates / fail-loud ----------------
def test_shadow_seam_requires_shadow_auth():
    try:
        seam.build_shadow_seam(config=_shadow_cfg(shadow_authorised=False),
                               redis_client=StrictFakeRedis()); assert False
    except ValueError as e:
        assert "GOV-CANDLE-PUB-SHADOW-001" in str(e)


def test_shadow_seam_requires_explicit_target():
    try:
        seam.build_shadow_seam(config=_shadow_cfg(redis_host=""), redis_client=StrictFakeRedis()); assert False
    except ValueError as e:
        assert "GOV-CANDLE-PUB-SHADOW-002" in str(e)


def test_localhost_as_production_fails_loud():
    try:
        seam.build_shadow_seam(config=_shadow_cfg(redis_host="127.0.0.1", treat_as_production=True,
                                                  dev_shadow=False), redis_client=StrictFakeRedis()); assert False
    except ValueError as e:
        assert "GOV-CANDLE-PUB-SHADOW-003" in str(e)


def test_shadow_seam_requires_writer_type():
    try:
        seam.ShadowCandleForwardSeam(object()); assert False
    except ValueError as e:
        assert "GOV-CANDLE-FWD-SEAM-004" in str(e)


# ---------------- from_env (default disabled / fail-loud; no real client) ----------------
def _clear_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("HERMES_CANDLE_FORWARD") or k.startswith("DEV_HERMES_CANDLE_FORWARD"):
            monkeypatch.delenv(k, raising=False)


def test_from_env_default_disabled(monkeypatch):
    _clear_env(monkeypatch)
    assert isinstance(seam.build_candle_forward_seam_from_env(), cp.DisabledCandleEmitter)


def test_from_env_shadow_missing_redis_config_fails_loud(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("HERMES_CANDLE_FORWARD_ENABLED", "true")
    monkeypatch.setenv("HERMES_CANDLE_FORWARD_SINK", "shadow")
    # no redis host/port/db -> env_config required-missing fail-loud BEFORE any client construction
    try:
        seam.build_candle_forward_seam_from_env(); assert False
    except (ValueError, Exception) as e:
        assert "HERMES_CANDLE_FORWARD_SHADOW_REDIS_HOST" in str(e) or "required" in str(e).lower()


def test_from_env_canonical_sink_fails_loud(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("HERMES_CANDLE_FORWARD_ENABLED", "true")
    monkeypatch.setenv("HERMES_CANDLE_FORWARD_SINK", "canonical")
    try:
        seam.build_candle_forward_seam_from_env(); assert False
    except ValueError as e:
        assert seam.FAULT_WRITE_FORBIDDEN in str(e)


# ---------------- tripwires ----------------
def test_no_cross_app_or_legacy_imports():
    tree = ast.parse(open(SEAM_MODULE).read())
    imp = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imp.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imp.add(n.module.split(".")[0])
    # stdlib datetime/logging + HERMES utils (top-level); env_config + redis are LAZY (inside functions)
    assert imp <= {"datetime", "logging", "utils", "env_config", "redis"}
    assert not (imp & {"tradingProteus", "falcon", "ares", "helios", "structure_engine", "solo", "neo"})
    # redis must be imported lazily, not at module top-level (no real client at import)
    top = ast.parse(open(SEAM_MODULE).read()).body
    top_imports = set()
    for n in top:
        if isinstance(n, ast.Import):
            top_imports.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            top_imports.add(n.module.split(".")[0])
    assert "redis" not in top_imports and "env_config" not in top_imports


def test_no_legacy_candle_pubsub_or_sql_in_module():
    src = open(SEAM_MODULE).read().lower()
    assert "signals:candle" not in src
    assert "candles_h4" not in src and "candles_m30" not in src   # no stale-SQL source
    assert "backfill" not in src and "select " not in src


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = 0
    for fn in fns:
        try:
            import inspect
            if "monkeypatch" in inspect.signature(fn).parameters:
                continue
            fn(); p += 1; print("PASS", fn.__name__)
        except Exception:
            print("FAIL", fn.__name__); traceback.print_exc()
    print(f"{p} non-monkeypatch passed")
