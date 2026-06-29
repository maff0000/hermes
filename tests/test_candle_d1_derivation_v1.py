"""HERMES D1 derivation from 6xH4 (fixed 22:00 UTC NY-5PM day) — code-only, no Redis I/O.
WO-HELM-HERMES-GOLD-D1-DERIVATION-FROM-H4-0001.
"""
from datetime import datetime, timedelta, timezone

import pytest

import utils.candle_d1_derivation_v1 as d1d
import utils.candle_contract_v1 as cc
import utils.candle_publisher_v1 as cp
import utils.candle_history_v1 as chv

UTC = timezone.utc
_D1O = datetime(2026, 6, 25, 22, 0, tzinfo=UTC)        # a valid D1 open (22:00Z)


def _h4(open_dt, o, hi, lo, c, v):
    return {"timestamp": open_dt, "open": o, "high": hi, "low": lo, "close": c, "volume": v}


def _six_children(d1_open=_D1O):
    """6 H4 children at 22/02/06/10/14/18, with distinct OHLCV to verify aggregation."""
    opens = d1d.d1_child_h4_opens(d1_open)
    # (o, hi, lo, c, v) per child; global max high=2080 (child2), global min low=1900 (child4)
    specs = [(2000, 2010, 1990, 2005, 10), (2005, 2030, 1995, 2020, 11), (2020, 2080, 2010, 2050, 12),
             (2050, 2060, 2000, 2030, 13), (2030, 2040, 1900, 1950, 14), (1950, 1975, 1940, 1970, 15)]
    return [_h4(opens[i], *specs[i]) for i in range(6)]


def _gen(d1_open=_D1O):
    return cc.normalise_utc(d1_open) + timedelta(seconds=d1d.D1_SECONDS)   # close time -> OK (age 0)


# ============================ anchor: fixed 22:00 UTC ============================
def test_bucket_open_is_2200_utc_for_various_times():
    cases = {
        datetime(2026, 6, 25, 22, 0, tzinfo=UTC): datetime(2026, 6, 25, 22, 0, tzinfo=UTC),    # exactly 22:00
        datetime(2026, 6, 25, 22, 0, 1, tzinfo=UTC): datetime(2026, 6, 25, 22, 0, tzinfo=UTC),  # just after
        datetime(2026, 6, 26, 3, 0, tzinfo=UTC): datetime(2026, 6, 25, 22, 0, tzinfo=UTC),      # next-day AM
        datetime(2026, 6, 26, 21, 59, 59, tzinfo=UTC): datetime(2026, 6, 25, 22, 0, tzinfo=UTC),# just before roll
        datetime(2026, 6, 26, 22, 0, tzinfo=UTC): datetime(2026, 6, 26, 22, 0, tzinfo=UTC),     # next bucket
        datetime(2026, 6, 26, 0, 0, tzinfo=UTC): datetime(2026, 6, 25, 22, 0, tzinfo=UTC),      # UTC-midnight -> 22:00 prev
    }
    for dt, expected in cases.items():
        assert d1d.d1_bucket_open(dt) == expected, dt
        assert d1d.d1_bucket_open(dt).hour == 22


def test_d1_spans_2200_to_2200_six_h4_children_opens():
    opens = d1d.d1_child_h4_opens(_D1O)
    assert [o.hour for o in opens] == [22, 2, 6, 10, 14, 18]
    assert opens[0] == _D1O and opens[-1] == _D1O + timedelta(hours=20)
    assert opens[1].date() != _D1O.date()          # 02:00 falls on the next UTC calendar day


def test_assert_anchor_rejects_utc_midnight_and_offgrid():
    for bad in (datetime(2026, 6, 26, 0, 0, tzinfo=UTC),      # UTC midnight
                datetime(2026, 6, 25, 21, 0, tzinfo=UTC),     # 21:00
                datetime(2026, 6, 25, 22, 30, tzinfo=UTC)):   # 22:30
        with pytest.raises(ValueError) as e:
            d1d.assert_d1_open_anchor(bad)
        assert "GOV-CANDLE-D1-002" in str(e.value)


