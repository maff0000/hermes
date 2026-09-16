"""WO-HELM-HERMES-FAST-TRACK-HELIOS-INDICATOR-CONTRACT-COMPLETION-0001 — wire ema_9, ema_21, ema_200 onto the
existing indicator runner (indicator_step) so HELIOS's canonical surface (ema_9/21/50/200 + rsi_14 + atr_14) is
satisfiable, in particular GoldenCrossAtom.REQUIRED_FIELDS = ("close", "ema_50", "ema_200"). Reuses the existing
calc lib (utils.indicators.calculate_ema) + payload contract (hermes_indicators_v1). No new service/runner/
framework/Redis surface. No regime/risk/decision fields. No live runtime mutation.
"""
import datetime
import json

import pytest

from utils import hermes_runtime_publisher_steps_v1 as steps
from utils import hermes_indicators_v1 as ind
from utils import indicators as ic


@pytest.fixture(autouse=True)
def _registry_adoption(monkeypatch):
    from tests.test_hermes_instrument_registry_v1 import rollout_rows
    import utils.hermes_instrument_registry_v1 as reg
    recs = reg.load_registry(rollout_rows())
    monkeypatch.setattr(reg, "load_from_db", lambda fetch=None: recs)


UTC = datetime.timezone.utc
INST = "XAU_USD"


def _candle(i, base=2000.0):
    p = base + (i % 9) * 0.7 - (i % 4) * 0.5 + i * 0.01
    return {"open": round(p, 5), "high": round(p + 1.3, 5), "low": round(p - 1.15, 5),
            "close": round(p + 0.35, 5), "timestamp_utc": "2026-07-15T09:%02d:00.000Z" % (i % 60),
            "candle_direction": "up"}


def _candles(n):
    return [_candle(i) for i in range(n)]


# --------------------------------------------------------------------------- calculation matches direct calls
def test_ema9_matches_direct_calculation():
    c = _candles(60)
    closes_tail = [x["close"] for x in c[-steps.INDICATOR_WINDOW:]]
    assert steps._compute_indicators(c)["ema_9"] == round(ic.calculate_ema(closes_tail, 9), 6)


def test_ema21_matches_direct_calculation():
    c = _candles(60)
    closes_tail = [x["close"] for x in c[-steps.INDICATOR_WINDOW:]]
    assert steps._compute_indicators(c)["ema_21"] == round(ic.calculate_ema(closes_tail, 21), 6)


def test_ema200_matches_direct_calculation():
    c = _candles(steps.EMA_TREND_WINDOW)
    closes = [x["close"] for x in c]
    assert steps._compute_indicators(c)["ema_200"] == round(ic.calculate_ema(closes, 200), 6)


# --------------------------------------------------------------------------- warm-up boundary / insufficient history
def test_ema9_boundary():
    assert steps._compute_indicators(_candles(8))["ema_9"] is None
    assert steps._compute_indicators(_candles(9))["ema_9"] is not None


def test_ema21_boundary():
    assert steps._compute_indicators(_candles(20))["ema_21"] is None
    assert steps._compute_indicators(_candles(21))["ema_21"] is not None


def test_ema200_boundary():
    assert steps._compute_indicators(_candles(199))["ema_200"] is None
    assert steps._compute_indicators(_candles(200))["ema_200"] is not None


def test_ema200_insufficient_history_never_fabricated():
    # H4/D1-shaped shallow supply (well under 200) must publish an explicit null, never zero/guessed/stale.
    out = steps._compute_indicators(_candles(69))         # matches DEV H4 Redis history depth at time of writing
    assert out["ema_200"] is None
    # existing + new short-period indicators still compute fine at this depth
    assert out["ema_12"] is not None and out["ema_9"] is not None and out["ema_21"] is not None


# --------------------------------------------------------------------------- deeper supply never perturbs the
# --------------------------------------------------------------------------- existing INDICATOR_WINDOW family
def test_deeper_supply_does_not_change_existing_indicators():
    c210 = _candles(steps.EMA_TREND_WINDOW)
    tail = c210[-60:]                          # the exact 60 candles a bare INDICATOR_WINDOW read would return
    out_from_60 = steps._compute_indicators(tail)     # caller supplies only the shallow 60-candle window
    out_from_210 = steps._compute_indicators(c210)    # caller supplies the full 210-candle window
    for f in ("ema_12", "ema_26", "ema_50", "ema_9", "ema_21", "rsi_14", "atr_14",
              "bollinger_upper_20_2", "bollinger_middle_20_2", "bollinger_lower_20_2",
              "adx_14", "adx_plus_di_14", "adx_minus_di_14"):
        assert out_from_60[f] == out_from_210[f], f"{f} changed when deeper history was supplied"
    # only ema_200 differs (present with deep supply, absent/None with the shallow one)
    assert out_from_60["ema_200"] is None
    assert out_from_210["ema_200"] is not None


# --------------------------------------------------------------------------- payload fields + declared methods
def test_payload_contains_new_fields_and_declared_methods():
    c = _candles(steps.EMA_TREND_WINDOW)
    inds = steps._compute_indicators(c)
    pay = ind.build_indicator_contract(instrument=INST, timeframe="H1",
                                       generated_at_utc=datetime.datetime(2026, 7, 15, 10, tzinfo=UTC),
                                       value_open_time_utc=datetime.datetime(2026, 7, 15, 9, tzinfo=UTC), indicators=inds)
    fields = pay["indicators"]
    for f in ("ema_9", "ema_21", "ema_50", "ema_200", "rsi_14", "atr_14"):
        assert f in fields
    assert pay["methods"]["ema_method"] == "STANDARD_2_OVER_N_PLUS_1_SMA_SEED"
    assert ind.validate_indicator_contract(pay) is True


