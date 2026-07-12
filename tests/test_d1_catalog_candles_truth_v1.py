"""HERMES catalog:candles D1 truth — code-only, fake Redis.
WO-HELM-HERMES-D1-CATALOG-CANDLES-TRUTH-0001.

Proves hermes:catalog:candles:v1 D1 latest_status/history_status/forward_history_status become ACTIVE ONLY from
validated runtime truth (sealed D1 latest / valid D1 history newest member + depth>0 / forward-writer gates), and
stay PENDING/BLOCKED otherwise (no overclaim). M1-H4 catalog + PR#86/#87 manifest truth unchanged.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import utils.hermes_runtime_publisher_steps_v1 as steps
import utils.hermes_control_plane_v1 as cp
import utils.hermes_sessions_v1 as sess
import utils.hermes_levels_v1 as lvl
import utils.candle_d1_history_v1 as d1h
import utils.candle_d1_derivation_v1 as d1d
import utils.candle_contract_v1 as cc

INST = "XAU_USD"


class _FakeRedis:
    def __init__(self):
        self.kv = {}
        self.z = {}
        self.deletes = []

    def get(self, k):
        v = self.kv.get(k)
        return v.encode() if isinstance(v, str) else v

    def exists(self, k):
        return 1 if (k in self.kv or k in self.z) else 0

    def set(self, k, v, ex=None):
        self.kv[k] = v

    def zadd(self, k, m):
        self.z.setdefault(k, {}).update(m)

    def zrevrange(self, k, a, b):
        items = sorted(self.z.get(k, {}).items(), key=lambda kv: kv[1], reverse=True)
        return [m for m, _ in (items[a:b + 1] if b != -1 else items[a:])]

    def zcard(self, k):
        return len(self.z.get(k, {}))

    def delete(self, *a):
        self.deletes.extend(a)


def _sealed_d1_env(d1_open=datetime(2026, 7, 9, 22, 0, tzinfo=timezone.utc)):
    opens = d1d.d1_child_h4_opens(d1_open)
    specs = [(2000, 2010, 1990, 2005, 10), (2005, 2030, 1995, 2020, 11), (2020, 2080, 2010, 2050, 12),
             (2050, 2060, 2000, 2030, 13), (2030, 2040, 1900, 1950, 14), (1950, 1975, 1940, 1970, 15)]
    kids = [{"timestamp": opens[i], "open": specs[i][0], "high": specs[i][1], "low": specs[i][2],
             "close": specs[i][3], "volume": specs[i][4]} for i in range(6)]
    env, _ = d1d.derive_d1(instrument=INST, d1_open=d1_open, h4_children=kids,
                           generated_at_utc=cc.normalise_utc(d1_open) + timedelta(seconds=d1d.D1_SECONDS))
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
    if env is None:
        fake.kv.pop(f"hermes:candles:{INST}:D1:latest:v1", None)
    else:
        fake.kv[f"hermes:candles:{INST}:D1:latest:v1"] = json.dumps(env)
    return fake


def _seed_d1_history(fake, *, days=5, first=datetime(2026, 5, 1, 22, 0, tzinfo=timezone.utc)):
    idx = f"hermes:candles:{INST}:D1:history:v1:index"
    for d in range(days):
        env = _sealed_d1_env(first + timedelta(days=d))
        ep = int(cc.normalise_utc(first + timedelta(days=d)).timestamp())
        fake.kv[d1h.d1_history_key(INST, ep)] = json.dumps(env)
        fake.z.setdefault(idx, {})[str(ep)] = ep
    return fake


def _cp_env(monkeypatch, *, fwd="false"):
    monkeypatch.setenv("HERMES_REDIS_CONTROL_PLANE_ENABLED", "true")
    monkeypatch.setenv("HERMES_REDIS_CONTROL_PLANE_AUTHORISED", "true")
    for e in ("HERMES_INDICATOR_D1_AUTHORISED", "HERMES_CANDLE_FEATURE_D1_AUTHORISED", "HERMES_LEVEL_D1_AUTHORISED"):
        monkeypatch.setenv(e, "false")
    monkeypatch.setenv("HERMES_CANDLE_D1_HISTORY_ENABLED", fwd)
    monkeypatch.setenv("HERMES_CANDLE_D1_HISTORY_AUTHORISED", fwd)


def _catalog(fake):
    steps.control_plane_step(fake)
    return json.loads(fake.kv[cp.KEY_CATALOG_CANDLES])["timeframes"]["D1"]


# --------------------------------------------------------------------------- fully live -> all ACTIVE
def test_fully_live_d1_all_active(monkeypatch):
    fake = _seed_d1_history(_set_d1_latest(_seed_base(_FakeRedis()), _sealed_d1_env()), days=5)
    _cp_env(monkeypatch, fwd="true")
    d1 = _catalog(fake)
    assert d1["latest_status"] == cp.STATUS_ACTIVE
    assert d1["history_status"] == cp.STATUS_ACTIVE
    assert d1["forward_history_status"] == cp.STATUS_ACTIVE
    assert d1["notes"] is None                                          # caveat note cleared once fully live
    cp.validate_candle_catalog({"publisher": "HERMES", "schema_version": cp.SCHEMA_VERSION, "contract_version": "v1",
                                "generated_at_utc": "2026-07-12T00:00:00.000Z",
                                "canonical_instruments": ["XAU_USD"],
                                "timeframes": json.loads(fake.kv[cp.KEY_CATALOG_CANDLES])["timeframes"],
                                "source_policies": cp.SOURCE_POLICIES})


# --------------------------------------------------------------------------- latest truth
def test_missing_d1_latest_stays_pending(monkeypatch):
    fake = _set_d1_latest(_seed_d1_history(_seed_base(_FakeRedis()), days=5), None)
    _cp_env(monkeypatch, fwd="true")
    assert _catalog(fake)["latest_status"] == cp.STATUS_PENDING_FIRST_DAILY_SEAL


def test_invalid_d1_latest_stays_pending(monkeypatch):
    fake = _seed_d1_history(_seed_base(_FakeRedis()), days=5)
    fake.kv[f"hermes:candles:{INST}:D1:latest:v1"] = "{}"
    _cp_env(monkeypatch, fwd="true")
    assert _catalog(fake)["latest_status"] == cp.STATUS_PENDING_FIRST_DAILY_SEAL


def test_partial_and_wrong_anchor_and_xauusd_latest_pending(monkeypatch):
    for mutate in (lambda e: e["data"].__setitem__("source_count", 5),
                   lambda e: e["data"].__setitem__("timestamp_utc", "2026-07-09T00:00:00.000Z"),
                   lambda e: e["data"].__setitem__("instrument", "XAUUSD")):
        env = _sealed_d1_env(); mutate(env)
        fake = _seed_d1_history(_set_d1_latest(_seed_base(_FakeRedis()), env), days=5)
        _cp_env(monkeypatch, fwd="true")
        assert _catalog(fake)["latest_status"] == cp.STATUS_PENDING_FIRST_DAILY_SEAL


# --------------------------------------------------------------------------- history truth
def test_missing_d1_history_blocked(monkeypatch):
    fake = _set_d1_latest(_seed_base(_FakeRedis()), _sealed_d1_env())        # latest live, but NO history index
    _cp_env(monkeypatch, fwd="true")
    d1 = _catalog(fake)
    assert d1["latest_status"] == cp.STATUS_ACTIVE                           # latest independent
    assert d1["history_status"] == cp.STATUS_BLOCKED_UNTIL_D1_LATEST_GREEN   # history not active


def test_invalid_history_member_blocked(monkeypatch):
    fake = _set_d1_latest(_seed_base(_FakeRedis()), _sealed_d1_env())
    idx = f"hermes:candles:{INST}:D1:history:v1:index"
    fake.z[idx] = {"111": 111}; fake.kv[f"hermes:candles:{INST}:D1:history:v1:111"] = "{}"   # depth>0 but member invalid
    _cp_env(monkeypatch, fwd="true")
    assert _catalog(fake)["history_status"] == cp.STATUS_BLOCKED_UNTIL_D1_LATEST_GREEN


# --------------------------------------------------------------------------- forward-writer truth
def test_forward_writer_gates_off_blocked(monkeypatch):
    fake = _seed_d1_history(_set_d1_latest(_seed_base(_FakeRedis()), _sealed_d1_env()), days=5)
    _cp_env(monkeypatch, fwd="false")                                       # writer gates OFF
    assert _catalog(fake)["forward_history_status"] == cp.STATUS_BLOCKED_UNTIL_D1_LATEST_GREEN


def test_forward_writer_gates_on_active(monkeypatch):
    fake = _seed_d1_history(_set_d1_latest(_seed_base(_FakeRedis()), _sealed_d1_env()), days=5)
    _cp_env(monkeypatch, fwd="true")
    assert _catalog(fake)["forward_history_status"] == cp.STATUS_ACTIVE
    assert steps._d1_forward_writer_active() is True
    monkeypatch.setenv("HERMES_CANDLE_D1_HISTORY_AUTHORISED", "false")
    assert steps._d1_forward_writer_active() is False                       # enabled-without-authorised -> not active


# --------------------------------------------------------------------------- M1-H4 + manifest compat + boundaries
def test_m1h4_catalog_unchanged_and_manifest_still_truthful(monkeypatch):
    fake = _seed_d1_history(_set_d1_latest(_seed_base(_FakeRedis()), _sealed_d1_env()), days=5)
    # also make derived families live so the PR#86/#87 manifest path is exercised
    fake.kv[f"hermes:indicators:{INST}:D1:v1"] = "{}"; fake.kv[f"hermes:candle_features:{INST}:D1:v1"] = "{}"
    fake.kv[lvl.levels_key(INST, "daily")] = "{}"
    _cp_env(monkeypatch, fwd="true")
    monkeypatch.setenv("HERMES_INDICATOR_D1_AUTHORISED", "true")
    monkeypatch.setenv("HERMES_CANDLE_FEATURE_D1_AUTHORISED", "true")
    monkeypatch.setenv("HERMES_LEVEL_D1_AUTHORISED", "true")
    steps.control_plane_step(fake)
    cat = json.loads(fake.kv[cp.KEY_CATALOG_CANDLES])["timeframes"]
    for tf in ("M1", "M5", "M15", "H1", "H4"):                              # M1-H4 catalog untouched
        assert cat[tf]["latest_status"] == cp.STATUS_ACTIVE and cat[tf]["history_status"] == cp.STATUS_ACTIVE
    m = json.loads(fake.kv[cp.KEY_CONTRACT_MANIFEST])                       # PR#86/#87 manifest truth unchanged
    assert m["active_families"]["indicators"].get("D1") == cp.STATUS_ACTIVE
    assert m["active_families"]["candle_latest"].get("D1") == cp.STATUS_ACTIVE
    assert "candle_latest_d1" not in m["gated_families"]
    assert fake.deletes == []                                              # no Redis deletes


def test_no_forbidden_semantics_or_source_in_new_helpers():
    import inspect
    src = "".join(inspect.getsource(getattr(steps, n)) for n in ("_d1_history_active", "_d1_forward_writer_active"))
    for tok in ("consumer_live", "falcon", "regime", "risk", "strategy", "signal", "trade",
                "pymysql", "get_db_config", "market_map", "candles_H4", ":D1:latest:"):
        assert tok not in src, f"catalog-truth helper must not reference {tok!r}"