def test_derive_rejects_utc_midnight_anchor():
    with pytest.raises(ValueError) as e:
        d1d.derive_d1(instrument="XAU_USD", d1_open=datetime(2026, 6, 26, 0, 0, tzinfo=UTC),
                      h4_children=_six_children(), generated_at_utc=_gen())
    assert "GOV-CANDLE-D1-002" in str(e.value)


# ============================ derivation OHLCV ============================
def test_six_h4_children_derive_correct_ohlcv():
    env, meta = d1d.derive_d1(instrument="XAU_USD", d1_open=_D1O, h4_children=_six_children(),
                              generated_at_utc=_gen(), is_closed=True)
    d = env["data"]
    assert d["timeframe"] == "D1" and d["instrument"] == "XAU_USD"
    assert d["timestamp_utc"] == cc._fmt(_D1O)
    assert d["open"] == 2000.0          # first child open
    assert d["high"] == 2080.0          # max child high (child2)
    assert d["low"] == 1900.0           # min child low (child4)
    assert d["close"] == 1970.0         # last child close (child5)
    assert d["volume"] == 10 + 11 + 12 + 13 + 14 + 15      # sum
    assert d["source_timeframe"] == "H4" and d["source_count"] == 6 and d["expected_source_count"] == 6
    assert d["source_coverage"] == 1.0
    assert env["provenance"]["derivation"] == cc.DERIVATION_DERIVED
    assert d["derivation_policy"] == cc.DERIVATION_POLICY_D1_FROM_H4
    assert d["source_policy_epoch"] == "D1_FROM_H4_NY1700_FIXED_UTC_V1"
    assert meta["d1_open_epoch"] == int(_D1O.timestamp()) and len(meta["child_open_epochs"]) == 6


def test_six_of_six_closed_is_ok():
    env, _ = d1d.derive_d1(instrument="XAU_USD", d1_open=_D1O, h4_children=_six_children(),
                           generated_at_utc=_gen(), is_closed=True)
    assert env["status"] == "OK" and env["data"]["gap_state"] == "NONE"
    assert cc.validate_candle_contract(env) is True


# ============================ completeness / gap ============================
def test_source_count_below_six_never_ok():
    for n in (0, 1, 3, 5):
        kids = _six_children()[:n]
        env, meta = d1d.derive_d1(instrument="XAU_USD", d1_open=_D1O, h4_children=kids,
                                  generated_at_utc=_gen(), is_closed=True)
        assert env["status"] != "OK", n
        assert meta["source_count"] == n                       # NOT padded to 6 (no synthesis)
        if n == 0:
            assert env["status"] == "NO_SOURCE_DATA" and env["data"]["gap_state"] == "GAP_DETECTED"
        else:
            assert env["status"] == "SOURCE_INCOMPLETE"
            assert env["data"]["source_coverage"] == round(n / 6, 6) < 1.0
            assert env["data"]["gap_state"] == "INCOMPLETE"


def test_missing_child_surfaces_incomplete_not_laundered():
    kids = [c for c in _six_children() if c["timestamp"].hour != 6]   # drop the 06:00 child -> 5/6
    env, _ = d1d.derive_d1(instrument="XAU_USD", d1_open=_D1O, h4_children=kids, generated_at_utc=_gen())
    assert env["status"] == "SOURCE_INCOMPLETE" and env["data"]["source_count"] == 5
    assert env["data"]["source_coverage"] == round(5 / 6, 6)


def test_forming_d1_is_forming_not_ok():
    env, _ = d1d.derive_d1(instrument="XAU_USD", d1_open=_D1O, h4_children=_six_children(),
                           generated_at_utc=_gen(), is_closed=False)
    assert env["status"] == "FORMING" and env["freshness_state"] == "FORMING"


def test_h4_children_selection_window():
    kids = _six_children() + [_h4(_D1O + timedelta(hours=24), 1, 2, 0.5, 1.5, 1)]   # next day's 22:00 H4
    sel = d1d.h4_children_in_bucket(_D1O, kids)
    assert len(sel) == 6                                       # the +24h child is outside [open, open+24h)


