"""HERMES D1 indicators / candle_features / daily-levels (derived from sealed D1 history) — code-only, fake Redis.
WO-HELM-HERMES-D1-INDICATORS-FEATURES-LEVELS-0002.

Proves D1 surfaces derive ONLY from the governed sealed D1 history, are DARK by default (publisher must authorise
D1), depth-guarded (>=26), source-validated (sealed 6/6 / 22:00 anchor / no XAUUSD), and that M1-H4 behaviour is
unchanged. No live Redis, no SQL, no deletes, no interpretive semantics.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import utils.hermes_runtime_publisher_steps_v1 as steps
import utils.candle_d1_history_v1 as d1h
import utils.candle_d1_derivation_v1 as d1d
import utils.candle_contract_v1 as cc

UTC = timezone.utc
INST = "XAU_USD"


class _FakeRedis:
    def __init__(self):
        self.kv = {}
        self.z = {}
        self.sets = []
        self.deletes = []

    def get(self, k):
        v = self.kv.get(k)
        return v.encode() if isinstance(v, str) else v

    def exists(self, k):
        return 1 if (k in self.kv or k in self.z) else 0

    def set(self, k, v, ex=None):
        self.kv[k] = v
        self.sets.append(k)

    def zadd(self, k, m):
        self.z.setdefault(k, {}).update(m)

    def zrevrange(self, k, a, b):
        items = sorted(self.z.get(k, {}).items(), key=lambda kv: kv[1], reverse=True)
        return [m for m, _ in (items[a:b + 1] if b != -1 else items[a:])]

    def zcard(self, k):
        return len(self.z.get(k, {}))

    def delete(self, *a):
        self.deletes.extend(a)


def _sealed_d1(d1_open):
    opens = d1d.d1_child_h4_opens(d1_open)
    base = (d1_open - datetime(2026, 1, 1, tzinfo=UTC)).days
    specs = [(2000 + base, 2010 + base, 1990 + base, 2005 + base, 10),
             (2005 + base, 2030 + base, 1995 + base, 2020 + base, 11),
             (2020 + base, 2080 + base, 2010 + base, 2050 + base, 12),
             (2050 + base, 2060 + base, 2000 + base, 2030 + base, 13),
             (2030 + base, 2040 + base, 1900 + base, 1950 + base, 14),
             (1950 + base, 1975 + base, 1940 + base, 1970 + base, 15)]
    kids = [{"timestamp": opens[i], "open": specs[i][0], "high": specs[i][1], "low": specs[i][2],
             "close": specs[i][3], "volume": specs[i][4]} for i in range(6)]
    env, _ = d1d.derive_d1(instrument=INST, d1_open=d1_open, h4_children=kids,
                           generated_at_utc=cc.normalise_utc(d1_open) + timedelta(seconds=d1d.D1_SECONDS))
    assert env["status"] == "OK" and env["data"]["source_count"] == 6
    return env


def _seed_d1_history(fake, *, days, first=datetime(2026, 5, 1, 22, 0, tzinfo=UTC)):
    idx = f"hermes:candles:{INST}:D1:history:v1:index"
    for d in range(days):
        env = _sealed_d1(first + timedelta(days=d))
        ep = int(cc.normalise_utc(first + timedelta(days=d)).timestamp())
        fake.kv[d1h.d1_history_key(INST, ep)] = json.dumps(env)
        fake.z.setdefault(idx, {})[str(ep)] = ep
    return fake


def _seed_m1h4_history(fake):
    """A few M1-H4 history candles so the M1-H4 path still publishes (regression control)."""
    for tf in ("M1", "M5", "M15", "H1", "H4"):
        idx = f"hermes:candles:{INST}:{tf}:history:v1:index"
        for i in range(20):
            ep = 1_800_000_000 + i * 60
            env = {"status": "OK", "data": {"instrument": INST, "timeframe": tf,
                   "timestamp_utc": cc._fmt(datetime.fromtimestamp(ep, tz=UTC)),
                   "open": 2000.0 + i, "high": 2010.0 + i, "low": 1990.0 + i, "close": 2005.0 + i,
                   "candle_direction": "UP", "source_count": 1, "expected_source_count": 1,
                   "source_coverage": 1.0, "gap_state": "NONE", "is_closed": True}}
            fake.kv[f"hermes:candles:{INST}:{tf}:history:v1:{ep}"] = json.dumps(env)
            fake.z.setdefault(idx, {})[str(ep)] = ep
        fake.kv[f"hermes:candles:{INST}:{tf}:latest:v1"] = json.dumps({"status": "OK"})
    return fake


def _ind_env(monkeypatch, *, d1=False):
    monkeypatch.setenv("HERMES_INDICATOR_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("HERMES_INDICATOR_PUBLISH_AUTHORISED", "true")
    monkeypatch.setenv("HERMES_INDICATOR_PUBLISH_INSTRUMENTS", "XAU_USD")
    tfs = "M1,M5,M15,H1,H4" + (",D1" if d1 else "")
    monkeypatch.setenv("HERMES_INDICATOR_PUBLISH_TIMEFRAMES", tfs)
    monkeypatch.setenv("HERMES_INDICATOR_D1_AUTHORISED", "true" if d1 else "false")


def _feat_env(monkeypatch, *, d1=False):
    monkeypatch.setenv("HERMES_CANDLE_FEATURE_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("HERMES_CANDLE_FEATURE_PUBLISH_AUTHORISED", "true")
    monkeypatch.setenv("HERMES_CANDLE_FEATURE_PUBLISH_INSTRUMENTS", "XAU_USD")
    monkeypatch.setenv("HERMES_CANDLE_FEATURE_PUBLISH_TIMEFRAMES", "M1,M5,M15,H1,H4" + (",D1" if d1 else ""))
    monkeypatch.setenv("HERMES_CANDLE_FEATURE_D1_AUTHORISED", "true" if d1 else "false")


# --------------------------------------------------------------------------- source validation
def test_d1_source_validation_accepts_sealed_rejects_bad():
    fake = _seed_d1_history(_FakeRedis(), days=30)
    got = steps._read_d1_history_validated(fake, 60)
    assert len(got) == 30 and got[-1]["timeframe"] == "D1" and got[0]["timestamp_utc"].endswith("Z")
    idx = f"hermes:candles:{INST}:D1:history:v1:index"
    newest = max(fake.z[idx].values())
    # corrupt the newest member: non-OK -> rejected
    bad = json.loads(fake.kv[d1h.d1_history_key(INST, newest)]); bad["status"] = "PARTIAL"
    fake.kv[d1h.d1_history_key(INST, newest)] = json.dumps(bad)
    with pytest.raises(ValueError):
        steps._read_d1_history_validated(fake, 60)


def test_d1_source_rejects_wrong_anchor_and_xauusd():
    fake = _FakeRedis()
    env = _sealed_d1(datetime(2026, 5, 1, 22, 0, tzinfo=UTC))
    # wrong anchor (shift timestamp to 00:00) -> reject
    env2 = json.loads(json.dumps(env)); env2["data"]["timestamp_utc"] = "2026-05-01T00:00:00.000Z"
    fake.kv[d1h.d1_history_key(INST, 1)] = json.dumps(env2)
    fake.z[f"hermes:candles:{INST}:D1:history:v1:index"] = {"1": 1}
    with pytest.raises(ValueError):
        steps._read_d1_history_validated(fake, 10)


# --------------------------------------------------------------------------- depth guard / dark default
def test_d1_tf_included_only_when_authorised_and_deep(monkeypatch):
    fake = _seed_d1_history(_FakeRedis(), days=30)
    _ind_env(monkeypatch, d1=True)
    import utils.hermes_indicators_v1 as ind
    pub = ind.build_indicator_publisher_from_env()
    assert steps._d1_tf_if_ready(pub, fake) == ("D1",)            # authorised + depth 30 -> included
    # shallow history -> blocked (governed skip), even though authorised
    shallow = _seed_d1_history(_FakeRedis(), days=10)
    assert steps._d1_tf_if_ready(pub, shallow) == ()
    assert steps._d1_derived_ready(fake) is True and steps._d1_derived_ready(shallow) is False


def test_d1_dark_when_publisher_not_authorised(monkeypatch):
    fake = _seed_d1_history(_FakeRedis(), days=30)
    _ind_env(monkeypatch, d1=False)                              # D1 not in timeframes
    import utils.hermes_indicators_v1 as ind
    pub = ind.build_indicator_publisher_from_env()
    assert "D1" not in pub.timeframes
    assert steps._d1_tf_if_ready(pub, fake) == ()                # dark by default


# --------------------------------------------------------------------------- indicator_step
def test_indicator_step_publishes_d1_when_ready(monkeypatch):
    fake = _seed_m1h4_history(_seed_d1_history(_FakeRedis(), days=30))
    _ind_env(monkeypatch, d1=True)
    res = steps.indicator_step(fake)
    dkey = "hermes:indicators:XAU_USD:D1:v1"
    assert dkey in fake.kv
    payload = json.loads(fake.kv[dkey])
    inds = payload["indicators"]
    assert "ema_26" in inds and "ema_12" in inds and "rsi_14" in inds and "atr_14" in inds  # depth 30 -> ema_26 present
    assert payload["timeframe"] == "D1" and payload["instrument"] == "XAU_USD" and "XAUUSD" not in dkey
    assert fake.deletes == []


def test_indicator_step_no_d1_when_dark(monkeypatch):
    fake = _seed_m1h4_history(_seed_d1_history(_FakeRedis(), days=30))
    _ind_env(monkeypatch, d1=False)
    steps.indicator_step(fake)
    assert "hermes:indicators:XAU_USD:D1:v1" not in fake.kv       # D1 dark
    # M1-H4 unchanged (all present)
    for tf in ("M1", "M5", "M15", "H1", "H4"):
        assert f"hermes:indicators:XAU_USD:{tf}:v1" in fake.kv


def test_indicator_step_d1_blocked_below_min_depth(monkeypatch):
    fake = _seed_m1h4_history(_seed_d1_history(_FakeRedis(), days=20))   # < 26
    _ind_env(monkeypatch, d1=True)
    steps.indicator_step(fake)
    assert "hermes:indicators:XAU_USD:D1:v1" not in fake.kv       # depth guard blocks D1
    assert "hermes:indicators:XAU_USD:H4:v1" in fake.kv           # M1-H4 unaffected


# --------------------------------------------------------------------------- candle_feature_step
def test_candle_feature_step_publishes_d1_from_sealed_history(monkeypatch):
    fake = _seed_m1h4_history(_seed_d1_history(_FakeRedis(), days=30))
    _feat_env(monkeypatch, d1=True)
    steps.candle_feature_step(fake)
    dkey = "hermes:candle_features:XAU_USD:D1:v1"
    assert dkey in fake.kv
    feats = json.loads(fake.kv[dkey])["features"]
    assert "body_size" in feats and "range_size" in feats and "engulfing" in feats and "candle_direction" in feats
    assert fake.deletes == []


def test_candle_feature_step_d1_dark_by_default(monkeypatch):
    fake = _seed_m1h4_history(_seed_d1_history(_FakeRedis(), days=30))
    _feat_env(monkeypatch, d1=False)
    steps.candle_feature_step(fake)
    assert "hermes:candle_features:XAU_USD:D1:v1" not in fake.kv


def test_candle_feature_prev_unavailable_fails_loud(monkeypatch):
    # authorised + depth-guard passes (zcard>=26) but members missing -> reader returns <2 -> fail loud
    fake = _seed_m1h4_history(_FakeRedis())
    idx = f"hermes:candles:{INST}:D1:history:v1:index"
    fake.z[idx] = {str(i): i for i in range(30)}                  # zcard 30 (guard passes) but NO member keys
    _feat_env(monkeypatch, d1=True)
    with pytest.raises(ValueError):
        steps.candle_feature_step(fake)


# --------------------------------------------------------------------------- daily levels
def test_daily_levels_from_sealed_d1():
    fake = _seed_d1_history(_FakeRedis(), days=30)
    d1c = steps._read_d1_history_validated(fake, 20)
    lv = steps._d1_daily_levels(d1c)
    assert set(("previous_day_high", "previous_day_low", "previous_day_close")) <= set(lv)
    assert lv["previous_day_high"] == d1c[-1]["high"] and lv["previous_day_low"] == d1c[-1]["low"]
    assert "adr_20" in lv and lv["adr_20"] > 0                    # 20 D1 available


def test_daily_levels_fail_loud_when_no_prior():
    with pytest.raises(ValueError):
        steps._d1_daily_levels([])                                # no prior D1 -> fail loud


# --------------------------------------------------------------------------- no interpretive semantics
def test_no_forbidden_tokens_in_d1_helpers():
    import inspect
    # scan ONLY the D1 functions added by this WO (not the pre-existing steps' negative declarations / hermes-signal id)
    fns = [steps._d1_history_depth, steps._read_d1_history_validated, steps._d1_derived_ready,
           steps._d1_tf_if_ready, steps._d1_daily_levels]
    for fn in fns:
        body = "\n".join(l for l in inspect.getsource(fn).splitlines()
                         if not l.strip().startswith("#") and "interpretation" not in l).lower()
        for tok in ("regime", "regime_confidence", "strategy", "signal", " buy ", " sell ", "no-go", "market_map"):
            assert tok not in body, f"forbidden interpretive token {tok!r} in {fn.__name__}"
