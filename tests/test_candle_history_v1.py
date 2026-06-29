"""HERMES candle history/window contract v1 — key schema, target guards, payload, retention, gap profile.
WO-HELM-HERMES-GOLD-MTF-CANDLE-WINDOW-CONTRACT-0001.

DESIGN/CODE ONLY — no Redis I/O. Proves history is hard-separated from live :latest:v1 and that the
target guard rejects every forbidden write shape.
"""
from datetime import datetime, timedelta, timezone

import utils.candle_history_v1 as h
import utils.candle_contract_v1 as cc

_TS = datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc)        # an M1/M5/M15/H1-aligned open
_RUN = "bf_20260626T120000Z_0001"


def _ohlc(o=2000.0, hi=2010.0, lo=1995.0, c=2005.0, v=100):
    return {"open": o, "high": hi, "low": lo, "close": c, "volume": v}


def _env(tf="M5", ts=_TS, **o):
    return h.build_history_envelope(
        instrument="XAU_USD", timeframe=tf, timestamp_utc=ts, ohlc=_ohlc(**o),
        backfill_run_id=_RUN, backfill_inserted_at_utc=datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc),
        source_table=f"candles_{tf}", source_timestamp_utc=ts)


# --------------------------------------------------------------- key generation
def test_history_key_generation_for_grid():
    epoch = int(_TS.timestamp())
    for tf in ("M1", "M5", "M15", "H1"):
        k = h.history_key("XAU_USD", tf, epoch)
        assert k == f"hermes:candles:XAU_USD:{tf}:history:v1:{epoch}"
        assert h.assert_history_target(k) is True
    assert h.history_index_key("XAU_USD", "M5") == "hermes:candles:XAU_USD:M5:history:v1:index"
    assert h.assert_history_target(h.history_index_key("XAU_USD", "H1")) is True


def test_history_key_accepts_datetime_open():
    k = h.history_key("XAU_USD", "M1", _TS)            # datetime coerced to epoch
    assert k.endswith(f":history:v1:{int(_TS.timestamp())}")


# --------------------------------------------------------------- HARD separation from latest
def test_latest_key_rejected_by_target_guard():
    for tf in ("M1", "M5", "M15", "H1"):
        try:
            h.assert_history_target(f"hermes:candles:XAU_USD:{tf}:latest:v1"); assert False
        except ValueError as e:
            assert "GOV-CANDLE-HIST-TGT-002" in str(e)


def test_non_history_key_rejected():
    for k in ("hermes:candles:XAU_USD:M5:window:v1:123", "hermes:price:XAU_USD", "hermes:candles:XAU_USD:M5:v1"):
        try:
            h.assert_history_target(k); assert False, k
        except ValueError as e:
            assert "GOV-CANDLE-HIST-TGT" in str(e)


# --------------------------------------------------------------- alias / non-XAU / H4 / D1 / unversioned
def test_xauusd_alias_rejected_as_output():
    try:
        h.history_key("XAUUSD", "M5", int(_TS.timestamp())); assert False
    except ValueError as e:
        assert "GOV-CANDLE-HIST-002" in str(e)
    try:
        h.assert_history_target(f"hermes:candles:XAUUSD:M5:history:v1:{int(_TS.timestamp())}"); assert False
    except ValueError as e:
        assert "GOV-CANDLE-HIST-TGT-004" in str(e)


def test_non_xau_instrument_rejected():
    for inst in ("AUD_USD", "EUR_USD", "XAG_USD"):
        try:
            h.history_key(inst, "M5", int(_TS.timestamp())); assert False, inst
        except ValueError as e:
            assert "GOV-CANDLE-HIST-001" in str(e)
        try:
            h.assert_history_target(f"hermes:candles:{inst}:M5:history:v1:{int(_TS.timestamp())}"); assert False
        except ValueError as e:
            assert "GOV-CANDLE-HIST-TGT-005" in str(e)


def test_h4_and_d1_accepted_legacyD_rejected():
    ep = int(_TS.timestamp())
    # H4 and D1 are now governed (derived) history timeframes -> accepted (D1 additionally gated by payload guard)
    for tf in ("H4", "D1"):
        assert h.history_key("XAU_USD", tf, ep) == f"hermes:candles:XAU_USD:{tf}:history:v1:{ep}"
        assert h.assert_history_target(f"hermes:candles:XAU_USD:{tf}:history:v1:{ep}") is True
    # legacy "D" remains rejected
    try:
        h.history_key("XAU_USD", "D", ep); assert False
    except ValueError as e:
        assert "GOV-CANDLE-HIST-003" in str(e)
    try:
        h.assert_history_target(f"hermes:candles:XAU_USD:D:history:v1:{ep}"); assert False
    except ValueError as e:
        assert "GOV-CANDLE-HIST-TGT-006" in str(e)


def test_unversioned_history_key_rejected():
    # missing :v1 segment
    try:
        h.assert_history_target("hermes:candles:XAU_USD:M5:history:123456"); assert False
    except ValueError as e:
        assert "GOV-CANDLE-HIST-TGT-003" in str(e)


