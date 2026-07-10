"""HERMES control-plane manifest truth for live D1 surfaces — code-only, fake Redis.
WO-HELM-HERMES-D1-MANIFEST-TRUTH-0001.

Proves the control-plane manifest reflects D1 indicators/candle_features/daily-levels as ACTIVE when their D1 gate
is true AND their D1 key is live, removing them from gated_families; and keeps them gated (no overclaim) when the
gate is false or the key is absent. M1-H4 manifest behaviour unchanged. Derived from explicit gates + live state only.
"""
import json

import pytest

import utils.hermes_runtime_publisher_steps_v1 as steps
import utils.hermes_control_plane_v1 as cp
import utils.hermes_sessions_v1 as sess
import utils.hermes_levels_v1 as lvl

INST = "XAU_USD"


class _FakeRedis:
    def __init__(self):
        self.kv = {}
        self.sets = []
        self.deletes = []

    def get(self, k):
        v = self.kv.get(k)
        return v.encode() if isinstance(v, str) else v

    def exists(self, k):
        return 1 if k in self.kv else 0

    def set(self, k, v, ex=None):
        self.kv[k] = v
        self.sets.append(k)

    def delete(self, *a):
        self.deletes.extend(a)


def _seed_base(fake):
    """M1-H4 indicators + candle_features + session/intraday levels + D1 latest (for heartbeat d1_state)."""
    for tf in ("M1", "M5", "M15", "H1", "H4"):
        fake.kv[f"hermes:indicators:{INST}:{tf}:v1"] = "{}"
        fake.kv[f"hermes:candle_features:{INST}:{tf}:v1"] = "{}"
    fake.kv[sess.sessions_key(INST)] = "{}"
    fake.kv[lvl.levels_key(INST, "session")] = "{}"
    fake.kv[lvl.levels_key(INST, "intraday")] = "{}"
    fake.kv[f"hermes:candles:{INST}:D1:latest:v1"] = "{}"
    return fake


def _seed_d1_surfaces(fake, *, ind=True, feat=True, daily=True):
    if ind:
        fake.kv[f"hermes:indicators:{INST}:D1:v1"] = "{}"
    if feat:
        fake.kv[f"hermes:candle_features:{INST}:D1:v1"] = "{}"
    if daily:
        fake.kv[lvl.levels_key(INST, "daily")] = "{}"
    return fake


def _cp_env(monkeypatch, *, ind_d1="false", feat_d1="false", lvl_d1="false"):
    monkeypatch.setenv("HERMES_REDIS_CONTROL_PLANE_ENABLED", "true")
    monkeypatch.setenv("HERMES_REDIS_CONTROL_PLANE_AUTHORISED", "true")
    monkeypatch.setenv("HERMES_INDICATOR_D1_AUTHORISED", ind_d1)
    monkeypatch.setenv("HERMES_CANDLE_FEATURE_D1_AUTHORISED", feat_d1)
    monkeypatch.setenv("HERMES_LEVEL_D1_AUTHORISED", lvl_d1)


def _manifest(fake):
    steps.control_plane_step(fake)
    return json.loads(fake.kv[cp.KEY_CONTRACT_MANIFEST])


# --------------------------------------------------------------------------- active when gate true + key live
def test_d1_reflected_active_when_authorised_and_live(monkeypatch):
    fake = _seed_d1_surfaces(_seed_base(_FakeRedis()), ind=True, feat=True, daily=True)
    _cp_env(monkeypatch, ind_d1="true", feat_d1="true", lvl_d1="true")
    m = _manifest(fake)
    assert m["active_families"]["indicators"].get("D1") == cp.STATUS_ACTIVE
    assert m["active_families"]["candle_features"].get("D1") == cp.STATUS_ACTIVE
    assert m["active_families"]["levels"].get("daily") == cp.STATUS_ACTIVE
    # removed from gated_families when active
    gf = m["gated_families"]
    assert "indicators_d1" not in gf and "candle_features_d1" not in gf and "levels_d1" not in gf
    # M1-H4 unchanged
    for tf in ("M1", "M5", "M15", "H1", "H4"):
        assert m["active_families"]["indicators"][tf] == cp.STATUS_ACTIVE
        assert m["active_families"]["candle_features"][tf] == cp.STATUS_ACTIVE
    assert m["active_families"]["levels"]["session"] == cp.STATUS_ACTIVE
    assert m["active_families"]["levels"]["intraday"] == cp.STATUS_ACTIVE


