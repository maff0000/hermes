"""HERMES control-plane manifest truth for the live D1 LATEST candle — code-only, fake Redis.
WO-HELM-HERMES-D1-CANDLE-LATEST-MANIFEST-TRUTH-0001.

Proves candle_latest_d1 is reflected ACTIVE (active_families.candle_latest.D1, removed from gated_families) ONLY
when hermes:candles:XAU_USD:D1:latest:v1 exists AND validates as a genuine sealed 6/6, 22:00-anchored, canonical
XAU_USD D1 candle; missing/invalid/unsealed/wrong-anchor/XAUUSD -> stays PENDING (no overclaim). PR #86 derived-family
behaviour + M1-H4 unchanged.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import utils.hermes_runtime_publisher_steps_v1 as steps
import utils.hermes_control_plane_v1 as cp
import utils.hermes_sessions_v1 as sess
import utils.hermes_levels_v1 as lvl
import utils.candle_d1_derivation_v1 as d1d
import utils.candle_contract_v1 as cc

INST = "XAU_USD"


class _FakeRedis:
    def __init__(self):
        self.kv = {}
        self.deletes = []

    def get(self, k):
        v = self.kv.get(k)
        return v.encode() if isinstance(v, str) else v

    def exists(self, k):
        return 1 if k in self.kv else 0

    def set(self, k, v, ex=None):
        self.kv[k] = v

    def delete(self, *a):
        self.deletes.extend(a)


def _sealed_d1_env(d1_open=datetime(2026, 7, 8, 22, 0, tzinfo=timezone.utc)):
    opens = d1d.d1_child_h4_opens(d1_open)
    specs = [(2000, 2010, 1990, 2005, 10), (2005, 2030, 1995, 2020, 11), (2020, 2080, 2010, 2050, 12),
             (2050, 2060, 2000, 2030, 13), (2030, 2040, 1900, 1950, 14), (1950, 1975, 1940, 1970, 15)]
    kids = [{"timestamp": opens[i], "open": specs[i][0], "high": specs[i][1], "low": specs[i][2],
             "close": specs[i][3], "volume": specs[i][4]} for i in range(6)]
    env, _ = d1d.derive_d1(instrument=INST, d1_open=d1_open, h4_children=kids,
                           generated_at_utc=cc.normalise_utc(d1_open) + timedelta(seconds=d1d.D1_SECONDS))
    assert env["status"] == "OK" and env["data"]["source_count"] == 6
    return env


def _seed_base(fake):
    for tf in ("M1", "M5", "M15", "H1", "H4"):
        fake.kv[f"hermes:indicators:{INST}:{tf}:v1"] = "{}"
        fake.kv[f"hermes:candle_features:{INST}:{tf}:v1"] = "{}"
    fake.kv[sess.sessions_key(INST)] = "{}"
    fake.kv[lvl.levels_key(INST, "session")] = "{}"
    fake.kv[lvl.levels_key(INST, "intraday")] = "{}"
    return fake


def _set_d1_latest(fake, env):
    fake.kv[f"hermes:candles:{INST}:D1:latest:v1"] = json.dumps(env) if env is not None else None
    if env is None:
        fake.kv.pop(f"hermes:candles:{INST}:D1:latest:v1", None)
    return fake


def _cp_env(monkeypatch):
    monkeypatch.setenv("HERMES_REDIS_CONTROL_PLANE_ENABLED", "true")
    monkeypatch.setenv("HERMES_REDIS_CONTROL_PLANE_AUTHORISED", "true")
    for e in ("HERMES_INDICATOR_D1_AUTHORISED", "HERMES_CANDLE_FEATURE_D1_AUTHORISED", "HERMES_LEVEL_D1_AUTHORISED"):
        monkeypatch.setenv(e, "false")


def _manifest(fake):
    steps.control_plane_step(fake)
    return json.loads(fake.kv[cp.KEY_CONTRACT_MANIFEST])


# --------------------------------------------------------------------------- valid sealed D1 latest -> ACTIVE
def test_valid_sealed_d1_latest_active(monkeypatch):
    fake = _set_d1_latest(_seed_base(_FakeRedis()), _sealed_d1_env())
    _cp_env(monkeypatch)
    m = _manifest(fake)
    assert m["active_families"]["candle_latest"].get("D1") == cp.STATUS_ACTIVE
    assert "candle_latest_d1" not in m["gated_families"]                 # no longer the false PENDING
    cp.validate_manifest(m)                                              # relaxed validator accepts active state
    # M1-H4 candle_latest unchanged
    for tf in ("M1", "M5", "M15", "H1", "H4"):
        assert m["active_families"]["candle_latest"][tf] == cp.STATUS_ACTIVE


# --------------------------------------------------------------------------- missing / invalid -> stays PENDING
def test_missing_d1_latest_stays_pending(monkeypatch):
    fake = _set_d1_latest(_seed_base(_FakeRedis()), None)                # key absent
    _cp_env(monkeypatch)
    m = _manifest(fake)
    assert "D1" not in m["active_families"]["candle_latest"]
    assert m["gated_families"]["candle_latest_d1"]["status"] == cp.STATUS_PENDING_FIRST_DAILY_SEAL


def test_invalid_unsealed_d1_latest_stays_pending(monkeypatch):
    fake = _seed_base(_FakeRedis())
    fake.kv[f"hermes:candles:{INST}:D1:latest:v1"] = "{}"                # present but invalid/unsealed
    _cp_env(monkeypatch)
    m = _manifest(fake)
    assert "D1" not in m["active_families"]["candle_latest"]
    assert m["gated_families"]["candle_latest_d1"]["status"] == cp.STATUS_PENDING_FIRST_DAILY_SEAL


def test_partial_source_count_stays_pending(monkeypatch):
    env = _sealed_d1_env(); env["data"]["source_count"] = 5             # not 6/6 -> not active
    fake = _set_d1_latest(_seed_base(_FakeRedis()), env)
    _cp_env(monkeypatch)
    m = _manifest(fake)
    assert "D1" not in m["active_families"]["candle_latest"]
    assert m["gated_families"]["candle_latest_d1"]["status"] == cp.STATUS_PENDING_FIRST_DAILY_SEAL


def test_wrong_anchor_stays_pending(monkeypatch):
    env = _sealed_d1_env(); env["data"]["timestamp_utc"] = "2026-07-08T00:00:00.000Z"   # 00:00, not 22:00
    fake = _set_d1_latest(_seed_base(_FakeRedis()), env)
    _cp_env(monkeypatch)
    m = _manifest(fake)
    assert "D1" not in m["active_families"]["candle_latest"]
    assert m["gated_families"]["candle_latest_d1"]["status"] == cp.STATUS_PENDING_FIRST_DAILY_SEAL


def test_xauusd_d1_latest_denied(monkeypatch):
    env = _sealed_d1_env(); env["data"]["instrument"] = "XAUUSD"
    fake = _set_d1_latest(_seed_base(_FakeRedis()), env)
    _cp_env(monkeypatch)
    m = _manifest(fake)
    assert "D1" not in m["active_families"]["candle_latest"]
    assert m["gated_families"]["candle_latest_d1"]["status"] == cp.STATUS_PENDING_FIRST_DAILY_SEAL


# --------------------------------------------------------------------------- helper unit + no overclaim/split-brain
def test_d1_latest_active_helper():
    fake = _FakeRedis()
    assert steps._d1_latest_active(fake) is False                       # absent
    fake.kv[f"hermes:candles:{INST}:D1:latest:v1"] = json.dumps(_sealed_d1_env())
    assert steps._d1_latest_active(fake) is True
    fake.kv[f"hermes:candles:{INST}:D1:latest:v1"] = "{}"
    assert steps._d1_latest_active(fake) is False


def _mk_manifest():
    return cp.build_contract_manifest(generated_at_utc=datetime(2026, 7, 8, tzinfo=timezone.utc),
                                      environment="dev", run_env="STAGING", deployed_sha="x",
                                      service_identity="hermes-signal")


def test_default_builder_manifest_still_pending_and_valid():
    m = _mk_manifest()                                                   # pure builder default (no I/O): PENDING
    assert m["gated_families"]["candle_latest_d1"]["status"] == cp.STATUS_PENDING_FIRST_DAILY_SEAL
    assert "D1" not in m["active_families"]["candle_latest"]
    cp.validate_manifest(m)                                              # unchanged builder default is still valid


def test_validator_forbids_both_active_and_gated():
    b = _mk_manifest()
    b["active_families"]["candle_latest"]["D1"] = cp.STATUS_ACTIVE       # active marker present...
    # ...AND still gated -> split-brain, must raise
    with pytest.raises(ValueError):
        cp.validate_manifest(b)


def test_validator_accepts_active_when_not_gated():
    b = _mk_manifest()
    b["active_families"]["candle_latest"]["D1"] = cp.STATUS_ACTIVE
    b["gated_families"].pop("candle_latest_d1", None)
    cp.validate_manifest(b)                                              # active + not gated -> valid


# --------------------------------------------------------------------------- consumer_live / Falcon untouched
def test_no_consumer_falcon_added_by_this_wo():
    import inspect
    added = inspect.getsource(steps._d1_latest_active)
    for tok in ("consumer_live", "falcon", "regime", "risk", "strategy", "signal", "trade",
                "pymysql", "get_db_config", "market_map", "candles_H4"):
        assert tok not in added, f"candle_latest helper must not reference {tok!r}"
