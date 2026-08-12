"""Multi-instrument core candle-history contract (M1/M5/M15/H1) for every enabled instrument.
WO-HELM-HERMES-DEV-MULTI-INSTRUMENT-CORE-CANDLE-WICK-HISTORY-CONTRACT-0001.

Proves the generalisation is config-driven and generic: the forward-history lane and the base candle seam
accept the configured enabled instrument set (not XAU-only), XAU behaviour is preserved, the XAUUSD alias is
still rejected as an output instrument, and the H4 producer is DECOUPLED (its own allowlist) so it stays XAU
while the base timeframes fan out. Also asserts the core wick/body geometry is present + generic for non-XAU.
"""
import pytest

from utils import candle_history_forward_writer_v1 as fw
from utils import candle_runtime_seam_v1 as seam
from utils import candle_contract_v1 as cc
from utils import candle_h4_publish_wire_v1 as h4w

ENABLED_14 = ("XAU_USD", "XAG_USD", "XPT_USD", "XCU_USD", "EUR_USD", "GBP_USD", "USD_JPY",
              "USD_CHF", "USD_CAD", "AUD_USD", "NZD_USD", "EUR_GBP", "SPX500_USD", "WTICO_USD")


def test_forward_history_accepts_full_enabled_set():
    got = fw.parse_forward_instruments(",".join(ENABLED_14))
    assert got == frozenset(ENABLED_14)


def test_forward_history_still_accepts_xau_only():
    assert fw.parse_forward_instruments("XAU_USD") == frozenset({"XAU_USD"})


def test_forward_history_alias_still_rejected():
    with pytest.raises(ValueError):
        fw.parse_forward_instruments("XAUUSD")


def test_forward_history_no_default_fanout():
    # fail-closed: empty config is rejected (never silently publishes for everything)
    for raw in (None, "", "  ", ","):
        with pytest.raises(ValueError):
            fw.parse_forward_instruments(raw)


def test_base_seam_allowlist_is_generic():
    # the base candle seam allowlist parser accepts any listed canonical instruments (not XAU-only)
    allowed = seam.parse_canonical_allowlist(",".join(ENABLED_14))
    assert frozenset(ENABLED_14) <= frozenset(allowed)


def test_h4_uses_its_own_decoupled_env_key():
    # H4 reads HERMES_CANDLE_H4_INSTRUMENTS, NOT the base HERMES_CANDLE_CANONICAL_INSTRUMENTS.
    assert h4w.H4_INSTRUMENTS_ENV == "HERMES_CANDLE_H4_INSTRUMENTS"
    assert h4w.H4_INSTRUMENTS_ENV != seam.CANONICAL_ALLOWLIST_ENV


def test_core_wick_body_geometry_is_generic():
    # The core candle geometry is computed purely from OHLC (no instrument parameter) -> identical rules for
    # metals/FX/indices/oil. body_high=max(o,c)=104 body_low=min=100 wick_high=high-body_high=6
    # wick_low=body_low-low=5 body_size=4 range_size(total_range)=15 direction=UP.
    g = cc._candle_geometry({"open": 100.0, "high": 110.0, "low": 95.0, "close": 104.0})
    assert g["body_high"] == 104.0 and g["body_low"] == 100.0
    assert g["wick_high"] == 6.0 and g["wick_low"] == 5.0
    assert g["body_size"] == 4.0 and g["range_size"] == 15.0
    assert g["candle_direction"] == "UP"
    # DOWN + FLAT direction sanity (still instrument-agnostic)
    assert cc._candle_geometry({"open": 104.0, "high": 110.0, "low": 95.0, "close": 100.0})["candle_direction"] == "DOWN"
    assert cc._candle_geometry({"open": 100.0, "high": 110.0, "low": 95.0, "close": 100.0})["candle_direction"] == "FLAT"
