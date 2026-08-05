"""WO-HELM-HERMES-INDICATOR-PUBLICATION-WIRING-0001 — wire existing Bollinger(20,2), ADX(+DI/-DI), EMA50.
Reuses the existing indicator runner (indicator_step), calc lib (utils.indicators) + payload contract
(hermes_indicators_v1). No new service/runner/framework/Redis surface. No live runtime mutation.
"""
import datetime
import json

import pytest

from utils import hermes_runtime_publisher_steps_v1 as steps
from utils import hermes_indicators_v1 as ind
from utils import indicators as ic
from utils import atr_calculator


@pytest.fixture(autouse=True)
def _registry_adoption(monkeypatch):
    """WO-...-XAU-MODULE-ADOPTION-0001: adopted indicator/feed-health/quote runtime paths obtain instrument authority
    from the canonical registry. Patch the loader to the XAU-active rollout so these enabled-path wiring tests are
    registry-driven with no DB (behaviour asserted unchanged; XAU pilot active, 7 new NOT_ENABLED)."""
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
def test_ema50_matches_direct_calculation():
    c = _candles(60); closes = [x["close"] for x in c]
    assert steps._compute_indicators(c)["ema_50"] == round(ic.calculate_ema(closes, 50), 6)


def test_bollinger_matches_direct_calculation():
    c = _candles(60); closes = [x["close"] for x in c]
    bb = ic.calculate_bollinger_bands(closes, 20, 2.0)
    out = steps._compute_indicators(c)
    assert out["bollinger_upper_20_2"] == round(bb.upper, 6)
    assert out["bollinger_middle_20_2"] == round(bb.middle, 6)
    assert out["bollinger_lower_20_2"] == round(bb.lower, 6)


def test_adx_matches_direct_calculation():
    c = _candles(60)
    closes = [x["close"] for x in c]; highs = [x["high"] for x in c]; lows = [x["low"] for x in c]
    a = ic.calculate_adx(highs, lows, closes, 14)
    out = steps._compute_indicators(c)
    assert out["adx_14"] == round(a.adx, 4)
    assert out["adx_plus_di_14"] == round(a.plus_di, 4)
    assert out["adx_minus_di_14"] == round(a.minus_di, 4)


# --------------------------------------------------------------------------- payload fields + methods
def test_payload_contains_new_fields_and_declared_methods():
    c = _candles(60)
    inds = steps._compute_indicators(c)
    pay = ind.build_indicator_contract(instrument=INST, timeframe="H1",
                                       generated_at_utc=datetime.datetime(2026, 7, 15, 10, tzinfo=UTC),
                                       value_open_time_utc=datetime.datetime(2026, 7, 15, 9, tzinfo=UTC), indicators=inds)
    fields = pay["indicators"]
    for f in ("ema_50", "bollinger_upper_20_2", "bollinger_middle_20_2", "bollinger_lower_20_2",
              "adx_14", "adx_plus_di_14", "adx_minus_di_14"):
        assert f in fields
    m = pay["methods"]
    assert m["adx_method"] == "WILDER_ADX_14" and m["bands_method"] == "SMA_N_STD_2" and m["ema_method"].startswith("STANDARD")
    assert ind.validate_indicator_contract(pay) is True


def test_adx_is_a_recognised_family_trivial_extension():
    assert ind._family_of("adx_14") == "adx"
    assert ind._family_of("adx_plus_di_14") == "adx"
    assert "adx" in ind.HERMES_DETERMINISTIC_INDICATORS
    assert ind.INDICATOR_METHODS["adx"] == "WILDER_ADX_14" and ind._METHOD_FIELD["adx"] == "adx_method"


# --------------------------------------------------------------------------- insufficient depth -> explicit null (never fabricated)
def test_new_indicators_null_when_depth_insufficient():
    out = steps._compute_indicators(_candles(16))            # >=15 so existing publish; <20/<29/<50 for new
    assert out["ema_50"] is None
    assert out["bollinger_upper_20_2"] is None and out["bollinger_middle_20_2"] is None and out["bollinger_lower_20_2"] is None
    assert out["adx_14"] is None and out["adx_plus_di_14"] is None and out["adx_minus_di_14"] is None
    # existing indicators still present
    assert out["ema_12"] is not None and out["rsi_14"] is not None and out["atr_14"] is not None


def test_adx_never_publishes_neutral_fabrication_boundary():
    # calculate_adx returns neutral 20/20/20 below period+1; our guard (2*period+1=29) means < 29 -> null, >= 29 -> real
    assert steps._compute_indicators(_candles(28))["adx_14"] is None
    val = steps._compute_indicators(_candles(29))["adx_14"]
    assert val is not None and val != 20.0                   # a settled value, not the fabricated neutral


def test_bollinger_never_publishes_current_price_fabrication():
    assert steps._compute_indicators(_candles(19))["bollinger_middle_20_2"] is None
    assert steps._compute_indicators(_candles(20))["bollinger_middle_20_2"] is not None


def test_ema50_boundary():
    assert steps._compute_indicators(_candles(49))["ema_50"] is None
    assert steps._compute_indicators(_candles(50))["ema_50"] is not None