def test_no_regime_or_forbidden_fields_introduced():
    out = steps._compute_indicators(_candles(steps.EMA_TREND_WINDOW))
    for banned in ("regime", "risk", "decision", "trade", "gate"):
        assert not any(banned in k for k in out), f"{banned} must not be emitted"


# --------------------------------------------------------------------------- regression: existing behaviour preserved
def test_existing_indicators_unchanged_with_standard_60_supply():
    c = _candles(60); closes = [x["close"] for x in c]
    out = steps._compute_indicators(c)
    assert out["ema_12"] == round(ic.calculate_ema(closes, 12), 6)
    assert out["ema_26"] == round(ic.calculate_ema(closes, 26), 6)
    assert out["ema_50"] == round(ic.calculate_ema(closes, 50), 6)


# --------------------------------------------------------------------------- restart / reinitialisation
# The runner recomputes stateless from the governed Redis history read on every cycle (no in-process EMA state
# carried across restarts for ANY existing indicator) — so ema_9/21/200 inherit the same restart-safe semantics
# as ema_12/26/50 automatically: a fresh process re-reads history and reproduces an identical value.
def test_restart_reinitialisation_is_deterministic_from_history_alone():
    c = _candles(steps.EMA_TREND_WINDOW)
    before_restart = steps._compute_indicators(c)
    # simulate a restart: fresh call, same history read, no shared process state
    after_restart = steps._compute_indicators(list(c))
    assert before_restart == after_restart


# --------------------------------------------------------------------------- hydration / two-observation publication
class FakeRedis:
    def __init__(self):
        self.kv = {}
        self.z = {}
        self.writes = []

    def zadd(self, key, mapping):
        self.z.setdefault(key, {}).update(mapping)

    def zrevrange(self, key, start, end):
        items = sorted(self.z.get(key, {}).items(), key=lambda kv: kv[1], reverse=True)
        sl = items[start:(end + 1 if end != -1 else None)]
        return [m for m, _ in sl]

    def get(self, key):
        return self.kv.get(key)

    def set(self, key, val, ex=None):
        self.kv[key] = val
        self.writes.append(key)


def _seed_history(r, tf, n):
    idx = f"hermes:candles:{INST}:{tf}:history:v1:index"
    base = 1_700_000_000
    for i in range(n):
        ep = base + i * 3600
        r.zadd(idx, {str(ep): ep})
        r.kv[f"hermes:candles:{INST}:{tf}:history:v1:{ep}"] = json.dumps({"data": _candle(i)})
    r.kv[f"hermes:candles:{INST}:{tf}:latest:v1"] = json.dumps({"status": "OK"})


def test_hydration_with_deep_history_yields_ema200():
    r = FakeRedis(); _seed_history(r, "H1", steps.EMA_TREND_WINDOW)
    candles = steps._read_history(r, "H1", steps.EMA_TREND_WINDOW)
    assert len(candles) == steps.EMA_TREND_WINDOW
    out = steps._compute_indicators(candles)
    assert out["ema_200"] is not None and out["ema_9"] is not None and out["ema_21"] is not None


def test_hydration_with_shallow_history_reports_ema200_null_never_fabricated():
    r = FakeRedis(); _seed_history(r, "H4", 69)   # matches DEV H4 Redis history depth at time of writing
    candles = steps._read_history(r, "H4", steps.EMA_TREND_WINDOW)   # requests 210, source only has 69
    assert len(candles) == 69
    out = steps._compute_indicators(candles)
    assert out["ema_200"] is None
    assert out["ema_9"] is not None and out["ema_21"] is not None and out["ema_50"] is not None


def test_two_observation_publication_deterministic(monkeypatch):
    monkeypatch.setenv(ind.ENABLED_ENV, "true")
    monkeypatch.setenv(ind.AUTHORISED_ENV, "true")
    monkeypatch.setenv(ind.INSTRUMENTS_ENV, "XAU_USD")
    monkeypatch.setenv(ind.TIMEFRAMES_ENV, "M1,M5,M15,H1,H4")
    monkeypatch.delenv(ind.D1_AUTHORISED_ENV, raising=False)
    r = FakeRedis()
    for tf in ("M1", "M5", "M15", "H1"):
        _seed_history(r, tf, steps.EMA_TREND_WINDOW)   # deep enough for ema_200
    _seed_history(r, "H4", 69)                          # shallow, matches real DEV H4 depth -> ema_200 null

    def _published_indicator_payloads():
        return {k: json.loads(v) for k, v in r.kv.items() if ":indicators:" in k}

    res1 = steps.indicator_step(r)
    payloads1 = _published_indicator_payloads()
    assert res1["published"] == 5

    for tf in ("M1", "M5", "M15", "H1"):
        p = payloads1[f"hermes:indicators:XAU_USD:{tf}:v1"]["indicators"]
        assert p["ema_9"] is not None and p["ema_21"] is not None and p["ema_200"] is not None
        assert p["ema_50"] is not None and p["rsi_14"] is not None and p["atr_14"] is not None

    h4 = payloads1["hermes:indicators:XAU_USD:H4:v1"]["indicators"]
    assert h4["ema_9"] is not None and h4["ema_21"] is not None and h4["ema_50"] is not None
    assert h4["ema_200"] is None   # genuinely insufficient H4 depth -> explicit null, never fabricated

    # deterministic across repeated observations (same input history -> identical values)
    res2 = steps.indicator_step(r)  # noqa: F841
    payloads2 = _published_indicator_payloads()
    assert payloads1["hermes:indicators:XAU_USD:H1:v1"]["indicators"] == \
        payloads2["hermes:indicators:XAU_USD:H1:v1"]["indicators"]