# --------------------------------------------------------------------------- gated when gate false (no overclaim)
def test_d1_gated_when_gate_false(monkeypatch):
    fake = _seed_d1_surfaces(_seed_base(_FakeRedis()), ind=True, feat=True, daily=True)  # keys present (residual)
    _cp_env(monkeypatch, ind_d1="false", feat_d1="false", lvl_d1="false")               # but gates false
    m = _manifest(fake)
    assert "D1" not in m["active_families"]["indicators"]                                # NOT active (no overclaim)
    assert "D1" not in m["active_families"]["candle_features"]
    assert "daily" not in m["active_families"]["levels"]
    gf = m["gated_families"]
    assert gf["indicators_d1"]["status"] == cp.STATUS_GATED
    assert gf["candle_features_d1"]["status"] == cp.STATUS_GATED
    assert gf["levels_d1"]["status"] == cp.STATUS_GATED


# --------------------------------------------------------------------------- gated when gate true but key absent
def test_d1_gated_when_authorised_but_not_yet_published(monkeypatch):
    fake = _seed_d1_surfaces(_seed_base(_FakeRedis()), ind=False, feat=False, daily=False)  # no D1 keys yet
    _cp_env(monkeypatch, ind_d1="true", feat_d1="true", lvl_d1="true")                       # authorised, warming up
    m = _manifest(fake)
    assert "D1" not in m["active_families"]["indicators"]                                    # conservative warm-up
    assert "daily" not in m["active_families"]["levels"]
    gf = m["gated_families"]
    assert gf["indicators_d1"]["status"] == cp.STATUS_GATED
    assert gf["levels_d1"]["status"] == cp.STATUS_GATED


# --------------------------------------------------------------------------- per-family independence
def test_partial_d1_authorisation_only_marks_that_family(monkeypatch):
    fake = _seed_d1_surfaces(_seed_base(_FakeRedis()), ind=True, feat=True, daily=True)
    _cp_env(monkeypatch, ind_d1="true", feat_d1="false", lvl_d1="false")                 # only indicators authorised
    m = _manifest(fake)
    assert m["active_families"]["indicators"].get("D1") == cp.STATUS_ACTIVE
    assert "D1" not in m["active_families"]["candle_features"]
    assert "daily" not in m["active_families"]["levels"]
    assert "indicators_d1" not in m["gated_families"]
    assert m["gated_families"]["candle_features_d1"]["status"] == cp.STATUS_GATED
    assert m["gated_families"]["levels_d1"]["status"] == cp.STATUS_GATED


# --------------------------------------------------------------------------- health + no consumer/Falcon leakage
def test_health_levels_d1_active_and_no_deletes(monkeypatch):
    fake = _seed_d1_surfaces(_seed_base(_FakeRedis()), ind=True, feat=True, daily=True)
    _cp_env(monkeypatch, ind_d1="true", feat_d1="true", lvl_d1="true")
    steps.control_plane_step(fake)
    health = json.loads(fake.kv[cp.KEY_HEALTH])
    assert health["per_family_health"].get("levels_d1") == cp.STATUS_ACTIVE
    assert fake.deletes == []                                                            # no Redis deletes
    # this WO's added code introduces NO consumer/Falcon/interpretive semantics (pre-existing manifest boundary
    # declarations are HERMES's honest 'hermes_must_not' fields, out of scope here)
    import inspect
    added = "".join(inspect.getsource(getattr(steps, n)) for n in
                    ("_d1_authorised", "_d1_family_active", "_daily_levels_active")).lower()
    for tok in ("consumer_live", "falcon", "regime", "risk", "strategy", "signal", "trade"):
        assert tok not in added, f"manifest-truth helpers must not add {tok!r}"


# --------------------------------------------------------------------------- source-path unchanged
def test_no_d1_source_path_drift():
    import inspect
    # the fix reads only key-existence + env gate; it must not introduce any candle source path
    added = inspect.getsource(steps._d1_family_active) + inspect.getsource(steps._daily_levels_active) \
        + inspect.getsource(steps._d1_authorised)
    for bad in ("pymysql", "get_db_config", "candles_H4", "candles_M30", ":D1:latest:", "market_map", "XAUUSD"):
        assert bad not in added, f"manifest-truth helpers must not reference {bad!r}"
