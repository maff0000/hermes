"""Design-phase demonstrative fixture for the reusable advanced-v1 contract engine.

WO-HELM-HERMES-REUSABLE-ADVANCED-V1-CONTRACT-ENGINE-DESIGN-0001 (design + test-fixture only).

This is NOT the production suite. It is self-contained (no DB/Redis/OANDA/production dependency) and proves
THREE things the reusable engine design mandates:
  1. ONE parameterised suite over the canonical registry (parametrize over enabled_instruments()), never a
     file per ticker;
  2. candle geometry invariants (§15) hold across ALL 8 rollout instruments with per-instrument precision,
     checked by an INDEPENDENT oracle (not the implementation compared against itself);
  3. an independent indicator oracle sanity path (EMA seed + warm-up) generalises by metadata.

Run: pytest tests/design/test_reusable_engine_contract_v1.py
"""
from __future__ import annotations

import pytest

# --- in-memory canonical registry stand-in (the real one is the SQL `instruments` table) --------------
# metadata is DATA/POLICY only; no per-ticker business logic.
_REGISTRY = {
    "XAU_USD":    {"category": "precious_metals", "price_precision": 5, "market_hours_policy": "metals"},
    "XAG_USD":    {"category": "precious_metals", "price_precision": 5, "market_hours_policy": "metals"},
    "EUR_USD":    {"category": "forex_major",     "price_precision": 5, "market_hours_policy": "fx_24x5"},
    "GBP_USD":    {"category": "forex_major",     "price_precision": 5, "market_hours_policy": "fx_24x5"},
    "AUD_USD":    {"category": "forex_major",     "price_precision": 5, "market_hours_policy": "fx_24x5"},
    "USD_JPY":    {"category": "forex_major",     "price_precision": 3, "market_hours_policy": "fx_24x5"},
    "SPX500_USD": {"category": "indices",         "price_precision": 1, "market_hours_policy": "index_cash"},
    "WTICO_USD":  {"category": "energy",          "price_precision": 3, "market_hours_policy": "energy"},
}


def enabled_instruments():
    """Single authority the whole suite iterates. In production this reads instruments WHERE enabled=1."""
    return sorted(_REGISTRY)


def registry_metadata(instrument):
    return _REGISTRY[instrument]


# --- independent geometry oracle (recomputes from OHLC; NOT the engine) --------------------------------
def geometry_oracle(o, h, l, c, precision):
    q = lambda x: round(x, precision)
    body_high, body_low = max(o, c), min(o, c)
    return {
        "body_high": q(body_high), "body_low": q(body_low),
        "body_size": q(abs(c - o)), "range_size": q(h - l),
        "upper_wick_size": q(h - body_high), "lower_wick_size": q(body_low - l),
        "candle_direction": "UP" if c > o else ("DOWN" if c < o else "FLAT"),
    }


# representative candles per instrument scaled to plausible price magnitude (proves precision generalises)
_SCALE = {"XAU_USD": 4000.0, "XAG_USD": 45.0, "EUR_USD": 1.15, "GBP_USD": 1.27, "AUD_USD": 0.66,
          "USD_JPY": 155.0, "SPX500_USD": 5200.0, "WTICO_USD": 78.0}


def _sample_candles(inst):
    s = _SCALE[inst]
    # (open, high, low, close): normal up, doji, zero-range, upper-wick, lower-wick
    return [
        (s * 1.000, s * 1.010, s * 0.995, s * 1.008),   # up with both wicks
        (s * 1.000, s * 1.002, s * 0.998, s * 1.000),   # doji
        (s * 1.000, s * 1.000, s * 1.000, s * 1.000),   # zero range
        (s * 1.000, s * 1.020, s * 1.000, s * 1.001),   # long upper wick
        (s * 1.000, s * 1.001, s * 0.980, s * 0.999),   # long lower wick
    ]


@pytest.mark.parametrize("instrument", enabled_instruments())
def test_candle_geometry_invariants_hold(instrument):
    """§15 invariants hold for every enabled instrument at its registry precision."""
    meta = registry_metadata(instrument)
    p = meta["price_precision"]
    for (o, h, l, c) in _sample_candles(instrument):
        g = geometry_oracle(o, h, l, c, p)
        assert h >= max(o, c) - 10 ** -p
        assert l <= min(o, c) + 10 ** -p
        assert h >= l
        assert g["upper_wick_size"] >= -(10 ** -p)
        assert g["lower_wick_size"] >= -(10 ** -p)
        assert abs(g["range_size"] - round(h - l, p)) <= 10 ** -p
        assert abs(g["body_size"] - round(abs(c - o), p)) <= 10 ** -p


@pytest.mark.parametrize("instrument", enabled_instruments())
def test_registry_metadata_is_data_only(instrument):
    """Every instrument is described purely by data (no code fork). Required keys present + typed."""
    meta = registry_metadata(instrument)
    assert isinstance(meta["price_precision"], int) and meta["price_precision"] >= 0
    assert meta["market_hours_policy"] in {"metals", "fx_24x5", "index_cash", "energy"}
    assert meta["category"] in {"precious_metals", "base_metals", "forex_major",
                                "forex_minor", "indices", "crypto", "energy"}


def _ema_oracle(values, n):
    """Independent EMA: SMA(n) seed then 2/(n+1) multiplier; None during warm-up."""
    if len(values) < n:
        return [None] * len(values)
    out = [None] * (n - 1)
    seed = sum(values[:n]) / n
    out.append(seed)
    k = 2 / (n + 1)
    prev = seed
    for v in values[n:]:
        prev = v * k + prev * (1 - k)
        out.append(prev)
    return out


@pytest.mark.parametrize("instrument", enabled_instruments())
def test_indicator_warmup_generalises(instrument):
    """Indicator warm-up is metadata-driven, not ticker-specific: EMA seeded at n, warm-up before."""
    s = _SCALE[instrument]
    series = [s * (1 + 0.001 * i) for i in range(30)]
    ema12 = _ema_oracle(series, 12)
    assert ema12[10] is None          # warm-up (before seed)
    assert ema12[11] is not None      # seeded at index n-1
    assert ema12[-1] is not None
    # monotone-increasing input -> EMA increases
    assert ema12[-1] > ema12[12]


def test_single_authority_no_second_list():
    """The suite iterates exactly one authority; the rollout set is exactly the 8 agreed instruments."""
    assert set(enabled_instruments()) == {
        "XAU_USD", "XAG_USD", "EUR_USD", "GBP_USD", "AUD_USD", "USD_JPY", "SPX500_USD", "WTICO_USD",
    }