# --------------------------------------------------------------------------- regression: existing behaviour preserved
def test_existing_indicators_unchanged():
    c = _candles(60); closes = [x["close"] for x in c]
    out = steps._compute_indicators(c)
    assert out["ema_12"] == round(ic.calculate_ema(closes, 12), 6)
    assert out["ema_26"] == round(ic.calculate_ema(closes, 26), 6)
    assert out["rsi_14"] == round(ic.calculate_rsi(closes, 14), 4)
    assert out["atr_14"] == round(atr_calculator.calculate_atr(c, 14), 6)


def test_no_forbidden_indicators_emitted():
    out = steps._compute_indicators(_candles(60))
    for banned in ("ema_200", "macd", "vwap", "sweep"):
        assert not any(banned in k for k in out), f"{banned} must not be emitted"


# --------------------------------------------------------------------------- no new framework / key surface
def test_no_new_key_family_or_runner():
    # key remains the existing versioned per-TF indicator key; no new family constant
    assert ind.indicator_key("XAU_USD", "H1") == "hermes:indicators:XAU_USD:H1:v1"
    # the runner is still the single existing indicator_step + it is wired at 60s cadence
    from utils import hermes_publisher_runtime_v1 as pubrt
    specs = {s[0]: s for s in pubrt.default_runner_specs()}
    assert "indicators" in specs and specs["indicators"][2] == pubrt.DEFAULT_INTERVAL_SECONDS == 60


def test_reuses_existing_calc_library_not_a_new_one():
    # the step reuses utils.indicators (ind_compute) + atr_calculator; no new calc module imported
    src = open("utils/hermes_runtime_publisher_steps_v1.py").read()
    assert "from utils import indicators as ind_compute" in src
    assert "ind_compute.calculate_bollinger_bands" in src and "ind_compute.calculate_adx" in src and "ind_compute.calculate_ema" in src


# --------------------------------------------------------------------------- hydration compatibility (PR #64/#65 warm-start path)
class FakeRedis:
    """Minimal governed-history + kv fake. Reuses the EXISTING history key format hermes:candles:XAU_USD:{tf}:history:v1
    (PR #64/#65 warm-start path) — no new hydration store."""
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
    """Warm-start a governed history series exactly as PR #64/#65 hydration would leave it."""
    idx = f"hermes:candles:{INST}:{tf}:history:v1:index"
    base = 1_700_000_000
    for i in range(n):
        ep = base + i * 3600
        r.zadd(idx, {str(ep): ep})
        r.kv[f"hermes:candles:{INST}:{tf}:history:v1:{ep}"] = json.dumps({"data": _candle(i)})
    r.kv[f"hermes:candles:{INST}:{tf}:latest:v1"] = json.dumps({"status": "OK"})


def test_hydration_compatibility_deep_history_yields_full_indicators():
    r = FakeRedis(); _seed_history(r, "H1", 60)
    candles = steps._read_history(r, "H1", steps.INDICATOR_WINDOW)
    assert len(candles) == 60                                 # warm-started history read via existing path
    out = steps._compute_indicators(candles)
    assert out["ema_50"] is not None and out["bollinger_middle_20_2"] is not None and out["adx_14"] is not None


# --------------------------------------------------------------------------- two-observation publication (no live runtime)
def test_two_observation_publication_deterministic(monkeypatch):
    monkeypatch.setenv(ind.ENABLED_ENV, "true")
    monkeypatch.setenv(ind.AUTHORISED_ENV, "true")
    monkeypatch.setenv(ind.INSTRUMENTS_ENV, "XAU_USD")
    monkeypatch.setenv(ind.TIMEFRAMES_ENV, "M1,M5,M15,H1,H4")
    monkeypatch.delenv(ind.D1_AUTHORISED_ENV, raising=False)
    r = FakeRedis()
    for tf in ("M1", "M5", "M15", "H1", "H4"):
        _seed_history(r, tf, 60)

    def _published_indicator_payloads():
        return {k: json.loads(v) for k, v in r.kv.items() if ":indicators:" in k}

    res1 = steps.indicator_step(r)
    keys1 = set(_published_indicator_payloads())
    res2 = steps.indicator_step(r)
    payloads2 = _published_indicator_payloads()

    assert res1["published"] == 5 and res2["published"] == 5
    assert keys1 == {f"hermes:indicators:XAU_USD:{tf}:v1" for tf in ("M1", "M5", "M15", "H1", "H4")}
    # every published payload carries the new fields with declared methods, and validates
    for k, p in payloads2.items():
        for f in ("ema_50", "bollinger_upper_20_2", "adx_14", "adx_plus_di_14", "ema_12", "rsi_14", "atr_14"):
            assert f in p["indicators"], f"{f} missing in {k}"
        assert p["methods"]["adx_method"] == "WILDER_ADX_14"
        assert ind.validate_indicator_contract(p) is True
    # deterministic across the two observations (same input history -> identical indicator values)
    h1_a = payloads2["hermes:indicators:XAU_USD:H1:v1"]["indicators"]
    res3 = steps.indicator_step(r)  # noqa: F841 (third obs to confirm stability)
    h1_c = _published_indicator_payloads()["hermes:indicators:XAU_USD:H1:v1"]["indicators"]
    assert h1_a == h1_c
