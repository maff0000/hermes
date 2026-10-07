"""HERMES governed indicator-history/window contract v1 — key schema, target guards, payload, retention.
WO-HERMES-INDICATOR-HISTORY-CONTRACT-0001.

DESIGN/CODE ONLY — no Redis I/O. Proves indicator-history is hard-separated from the mutable `latest`
indicator key, mirrors the governed candle-history pattern (key shape, retention reuse, provenance shape),
reuses the SAME governed indicator computation/contract (never a second implementation), and introduces no
event ID/content hash, no legacy SQL indicator reference, and no `hermes:signals:*` dependency.
"""
import datetime
import json

import pytest

import utils.indicator_history_v1 as h
import utils.candle_history_v1 as chv
import utils.hermes_indicators_v1 as ind
import utils.candle_contract_v1 as cc

UTC = datetime.timezone.utc
INST = "XAU_USD"
_VOT = datetime.datetime(2026, 9, 10, 10, 0, tzinfo=UTC)          # an M1/M5/M15/H1/H4-aligned open
_GEN = datetime.datetime(2026, 9, 10, 10, 5, tzinfo=UTC)
_PUB = datetime.datetime(2026, 9, 10, 10, 5, 1, tzinfo=UTC)
_RUN = h.PUBLISH_RUN_MARKER

_INDICATORS = {
    "ema_9": 2001.1, "ema_12": 2001.2, "ema_21": 2001.3, "ema_26": 2001.4, "ema_50": 2001.5,
    "ema_200": 2001.6, "rsi_14": 55.5, "atr_14": 3.21,
    "bollinger_upper_20_2": 2010.0, "bollinger_middle_20_2": 2001.0, "bollinger_lower_20_2": 1992.0,
    "adx_14": 22.0, "adx_plus_di_14": 25.0, "adx_minus_di_14": 15.0,
}


def _source_key(tf="H4", open_epoch=None):
    return chv.history_key(INST, tf, open_epoch if open_epoch is not None else int(_VOT.timestamp()))


def _env(tf="H4", vot=_VOT, gen=_GEN, pub=_PUB, run_id=_RUN, indicators=None, freshness="FRESH"):
    return h.build_history_envelope(
        instrument=INST, timeframe=tf, generated_at_utc=gen, value_open_time_utc=vot,
        indicators=indicators or dict(_INDICATORS), freshness_state=freshness,
        publish_run_id=run_id, published_at_utc=pub,
        source_candle_history_key=_source_key(tf, int(vot.timestamp())), source_timestamp_utc=vot)


# --------------------------------------------------------------------------- key generation
def test_history_key_generation_for_grid():
    epoch = int(_VOT.timestamp())
    for tf in ("M1", "M5", "M15", "H1", "H4"):
        k = h.history_key(INST, tf, epoch)
        assert k == f"hermes:indicators:{INST}:{tf}:history:v1:{epoch}"
        assert h.assert_history_target(k) is True
    assert h.history_index_key(INST, "H4") == f"hermes:indicators:{INST}:H4:history:v1:index"
    assert h.assert_history_target(h.history_index_key(INST, "M5")) is True


def test_history_key_accepts_datetime_open():
    k = h.history_key(INST, "M5", _VOT)
    assert k.endswith(f":history:v1:{int(_VOT.timestamp())}")


def test_d1_excluded_gated_out_of_scope():
    try:
        h.history_key(INST, "D1", int(_VOT.timestamp())); assert False
    except ValueError as e:
        assert "GOV-HERMES-IND-HIST-002" in str(e)
    try:
        h.assert_history_target(f"hermes:indicators:{INST}:D1:history:v1:{int(_VOT.timestamp())}"); assert False
    except ValueError as e:
        assert "GOV-HERMES-IND-HIST-TGT-004" in str(e)


# --------------------------------------------------------------------------- HARD separation from mutable latest
def test_mutable_latest_key_rejected_by_target_guard():
    for tf in ("M1", "M5", "M15", "H1", "H4"):
        try:
            h.assert_history_target(f"hermes:indicators:{INST}:{tf}:v1"); assert False
        except ValueError as e:
            assert "GOV-HERMES-IND-HIST-TGT" in str(e)


def test_non_history_key_rejected():
    for k in ("hermes:indicators:XAU_USD:H4:window:v1:123", "hermes:price:XAU_USD",
             "hermes:candles:XAU_USD:H4:history:v1:123"):
        try:
            h.assert_history_target(k); assert False, k
        except ValueError as e:
            assert "GOV-HERMES-IND-HIST-TGT" in str(e)


def test_alias_instrument_rejected():
    try:
        h.history_key("XAUUSD", "H4", int(_VOT.timestamp())); assert False
    except ValueError as e:
        assert "GOV-HERMES-IND-HIST-001" in str(e)
    try:
        h.assert_history_target(f"hermes:indicators:XAUUSD:H4:history:v1:{int(_VOT.timestamp())}"); assert False
    except ValueError as e:
        assert "GOV-HERMES-IND-HIST-TGT-003" in str(e)


def test_unversioned_history_key_rejected():
    try:
        h.assert_history_target(f"hermes:indicators:{INST}:H4:history:123456"); assert False
    except ValueError as e:
        assert "GOV-HERMES-IND-HIST-TGT-002" in str(e)