# --------------------------------------------------------------- payload + validation
def test_history_envelope_is_valid_v1_plus_history_block():
    env = _env("M5")
    assert cc.validate_candle_contract(env) is True
    d = env["data"]
    assert d["instrument"] == "XAU_USD" and d["timeframe"] == "M5"
    # latest v1 geometry preserved
    assert d["body_high"] <= d["high"] and d["wick_high"] >= 0 and d["wick_high"] != d["high"]
    # history block present with exactly the governed fields
    hb = env["history"]
    assert set(hb.keys()) == set(h._HISTORY_FIELDS)
    assert hb["history_contract_version"] == "v1" and hb["source_table"] == "candles_M5"
    assert hb["backfill_run_id"] == _RUN
    assert hb["source_timestamp_utc"].endswith("Z") and hb["backfill_inserted_at_utc"].endswith("Z")


def test_no_regime_fields_anywhere():
    env = _env("M5")
    blob = str(env).lower()
    assert "regime" not in blob and "regime_confidence" not in blob
    # injecting one trips the governed scan
    env["history"]["regime"] = "TREND"
    try:
        cc.validate_candle_contract(env); assert False
    except ValueError as e:
        assert "GOV-CANDLE-CONTRACT-029" in str(e)


def test_validator_still_required_before_write():
    env = _env("M5")
    env["data"]["wick_high"] = env["data"]["high"]      # break geometry
    try:
        h.build_history_write_plan(env); assert False
    except ValueError as e:
        assert "GOV-CANDLE-CONTRACT-035" in str(e)


# --------------------------------------------------------------- write plan / TTL / index / idempotency
def test_write_plan_ttl_index_and_target():
    env = _env("M15")
    plan = h.build_history_write_plan(env)
    epoch = int(_TS.timestamp())
    assert plan["key"] == f"hermes:candles:XAU_USD:M15:history:v1:{epoch}"
    assert plan["ttl_seconds"] == h.HISTORY_TTL_SECONDS == 35 * 86400
    assert plan["index_key"] == "hermes:candles:XAU_USD:M15:history:v1:index"
    assert plan["index_score"] == epoch and plan["index_member"] == str(epoch)
    assert plan["write_mode"] == "HISTORY_INERT_NO_WRITE" and plan["idempotent"] is True


def test_idempotent_same_candle_same_key_and_member():
    p1 = h.build_history_write_plan(_env("M5"))
    p2 = h.build_history_write_plan(_env("M5"))          # rebuilt identical candle
    assert p1["key"] == p2["key"]
    assert (p1["index_key"], p1["index_score"], p1["index_member"]) == \
           (p2["index_key"], p2["index_score"], p2["index_member"])


def test_retention_cutoff_epoch():
    now = datetime(2026, 6, 26, 0, 0, tzinfo=timezone.utc)
    assert h.history_retention_cutoff_epoch(now) == int(now.timestamp()) - 35 * 86400


# --------------------------------------------------------------- dry-run gap profile
def test_gap_profile_full_coverage_weekday():
    # Mon 2026-06-01: provide all 24 H1 opens -> 100% coverage
    day = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    opens = h.expected_opens_for_day("H1", day)
    gp = h.gap_profile("H1", day, opens)
    assert gp["expected_count"] == 24 and gp["actual_count"] == 24 and gp["missing_count"] == 0
    assert gp["coverage_pct"] == 100.0 and gp["gap_explanation"] == "FULL_COVERAGE"
    assert gp["sampled_missing_utc"] == []


def test_gap_profile_weekend_explanation_and_samples():
    # Sat 2026-06-06: no opens -> 0% coverage, weekend explanation, sampled missing capped
    day = datetime(2026, 6, 6, 0, 0, tzinfo=timezone.utc)
    assert day.weekday() == 5
    gp = h.gap_profile("M15", day, [], max_samples=5)
    assert gp["expected_count"] == 96 and gp["actual_count"] == 0 and gp["missing_count"] == 96
    assert gp["coverage_pct"] == 0.0 and gp["gap_explanation"] == "WEEKEND_MARKET_CLOSED"
    assert len(gp["sampled_missing_utc"]) == 5 and gp["first_actual_utc"] is None


def test_gap_profile_partial_weekday_investigate():
    # Tue with a hole -> WEEKDAY_GAP_INVESTIGATE
    day = datetime(2026, 6, 2, 0, 0, tzinfo=timezone.utc)
    assert day.weekday() == 1
    opens = h.expected_opens_for_day("H1", day)[:-3]     # drop last 3 hours
    gp = h.gap_profile("H1", day, opens)
    assert gp["missing_count"] == 3 and gp["gap_explanation"] == "WEEKDAY_GAP_INVESTIGATE"
    assert gp["coverage_pct"] == round(100 * 21 / 24, 2)


def test_expected_counts_per_tf():
    day = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    assert [len(h.expected_opens_for_day(tf, day)) for tf in ("M1", "M5", "M15", "H1")] == [1440, 288, 96, 24]