# ============================ instrument guards ============================
def test_non_xau_and_alias_rejected():
    for inst in ("EUR_USD", "XAUUSD", "XAG_USD"):
        with pytest.raises(ValueError) as e:
            d1d.derive_d1(instrument=inst, d1_open=_D1O, h4_children=_six_children(), generated_at_utc=_gen())
        assert "GOV-CANDLE-D1-001" in str(e.value)


def test_output_never_alias_or_nonxau_key():
    env, _ = d1d.derive_d1(instrument="XAU_USD", d1_open=_D1O, h4_children=_six_children(), generated_at_utc=_gen())
    assert env["key"] == "hermes:candles:XAU_USD:D1:latest:v1"     # versioned, canonical id only
    assert "XAUUSD" not in env["key"]


# ============================ safety: no direct-D1 / no 24xH1 / no regime / no shadow / no redis ============================
def test_module_has_no_direct_d1_source_no_24xh1_no_regime_no_shadow_no_redis():
    src = open(d1d.__file__).read()
    # no midnight-anchored direct daily table as a SOURCE
    assert '"candles_D1"' not in src and "'candles_D1'" not in src
    # no 24xH1 production source path: source is structurally 6xH4, never an H1 table / 24-child shortcut
    assert '"candles_H1"' not in src and "'candles_H1'" not in src
    assert "H1_TIMEFRAME" not in src                           # H1 is not a source timeframe in this module
    assert d1d.D1_EXPECTED_CHILDREN == 6 and d1d.H4_TIMEFRAME == "H4"   # source is H4, expected 6 (not 24xH1)
    # no interpretive / shadow / redis
    assert "regime_confidence" not in src
    assert '"regime"' not in src and "'regime'" not in src
    assert "shadow" not in src.lower()
    assert "import redis" not in src and ".set(" not in src and ".zadd(" not in src


def test_derived_payload_carries_no_forbidden_fields():
    env, _ = d1d.derive_d1(instrument="XAU_USD", d1_open=_D1O, h4_children=_six_children(), generated_at_utc=_gen())
    import json
    blob = json.dumps(env).lower()
    for tok in ("regime", "structure", "choch", "order_block", "signal", "shadow"):
        assert tok not in blob


# ===== D1 publish posture (updated by WO-...-D1-CANONICAL-PUBLISH-WIRE-0001): latest via governed path; history still blocked =====
def test_d1_canonical_latest_key_now_accepted_via_governed_path():
    # D1 latest key is now publish-grid-eligible (reachable ONLY through the governed D1 producer; the
    # direct seam still refuses D1, so generic/direct D1 publication remains impossible).
    assert cp.assert_canonical_key("hermes:candles:XAU_USD:D1:latest:v1") is True
    # legacy "D" token remains never published
    with pytest.raises(ValueError) as e:
        cp.assert_canonical_key("hermes:candles:XAU_USD:D:latest:v1")
    assert "GOV-CANDLE-PUB-CANON-KEY-005" in str(e.value)


def test_d1_history_target_now_accepted_via_governed_path():
    # D1 history keyspace is now governed (WO-...-D1-HISTORY-CONTRACT-WRITER-0001); payload guard + D1-history
    # authorisation still gate any actual write. The legacy "D" token remains rejected.
    for k in ("hermes:candles:XAU_USD:D1:history:v1:1782424800", "hermes:candles:XAU_USD:D1:history:v1:index"):
        assert chv.assert_history_target(k) is True
    with pytest.raises(ValueError) as e:
        chv.assert_history_target("hermes:candles:XAU_USD:D:history:v1:1782424800")
    assert "GOV-CANDLE-HIST-TGT-006" in str(e.value)


def test_d1_contract_recognised_publishable_and_history_eligible():
    assert "D1" in cc.TIMEFRAMES and cc.TF_SECONDS["D1"] == 86400
    assert "D1" in cp.CANONICAL_PUBLISH_TIMEFRAMES              # publishable via governed D1 producer
    assert "D1" in chv.HISTORY_TIMEFRAMES                       # history-eligible via governed D1-history path