# --------------------------------------------------------------------------- payload / provenance / validation
def test_history_envelope_mirrors_latest_contract_plus_history_block():
    env = _env("H4")
    assert ind.validate_indicator_contract(env) is True
    assert env["instrument"] == INST and env["timeframe"] == "H4"
    assert env["publisher"] == "HERMES" and env["deterministic_only"] is True
    for f in ("ema_9", "ema_12", "ema_21", "ema_26", "ema_50", "ema_200", "rsi_14", "atr_14",
             "bollinger_upper_20_2", "bollinger_middle_20_2", "bollinger_lower_20_2",
             "adx_14", "adx_plus_di_14", "adx_minus_di_14"):
        assert f in env["indicators"]
    assert env["methods"]["ema_method"] == "STANDARD_2_OVER_N_PLUS_1_SMA_SEED"
    hb = env["history"]
    assert set(hb.keys()) == set(h._HISTORY_FIELDS)
    assert hb["history_contract_version"] == "v1"
    assert hb["publish_run_id"] == _RUN
    assert hb["source_candle_history_key"] == _source_key("H4")
    assert hb["published_at_utc"].endswith("Z") and hb["source_timestamp_utc"].endswith("Z")


def test_natural_identity_no_event_id_or_hash():
    env = _env("H4")
    blob = json.dumps(env).lower()
    for forbidden in ("event_id", "content_hash", "uuid"):
        assert forbidden not in blob


def test_no_forbidden_regime_risk_fields_leak_into_history_block():
    env = _env("H4")
    env["history"]["regime"] = "TREND"
    try:
        ind.validate_indicator_contract(env); assert False
    except ValueError as e:
        assert "GOV-HERMES-IND-003" in str(e)


def test_no_legacy_signals_or_sql_reference_in_module():
    import inspect
    raw = inspect.getsource(h)
    assert "hermes:signals:" not in raw
    assert "candles_H4" not in raw and "candles_D1" not in raw and "SELECT" not in raw.upper()


# --------------------------------------------------------------------------- write plan / TTL / index / idempotency
def test_write_plan_ttl_index_and_target_h4_count_based(monkeypatch):
    env = _env("H4")
    plan = h.build_history_write_plan(env)
    epoch = int(_VOT.timestamp())
    assert plan["key"] == f"hermes:indicators:{INST}:H4:history:v1:{epoch}"
    assert plan["ttl_seconds"] == chv.H4_HISTORY_TTL_SECONDS == 120 * 86400
    assert plan["index_key"] == f"hermes:indicators:{INST}:H4:history:v1:index"
    assert plan["index_score"] == epoch and plan["index_member"] == str(epoch)
    assert plan["write_mode"] == chv.WRITE_MODE_HISTORY_INERT and plan["idempotent"] is True


def test_write_plan_ttl_reuses_time_based_retention_for_non_h4(monkeypatch):
    monkeypatch.setenv("HERMES_REDIS_HISTORY_RETENTION_DAYS", "14")
    env = _env("M5")
    plan = h.build_history_write_plan(env)
    assert plan["ttl_seconds"] == chv.history_ttl_seconds() == 14 * 86400


def test_idempotent_same_bar_same_key_and_member():
    p1 = h.build_history_write_plan(_env("H4"))
    p2 = h.build_history_write_plan(_env("H4"))
    assert p1["key"] == p2["key"]
    assert (p1["index_key"], p1["index_score"], p1["index_member"]) == \
           (p2["index_key"], p2["index_score"], p2["index_member"])


def test_write_plan_rejects_malformed_history_block():
    env = _env("H4")
    del env["history"]["publish_run_id"]
    try:
        h.build_history_write_plan(env); assert False
    except ValueError as e:
        assert "GOV-HERMES-IND-HIST-010" in str(e)


# --------------------------------------------------------------------------- open_epoch alignment / UTC
def test_open_epoch_alignment_matches_value_open_time():
    vot = datetime.datetime(2026, 9, 11, 14, 0, tzinfo=UTC)
    env = _env("H4", vot=vot)
    plan = h.build_history_write_plan(env)
    assert plan["index_score"] == int(vot.timestamp())
    assert plan["key"].endswith(f":{int(vot.timestamp())}")


def test_timestamps_are_governed_aware_utc():
    env = _env("H4")
    for f in ("generated_at_utc", "value_open_time_utc"):
        assert env[f].endswith("Z")
        datetime.datetime.strptime(env[f][:-1], cc._UTC_MS)   # parses cleanly as aware-UTC-shaped
    for f in ("published_at_utc", "source_timestamp_utc"):
        assert env["history"][f].endswith("Z")
        datetime.datetime.strptime(env["history"][f][:-1], cc._UTC_MS)


# --------------------------------------------------------------------------- determinism
def test_deterministic_across_replay_byte_identical():
    env1 = _env("H4")
    env2 = _env("H4")   # identical inputs
    assert json.dumps(env1, sort_keys=True) == json.dumps(env2, sort_keys=True)


def test_same_governed_calculation_as_latest_not_reimplemented():
    """The indicators dict this module accepts must be byte-identical to what the LATEST contract carries
    for the same inputs — i.e. this module never recomputes, it only snapshots."""
    latest = ind.build_indicator_contract(instrument=INST, timeframe="H4", generated_at_utc=_GEN,
                                          value_open_time_utc=_VOT, indicators=dict(_INDICATORS))
    hist = _env("H4")
    assert latest["indicators"] == hist["indicators"]
    assert latest["methods"] == hist["methods"]
